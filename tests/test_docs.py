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


def test_the_receiver_never_joins_caller_text_onto_a_path():
    """The one bug this example must not teach."""

    source = _receiver_source()
    assert "uuid.uuid4().hex" in source
    assert 'stem = "capture"' in source
    assert "os.replace(staging" in source


def test_the_audit_scope_is_present_for_the_reviewer():
    scope = (ROOT / "docs" / "SECURITY-AUDIT-SCOPE.md").read_text(encoding="utf-8")
    for heading in ("Manifest and permission minimisation", "Token storage and transport",
                    "Fixtures and restricted full text", "Receiver contract"):
        assert heading in scope
