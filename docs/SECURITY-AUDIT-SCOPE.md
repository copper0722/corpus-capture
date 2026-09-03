# Security audit scope

This checklist is the brief handed to an independent reviewer before this
repository is announced anywhere. It is written down so the audit measures the
same surface twice, and so a later reader can tell which questions were asked.

The extension is deliberately privileged. It holds `<all_urls>` and it fetches
assets with the reader's own credentials, because publishers gate
high-resolution figures behind a session cookie. Every item below exists to
check that the privilege buys only what it is claimed to buy.

## 1. Manifest and permission minimisation

- Is every entry in `permissions` used? `activeTab`, `scripting`, `storage`,
  `downloads`, `alarms` are declared; find the call site of each.
- Is `host_permissions: ["<all_urls>"]` necessary, or would `activeTab` plus a
  narrower asset fetch do? If it is necessary, say what breaks without it.
- Confirm the absence of `cookies`, `webRequest`, `history`, `tabs` and any
  `content_scripts` block: nothing may run on a page the user did not act on.
- Is any script injected from a stored string, or fetched at runtime?

## 2. Extension page XSS and CSP

- `content_security_policy.extension_pages` forbids `unsafe-eval`. Verify no
  `eval`, `new Function`, `innerHTML`, `insertAdjacentHTML`, or `document.write`
  on any path that touches page-controlled data.
- The popup renders the profile badge, the figure manifest, captions and the
  reader URL. All of that originates on the captured page. Confirm each is
  written as text, and that a link's `href` is scheme-checked before it is set.
- Options page: the API address and token are user input rendered back. Check
  the failure path, where an error message may carry attacker-influenced text.

## 3. Serialization and third-party content

- `serialize.js` strips `script`, `noscript`, `iframe` and `on*` attributes.
  Enumerate what it does NOT strip: `srcset`, `style` with `url()`, SVG with
  embedded scripts, `<link rel=import>`, `<meta http-equiv="refresh">`,
  `<base>`, event handlers spelled with unusual casing or entities.
- The output is a self-contained HTML file that a receiver may later open in a
  browser. Treat the artifact as untrusted content at rest and say what a
  receiver must do before rendering it.
- Are `data:` URIs bounded in count and size? An adversarial page can make the
  capture enormous.

## 4. Sidecar and envelope injection

- Every sidecar field originates on the page: title, DOI, authors, captions,
  publisher metadata. Confirm the receiver treats them as data, and that the
  reference receiver never joins any of them onto a filesystem path.
- Check the offline path specifically: `chrome.downloads.download` is called
  with a filename built from the DOI and the URL. Verify path traversal, absolute
  paths and reserved names cannot reach it.
- Confirm the envelope cannot carry `source_uid`, `doi`, `bundle_path`, `tags`
  or `rights` — identity and rights are not a producer's to assert.

## 5. Token storage and transport

- Token in `chrome.storage.local`, never `chrome.storage.sync`. Confirm it is
  not logged, not put in a URL, and not sent to any origin but the configured
  base.
- The endpoint must be `https`, with `http` accepted only for `localhost` and
  `127.0.0.1`. Check the normalisation for a bypass: userinfo (`https://x@evil`),
  trailing dot, uppercase scheme, IPv6 loopback, DNS names that resolve to
  loopback.
- What happens to the token on an unexpected redirect from the configured base?

## 6. Residual identity from the private tree

This code was written inside a private operations repository. Confirm nothing
survived the scrub: internal hostnames, Tailscale addresses, absolute paths from
the author's machines, internal environment-variable names, private tracker card
numbers, personal names or email addresses. `tests/test_public_safety.py` scans
for these; audit the LIST as well as the result, since a scanner only finds what
it was told to look for.

## 7. Fixtures and restricted full text

- `fixtures/` must be skeletons: structure and metadata kept, prose reduced to
  one sentence per block, images replaced by a placeholder. Verify on the
  committed bytes, not on the generator's intent — the first version of the
  reducer only handled `<p>` and `<li>`, and a publisher that lays its article
  out in `<div>`s kept about ten thousand characters of subscription text.
- Confirm no fixture carries a figure image, a full reference list, or
  supplementary material.

## 8. Receiver contract

- `docs/receiver-reference.md`: hash recomputed over the received bytes before
  anything is written; size bounded; staging directory plus a single rename so
  no half-written envelope is observable; handoff id generated, never accepted.
- Authentication compares the token in constant time, and refuses when no token
  is configured rather than allowing anonymous writes.
- Receipt lookup: does an unauthenticated caller learn whether a receipt id
  exists?
- CORS is open by necessity (`chrome-extension://` origins). Confirm that this,
  combined with token auth, does not create a cross-site write from an ordinary
  web page.

## Reporting

Findings go back on the tracking card with a severity and a reproduction. The
repository stays unannounced until they are addressed.
