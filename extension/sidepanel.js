"use strict";

// The side panel. It follows the active tab and is one of two things:
//
// - On an article: a preview of the page's identity to correct, then save.
//   Opening the panel (or switching tabs) reads what the page declares -- its
//   DOI and bibliographic <meta> -- and asks the receiver for the record it
//   holds for that DOI. The reader corrects whatever is wrong and presses one
//   button; the capture then runs here, with the previewed values sent as the
//   `reader_review` observation when the capture is finalized.
// - On the receiver's own reading page: the work that page shows, read-only
//   (bundle-view.js). Nothing is captured there; the work is already held.
//
// Every line on screen has to earn its place: a value, or a state the reader
// acts on. Labels a value already explains, and notes about the normal case,
// are not shown (operator request, 2026-09-18).

import { safeReaderUrl } from "./net-policy.js";
import {
  EXTENSION_VERSION,
  SETTLED_STATES as SETTLED,
  finalizeCapture,
  listReceipts,
  lookupIdentity,
  mergeReceipt,
  normalizeDoi,
  probeTab,
  profileForUrl,
  profileRegistry,
  readReceipt,
  receiptsBeside,
  rememberReceipt,
  reviewEnabled,
  settings,
} from "./capture.js";
import {
  BUNDLE_DECLARATION_KEYS,
  BUNDLE_SETTLE_MS,
  BUNDLE_WAIT_MS,
  authorLine,
  awaitBundleDeclaration,
  bundleCard,
  declaresWork,
  isReceiverPage,
} from "./bundle-view.js";
import { CAPTURE_REQUEST_KEY, isCapturableUrl } from "./launch.js";
import { LIMITS } from "./limits.js";
import {
  DOI_SOURCE_LABELS,
  FIELD_LABELS,
  METADATA_FIELDS,
  buildReaderReview,
  cleanAuthors,
  mergeProposal,
  pageMetadata,
} from "./review.js";
import { KIND_LABEL, runCapture } from "./runner.js";

const REQUEST_MAX_AGE_MS = 60 * 1000;
//: How long a card stays, dimmed, after the reading page withdraws its
//: declarations: a page moving to the next work clears them first.
const STALE_MS = 1500;
//: A DOI the receiver is still resolving is asked about again, a few times.
const LOOKUP_ATTEMPTS = 4;
const LOOKUP_RETRY_MS = 3000;
const RECEIPTS_SHOWN = 5;
const STATE_LABEL = {
  held: "上傳附件中",
  received: "等待入庫",
  queued: "排隊中",
  claimed: "建立中",
  admitted: "已入庫",
  duplicate: "已併入既有",
  supplement_attached: "已掛到母文章",
  needs_identity_review: "待確認身分",
  needs_proxy: "需授權管道",
  unsupported: "不支援",
  error: "失敗",
  unknown: "狀態不明",
  downloaded: "已離線存檔",
};
const BAD_STATES = new Set(["needs_identity_review", "needs_proxy", "unsupported", "error", "unknown"]);
const REVIEW_LABEL = { confirmed: "身分已確認", corrected: "身分已修正" };
const ROW_MARK = { captured: "✓", duplicate: "重複", failed: "缺" };
const SOURCE_LABEL = {
  page: "頁面", registry: "登記", corpus: "corpus",
  crossref: "Crossref", openalex: "OpenAlex", receiver: "接收端",
};

const $ = (selector) => document.querySelector(selector);
const params = new URLSearchParams(location.search);
// A fixed target tab, for a panel opened as an ordinary tab (tests, fallback).
const pinnedTab = Number.parseInt(params.get("tab") || "", 10);

let windowId = null;
let apiOrigin = "";
let running = false;
let inFlight = null;
let preview = null;
// The tab and address being read right now. Opening the panel fires tab
// events of its own; without this each one read the page and asked the
// receiver again, and a late one wiped what the reader had started typing.
let reading = null;
let generation = 0;
let pollTimer = null;
// The reading page being followed, and what it declared last.
let follow = null;
let staleTimer = 0;
// The receiver's answer per DOI, so paging back and forth in the reader asks once.
const identities = new Map();

// ---------------------------------------------------------------- the tab --

async function targetTab() {
  if (Number.isInteger(pinnedTab)) return chrome.tabs.get(pinnedTab).catch(() => null);
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  return tab || null;
}

async function receiverBase() {
  try {
    apiOrigin = (await settings()).apiBase;
  } catch (_) {
    apiOrigin = "";
  }
  return apiOrigin;
}

async function showBadge(tab) {
  const badge = $("#profile");
  let status = "supported";
  let text = "";
  try {
    const registry = await profileRegistry();
    if (!registry) throw new Error("no_registry");
    const profile = profileForUrl(registry, tab.url);
    status = profile.status || "generic";
    if (status === "unsupported") text = `未支援${profile.reason ? `：${profile.reason}` : ""}`;
    else if (status !== "supported") text = "通用模式";
  } catch (_) {
    status = "generic";
    text = "設定檔未載入，通用模式";
  }
  // A site with its own profile is the normal case and says nothing.
  badge.textContent = text;
  badge.className = `badge ${status}`;
  badge.hidden = !text;
}

function pageNote(text) {
  $("#page-note").textContent = text || "";
  $("#page-note").hidden = !text;
}

// Which of the panel's two jobs is on screen; the stylesheet hides the rest.
function setMode(mode) {
  document.body.dataset.mode = mode;
}

function leaveCapture() {
  generation += 1;
  preview = null;
  reading = null;
  $("#review").hidden = true;
  $("#profile").hidden = true;
}

function leaveReader() {
  follow = null;
  clearTimeout(staleTimer);
  $("#bundle").hidden = true;
}

async function showTab() {
  const tab = await targetTab();
  if (!tab || !isCapturableUrl(tab.url)) {
    leaveCapture();
    leaveReader();
    setMode("idle");
    pageNote("非文章頁");
    await renderReceipts();
    return;
  }
  if (isReceiverPage(tab.url, await receiverBase())) {
    leaveCapture();
    setMode("reader");
    followBundle(tab);
    return;
  }
  leaveReader();
  setMode("capture");
  pageNote("");
  await showBadge(tab);
  await ensurePreview(tab);
  await renderReceipts();
}

// The same page keeps what the reader already typed, and a page being read is
// waited for, not read again; a new page is read anew.
async function ensurePreview(tab) {
  const same = (entry) => Boolean(entry) && entry.tabId === tab.id && entry.url === tab.url;
  if (same(preview)) return undefined;
  if (same(reading)) return reading.done;
  return loadPreview(tab);
}

// ------------------------------------------------------ the reading page --

/**
 * What the reading page declares, or -- when that is still `seen` -- its next
 * change. The page is watched from inside (a MutationObserver on its <head>),
 * so a click in the reader reaches the panel without the panel polling.
 */
async function readBundleDeclaration(tabId, seen, waitMs) {
  const [injected] = await chrome.scripting.executeScript({
    target: { tabId },
    func: awaitBundleDeclaration,
    args: [BUNDLE_DECLARATION_KEYS, LIMITS, seen, waitMs, BUNDLE_SETTLE_MS],
  });
  const declared = injected && injected.result;
  return declared && typeof declared === "object" ? declared : {};
}

const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

// One watch per tab: the reader changes its address on every click, and each
// change is already seen from inside the page.
function followBundle(tab) {
  if (follow && follow.tabId === tab.id) return;
  const mine = { tabId: tab.id, seen: null };
  follow = mine;
  pageNote("");
  (async () => {
    let failures = 0;
    while (follow === mine) {
      let declared;
      try {
        declared = await readBundleDeclaration(tab.id, mine.seen, mine.seen === null ? 0 : BUNDLE_WAIT_MS);
        failures = 0;
      } catch (_) {
        // A reload or a navigation ends the wait with an error; the next read
        // finds the new document.
        if (follow !== mine) return;
        failures += 1;
        if (failures > 20) {
          leaveReader();
          pageNote("讀不到這個 Reader 分頁；重新整理後再試。");
          return;
        }
        await pause(Math.min(3000, 150 * failures));
        continue;
      }
      if (follow !== mine) return;
      const key = JSON.stringify(declared);
      if (key === mine.seen) continue;
      const initial = mine.seen === null;
      mine.seen = key;
      showDeclared(declared, mine, key, initial);
    }
  })();
}

function rememberIdentity(doi, identity) {
  identities.delete(doi);
  identities.set(doi, identity);
  while (identities.size > 100) identities.delete(identities.keys().next().value);
}

async function showDeclared(declared, mine, key, initial) {
  const live = () => follow === mine && mine.seen === key;
  if (!declaresWork(declared)) {
    if (initial || $("#bundle").hidden) {
      renderBundle(null);
      return;
    }
    // Moving to the next work clears the page's declarations first; dim the
    // card instead of blanking it, and blank it only if nothing follows.
    $("#bundle").classList.add("stale");
    clearTimeout(staleTimer);
    staleTimer = setTimeout(() => { if (live()) renderBundle(null); }, STALE_MS);
    return;
  }
  clearTimeout(staleTimer);
  const doi = normalizeDoi((declared.doi || [])[0]);
  renderBundle(bundleCard(declared, (doi && identities.get(doi)) || null));
  if (!doi || identities.has(doi)) return;
  for (let attempt = 1; attempt <= LOOKUP_ATTEMPTS; attempt += 1) {
    let identity = null;
    try {
      identity = await lookupIdentity(doi);
    } catch (_) {
      return;
    }
    if (!identity) return;
    if (identity.metadata_status !== "in_progress") {
      rememberIdentity(doi, identity);
      if (live()) renderBundle(bundleCard(declared, identity));
      return;
    }
    // The receiver is still asking the registries about this DOI.
    await pause(LOOKUP_RETRY_MS);
    if (!live()) return;
  }
}

function link(href, text, title = "") {
  const node = document.createElement("a");
  node.href = href;
  node.target = "_blank";
  node.rel = "noreferrer noopener";
  node.textContent = text;
  if (title) node.title = title;
  return node;
}

function renderAuthors(authors) {
  const box = $("#bundle-authors");
  const line = authorLine(authors);
  if (!line.total) {
    box.replaceChildren();
    return;
  }
  if (!line.folded) {
    box.replaceChildren(document.createTextNode(line.short));
    return;
  }
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${line.short}（${line.total}）`;
  summary.title = "展開全部作者";
  const all = document.createElement("p");
  all.textContent = cleanAuthors(authors).join(", ");
  all.title = "收合";
  all.addEventListener("click", () => { details.open = false; });
  details.append(summary, all);
  box.replaceChildren(details);
}

function chip(text, className = "chip") {
  const node = document.createElement("span");
  node.className = className;
  node.textContent = text;
  return node;
}

function renderBundle(card) {
  clearTimeout(staleTimer);
  const box = $("#bundle");
  box.classList.remove("stale");
  if (!card) {
    box.hidden = true;
    pageNote("未顯示 bundle");
    return;
  }
  pageNote("");
  $("#bundle-title").textContent = card.title || card.doi;
  renderAuthors(card.authors);
  // The date and the volume, issue and pages read as one token; only the
  // journal's name may wrap.
  const where = $("#bundle-source");
  where.replaceChildren();
  if (card.container) where.append(card.container);
  if (card.container && card.when) where.append(" · ");
  if (card.when) where.append(Object.assign(document.createElement("span"), { className: "nowrap", textContent: card.when }));
  const ids = [];
  if (card.doi) ids.push(link(`https://doi.org/${encodeURI(card.doi)}`, card.doi, "在 doi.org 開啟"));
  if (card.pmid) {
    ids.push(link(`https://pubmed.ncbi.nlm.nih.gov/${card.pmid}/`, `PMID ${card.pmid}`, "在 PubMed 開啟"));
  }
  $("#bundle-ids").replaceChildren(...ids);
  $("#bundle-tags").replaceChildren(
    ...(card.type ? [chip(card.type, "chip type")] : []),
    ...card.tags.map((tag) => chip(tag)),
  );
  box.hidden = false;
}

// -------------------------------------------------------------- the preview --

function fieldControl(field) {
  const multiline = field === "title" || field === "authors";
  const node = document.createElement(multiline ? "textarea" : "input");
  if (!multiline) node.type = "text";
  node.id = `meta-${field}`;
  node.name = field;
  node.autocomplete = "off";
  if (multiline) node.rows = field === "authors" ? 3 : 2;
  if (field === "authors") node.placeholder = "一行一位";
  return node;
}

function buildFields() {
  $("#fields").replaceChildren(...METADATA_FIELDS.map((field) => {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.dataset.field = field;
    const label = document.createElement("label");
    label.htmlFor = `meta-${field}`;
    label.textContent = FIELD_LABELS[field];
    const node = fieldControl(field);
    node.addEventListener("input", () => {
      if (preview) preview.touched.add(field);
      markChanged(field);
    });
    const alt = document.createElement("p");
    alt.className = "alt";
    alt.hidden = true;
    wrap.append(label, node, alt);
    return wrap;
  }));
}

const control = (field) => $(`#meta-${field}`);
const fieldWrap = (field) => document.querySelector(`.field[data-field="${field}"]`);

function readValue(field) {
  const value = control(field).value;
  return field === "authors" ? cleanAuthors(value) : value.replace(/\s+/g, " ").trim();
}

function writeValue(field, value) {
  control(field).value = field === "authors" ? (value || []).join("\n") : (value || "");
}

function readForm() {
  const metadata = {};
  for (const field of METADATA_FIELDS) metadata[field] = readValue(field);
  return { doi: $("#doi").value, metadata };
}

function markChanged(field) {
  if (!preview) return;
  const baseline = preview.baseline.metadata[field];
  fieldWrap(field).classList.toggle("changed", JSON.stringify(readValue(field)) !== JSON.stringify(baseline));
}

function showAlternative(field, alternative) {
  const alt = fieldWrap(field).querySelector(".alt");
  if (!alternative) {
    alt.hidden = true;
    alt.replaceChildren();
    return;
  }
  const shown = Array.isArray(alternative.value) ? alternative.value.join("；") : alternative.value;
  const text = document.createElement("span");
  text.textContent = `${SOURCE_LABEL[alternative.source] || alternative.source}：${shown}`;
  const use = document.createElement("button");
  use.type = "button";
  use.className = "secondary";
  use.textContent = "改用";
  use.addEventListener("click", () => {
    writeValue(field, alternative.value);
    if (preview) preview.touched.add(field);
    markChanged(field);
  });
  alt.replaceChildren(text, use);
  alt.hidden = false;
}

function applyProposal(proposal, { overwriteTouched = false } = {}) {
  for (const field of METADATA_FIELDS) {
    const touched = preview && preview.touched.has(field);
    const offered = proposal.values[field];
    if (!touched || overwriteTouched) {
      writeValue(field, offered);
      showAlternative(field, proposal.alternatives[field]);
    } else if (JSON.stringify(readValue(field)) !== JSON.stringify(offered)
               && (Array.isArray(offered) ? offered.length : offered)) {
      // Never overwrite what the reader typed; offer the new value beside it.
      showAlternative(field, { source: proposal.sources[field] || "receiver", value: offered });
    }
    markChanged(field);
  }
}

function setDoiLink(doi) {
  const anchor = $("#doi-link");
  const normalized = normalizeDoi(doi);
  anchor.hidden = !normalized;
  if (normalized) anchor.href = `https://doi.org/${encodeURI(normalized)}`;
}

function metaStatus(text) {
  $("#meta-status").textContent = text || "";
  $("#meta-status").hidden = !text;
}

function showKnown(identity) {
  const known = identity && identity.known;
  const note = $("#known");
  note.replaceChildren();
  note.hidden = !known;
  if (!known) return;
  note.append(document.createTextNode("已在庫（儲存將併入）"));
  const reader = safeReaderUrl(known.reader_url, apiOrigin);
  if (reader) note.append(document.createTextNode(" "), link(reader, "開啟", "在 Reader 開啟"));
}

const LOOKUP_NOTE = {
  in_progress: "登記資料查詢中，稍後按 ↻",
  unavailable: "僅頁面資料",
  error: "登記資料查詢失敗",
  unresolved: "查無登記資料",
};

async function lookup(doi) {
  if (!preview) return;
  const mine = preview.generation;
  showKnown(null);
  // A page with no DOI already says so beside the DOI field.
  if (!doi) {
    metaStatus("");
    return;
  }
  metaStatus("查詢中…");
  let identity = null;
  try {
    identity = await lookupIdentity(doi);
  } catch (error) {
    if (!preview || preview.generation !== mine) return;
    metaStatus(`${LOOKUP_NOTE.error}（${error.message}）`);
    return;
  }
  if (!preview || preview.generation !== mine) return;
  if (!identity) {
    metaStatus(LOOKUP_NOTE.unavailable);
    return;
  }
  const proposal = mergeProposal(preview.pageMeta, identity);
  // The record for the page's own DOI is what a correction is measured
  // against, however often it is asked for (a retry after "in progress"
  // included), and it never overwrites what the reader typed. A DOI the reader
  // typed is another work: its record replaces the form, and that replacement
  // is itself the correction.
  const own = (normalizeDoi(doi) || null) === (normalizeDoi(preview.detectedDoi) || null);
  if (own) preview.baseline = { doi: preview.detectedDoi, metadata: proposal.values };
  applyProposal(proposal, { overwriteTouched: !own });
  showKnown(identity);
  // Loaded is the normal case: the fields say it. Disagreements sit under them.
  metaStatus(identity.metadata ? "" : LOOKUP_NOTE[identity.metadata_status] || LOOKUP_NOTE.unresolved);
}

async function loadPreview(tab) {
  generation += 1;
  const mine = generation;
  const done = readPreview(tab, mine);
  reading = { tabId: tab.id, url: tab.url, done };
  try {
    await done;
  } finally {
    if (mine === generation) reading = null;
  }
}

async function readPreview(tab, mine) {
  metaStatus("讀取中…");
  let page;
  try {
    page = await probeTab(tab.id);
  } catch (error) {
    if (mine !== generation) return;
    preview = null;
    $("#review").hidden = true;
    pageNote(`讀不到這個頁面（${error.message}）；重新整理後再試。`);
    await renderReceipts();
    return;
  }
  if (mine !== generation) return;
  const pageMeta = pageMetadata(page);
  const proposal = mergeProposal(pageMeta, null);
  preview = {
    tabId: tab.id, url: tab.url, generation: mine, touched: new Set(),
    detectedDoi: page.doi, pageUrls: [page.url, page.final_url], pageMeta,
    baseline: { doi: page.doi, metadata: proposal.values },
  };
  // The previous page's settled captures leave with it, before the lookup.
  renderReceipts();
  const input = $("#doi");
  input.value = page.doi || "";
  input.setCustomValidity("");
  const hint = DOI_SOURCE_LABELS[page.doi_source] ?? DOI_SOURCE_LABELS.none;
  $("#doi-hint").textContent = hint;
  $("#doi-hint").hidden = !hint;
  setDoiLink(page.doi);
  applyProposal(proposal, { overwriteTouched: true });
  $("#review").hidden = false;
  $("#save").disabled = running;
  pageNote("");
  await lookup(page.doi);
}

$("#doi").addEventListener("input", () => {
  $("#doi").setCustomValidity("");
  setDoiLink($("#doi").value);
});

$("#doi-lookup").addEventListener("click", () => {
  const input = $("#doi");
  const doi = normalizeDoi(input.value);
  if (input.value.trim() && !doi) {
    input.setCustomValidity("DOI 格式為 10.xxxx/…");
    input.reportValidity();
    return;
  }
  lookup(doi);
});

function currentReview() {
  return buildReaderReview({
    detectedDoi: preview.detectedDoi, proposed: preview.baseline, final: readForm(),
  });
}

$("#review").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!preview || running) return;
  let review;
  try {
    review = currentReview();
  } catch (_) {
    $("#doi").setCustomValidity("DOI 格式為 10.xxxx/…；沒有請留空");
    $("#doi").reportValidity();
    return;
  }
  startCapture(preview.tabId, review);
});

// ------------------------------------------------------------------- saving --

function renderRows(rows) {
  $("#rows").replaceChildren(...rows.map((row) => {
    const item = document.createElement("li");
    item.className = row.status || "";
    const kind = KIND_LABEL[row.kind] || row.kind;
    // "PDF Full text PDF": a link text that repeats the kind says nothing more.
    const said = row.label && !String(row.label).toLowerCase().includes(String(kind).toLowerCase());
    const label = said ? ` ${String(row.label).slice(0, 40)}` : "";
    const size = row.bytes ? ` ${(row.bytes / 1024 / 1024).toFixed(1)} MB` : "";
    item.textContent = `${kind}${label}${size} `
      + `${ROW_MARK[row.status] || row.status}${row.status === "failed" ? `：${row.reason}` : ""}`;
    return item;
  }));
}

function setBusy(busy) {
  running = busy;
  for (const node of $("#review").querySelectorAll("input, textarea, button")) node.disabled = busy;
}

async function startCapture(tabId, review) {
  if (running) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !isCapturableUrl(tab.url)) return;
  // The receiver's own pages are where saved work is read, not a source of it.
  if (isReceiverPage(tab.url, await receiverBase())) return;
  setBusy(true);
  $("#run").hidden = false;
  $("#rows").replaceChildren();
  $("#result").textContent = "";
  $("#result").className = "";
  $("#status").textContent = "準備中…";
  try {
    const sendReview = (await reviewEnabled()) ? review : null;
    const outcome = await runCapture(tabId, {
      onStatus: (text) => { $("#status").textContent = text; },
      onRow: (rows) => renderRows(rows),
      onHeld: (state) => { inFlight = { ...state, uploading: true, review: sendReview }; },
      // The review is already made: the preview is what the reader confirmed.
      review: sendReview ? async ({ uploads }) => {
        uploads.then(() => { if (inFlight) inFlight.uploading = false; }).catch(() => {});
        return sendReview;
      } : null,
    });
    renderRows(outcome.rows);
    const lines = [
      outcome.review ? REVIEW_LABEL[outcome.review.decision] : "",
      outcome.summary,
      outcome.note,
    ].filter(Boolean);
    $("#result").textContent = lines.join("\n");
    $("#result").className = outcome.complete ? "ok" : "error";
    $("#status").textContent = outcome.mode === "online"
      ? "已送出" : `已離線存檔：Downloads/corpus-capture/${outcome.stem}`;
    await chrome.runtime.sendMessage({ type: "corpus-capture-watch" }).catch(() => {});
  } catch (error) {
    if (inFlight && inFlight.uploading) {
      try {
        await finalizeCapture(inFlight.receiptId, inFlight.rows, {
          complete: false, discovered: inFlight.discovered, overflow: inFlight.overflow,
          review: inFlight.review,
        }, { config: inFlight.config });
      } catch (_) { /* the receiver's stale-hold sweep is the backstop */ }
    }
    $("#status").textContent = "失敗";
    $("#result").textContent = String((error && error.message) || error);
    $("#result").className = "error";
  } finally {
    inFlight = null;
    setBusy(false);
    await refreshReceipts();
    // Tab switches were held while the capture ran.
    await showTab();
  }
}

// --------------------------------------------------------------- receipts --

function receiptItem(row) {
  const item = document.createElement("li");
  item.className = "receipt";
  const state = document.createElement("span");
  state.className = `state${BAD_STATES.has(row.state) ? " bad" : ""}`;
  state.textContent = STATE_LABEL[row.state] || row.state || "";
  const title = row.title || row.url || row.receipt_id;
  const reader = safeReaderUrl(row.reader_url, apiOrigin);
  const what = reader ? link(reader, title) : document.createElement("span");
  if (!reader) what.textContent = title;
  what.classList.add("what");
  what.title = [title, row.doi ? `DOI ${row.doi}` : "", row.attachments_summary]
    .filter(Boolean).join("\n");
  item.append(state, what);
  return item;
}

// Only the receipts that say something beside the page on screen: its own, and
// any still in flight. Yesterday's admitted article is not news on today's page
// (operator request, 2026-09-19).
async function renderReceipts() {
  const rows = await listReceipts();
  // Read after the wait, so the last call to finish shows the page now on screen.
  const page = preview ? { doi: preview.detectedDoi, urls: preview.pageUrls } : null;
  $("#receipts").replaceChildren(...receiptsBeside(rows, page).slice(0, RECEIPTS_SHOWN).map(receiptItem));
  return rows;
}

async function refreshReceipts() {
  await receiverBase();
  for (const row of (await listReceipts()).filter((r) => r.receipt_id && !SETTLED.has(r.state))) {
    try {
      await rememberReceipt(mergeReceipt(row, await readReceipt(row.receipt_id)));
    } catch (_) { /* a failed poll says nothing about the capture */ }
  }
  const rows = await renderReceipts();
  const waiting = rows.some((row) => row.receipt_id && !SETTLED.has(row.state));
  if (waiting && !pollTimer) pollTimer = setInterval(refreshReceipts, 5000);
  if (!waiting && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ------------------------------------------------------------------ wiring --

async function takeCaptureRequest() {
  const stored = await chrome.storage.session.get([CAPTURE_REQUEST_KEY]);
  const request = stored[CAPTURE_REQUEST_KEY];
  if (!request || request.windowId !== windowId) return;
  await chrome.storage.session.remove(CAPTURE_REQUEST_KEY);
  if (Date.now() - Number(request.at || 0) > REQUEST_MAX_AGE_MS || running) return;
  const tab = await chrome.tabs.get(request.tabId).catch(() => null);
  if (!tab || !isCapturableUrl(tab.url) || isReceiverPage(tab.url, await receiverBase())) return;
  // The keyboard saves what the preview shows: read it first if it is not.
  await ensurePreview(tab);
  if (!preview || preview.tabId !== tab.id) return;
  let review;
  try {
    review = currentReview();
  } catch (_) {
    return;
  }
  await startCapture(tab.id, review);
}

window.addEventListener("pagehide", () => {
  // Uploads die with this page: publish what arrived now, with the review the
  // reader already made.
  if (!inFlight || !inFlight.uploading) return;
  try {
    finalizeCapture(inFlight.receiptId, inFlight.rows, {
      complete: false, discovered: inFlight.discovered, overflow: inFlight.overflow,
      review: inFlight.review,
    }, { keepalive: true, config: inFlight.config });
  } catch (_) { /* the stale-hold sweep is the backstop */ }
});

$("#options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

chrome.tabs.onActivated.addListener((info) => {
  if (info.windowId === windowId && !running) showTab();
});
chrome.tabs.onUpdated.addListener((_tabId, change, tab) => {
  if (tab.windowId === windowId && tab.active && !running
      && (change.status === "complete" || change.url)) showTab();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[CAPTURE_REQUEST_KEY] && changes[CAPTURE_REQUEST_KEY].newValue) {
    takeCaptureRequest();
  }
  // A new receiver address changes which tabs are its reading pages.
  if (area === "local" && changes.apiBase && !running) {
    leaveReader();
    showTab();
  }
});

async function main() {
  // The version is on the panel because a reload is how every fix reaches an
  // unpacked extension, and nothing else on screen says which build is loaded
  // (operator request, 2026-09-18).
  $("#version").textContent = EXTENSION_VERSION;
  buildFields();
  windowId = (await chrome.windows.getCurrent()).id;
  await refreshReceipts();
  await showTab();
  await takeCaptureRequest();
}

main();
