# Changelog

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
