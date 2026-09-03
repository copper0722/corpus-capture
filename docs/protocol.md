# Receiver protocol

What this extension sends, and what a receiver has to implement. Nothing here
depends on a particular corpus implementation; it is the contract, not the
server.

## The boundary

A producer may hand over **bytes** and **observations**. It may not hand over an
identity. It does not decide which work this is, where the artifact is filed,
what rights apply, or whether anything may be published. Those belong to the
receiver, which has the registration records and the rights evidence a browser
does not.

That is not a style preference. It is why the handoff manifest below physically
cannot carry a DOI: an envelope that asserts corpus identity is refused, so the
page's own declarations travel in a **sidecar** beside the payload instead, as
producer evidence.

## `POST /api/v1/intake/html`

Authenticates with either a session the receiver already trusts, or a service
token in `x-corpus-service-token`. A browser extension always uses the token: its
`Origin` is `chrome-extension://…` and can never satisfy a same-origin CSRF
check.

```json
{
  "url": "https://example.org/article/1",
  "final_url": "https://example.org/article/1",
  "html": "<!doctype html>…",
  "sha256": "<sha-256 of the html, as UTF-8 bytes>",
  "captured_at": "2026-09-03T11:20:00+08:00",
  "doi": "10.1000/example",
  "title": "The article's title",
  "date_published": "2026-08-27",
  "publisher_meta": { "journal": "…", "volume": "…", "issue": "…" },
  "authors": ["Family Given", "…"],
  "access": "open | login_required | metered | unknown",
  "meta_sha256": "<sha-256 of the normalized meta block>",
  "profile": "nejm | generic | …",
  "figures": [
    {
      "figure_id": "f1",
      "label": "Figure 1",
      "caption": "…",
      "alt": "Figure1",
      "asset_url": "https://example.org/asset/f1.jpg",
      "position": 1
    }
  ],
  "capture_tool": "chrome-capture/1.2.0"
}
```

`sha256` is a claim about `html` and the receiver must verify it against the
bytes. A mismatch is the caller's bug, not a retryable transport fault: answer
4xx, and the client must not retry it down the offline path, because downloading
a payload the server refused only moves the refusal further along.

Response `202`:

```json
{ "receipt_id": "<uuid>", "state": "received", "payload_sha256": "…", "doi": "…" }
```

`202`, not `201`: the bytes are durably received and the artifact does not exist
yet. Reporting `201` would name a resource the caller cannot fetch.

## `GET /api/v1/intake/{receipt_id}`

```json
{
  "receipt_id": "<uuid>",
  "state": "received | queued | claimed | admitted | duplicate | error | …",
  "source_uid": "…",
  "bundle_id": "…",
  "reader_url": "https://…",
  "detail": null
}
```

`received` means the receiver holds the bytes and has not processed them yet —
the honest answer while an ingestion schedule has not fired. It deliberately is
not "pending" or "processing", which would suggest a worker already holds it.

## `GET /api/v1/capture/profiles`

Returns the publisher selector registry as pure data: host patterns, article
container selectors, figure and caption selectors, metadata sources, access
markers, and a `status` per profile. The extension caches it and falls back to
the cached copy, then to generic selectors.

`status` is a measurement: `supported` requires a committed fixture that a test
asserts against, `generic` means only fallback selectors are proven, and
`unsupported` carries the measured reason.

## Envelope, as the receiver stores it

The reference receiver publishes each capture as one directory:

```
<inbox>/corpus-capture/<handoff_id>/
    handoff.json      transport manifest: hashes, sizes, times, producer key
    <slug>.html       the payload
    <slug>.json       the sidecar: what the page declared
```

`handoff.json` refuses `source_uid`, `doi`, `bundle_path`, `tags` and `rights` —
identity and rights are not the producer's to assert. It is written to a staging
directory and moved into place with a single rename, so a reader never observes
a half-written envelope.

## The payload is inert, and the viewer must keep it that way

The producer applies an element and attribute **allowlist** before the artifact
exists: an element nobody listed is unwrapped and its text kept, and the handful
that carry their own payload are removed outright — `script`, `iframe`,
`object`, `embed`, `svg`, `math`, `canvas`, media and `track`, `form` and every
form control, `base`, `link`, `source`, and any `meta` carrying `http-equiv`.
Attributes survive only from a list: identity, presentation, table geometry,
`aria-*`, `data-*`, and the two URL attributes, whose values must be `https`,
`http`, `mailto`, `data:` or a fragment. CSS — fetched, inline `<style>`, or a
`style` attribute — goes through the same filter: no `@import`, no
`expression()`, no `behavior`, no `javascript:` URL, and no sequence that could
end the element it is written into.

**A receiver must still serve it as untrusted content.** The producer's
allowlist bounds what is in the file; it cannot bound what a viewer does with
it. Serve or render a stored capture from an opaque origin — a sandboxed iframe
without `allow-same-origin`, or a distinct origin that shares nothing — under:

```
Content-Security-Policy: default-src 'none'; img-src data:; style-src 'unsafe-inline';
    base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox
```

`img-src data:` and `style-src 'unsafe-inline'` are what make an embedded figure
and its stylesheet render at all; everything else is off, so the artifact cannot
reach the network, navigate, or borrow the reader's session. The extension's own
`content_security_policy` governs extension pages only and has no authority over
a file a receiver serves.

## Sidecar — `corpus-capture-sidecar-v2`

The one document both the online and the offline path emit, so a receiver has a
single rule to implement:

| field | meaning |
|---|---|
| `schema` | `corpus-capture-sidecar-v2` (v1 omits everything below `access`) |
| `url` / `final_url` | canonical and actual page address |
| `doi` | what the page declared, normalized; never derived from a filename |
| `title`, `date_published` | as declared |
| `html_sha256`, `html_bytes` | the payload these describe |
| `payload_name` | the payload this sidecar belongs to |
| `capture_tool` | producer and version |
| `publisher_meta` | bounded set of the page's own bibliographic declarations |
| `authors` | names in page order |
| `access` | what the reader's session saw |
| `meta_sha256` | hash of the normalized meta block |
| `profile`, `figures` | which profile ran, and the article's figure manifest |

A sidecar that does not name its payload is ignored. Without that check, a
leftover `.json` lends its DOI to whatever file later takes the same stem.

`access` is an observation about what the page showed, never a licence. Only
`login_required` is evidence — that the article is not open. A page that merely
rendered proves nothing: the browser may have been carrying an entitled session,
which is the entire reason this lane exists.

## Offline path

When the endpoint is unreachable the extension writes both files to the
browser's download directory under `corpus-capture/`, with the same stem. A
receiver that watches that directory admits the `.html` only when the sidecar is
beside it: a name is not evidence.
