"""The sidecar contract: what the page declared, beside the bytes it declared it about.

Purpose
-------
A producer may hand over bytes and observations. It may not hand over an
identity. So the transport envelope carries no DOI, and the page's own
declarations travel in a sidecar document beside the payload, as producer
evidence a receiver is free to disbelieve.

Both paths emit the same document. Online it is the JSON body of
``POST /api/v1/intake/html``; offline it is the ``.json`` written next to the
``.html`` in the browser's download directory. One schema, so a receiver has one
rule to implement.

Inputs
------
A sidecar mapping (parsed JSON), and for the offline path a DOI, a URL and a
capture time.

Outputs
-------
The normalized DOI, the offline filename stem both sides must agree on, and a
validated sidecar.

State changes
-------------
None.

Failure behavior
----------------
:func:`validate_sidecar` raises :class:`SidecarError` with a stable code. It
never repairs a sidecar: a document that does not name its payload is refused,
because a leftover ``.json`` would otherwise lend its DOI to whatever file next
takes the same stem.

Public entrypoints
------------------
``normalize_doi()``, ``capture_slug()``, ``download_basename()``,
``attachment_payload_name()``, ``validate_attachments()``, ``validate_sidecar()``.

Related tests
-------------
``tests/test_sidecar.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

#: The current sidecar schema. v3 adds the attachment manifest: every file the
#: page linked -- its PDF, supplements, audio, video -- captured with its hash
#: and stored name, or failed with a reason. v1 and v2 are still accepted: v1
#: omits everything from ``access`` onward, v2 says nothing about attachments,
#: and bytes captured by an older producer are still bytes.
SIDECAR_SCHEMA = "corpus-capture-sidecar-v3"
SIDECAR_SCHEMAS_ACCEPTED = (
    "corpus-capture-sidecar-v1", "corpus-capture-sidecar-v2", "corpus-capture-sidecar-v3",
)
#: What an attachment is, as a receiver files it. ``other`` is a download the
#: page offered that fits none of the four; it is kept and labelled, and never
#: mistaken for the article's PDF.
ATTACHMENT_KINDS = ("pdf", "supplement", "audio", "video", "other")
#: ``duplicate`` is a candidate whose bytes another row already holds (the same
#: PDF behind two links, one video behind two players): complete, stored once.
ATTACHMENT_STATUSES = ("captured", "duplicate", "failed")
#: Stored suffixes a receiver accepts. Anything else is stored as ``.bin`` with
#: its MIME type recorded, so an odd suffix is kept rather than refused.
ATTACHMENT_SUFFIXES = frozenset({
    ".pdf", ".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".webm", ".mov", ".m4v", ".ts",
    ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".tsv", ".txt",
    ".json", ".xml", ".rtf", ".png", ".jpg", ".tif", ".epub", ".bin",
})
#: Attachments one capture may declare. The same ceiling the extension applies.
MAX_ATTACHMENTS = 40
#: One attachment's bytes. The receiver caps what it reads, not only what is declared.
MAX_ATTACHMENT_BYTES = 256 * 1024 * 1024
#: Fields an attachment manifest row may carry. Unknown keys fail closed, for
#: the same reason a submission's do: an extra key is a producer talking to a
#: different receiver.
ATTACHMENT_ROW_FIELDS = frozenset({
    "index", "kind", "url", "label", "source", "status", "payload_name", "sha256",
    "bytes", "mime", "final_url", "reason", "media_id", "nejmdo", "duplicate_of",
})
#: What the reader's session saw. An observation, never a licence: only
#: ``login_required`` is evidence, and it is evidence that the article is NOT
#: open. A page that merely rendered proves nothing, because the browser may
#: have been carrying an entitled session.
ACCESS_CLASSES = ("open", "login_required", "metered", "unknown")
#: Who handed the bytes over, in the envelope a receiver records.
PRODUCER_KEY = "chrome-capture"

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
#: Viewer path segments publishers append AFTER the DOI in a landing-page URL.
#: A DOI inside a URL runs to the end of the path, so any regex that captures it
#: also captures these: ``/do/10.1056/NEJMdo008670/full/`` yielded the identity
#: ``10.1056/nejmdo008670/full/`` on the first real capture.
_DOI_URL_TAIL_RE = re.compile(
    r"/(?:full|abstract|pdf|epdf|epub|html|text|references|figures|metrics)/?$", re.I
)
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class SidecarError(ValueError):
    """A typed refusal carrying a stable code and no payload content."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def normalize_doi(value: str | None) -> str | None:
    """Return a bare lowercase DOI, or None when the string is not one.

    Accepts the three spellings publishers actually serve -- bare, ``doi:``
    prefixed, and a doi.org URL -- because the page's ``<meta>`` tag decides the
    spelling and the producer must not have to normalize on the client.
    """

    text = (value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/",
                   "https://dx.doi.org/", "info:doi/", "doi:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = text.split("?")[0].split("#")[0].rstrip(".,;)")
    for _ in range(3):
        trimmed = _DOI_URL_TAIL_RE.sub("", text)
        if trimmed == text:
            break
        text = trimmed
    text = text.rstrip("/")
    return text.lower() if _DOI_RE.fullmatch(text) else None


def capture_slug(*, doi: str | None, url: str, max_len: int = 72) -> str:
    """A readable, filesystem-safe stem for one capture.

    Identity is NOT derived from this. It exists so a human looking at a
    download directory can tell two captures apart; a receiver reads the
    sidecar. A DOI makes the better stem when the page declared one, and the
    host plus the last path segment when it did not.
    """

    normalized = normalize_doi(doi)
    if normalized:
        raw = normalized
    else:
        parts = urlsplit(url)
        host = parts.hostname or ""
        tail = (parts.path or "").rstrip("/").rsplit("/", 1)[-1] if host else ""
        raw = f"{host}-{tail}" if tail else (host or "page")
    slug = _SLUG_STRIP_RE.sub("-", raw.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "capture"


def download_basename(*, doi: str | None, url: str, captured_at: datetime) -> str:
    """Stem for the offline download fallback: slug plus capture minute.

    The minute, not a handoff id, because this path has no handoff: the pair of
    files lands in the browser's download directory and a watcher carries them
    over. Two captures of one page in one minute collide, and the browser
    resolves that with its own ``(1)`` suffix -- the sidecar still names its
    payload explicitly, so a renamed download stays readable.
    """

    stamp = captured_at.astimezone(UTC).strftime("%Y%m%d-%H%M")
    return f"{capture_slug(doi=doi, url=url)}-{stamp}"


def attachment_payload_name(stem: str, index: int, kind: str, suffix: str | None) -> str:
    """The name an attachment is stored under, beside the capture that owns it.

    ``<stem>--NN-<kind><suffix>``. The double dash cannot occur in a capture
    stem -- the slug allows one dash at a time -- so a receiver can tell an
    attachment from a capture by name alone, and still never trusts the name
    for identity: the sidecar lists each attachment with its hash. Identical on
    both sides (``attachmentPayloadName`` in the extension) because the offline
    path has no receiver to choose the name.
    """

    safe_kind = kind if kind in ATTACHMENT_KINDS else "other"
    text = str(suffix or "").lower()
    safe_suffix = text if text in ATTACHMENT_SUFFIXES else ".bin"
    return f"{stem}--{int(index):02d}-{safe_kind}{safe_suffix}"


def validate_attachment_row(row: Any, *, position: int = 0) -> dict[str, Any]:
    """Check one attachment manifest row, or raise. Returns it unchanged.

    A ``captured`` row must name its payload and its hash; a ``failed`` row must
    say why. Neither may carry a path: the payload name is a basename beside the
    capture, and a name with a separator in it is a producer that is not the
    extension.
    """

    where = f"attachments[{position}]"
    if not isinstance(row, dict):
        raise SidecarError("attachment_not_an_object", where)
    unknown = set(row) - ATTACHMENT_ROW_FIELDS
    if unknown:
        raise SidecarError("attachment_field_unknown", ", ".join(sorted(unknown)))
    kind = row.get("kind")
    if kind not in ATTACHMENT_KINDS:
        raise SidecarError("attachment_kind_unknown", str(kind)[:40])
    status = row.get("status")
    if status not in ATTACHMENT_STATUSES:
        raise SidecarError("attachment_status_unknown", str(status)[:40])
    url = row.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        raise SidecarError("attachment_url_invalid", where)
    if len(url) > 2048:
        raise SidecarError("attachment_url_too_long", where)
    for key in ("label", "source", "reason", "mime", "final_url", "media_id", "nejmdo",
                "payload_name"):
        value = row.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 2048):
            raise SidecarError("attachment_field_malformed", key)
    index = row.get("index")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool) or index < 1):
        raise SidecarError("attachment_index_malformed", where)
    size = row.get("bytes")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise SidecarError("attachment_bytes_malformed", where)
    if status == "captured":
        name = str(row.get("payload_name") or "")
        if (
            not _PAYLOAD_NAME_RE.fullmatch(name) or "/" in name or "\\" in name
            or name in {".", ".."}
        ):
            raise SidecarError("attachment_payload_name_invalid", where)
        if not _SHA256_RE.fullmatch(str(row.get("sha256") or "").lower()):
            raise SidecarError("attachment_hash_malformed", where)
        if not isinstance(size, int) or size <= 0 or size > MAX_ATTACHMENT_BYTES:
            raise SidecarError("attachment_bytes_malformed", where)
    elif status == "duplicate":
        target = row.get("duplicate_of")
        if not isinstance(target, int) or isinstance(target, bool) or target < 1:
            raise SidecarError("attachment_duplicate_without_target", where)
        if target == index:
            raise SidecarError("attachment_duplicate_of_itself", where)
        digest = row.get("sha256")
        if digest is not None and not _SHA256_RE.fullmatch(str(digest).lower()):
            raise SidecarError("attachment_hash_malformed", where)
    elif not str(row.get("reason") or "").strip():
        raise SidecarError("attachment_failed_without_reason", where)
    return row


def validate_attachments(rows: Any) -> list[dict[str, Any]]:
    """Check an attachment manifest, or raise. Returns it unchanged."""

    if not isinstance(rows, list):
        raise SidecarError("attachments_not_a_list")
    if len(rows) > MAX_ATTACHMENTS:
        raise SidecarError("attachments_too_many", str(len(rows)))
    names: set[str] = set()
    captured: set[int] = set()
    for position, row in enumerate(rows):
        validate_attachment_row(row, position=position)
        name = row.get("payload_name")
        if row.get("status") == "captured":
            if name in names:
                raise SidecarError("attachment_payload_name_duplicate", str(name))
            names.add(name)
            if isinstance(row.get("index"), int):
                captured.add(row["index"])
    for position, row in enumerate(rows):
        # A duplicate points at a row that holds the bytes, or it points at
        # nothing and the file it stood for is simply not in the bundle.
        if row.get("status") == "duplicate" and row["duplicate_of"] not in captured:
            raise SidecarError("attachment_duplicate_target_missing", f"attachments[{position}]")
    return rows


def validate_sidecar(payload: Any, *, payload_name: str | None = None) -> dict[str, Any]:
    """Check a sidecar against the contract, or raise. Returns it unchanged.

    ``payload_name`` is the file the caller actually holds. Passing it turns on
    the one check that matters offline: a sidecar must name the payload it
    describes. Without it a leftover ``.json`` lends its DOI to whatever file
    later takes the same stem, and the receiver admits an article under another
    article's identity.
    """

    if not isinstance(payload, dict):
        raise SidecarError("sidecar_not_an_object")
    if payload.get("schema") not in SIDECAR_SCHEMAS_ACCEPTED:
        raise SidecarError("sidecar_schema_unknown", str(payload.get("schema")))
    url = str(payload.get("url") or "").strip()
    if not url or urlsplit(url).scheme not in ("http", "https"):
        raise SidecarError("sidecar_url_missing")
    digest = str(payload.get("html_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(digest):
        raise SidecarError("sidecar_hash_malformed")
    declared = str(payload.get("payload_name") or "").strip()
    if not declared:
        raise SidecarError("sidecar_names_no_payload")
    if payload_name is not None and declared != payload_name:
        raise SidecarError("sidecar_payload_mismatch", declared)
    access = payload.get("access", "unknown")
    if access not in ACCESS_CLASSES:
        raise SidecarError("sidecar_access_unknown", str(access))
    if "attachments" in payload:
        rows = validate_attachments(payload["attachments"])
        # An attachment may not be the payload itself, and may not be named
        # twice under two roles.
        if payload_name is not None and any(
            row.get("payload_name") == payload_name for row in rows
        ):
            raise SidecarError("attachment_is_the_payload")
    for key in ("attachments_complete",):
        if key in payload and not isinstance(payload[key], bool):
            raise SidecarError("sidecar_field_malformed", key)
    for key in ("attachments_discovered",):
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise SidecarError("sidecar_field_malformed", key)
    return payload
