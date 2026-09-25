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

The reader may review that evidence before the capture is published: the side
panel shows the detected DOI and the bibliographic record, and the reader
confirms or corrects them. The result travels as one more observation,
`reader_review`, with the finalize call. A receiver decides whether to file the
capture under it; the page's own declarations are never discarded.

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

### Holding a capture for its attachments

`hold_attachments: true` in the body asks the receiver NOT to publish the
envelope yet. The receiver stages it, answers `202` with `state: "held"`, and
waits for the attachment uploads and the finalize call below. A held envelope
is invisible to whatever ingests the inbox, so a bundle is never built from
the page alone while the reader's browser is still uploading its PDF.

A producer that lets the reader review the capture's identity holds every
capture, attachments or not, and finalizes only after the review.

A hold is not forever. A receiver publishes a held envelope on its own after a
bounded time (the reference receiver: one hour) with whatever attachments
arrived and `attachments_complete: false`. The extension runs a capture in its
side panel (or, without the side panel API, in its own tab); a panel or tab
closed while attachments are still uploading sends the finalize below with
`keepalive` and the rows it has, and a capture waiting only for the reader's
review stays held so the panel can resume it. The timeout is the backstop for a
browser that went away entirely.

## `POST /api/v1/intake/{receipt_id}/attachments`

One attachment's bytes, raw, for a held capture. The body IS the file; the
description travels in one header so a 200 MB video is not wrapped in a JSON
string with a base64 copy of itself:

```
Content-Type: application/octet-stream
x-corpus-attachment-meta: <percent-encoded JSON>
```

```json
{
  "index": 3,
  "kind": "audio | pdf | supplement | video | other",
  "url": "https://example.org/cms/asset/…/interview.mp3",
  "final_url": "https://example.org/…",
  "label": "Download audio",
  "source": "anchor | citation_pdf_url | audio_element | jwplayer | nejm_do | profile:<selector>",
  "sha256": "<sha-256 of the body>",
  "bytes": 61234567,
  "mime": "audio/mpeg",
  "ext": ".mp3",
  "media_id": "AbCd1234",
  "nejmdo": "10.1056/NEJMdo000123"
}
```

`sha256` and `bytes` are claims about the body. The receiver caps what it
reads, hashes what it read, and refuses a mismatch with `4xx`. It chooses the
stored name itself — `<capture stem>--NN-<kind><ext>` — and answers:

```json
{ "receipt_id": "<uuid>", "state": "held",
  "attachment": { "index": 3, "kind": "audio", "payload_name": "…--03-audio.mp3",
                  "sha256": "…", "bytes": 61234567, "mime": "audio/mpeg" } }
```

The producer fetched the bytes with the reader's own session, from inside the
page, because a publisher's PDF and media links want the tab's cookies AND its
Referer. It fetched them only from the page's origin, or from an origin the
publisher profile names for anonymous video (`attachment_origins`); nothing a
page can link reaches a private host. A PDF linked on another site is not a
candidate at all: it is a document the article cites (a position statement
linking an anti-doping list), not the article's copy, unless the page declares it
as `citation_pdf_url` or the profile names its host. And it looked at the bytes
before sending them: a login page answered with status 200 to a PDF request is
recorded as a gap (`html_instead_of_pdf`), never uploaded as the PDF.

## `POST /api/v1/intake/{receipt_id}/finalize`

Closes a held capture and publishes the envelope.

```json
{
  "attachments": [
    { "index": 1, "kind": "pdf", "url": "…", "label": "PDF", "source": "citation_pdf_url",
      "status": "captured", "payload_name": "…--01-pdf.pdf", "sha256": "…",
      "bytes": 812345, "mime": "application/pdf" },
    { "index": 2, "kind": "video", "url": "…/master.m3u8", "label": "Watch",
      "source": "anchor", "status": "failed", "reason": "hls_not_supported" }
  ],
  "complete": false,
  "discovered": 2,
  "overflow": 0,
  "reader_review": {
    "decision": "corrected",
    "doi": "10.1056/nejmp2607831",
    "detected_doi": "10.1056/nejmp2607830",
    "metadata": { "title": "…", "authors": ["…"], "journal": "…", "published": "2026-09-10",
                  "volume": "395", "issue": "11", "pages": "1001-1003", "issn": "…",
                  "publisher": "…" },
    "changed": ["doi"],
    "reviewed_at": "2026-09-17T07:00:00+00:00"
  }
}
```

`reader_review` is optional. `decision` is `confirmed` (nothing changed, so
`changed` is empty) or `corrected`. `doi` is what the reader left in the field,
normalized, or `null` when the reader says the work has no DOI; `detected_doi`
is what the capture found on the page. `metadata` carries exactly the nine keys
shown, bounded to 1000 characters each (authors: at most 100 names of 300).
A receiver that applies the review keeps the page's declarations under
`page_declared` in the sidecar and records the review itself as
`reader_review`. The corpus receiver files the capture under the reviewed DOI,
and a review that clears the DOI stops the receiver from deriving one from the
page.

The manifest names every candidate the page offered: `captured` with the hash
and stored name, `duplicate` with `duplicate_of` (the index of the captured row
holding the same bytes -- the PDF behind `citation_pdf_url` and behind a
`?download=true` link, one video behind two players), or `failed` with a reason
(`http_403`, `too_large`, `html_instead_of_pdf`, `off_origin`,
`hls_not_supported`, `not_a_video`, …). A duplicate is complete, never a gap,
and its bytes are fetched and stored once.
The receiver trusts only its own ledger for what was captured — a row the
producer calls `captured` that the receiver never received is recorded as
`failed: not_uploaded` — and writes the merged manifest into the sidecar as
`attachments`, with `attachments_complete` and `attachments_discovered`. Answers
`202` with the ordinary receipt, `state: "received"`.

A bundle built from the envelope therefore records which files it holds and
which it does not, instead of looking complete because nothing says otherwise.

## `GET /api/v1/intake/{receipt_id}`

```json
{
  "receipt_id": "<uuid>",
  "state": "held | received | queued | claimed | admitted | duplicate | error | …",
  "source_uid": "…",
  "bundle_id": "…",
  "reader_url": "https://…",
  "detail": null
}
```

`received` means the receiver holds the bytes and has not processed them yet —
the honest answer while an ingestion schedule has not fired. It deliberately is
not "pending" or "processing", which would suggest a worker already holds it.

## `GET /api/v1/capture/identity?doi=`

What the side panel previews before anything is captured: the record the
receiver resolved for a DOI and whether it already holds the work. The panel
reads the page's own declarations itself (only `<meta>`, the article's JSON-LD
and the address), shows them at once, and merges this answer in when it arrives.

```json
{
  "doi": "10.1056/nejmp2607831",
  "metadata": { "title": "…", "authors": ["…"], "journal": "…", "published": "2026-09-10",
                "volume": "395", "issue": "11", "pages": "1001-1003", "issn": "…",
                "publisher": "…" },
  "metadata_source": "registry",
  "metadata_status": "resolved | unresolved | in_progress | unavailable | error",
  "known": { "title": "…", "reader_url": "https://…" }
}
```

A receiver without it answers 404, and the preview shows the page alone. When
the reader saves, the previewed (and corrected) values travel as
`reader_review` with finalize.

## `GET /api/v1/capture/note?doi=` and `POST /api/v1/capture/note` (optional)

The reader's own note on a work the receiver already holds. Beside such a work
the side panel folds the bibliographic fields away behind the line that says
the work is held, shows the note, and takes more of it.

```json
GET  -> { "doi": "10.1126/science.aec6129", "held": true, "exists": true,
          "body_md": "…", "revision": 4, "updated_at": "2026-09-19T03:00:00Z" }
POST <- { "doi": "10.1126/science.aec6129", "append_md": "what the reader typed",
          "request_id": "a uuid the panel keeps with those words" }
POST -> the same shape as GET, plus "changed" and "replayed"
```

The panel sends only the new text. The receiver joins it to the note it holds
(one blank line between), because the reader may edit the same note elsewhere
and a body concatenated in the panel would overwrite that. A repeated
`request_id` adds nothing and answers `"replayed": true`. `held: false` means
there is no work for a note to belong to; an append for such a DOI is `409
work_not_held`, a body past the receiver's limit `422 note_too_long`. A
receiver without these routes answers 404 and the panel shows no note area. A
note is the reader's text: it is sent to the configured receiver and nowhere
else, and it is not part of the capture, the sidecar or the envelope.

## `GET /api/v1/intake/{receipt_id}/identity`

What the reader reviews before finalize. `?doi=` looks up a DOI the reader
typed instead of the detected one.

```json
{
  "receipt_id": "<uuid>",
  "state": "held",
  "reviewable": true,
  "detected_doi": "10.1056/nejmp2607830",
  "doi": "10.1056/nejmp2607831",
  "page": { "title": "…", "authors": ["…"] },
  "metadata": { "title": "…", "authors": ["…"], "journal": "…", "published": "2026-09-10",
                "volume": "395", "issue": "11", "pages": "1001-1003", "issn": "…",
                "publisher": "…" },
  "metadata_source": "registry",
  "metadata_status": "resolved | unresolved | in_progress | no_doi | unavailable",
  "known": { "title": "…", "reader_url": "https://…" }
}
```

`metadata` is the record the receiver resolved for `doi` (or `null`), in the
same nine keys the review sends back. `known` is set when the receiver already
holds a work with that DOI. `reviewable: false` means the capture is no longer
held, so a review would not be applied. A receiver without this endpoint
answers 404, and the extension then reviews the page's declarations alone.

## Reading pages (optional)

A receiver that also serves a reading UI on its own origin can let the side
panel describe the work on screen. On a tab whose origin is the configured
receiver the panel offers no capture; it reads the page's declarations and
shows them read-only.

- The page declares the work it shows with standard `<meta name>` tags:
  `citation_title`, `citation_author` (one per author), `citation_journal_title`
  (a chapter: `citation_inbook_title`), `citation_publication_date`,
  `citation_volume`, `citation_issue`, `citation_firstpage`, `citation_lastpage`,
  `citation_doi`, `citation_pmid`, `citation_issn`, `citation_publisher`,
  `citation_keywords` (one per tag) and `dc.type`. One set for the work shown,
  none when no work is shown. A single-page app rewrites them when the work on
  screen changes; the panel watches `<head>` and follows. Where a `citation_`
  name is absent the panel also reads `dc.title`, `dc.creator`, `dc.date`,
  `citation_date`, `citation_conference_title`, `dc.publisher` and `dc.subject`.
- For a declared DOI the panel asks `GET /api/v1/capture/identity?doi=`, and the
  receiver's record fills in what the page leaves out (authors, the journal's
  full name, pages). Nothing else is sent, and nothing is written.

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
    handoff.json                transport manifest: hashes, sizes, times, producer key
    <slug>.html                 the payload
    <slug>.json                 the sidecar: what the page declared, and the attachment manifest
    <slug>--01-pdf.pdf          the attachments the sidecar lists, one file each
    <slug>--02-supplement.pdf
    <slug>--03-audio.mp3
```

An attachment is admissible only through the sidecar that lists it with its
hash: a `.pdf` beside a capture that no sidecar names is a stray download,
and a receiver's ingest must treat it as one rather than as this article's.

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

## Sidecar — `corpus-capture-sidecar-v3`

The one document both the online and the offline path emit, so a receiver has a
single rule to implement:

| field | meaning |
|---|---|
| `schema` | `corpus-capture-sidecar-v3` (v2 says nothing about attachments; v1 omits everything below `access`) |
| `url` / `final_url` | canonical and actual page address |
| `doi` | what the page declared, normalized; never derived from a filename |
| `title`, `date_published` | as declared; the date from the article's JSON-LD (`datePublished`) when no `<meta>` gives one |
| `html_sha256`, `html_bytes` | the payload these describe |
| `payload_name` | the payload this sidecar belongs to |
| `capture_tool` | producer and version |
| `publisher_meta` | bounded set of the page's own bibliographic declarations, as printed; a receiver treats a placeholder volume or issue (`-1`, `aop`, `Online First`, a volume of `0`) as absent |
| `authors` | names in page order; from the article's JSON-LD (`author`) when no `<meta>` names any |
| `access` | what the reader's session saw |
| `meta_sha256` | hash of the normalized meta block |
| `profile`, `figures` | which profile ran, and the article's figure manifest |
| `attachments` | the attachment manifest: every linked file, `captured` (with `payload_name`, `sha256`, `bytes`, `mime`), `duplicate` (with `duplicate_of`) or `failed` (with `reason`) |
| `attachments_discovered`, `attachments_complete` | how many the page linked, and whether every one was captured |
| `reader_review` | optional: the reader's confirmation or correction of the identity, as sent with finalize |
| `page_declared` | optional: the page's own `doi`, `title`, `date_published` and `authors`, kept when a receiver filed the capture under `reader_review` |

A sidecar that does not name its payload is ignored. Without that check, a
leftover `.json` lends its DOI to whatever file later takes the same stem.

`access` is an observation about what the page showed, never a licence. Only
`login_required` is evidence — that the article is not open. A page that merely
rendered proves nothing: the browser may have been carrying an entitled session,
which is the entire reason this lane exists.

## Offline path

When the endpoint is unreachable the extension writes both files to the
browser's download directory under `corpus-capture/`, with the same stem, and
each captured attachment beside them as `<stem>--NN-<kind><ext>` — the same
name a receiver would have chosen, because here there is no receiver to choose
it. A receiver that watches that directory admits the `.html` only when the
sidecar is beside it, and an attachment only when that sidecar lists it with
its hash: a name is not evidence.


### Optional held-work desktop actions (extension 1.6.0)

A receiver may offer authenticated `GET /api/v1/capture/files?doi=...`, returning
`{files: [{file_ref, role, kind, bytes, label}]}`, and
`POST /api/v1/capture/desktop?doi=...&file_ref=...`, returning
`{ok, name, bytes, sha256, desktop_path}` after verified delivery. The receiver
resolves both DOI and opaque file reference and owns the desktop destination.
The client never supplies a filesystem path or another origin. Unsupported
receivers show an explicit error; the client does not silently recapture a
Reader page or pretend a browser download reached the desktop.
