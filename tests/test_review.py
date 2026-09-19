"""The reader's review: built by the side panel, checked by every receiver.

The panel's pure helpers run under node here, and what they emit is fed to the
receiver's validator, so the two halves of the contract cannot drift apart.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from corpus_capture.sidecar import SidecarError, validate_reader_review, validate_sidecar
from corpus_capture.submission import SubmissionError, validate_finalize

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
REVIEW_JS = (EXTENSION / "review.js").as_posix()
NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this host")

CAPTURE = {
    "title": "Artificial Intelligence and the Workforce | NEJM",
    "date_published": "2026-09-10",
    "authors": ["Ada Lovelace", "  ", "Grace Hopper"],
    "publisher_meta": {
        "title": "Artificial Intelligence and the Workforce",
        "journal": "N Engl J Med",
        "publication_date": "2026/09/10",
        "volume": "395",
        "issue": "11",
        "first_page": "1001",
        "last_page": "1003",
        "issn": "0028-4793",
    },
}
RECEIVER = {
    "metadata_source": "registry",
    "metadata": {
        "title": "Artificial Intelligence and the Workforce",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "journal": "New England Journal of Medicine",
        "published": "2026-09-10",
        "volume": "395", "issue": "11", "pages": "1001-1003",
        "issn": "0028-4793", "publisher": "Massachusetts Medical Society",
    },
}


def _run(body: str):
    script = f'import * as review from "{REVIEW_JS}";\n{body}'
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _review(**overrides):
    base = {
        "decision": "confirmed",
        "doi": "10.1056/nejmp2607831",
        "detected_doi": "10.1056/nejmp2607831",
        "metadata": dict(RECEIVER["metadata"]),
        "changed": [],
        "reviewed_at": "2026-09-17T07:00:00+00:00",
    }
    base.update(overrides)
    return base


@needs_node
def test_the_page_declarations_become_the_review_fields():
    fields = _run(f"console.log(JSON.stringify(review.pageMetadata({json.dumps(CAPTURE)})));")
    assert fields == {
        "title": "Artificial Intelligence and the Workforce",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "journal": "N Engl J Med",
        # Google Scholar's 2026/09/10 is shown as the ISO day it names.
        "published": "2026-09-10",
        "volume": "395", "issue": "11", "pages": "1001-1003",
        "issn": "0028-4793", "publisher": "",
    }


@needs_node
@pytest.mark.parametrize(
    ("volume", "issue", "expected"),
    [
        # Human Kinetics, ahead of print (the capture that found this).
        ("-1", "aop", ("", "")),
        # Taylor & Francis, Latest Articles.
        ("0", "0", ("", "")),
        # A real supplement issue the registry records as 0 survives.
        ("40", "0", ("40", "0")),
        ("12", "Online First", ("12", "")),
        ("Ahead of Print", "", ("", "")),
        (" -2 ", "Suppl 1", ("", "Suppl 1")),
        ("36", "6", ("36", "6")),
    ],
)
def test_a_placeholder_enumeration_is_never_proposed(volume, issue, expected):
    meta = {**CAPTURE["publisher_meta"], "volume": volume, "issue": issue}
    capture = {**CAPTURE, "publisher_meta": meta}
    fields = _run(f"console.log(JSON.stringify(review.pageMetadata({json.dumps(capture)})));")
    assert (fields["volume"], fields["issue"]) == expected


@needs_node
def test_a_page_day_refines_a_registry_year_but_never_overrides_a_registry_day():
    merged = _run(
        "const day = (d) => ({publisher_meta: {publication_date: d}});"
        "const said = (d) => ({metadata_source: 'registry', metadata: {published: d}});"
        "const page = review.pageMetadata(day('2026/09/15'));"
        "const year = review.mergeProposal(page, said('2026'));"
        "const other = review.mergeProposal(page, said('2026-10-01'));"
        "const impossible = review.pageMetadata(day('2026/02/30'));"
        "console.log(JSON.stringify({year, other, impossible: impossible.published}));"
    )
    assert merged["year"]["values"]["published"] == "2026-09-15"
    assert merged["year"]["sources"]["published"] == "page"
    assert "published" not in merged["year"]["alternatives"]
    assert merged["other"]["values"]["published"] == "2026-10-01"
    assert merged["other"]["alternatives"]["published"] == {"source": "page", "value": "2026-09-15"}
    # Not a real day: shown as the page printed it, for the reader to judge.
    assert merged["impossible"] == "2026/02/30"


@needs_node
def test_the_receiver_record_wins_and_the_page_value_stays_visible():
    merged = _run(
        f"const page = review.pageMetadata({json.dumps(CAPTURE)});"
        f"console.log(JSON.stringify(review.mergeProposal(page, {json.dumps(RECEIVER)})));"
    )
    assert merged["values"]["journal"] == "New England Journal of Medicine"
    assert merged["sources"]["journal"] == "registry"
    assert merged["alternatives"]["journal"] == {"source": "page", "value": "N Engl J Med"}
    # Agreement is not a disagreement: no alternative where the values match.
    assert "title" not in merged["alternatives"] and "volume" not in merged["alternatives"]
    # A field only the receiver has keeps its source; nothing is invented.
    assert merged["values"]["publisher"] == "Massachusetts Medical Society"


@needs_node
def test_without_a_receiver_the_page_is_the_proposal():
    merged = _run(
        f"const page = review.pageMetadata({json.dumps(CAPTURE)});"
        "console.log(JSON.stringify(review.mergeProposal(page, null)));"
    )
    assert merged["values"]["journal"] == "N Engl J Med"
    assert merged["sources"]["publisher"] == "" and merged["alternatives"] == {}


@needs_node
def test_a_confirmation_and_a_correction_are_told_apart_and_accepted():
    built = _run(
        "const proposed = {doi: '10.1056/NEJMp2607831', "
        f"metadata: {json.dumps(RECEIVER['metadata'])}}};"
        "const same = review.buildReaderReview({detectedDoi: '10.1056/NEJMp2607831', proposed,"
        "  final: {doi: ' https://doi.org/10.1056/NEJMp2607831 ', metadata: proposed.metadata},"
        "  reviewedAt: new Date('2026-09-17T07:00:00Z')});"
        "const fixed = review.buildReaderReview({detectedDoi: '10.1056/NEJMp2607831', proposed,"
        "  final: {doi: '10.1056/NEJMp2607832', metadata: {...proposed.metadata, volume: '396'}},"
        "  reviewedAt: new Date('2026-09-17T07:00:00Z')});"
        "const none = review.buildReaderReview({detectedDoi: '10.1056/NEJMp2607831', proposed,"
        "  final: {doi: '', metadata: proposed.metadata}});"
        "let refused = null;"
        "try { review.buildReaderReview({detectedDoi: null, proposed,"
        "  final: {doi: 'NEJMp2607832', metadata: proposed.metadata}}); }"
        "catch (e) { refused = e.message; }"
        "console.log(JSON.stringify({same, fixed, none, refused}));"
    )
    assert built["same"]["decision"] == "confirmed" and built["same"]["changed"] == []
    assert built["same"]["doi"] == "10.1056/nejmp2607831"
    assert built["fixed"]["decision"] == "corrected"
    assert built["fixed"]["changed"] == ["doi", "volume"]
    assert built["none"]["doi"] is None and built["none"]["changed"] == ["doi"]
    assert built["refused"] == "doi_invalid"
    # What the panel emits is what a receiver accepts, unchanged.
    for name in ("same", "fixed", "none"):
        assert validate_reader_review(built[name])["decision"] == built[name]["decision"]


@needs_node
def test_a_polled_receipt_never_erases_what_the_extension_knows():
    capture_js = (EXTENSION / "capture.js").as_posix()
    script = f"""
      import {{ mergeReceipt }} from "{capture_js}";
      const row = {{receipt_id: "r1", state: "held", doi: "10.1/x", title: "T"}};
      const fresh = {{state: "received", doi: null, detail: undefined, source_uid: "s1"}};
      console.log(JSON.stringify(mergeReceipt(row, fresh)));
    """
    result = subprocess.run([NODE, "--input-type=module", "--eval", script],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "receipt_id": "r1", "state": "received", "doi": "10.1/x", "title": "T",
        "source_uid": "s1",
    }
    for module in ("sidepanel.js", "service-worker.js"):
        source = (EXTENSION / module).read_text(encoding="utf-8")
        assert "mergeReceipt(" in source and "...fresh" not in source, module


def test_finalize_carries_a_valid_review():
    body = validate_finalize({"attachments": [], "complete": True, "reader_review": _review()})
    assert body["reader_review"]["doi"] == "10.1056/nejmp2607831"
    assert "reader_review" not in validate_finalize({"attachments": [], "complete": True})


@pytest.mark.parametrize("overrides, code", [
    ({"decision": "maybe"}, "reader_review_decision_invalid"),
    ({"changed": ["doi"]}, "reader_review_decision_invalid"),
    ({"decision": "corrected", "changed": ["source_uid"]}, "reader_review_changed_invalid"),
    ({"doi": "not-a-doi"}, "reader_review_doi_invalid"),
    ({"reviewed_at": "2026-09-17T07:00:00"}, "reader_review_time_invalid"),
    ({"metadata": {**RECEIVER["metadata"], "tags": "x"}}, "reader_review_metadata_invalid"),
    ({"metadata": {**RECEIVER["metadata"], "title": "x" * 1001}}, "reader_review_metadata_invalid"),
    ({"metadata": {**RECEIVER["metadata"], "authors": ["x"] * 101}},
     "reader_review_metadata_invalid"),
    ({"source_uid": "abc"}, "reader_review_unknown_field"),
])
def test_a_malformed_review_is_refused_at_finalize(overrides, code):
    with pytest.raises(SubmissionError) as info:
        validate_finalize({"attachments": [], "reader_review": _review(**overrides)})
    assert info.value.code == code


def test_a_sidecar_with_a_review_is_still_a_sidecar():
    sidecar = {
        "schema": "corpus-capture-sidecar-v3", "url": "https://www.nejm.org/doi/full/10.1056/x",
        "html_sha256": "a" * 64, "payload_name": "capture.html",
        "reader_review": _review(decision="corrected", doi=None, changed=["doi"]),
        "page_declared": {"doi": "10.1056/x"},
    }
    assert validate_sidecar(sidecar, payload_name="capture.html") is sidecar
    sidecar["reader_review"] = {"decision": "confirmed"}
    with pytest.raises(SidecarError):
        validate_sidecar(sidecar)


def test_the_identity_step_is_documented_on_both_sides():
    protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    receiver = (ROOT / "docs" / "receiver-reference.md").read_text(encoding="utf-8")
    assert "## `GET /api/v1/intake/{receipt_id}/identity`" in protocol
    assert '"reader_review": {' in protocol and "`page_declared`" in protocol
    assert '@app.get("/api/v1/intake/{receipt_id}/identity")' in receiver
    assert 'review=body.get("reader_review")' in receiver
    assert 'sidecar["page_declared"]' in receiver
    assert "## `GET /api/v1/capture/identity?doi=`" in protocol
    assert "/api/v1/capture/identity?doi=" in (EXTENSION / "capture.js").read_text(encoding="utf-8")


# What a Science news story declares: no citation_* at all, the DOI under
# `publication_doi`, and the byline, moment and publisher only in JSON-LD beside
# a breadcrumb list and a block that does not parse.
NEWS_PAGE = """<!doctype html><html><head><title>Fallback title</title>
<meta property="og:type" content="article">
<meta property="og:title" content="A synthetic news story">
<meta name="publication_doi" content="10.1126/science.z000000">
<link rel="canonical" href="https://news.example/content/article/a-synthetic-news-story">
<script type="application/ld+json">{ not json</script>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "BreadcrumbList",
 "name": "Breadcrumbs", "author": {"name": "Nobody"}}</script>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "NewsArticle",
 "author": [{"@type": "Person", "name": "Ada Lovelace"}, "Grace Hopper", {"@type": "Person"}],
 "publisher": {"@type": "Organization", "name": "A Synthetic Society"},
 "headline": "A synthetic news story", "datePublished": "2026-09-15T18:45:00.000Z"}</script>
</head><body><article><p>body</p></article></body></html>"""

# The same story on a page whose <meta> does speak: the JSON-LD is not asked.
DECLARED_NEWS_PAGE = NEWS_PAGE.replace(
    '<meta name="publication_doi"',
    '<meta name="citation_author" content="Meta Author">'
    '<meta name="citation_publication_date" content="2026/09/14">'
    '<meta name="citation_publisher" content="Meta Publisher">'
    '<meta name="publication_doi"',
)


def _declared(page_html: str):
    """What the preview and the saved capture each read from one page."""

    if not (ROOT / "node_modules" / "linkedom").is_dir():
        pytest.skip("linkedom is not installed")
    probe_js = (EXTENSION / "identity-probe.js").as_posix()
    address = "https://news.example/content/article/a-synthetic-news-story"
    script = f"""
      import {{ parseHTML }} from "linkedom";
      import {{ DECLARATION_KEYS, probePageIdentity }} from "{probe_js}";
      import {{ serializePage }} from "{(EXTENSION / "serialize.js").as_posix()}";
      import {{ LIMITS }} from "{(EXTENSION / "limits.js").as_posix()}";
      const {{ document }} = parseHTML({json.dumps(page_html)});
      globalThis.document = document;
      globalThis.location = {{ href: "{address}" }};
      const probe = probePageIdentity(DECLARATION_KEYS, LIMITS);
      const saved = serializePage("0123456789abcdef", {{ id: "generic" }}, LIMITS);
      const pick = (doi, date, authors, publisher) => ({{ doi, date, authors, publisher }});
      console.log(JSON.stringify({{
        error: saved.error || null,
        preview: pick(probe.doi, probe.date_published, probe.authors,
                      probe.publisher_meta.publisher || ""),
        saved: pick(saved.meta.doi, saved.meta.date_published, saved.authors,
                    saved.publisher_meta.publisher || ""),
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
        check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_a_news_page_is_read_where_it_declares_itself():
    """`publication_doi` and JSON-LD, by the preview and the saved capture alike."""

    declared = _declared(NEWS_PAGE)
    assert declared["error"] is None
    assert declared["preview"] == {
        "doi": "10.1126/science.z000000",
        "date": "2026-09-15T18:45:00.000Z",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "publisher": "A Synthetic Society",
    }
    assert declared["saved"] == declared["preview"]


@needs_node
def test_json_ld_is_asked_only_where_the_meta_said_nothing():
    declared = _declared(DECLARED_NEWS_PAGE)
    assert declared["preview"] == {
        "doi": "10.1126/science.z000000",
        "date": "2026/09/14",
        "authors": ["Meta Author"],
        "publisher": "Meta Publisher",
    }
    assert declared["saved"] == declared["preview"]


@needs_node
def test_a_declared_moment_is_proposed_as_its_day():
    days = _run(
        "console.log(JSON.stringify(['2026-09-15T18:45:00.000Z', '2026-09-15T14:45:00-04:00',"
        " '2026/9/5', '2026-02-31T10:00:00Z', '2026-09-15Tjunk', '2026'].map(review.isoDay)));"
    )
    assert days == ["2026-09-15", "2026-09-15", "2026-09-05",
                    "2026-02-31T10:00:00Z", "2026-09-15Tjunk", "2026"]
    page = _run(
        "console.log(JSON.stringify(review.pageMetadata("
        "{date_published: '2026-09-15T18:45:00.000Z', publisher_meta: {}})));"
    )
    assert page["published"] == "2026-09-15"


@needs_node
def test_a_settled_capture_is_listed_beside_its_own_page_only():
    """Yesterday's admitted article says nothing beside today's page."""

    capture_js = (EXTENSION / "capture.js").as_posix()
    shown = _run(f"""
      const {{ receiptsBeside }} = await import("{capture_js}");
      const rows = [
        {{receipt_id: "r1", state: "admitted", doi: "10.1123/ijsnem.2026-0001",
          url: "https://journals.example/view/ijsnem-2026-0001"}},
        {{receipt_id: "r2", state: "received", doi: null, url: "https://other.example/in-flight"}},
        {{receipt_id: "r3", state: "needs_identity_review", doi: null, url: "https://other.example/waiting"}},
        {{receipt_id: "r4", state: "error", doi: null, url: "https://other.example/failed"}},
        {{receipt_id: null, state: "downloaded", doi: null, url: "https://news.example/story"}},
      ];
      const ids = (page) => receiptsBeside(rows, page).map((row) => row.receipt_id || row.state);
      console.log(JSON.stringify({{
        elsewhere: ids({{doi: "10.1126/science.z000000", urls: ["https://news.example/other-story"]}}),
        nowhere: ids(null),
        byDoi: ids({{doi: "https://doi.org/10.1123/IJSNEM.2026-0001", urls: ["https://mirror.example/x"]}}),
        byUrl: ids({{doi: null, urls: ["https://news.example/story#comments", ""]}}),
        failedHere: ids({{doi: null, urls: ["https://other.example/failed"]}}),
      }}));
    """)
    assert shown == {
        "elsewhere": ["r2", "r3"],
        "nowhere": ["r2", "r3"],
        "byDoi": ["r1", "r2", "r3"],
        "byUrl": ["r2", "r3", "downloaded"],
        "failedHere": ["r2", "r3", "r4"],
    }


# What Chrome leaves in a page it is translating: a class on <html>, and the
# translated text wrapped in <font> elements.
TRANSLATED_BY_CLASS = NEWS_PAGE.replace(
    "<html>", '<html class="no-js translated-ltr" lang="zh-TW">'
)
TRANSLATED_BY_WRAPPER = NEWS_PAGE.replace(
    "<p>body</p>",
    '<p><font style="vertical-align: inherit;"><font style="vertical-align: inherit;">'
    "內文</font></font></p>",
)
# A page that only looks like one: a class that merely contains the word, and a
# <font> the publisher wrote.
NOT_TRANSLATED = NEWS_PAGE.replace("<html>", '<html class="untranslated-ltr-theme">').replace(
    "<p>body</p>", '<p><font color="red">body</font></p>'
)


def _translation(page_html: str):
    if not (ROOT / "node_modules" / "linkedom").is_dir():
        pytest.skip("linkedom is not installed")
    probe_js = (EXTENSION / "identity-probe.js").as_posix()
    script = f"""
      import {{ parseHTML }} from "linkedom";
      import {{ DECLARATION_KEYS, probePageIdentity }} from "{probe_js}";
      import {{ serializePage }} from "{(EXTENSION / "serialize.js").as_posix()}";
      import {{ LIMITS }} from "{(EXTENSION / "limits.js").as_posix()}";
      const {{ document }} = parseHTML({json.dumps(page_html)});
      globalThis.document = document;
      globalThis.location = {{ href: "https://news.example/story" }};
      const saved = serializePage("0123456789abcdef", {{ id: "generic" }}, LIMITS);
      console.log(JSON.stringify({{
        preview: probePageIdentity(DECLARATION_KEYS, LIMITS).translated,
        saved: saved.error || null, html: Boolean(saved.html),
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
        check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
@pytest.mark.parametrize(
    "page_html", [TRANSLATED_BY_CLASS, TRANSLATED_BY_WRAPPER], ids=["html-class", "font-wrapper"]
)
def test_a_page_the_browser_is_translating_is_not_saved_as_the_source(page_html):
    """A machine translation of the top of an article is not the article."""

    assert _translation(page_html) == {"preview": True, "saved": "page_translated", "html": False}


@needs_node
@pytest.mark.parametrize("page_html", [NEWS_PAGE, NOT_TRANSLATED], ids=["plain", "lookalike"])
def test_a_page_in_its_own_words_is_saved(page_html):
    assert _translation(page_html) == {"preview": False, "saved": None, "html": True}


def test_the_reader_is_told_how_to_save_a_translated_page():
    capture = (EXTENSION / "capture.js").read_text(encoding="utf-8")
    assert "throw new Error(PAGE_REFUSALS[page.error] || page.error)" in capture
    assert "顯示原文" in capture[capture.index("PAGE_REFUSALS"):capture.index("capturePage(")]
    panel = (EXTENSION / "sidepanel.js").read_text(encoding="utf-8")
    assert "pageNote(page.translated ? PAGE_REFUSALS.page_translated" in panel


PROBE_PAGE = """<!doctype html><html><head><title>Fallback title</title>
<meta name="citation_doi" content="10.1123/ijsnem.2026-0001">
<meta name="citation_title" content="UCI Sports Nutrition Project">
<meta name="citation_author" content="Ada Lovelace">
<meta name="citation_author" content="Grace Hopper">
<meta name="citation_journal_title" content="Int J Sport Nutr Exerc Metab">
<meta name="citation_firstpage" content="1"><meta name="citation_lastpage" content="20">
<meta property="og:site_name" content="Human Kinetics">
<link rel="canonical" href="https://journals.example/doi/10.1123/ijsnem.2026-0001">
</head><body><p>body</p></body></html>"""


@needs_node
def test_the_preview_reads_only_the_page_declarations():
    if not (ROOT / "node_modules" / "linkedom").is_dir():
        pytest.skip("linkedom is not installed")
    probe_js = (EXTENSION / "identity-probe.js").as_posix()
    limits_js = (EXTENSION / "limits.js").as_posix()
    script = f"""
      import {{ parseHTML }} from "linkedom";
      import {{ DECLARATION_KEYS, probePageIdentity }} from "{probe_js}";
      import {{ LIMITS }} from "{limits_js}";
      const {{ document }} = parseHTML({json.dumps(PROBE_PAGE)});
      globalThis.document = document;
      globalThis.location = {{ href: "https://journals.example/view/ijsnem-2026-0001" }};
      console.log(JSON.stringify(probePageIdentity(DECLARATION_KEYS, LIMITS)));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
        check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    page = json.loads(result.stdout.strip().splitlines()[-1])
    assert page["doi"] == "10.1123/ijsnem.2026-0001"
    assert page["title"] == "UCI Sports Nutrition Project"
    assert page["authors"] == ["Ada Lovelace", "Grace Hopper"]
    assert page["publisher_meta"] == {
        "title": "UCI Sports Nutrition Project", "journal": "Int J Sport Nutr Exerc Metab",
        "first_page": "1", "last_page": "20",
    }
    assert page["canonical_url"] == "https://journals.example/doi/10.1123/ijsnem.2026-0001"


def test_the_preview_uses_the_serializer_s_keys():
    """The preview must not show an identity the saved capture would not carry."""

    probe = (EXTENSION / "identity-probe.js").read_text(encoding="utf-8")
    serializer = (EXTENSION / "serialize.js").read_text(encoding="utf-8")
    import re

    keys = set(re.findall(r'"([a-z_.:]+[A-Za-z]*)"', probe[probe.index("DECLARATION_KEYS"):
                                                            probe.index("export function")]))
    keys -= {"publisher_meta", "doi", "title", "date_published", "authors", "journal",
             "publisher", "publication_date", "volume", "issue", "first_page", "last_page", "issn"}
    assert keys, "no keys parsed"
    missing = sorted(key for key in keys if f'"{key}"' not in serializer)
    assert not missing, missing
