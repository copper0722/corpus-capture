"""What a receiver accepts, and what it refuses before it costs anything.

Purpose
-------
A receiver is reachable by whoever can reach it, and the first thing it does
with a request is the most expensive thing it does: parse it. The audit of
v0.1.0 found the reference receiver declaring a whole ``dict`` body and calling
its authentication check inside the handler, so an unauthenticated caller could
spend the receiver's memory before anything looked at the token, and its only
size limit covered ``html`` and ran afterwards.

So the order is fixed here, in one place a receiver can import rather than
reimplement: authenticate, then bound the body, then parse, then validate
against an exact schema. Every step refuses with a code and never repairs.

Inputs
------
A ``Content-Length`` value, the raw body bytes, and the parsed submission.

Outputs
-------
A validated submission, or :class:`SubmissionError`. A bounded receipt store
for the state a receiver keeps between the two requests of an intake.

State changes
-------------
:class:`ReceiptStore` holds receipts in memory, with a TTL and a ceiling.
Nothing else here keeps state.

Failure behavior
----------------
Every refusal is a :class:`SubmissionError` carrying a stable code and no
payload content. A malformed field is never coerced: a receiver that repairs a
producer's mistake makes the producer's next mistake invisible.

Public entrypoints
------------------
``enforce_body_size()``, ``validate_submission()``, ``ReceiptStore``.

Related tests
-------------
``tests/test_submission.py``.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import OrderedDict
from typing import Any

from corpus_capture.sidecar import ACCESS_CLASSES, normalize_doi

#: The whole request body. A capture is a self-contained HTML page with its
#: figures embedded as data URIs, so the ceiling is high; it is still a ceiling,
#: and it is checked against the declared length before a byte is read.
MAX_BODY_BYTES = 40 * 1024 * 1024
#: The payload itself, which is most of the body.
MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 500
MAX_SHORT_TEXT_CHARS = 200
MAX_AUTHORS = 100
MAX_FIGURES = 200
MAX_CAPTION_CHARS = 2000
MAX_META_KEYS = 40
MAX_META_VALUE_CHARS = 2000
#: Everything that is not the payload, added up. Bounding each field separately
#: still allows forty of them at their individual maximum.
MAX_METADATA_BYTES = 256 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
                     r"(Z|[+-]\d{2}:?\d{2})?)?$")
#: Fields a producer may send. Anything else is refused rather than ignored: an
#: unknown key is either a producer talking to a different receiver or a caller
#: trying to reach a field this one does not mean to expose.
ALLOWED_FIELDS = frozenset({
    "url", "final_url", "html", "sha256", "captured_at", "doi", "title",
    "date_published", "publisher_meta", "authors", "access", "meta_sha256",
    "profile", "figures", "capture_tool", "container_selector",
    "decorative_count", "scoped_to_article",
})
#: Fields a producer may NOT send at any size. Identity, filing and rights are
#: the receiver's to decide; a producer that asserts them is refused loudly
#: rather than having them quietly dropped.
FORBIDDEN_FIELDS = frozenset({
    "source_uid", "bundle_id", "bundle_path", "rights", "tags", "receipt_id",
    "handoff_id", "state", "reader_url",
})


class SubmissionError(ValueError):
    """A typed refusal carrying a stable code and no payload content."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def enforce_body_size(content_length: Any, *, limit: int = MAX_BODY_BYTES) -> int:
    """Refuse an oversized request before its body is read. F-09.

    A declared length is a claim, so a receiver must also cap what it actually
    reads; this is the free half of the check, and the one that stops the
    allocation from happening at all. A body with no declared length is not
    refused here -- it is refused by the reader that streams it.
    """

    if content_length in (None, ""):
        return 0
    try:
        declared = int(content_length)
    except (TypeError, ValueError) as exc:
        raise SubmissionError("content_length_malformed") from exc
    if declared < 0:
        raise SubmissionError("content_length_malformed")
    if declared > limit:
        raise SubmissionError("payload_too_large", f"{declared} > {limit}")
    return declared


def _text(payload: dict[str, Any], key: str, *, limit: int, required: bool = False) -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise SubmissionError(f"{key}_missing")
        return None
    # Not coerced. `str(value)` on a dict produces a plausible string and hides
    # the fact that the producer sent something else entirely.
    if not isinstance(value, str):
        raise SubmissionError(f"{key}_not_a_string", type(value).__name__)
    if len(value) > limit:
        raise SubmissionError(f"{key}_too_long", str(len(value)))
    return value


def _metadata_bytes(payload: dict[str, Any]) -> int:
    import json

    return len(json.dumps(
        {key: value for key, value in payload.items() if key != "html"},
        ensure_ascii=False,
    ).encode("utf-8"))


def validate_submission(payload: Any) -> dict[str, Any]:
    """Check one intake submission against an exact schema, or raise.

    Returns the accepted fields, normalized only where normalization is
    lossless: a DOI is lowercased to its bare form, and everything else is
    exactly what the producer sent.
    """

    if not isinstance(payload, dict):
        raise SubmissionError("body_not_an_object")
    unknown = set(payload) - ALLOWED_FIELDS
    forbidden = unknown & FORBIDDEN_FIELDS
    if forbidden:
        # Identity and rights are not the producer's to assert. Refusing is the
        # contract; dropping silently would let a producer believe it had.
        raise SubmissionError("producer_asserted_identity", ", ".join(sorted(forbidden)))
    if unknown:
        raise SubmissionError("unknown_field", ", ".join(sorted(unknown)))

    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        raise SubmissionError("html_missing")
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise SubmissionError("html_too_large")

    sha256 = _text(payload, "sha256", limit=64, required=True)
    if not _SHA256_RE.fullmatch(sha256 or ""):
        raise SubmissionError("sha256_malformed")

    url = _text(payload, "url", limit=MAX_URL_CHARS, required=True)
    if not (url or "").startswith(("http://", "https://")):
        raise SubmissionError("url_not_http")
    final_url = _text(payload, "final_url", limit=MAX_URL_CHARS)

    captured_at = _text(payload, "captured_at", limit=64)
    if captured_at and not _ISO_RE.match(captured_at):
        raise SubmissionError("captured_at_malformed")
    date_published = _text(payload, "date_published", limit=64)

    # F-11: `doi` reached `normalize_doi()`, which calls `.strip()` on anything
    # truthy, so `{"doi": {}}` was a 500 rather than a 400. The type is checked
    # before the value is looked at.
    doi_raw = payload.get("doi")
    if doi_raw is not None and not isinstance(doi_raw, str):
        raise SubmissionError("doi_not_a_string", type(doi_raw).__name__)
    doi = normalize_doi(doi_raw)

    access = payload.get("access", "unknown")
    if not isinstance(access, str) or access not in ACCESS_CLASSES:
        raise SubmissionError("access_unknown", str(access)[:40])

    authors = payload.get("authors", [])
    if not isinstance(authors, list):
        raise SubmissionError("authors_not_a_list")
    if len(authors) > MAX_AUTHORS:
        raise SubmissionError("authors_too_many", str(len(authors)))
    for author in authors:
        if not isinstance(author, str) or len(author) > MAX_SHORT_TEXT_CHARS:
            raise SubmissionError("author_malformed")

    meta = payload.get("publisher_meta", {})
    if not isinstance(meta, dict):
        raise SubmissionError("publisher_meta_not_an_object")
    if len(meta) > MAX_META_KEYS:
        raise SubmissionError("publisher_meta_too_many_keys", str(len(meta)))
    for key, value in meta.items():
        if not isinstance(key, str) or len(key) > MAX_SHORT_TEXT_CHARS:
            raise SubmissionError("publisher_meta_key_malformed")
        if not isinstance(value, str) or len(value) > MAX_META_VALUE_CHARS:
            raise SubmissionError("publisher_meta_value_malformed", key[:40])

    figures = payload.get("figures", [])
    if not isinstance(figures, list):
        raise SubmissionError("figures_not_a_list")
    if len(figures) > MAX_FIGURES:
        raise SubmissionError("figures_too_many", str(len(figures)))
    for figure in figures:
        if not isinstance(figure, dict):
            raise SubmissionError("figure_malformed")
        for field, value in figure.items():
            if not isinstance(field, str) or field not in {
                "figure_id", "label", "caption", "alt", "asset_url", "selector", "position",
            }:
                raise SubmissionError("figure_field_unknown", str(field)[:40])
            if field == "position":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise SubmissionError("figure_position_malformed")
                continue
            limit = MAX_CAPTION_CHARS if field == "caption" else MAX_URL_CHARS
            if not isinstance(value, str) or len(value) > limit:
                raise SubmissionError("figure_field_malformed", field)

    # Each field is bounded, and forty bounded fields still add up.
    if _metadata_bytes(payload) > MAX_METADATA_BYTES:
        raise SubmissionError("metadata_too_large")

    return {
        "url": url,
        "final_url": final_url or url,
        "html": html,
        "sha256": sha256,
        "captured_at": captured_at,
        "doi": doi,
        "title": _text(payload, "title", limit=MAX_TITLE_CHARS),
        "date_published": date_published,
        "publisher_meta": meta,
        "authors": authors,
        "access": access,
        "meta_sha256": _text(payload, "meta_sha256", limit=64),
        "profile": _text(payload, "profile", limit=MAX_SHORT_TEXT_CHARS) or "generic",
        "figures": figures,
        "capture_tool": _text(payload, "capture_tool", limit=MAX_SHORT_TEXT_CHARS),
    }


class ReceiptStore:
    """Receipts, with a ceiling and an expiry. F-10.

    A receiver has to remember a receipt between the POST that creates it and
    the GET that reads it, and the reference implementation remembered every one
    of them forever in a process-global dict. A bearer-token caller could grow
    that without limit; so could an ordinary user over a long enough uptime.

    Oldest-first eviction, because a receipt is read minutes after it is
    written and never again.
    """

    def __init__(self, *, ttl_seconds: int = 24 * 3600, max_entries: int = 5000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._rows: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def _now(self) -> float:
        return time.monotonic()

    def prune(self) -> int:
        """Drop what has expired. Returns how many rows went."""

        cutoff = self._now() - self.ttl_seconds
        gone = 0
        for key in [key for key, (born, _) in self._rows.items() if born < cutoff]:
            self._rows.pop(key, None)
            gone += 1
        return gone

    def put(self, record: dict[str, Any]) -> str:
        self.prune()
        receipt_id = str(uuid.uuid4())
        self._rows[receipt_id] = (self._now(), {**record, "receipt_id": receipt_id})
        while len(self._rows) > self.max_entries:
            self._rows.popitem(last=False)
        return receipt_id

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        self.prune()
        row = self._rows.get(str(receipt_id))
        return dict(row[1]) if row else None

    def update(self, receipt_id: str, **fields: Any) -> dict[str, Any] | None:
        row = self._rows.get(str(receipt_id))
        if not row:
            return None
        born, record = row
        record.update(fields)
        self._rows[str(receipt_id)] = (born, record)
        return dict(record)

    def __len__(self) -> int:
        return len(self._rows)
