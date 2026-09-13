---
summary: Public browser capture producer and open-intake protocol boundary.
status: active
owner: repository-maintainer
---

# corpus-capture

Scope: the public browser-capture producer, publisher-profile registry, capture
library, and open intake protocol. It packages a page the reader already may
access; it does not own corpus identity, rights, publication, or receiver data.

- Capture only on explicit reader action and only the current article container
  plus allowed assets. Do not crawl, batch, log in, or bypass access controls.
- The receiver endpoint is user-configured; use HTTPS except permitted local
  development, reject unsafe origins/redirects, and keep service tokens in
  device-local storage outside Git.
- Profiles need fixture/test evidence before a status is promoted. A sidecar is
  evidence, not identity or publication authority.
- Sanitize stored HTML/CSS with the allowlist; receivers serve captures from a
  restrictive opaque origin/CSP.
- Keep article text, cookies, account material, and private receiver payloads
  out of the repo. Consumers pin releases and do not edit vendored copies.

Read `AGENT_REFERENCE.md` only for the selected protocol/profile task.
## Boundary

The producer captures an explicitly selected page and does not own identity, rights, publication, or receiver data.
## Task route

Use the selected extension, publisher profile, fixture, or receiver protocol; keep the receiver decision separate.
## Verification

Run the selected fixture/profile and protocol checks and inspect sidecar/sanitization output. No receiver deployment is implied.
