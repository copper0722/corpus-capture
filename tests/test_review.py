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
        "published": "2026/09/10",
        "volume": "395", "issue": "11", "pages": "1001-1003",
        "issn": "0028-4793", "publisher": "",
    }


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
    assert "/identity${query}" in (EXTENSION / "capture.js").read_text(encoding="utf-8")
