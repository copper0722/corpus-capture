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

Four things, and each of them is a bug someone has already shipped:

1. **Recompute the hash.** `sha256` in the envelope is the producer's claim
   about `html`. A mismatch is the caller's bug, so it answers `4xx` — and the
   client must not retry it down the offline path, because downloading a payload
   the receiver refused only moves the refusal.
2. **Never build a path from caller-controlled text.** The handoff id is
   generated here, not accepted. A `payload_name` of `../../etc/authorized_keys`
   is exactly what a producer sends when it is not the extension.
3. **Write, then rename.** The envelope is assembled in a staging directory and
   moved into place with a single `os.replace`, so a watcher never observes a
   half-written capture and admits it.
4. **`202`, not `201`.** The bytes are durably received; the artifact does not
   exist yet. `201` would name a resource the caller cannot fetch.

## `receiver.py`

```python
"""A minimal corpus-capture receiver: verify, store, receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from corpus_capture import SIDECAR_SCHEMA, normalize_doi, public_registry

INBOX = Path(os.environ.get("CORPUS_CAPTURE_INBOX", "./capture-inbox")).resolve()
TOKEN = os.environ.get("CORPUS_CAPTURE_TOKEN", "")
MAX_HTML_BYTES = 32 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

app = FastAPI(title="corpus-capture reference receiver")
# The extension's Origin is `chrome-extension://…`, which can never satisfy a
# same-origin check; the service token is the whole authentication story here.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"],
                   allow_methods=["GET", "POST", "OPTIONS"])

RECEIPTS: dict[str, dict[str, Any]] = {}


def _authenticate(token: str | None) -> None:
    if not TOKEN or not token or not secrets.compare_digest(token, TOKEN):
        raise HTTPException(status_code=401, detail="bad_service_token")


@app.get("/api/v1/capture/profiles")
def profiles() -> dict[str, Any]:
    """The registry, as data. No repository paths reach the browser."""

    return public_registry()


@app.post("/api/v1/intake/html", status_code=202)
def intake(
    payload: Annotated[dict[str, Any], Body()],
    x_corpus_service_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _authenticate(x_corpus_service_token)

    html = payload.get("html")
    claimed = str(payload.get("sha256") or "").lower()
    if not isinstance(html, str) or not html.strip():
        raise HTTPException(status_code=400, detail="html_missing")
    body = html.encode("utf-8")
    if len(body) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="html_too_large")
    if not SHA256_RE.fullmatch(claimed):
        raise HTTPException(status_code=400, detail="sha256_malformed")

    # The claim is checked, never trusted. This is the whole reason the field
    # exists: a truncated upload is a valid HTTP request with the wrong bytes.
    actual = hashlib.sha256(body).hexdigest()
    if actual != claimed:
        raise HTTPException(status_code=400, detail="sha256_mismatch")

    # Every path segment below is generated here. Nothing the caller sent is
    # ever joined onto a filesystem path.
    handoff_id = uuid.uuid4().hex
    received_at = datetime.now(UTC)
    stem = "capture"
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "url": payload.get("url"),
        "final_url": payload.get("final_url") or payload.get("url"),
        "doi": normalize_doi(payload.get("doi")),
        "title": payload.get("title"),
        "date_published": payload.get("date_published"),
        "captured_at": payload.get("captured_at"),
        "html_sha256": actual,
        "html_bytes": len(body),
        "payload_name": f"{stem}.html",
        "publisher_meta": payload.get("publisher_meta") or {},
        "authors": payload.get("authors") or [],
        "access": payload.get("access") or "unknown",
        "meta_sha256": payload.get("meta_sha256"),
        "profile": payload.get("profile") or "generic",
        "figures": payload.get("figures") or [],
        "capture_tool": payload.get("capture_tool"),
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

    INBOX.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=INBOX, prefix=".staging-"))
    (staging / f"{stem}.html").write_bytes(body)
    (staging / f"{stem}.json").write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    (staging / "handoff.json").write_text(json.dumps(handoff, indent=1), encoding="utf-8")
    # One rename. A watcher either sees a complete envelope or sees nothing.
    os.replace(staging, INBOX / handoff_id)

    receipt_id = str(uuid.uuid4())
    RECEIPTS[receipt_id] = {
        "receipt_id": receipt_id,
        "state": "received",
        "handoff_id": handoff_id,
        "payload_sha256": actual,
        "doi": sidecar["doi"],
        "source_uid": None,
        "bundle_id": None,
        "reader_url": None,
        "detail": None,
    }
    return {
        "receipt_id": receipt_id,
        "state": "received",
        "payload_sha256": actual,
        "doi": sidecar["doi"],
    }


@app.get("/api/v1/intake/{receipt_id}")
def receipt(
    receipt_id: str,
    x_corpus_service_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _authenticate(x_corpus_service_token)
    record = RECEIPTS.get(receipt_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown_receipt")
    # `received` is the honest answer while nothing has processed the bytes.
    # It deliberately is not "pending" or "processing", which would suggest a
    # worker already holds it.
    return record
```

## What a real receiver adds

The example keeps receipts in memory and stops at the envelope. A durable one
replaces both ends and nothing in between:

- receipts in a database, so a restart does not lose the caller's only handle;
- a worker that picks the envelope up, resolves identity from the page's own
  `<meta>` declarations plus a metadata service, and moves the receipt through
  `queued → claimed → admitted | duplicate | error`;
- a `reader_url` on the receipt once the artifact exists, which is what the
  popup shows when it stops polling;
- a watcher on the browser's download directory for the offline path, admitting
  an `.html` **only** when its sidecar is beside it and names it. A filename is
  not evidence: a leftover `.json` otherwise lends its DOI to whatever file next
  takes the same stem.
