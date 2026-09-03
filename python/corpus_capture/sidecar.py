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
``validate_sidecar()``.

Related tests
-------------
``tests/test_sidecar.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

#: The current sidecar schema. v1 is still accepted: it omits everything from
#: ``access`` onward, and bytes captured by an older producer are still bytes.
SIDECAR_SCHEMA = "corpus-capture-sidecar-v2"
SIDECAR_SCHEMAS_ACCEPTED = ("corpus-capture-sidecar-v1", "corpus-capture-sidecar-v2")
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
    return payload
