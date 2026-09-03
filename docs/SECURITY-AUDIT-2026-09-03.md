# Security audit — `corpus-capture` v0.1.0

**Verdict: FINDINGS PRESENT — do not announce the release until the findings below are triaged.**

## Target and method

- Repository: `copper0722/corpus-capture`
- Annotated tag: `v0.1.0` (tag object `4c59d0146f2f6b8264c08ba18717cb6510b5d3fb`)
- Tagged commit: `478eb778cc7d81bca0cb46a249cc7b561998f255`
- Scope: all eight sections in `docs/SECURITY-AUDIT-SCOPE.md`
- Reviewed inventory: 35 files in the tagged snapshot
- Method: source-backed static review, independent baseline/packet review, local syntax/static checks, and local tests; no browser, receiver deployment, external application, or live service was used
- Verification: `151 passed`; `ruff check .` passed; all five extension modules passed `node --check`; the fenced reference receiver compiled as Python

The tagged snapshot contains an extension, Python library, registry/schema, fixture generator, fixtures, tests, and a documentation-only reference receiver. It does not contain an executable receiver, offline watcher, reader, rights service, or deployment configuration.

## Findings

### F-01 — Medium — page-controlled assets become credentialed cross-origin fetches

- Finding: `serializePage()` accepts any absolute HTTP(S) image or stylesheet URL from page-controlled markup, and the extension fetches each URL with `credentials: "include"` under `<all_urls>` host permission.
- Impact: after the user clicks capture, a malicious page can induce authenticated requests to unrelated, private-network, loopback, or plaintext HTTP destinations. Returned bytes are embedded in the capture and sent to the configured receiver.
- Evidence: `extension/manifest.json:6-7`; `extension/serialize.js:65-73,173-187`; `extension/capture.js:166-182,205-233`; `README.md:141-143`.
- Recommendation: restrict assets to the captured origin and explicit per-profile CDN origins; deny loopback/private/link-local/metadata destinations and unexpected redirects; omit credentials for untrusted origins; reduce `<all_urls>` where possible.

### F-02 — Medium — token-bearing API requests lack redirect destination binding

- Finding: profile, intake, and receipt requests attach `x-corpus-service-token` but use default redirect behavior without `redirect: "error"` or a final-origin check.
- Impact: an open redirect, compromised receiver, or misrouting intermediary may forward the token, and potentially capture data, to another origin. Exact custom-header forwarding is browser-dependent and was not runtime-tested.
- Evidence: `extension/capture.js:40-45,276-284,314-321`; initial-only normalization at `extension/capture.js:84-93`.
- Recommendation: set `redirect: "error"` on every token-bearing fetch. If redirects are required, follow manually only after exact configured-origin and HTTPS validation; omit cookies for token-authenticated API calls unless explicitly required.

### F-03 — Low — receiver-provided `reader_url` is assigned without scheme validation

- Finding: receipt JSON is merged into local state and `row.reader_url` is assigned directly to `link.href`.
- Impact: a malicious or compromised configured receiver can create phishing or unsafe-scheme navigation from extension UI. Extension-page script execution is not established because CSP may block it, but the navigation target is still uncontrolled.
- Evidence: `extension/popup.js:46-64`; receipt JSON path `extension/capture.js:314-324`; protocol field `docs/protocol.md:70-80`.
- Recommendation: parse with `URL`, allow only HTTPS and approved reader origins, reject `data:`, `file:`, `javascript:`, `blob:`, extension schemes, userinfo, and unapproved ports; omit invalid links.

### F-04 — Medium — serialized captures retain active and network-capable markup

- Finding: the serializer removes selected script-bearing elements and `on*` attributes, but retains browser-active constructs such as `base`, meta refresh, forms, SVG references, existing styles, URI-bearing anchors, and `source` `srcset`.
- Impact: a later reader that does not enforce an opaque sandbox and restrictive CSP can execute or navigate attacker-controlled content or make uncontrolled requests. The extension-page CSP does not govern downloaded or receiver-served HTML.
- Evidence: selective removal `extension/serialize.js:79-104`; image-only `srcset` removal and stylesheet replacement `extension/serialize.js:173-202`; persisted `outerHTML` `extension/serialize.js:259-260`; extension-only CSP `extension/manifest.json:17-18`; stored-artifact contract `docs/protocol.md:98-112`.
- Recommendation: use a strict element/attribute allowlist; remove active head/URL constructs and unsafe SVG/CSS; require an opaque-origin viewer with `default-src 'none'`, `script-src 'none'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'none'`, and no uncontrolled network.

### F-05 — Medium — fetched CSS can reintroduce executable markup

- Finding: fetched stylesheet bytes are inserted into already serialized HTML, and only the exact lowercase `</style` sequence is escaped.
- Impact: a stylesheet containing an uppercase or mixed-case closing tag can terminate the style element and inject HTML/script into the stored artifact. A reader without restrictive artifact CSP can execute it.
- Evidence: stylesheet token creation `extension/serialize.js:189-201`; raw decode/substitution and lowercase-only replacement `extension/capture.js:226-230`; artifact persistence/download `extension/serialize.js:259-260`, `extension/capture.js:353-355`.
- Recommendation: construct the final DOM and set `style.textContent` before serialization; otherwise use HTML-aware parsing/escaping and reject markup delimiters. Keep downstream sandbox/CSP independent of the extension-page policy.

### F-06 — Medium — capture and metadata resource limits are not end-to-end

- Finding: the asset list and several metadata fields are unbounded; responses are fully materialized with `arrayBuffer()` before the 24 MiB aggregate check; raw data-URI figure metadata and base64 expansion are outside that budget.
- Impact: a hostile page or asset server can cause excessive sequential requests, extension memory/CPU pressure, oversized metadata, or receiver parsing pressure. The receiver parses the complete JSON body before its HTML-only limit.
- Evidence: unbounded asset collection `extension/serialize.js:65-73`; figure URL/metadata capture `extension/serialize.js:113-139,204-215`; full response buffering and post-read budget `extension/capture.js:166-182,205-235`; receiver body parse/HTML check `docs/receiver-reference.md:87-101`.
- Recommendation: bound DOM size, asset/figure count, per-asset bytes, aggregate encoded output, metadata keys/values, and data-URI length; stream and abort before allocation; enforce request size before JSON parsing.

### F-07 — Medium — fixture generator preserves event handlers and live resource references

- Finding: `make_capture_fixture.py` removes selected elements and rewrites selected `img` fields, but has no attribute/URL allowlist and does not remove `on*` attributes, `source` `srcset`, or wrapper links.
- Impact: generated public fixtures can contain executable event-handler attributes or live external resources. The current committed bytes demonstrate the gap.
- Evidence: generator `tools/make_capture_fixture.py:84-117,119-146`; committed handlers `fixtures/capture-profiles/nejm.html:872,889`; live `srcset` and wrapper links `fixtures/capture-profiles/lww.html:136-146,380-390`; fixtures are distributed by `pyproject.toml:49-52`.
- Recommendation: apply a strict inert-markup sanitizer before writing fixtures; remove all `on*`, dangerous URL schemes, `source/srcset`, forms, active head elements, and live resource links. Add committed-byte negative tests.

### F-08 — Medium — fixture generation can retain complete table payloads

- Finding: the reducer shortens direct text but has no table placeholder/removal policy, so table structure, headers, rows, and cell values survive.
- Impact: if a restricted capture is passed through the generator and committed, substantial article content can enter the public repository even though paragraph word-count checks pass. The current PMC profile is marked open access; its bytes demonstrate the code path, not a current rights violation.
- Evidence: reducer `tools/make_capture_fixture.py:52-82`; retained Table 1 `fixtures/capture-profiles/pmc.html:387-463`; retained Table 2 `fixtures/capture-profiles/pmc.html:484-671`; test coverage `tests/test_public_safety.py:149-170`.
- Recommendation: replace tables with structural placeholders or remove them unless an explicit rights policy permits them; add tests rejecting non-placeholder cells, full references, and supplementary content.

### F-09 — Medium — reference receiver parses unbounded JSON before authentication

- Finding: the documentation receiver declares `Body()` as a full `dict` and calls `_authenticate()` only inside the handler; its only size check happens later and covers only `html`.
- Impact: any caller that can reach a deployed receiver can consume memory/CPU with a large non-HTML JSON body before authentication. The documented loopback bind reduces default network exposure but does not fix the parser order.
- Evidence: receiver configuration and token `docs/receiver-reference.md:61-70`; body declaration, late authentication, and late HTML-only check `docs/receiver-reference.md:87-101`.
- Recommendation: enforce request-size limits in ASGI/reverse-proxy/server middleware before body parsing, use bounded streaming JSON, and authenticate before allocating large bodies.

### F-10 — Low — authenticated sidecar, inbox, and receipt state have no cumulative bounds

- Finding: the reference receiver copies `title`, URLs, `publisher_meta`, `authors`, `figures`, and other fields without field/collection/total limits, creates an inbox directory per request, and retains every receipt in an unbounded process-global dictionary.
- Impact: a bearer-token caller can exhaust disk or process memory through oversized metadata or repeated valid captures. The token prerequisite lowers likelihood but no quota is implemented.
- Evidence: global `RECEIPTS` `docs/receiver-reference.md:72,153-164`; direct sidecar copies `docs/receiver-reference.md:115-132`; filesystem writes `docs/receiver-reference.md:145-151`.
- Recommendation: use an exact bounded schema, total metadata/request quotas, rate and concurrency limits, inbox disk quotas/cleanup, and receipt TTL/retention.

### F-11 — Low — malformed non-string DOI reaches an unhandled exception

- Finding: the reference receiver passes arbitrary JSON `doi` values to `normalize_doi()`, which calls `.strip()` on any truthy value without first requiring a string.
- Impact: an authenticated malformed request such as an object-valued DOI can produce a 500 instead of a deterministic 4xx and consume worker/logging resources.
- Evidence: receiver call `docs/receiver-reference.md:87-92,115-120`; untyped normalizer `python/corpus_capture/sidecar.py:86-110`.
- Recommendation: validate an exact schema (`doi: str | null`) before normalization and return 400 for wrong types; guard normalization and serialization failures.

## Eight-section disposition

1. **Manifest and permissions — reported.** All declared permissions have call sites: `activeTab`/`scripting` in the user-triggered popup flow, `storage` for settings/cache/receipts, `downloads` for fallback, and `alarms` for polling. `cookies`, `webRequest`, `history`, `tabs`, and `content_scripts` are absent. The `<all_urls>` use is real but forms the F-01 confused-deputy boundary.
2. **Extension-page XSS and CSP — reported.** Visible fields and options errors use `textContent`; no product `eval`, `new Function`, or HTML-writing `innerHTML` sink was found. F-03 remains an unsafe navigation sink.
3. **Serialization and third-party content — reported.** F-04, F-05, and F-06 cover active markup, CSS substitution, resource limits, data URIs, and downstream rendering assumptions.
4. **Sidecar and envelope injection — reported.** F-10 and F-11 cover receiver-side field/resource validation. The handoff itself uses generated identifiers and static filenames; producer identity, rights, and path text do not reach filesystem joins.
5. **Token storage and transport — reported.** The token is in `chrome.storage.local`, never sync storage or a URL, and initial remote endpoints require HTTPS. F-02 covers missing redirect destination binding.
6. **Residual identity from the private tree — no issue found.** The listed deployment markers and identity patterns had no hits outside the test's own declarations and the fixture-exemption cases; the existing public-safety suite passed.
7. **Fixtures and restricted full text — reported.** Current `img` values are 1×1 placeholders, reference sections contain numbering rather than full citation entries, and no supplementary payload file is present. F-07 and F-08 show that the generator/output boundary is not inert or rights-safe for future inputs.
8. **Receiver contract — reported.** Authentication uses constant-time comparison, receipt lookup authenticates before existence lookup, HTML bytes are rehashed and bounded, caller text is not joined to paths, and final publication uses a generated handoff ID plus rename. F-09–F-11 remain receiver resource/input-validation findings.

## Unresolved follow-up boundaries

- The tag has no offline watcher or reader. A watcher must recompute payload hash/size, handle partial sequential downloads and browser collision suffixes, validate sidecar payload names, and never render untrusted HTML in a privileged origin.
- The receiver creates `.staging-*` inside `INBOX` before the final rename (`docs/receiver-reference.md:145-151`). If a downstream worker scans every child directory, it can observe partial files; no worker is shipped, so this remains a conditional follow-up rather than a confirmed deployed finding.
- `python/corpus_capture/sidecar.py:148-176` validates hash syntax and payload-name association but does not recompute payload bytes; the protocol requires a receiver/watcher to perform that independent check.
- The exact browser behavior for custom-header forwarding across cross-origin redirects was not runtime-tested; the source still lacks a fail-closed redirect policy.

## Reproduction and remediation note

No exploit code was added and no program/runtime mutation was performed. The line references above are sufficient to reproduce each source path from the pinned tag. The recommended fixes should retain explicit user action, exact-byte verification, producer/receiver identity separation, and the existing no-`cookies`/no-background-collection boundaries.

## Remediation — v0.1.1, 2026-09-03

Every finding is closed in `v0.1.1`. Each row names the commit that closed it
and the test that fails without it; the tests are the durable half, because a
fix with no test is a fix until somebody refactors it.

| finding | severity | commit | fixed by | test |
|---|---|---|---|---|
| F-01 | Medium | `29f4c20` | `extension/net-policy.js` decides every asset fetch: page origin with cookies, per-profile `asset_origins` CDN without, everything else refused; loopback/private/link-local/metadata/numeric-host/IPv6/single-label and plaintext http denied | `tests/test_net_policy.py::TestWhatMayBeFetched` |
| F-02 | Medium | `29f4c20` | one `apiFetch` owns every token-bearing request, with `redirect: "error"`, `credentials: "omit"` and a final-origin check; asset fetches refuse redirects too | `tests/test_net_policy.py::test_every_token_bearing_request_refuses_a_redirect` |
| F-03 | Low | `29f4c20` | `safeReaderUrl()`; a link is offered only when it is https on the configured receiver's origin, and a refused one is reported | `tests/test_net_policy.py::TestReaderLink` |
| F-04 | Medium | `99edad2` | element and attribute allowlist in `extension/sanitize.js`, applied in the extension over the parsed DOM; opaque-viewer CSP documented in `docs/protocol.md` | `tests/test_sanitize.py::TestActiveMarkupIsRemoved` |
| F-05 | Medium | `99edad2` | the artifact is a DOM, serialized once; `style.textContent` replaces string substitution, and `</style` is neutralized in every case because serialization does not escape it | `tests/test_sanitize.py::TestCssCannotReintroduceMarkup` |
| F-06 | Medium | `f568786` | `extension/limits.js`; `readBounded()` checks the declared length, then streams with a cap and aborts the transfer; assets, figures, metadata and document size bounded; oversized document refused rather than truncated | `tests/test_limits.py` |
| F-07 | Medium | `52b0b0d` | `_make_inert()` in the fixture generator: element drop list plus attribute allowlist over the whole document | `tests/test_fixtures_inert.py::TestNothingInAFixtureRuns`, `::test_the_generator_makes_a_hostile_page_inert` |
| F-08 | Medium | `52b0b0d` | `_placeholder_tables()`: a table keeps its caption and loses its cells | `tests/test_fixtures_inert.py::TestNoTablePayloadSurvives` |
| F-09 | Medium | `faf62f7` | the receiver authenticates from headers, calls `enforce_body_size()`, then streams the body with a cap, then parses | `tests/test_submission.py::TestSizeIsRefusedBeforeTheBodyIsRead`, `tests/test_docs.py::test_the_receiver_authenticates_before_it_reads_the_body` |
| F-10 | Low | `faf62f7` | `validate_submission()` bounds every field, collection and the metadata total; `ReceiptStore` gives receipts a TTL and a ceiling; the inbox refuses past `MAX_ENVELOPES` | `tests/test_submission.py::TestReceiptsExpire`, `::test_forty_bounded_fields_still_add_up` |
| F-11 | Low | `faf62f7` | `doi` is type-checked before normalization; a non-string is a 400 with the code `doi_not_a_string` | `tests/test_submission.py::test_a_non_string_doi_is_a_refusal_and_not_a_500` |

### On the follow-up boundaries

- **No watcher or reader ships here, and none is added.** The requirements the
  audit lists for one -- recompute hash and size, handle partial downloads and
  browser collision suffixes, validate the sidecar's payload name, never render
  untrusted HTML in a privileged origin -- are now written into
  `docs/protocol.md` and the receiver reference's closing section, so whoever
  builds one has them in front of them.
- **The `.staging-*` directory inside `INBOX` is unchanged**, and remains a
  conditional follow-up: it is dot-prefixed, and the contract for a watcher is
  to skip dotted names. That is a rule a watcher must follow, not a guarantee
  the receiver can make on its behalf.
- **`validate_sidecar()` still does not recompute payload bytes.** It validates
  the document, and the protocol continues to require that whoever holds the
  bytes hashes them. The reference receiver does exactly that for the online
  path.
- **Cross-origin custom-header forwarding was still not runtime-tested.** It no
  longer needs to be: `redirect: "error"` fails closed before any redirect is
  followed, whatever a given browser would have done with the header.

### What the fixes cost

Three behaviours changed for legitimate captures, all of them deliberate and
all of them documented in `CHANGELOG.md`: a publisher CDN that no profile names
is not fetched, a profile CDN is fetched without the reader's cookies, and a
page served over plaintext http captures without its assets.
