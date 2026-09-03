"use strict";

// Where bytes may be fetched from, and with whose credentials.
//
// The capture runs under `<all_urls>` and fetches assets with the reader's own
// session, because publishers gate display-resolution figures on the same
// cookie the tab is already carrying. That is a confused deputy waiting to
// happen: every asset URL in the list came out of page-controlled markup, so a
// hostile page could otherwise aim an authenticated request at a loopback
// service, a private-network host, or a cloud metadata endpoint, and have the
// answer embedded in an artifact and posted to the receiver.
//
// So the answer to "may I fetch this?" is a decision, made here, with a reason
// attached. Nothing calls `fetch` on a page-supplied URL without going through
// `assetDecision`, and nothing sends credentials anywhere but the page's own
// origin.

//: Hosts that are never a publisher and always somebody's internal surface.
const DENIED_HOSTS = new Set([
  "localhost", "metadata.google.internal", "metadata.goog", "instance-data",
  "metadata", "169.254.169.254",
]);
//: Suffixes reserved for private naming. `.local` is mDNS, `.internal` and
//: `.home.arpa` are private zones, and no journal is served from any of them.
const DENIED_SUFFIXES = [
  ".local", ".localhost", ".internal", ".home.arpa", ".lan", ".intranet", ".corp",
];

export function isIpv4Literal(host) {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
}

//: RFC1918 and friends, plus the ranges that are somebody's infrastructure:
//: loopback, link-local (which is where the cloud metadata service lives),
//: carrier-grade NAT, benchmarking, multicast and broadcast.
function isPrivateIpv4(host) {
  const octets = host.split(".").map((part) => Number(part));
  if (octets.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) return true;
  const [a, b] = octets;
  if (a === 0 || a === 10 || a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 192 && b === 0) return true;
  if (a === 198 && (b === 18 || b === 19)) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  if (a >= 224) return true;
  return false;
}

// A hostname that is not a name. Browsers accept `0x7f000001` and `2130706433`
// as addresses, and both of those are 127.0.0.1 spelled to get past a check
// that only knows dotted quads. Neither is ever a publisher, so both are
// refused rather than decoded.
function isNumericHost(host) {
  return /^(0x[0-9a-f]+|\d+)$/i.test(host);
}

export function isBlockedHost(hostname) {
  const host = String(hostname || "").toLowerCase().replace(/\.$/, "");
  if (!host) return true;
  if (host.startsWith("[")) return true;            // an IPv6 literal is not a publisher
  if (DENIED_HOSTS.has(host)) return true;
  if (DENIED_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true;
  if (isNumericHost(host)) return true;
  if (isIpv4Literal(host)) return isPrivateIpv4(host);
  // A single-label name resolves through the local search domain, which is
  // exactly the intranet this must not reach.
  return !host.includes(".");
}

export function hostMatches(host, pattern) {
  const h = String(host || "").toLowerCase().replace(/^\./, "").replace(/\.$/, "");
  const p = String(pattern || "").toLowerCase().replace(/^\./, "").replace(/\.$/, "");
  if (!h || !p) return false;
  if (p.startsWith("*.")) {
    const suffix = p.slice(2);
    return h === suffix || h.endsWith(`.${suffix}`);
  }
  return h === p;
}

function parse(raw, base) {
  try {
    return new URL(String(raw || ""), base || undefined);
  } catch (_) {
    return null;
  }
}

/**
 * May this asset be fetched, and with whose credentials?
 *
 * `include` is only ever returned for the page's own origin. A publisher CDN
 * named by the profile is fetched anonymously: the whole reason to send cookies
 * is an entitlement check, and an entitlement check on a third-party host is
 * the case where sending them is most likely to be handing a session to
 * somebody the reader did not choose.
 */
export function assetDecision(rawUrl, { pageUrl, profile } = {}) {
  const page = parse(pageUrl);
  const url = parse(rawUrl, pageUrl);
  if (!url) return { allowed: false, reason: "unparsable_url" };
  if (url.protocol === "data:") return { allowed: false, reason: "already_inline" };
  // http is refused even for the page's own origin. The bytes would be fetched
  // over a network anyone on the path can rewrite, and then stored as though
  // the publisher had served them.
  if (url.protocol !== "https:") return { allowed: false, url: url.href, reason: "insecure_scheme" };
  if (url.username || url.password) {
    return { allowed: false, url: url.href, reason: "credentials_in_url" };
  }
  if (isBlockedHost(url.hostname)) {
    return { allowed: false, url: url.href, reason: "blocked_host" };
  }
  if (page && url.origin === page.origin) {
    return { allowed: true, url: url.href, credentials: "include", reason: "page_origin" };
  }
  const allowlist = (profile && profile.asset_origins) || [];
  if (allowlist.some((pattern) => hostMatches(url.hostname, pattern))) {
    return { allowed: true, url: url.href, credentials: "omit", reason: "profile_cdn" };
  }
  return { allowed: false, url: url.href, reason: "off_origin" };
}

/**
 * A link the popup is willing to put in front of the reader.
 *
 * `reader_url` arrives in receipt JSON from the configured receiver. A
 * compromised or merely wrong receiver could otherwise navigate the reader
 * anywhere, from an extension surface they trust, so the only accepted answer
 * is an https URL on the receiver's own origin.
 */
export function safeReaderUrl(raw, apiBase) {
  const url = parse(raw);
  const base = parse(apiBase);
  if (!url || !base) return null;
  if (url.protocol !== "https:" && !(base.protocol === "http:" && url.origin === base.origin)) {
    return null;
  }
  if (url.username || url.password) return null;
  if (url.origin !== base.origin) return null;
  return url.href;
}

/** The final URL a token-bearing response actually came from must be the one we asked. */
export function sameOrigin(responseUrl, apiBase) {
  const a = parse(responseUrl);
  const b = parse(apiBase);
  return Boolean(a && b && a.origin === b.origin);
}
