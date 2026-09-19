# Changelog

## Unreleased

### Added (extension 1.5.0)

- **A held work folds its metadata away.** When the receiver answers that it
  already holds the article, the panel shows only 「已在庫（儲存將併入）」; the
  DOI and the bibliographic fields sit behind the ▸ on that line and open on
  demand. A refused DOI opens them by itself, because a folded field cannot say
  why it is refused (operator request, 2026-09-19).
- **The reader's note, beside the article.** The room that frees goes to the
  note the reader keeps on that work: the note so far, and a box for more. With
  words in the box the one button reads 「儲存筆記」 and appends them; with the
  box empty it is the ordinary 「儲存」 and captures the page. Only the new text
  is sent -- `POST /api/v1/capture/note` -- and the receiver joins it to the note
  it holds, so a note also edited in a reader is never overwritten by a copy
  the panel happened to show. A draft survives a look at another tab, and a
  retry of the same words is the same request. `GET /api/v1/capture/note?doi=`
  reads the note; a receiver without the routes answers 404 and no note area
  is shown. The keyboard command saves what the button would.

### Fixed (extension 1.4.4)

- **The declared DOI is the first value that is a DOI.** A Science research
  page declares `dc.Identifier` as the publisher's own id (`aec6129`) and its
  DOI under `publication_doi`. The first key that had any value won, its value
  was not a DOI, and the identity fell back to the address («DOI 由 canonical
  網址推得») on a page that declares its DOI outright. Every declared value is
  now tried, in key order, by the preview and the serializer alike. (The first
  wording of this entry, and of commit 9c3c011, said the page declares
  `dc.Identifier` twice; it does not -- measured on the stored capture and the
  live page.)

### Added (extension 1.4.3, figure selection)

- **The visual abstract is a figure.** A figure element the page captions
  inside its abstract -- an ancestor marked `abstract` by id or class, or
  `role="doc-abstract"`, within the article container -- is selected as
  `Graphical abstract` even though nothing on it says "Fig.". Science prints
  its visual abstract as `<figure id="Fa">` in `#structured-abstract` with a
  full caption and no label, so it was filed as decoration while Fig. 1-4 were
  kept. The rule is layout, not a publisher spelling, and it is the same in the
  serializer and in `figure_manifest.build_figure_manifest()`; a test runs both
  on one page. An unlabelled captioned figure in the body is still not guessed
  at, and a caption is required (operator request, 2026-09-19).

### Fixed (extension 1.4.2)

- **A page the browser is translating is not saved.** Chrome's page
  translation rewrites the text in place, a screenful at a time, so a capture
  taken while it was on stored a machine translation of the top of the article
  above the original, as the source (first seen on a Science research article:
  title, abstract, byline and two captions translated, the methods not). The
  serializer now refuses such a page (`page_translated`: the `translated-ltr` /
  `translated-rtl` class on `<html>`, or Chrome's `<font>` wrappers), the panel
  says so as soon as it reads the page, and the refusal tells the reader to
  show the original first. The original text is not in a translated page, so
  there is nothing to repair it from.

### Fixed (extension 1.4.1)

- **A Science news story has a DOI.** An Atypon page that is not a journal
  article declares its DOI as `<meta name="publication_doi">` and carries no
  `citation_*` at all; the panel said 「頁面無 DOI」 and the capture was sent
  without one. The key is read by the preview and the serializer alike.
- **A news page's byline and date.** Where no `<meta>` names an author, a
  publication date or a publisher, the article's JSON-LD (`author`,
  `datePublished`, `publisher`) is read instead -- never for a DOI. A declared
  moment (`2026-09-15T18:45:00.000Z`) is proposed as its day, as the page wrote
  it.
- **A settled capture is listed beside its own page only.** The recent captures
  showed yesterday's admitted article beside every page. A settled capture is
  now listed only where its DOI or address is the page on screen; one still in
  flight, or waiting on the reader, is listed everywhere (operator request,
  2026-09-19).

### Added (extension 1.4.0)

- **The panel on your reading page.** On a tab whose origin is the configured
  receiver, the side panel stops offering a capture and shows the work that
  page displays: title, authors, where it appeared (`Nature ·
  2026-09-02;657(8130):47-58`), DOI and PMID links, type and tags. The page
  declares the work with standard Highwire `citation_*` and Dublin Core
  `<meta>` (one set for the work shown, none when none is); the panel watches
  `<head>` from inside the page and follows each change, and a declared DOI is
  completed from `GET /api/v1/capture/identity` (asked once per DOI). No new
  endpoint. Neither the button nor the keyboard command captures a reading
  page (operator request, 2026-09-18).

### Changed (extension 1.4.0)

- **Only what informs is on screen.** The panel lost its headings, the page
  title that repeated the title field, the per-field source notes, the
  "loaded" status line, the shortcut paragraph and the receipt id. A badge
  appears only when a site is not supported; the DOI's source only when it was
  not the page's own declaration; a status only when a lookup did not answer.
  The DOI's lookup and doi.org link are icon buttons, the save button says
  「儲存」, and the recent captures are one line each: state and title, linked
  to the reader. A test caps the static text on screen at 40 characters.

### Added

- **Side panel (extension 1.3.0).** The toolbar icon opens a side panel that
  stays beside the article. The popup is gone. A browser without the side
  panel API keeps the progress tab.
- **Preview first, then save.** Opening the panel (or switching tabs) reads the
  page's declared DOI and bibliographic `<meta>` and shows them at once, then
  fills in the receiver's record for that DOI and says whether the corpus
  already holds the work. Nothing is sent until the reader presses
  「儲存並建立 bundle」; the keyboard command opens the panel and saves the
  preview as shown. The reader can correct the DOI (and look it up again) and
  any field (title, authors, journal, date, volume, issue, pages, ISSN,
  publisher). The previewed values travel with finalize as `reader_review`,
  validated by `validate_reader_review`; a receiver that applies it keeps the
  page's own declarations as `page_declared`. Attachments upload as before.
- **`GET /api/v1/capture/identity?doi=`.** What the panel previews: the
  receiver's resolved record for a DOI and whether it already holds the work.
  A receiver without it answers 404 and the panel previews the page alone.
- **`GET /api/v1/intake/{receipt_id}/identity`.** The same answer for a held
  capture, with the page's declarations as the receiver recorded them.

### Added (extension 1.3.2)

- **The panel shows its version.** The side panel title and the settings page
  carry the manifest version, because a reload is how every fix reaches an
  unpacked extension and there was no way to see which build was loaded.

### Fixed (extension 1.3.1)

- **A PDF on another site is not an attachment.** A body link to someone
  else's PDF (a cycling position statement citing the WADA Prohibited List) was
  listed as the article's PDF and reported as a missing attachment
  (`off_origin`). PDF links now count only on the page's origin, on a host the
  profile names, or when declared by `citation_pdf_url`; link text that is just
  an address no longer counts as a "PDF" label.
- **Placeholder volume and issue.** An ahead-of-print page's `-1`/`aop`
  (Human Kinetics) or `0`/`0` (Taylor & Francis) is no longer proposed as the
  volume and issue. An issue of `0` beside a real volume is kept.
- **Publication date.** A page's `2026/09/15` is shown as `2026-09-15`, and it
  is kept when the registry knows only the year.
- **Retrying the lookup.** Asking again for the page's own DOI (after "in
  progress") resets what a correction is measured against and keeps what the
  reader typed; before, the retried record itself read as the reader's
  correction.
- **One read per page.** Opening the panel read the page and asked the
  receiver once per tab event (four lookups on the first real capture); a read
  in flight is now awaited instead of repeated.

## v0.2.0 — 2026-09-17

### Added

- **Attachments.** One click now captures the article's own files beside the
  page: the publisher's PDF, the supplementary files, the audio interview and
  the embedded video (extension 1.2.0), into the same bundle, on the same click
  (operator request, 2026-09-17). Discovery runs inside the page
  (`discoverAttachmentsInPage`): `citation_pdf_url`, the publisher's PDF and
  supplement link shapes, `<audio>`/`<video>` sources, JW Player media ids and
  NEJM Quick Take cards, plus whatever a profile names in
  `attachment_link_selectors`. Fetching runs inside the page too, because a
  publisher's media path is hotlink-protected and wants the tab's cookies AND
  its Referer; the bytes cross into the extension as bounded base64 chunks.
  An off-origin video CDN a profile lists in `attachment_origins` is fetched
  anonymously from the extension. Everything else is `off_origin` and recorded
  as a gap.
- **The bytes decide.** `classifyAttachmentBytes` reads the magic bytes: a
  publisher's login page answered with status 200 to a PDF request is a
  `failed: html_instead_of_pdf` row in the manifest, never `source.pdf`.
- **Held captures.** `POST /api/v1/intake/html` accepts `hold_attachments`;
  the receiver stages the page, takes each attachment through
  `POST /api/v1/intake/{receipt_id}/attachments` (raw body, description in
  `x-corpus-attachment-meta`), and publishes on
  `POST /api/v1/intake/{receipt_id}/finalize`. A stale hold is published by the
  receiver on its own with whatever arrived. The drain therefore never sees the
  page without the files the reader was still uploading.
- **Sidecar v3.** `attachments` lists every candidate the page offered,
  `captured` with hash and stored name or `failed` with a reason, plus
  `attachments_discovered` and `attachments_complete`. v1 and v2 remain
  admissible. Offline, each captured attachment is downloaded beside the page
  as `<stem>--NN-<kind><ext>`, the same name a receiver would choose
  (`attachment_payload_name`, asserted identical on both sides).
- **A capture runs in its own tab.** The toolbar popup, and a new keyboard
  command (`Alt+Shift+S`, `Control+Shift+S` on a Mac), open `progress.html`
  beside the article and the capture runs there: Chrome closes a popup on the
  first click elsewhere, and a 70 MB interview upload must not close with it. A
  progress tab closed mid-run finalizes its held capture with `keepalive`.
- **Duplicates.** One file behind two links is fetched and stored once:
  presentation query parameters (`download`, `utm_*`, …) do not make a second
  candidate, and a resolved media id, URL or byte hash already held marks the
  row `duplicate` with `duplicate_of`.
- Library: `validate_attachments`, `validate_attachment_meta`,
  `validate_finalize`, `attachment_payload_name`, `ReceiptStore.items()`;
  registry keys `attachment_link_selectors` and `attachment_origins`; the NEJM
  profile names its audio, supplement and PDF anchors and the JW Platform CDN.

### Not done

- HLS-only video (`.m3u8` with no mp4 rendition) is recorded as
  `hls_not_supported`, not stitched.
- A browser that quits mid-run cannot send its finalize; the receiver's
  one-hour stale-hold sweep publishes the page with what arrived.

## v0.1.4 — 2026-09-11

### Fixed

- An embedded capture could not say which asset a figure came from. Once `src`
  is a data: URI the remote URL is gone, so a manifest built from the STORED
  artifact reported `asset_url=""` — which is the manifest's whole job, and it
  left `unnumbered_asset_patterns` unable to fire anywhere downstream of the
  capture: the corpus re-read the bundle and went straight back to calling the
  Elsevier graphical abstract "Figure 10". An embedder now parks the URL it
  replaces on `data-capture-src` (`ORIGINAL_SRC_ATTR`), the extension included,
  and `_asset_url` reads it when `src` has been embedded. Nothing preserved is
  still reported as no URL, never as the data: URI.

## v0.1.3 — 2026-09-11

### Fixed

- A single unnumbered display item was reported as "Figure 10". Measured on two
  Lancet Comments captured through ScienceDirect: each has exactly ONE image,
  the graphical abstract, in `<figure id="f10">` with the asset `...-fx1.jpg`,
  and the page prints no number anywhere. The element id is an internal counter,
  not a figure number, and a number nobody can see is worse than no number
  because a reader cites it. Profiles may now declare
  `unnumbered_asset_patterns` — Elsevier's `-fx`, against its numbered `-gr` —
  and a match keeps the figure with the bare word ("Figure") instead of the
  counter's digits. This is publisher knowledge and therefore registry data;
  `figure_label(..., numbered=False)` never returns "", because an empty label
  drops the figure from the manifest and the figure is real.

- A figure's description was lost when the page attaches it with
  `aria-describedby` instead of a caption element. ScienceDirect hides it in a
  `u-display-none` div outside every caption selector, so a figure that HAS a
  description was recorded with none. The caption now falls back to the text the
  ARIA reference points at, looked up from the document because the target
  commonly sits outside the referring element. A real caption still wins, and a
  dangling reference is the page's bug, not a capture failure.

## v0.1.2 — 2026-09-03

### Fixed

- The network-policy tests no longer contain a URL literal with a user and a
  password before its host. It was synthetic — the negative-test input for
  `credentials_in_url` — but that is exactly what a credential in a URL looks
  like to a secret scanner, and this repository is vendored into others whose
  pre-push hook blocks on that shape. Measured: it blocked one. The URL is now
  assembled at run time, so the check it exercises is unchanged and the file
  travels.

## v0.1.1 — 2026-09-03

### Security

Every finding in the independent audit of v0.1.0
([`docs/SECURITY-AUDIT-2026-09-03.md`](docs/SECURITY-AUDIT-2026-09-03.md), 11
findings, F-01 to F-11) is fixed, and each one has at least one test that fails
without the fix. The remediation table at the end of that document maps every
finding to the commit that closed it.

**What may be fetched, and with whose cookies (F-01, F-02, F-03).** Asset URLs
come out of page-controlled markup, so the extension was a deputy waiting to be
confused. `extension/net-policy.js` now decides every fetch and returns a
reason. The reader's session goes to the captured page's own origin and nowhere
else; a publisher CDN named by the profile is fetched anonymously. Loopback,
RFC1918, link-local, CGNAT, multicast, cloud metadata, private naming suffixes,
single-label hosts, IPv6 literals and the numeric spellings of 127.0.0.1 are
refused, as is plaintext `http` and anything that answers a redirect. Every
token-bearing request now goes out with `redirect: "error"`, no cookies, and a
final-origin check. A receipt's `reader_url` is only offered as a link when it
is https on the configured receiver's origin.

**What the stored file may contain (F-04, F-05).** The artifact is built as a
DOM and serialized once, so fetched CSS can no longer close the element it is
written into — the previous path substituted bytes into a string and escaped
`</style` in lowercase only. What survives is now an element and attribute
allowlist rather than a list of known-bad names: `<base>`, meta refresh, forms,
`<source srcset>`, `<link>`, SVG and media were all retained before, and none of
them is exotic. `docs/protocol.md` states the opaque-origin viewer requirement
and the exact CSP a receiver must serve a capture under.

**Ceilings (F-06).** Assets per page, figures, metadata keys, values per key,
value length, document size and payload size are bounded, and one asset is read
through a streaming cap that aborts the transfer rather than the allocation. A
document past its ceiling is refused, not truncated.

**Fixtures (F-07, F-08).** The generator applies an inert-markup sanitizer, so a
committed fixture cannot carry an event handler or a live external reference,
and tables are reduced to a captioned placeholder rather than kept whole. The
tests assert the committed bytes, and then run the generator over a page built
to be hostile.

**The reference receiver (F-09, F-10, F-11).** It authenticates before it reads,
bounds what it reads, and validates against an exact schema —
`corpus_capture.validate_submission`, which is library code with its own tests
rather than a fenced block nobody runs. Receipts expire and the inbox has a
ceiling. A non-string `doi` is a 400 rather than a 500.

### Added

- `asset_origins` in the profile registry: the hosts, other than the page's own
  origin, a publisher serves assets from. Registry schema updated.
- `corpus_capture.submission`: `validate_submission`, `enforce_body_size`,
  `ReceiptStore`, `SubmissionError`.
- `extension/net-policy.js`, `extension/sanitize.js`, `extension/limits.js`.
- A test-only `linkedom` dependency so the sanitizer tests run against a real
  DOM. CI sets `CORPUS_CAPTURE_REQUIRE_DOM=1`, so a missing `npm ci` fails the
  run instead of skipping them.

### Changed

- Extension version 1.0.0 to 1.1.0, and `capture_tool` now reports the manifest
  version instead of a literal that had drifted two minor versions from it.
- Fixtures regenerated: NEJM 41.5 KB to 31.2 KB, PMC 30.6 KB to 20.2 KB.

### Known effects of the fixes

- A publisher that serves figures from a CDN with no `asset_origins` entry will
  capture without those figures until the profile names it. The refusal is
  counted and shown rather than silent.
- Figures fetched from a profile CDN are fetched without cookies, so an
  entitled asset served from a third-party host may come back at a lower
  resolution or not at all. Same-origin assets, which is where entitlement
  checks usually live, are unaffected.
- A page served over plaintext `http` captures without its assets.

## v0.1.0 — 2026-09-03

First public release: the extension, the publisher registry and its schema, the
receiver contract and a working reference receiver, the library a receiver
imports, skeleton fixtures, and the public-safety scan.
