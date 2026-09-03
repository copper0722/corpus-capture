# A reference receiver

Everything the extension needs, in one file. It verifies the hash, writes one
envelope directory per capture, and answers the receipt. It does not decide
identity, and that is the point: a receiver may adopt a DOI later, from the
page's own declarations plus a metadata service, but the producer never asserts
one.

Run it:

```bash
pip install -e '.[receiver]'
export CORPUS_CAPTURE_INBOX=/srv/capture-inbox
export CORPUS_CAPTURE_TOKEN="$(openssl rand -hex 32)"
uvicorn receiver:app --host 127.0.0.1 --port 8000
```

Then set the extension's API address to `http://localhost:8000` and paste the
same token into its options page.

## What it must get right

Seven things, and every one of them is a bug someone has already shipped —
four of them in the first version of this file, found by the 2026-09-03 audit.

1. **Do the cheap refusals first.** Authenticate from the headers, then check
   the declared length, then read the body, then parse it, then validate it.
   The first version declared a whole `dict` body and authenticated inside the
   handler, so an unauthenticated caller could spend the receiver's memory
   before anything looked at the token.
2. **A declared length is a claim.** Cap what is actually read as well, and stop
   reading when the cap is passed rather than after.
3. **Validate against an exact schema.** `corpus_capture.validate_submission`
   is that schema: known fields only, exact types, bounded lengths and
   collections, and a total metadata ceiling because forty bounded fields still
   add up. It refuses `doi` values that are not strings, which used to reach
   `.strip()` and produce a 500.
4. **Recompute the hash.** `sha256` in the envelope is the producer's claim
   about `html`. A mismatch is the caller's bug, so it answers `4xx` — and the
   client must not retry it down the offline path, because downloading a payload
   the receiver refused only moves the refusal.
5. **Never build a path from caller-controlled text.** The handoff id is
   generated here, not accepted. A `payload_name` of `../../etc/authorized_keys`
   is exactly what a producer sends when it is not the extension.
6. **Write, then rename.** The envelope is assembled in a staging directory and
   moved into place with a single `os.replace`, so a watcher never observes a
   half-written capture and admits it.
7. **`202`, not `201`.** The bytes are durably received; the artifact does not
   exist yet. `201` would name a resource the caller cannot fetch.

State has ceilings too. Receipts live in a `ReceiptStore` with a TTL and a
maximum, because the first version kept every receipt for the life of the
process, and the inbox is refused once it holds more envelopes than the operator
said it may.

## `receiver.py`

```python
"""A minimal corpus-capture receiver: authenticate, bound, verify, store."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from corpus_capture import (
    MAX_BODY_BYTES,
    SIDECAR_SCHEMA,
    ReceiptStore,
    SubmissionError,
    enforce_body_size,
    public_registry,
    validate_submission,
)

INBOX = Path(os.environ.get("CORPUS_CAPTURE_INBOX", "./capture-inbox")).resolve()
TOKEN = os.environ.get("CORPUS_CAPTURE_TOKEN", "")
#: Envelopes the inbox may hold before this refuses to write another. A token
#: holder can otherwise fill the disk one valid capture at a time.
MAX_ENVELOPES = int(os.environ.get("CORPUS_CAPTURE_MAX_ENVELOPES", "10000"))

app = FastAPI(title="corpus-capture reference receiver")
# The extension's Origin is `chrome-extension://...`, which can never satisfy a
# same-origin check; the service token is the whole authentication story here.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"],
                   allow_methods=["GET", "POST", "OPTIONS"])

RECEIPTS = ReceiptStore(ttl_seconds=24 * 3600, max_entries=5000)


def _authenticate(token: str | None) -> None:
    """Before the body. Constant-time, and refuses when nothing is configured."""

    if not TOKEN or not token or not secrets.compare_digest(token, TOKEN):
        raise HTTPException(status_code=401, detail="bad_service_token")


async def _read_bounded(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    """Read at most `limit` bytes, and stop reading rather than stop at the end."""

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _refuse(error: SubmissionError) -> HTTPException:
    status = 413 if error.code in {"payload_too_large", "html_too_large",
                                   "metadata_too_large"} else 400
    return HTTPException(status_code=status, detail=error.code)


@app.get("/api/v1/capture/profiles")
def profiles() -> dict[str, Any]:
    """The registry, as data. No repository paths reach the browser."""

    return public_registry()


@app.post("/api/v1/intake/html", status_code=202)
async def intake(
    request: Request,
    x_corpus_service_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # Order matters more than any single check here. Cheapest and most
    # restrictive first: a caller with no token never reaches the parser.
    _authenticate(x_corpus_service_token)
    try:
        enforce_body_size(request.headers.get("content-length"))
    except SubmissionError as error:
        raise _refuse(error) from error

    raw = await _read_bounded(request)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="body_not_json") from error
    try:
        submission = validate_submission(payload)
    except SubmissionError as error:
        raise _refuse(error) from error

    body = submission["html"].encode("utf-8")
    # The claim is checked, never trusted. This is the whole reason the field
    # exists: a truncated upload is a valid HTTP request with the wrong bytes.
    actual = hashlib.sha256(body).hexdigest()
    if actual != submission["sha256"]:
        raise HTTPException(status_code=400, detail="sha256_mismatch")

    INBOX.mkdir(parents=True, exist_ok=True)
    if sum(1 for child in INBOX.iterdir() if child.is_dir()) >= MAX_ENVELOPES:
        raise HTTPException(status_code=507, detail="inbox_full")

    # Every path segment below is generated here. Nothing the caller sent is
    # ever joined onto a filesystem path.
    handoff_id = uuid_hex()
    received_at = datetime.now(UTC)
    stem = "capture"
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "payload_name": f"{stem}.html",
        "html_sha256": actual,
        "html_bytes": len(body),
        **{key: submission[key] for key in (
            "url", "final_url", "doi", "title", "date_published", "captured_at",
            "publisher_meta", "authors", "access", "meta_sha256", "profile",
            "figures", "capture_tool",
        )},
    }
    # The transport manifest carries hashes, sizes and times -- and refuses
    # identity: no source_uid, no bundle path, no rights, no tags. Those belong
    # to whatever admits the capture, which has records a browser does not.
    handoff = {
        "handoff_id": handoff_id,
        "producer_key": "chrome-capture",
        "received_at": received_at.isoformat(),
        "payload": {"name": f"{stem}.html", "sha256": actual, "bytes": len(body)},
        "sidecar": {"name": f"{stem}.json", "schema": SIDECAR_SCHEMA},
    }

    staging = Path(tempfile.mkdtemp(dir=INBOX, prefix=".staging-"))
    (staging / f"{stem}.html").write_bytes(body)
    (staging / f"{stem}.json").write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    (staging / "handoff.json").write_text(json.dumps(handoff, indent=1), encoding="utf-8")
    # One rename. A watcher either sees a complete envelope or sees nothing.
    os.replace(staging, INBOX / handoff_id)

    receipt_id = RECEIPTS.put({
        "state": "received",
        "handoff_id": handoff_id,
        "payload_sha256": actual,
        "doi": submission["doi"],
        "source_uid": None,
        "bundle_id": None,
        "reader_url": None,
        "detail": None,
    })
    return {
        "receipt_id": receipt_id,
        "state": "received",
        "payload_sha256": actual,
        "doi": submission["doi"],
    }


@app.get("/api/v1/intake/{receipt_id}")
def receipt(
    receipt_id: str,
    x_corpus_service_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _authenticate(x_corpus_service_token)
    record = RECEIPTS.get(receipt_id)
    if record is None:
        # A receipt that has expired and a receipt that never existed are the
        # same answer on purpose: neither tells a caller what else is stored.
        raise HTTPException(status_code=404, detail="unknown_receipt")
    # `received` is the honest answer while nothing has processed the bytes.
    # It deliberately is not "pending" or "processing", which would suggest a
    # worker already holds it.
    return record


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex
```

## What a real receiver adds

The example keeps receipts in memory and stops at the envelope. A durable one
replaces both ends and nothing in between:

- receipts in a database, so a restart does not lose the caller's only handle;
- request-size limits in the reverse proxy as well, so an oversized body is
  refused before it reaches this process at all;
- rate and concurrency limits per token, and a disk quota on the inbox rather
  than only a count;
- a worker that picks the envelope up, resolves identity from the page's own
  `<meta>` declarations plus a metadata service, and moves the receipt through
  `queued → claimed → admitted | duplicate | error`;
- a `reader_url` on the receipt once the artifact exists, on the receiver's own
  origin, because the extension refuses to offer a link anywhere else;
- a watcher on the browser's download directory for the offline path, admitting
  an `.html` **only** when its sidecar is beside it, names it, and its bytes
  hash to what the sidecar says. A filename is not evidence: a leftover `.json`
  otherwise lends its DOI to whatever file next takes the same stem.
