"""The side panel on the receiver's own reading page.

The reading page declares the work it shows with standard `citation_*` <meta>;
the panel turns that, plus the receiver's record for a declared DOI, into one
read-only card. The pure half runs under node here; the injected watcher runs
here against a minimal DOM, and in Chromium in the end-to-end check.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
VIEW_JS = (EXTENSION / "bundle-view.js").as_posix()
NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this host")

# What the Corpus Reader declares for a bundle it shows (its own record: the
# authors are often missing there, the journal may be the publisher's name).
DECLARED = {
    "title": ["UCI Sports Nutrition Project: Position Statement on Nutrition for Cycling"],
    "journal": ["Human Kinetics"],
    "published": ["2026/09/15"],
    "volume": ["-1"],
    "issue": ["aop"],
    "doi": ["https://doi.org/10.1123/IJSNEM.2026-0135"],
    "pmid": ["42687023"],
    "publisher": ["Human Kinetics"],
    "keywords": ["運動營養", "cycling", "運動營養", " "],
    "type": ["臨床指引"],
}
# The receiver's record for that DOI (GET /api/v1/capture/identity).
IDENTITY = {
    "metadata_source": "registry",
    "metadata_status": "resolved",
    "metadata": {
        "title": "UCI Sports Nutrition Project: Position Statement on Nutrition for Cycling",
        "authors": ["Louise M. Burke", "Eimear Dolan", "Javier T. Gonzalez", "Iñigo Mujika",
                    "Jamie Whitfield"],
        "journal": "International Journal of Sport Nutrition and Exercise Metabolism",
        "published": "2026",
        "volume": "", "issue": "", "pages": "1-27",
        "issn": "1526-484X", "publisher": "Human Kinetics",
    },
}


def _run(body: str):
    script = f'import * as view from "{VIEW_JS}";\n{body}'
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_the_reading_page_is_the_receivers_own_origin():
    cases = [
        ("https://corpus.example/#/reader?item=r_1", "https://corpus.example", True),
        ("https://corpus.example/index.html?x=1#/search", "https://corpus.example", True),
        ("https://corpus.example:8443/", "https://corpus.example", False),
        ("http://corpus.example/", "https://corpus.example", False),
        ("https://publisher.example/doi/10.1/x", "https://corpus.example", False),
        ("https://corpus.example/", "", False),
        ("chrome://newtab/", "https://corpus.example", False),
        ("http://127.0.0.1:8750/#/search", "http://127.0.0.1:8750", True),
    ]
    got = _run(
        f"console.log(JSON.stringify({json.dumps(cases)}"
        ".map(([u, b]) => view.isReceiverPage(u, b))));"
    )
    assert got == [expected for _url, _base, expected in cases]


@needs_node
def test_the_card_is_what_the_page_declares_when_the_receiver_has_nothing():
    card = _run(f"console.log(JSON.stringify(view.bundleCard({json.dumps(DECLARED)}, null)));")
    assert card == {
        "title": "UCI Sports Nutrition Project: Position Statement on Nutrition for Cycling",
        "authors": [],
        # The placeholder volume and issue of an ahead-of-print page are not printed.
        "source": "Human Kinetics · 2026-09-15",
        "container": "Human Kinetics",
        "when": "2026-09-15",
        "doi": "10.1123/ijsnem.2026-0135",
        "pmid": "42687023",
        "type": "臨床指引",
        "tags": ["運動營養", "cycling"],
    }


@needs_node
def test_the_receivers_record_fills_in_what_the_page_leaves_out():
    card = _run(
        f"console.log(JSON.stringify(view.bundleCard({json.dumps(DECLARED)}, "
        f"{json.dumps(IDENTITY)})));"
    )
    assert card["authors"] == IDENTITY["metadata"]["authors"]
    # The registry's journal wins over the publisher name the page printed, and
    # the page's day refines the registry's year instead of losing to it.
    assert card["source"] == (
        "International Journal of Sport Nutrition and Exercise Metabolism · 2026-09-15:1-27"
    )
    assert card["doi"] == "10.1123/ijsnem.2026-0135"


@needs_node
def test_the_source_line_reads_like_a_citation():
    cases = [
        {"journal": "Nature", "published": "2026-09-02", "volume": "657", "issue": "8130",
         "pages": "47-58"},
        {"journal": "Kidney International", "published": "2021", "volume": "100", "issue": "4",
         "pages": ""},
        {"journal": "", "publisher": "Elsevier", "published": "2020", "volume": "", "pages": ""},
        {"journal": "N Engl J Med", "published": "", "volume": "395", "issue": "",
         "pages": "1001-1003"},
        {"journal": "", "publisher": "", "published": "", "volume": "", "pages": ""},
    ]
    got = _run(f"console.log(JSON.stringify({json.dumps(cases)}.map(view.sourceLine)));")
    assert got == [
        "Nature · 2026-09-02;657(8130):47-58",
        "Kidney International · 2021;100(4)",
        "Elsevier · 2020",
        "N Engl J Med · 395:1001-1003",
        "",
    ]


@needs_node
def test_a_long_author_list_is_folded_to_the_first_three_and_the_last():
    names = [f"Author {i}" for i in range(1, 53)]
    got = _run(
        "console.log(JSON.stringify(["
        f"view.authorLine({json.dumps(names)}),"
        f"view.authorLine({json.dumps(names[:4])}),"
        "view.authorLine([])]));"
    )
    assert got[0] == {
        "short": "Author 1, Author 2, Author 3 … Author 52", "total": 52, "folded": True,
    }
    assert got[1] == {
        "short": "Author 1, Author 2, Author 3, Author 4", "total": 4, "folded": False,
    }
    assert got[2] == {"short": "", "total": 0, "folded": False}


@needs_node
def test_a_page_without_a_title_or_doi_declares_no_work():
    got = _run(
        "console.log(JSON.stringify(["
        "view.declaresWork({}),"
        "view.declaresWork({keywords: ['x'], type: ['y']}),"
        "view.declaresWork({title: ['T']}),"
        "view.declaresWork({doi: ['10.1/x']})]));"
    )
    assert got == [False, False, True, True]


def test_only_standard_names_are_read():
    """A receiver needs no vocabulary of ours: Highwire and Dublin Core only."""

    source = (EXTENSION / "bundle-view.js").read_text(encoding="utf-8")
    block = source[source.index("export const BUNDLE_DECLARATION_KEYS"):]
    block = block[:block.index("};")]
    names = re.findall(r'"([^"]+)"', block)
    assert names, "no names parsed"
    assert all(name.startswith(("citation_", "dc.")) for name in names), names


def test_the_protocol_names_every_declaration_the_panel_reads():
    source = (EXTENSION / "bundle-view.js").read_text(encoding="utf-8")
    block = source[source.index("export const BUNDLE_DECLARATION_KEYS"):]
    names = re.findall(r'"([^"]+)"', block[:block.index("};")])
    protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    section = protocol[protocol.index("## Reading pages"):]
    section = section[:section.index("\n## ")]
    assert [name for name in names if f"`{name}`" not in section] == []
    assert "GET /api/v1/capture/identity?doi=" in section


# A DOM just large enough for the injected watcher: <meta> nodes and a
# MutationObserver the test fires by hand.
_DOM = """
const metas = [];
const meta = (name, content) => ({ getAttribute: (key) => (key === "name" ? name : content) });
const observers = [];
globalThis.document = {
  querySelectorAll: () => metas,
  head: { tag: "head" },
  documentElement: { tag: "html" },
};
globalThis.MutationObserver = class {
  constructor(callback) {
    this.callback = callback; this.target = null; this.disconnected = false; observers.push(this);
  }
  observe(target) { this.target = target; }
  disconnect() { this.disconnected = true; }
};
const cap = { maxMetaKeys: 200, maxMetaValues: 100, maxMetaValueChars: 2000 };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
"""


@needs_node
def test_the_watcher_answers_at_once_then_waits_for_the_next_change():
    got = _run(_DOM + """
metas.push(meta("citation_title", "First"), meta("citation_author", "A"),
           meta("citation_author", "B"), meta("og:title", "ignored"));
const now = view.awaitBundleDeclaration(view.BUNDLE_DECLARATION_KEYS, cap, null, 0, 10);
const seen = JSON.stringify(now);
const waiting = view.awaitBundleDeclaration(view.BUNDLE_DECLARATION_KEYS, cap, seen, 5000, 10);
const pending = waiting instanceof Promise;
// The page clears and rewrites its declarations in two steps; one change is reported.
metas.length = 0;
observers[0].callback([]);
metas.push(meta("citation_title", "Second"), meta("citation_doi", "10.1/two"));
observers[0].callback([]);
const next = await waiting;
console.log(JSON.stringify({ now, pending, next, watched: observers[0].target.tag,
                             disconnected: observers[0].disconnected }));
""")
    assert got["now"] == {"title": ["First"], "authors": ["A", "B"]}
    assert got["pending"] is True
    assert got["next"] == {"title": ["Second"], "doi": ["10.1/two"]}
    assert got["watched"] == "head"
    assert got["disconnected"] is True


@needs_node
def test_the_watcher_gives_up_waiting_with_what_is_there():
    got = _run(_DOM + """
metas.push(meta("citation_title", "Same"));
const keys = view.BUNDLE_DECLARATION_KEYS;
const seen = JSON.stringify(view.awaitBundleDeclaration(keys, cap, null, 0, 10));
// A mutation that changes nothing declared (another <meta>) does not end the wait.
const waiting = view.awaitBundleDeclaration(view.BUNDLE_DECLARATION_KEYS, cap, seen, 80, 10);
metas.push(meta("theme-color", "#fff"));
observers[0].callback([]);
await sleep(30);
const early = observers[0].disconnected;
const last = await waiting;
console.log(JSON.stringify({ early, last, disconnected: observers[0].disconnected }));
""")
    assert got == {"early": False, "last": {"title": ["Same"]}, "disconnected": True}
