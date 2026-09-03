"""The documented contract and the shipped code are one thing, checked as one.

A protocol document drifts from its implementation silently, and the reference
receiver is the copy a stranger will paste. Both are asserted against the
extension's actual strings, so a renamed endpoint fails here before it fails for
somebody reading the docs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
RECEIVER_DOC = (ROOT / "docs" / "receiver-reference.md").read_text(encoding="utf-8")
CAPTURE_JS = (ROOT / "extension" / "capture.js").read_text(encoding="utf-8")


def _receiver_source() -> str:
    match = re.search(r"## `receiver\.py`\n\n```python\n(.*?)\n```", RECEIVER_DOC, re.S)
    assert match, "the reference receiver is no longer a fenced python block"
    return match.group(1)


def test_the_reference_receiver_is_valid_python():
    """A stranger will paste this. A syntax error is the worst first impression."""

    compile(_receiver_source(), "receiver.py", "exec")


@pytest.mark.parametrize(
    "endpoint",
    ["/api/v1/intake/html", "/api/v1/capture/profiles"],
)
def test_every_endpoint_the_client_calls_is_documented_and_implemented(endpoint: str):
    assert endpoint in CAPTURE_JS
    assert endpoint in PROTOCOL
    assert endpoint in _receiver_source()


def test_the_receipt_route_agrees_across_all_three():
    assert "/api/v1/intake/${encodeURIComponent(receiptId)}" in CAPTURE_JS
    assert "GET /api/v1/intake/{receipt_id}" in PROTOCOL
    assert '@app.get("/api/v1/intake/{receipt_id}")' in _receiver_source()


def test_the_service_token_header_is_spelled_the_same_everywhere():
    assert "x-corpus-service-token" in CAPTURE_JS
    assert "x-corpus-service-token" in PROTOCOL
    assert "x_corpus_service_token" in _receiver_source()


def test_the_receiver_recomputes_the_hash_and_refuses_a_mismatch():
    source = _receiver_source()
    assert "hashlib.sha256(body).hexdigest()" in source
    assert "sha256_mismatch" in source


def test_the_receiver_authenticates_before_it_reads_the_body():
    """F-09: the order is the fix. A caller with no token never reaches the parser."""

    source = _receiver_source()
    authenticate = source.index("_authenticate(x_corpus_service_token)")
    for later in ("enforce_body_size(", "_read_bounded(request)", "json.loads(raw)",
                  "validate_submission(payload)"):
        assert authenticate < source.index(later), later


def test_the_receiver_bounds_the_body_it_reads():
    source = _receiver_source()
    assert "async for chunk in request.stream()" in source
    assert "payload_too_large" in source
    # The old signature parsed a whole dict before anything looked at it.
    assert "Body()" not in source


def test_the_receiver_keeps_no_unbounded_state():
    """F-10: receipts expire, and the inbox has a ceiling."""

    source = _receiver_source()
    assert "ReceiptStore(" in source
    assert "ttl_seconds" in source and "max_entries" in source
    assert "MAX_ENVELOPES" in source and "inbox_full" in source
    assert "RECEIPTS: dict" not in source


def test_the_receiver_never_joins_caller_text_onto_a_path():
    """The one bug this example must not teach."""

    source = _receiver_source()
    assert "uuid.uuid4().hex" in source
    assert 'stem = "capture"' in source
    assert "os.replace(staging" in source


def test_every_audit_finding_has_a_remediation_row():
    """The audit is a document, and a document drifts from the code it describes.

    This is the cheapest available guard: every finding the report raises must
    appear in the remediation table with a commit, and every test named there
    must exist. It cannot tell whether the fix is right -- the tests in those
    files do that -- but it can tell that a finding was not quietly forgotten.
    """

    audit = (ROOT / "docs" / "SECURITY-AUDIT-2026-09-03.md").read_text(encoding="utf-8")
    report, _, remediation = audit.partition("## Remediation")
    assert remediation, "the remediation table is missing"

    raised = set(re.findall(r"^### (F-\d\d) ", report, re.M))
    assert len(raised) == 11, sorted(raised)
    rows = dict(re.findall(r"^\| (F-\d\d) \| \w+ \| `([0-9a-f]{7,40})`", remediation, re.M))
    assert set(rows) == raised, sorted(raised - set(rows))

    for name in set(re.findall(r"`(tests/test_[a-z_]+\.py)", remediation)):
        assert (ROOT / name).is_file(), name


def test_the_changelog_cites_the_audit():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "### Security" in changelog
    assert "SECURITY-AUDIT-2026-09-03.md" in changelog
    assert "F-01" in changelog and "F-11" in changelog


def test_the_audit_scope_is_present_for_the_reviewer():
    scope = (ROOT / "docs" / "SECURITY-AUDIT-SCOPE.md").read_text(encoding="utf-8")
    for heading in ("Manifest and permission minimisation", "Token storage and transport",
                    "Fixtures and restricted full text", "Receiver contract"):
        assert heading in scope
