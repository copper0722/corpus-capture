"""Ceilings, and the fact that they hold before the memory is spent.

A capture is driven entirely by a document the reader does not control: the
asset list, the figure list, the metadata, the size of any single response and
of the finished artifact are all page-supplied. Each of these tests hands the
shipped code a page or a response that is trying to be too big.
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


pytestmark = pytest.mark.skipif(_node() is None, reason="node is not installed on this host")


def _run(script: str, *, need_dom: bool = False):
    if need_dom and not (NODE_MODULES / "linkedom").is_dir():
        pytest.skip("run `npm ci` for the DOM tests")
    result = subprocess.run(
        [_node(), "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False, cwd=ROOT,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


LIMITS_JS = (EXTENSION / "limits.js").as_posix()
CAPTURE_JS = (EXTENSION / "capture.js").as_posix()
SERIALIZE_JS = (EXTENSION / "serialize.js").as_posix()


class TestOneAssetCannotBeUnbounded:
    """`arrayBuffer()` materialized whatever arrived before anything measured it."""

    def test_a_declared_length_over_the_cap_is_refused_without_reading(self):
        outcome = _run(f"""
          import {{ readBounded }} from "{CAPTURE_JS}";
          const body = new ReadableStream({{
            pull(controller) {{ controller.enqueue(new Uint8Array(1024)); }}
          }});
          const response = new Response(body, {{ headers: {{ "content-length": "99999999" }} }});
          const controller = new AbortController();
          const bytes = await readBounded(response, 1024, controller);
          console.log(JSON.stringify({{
            bytes, aborted: controller.signal.aborted, bodyUsed: response.bodyUsed,
          }}));
        """)
        assert outcome["bytes"] is None
        assert outcome["aborted"] is True
        assert outcome["bodyUsed"] is False, "the body was read despite the declared size"

    def test_a_dishonest_length_is_caught_while_streaming(self):
        """A declared length is a claim. The cap applies to what actually arrives."""

        outcome = _run(f"""
          import {{ readBounded }} from "{CAPTURE_JS}";
          let sent = 0;
          const body = new ReadableStream({{
            pull(controller) {{ sent += 1; controller.enqueue(new Uint8Array(4096)); }}
          }});
          const response = new Response(body, {{ headers: {{ "content-length": "10" }} }});
          const controller = new AbortController();
          const bytes = await readBounded(response, 8192, controller);
          console.log(JSON.stringify({{
            bytes, aborted: controller.signal.aborted, chunksSent: sent,
          }}));
        """)
        assert outcome["bytes"] is None
        assert outcome["aborted"] is True
        assert outcome["chunksSent"] <= 4, "the stream ran on after the cap"

    def test_an_asset_within_the_cap_comes_back_whole(self):
        outcome = _run(f"""
          import {{ readBounded }} from "{CAPTURE_JS}";
          const response = new Response(new Uint8Array([1, 2, 3, 4]));
          const controller = new AbortController();
          const bytes = await readBounded(response, 1024, controller);
          console.log(JSON.stringify({{
            length: bytes ? bytes.length : null, aborted: controller.signal.aborted,
          }}));
        """)
        assert outcome == {"length": 4, "aborted": False}


class TestThePageCannotChooseHowMuchWorkThisDoes:
    """The serializer runs inside the page. These are its ceilings."""

    def _serialize(self, body_html: str, limits: dict | None = None):
        return _run(f"""
          import {{ parseHTML }} from "linkedom";
          import {{ serializePage }} from "{SERIALIZE_JS}";
          import {{ LIMITS }} from "{LIMITS_JS}";
          const {{ document }} = parseHTML({json.dumps(body_html)});
          globalThis.document = document;
          globalThis.location = {{ href: "https://www.nejm.org/doi/full/10.1056/x" }};
          const limits = Object.assign({{}}, LIMITS, {json.dumps(limits or {})});
          const page = serializePage("0123456789abcdef", {{ id: "nejm" }}, limits);
          console.log(JSON.stringify({{
            error: page.error || null,
            assets: (page.assets || []).length,
            overflow: page.assets_overflow || 0,
            figures: (page.figures || []).length,
            metaKeys: (page.meta_block || "").split("\\u001e").filter(Boolean).length,
            title: (page.meta || {{}}).title || "",
          }}));
        """, need_dom=True)

    def test_the_asset_list_stops_at_the_ceiling_and_says_so(self):
        images = "".join(f'<img src="https://www.nejm.org/a{i}.png">' for i in range(400))
        page = self._serialize(f"<html><body><article>{images}</article></body></html>")
        assert page["assets"] == 300
        assert page["overflow"] == 100, "a dropped asset must be counted, not forgotten"

    def test_the_figure_manifest_stops_at_the_ceiling(self):
        figures = "".join(
            f'<figure id="f{i}"><img src="https://www.nejm.org/f{i}.png" alt="Figure{i}">'
            f"<figcaption>Figure {i}.</figcaption></figure>"
            for i in range(1, 60)
        )
        page = self._serialize(
            f"<html><body><article>{figures}</article></body></html>", {"maxFigures": 10}
        )
        assert page["figures"] == 10

    def test_a_document_past_the_ceiling_is_refused_rather_than_truncated(self):
        """Half a document parses into something nobody wrote."""

        page = self._serialize(
            "<html><body><article><p>" + ("word " * 400) + "</p></article></body></html>",
            {"maxDocumentChars": 512},
        )
        assert page["error"] == "page_too_large"

    def test_metadata_keys_and_values_are_both_bounded(self):
        metas = "".join(f'<meta name="k{i}" content="v{i}">' for i in range(300))
        long_value = "x" * 5000
        page = self._serialize(
            f'<html><head>{metas}<meta name="citation_title" content="{long_value}">'
            "</head><body><article><p>text</p></article></body></html>",
            {"maxMetaKeys": 50, "maxMetaValueChars": 100},
        )
        assert page["metaKeys"] <= 50
        assert len(page["title"]) <= 100


def test_the_injected_defaults_match_the_shared_table():
    """The serializer carries a copy, because an injected function cannot import.

    A copy that drifts is a ceiling that is not the ceiling anyone reviewed.
    """

    source = (EXTENSION / "serialize.js").read_text(encoding="utf-8")
    limits = _run(f"""
      import {{ LIMITS }} from "{LIMITS_JS}";
      console.log(JSON.stringify(LIMITS));
    """)
    block = source.split("const cap = Object.assign({", 1)[1].split("}, limits || {});", 1)[0]
    named = dict(
        (part.split(":")[0].strip(), part.split(":", 1)[1].strip())
        for part in block.replace("\n", " ").split(",")
        if ":" in part
    )
    assert named, "the serializer no longer carries its defaults"
    def number(expression: str) -> int:
        value = 1
        for factor in expression.split("*"):
            value *= int(factor.strip())
        return value

    for key, expression in named.items():
        assert key in limits, f"{key} is not in the shared table"
        assert number(expression) == limits[key], key


def test_the_payload_is_refused_before_it_is_sent():
    source = (EXTENSION / "capture.js").read_text(encoding="utf-8")
    assert "capture_too_large" in source
    assert "LIMITS.maxPayloadBytes" in source
