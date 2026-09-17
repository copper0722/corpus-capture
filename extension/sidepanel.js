"use strict";

// The side panel: preview the open page's identity, correct it, then save.
//
// Opening the panel (or switching tabs) reads what the page declares -- its
// DOI and bibliographic <meta> -- and asks the receiver for the record it holds
// for that DOI. The reader corrects whatever is wrong and presses one button;
// the capture then runs here, with the previewed values sent as the
// `reader_review` observation when the capture is finalized.

import { safeReaderUrl } from "./net-policy.js";
import {
  finalizeCapture,
  listReceipts,
  lookupIdentity,
  mergeReceipt,
  normalizeDoi,
  probeTab,
  profileForUrl,
  profileRegistry,
  readReceipt,
  rememberReceipt,
  reviewEnabled,
  settings,
} from "./capture.js";
import { CAPTURE_REQUEST_KEY, isCapturableUrl } from "./launch.js";
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
const STATE_LABEL = {
  held: "已收下，附件上傳中",
  received: "已收下，等排程建立 bundle",
  queued: "已排入佇列",
  claimed: "建立 bundle 中",
  admitted: "已入庫",
  duplicate: "重複，已併入既有 bundle",
  supplement_attached: "已當附件掛上母文章",
  needs_identity_review: "身分待人工確認",
  needs_proxy: "需授權管道",
  unsupported: "不支援的來源",
  error: "入庫失敗",
  unknown: "狀態不明",
  downloaded: "已離線存到 Downloads/corpus-capture/",
};
const REVIEW_LABEL = { confirmed: "身分已確認", corrected: "身分已修正" };
const STATUS_BADGE = { supported: "已支援本網站之打包", generic: "通用模式", unsupported: "未支援" };
const ROW_LABEL = { captured: "已存", duplicate: "重複連結，已合併", failed: "缺" };
const SETTLED = new Set([
  "admitted", "duplicate", "supplement_attached", "unsupported", "error", "downloaded",
]);
const SOURCE_LABEL = {
  page: "頁面", registry: "書目登記資料", corpus: "corpus 既有紀錄",
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

// ---------------------------------------------------------------- the page --

async function targetTab() {
  if (Number.isInteger(pinnedTab)) return chrome.tabs.get(pinnedTab).catch(() => null);
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  return tab || null;
}

async function showBadge(tab) {
  const badge = $("#profile");
  try {
    const registry = await profileRegistry();
    if (!registry) throw new Error("no_registry");
    const profile = profileForUrl(registry, tab.url);
    const status = profile.status || "generic";
    badge.textContent = [profile.matched ? profile.display_name : "", STATUS_BADGE[status] || status]
      .filter(Boolean).join(" — ") + (status === "unsupported" && profile.reason ? `（${profile.reason}）` : "");
    badge.className = `badge ${status}`;
  } catch (_) {
    badge.textContent = "設定檔未載入（仍可用通用模式擷取）";
    badge.className = "badge generic";
  }
}

function pageNote(text) {
  $("#page-note").textContent = text || "";
  $("#page-note").hidden = !text;
}

async function showTab() {
  const tab = await targetTab();
  $("#page-title").textContent = (tab && (tab.title || tab.url)) || "—";
  const capturable = Boolean(tab && isCapturableUrl(tab.url));
  if (!capturable) {
    generation += 1;
    preview = null;
    reading = null;
    $("#review").hidden = true;
    $("#profile").textContent = "這個分頁不是 http(s) 文章頁";
    $("#profile").className = "badge unsupported";
    pageNote("切到文章頁即可預覽並儲存。");
    return;
  }
  await showBadge(tab);
  await ensurePreview(tab);
}

// The same page keeps what the reader already typed, and a page being read is
// waited for, not read again; a new page is read anew.
async function ensurePreview(tab) {
  const same = (entry) => Boolean(entry) && entry.tabId === tab.id && entry.url === tab.url;
  if (same(preview)) return undefined;
  if (same(reading)) return reading.done;
  return loadPreview(tab);
}

// -------------------------------------------------------------- the preview --

function fieldControl(field) {
  const multiline = field === "title" || field === "authors";
  const node = document.createElement(multiline ? "textarea" : "input");
  if (!multiline) node.type = "text";
  node.id = `meta-${field}`;
  node.name = field;
  node.autocomplete = "off";
  if (multiline) node.rows = field === "authors" ? 4 : 2;
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
    const source = document.createElement("span");
    source.className = "source";
    label.append(source);
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
    fieldWrap(field).querySelector(".source").textContent = proposal.sources[field]
      ? `（${SOURCE_LABEL[proposal.sources[field]] || proposal.sources[field]}）` : "";
    markChanged(field);
  }
}

function setDoiLink(doi) {
  const link = $("#doi-link");
  const normalized = normalizeDoi(doi);
  link.hidden = !normalized;
  if (normalized) link.href = `https://doi.org/${encodeURI(normalized)}`;
}

function showKnown(identity) {
  const known = identity && identity.known;
  const note = $("#known");
  note.replaceChildren();
  note.hidden = !known;
  if (!known) return;
  const text = document.createElement("span");
  text.textContent = `corpus 已有這篇${known.title ? `：${known.title}` : ""}。儲存時會併入既有紀錄。`;
  note.append(text);
  const reader = safeReaderUrl(known.reader_url, apiOrigin);
  if (reader) {
    const link = document.createElement("a");
    link.href = reader;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = " 在 Reader 開啟";
    note.append(link);
  }
}

const LOOKUP_NOTE = {
  in_progress: "接收端正在查詢這個 DOI；以下先列頁面宣告的資料，稍後可按「用這個 DOI 重新查詢」。",
  unavailable: "接收端未提供書目查詢；以下為頁面宣告的資料。",
  error: "接收端查詢書目失敗；以下為頁面宣告的資料，可直接修正後儲存。",
  unresolved: "接收端沒有查到這個 DOI 的書目資料；以下為頁面宣告的資料。",
};

async function lookup(doi) {
  if (!preview) return;
  const mine = preview.generation;
  const status = $("#meta-status");
  showKnown(null);
  if (!doi) {
    status.textContent = "頁面沒有 DOI；請確認下列書目資料，或在上方補上 DOI 後重新查詢。";
    return;
  }
  status.textContent = "向接收端查詢書目資料…";
  let identity = null;
  try {
    identity = await lookupIdentity(doi);
  } catch (error) {
    if (!preview || preview.generation !== mine) return;
    status.textContent = `接收端查詢失敗（${error.message}）；以下為頁面宣告的資料，可直接修正後儲存。`;
    return;
  }
  if (!preview || preview.generation !== mine) return;
  if (!identity) {
    status.textContent = LOOKUP_NOTE.unavailable;
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
  const source = SOURCE_LABEL[identity.metadata_source] || identity.metadata_source || "接收端";
  status.textContent = identity.metadata
    ? `已載入${source}；與頁面不同處列在欄位下方。`
    : LOOKUP_NOTE[identity.metadata_status] || LOOKUP_NOTE.unresolved;
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
  $("#meta-status").textContent = "讀取頁面中…";
  let page;
  try {
    page = await probeTab(tab.id);
  } catch (error) {
    if (mine !== generation) return;
    preview = null;
    $("#review").hidden = true;
    pageNote(`讀不到這個頁面的宣告（${error.message}）；重新整理頁面後再試。`);
    return;
  }
  if (mine !== generation) return;
  const pageMeta = pageMetadata(page);
  const proposal = mergeProposal(pageMeta, null);
  preview = {
    tabId: tab.id, url: tab.url, generation: mine, touched: new Set(),
    detectedDoi: page.doi, pageMeta,
    baseline: { doi: page.doi, metadata: proposal.values },
  };
  const input = $("#doi");
  input.value = page.doi || "";
  input.setCustomValidity("");
  $("#doi-hint").textContent = `來源：${DOI_SOURCE_LABELS[page.doi_source] || DOI_SOURCE_LABELS.none}`;
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
    input.setCustomValidity("DOI 應為 10.xxxx/… 的格式");
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
    $("#doi").setCustomValidity("DOI 應為 10.xxxx/… 的格式；沒有 DOI 請留空。");
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
    const size = row.bytes ? ` ${(row.bytes / 1024 / 1024).toFixed(1)} MB` : "";
    item.textContent = `${KIND_LABEL[row.kind] || row.kind}${row.label ? `「${row.label}」` : ""}${size} — `
      + `${ROW_LABEL[row.status] || row.status}${row.status === "failed" ? `：${row.reason}` : ""}`;
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
  setBusy(true);
  $("#run").hidden = false;
  $("#run-title").textContent = tab.title || tab.url;
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
      outcome.mode === "online" ? `收據 ${outcome.receiptId}` : `已離線存檔：${outcome.stem}`,
      outcome.review
        ? `${REVIEW_LABEL[outcome.review.decision]}${outcome.review.doi ? `：${outcome.review.doi}` : "（無 DOI）"}`
        : "",
      outcome.summary,
      outcome.note,
    ].filter(Boolean);
    $("#result").textContent = lines.join("\n");
    $("#result").className = outcome.complete ? "ok" : "error";
    $("#status").textContent = outcome.mode === "online"
      ? "已送出；接收端會在下一輪排程建立 bundle" : "已存到 Downloads/corpus-capture/";
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
  }
}

// --------------------------------------------------------------- receipts --

function receiptItem(row) {
  const item = document.createElement("li");
  item.className = "receipt";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = row.title || row.url || row.receipt_id;
  const state = document.createElement("div");
  state.className = "state";
  state.textContent = [
    STATE_LABEL[row.state] || row.state,
    REVIEW_LABEL[row.review_decision],
    row.doi ? `DOI ${row.doi}` : "",
    row.attachments_summary,
  ].filter(Boolean).join("｜");
  item.append(title, state);
  const reader = safeReaderUrl(row.reader_url, apiOrigin);
  if (reader) {
    const link = document.createElement("a");
    link.href = reader;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = "在 Reader 開啟";
    item.append(link);
  }
  return item;
}

async function refreshReceipts() {
  try { apiOrigin = (await settings()).apiBase; } catch (_) { apiOrigin = ""; }
  for (const row of (await listReceipts()).filter((r) => r.receipt_id && !SETTLED.has(r.state))) {
    try {
      await rememberReceipt(mergeReceipt(row, await readReceipt(row.receipt_id)));
    } catch (_) { /* a failed poll says nothing about the capture */ }
  }
  const rows = await listReceipts();
  $("#receipts").replaceChildren(...rows.slice(0, 8).map(receiptItem));
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
  if (!tab || !isCapturableUrl(tab.url)) return;
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
});

async function main() {
  buildFields();
  windowId = (await chrome.windows.getCurrent()).id;
  await refreshReceipts();
  await showTab();
  await takeCaptureRequest();
}

main();
