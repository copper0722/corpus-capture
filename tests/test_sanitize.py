"""What a stored capture is allowed to contain.

The artifact outlives the browser that made it. Somebody opens it months later,
from a receiver or a download directory or a reader UI whose CSP may or may not
be right, and whatever is in the file is what runs. Removing `<script>` and
`on*` was not enough: `<base>` rewrites every relative URL, a meta refresh
navigates, a form posts, `<source srcset>` and `<link rel=stylesheet>` fetch,
and SVG carries its own script surface.

These tests run the SHIPPED sanitizer over a real DOM. A Python reimplementation
of the allowlist would pass while the shipped allowlist was wrong, which is the
only failure that matters.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
NODE_MODULES = ROOT / "node_modules"


def _node() -> str | None:
    return shutil.which("node") or shutil.which("nodejs")


def _dom_available() -> bool:
    return (NODE_MODULES / "linkedom").is_dir()


# A skip is not a pass. CI sets this, so a missing `npm ci` fails the build
# instead of quietly running none of the sanitizer tests.
if not _dom_available() and os.environ.get("CORPUS_CAPTURE_REQUIRE_DOM") == "1":
    raise RuntimeError("linkedom is missing; run `npm ci` before pytest")

pytestmark = [
    pytest.mark.skipif(_node() is None, reason="node is not installed on this host"),
    pytest.mark.skipif(not _dom_available(), reason="run `npm ci` for the DOM tests"),
]


def _sanitize(markup: str, base_url: str = "https://www.nejm.org/a") -> dict:
    """Run sanitizeDocument over `markup` and return the result plus counts."""

    script = f"""
      import {{ parseHTML }} from "linkedom";
      import {{ sanitizeDocument, serializeDocument }}
        from "{(EXTENSION / 'sanitize.js').as_posix()}";
      const {{ document }} = parseHTML({json.dumps(markup)});
      const removed = sanitizeDocument(document, {{ baseUrl: {json.dumps(base_url)} }});
      console.log(JSON.stringify({{ html: serializeDocument(document), removed }}));
    """
    result = subprocess.run(
        [_node(), "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False, cwd=ROOT,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestActiveMarkupIsRemoved:
    """F-04. Each of these was retained by the previous selective removal."""

    @pytest.mark.parametrize(
        ("markup", "gone"),
        [
            ('<base href="https://evil.test/">', "evil.test"),
            ('<meta http-equiv="refresh" content="0;url=https://evil.test/">', "refresh"),
            ('<form action="https://evil.test/x"><input name="a"></form>', "evil.test"),
            ('<picture><source srcset="https://evil.test/x.jpg"></picture>', "evil.test"),
            ('<link rel="stylesheet" href="https://evil.test/x.css">', "evil.test"),
            ('<svg><use href="https://evil.test/x.svg#a"/></svg>', "evil.test"),
            ('<iframe src="https://evil.test/"></iframe>', "evil.test"),
            ('<object data="https://evil.test/x.swf"></object>', "evil.test"),
            ('<video src="https://evil.test/x.mp4"></video>', "evil.test"),
            ('<script>fetch("https://evil.test/")</script>', "evil.test"),
            ('<math><mtext>x</mtext></math>', "<math"),
            ('<canvas id="c"></canvas>', "<canvas"),
            ('<textarea>x</textarea>', "<textarea"),
            ('<button onclick="x()">go</button>', "<button"),
        ],
    )
    def test_the_element_does_not_survive(self, markup, gone):
        assert gone not in _sanitize(f"<html><body>{markup}</body></html>")["html"]

    def test_an_event_handler_attribute_does_not_survive(self):
        markup = '<html><body><p onclick="steal()" onmouseover="x">t</p></body></html>'
        html = _sanitize(markup)["html"]
        assert "onclick" not in html and "onmouseover" not in html
        assert ">t<" in html, "the text is the article and stays"

    def test_a_javascript_href_is_dropped_and_the_text_kept(self):
        html = _sanitize(
            '<html><body><a href="javascript:alert(1)">read</a></body></html>'
        )["html"]
        assert "javascript:" not in html
        assert "read" in html

    def test_an_unknown_element_is_unwrapped_not_deleted(self):
        """A custom element is not evidence; the words inside it are the article."""

        result = _sanitize("<html><body><my-widget><p>the finding</p></my-widget></body></html>")
        assert "my-widget" not in result["html"]
        assert "the finding" in result["html"]
        assert result["removed"]["unwrapped"] >= 1

    def test_the_declaring_half_of_meta_survives(self):
        """citation_* is what a receiver reads identity out of."""

        html = _sanitize(
            '<html><head><meta name="citation_doi" content="10.1056/x">'
            '<meta http-equiv="refresh" content="0;url=https://evil.test/">'
            '</head><body><p>x</p></body></html>'
        )["html"]
        assert "citation_doi" in html and "10.1056/x" in html
        assert "refresh" not in html and "evil.test" not in html

    def test_the_article_survives_intact(self):
        """A sanitizer that eats the article is not a fix."""

        html = _sanitize(
            "<html><body><article><h1>Title</h1><p>Body text.</p>"
            '<figure id="f1"><img src="data:image/gif;base64,R0lGOD" alt="Figure1">'
            "<figcaption>Figure 1. A caption.</figcaption></figure>"
            "<table><caption>Table 1</caption>"
            "<tr><th scope=col>A</th><td colspan=2>1</td></tr></table>"
            "</article></body></html>"
        )["html"]
        for kept in ("<h1", "Body text.", "<figure", "figcaption", "Figure 1. A caption.",
                     "<table", "<caption", 'scope="col"', 'colspan="2"', "data:image/gif"):
            assert kept in html, kept

    def test_the_sanitizer_reports_what_it_did(self):
        """`removed nothing` and `did not run` must not look the same."""

        result = _sanitize('<html><body><script>x</script><p>t</p></body></html>')
        assert result["removed"]["elements"] >= 1


class TestCssCannotReintroduceMarkup:
    """F-05, and the CSS half of F-04."""

    def _css(self, css: str, base: str = "https://cdn.example/a/style.css") -> str:
        script = f"""
          import {{ sanitizeCss }} from "{(EXTENSION / 'sanitize.js').as_posix()}";
          console.log(JSON.stringify(sanitizeCss({json.dumps(css)}, {json.dumps(base)})));
        """
        result = subprocess.run(
            [_node(), "--input-type=module", "--eval", script],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    @pytest.mark.parametrize("closer", ["</style", "</STYLE", "</StYlE", "</style\n>"])
    def test_a_closing_tag_in_css_cannot_end_the_element(self, closer):
        """The measured bug, asserted on a round trip rather than on a string.

        Serializing a `<style>` does NOT escape its text -- the parser ends the
        element at the first `</style` and CSS has no escape that stops it. So
        building the DOM is necessary but not sufficient: the sequence is
        neutralized in every case, where the old guard matched only lowercase.
        The proof is that the artifact, re-parsed, contains no script.
        """

        hostile = f"body{{color:red}} {closer}><script>alert(1)</script><style>"
        script = f"""
          import {{ parseHTML }} from "linkedom";
          import {{ sanitizeCss, serializeDocument }}
            from "{(EXTENSION / 'sanitize.js').as_posix()}";
          const source =
            "<html><head><style></style></head><body><p>t</p></body></html>";
          const {{ document }} = parseHTML(source);
          document.querySelector("style").textContent =
            sanitizeCss({json.dumps(hostile)}, "https://cdn.example/x.css");
          const artifact = serializeDocument(document);
          const reparsed = parseHTML(artifact).document;
          console.log(JSON.stringify({{
            scripts: reparsed.querySelectorAll("script").length,
            styled: (reparsed.querySelector("style") || {{}}).textContent || "",
            body: (reparsed.querySelector("p") || {{}}).textContent || "",
          }}));
        """
        result = subprocess.run(
            [_node(), "--input-type=module", "--eval", script],
            capture_output=True, text=True, check=False, cwd=ROOT,
            env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        )
        assert result.returncode == 0, result.stderr
        round_trip = json.loads(result.stdout)
        assert round_trip["scripts"] == 0, "CSS closed the style element"
        assert round_trip["body"] == "t", "the artifact after the style block survived"
        assert "color:red" in round_trip["styled"], "the actual CSS is still there"

    def test_an_import_is_removed(self):
        assert "@import" not in self._css('@import url("https://evil.test/x.css"); p{color:red}')

    def test_expression_and_binding_are_neutralized(self):
        css = self._css("p{width:expression(alert(1));behavior:url(x.htc);-moz-binding:url(y)}")
        assert "expression(" not in css
        assert "behavior:" not in css and "-moz-binding" not in css

    def test_a_javascript_url_becomes_blank(self):
        assert "javascript:" not in self._css("p{background:url(javascript:alert(1))}")

    def test_a_relative_url_is_absolutized_against_the_stylesheet(self):
        assert "https://cdn.example/a/bg.png" in self._css("p{background:url(bg.png)}")

    def test_a_data_uri_is_left_alone(self):
        css = self._css("p{background:url(data:image/gif;base64,R0lGOD)}")
        assert "data:image/gif;base64,R0lGOD" in css


def test_the_style_attribute_is_sanitized_too():
    markup = '<html><body><p style="background:url(javascript:alert(1))">t</p></body></html>'
    html = _sanitize(markup)["html"]
    assert "javascript:" not in html


def test_the_capture_no_longer_substitutes_bytes_into_a_string():
    """F-05 structurally: the class of bug, not one instance of it."""

    source = (EXTENSION / "capture.js").read_text(encoding="utf-8")
    assert "DOMParser" in source
    assert "serializeDocument(doc)" in source
    assert "node.textContent = sanitizeCss" in source
    for gone in ("html.split(", ".join(css)", "</style"):
        assert gone not in source, f"string substitution is back: {gone}"
