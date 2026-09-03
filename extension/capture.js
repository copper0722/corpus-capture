"use strict";

import { LIMITS } from "./limits.js";
import { assetDecision, hostMatches, sameOrigin } from "./net-policy.js";
import { sanitizeCss, sanitizeDocument, serializeDocument } from "./sanitize.js";
import { serializePage } from "./serialize.js";

// No default endpoint ships with the extension. The receiver is a corpus you
// run; its address is yours, it is often on a private network, and baking one
// in would be both wrong for everyone else and a disclosure for whoever built
// it. The options page asks for it and refuses to save anything that is not
// https (or localhost, for a receiver on the same machine).
export const DEFAULT_API_BASE = "";
export const PROFILES_KEY = "profileRegistry";
export const PROFILES_FETCHED_KEY = "profileRegistryFetchedAt";
export const RECEIPTS_KEY = "receipts";
export const MAX_RECEIPTS = 20;
// Every ceiling lives in limits.js, including the copy the injected serializer
// carries, so there is one table rather than numbers spread across two worlds.
const MAX_INLINE_BYTES = LIMITS.maxInlineBytes;
const ASSET_TIMEOUT_MS = LIMITS.assetTimeoutMs;

// Every request that carries the service token goes through this. `redirect:
// "error"` is the point: a receiver that answers 30x -- because it was
// misconfigured, because something in front of it was, or because it was taken
// over -- must not have the browser replay the token at whatever it named.
// Cookies are omitted for the same reason: the token IS the credential here,
// and a second one only widens what a wrong destination receives.
async function apiFetch(path, { apiBase, serviceToken, ...init } = {}) {
  const headers = { ...(init.headers || {}) };
  if (serviceToken) headers["x-corpus-service-token"] = serviceToken;
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers,
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
  });
  // Belt and braces: `redirect: "error"` already rejects a redirected response,
  // but a response that claims a different origin is not the receiver's answer
  // whatever produced it.
  if (response.redirected || !sameOrigin(response.url || apiBase, apiBase)) {
    throw new Error("receiver_origin_changed");
  }
  return response;
}

export async function settings() {
  const stored = await chrome.storage.local.get(["apiBase", "serviceToken"]);
  const configured = String(stored.apiBase || DEFAULT_API_BASE).trim();
  if (!configured) throw new Error("api_base_not_configured");
  return {
    apiBase: normalizeBase(configured),
    serviceToken: String(stored.serviceToken || "").trim(),
  };
}

// The publisher selector registry. Fetched from the receiver so one file
// governs the extension, the acquisition script and the intake mapping; cached
// so a capture still works while the receiver is unreachable, which is exactly
// when the offline download path matters most.
export async function profileRegistry({ refresh = false } = {}) {
  const stored = await chrome.storage.local.get([PROFILES_KEY, PROFILES_FETCHED_KEY]);
  const cached = stored[PROFILES_KEY];
  const age = Date.now() - Number(stored[PROFILES_FETCHED_KEY] || 0);
  if (cached && !refresh && age < 6 * 60 * 60 * 1000) return cached;
  try {
    const { apiBase, serviceToken } = await settings();
    const response = await apiFetch("/api/v1/capture/profiles", { apiBase, serviceToken });
    if (!response.ok) throw new Error(`http_${response.status}`);
    const registry = await response.json();
    await chrome.storage.local.set({
      [PROFILES_KEY]: registry, [PROFILES_FETCHED_KEY]: Date.now(),
    });
    return registry;
  } catch (_) {
    return cached || null;
  }
}

export function profileForUrl(registry, url) {
  const generic = (registry && registry.generic) || { id: "generic" };
  let host = "";
  try { host = new URL(url).hostname; } catch (_) { /* an unparsable url is generic */ }
  for (const profile of (registry && registry.profiles) || []) {
    if ((profile.host_patterns || []).some((pattern) => hostMatches(host, pattern))) {
      const merged = { ...generic, ...profile, matched: true };
      for (const key of ["article_container_selectors", "figure_selectors",
                         "caption_selectors", "access_markers", "drop_selectors",
                         "decorative_asset_patterns", "asset_origins"]) {
        if (!profile[key]) merged[key] = generic[key] || [];
      }
      return merged;
    }
  }
  return { ...generic, id: "generic", status: "generic", matched: false };
}

export function normalizeBase(raw) {
  const parsed = new URL(String(raw || "").trim());
  const local = ["127.0.0.1", "localhost"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) {
    throw new Error("api_base_requires_https");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("api_base_invalid");
  }
  return parsed.origin;
}

export function normalizeDoi(value) {
  let text = String(value || "").trim();
  if (!text) return null;
  const lowered = text.toLowerCase();
  for (const prefix of ["https://doi.org/", "http://doi.org/", "http://dx.doi.org/",
                        "https://dx.doi.org/", "info:doi/", "doi:"]) {
    if (lowered.startsWith(prefix)) {
      text = text.slice(prefix.length).trim();
      break;
    }
  }
  const match = text.match(/10\.\d{4,9}\/\S+/);
  if (!match) return null;
  return match[0].replace(/[.,;)]+$/, "").toLowerCase();
}

//: Path segments a publisher appends AFTER the DOI in a landing-page URL.
const DOI_URL_TAIL = /\/(full|abstract|pdf|epdf|epub|html|text|references|figures|metrics)\/?$/i;

export function doiFromUrl(url) {
  // A DOI inside a URL runs to the end of the path, so `\S+` swallows whatever
  // the publisher appended. Cut the known viewer segments, then any trailing
  // slash. Nothing here invents a DOI: no match still means no DOI.
  let candidate = normalizeDoi(String(url || "").split(/[?#]/)[0]);
  if (!candidate) return null;
  for (let i = 0; i < 3; i += 1) {
    const trimmed = candidate.replace(DOI_URL_TAIL, "");
    if (trimmed === candidate) break;
    candidate = trimmed;
  }
  return candidate.replace(/\/+$/, "") || null;
}

export function captureSlug(doi, url) {
  const normalized = normalizeDoi(doi);
  let raw = normalized;
  if (!raw) {
    try {
      const parsed = new URL(url);
      const tail = (parsed.pathname || "").replace(/\/+$/, "").split("/").pop();
      raw = tail ? `${parsed.hostname}-${tail}` : parsed.hostname;
    } catch (_) {
      raw = "page";
    }
  }
  const slug = raw.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug.slice(0, 72).replace(/-+$/, "") || "capture";
}

export function downloadName(doi, url, capturedAt) {
  const iso = capturedAt.toISOString();
  const stamp = `${iso.slice(0, 4)}${iso.slice(5, 7)}${iso.slice(8, 10)}-${iso.slice(11, 13)}${iso.slice(14, 16)}`;
  return `${captureSlug(doi, url)}-${stamp}`;
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function bytesToBase64(bytes) {
  let binary = "";
  // Chunked because String.fromCharCode(...bytes) blows the argument limit on
  // anything larger than a favicon, which is exactly the case that matters.
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

/**
 * Read at most `cap` bytes, and stop the transfer rather than the allocation.
 *
 * `arrayBuffer()` materializes whatever arrives before anything can measure it,
 * so a response with no `content-length` -- or a dishonest one -- was a
 * ceiling that only applied after the memory had been spent. The declared
 * length is checked first because it is free, and then disbelieved.
 */
export async function readBounded(response, cap, controller) {
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > cap) {
    controller.abort();
    return null;
  }
  const reader = response.body && response.body.getReader
    ? response.body.getReader() : null;
  if (!reader) {
    const buffer = new Uint8Array(await response.arrayBuffer());
    return buffer.length > cap ? null : buffer;
  }
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > cap) {
      controller.abort();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

async function fetchAsset(url, credentials, cap) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ASSET_TIMEOUT_MS);
  try {
    // `credentials: "include"` is the reason an entitled figure comes back at
    // all: publishers gate display-resolution images on the same session cookie
    // the reader is already using in this tab. It is passed in rather than
    // hardcoded because `assetDecision` returns it only for the page's own
    // origin -- see net-policy.js.
    //
    // `redirect: "error"` closes the hole the origin check would otherwise
    // leave: an allowed URL that answers 302 could hand the request, cookies
    // and all, to a host no policy ever looked at.
    const response = await fetch(url, {
      credentials,
      redirect: "error",
      cache: "force-cache",
      signal: controller.signal,
    });
    if (!response.ok || response.redirected) return null;
    const buffer = await readBounded(response, cap, controller);
    if (!buffer || !buffer.length) return null;
    const type = (response.headers.get("content-type") || "").split(";")[0].trim();
    return { bytes: buffer, type };
  } catch (_) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function inlineAssets(page, onProgress, profile) {
  // The artifact is built as a DOM and serialized once, at the end.
  //
  // It used to be built by substituting fetched bytes into an already
  // serialized string, and that is a parser the code did not know it had: a
  // stylesheet containing `</STYLE` closed the element the CSS was being
  // written into, and everything after it was markup in the stored file. The
  // escape that guarded it matched only the lowercase spelling. Setting
  // `style.textContent` on a node cannot do that, whatever the bytes say,
  // because at no point are they parsed as HTML.
  if (typeof DOMParser === "undefined") throw new Error("dom_parser_required");
  const doc = new DOMParser().parseFromString(page.html, "text/html");

  // Where each placeholder ended up, found once. An asset referenced by three
  // images carries one token, so the map is token -> nodes.
  const imageNodes = new Map();
  const styleNodes = new Map();
  const push = (map, key, node) => {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(node);
  };
  for (const image of doc.querySelectorAll("img[src]")) {
    push(imageNodes, image.getAttribute("src"), image);
  }
  for (const style of doc.querySelectorAll("style")) {
    const found = /^\s*\/\*(corpus-asset-[0-9a-f]+-\d+-end)\*\/\s*$/.exec(style.textContent || "");
    if (found) push(styleNodes, found[1], style);
  }

  let budget = MAX_INLINE_BYTES;
  let inlined = 0;
  let dropped = 0;
  const refused = [];
  for (const asset of page.assets) {
    if (onProgress) onProgress(asset.index + 1, page.assets.length);
    const token = `corpus-asset-${page.nonce}-${asset.index}-end`;
    const targets = asset.kind === "style" ? styleNodes.get(token) : imageNodes.get(token);
    // Every URL here came out of page-controlled markup. What may be fetched,
    // and with whose cookies, is decided before anything touches the network.
    const decision = assetDecision(asset.url, { pageUrl: page.url, profile });
    if (!decision.allowed) refused.push({ asset_url: asset.url, reason: decision.reason });
    // The per-asset ceiling and what is left of the aggregate one, whichever
    // is smaller: an asset that cannot fit in the budget must not be read at
    // all, let alone read and then discarded.
    const cap = Math.min(LIMITS.maxAssetBytes, budget);
    const fetched = decision.allowed && cap > 0 && targets
      ? await fetchAsset(decision.url, decision.credentials, cap)
      : null;
    if (!fetched || fetched.bytes.length > budget) {
      dropped += 1;
      // A dropped asset leaves nothing behind. A leftover placeholder would
      // read as a live remote reference, and a half-written data URI would read
      // as a corrupt capture; neither is what happened.
      for (const node of targets || []) {
        if (asset.kind === "style") node.remove();
        else node.removeAttribute("src");
      }
      continue;
    }
    budget -= fetched.bytes.length;
    inlined += 1;
    for (const node of targets) {
      if (asset.kind === "style") {
        node.textContent = sanitizeCss(new TextDecoder().decode(fetched.bytes), asset.url);
      } else {
        const type = fetched.type && fetched.type.startsWith("image/") ? fetched.type : "image/jpeg";
        node.setAttribute("src", `data:${type};base64,${bytesToBase64(fetched.bytes)}`);
      }
    }
  }

  // Last, over everything -- including whatever a stylesheet or an alt text
  // brought in. The in-page pass was a size measure; this is the one that
  // decides what the artifact contains.
  const removed = sanitizeDocument(doc, { baseUrl: page.url });
  return { html: serializeDocument(doc), inlined, dropped, refused, removed };
}

export async function capturePage(tabId, onProgress, profile) {
  const nonce = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  const [injected] = await chrome.scripting.executeScript({
    target: { tabId },
    func: serializePage,
    args: [nonce, profile || {}, LIMITS],
  });
  const page = injected && injected.result;
  if (page && page.error) throw new Error(page.error);
  if (!page || !page.html) throw new Error("page_not_serializable");
  page.nonce = nonce;
  const { html, inlined, dropped, refused, removed } = await inlineAssets(
    page, onProgress, profile
  );
  // The page's own declaration first; a URL is only ever a fallback, and it has
  // to be trimmed -- NEJM serves `/do/10.1056/NEJMdo008670/full/`, whose path
  // tail is not part of the DOI and turned one into `10.1056/NEJMdo008670/full/`.
  const doi = normalizeDoi(page.meta.doi)
    || doiFromUrl(page.canonical_url)
    || doiFromUrl(page.url);
  const payloadBytes = new TextEncoder().encode(html).length;
  // The receiver refuses this too, but refusing it here means the bytes are
  // never sent and the reader is told why rather than reading `http_413`.
  if (payloadBytes > LIMITS.maxPayloadBytes) throw new Error("capture_too_large");

  return {
    html,
    bytes: payloadBytes,
    sha256: await sha256Hex(html),
    url: page.canonical_url || page.url,
    final_url: page.url,
    doi,
    title: (page.meta.title || "").slice(0, LIMITS.maxTitleChars) || null,
    date_published: (page.meta.date_published || "").slice(0, 32) || null,
    publisher_meta: page.publisher_meta || {},
    authors: page.authors || [],
    access: page.access || "unknown",
    meta_sha256: await sha256Hex(page.meta_block || ""),
    profile_id: page.profile_id || "generic",
    container_selector: page.container_selector || "",
    scoped_to_article: Boolean(page.scoped_to_article),
    figures: page.figures || [],
    decorative: page.decorative || [],
    images: {
      inlined, dropped, total: page.assets.length, refused: refused.length,
      overflow: page.assets_overflow || 0,
    },
    // Kept, because "the capture is missing a figure" and "the capture refused
    // to fetch a figure from somewhere it should not have" are different facts
    // and only one of them is a bug in the profile.
    refused_assets: refused.slice(0, 100),
    // What the allowlist took out. Recorded so an artifact that lost half its
    // markup is visible as that, rather than as a publisher who changed layout.
    sanitized: removed,
  };
}

export async function submitCapture(capture, capturedAt) {
  const { apiBase, serviceToken } = await settings();
  const response = await apiFetch("/api/v1/intake/html", {
    apiBase,
    serviceToken,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: capture.url,
      html: capture.html,
      sha256: capture.sha256,
      captured_at: capturedAt.toISOString(),
      doi: capture.doi,
      title: capture.title,
      date_published: capture.date_published,
      final_url: capture.final_url,
      publisher_meta: capture.publisher_meta,
      authors: capture.authors,
      access: capture.access,
      meta_sha256: capture.meta_sha256,
      profile: capture.profile_id,
      figures: capture.figures,
      capture_tool: "chrome-capture/1.2.0",
    }),
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* keep the status */ }
  if (!response.ok) {
    const detail = (payload && (payload.detail || payload.error)) || `http_${response.status}`;
    const error = new Error(String(detail));
    error.status = response.status;
    throw error;
  }
  return payload;
}

export async function readReceipt(receiptId) {
  const { apiBase, serviceToken } = await settings();
  const response = await apiFetch(`/api/v1/intake/${encodeURIComponent(receiptId)}`, {
    apiBase, serviceToken,
  });
  if (!response.ok) throw new Error(`http_${response.status}`);
  return response.json();
}

export async function downloadFallback(capture, capturedAt) {
  // The sidecar is not a convenience copy of the filename: the intake worker
  // reads identity from it and from nothing else, so a download without one is
  // an unadmissible .html and stays in the inbox.
  const stem = downloadName(capture.doi, capture.url, capturedAt);
  const sidecar = {
    schema: "corpus-capture-sidecar-v2",
    url: capture.url,
    final_url: capture.final_url,
    doi: capture.doi,
    title: capture.title,
    date_published: capture.date_published,
    captured_at: capturedAt.toISOString(),
    html_sha256: capture.sha256,
    html_bytes: new TextEncoder().encode(capture.html).length,
    capture_tool: "chrome-capture/1.2.0",
    payload_name: `${stem}.html`,
    publisher_meta: capture.publisher_meta || {},
    authors: capture.authors || [],
    access: capture.access || "unknown",
    meta_sha256: capture.meta_sha256 || null,
    profile: capture.profile_id || "generic",
    container_selector: capture.container_selector || "",
    figures: capture.figures || [],
    decorative_count: (capture.decorative || []).length,
  };
  const htmlUrl = URL.createObjectURL(new Blob([capture.html], { type: "text/html" }));
  const jsonUrl = URL.createObjectURL(
    new Blob([JSON.stringify(sidecar, null, 1)], { type: "application/json" })
  );
  try {
    await chrome.downloads.download({
      url: htmlUrl, filename: `corpus-capture/${stem}.html`, saveAs: false,
    });
    await chrome.downloads.download({
      url: jsonUrl, filename: `corpus-capture/${stem}.json`, saveAs: false,
    });
  } finally {
    // Late enough that the download has read the blob, and unconditional so a
    // failed second download cannot leak the first one's memory.
    setTimeout(() => {
      URL.revokeObjectURL(htmlUrl);
      URL.revokeObjectURL(jsonUrl);
    }, 60000);
  }
  return { stem, sidecar };
}

export async function rememberReceipt(entry) {
  const stored = await chrome.storage.local.get([RECEIPTS_KEY]);
  const rows = Array.isArray(stored[RECEIPTS_KEY]) ? stored[RECEIPTS_KEY] : [];
  const kept = rows.filter((row) => row.receipt_id !== entry.receipt_id);
  kept.unshift(entry);
  await chrome.storage.local.set({ [RECEIPTS_KEY]: kept.slice(0, MAX_RECEIPTS) });
  return kept.slice(0, MAX_RECEIPTS);
}

export async function listReceipts() {
  const stored = await chrome.storage.local.get([RECEIPTS_KEY]);
  return Array.isArray(stored[RECEIPTS_KEY]) ? stored[RECEIPTS_KEY] : [];
}
