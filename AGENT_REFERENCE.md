# corpus-capture public protocol

Preserved operational detail from the supplied README:

- The MV3 extension captures only after an explicit reader action. It keeps the
  article container and head, uses the configured receiver, and does not crawl,
  log in, or collect in the background.
- Figure selection is structural: publisher profile figure elements, then
  article-contained `<figure>`, then figure/table-labelled images. A sidecar
  records observations; it does not decide identity, rights, or publication.
- Fetch only the page origin or profile-allowlisted CDN without redirects; deny
  unsafe/private origins and plaintext remote HTTP. Refused assets remain
  visible in the receipt.
- Sanitize elements/attributes/CSS through the allowlist. Receivers serve the
  inert capture from an opaque origin with restrictive CSP.
- The receiver owns intake, identity, rights, and publication. Offline pairs
  are local download artifacts; a refused response is not silently retried.

Public-safe disposition: no private receiver host, account, token value, cookie,
or captured article payload is retained here.
