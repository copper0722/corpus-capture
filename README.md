# corpus-capture

One click on the article you are reading, and the page becomes a single
self-contained HTML file — figures embedded, adverts left behind — posted to a
receiver you run yourself.

The session you are already logged into is what gets you the full text and the
high-resolution figures. The extension does not log in, does not store
credentials, and does not collect anything in the background. It packages the
page you are looking at, the way SingleFile does, and adds two things a reading
archive actually needs: **publisher profiles**, so figures are selected by
document structure rather than by guesswork, and an **open intake protocol**, so
the receiving end is yours to implement.

> The extension's interface is Traditional Chinese. Everything else here —
> protocol, registry, library, tests — is English.

## What is in this repository

| path | what it is |
|---|---|
| `extension/` | the MV3 browser extension: the producer |
| `profiles/capture_profiles.json` | the publisher registry, and `schema.json` that constrains it |
| `python/corpus_capture/` | the library a receiver uses: the same registry, the same figure selection, the sidecar contract |
| `docs/protocol.md` | the wire contract: endpoints, envelope, sidecar, offline path |
| `docs/receiver-reference.md` | a minimal receiver you can run, in about a hundred lines |
| `fixtures/` | skeletons of real publisher pages: structure kept, article text reduced to first sentences |
| `tests/` | what pins all of the above |

## Install the extension

1. Open `chrome://extensions` and turn on developer mode.
2. **Load unpacked**, and choose the `extension/` directory.
3. Click the icon, then "設定 API 位址與 token", and fill in your receiver's
   address and service token.

**There is no built-in endpoint.** A receiver is something you run, usually on a
private network. Shipping one address would be useless to everyone else and
would disclose where one person's archive lives. The address must be `https`
(`http` is accepted only for `localhost`).

## What happens when you press the button

The badge at the top of the popup tells you what this site is worth:

| badge | meaning |
|---|---|
| supported | a publisher profile exists, and a fixture test defends it |
| generic | no profile; the fallback selectors run, and the sidecar records `profile=generic` |
| unsupported | a measured blocker, named in the badge |

Then the status line moves through embedding progress, the figure manifest (how
many figures, what they are called, how many images were classified as
decoration), the receipt id, and finally a link to read what was admitted. You
can close the popup; the service worker keeps polling.

## Figures are chosen by structure, not by size

This was measured, not assumed. Picking embedded images by byte size, on an
NEJM Case Challenge page, returned five promotional images — cover art, a
banner, logos — and none of the article's three figures. A big image is just a
big image. Only its position in the document says whose it is.

Selection is therefore structural, in three ordered rungs:

1. the publisher's own figure elements, named by the profile (`figure.graphic`);
2. any `<figure>` inside the article container;
3. an `<img>` whose `alt` names itself a figure.

Rung 3 has one detail that is easy to get wrong. NEJM writes `alt="Figure1"`
with no space, so `^(Figure|Table)\b` does not match: `e` and `1` are both word
characters, so there is no boundary between them. That single case is the
difference between three figures and zero.

Only the **article container** plus `<head>` is serialized. The rest of the page
— rails, modals, marketing, third-party frames — is not the article and does not
belong in a file that claims to be one. Images outside the manifest are still
embedded, so the page stays whole, but they are flagged `decorative` so nothing
downstream mistakes a masthead for a result.

## What it will not fetch

The extension is a deputy: it holds `<all_urls>` and it fetches with the
reader's own session, and every asset URL it is handed came out of markup the
page controls. So the fetch is a decision, not a loop:

| destination | fetched? | cookies? |
|---|---|---|
| the captured page's own origin | yes | yes — this is what gets the entitled figure |
| a CDN named by the publisher's profile (`asset_origins`) | yes | no |
| anything else | no | — |
| loopback, RFC1918, link-local, cloud metadata, `.local`, `.internal`, a bare hostname | no | — |
| plaintext `http`, even same-origin | no | — |
| anything that answers a redirect | no | — |

A refused asset is counted and shown in the popup rather than dropped quietly.
If a publisher serves figures from a CDN this does not know about, the fix is a
line in `profiles/capture_profiles.json`, not a wider permission.

Requests that carry your service token — the registry, the intake, the receipt —
go out with `redirect: "error"`, no cookies, and a check that the answer came
from the origin you configured.

## The stored file is inert

Before the artifact exists it goes through an element and attribute
**allowlist**. An element nobody listed is unwrapped and its text kept; the ones
that carry their own payload are removed with their subtree, `<base>`,
`<meta http-equiv>`, forms, `<source srcset>`, `<link>`, SVG and media among
them. CSS is filtered wherever it appears: no `@import`, no `expression()`, no
`javascript:` URL, and nothing that could end the element it is written into.

That bounds what is in the file. It cannot bound what a viewer does with it, so
a receiver must still serve a stored capture from an opaque origin under a
restrictive CSP — the exact header is in
[`docs/protocol.md`](docs/protocol.md).

## Publisher profiles

The registry is **data**, served by the receiver at
`GET /api/v1/capture/profiles`, fetched at startup and cached. One file drives
the extension, any acquisition script, and the receiver's own mapping; written
three times they drift, and the copy that drifts silently is the one nobody runs
by hand.

`asset_origins` is part of a profile too: the hosts, other than the page's own
origin, that this publisher serves figures and stylesheets from. Nothing else is
fetched.

`status` is a measurement, not a plan. A profile is `supported` only when a
committed fixture exists and a test asserts the container, the identity and the
figure manifest against it. `unsupported` requires a `reason` that says what was
observed. Nothing is promoted by intending to test it later.

Adding a profile is a pull request against `profiles/capture_profiles.json`. To
claim `supported`, add a skeleton fixture with `tools/make_capture_fixture.py`,
which keeps `<head>`, the article container, the figure elements and their
captions, and reduces every text block to its first sentence.

## The receiver is yours

`docs/protocol.md` is the whole contract:

- `POST /api/v1/intake/html` — bytes plus the page's own declarations, with a
  SHA-256 the receiver must verify. Answers `202` and a receipt id, because the
  bytes are received and the artifact does not exist yet.
- `GET /api/v1/intake/{receipt_id}` — what became of it.
- `GET /api/v1/capture/profiles` — the registry, as data.

The producer hands over bytes and observations. It does not hand over an
identity: the envelope physically cannot carry a DOI, so what the page declared
about itself travels in a sidecar beside the payload, as evidence the receiver
is free to disbelieve. Which work this is, where it is filed, what rights apply,
and whether anything may be published are decisions a browser cannot make.

When the endpoint is unreachable, the pair is written to the browser's download
directory under `corpus-capture/` instead, `.html` and `.json` sharing one stem.
A `4xx` is never retried down that path: downloading a payload the receiver
refused only moves the refusal.

## The library

```bash
pip install -e '.[dev]'
pytest
```

```python
from corpus_capture import build_figure_manifest, profile_for_url

profile = profile_for_url("https://www.nejm.org/doi/full/10.1056/NEJMoa2600001")
manifest = build_figure_manifest(html, profile=profile, base_url=url)
print(manifest.labels)          # ('Table 1', 'Figure 1', 'Figure 2')
print(len(manifest.decorative)) # everything kept but not claimed
```

## Privacy

- The token lives in `chrome.storage.local`, which is device-local and never
  synced.
- The extension reads the current tab only when you press the button.
- Figures and stylesheets are fetched with credentials, because publishers
  commonly gate high-resolution assets behind a session cookie. Those requests
  go only to origins the page itself was already requesting.
- Nothing is sent anywhere except the endpoint you configured.

## Scope, and what this is not

This packages a page you can already see, for your own archive. It does not
circumvent access controls, does not crawl, does not batch, and has no bypass of
any kind. What you may keep and what you may republish are governed by your
agreements with publishers, not by this tool.

Fixtures in this repository are skeletons: structure and metadata kept, article
prose cut to one sentence per block, figures replaced by a 1×1 placeholder. A
test enforces that on the committed bytes.

## Vendoring

This repository is the source of truth for the extension and the registry. A
consumer that vendors it — as a `git subtree`, a submodule, or a copy — pins a
release tag and pulls a newer one deliberately; it does not edit its copy. A
selector fixed downstream is a selector that fixes nothing for anyone else, and
the divergence is invisible until two captures of the same publisher disagree.

Fixture paths inside `profiles/capture_profiles.json` are relative to THIS
repository's root, so `corpus_capture.profiles.fixture_path()` resolves them
from the registry's own location rather than from the consumer's.

## Licence

MIT. See [`LICENSE`](LICENSE).
