"use strict";

// Phase 3 of a capture: the article's own attachments.
//
// The HTML with its figures is the page as the reader saw it. It is not the
// whole article: the publisher's PDF, the supplementary appendix, the audio
// interview and the embedded video are linked from the page, not in it, and a
// bundle without them is a bundle the reader has to go back to the browser for.
// The operator's request (2026-09-17): one click saves the whole page with its
// images AND every linked file -- body PDF, supplement, mp3, embedded video --
// into the same, automatically created bundle.
//
// Four functions here run INSIDE the page: they are injected as stringified
// functions, import nothing, and see only their arguments and the page. The
// discovery has to run there because the links are page markup; the fetch has
// to run there because a publisher's media path is hotlink-protected -- it
// wants the tab's cookies AND its Referer, and only a request the page itself
// issues carries both. The bytes then cross into the extension as base64
// through executeScript, one bounded chunk at a time, because the extension's
// isolated world cannot read a page-origin blob and a service worker cannot
// make one.
//
// Everything discovered here is page-controlled text. What may be fetched, from
// where and with whose credentials is decided by `attachmentDecision` in
// net-policy.js before anything touches the network, exactly as figures are.
// A page cannot name an attachment that reaches a private host.

import { LIMITS } from "./limits.js";
import { attachmentDecision } from "./net-policy.js";

//: What an attachment is, as the receiver files it. `other` is a link the page
//: presented as a download that fits none of the four; it is kept, labelled,
//: and never mistaken for the article's PDF.
export const ATTACHMENT_KINDS = ["pdf", "supplement", "audio", "video", "other"];
//: `duplicate` is a candidate whose bytes another row already holds -- the same
//: PDF behind `citation_pdf_url` and a `?download=true` link, or one video
//: reachable from a JW media id and from an NEJM card. It is complete, not a
//: gap, and it is never downloaded or stored twice.
export const ATTACHMENT_STATUSES = ["captured", "duplicate", "failed"];

//: Extensions the receiver will accept for a stored attachment. The name is
//: chosen from the bytes' declared type first and the link's own suffix second;
//: anything outside this table is stored as `.bin` and still recorded with its
//: MIME type, so a file with an odd suffix is kept rather than refused.
export const ATTACHMENT_EXTENSIONS = {
  "application/pdf": ".pdf",
  "audio/mpeg": ".mp3",
  "audio/mp3": ".mp3",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/ogg": ".ogg",
  "video/mp4": ".mp4",
  "video/webm": ".webm",
  "video/quicktime": ".mov",
  "application/zip": ".zip",
  "application/x-zip-compressed": ".zip",
  "application/msword": ".doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/vnd.ms-excel": ".xls",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
  "application/vnd.ms-powerpoint": ".ppt",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
  "text/csv": ".csv",
  "text/tab-separated-values": ".tsv",
  "text/plain": ".txt",
  "application/json": ".json",
  "application/xml": ".xml",
  "text/xml": ".xml",
  "application/rtf": ".rtf",
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/tiff": ".tif",
};
const KNOWN_SUFFIXES = new Set(Object.values(ATTACHMENT_EXTENSIONS).concat([".ts", ".m4v", ".epub"]));

// ---------------------------------------------------------------------------
// Injected into the page. No imports, no module scope: the body is what runs.
// ---------------------------------------------------------------------------

/**
 * Enumerate the attachments the page links to. Runs inside the page.
 *
 * Returns candidates only -- a kind, a URL, a label and where it was found.
 * Nothing is fetched here; whether each one MAY be fetched is a policy decision
 * the extension makes afterwards. The list is bounded by `limits.maxAttachments`
 * because every entry is an authenticated request the page is asking for.
 */
export function discoverAttachmentsInPage(profile, limits) {
  profile = profile || {};
  const cap = Object.assign({ maxAttachments: 40 }, limits || {});
  const out = [];
  const seen = new Set();
  let overflow = 0;

  const abs = (raw) => {
    try {
      const url = new URL(String(raw || "").trim(), document.baseURI);
      if (!/^https?:$/.test(url.protocol)) return "";
      url.hash = "";
      return url.href;
    } catch (_) {
      return "";
    }
  };
  const textOf = (node) =>
    String((node && (node.innerText || node.textContent)) || "")
      .replace(/\s+/g, " ").trim().slice(0, 200);
  const qsa = (selector, root) => {
    try { return Array.from((root || document).querySelectorAll(selector)); }
    catch (_) { return []; }
  };
  // Query parameters that change how a file is served, never which file it is.
  // `?download=true` is the NEJM PDF link beside `citation_pdf_url`; counting
  // them as two attachments downloaded one article twice.
  const PRESENTATION_PARAMS = /^(download|dl|downloadtype|attachment|inline|role|rel|utm_[a-z]+|af|rss|cookieset|needaccess)$/i;
  const identity = (href) => {
    try {
      const url = new URL(href);
      for (const name of [...url.searchParams.keys()]) {
        if (PRESENTATION_PARAMS.test(name)) url.searchParams.delete(name);
      }
      return url.href;
    } catch (_) {
      return href;
    }
  };
  const add = (kind, raw, label, source, extra) => {
    const found = abs(raw);
    if (!found) return false;
    const href = identity(found);
    if (seen.has(href)) return false;
    if (out.length >= cap.maxAttachments) {
      overflow += 1;
      return false;
    }
    seen.add(href);
    out.push(Object.assign(
      { index: out.length + 1, kind, url: found, label: String(label || "").slice(0, 200), source },
      extra || {}
    ));
    return true;
  };

  // The article container, for the one rule that needs it: a bare `.pdf` link
  // inside the article is the article's; the same link in the site footer is
  // the author guidelines.
  const containerSelectors = Array.isArray(profile.article_container_selectors)
    && profile.article_container_selectors.length
    ? profile.article_container_selectors
    : ['[itemprop="articleBody"]', "article", "main", '[role="main"]', "#bodyContent"];
  let articleRoot = null;
  for (const selector of containerSelectors) {
    let node = null;
    try { node = document.querySelector(selector); } catch (_) { node = null; }
    if (node && (node.innerText || "").length > 400) { articleRoot = node; break; }
  }
  const inArticle = (node) => Boolean(articleRoot && articleRoot.contains(node));
  // Publisher text-to-speech is a reading aid, not an authored audio attachment.
  // Match the explicit player marker, not its host or the word "listen": an
  // interview can use the same media provider and must still be captured.
  const isReadAloud = (node) => Boolean(node.closest(".audio-player--tts"));

  const SUPPLEMENT_HREF = /\/doi\/suppl\/|\/suppl_file\/|supplement|supplementary|supplemental|\/appendix|MOESM|\/mmc\d|\/media\/[^/]*(?:suppl|appendix)|\.suppl\.|_suppl|-suppl|_appendix|_protocol|-protocol|_sap\b|data[-_]sharing|disclosure/i;
  const SUPPLEMENT_LABEL = /supplement|appendix|protocol|disclosure|data sharing|statistical analysis plan|\be-?(?:table|figure|appendix|method|component)s?\b|補充|附錄|附件/i;
  const DOC_SUFFIX = /\.(pdf|docx?|xlsx?|pptx?|zip|csv|tsv|txt|json|xml|rtf|mp4|mov|m4v|webm|mp3|m4a|wav)(?:[?#]|$)/i;
  const AUDIO_SUFFIX = /\.(mp3|m4a|wav|ogg)(?:[?#]|$)/i;
  const VIDEO_SUFFIX = /\.(mp4|m4v|webm|mov)(?:[?#]|$)/i;
  const HLS_SUFFIX = /\.m3u8(?:[?#]|$)/i;
  const PDF_HREF = /\/doi\/e?pdf(?:direct)?\/|\/content\/pdf\/|\/article\/pdf\/|\/articlepdf\/|\/pdf\/|\.pdf(?:[?#]|$)/i;
  const PDF_LABEL = /\bpdf\b|download|full text|全文|下載/i;

  const isSupplementLink = (href, label) =>
    SUPPLEMENT_HREF.test(href) || (SUPPLEMENT_LABEL.test(label) && DOC_SUFFIX.test(href));

  // A PDF on somebody else's site is a document the article cites, not the
  // article: a cycling position statement links the WADA Prohibited List from
  // its body text. The article's own PDF is on the page's origin, on a host the
  // profile names, or declared by citation_pdf_url.
  const namedHosts = []
    .concat(Array.isArray(profile.attachment_origins) ? profile.attachment_origins : [])
    .concat(Array.isArray(profile.asset_origins) ? profile.asset_origins : [])
    .map((pattern) => String(pattern || "").toLowerCase().replace(/^\./, "").replace(/\.$/, ""))
    .filter(Boolean);
  const onOwnSite = (raw) => {
    const found = abs(raw);
    if (!found) return false;
    const url = new URL(found);
    if (url.origin === location.origin) return true;
    const host = url.hostname.toLowerCase();
    return namedHosts.some((pattern) => (pattern.startsWith("*.")
      ? host === pattern.slice(2) || host.endsWith(`.${pattern.slice(2)}`)
      : host === pattern));
  };
  // Link text that is the link's own address says nothing about the file.
  const isAddress = (label) => /^(?:https?:\/\/|www\.)\S+$/i.test(label);

  // 1. The article's PDF. The page's own declaration first; the publisher's
  //    reader links second. A supplement that happens to be a PDF is not the
  //    article and is classified before the PDF rule can claim it.
  for (const meta of qsa('meta[name="citation_pdf_url"]')) {
    add("pdf", meta.content, "Full text PDF", "citation_pdf_url");
  }
  for (const anchor of qsa("a[href]")) {
    const href = anchor.getAttribute("href") || "";
    if (!href || /^(javascript|mailto|tel):/i.test(href)) continue;
    const label = textOf(anchor) || anchor.getAttribute("title") || anchor.getAttribute("aria-label") || "";
    if (AUDIO_SUFFIX.test(href)) {
      if (!isReadAloud(anchor)) add("audio", href, label || "Audio", "anchor");
      continue;
    }
    if (HLS_SUFFIX.test(href)) { add("video", href, label || "Video", "anchor", { resolver: "hls" }); continue; }
    if (VIDEO_SUFFIX.test(href)) { add("video", href, label || "Video", "anchor"); continue; }
    if (isSupplementLink(href, label)) {
      if (DOC_SUFFIX.test(href) || /\/doi\/suppl\/|\/suppl_file\/|MOESM|\/mmc\d/i.test(href)) {
        add("supplement", href, label || "Supplement", "anchor");
      }
      continue;
    }
    if (PDF_HREF.test(href)) {
      if (!onOwnSite(href)) continue;
      const strong = /\/doi\/e?pdf(?:direct)?\/|\/content\/pdf\/|\/article\/pdf\/|\/articlepdf\//i.test(href);
      if (strong || inArticle(anchor) || (PDF_LABEL.test(label) && !isAddress(label))) {
        add("pdf", href, label || "PDF", "anchor");
      }
    }
  }

  // 2. Media the page plays. `<audio>`/`<video>` carry their source on the
  //    element or a child `<source>`; a lazy player parks it on a data-*
  //    attribute. NEJM's downloadable interview is a `/cms/asset/.../*.mp3`
  //    anchor, already claimed above, which is why the anchor pass runs first.
  for (const node of qsa("audio[src], audio source[src], [data-audio-src], [data-src]")) {
    if (isReadAloud(node)) continue;
    const src = node.getAttribute("src") || node.getAttribute("data-audio-src") || node.getAttribute("data-src") || "";
    if (!src) continue;
    if (AUDIO_SUFFIX.test(src) || node.tagName === "AUDIO" || (node.parentElement && node.parentElement.tagName === "AUDIO")) {
      add("audio", src, node.getAttribute("title") || node.getAttribute("aria-label") || "Audio", "audio_element");
    }
  }
  for (const node of qsa("video[src], video source[src]")) {
    const src = node.getAttribute("src") || "";
    if (!src || /^blob:/i.test(src)) continue;
    if (HLS_SUFFIX.test(src)) add("video", src, "Video", "video_element", { resolver: "hls" });
    else add("video", src, node.getAttribute("title") || "Video", "video_element");
  }

  // 3. JW Player. The player is a script and travels nowhere, but its media id
  //    is in the markup, and JW's public metadata endpoint names an mp4
  //    rendition for it. Recorded as a video the extension still has to
  //    resolve; the resolution and the fetch are anonymous and policy-gated.
  const html = (document.documentElement && document.documentElement.innerHTML) || "";
  const JW_ID = /(?:media_id=|mediaid=["']?|mediaID=["']?|data-media-id=["']|jwplayer\.com\/(?:players|manifests|videos|previews|v2\/media)\/|jwplatform\.com\/(?:players|manifests|videos|v2\/media)\/)([A-Za-z0-9]{8})\b/g;
  const jwIds = new Set();
  let match;
  while ((match = JW_ID.exec(html)) !== null) {
    jwIds.add(match[1]);
    if (jwIds.size >= 12) break;
  }
  // The metadata endpoint is publisher knowledge and lives in the registry
  // (`media_metadata_endpoints`, host and path only), not here: without it a
  // media id cannot be resolved, so it is not offered as a candidate at all.
  const jwTemplate = String((profile.media_metadata_endpoints || {}).jwplayer || "");
  const jwUsable = /^[A-Za-z0-9.-]+\/\S*\{media_id\}/.test(jwTemplate);
  for (const id of jwUsable ? jwIds : []) {
    add("video", "https:" + "//" + jwTemplate.split("{media_id}").join(id), "Video",
      "jwplayer", { resolver: "jwplayer", media_id: id });
  }

  // 4. NEJM Quick Take / Double Take cards. The card carries the player's
  //    credentialed ajax URL, and that document names the JW media id. The
  //    ref is recorded with its nejmdo DOI so the resolver can report which
  //    video it was, and so a text-only card (a Research Summary uses the same
  //    markup) is reported as "not a video" rather than as a failure.
  for (const node of qsa("[data-ajaxurl]")) {
    const ajax = node.getAttribute("data-ajaxurl") || "";
    if (!ajax) continue;
    let nejmdo = node.getAttribute("data-nejmdo") || node.getAttribute("data-doi") || "";
    if (!nejmdo) {
      const found = /\/do\/(10\.1056\/NEJMdo\d+)/i.exec(ajax);
      if (found) nejmdo = found[1];
    }
    if (!nejmdo) {
      const child = node.querySelector("[data-multimedia-contentid]");
      if (child) nejmdo = child.getAttribute("data-multimedia-contentid") || "";
    }
    add("video", ajax, textOf(node).slice(0, 120) || "Video", "nejm_do", { resolver: "nejm_do", nejmdo });
  }
  for (const node of qsa('.ng-do-media_popup, [class*="ng-do-media"]')) {
    const blob = [node.getAttribute("href"), node.getAttribute("data-doi"), node.getAttribute("data-nejmdo"), node.outerHTML]
      .filter(Boolean).join(" ");
    const found = /10\.1056\/NEJMdo\d+/i.exec(blob);
    if (!found) continue;
    const nejmdo = found[0];
    if (out.some((row) => (row.nejmdo || "").toLowerCase() === nejmdo.toLowerCase())) continue;
    add("video", `/do/${nejmdo}/full/`, textOf(node).slice(0, 120) || "Video", "nejm_do", { resolver: "nejm_do", nejmdo });
  }

  // 5. Whatever the publisher profile names. A profile selector is publisher
  //    knowledge and therefore registry data; the kind is read off the link.
  const guessKind = (href, label) => {
    if (AUDIO_SUFFIX.test(href)) return "audio";
    if (VIDEO_SUFFIX.test(href) || HLS_SUFFIX.test(href)) return "video";
    if (isSupplementLink(href, label)) return "supplement";
    if (PDF_HREF.test(href)) return "pdf";
    return "other";
  };
  for (const selector of (Array.isArray(profile.attachment_link_selectors) ? profile.attachment_link_selectors : [])) {
    for (const anchor of qsa(selector)) {
      const href = anchor.getAttribute("href") || anchor.getAttribute("src") || "";
      if (!href) continue;
      const label = textOf(anchor) || anchor.getAttribute("title") || "";
      const kind = guessKind(href, label);
      if (isReadAloud(anchor)) continue;
      add(kind, href, label, `profile:${selector.slice(0, 80)}`, HLS_SUFFIX.test(href) ? { resolver: "hls" } : undefined);
    }
  }

  return { attachments: out, overflow, scoped: articleRoot !== null };
}

/**
 * Fetch one attachment with the page's own session and park it as a Blob in
 * the isolated world, keyed by `token`. Runs inside the page.
 *
 * The response is accepted only from the page's origin or an origin the caller
 * lists: a redirect can carry a request anywhere, and the policy was decided
 * against the URL the page named, not the one it ended up at. The read is
 * bounded while streaming, because a declared length is a claim. The digest is
 * computed HERE, over the exact bytes parked, so the receiver's check is
 * against what the page served and not against what survived the transfer.
 */
export async function fetchAttachmentInPage(url, token, options) {
  const opts = Object.assign(
    { cap: 256 * 1024 * 1024, timeoutMs: 600000, allowedOrigins: [] }, options || {}
  );
  const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs);
  try {
    let response;
    try {
      response = await fetch(url, {
        credentials: "include", redirect: "follow", cache: "no-store", signal: controller.signal,
      });
    } catch (error) {
      return { ok: false, reason: error && error.name === "AbortError" ? "timeout" : "fetch_failed" };
    }
    let finalOrigin = "";
    try { finalOrigin = new URL(response.url).origin; } catch (_) { finalOrigin = ""; }
    const allowed = [location.origin].concat(opts.allowedOrigins || []);
    if (response.url && !allowed.includes(finalOrigin)) {
      controller.abort();
      return { ok: false, reason: "redirected_off_origin", final_url: response.url };
    }
    if (!response.ok) return { ok: false, reason: `http_${response.status}`, final_url: response.url };
    const declared = Number(response.headers.get("content-length") || 0);
    if (declared > opts.cap) {
      controller.abort();
      return { ok: false, reason: "too_large", bytes: declared };
    }
    const type = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
    const chunks = [];
    let total = 0;
    const reader = response.body && response.body.getReader ? response.body.getReader() : null;
    if (!reader) {
      const buffer = new Uint8Array(await response.arrayBuffer());
      if (buffer.length > opts.cap) return { ok: false, reason: "too_large", bytes: buffer.length };
      chunks.push(buffer);
      total = buffer.length;
    } else {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.length;
        if (total > opts.cap) {
          controller.abort();
          return { ok: false, reason: "too_large", bytes: total };
        }
        chunks.push(value);
      }
    }
    if (!total) return { ok: false, reason: "empty" };
    // One contiguous copy, and the chunk list released before the digest: a
    // Blob plus its arrayBuffer held the same 200 MB video three times over.
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    chunks.length = 0;
    const digest = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
    const head = hex(bytes.subarray(0, Math.min(16, bytes.length)));
    const store = (globalThis.__corpusCaptureAttachments = globalThis.__corpusCaptureAttachments || {});
    store[token] = bytes;
    return {
      ok: true, bytes: total, type, sha256: digest, head, final_url: response.url,
      disposition: (response.headers.get("content-disposition") || "").slice(0, 300),
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Resolve an Atypon PDF viewer's explicit download link. Runs in the page.
 * Only the same origin and exact viewer DOI can supply the PDF. Never execute
 * viewer markup or follow its scripts, base element, or unrelated links.
 */
export function pdfDownloadInPage(token, viewerUrl) {
  const bytes = (globalThis.__corpusCaptureAttachments || {})[token];
  if (!bytes || bytes.length > 2 * 1024 * 1024) return null;
  let viewer;
  try { viewer = new URL(viewerUrl); } catch (_) { return null; }
  if (viewer.origin !== location.origin) return null;
  const match = /^\/doi\/epdf\/(10\.[^/]+\/.+)$/i.exec(viewer.pathname);
  if (!match) return null;
  const expectedPath = `/doi/pdf/${match[1]}`;
  const doc = new DOMParser().parseFromString(new TextDecoder().decode(bytes), "text/html");
  for (const anchor of doc.querySelectorAll("a[href]")) {
    try {
      const target = new URL(anchor.getAttribute("href"), viewer.href);
      if (target.origin === viewer.origin && target.pathname === expectedPath
          && !target.username && !target.password) return target.href;
    } catch (_) { /* Ignore malformed page-controlled links. */ }
  }
  return null;
}

/** One base64 slice of a parked attachment. Runs inside the page. */
export async function readAttachmentChunkInPage(token, offset, length) {
  const store = globalThis.__corpusCaptureAttachments || {};
  const held = store[token];
  if (!held) return null;
  const bytes = held.subarray(offset, offset + length);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** Forget a parked attachment. Runs inside the page. */
export function releaseAttachmentInPage(token) {
  const store = globalThis.__corpusCaptureAttachments || {};
  const held = Boolean(store[token]);
  delete store[token];
  return held;
}

/**
 * Fetch a small credentialed TEXT document from the page's origin. Runs inside
 * the page. Used for one thing: the NEJM player's ajax document, which names
 * the JW media id. Bounded, same-origin only, never stored.
 */
export async function fetchTextInPage(url, maxChars, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 30000);
  try {
    const target = new URL(url, document.baseURI);
    if (target.origin !== location.origin) return { ok: false, reason: "off_origin" };
    const response = await fetch(target.href, {
      credentials: "include", redirect: "error", cache: "no-store", signal: controller.signal,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!response.ok) return { ok: false, reason: `http_${response.status}` };
    const text = await response.text();
    return { ok: true, text: text.slice(0, maxChars || 400000) };
  } catch (error) {
    return { ok: false, reason: error && error.name === "AbortError" ? "timeout" : "fetch_failed" };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Extension side.
// ---------------------------------------------------------------------------

export function decodeBase64(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function hexOf(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Do the bytes agree with what the link claimed to be?
 *
 * A publisher answers an unentitled PDF request with its login page, status
 * 200, `text/html`. Storing that as `source.pdf` is worse than storing nothing:
 * it is a file with the right name that every later reader trusts. So the
 * magic bytes decide, not the link, not the status, and not the type header
 * alone -- a CDN that serves an mp3 as `application/octet-stream` is common and
 * still an mp3.
 */
export function classifyAttachmentBytes(kind, type, headHex) {
  const head = String(headHex || "").toLowerCase();
  const mime = String(type || "").toLowerCase();
  const isHtml = mime === "text/html" || mime === "application/xhtml+xml"
    || head.startsWith("3c21") || head.startsWith("3c68746d6c") || head.startsWith("3c485442");
  const isPdf = head.startsWith("25504446");
  const isId3 = head.startsWith("494433");
  const isMpegAudio = /^fff[bf32ae]/.test(head);
  const isFtyp = head.slice(8, 16) === "66747970";
  const isWebm = head.startsWith("1a45dfa3");
  const isOgg = head.startsWith("4f676753");
  const isRiff = head.startsWith("52494646");
  if (kind === "pdf") {
    if (isPdf) return { ok: true };
    return { ok: false, reason: isHtml ? "html_instead_of_pdf" : "not_a_pdf" };
  }
  if (kind === "audio") {
    if (mime.startsWith("audio/") || isId3 || isMpegAudio || isFtyp || isOgg || isRiff) return { ok: true };
    return { ok: false, reason: isHtml ? "html_instead_of_audio" : "not_audio" };
  }
  if (kind === "video") {
    if (mime.startsWith("video/") || isFtyp || isWebm) return { ok: true };
    return { ok: false, reason: isHtml ? "html_instead_of_video" : "not_video" };
  }
  if (isHtml) return { ok: false, reason: "html_instead_of_file" };
  return { ok: true };
}

/** The stored suffix: from the bytes' type first, the link's suffix second. */
export function extensionFor(type, url) {
  const mime = String(type || "").toLowerCase();
  if (ATTACHMENT_EXTENSIONS[mime]) return ATTACHMENT_EXTENSIONS[mime];
  let path = "";
  try { path = new URL(url).pathname; } catch (_) { path = String(url || ""); }
  const found = /(\.[a-z0-9]{1,5})$/i.exec(path);
  const suffix = found ? found[1].toLowerCase() : "";
  return KNOWN_SUFFIXES.has(suffix) ? suffix : ".bin";
}

/**
 * The name an attachment is stored under, beside the capture that owns it.
 *
 * `<stem>--NN-<kind><ext>`: the double dash cannot occur in a capture stem
 * (the slug allows one dash at a time), so a receiver can tell an attachment
 * from a capture by name alone -- and still never trusts the name for
 * identity: the sidecar lists each attachment with its hash.
 */
export function attachmentPayloadName(stem, index, kind, ext) {
  const safeKind = ATTACHMENT_KINDS.includes(kind) ? kind : "other";
  const safeExt = KNOWN_SUFFIXES.has(String(ext || "").toLowerCase()) ? String(ext).toLowerCase() : ".bin";
  return `${stem}--${String(index).padStart(2, "0")}-${safeKind}${safeExt}`;
}

async function inject(tabId, func, args) {
  const [injected] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  if (!injected) throw new Error("page_script_no_result");
  return injected.result;
}

export async function discoverAttachments(tabId, profile) {
  const result = await inject(tabId, discoverAttachmentsInPage, [profile || {}, LIMITS]);
  return result && Array.isArray(result.attachments)
    ? result
    : { attachments: [], overflow: 0, scoped: false };
}

async function pullFromPage(tabId, token, total) {
  const bytes = new Uint8Array(total);
  let offset = 0;
  while (offset < total) {
    const length = Math.min(LIMITS.attachmentChunkBytes, total - offset);
    const chunk = await inject(tabId, readAttachmentChunkInPage, [token, offset, length]);
    if (typeof chunk !== "string") throw new Error("attachment_chunk_lost");
    const decoded = decodeBase64(chunk);
    if (decoded.length !== length) throw new Error("attachment_chunk_short");
    bytes.set(decoded, offset);
    offset += length;
  }
  return bytes;
}

async function fetchInExtension(url, cap, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response;
    try {
      response = await fetch(url, {
        credentials: "omit", redirect: "follow", cache: "no-store", signal: controller.signal,
      });
    } catch (error) {
      return { ok: false, reason: error && error.name === "AbortError" ? "timeout" : "fetch_failed" };
    }
    if (!response.ok) return { ok: false, reason: `http_${response.status}`, final_url: response.url };
    const declared = Number(response.headers.get("content-length") || 0);
    if (declared > cap) {
      controller.abort();
      return { ok: false, reason: "too_large", bytes: declared };
    }
    const type = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
    const chunks = [];
    let total = 0;
    const reader = response.body && response.body.getReader ? response.body.getReader() : null;
    if (!reader) {
      const buffer = new Uint8Array(await response.arrayBuffer());
      if (buffer.length > cap) return { ok: false, reason: "too_large", bytes: buffer.length };
      chunks.push(buffer);
      total = buffer.length;
    } else {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.length;
        if (total > cap) {
          controller.abort();
          return { ok: false, reason: "too_large", bytes: total };
        }
        chunks.push(value);
      }
    }
    if (!total) return { ok: false, reason: "empty" };
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    const digest = hexOf(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
    return {
      ok: true, bytes: total, type, sha256: digest, head: hexOf(bytes.subarray(0, 16)),
      final_url: response.url, data: bytes,
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Pick one mp4 rendition from a JW Platform media document. */
export function pickJwRendition(media, maxWidth = 1080) {
  const items = (media && Array.isArray(media.playlist)) ? media.playlist : [];
  const sources = [];
  for (const item of items) {
    for (const source of (item && Array.isArray(item.sources)) ? item.sources : []) {
      const file = String((source && source.file) || "");
      const type = String((source && source.type) || "").toLowerCase();
      if (!/^https:/i.test(file)) continue;
      if (type === "video/mp4" || /\.mp4(?:[?#]|$)/i.test(file)) {
        sources.push({ file, width: Number(source.width || 0) });
      }
    }
    if (sources.length) break;
  }
  if (!sources.length) return null;
  const within = sources.filter((source) => source.width && source.width <= maxWidth);
  const pool = within.length ? within : sources;
  pool.sort((a, b) => b.width - a.width);
  return pool[0].file;
}

/**
 * The JW media id an NEJM player document names, or "" for a text-only card.
 *
 * Measured 2026-09-24 on NEJMdo008691 (NEJM Case 27-2026): the ajax document is
 * JSON, `{"hasAccess":true,"html":"<media-player-app ... mediaID=\\"BOV6WXtL\\" ...>"}`,
 * so the attribute's quotes arrive backslash-escaped and `mediaID=["']` never
 * matched -- every NEJM video failed as not_a_video. The JSON is decoded first;
 * a document with no `html` (a Research Summary card) is not a video. The
 * attribute is matched case-insensitively, because serialized markup lowercases it.
 */
export function playerMediaId(text) {
  let body = String(text || "").trim();
  if (body.startsWith("{")) {
    try {
      const payload = JSON.parse(body);
      if (payload && typeof payload === "object") {
        if (!payload.html) return "";
        body = String(payload.html);
      }
    } catch (_) {
      // Not JSON after all: read it as markup.
    }
  }
  const found = /media_id=([A-Za-z0-9]{8})\b|mediaid=\\?["']([A-Za-z0-9]{8})\\?["']|data-media-id=\\?["']([A-Za-z0-9]{8})\\?["']/i.exec(body);
  return found ? (found[1] || found[2] || found[3]) : "";
}

async function resolveVideo(tabId, candidate, { pageUrl, profile }) {
  let mediaId = candidate.media_id || "";
  if (candidate.resolver === "nejm_do") {
    // The player document is credentialed and same-origin; the page fetches it.
    const document = await inject(tabId, fetchTextInPage, [candidate.url, 400000, 30000]);
    if (!document || !document.ok) return { ok: false, reason: `player_document_${(document && document.reason) || "unavailable"}` };
    mediaId = playerMediaId(document.text);
    if (!mediaId) return { ok: false, reason: "not_a_video" };
  }
  if (!/^[A-Za-z0-9]{8}$/.test(mediaId)) return { ok: false, reason: "media_id_missing" };
  const template = String(((profile && profile.media_metadata_endpoints) || {}).jwplayer || "");
  if (!/^[A-Za-z0-9.-]+\/\S*\{media_id\}/.test(template)) {
    return { ok: false, reason: "no_media_resolver" };
  }
  const metadataUrl = "https:" + "//" + template.split("{media_id}").join(mediaId);
  const metadataDecision = attachmentDecision(metadataUrl, { pageUrl, profile });
  if (!metadataDecision.allowed) return { ok: false, reason: `metadata_${metadataDecision.reason}` };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  let media;
  try {
    const response = await fetch(metadataUrl, {
      credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) return { ok: false, reason: `metadata_http_${response.status}` };
    media = await response.json();
  } catch (_) {
    return { ok: false, reason: "metadata_unavailable" };
  } finally {
    clearTimeout(timer);
  }
  const file = pickJwRendition(media);
  if (!file) return { ok: false, reason: "no_mp4_rendition" };
  const decision = attachmentDecision(file, { pageUrl, profile });
  if (!decision.allowed) return { ok: false, reason: `rendition_${decision.reason}` };
  return { ok: true, url: decision.url, media_id: mediaId, title: String((media && media.title) || "").slice(0, 200) };
}

/**
 * Fetch every discovered attachment and hand each one to `sink`.
 *
 * `sink(meta, bytes)` stores the attachment -- online it uploads to the
 * receiver, offline it downloads beside the capture -- and returns
 * `{ payload_name }`. The result is the attachment manifest: one row per
 * candidate, `captured` with its hash and stored name, or `failed` with the
 * reason. A candidate that fails never stops the others: the manifest is what
 * makes a partial capture honest, and a partial capture with a manifest is
 * worth more than an aborted one.
 */
export async function collectAttachments({
  tabId, pageUrl, profile, discovered, sink, onProgress, rows = [],
}) {
  // `rows` may be the caller's own array: the progress page reads it while the
  // collection is still running, to close a held capture if the tab goes away.
  let totalBytes = 0;
  const allowedOrigins = [];
  // What is already held, three ways: a resolved media id, a fetched URL, and
  // the bytes themselves. The first two are checked before the network is
  // touched, which is what keeps a 200 MB video from being fetched twice.
  const byMediaId = new Map();
  const byUrl = new Map();
  const bySha = new Map();
  const duplicateOf = (row, index, extra = {}) =>
    ({ ...row, ...extra, status: "duplicate", duplicate_of: index });
  for (const [position, candidate] of (discovered || []).entries()) {
    const row = {
      index: candidate.index || position + 1,
      kind: ATTACHMENT_KINDS.includes(candidate.kind) ? candidate.kind : "other",
      url: candidate.url,
      label: String(candidate.label || "").slice(0, 200),
      source: String(candidate.source || "").slice(0, 120),
    };
    if (candidate.nejmdo) row.nejmdo = candidate.nejmdo;
    if (onProgress) onProgress(row, "fetching", position, discovered.length);
    const token = `att-${crypto.randomUUID()}`;
    try {
      if (candidate.resolver === "hls") {
        rows.push({ ...row, status: "failed", reason: "hls_not_supported" });
        continue;
      }
      let fetched;
      if (candidate.resolver === "jwplayer" || candidate.resolver === "nejm_do") {
        const resolved = await resolveVideo(tabId, candidate, { pageUrl, profile });
        if (!resolved.ok) {
          rows.push({ ...row, status: "failed", reason: resolved.reason });
          continue;
        }
        row.url = resolved.url;
        row.media_id = resolved.media_id;
        if (resolved.title && !row.label) row.label = resolved.title;
        const seenIndex = byMediaId.get(resolved.media_id) || byUrl.get(resolved.url);
        if (seenIndex) {
          rows.push(duplicateOf(row, seenIndex));
          continue;
        }
        fetched = await fetchInExtension(resolved.url, LIMITS.maxAttachmentBytes, LIMITS.attachmentTimeoutMs);
      } else {
        const decision = attachmentDecision(candidate.url, { pageUrl, profile });
        if (!decision.allowed) {
          rows.push({ ...row, status: "failed", reason: decision.reason });
          continue;
        }
        row.url = decision.url;
        if (byUrl.has(decision.url)) {
          rows.push(duplicateOf(row, byUrl.get(decision.url)));
          continue;
        }
        fetched = decision.where === "page"
          ? await inject(tabId, fetchAttachmentInPage, [decision.url, token, {
            cap: LIMITS.maxAttachmentBytes, timeoutMs: LIMITS.attachmentTimeoutMs, allowedOrigins,
          }])
          : await fetchInExtension(decision.url, LIMITS.maxAttachmentBytes, LIMITS.attachmentTimeoutMs);
      }
      if (!fetched || !fetched.ok) {
        rows.push({ ...row, status: "failed", reason: (fetched && fetched.reason) || "fetch_failed", ...(fetched && fetched.bytes ? { bytes: fetched.bytes } : {}) });
        continue;
      }
      // /doi/epdf is a reader shell on some publishers. Resolve only its
      // explicit same-DOI download link, then apply the usual policy and byte
      // checks again. A login/error HTML page remains a failed PDF.
      if (row.kind === "pdf" && fetched.type === "text/html" && !fetched.data) {
        const download = await inject(tabId, pdfDownloadInPage, [token, fetched.final_url || row.url]);
        if (download) {
          const decision = attachmentDecision(download, { pageUrl, profile });
          if (decision.allowed && decision.where === "page") {
            fetched = await inject(tabId, fetchAttachmentInPage, [decision.url, token, {
              cap: LIMITS.maxAttachmentBytes, timeoutMs: LIMITS.attachmentTimeoutMs, allowedOrigins,
            }]);
            if (!fetched || !fetched.ok) {
              rows.push({ ...row, status: "failed", reason: fetched?.reason || "fetch_failed" });
              continue;
            }
          }
        }
      }
      const verdict = classifyAttachmentBytes(row.kind, fetched.type, fetched.head);
      if (!verdict.ok) {
        rows.push({ ...row, status: "failed", reason: verdict.reason, mime: fetched.type || null, bytes: fetched.bytes });
        continue;
      }
      if (bySha.has(fetched.sha256)) {
        rows.push(duplicateOf(row, bySha.get(fetched.sha256), {
          sha256: fetched.sha256, bytes: fetched.bytes,
        }));
        continue;
      }
      if (totalBytes + fetched.bytes > LIMITS.maxAttachmentTotalBytes) {
        rows.push({ ...row, status: "failed", reason: "total_budget_exceeded", bytes: fetched.bytes });
        continue;
      }
      const bytes = fetched.data || await pullFromPage(tabId, token, fetched.bytes);
      if (!fetched.data) {
        // The page's digest is what the receiver checks. Recompute here as
        // well: a chunk that decoded to the right LENGTH is not yet the right
        // bytes, and the transfer is the one step nobody else verifies.
        const digest = hexOf(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
        if (digest !== fetched.sha256) throw new Error("attachment_transfer_corrupt");
      }
      totalBytes += bytes.length;
      const meta = {
        ...row,
        sha256: fetched.sha256,
        bytes: fetched.bytes,
        mime: fetched.type || "application/octet-stream",
        ext: extensionFor(fetched.type, row.url),
        final_url: fetched.final_url || row.url,
      };
      if (onProgress) onProgress(meta, "storing", position, discovered.length);
      const stored = await sink(meta, bytes);
      rows.push({ ...meta, status: "captured", payload_name: String((stored && stored.payload_name) || "") });
      bySha.set(meta.sha256, row.index);
      byUrl.set(row.url, row.index);
      if (meta.final_url) byUrl.set(meta.final_url, row.index);
      if (row.media_id) byMediaId.set(row.media_id, row.index);
    } catch (error) {
      rows.push({ ...row, status: "failed", reason: `error:${String((error && error.message) || error)}`.slice(0, 160) });
    } finally {
      try { await inject(tabId, releaseAttachmentInPage, [token]); } catch (_) { /* the page may have gone */ }
    }
  }
  return rows;
}

/** Every candidate ended as a file the bundle will hold. */
export function allCaptured(rows) {
  return (rows || []).every((row) => row.status === "captured" || row.status === "duplicate");
}

/** The manifest rows a sidecar carries: no bytes, no extension bookkeeping. */
export function manifestRows(rows) {
  return (rows || []).map((row) => {
    const out = {
      index: row.index, kind: row.kind, url: row.url, label: row.label || "",
      source: row.source || "", status: row.status,
    };
    if (row.status === "captured") {
      out.payload_name = row.payload_name;
      out.sha256 = row.sha256;
      out.bytes = row.bytes;
      out.mime = row.mime;
      if (row.final_url && row.final_url !== row.url) out.final_url = row.final_url;
    } else if (row.status === "duplicate") {
      out.duplicate_of = row.duplicate_of;
      if (row.sha256) out.sha256 = row.sha256;
      if (row.bytes) out.bytes = row.bytes;
    } else {
      out.reason = row.reason || "unknown";
      if (row.bytes) out.bytes = row.bytes;
      if (row.mime) out.mime = row.mime;
    }
    if (row.media_id) out.media_id = row.media_id;
    if (row.nejmdo) out.nejmdo = row.nejmdo;
    return out;
  });
}
