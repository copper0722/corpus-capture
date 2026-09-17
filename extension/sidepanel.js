"use strict";

// The side panel: where a capture starts, runs and is reviewed.
//
// It replaces the toolbar popup, which Chrome closes on the first click
// elsewhere. The panel stays open beside the article, so the capture runs here
// and the reader can confirm or correct the identity while the attachments are
// still uploading. Closing the panel mid-upload finalizes what arrived (as the
// progress tab did); closing it while only the review is left keeps the capture
// held, and the "待確認" list resumes it later.

import { safeReaderUrl } from "./net-policy.js";
import {
  finalizeCapture,
  listReceipts,
  normalizeDoi,
  profileForUrl,
  profileRegistry,
  readIdentity,
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

export const PENDING_KEY = "pendingReviews";
const REQUEST_MAX_AGE_MS = 60 * 1000;

const STATE_LABEL = {
  held: "已收下，等待附件或確認",
  received: "已收下，等排程入庫",
  queued: "已排入佇列",
  claimed: "處理中",
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
const REVIEW_LABEL = { confirmed: "已確認身分", corrected: "已修正身分" };
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
let session = null;
let pollTimer = null;

// ---------------------------------------------------------------- the page --

async function targetTab() {
  if (Number.isInteger(pinnedTab)) return chrome.tabs.get(pinnedTab).catch(() => null);
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  return tab || null;
}

async function showTab() {
  const tab = await targetTab();
  $("#page-title").textContent = (tab && (tab.title || tab.url)) || "—";
  const capturable = Boolean(tab && isCapturableUrl(tab.url));
  $("#capture").disabled = running || !capturable;
  const badge = $("#profile");
  if (!capturable) {
    badge.textContent = "這個分頁不是 http(s) 文章頁";
    badge.className = "badge unsupported";
    return;
  }
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
  let rows = await listReceipts();
  const pending = rows.filter((row) => row.receipt_id && !SETTLED.has(row.state));
  for (const row of pending) {
    try {
      await rememberReceipt({ ...row, ...(await readReceipt(row.receipt_id)) });
    } catch (_) { /* a failed poll says nothing about the capture */ }
  }
  rows = await listReceipts();
  $("#receipts").replaceChildren(...rows.slice(0, 8).map(receiptItem));
  await renderPending();
  const waiting = rows.some((row) => row.receipt_id && !SETTLED.has(row.state));
  if (waiting && !pollTimer) pollTimer = setInterval(refreshReceipts, 5000);
  if (!waiting && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ---------------------------------------------------------- pending review --

async function pendingReviews() {
  const stored = await chrome.storage.local.get([PENDING_KEY]);
  return Array.isArray(stored[PENDING_KEY]) ? stored[PENDING_KEY] : [];
}

async function savePending(entry) {
  const rows = (await pendingReviews()).filter((row) => row.receipt_id !== entry.receipt_id);
  rows.unshift(entry);
  await chrome.storage.local.set({ [PENDING_KEY]: rows.slice(0, 20) });
}

async function updatePending(receiptId, patch) {
  const rows = await pendingReviews();
  const row = rows.find((item) => item.receipt_id === receiptId);
  if (!row) return;
  Object.assign(row, patch);
  await chrome.storage.local.set({ [PENDING_KEY]: rows });
}

async function dropPending(receiptId) {
  const rows = (await pendingReviews()).filter((row) => row.receipt_id !== receiptId);
  await chrome.storage.local.set({ [PENDING_KEY]: rows });
}

async function renderPending() {
  const receipts = await listReceipts();
  const held = new Set(receipts.filter((row) => row.state === "held").map((row) => row.receipt_id));
  const rows = [];
  for (const entry of await pendingReviews()) {
    // Only a capture the receiver still holds, whose uploads finished, and that
    // is not the one under review right now can be resumed.
    if (!entry.finalize || (session && session.receiptId === entry.receipt_id)) continue;
    if (!held.has(entry.receipt_id)) {
      await dropPending(entry.receipt_id);
      continue;
    }
    rows.push(entry);
  }
  $("#pending").hidden = rows.length === 0;
  $("#pending-list").replaceChildren(...rows.map((entry) => {
    const item = document.createElement("li");
    item.className = "pending";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = entry.title || entry.url;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = "繼續確認";
    button.disabled = running || Boolean(session);
    button.addEventListener("click", () => resumeReview(entry));
    item.append(title, button);
    return item;
  }));
}

// ------------------------------------------------------------ review form --

function fieldControl(field) {
  const multiline = field === "title" || field === "authors";
  const control = document.createElement(multiline ? "textarea" : "input");
  if (!multiline) control.type = "text";
  control.id = `meta-${field}`;
  control.name = field;
  control.autocomplete = "off";
  if (multiline) control.rows = field === "authors" ? 4 : 2;
  return control;
}

function buildFields() {
  const container = $("#fields");
  container.replaceChildren(...METADATA_FIELDS.map((field) => {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.dataset.field = field;
    const label = document.createElement("label");
    label.htmlFor = `meta-${field}`;
    label.textContent = FIELD_LABELS[field];
    const source = document.createElement("span");
    source.className = "source";
    label.append(source);
    const control = fieldControl(field);
    control.addEventListener("input", () => {
      if (session) session.touched.add(field);
      markChanged(field);
    });
    const alt = document.createElement("p");
    alt.className = "alt";
    alt.hidden = true;
    wrap.append(label, control, alt);
    return wrap;
  }));
}

function control(field) {
  return $(`#meta-${field}`);
}

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
  if (!session) return;
  const wrap = document.querySelector(`.field[data-field="${field}"]`);
  const baseline = session.baseline.metadata[field];
  wrap.classList.toggle("changed", JSON.stringify(readValue(field)) !== JSON.stringify(baseline));
}

function showAlternative(field, alternative) {
  const alt = document.querySelector(`.field[data-field="${field}"] .alt`);
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
    if (session) session.touched.add(field);
    markChanged(field);
  });
  alt.replaceChildren(text, use);
  alt.hidden = false;
}

function applyProposal(proposal, { overwriteTouched = false } = {}) {
  for (const field of METADATA_FIELDS) {
    const wrap = document.querySelector(`.field[data-field="${field}"]`);
    const touched = session && session.touched.has(field);
    const current = readValue(field);
    const offered = proposal.values[field];
    if (!touched || overwriteTouched) {
      writeValue(field, offered);
      showAlternative(field, proposal.alternatives[field]);
    } else if (JSON.stringify(current) !== JSON.stringify(offered)
               && (Array.isArray(offered) ? offered.length : offered)) {
      // Never overwrite what the reader typed; offer the new value beside it.
      showAlternative(field, { source: proposal.sources[field] || "receiver", value: offered });
    }
    wrap.querySelector(".source").textContent = proposal.sources[field]
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
  if (!known) {
    note.hidden = true;
    note.replaceChildren();
    return;
  }
  const text = document.createElement("span");
  text.textContent = `corpus 已有這篇${known.title ? `：${known.title}` : ""}。入庫時會併入既有紀錄。`;
  note.replaceChildren(text);
  const reader = safeReaderUrl(known.reader_url, apiOrigin);
  if (reader) {
    const link = document.createElement("a");
    link.href = reader;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = " 在 Reader 開啟";
    note.append(link);
  }
  note.hidden = false;
}

async function lookup(doi, { initial = false } = {}) {
  const current = session;
  if (!current) return;
  const status = $("#meta-status");
  status.textContent = "向接收端查詢書目資料…";
  let identity = null;
  try {
    identity = await readIdentity(current.receiptId, doi || null);
  } catch (error) {
    if (session !== current) return;
    status.textContent = `接收端查詢失敗（${error.message}）；以下為頁面宣告的資料，可直接修正後確認。`;
    return;
  }
  if (session !== current) return;
  if (!identity) {
    status.textContent = "接收端未提供書目查詢；以下為頁面宣告的資料，可直接修正後確認。";
    return;
  }
  const proposal = mergeProposal(current.pageMeta, identity);
  // A lookup for the DOI the page declared defines the baseline a correction is
  // measured against; a lookup for a DOI the reader typed is itself a correction.
  if (initial) {
    current.baseline = { doi: current.detectedDoi, metadata: proposal.values };
  }
  applyProposal(proposal, { overwriteTouched: !initial });
  showKnown(identity);
  if (identity.reviewable === false) {
    status.textContent = "接收端已結案這筆擷取；確認內容不會再套用。";
    $("#confirm").textContent = "關閉";
    current.closed = true;
    return;
  }
  const source = SOURCE_LABEL[identity.metadata_source] || identity.metadata_source || "接收端";
  status.textContent = identity.metadata
    ? `已載入${source}的書目資料；與頁面不同處列在欄位下方。`
    : "接收端沒有查到這個 DOI 的書目資料；以下為頁面宣告的資料。";
}

function closeReview() {
  $("#review").hidden = true;
  session = null;
  renderPending();
}

/**
 * Show the review for one held capture. Resolves with the `reader_review`
 * observation once the reader submits, or `null` if the receiver already
 * closed the capture.
 */
function openReview({ receiptId, title, detectedDoi, doiSource, pageMeta }) {
  return new Promise((resolve) => {
    const page = pageMeta;
    session = {
      receiptId, resolve, detectedDoi: detectedDoi || null, pageMeta: page,
      touched: new Set(), closed: false,
      baseline: { doi: detectedDoi || null, metadata: mergeProposal(page, null).values },
    };
    $("#review-title").textContent = title || "";
    const doiInput = $("#doi");
    doiInput.value = detectedDoi || "";
    doiInput.setCustomValidity("");
    $("#doi-hint").textContent = `來源：${DOI_SOURCE_LABELS[doiSource] || DOI_SOURCE_LABELS.none}`;
    setDoiLink(detectedDoi);
    $("#confirm").textContent = "確認並入庫";
    $("#confirm").disabled = false;
    applyProposal(mergeProposal(page, null), { overwriteTouched: true });
    showKnown(null);
    $("#review").hidden = false;
    renderPending();
    doiInput.focus();
    lookup(detectedDoi, { initial: true });
  });
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

$("#review").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!session) return;
  const current = session;
  if (current.closed) {
    closeReview();
    current.resolve(null);
    return;
  }
  const input = $("#doi");
  const value = readForm();
  let review;
  try {
    review = buildReaderReview({
      detectedDoi: current.detectedDoi, proposed: current.baseline, final: value,
    });
  } catch (_) {
    input.setCustomValidity("DOI 應為 10.xxxx/… 的格式；沒有 DOI 請留空。");
    input.reportValidity();
    return;
  }
  $("#confirm").disabled = true;
  closeReview();
  current.resolve(review);
});

// ----------------------------------------------------------------- capture --

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

async function reviewStep({ receiptId, capture, uploads }) {
  const entry = {
    receipt_id: receiptId,
    title: capture.title,
    url: capture.url,
    captured_at: new Date().toISOString(),
    detected_doi: capture.doi || null,
    doi_source: capture.doi_source || "none",
    page_metadata: pageMetadata(capture),
    finalize: null,
  };
  await savePending(entry);
  uploads.then(async (closing) => {
    if (inFlight && inFlight.receiptId === receiptId) inFlight.uploading = false;
    await updatePending(receiptId, {
      finalize: {
        rows: closing.rows, complete: closing.complete,
        discovered: closing.discovered, overflow: closing.overflow,
      },
    });
  }).catch(() => {});
  const review = await openReview({
    receiptId, title: capture.title, detectedDoi: entry.detected_doi,
    doiSource: entry.doi_source, pageMeta: entry.page_metadata,
  });
  $("#status").textContent = "已確認，等附件上傳完成後入庫…";
  return review;
}

async function startCapture(tabId) {
  if (running || session) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !isCapturableUrl(tab.url)) return;
  running = true;
  $("#capture").disabled = true;
  $("#run").hidden = false;
  $("#run-title").textContent = tab.title || tab.url;
  $("#rows").replaceChildren();
  $("#result").textContent = "";
  $("#result").className = "";
  $("#status").textContent = "準備中…";
  let receiptId = null;
  try {
    const reviewing = await reviewEnabled();
    const outcome = await runCapture(tabId, {
      onStatus: (text) => { $("#status").textContent = text; },
      onRow: (rows) => renderRows(rows),
      onHeld: (state) => {
        receiptId = state.receiptId;
        inFlight = { ...state, uploading: true };
      },
      review: reviewing ? reviewStep : null,
    });
    renderRows(outcome.rows);
    const lines = [
      outcome.mode === "online" ? `收據 ${outcome.receiptId}` : `已離線存檔：${outcome.stem}`,
      outcome.review ? `${REVIEW_LABEL[outcome.review.decision]}${outcome.review.doi ? `：${outcome.review.doi}` : "（無 DOI）"}` : "",
      outcome.summary,
      outcome.note,
    ].filter(Boolean);
    $("#result").textContent = lines.join("\n");
    $("#result").className = outcome.complete ? "ok" : "error";
    $("#status").textContent = outcome.mode === "online" ? "已送出，等待入庫" : "已存到 Downloads/corpus-capture/";
    if (receiptId) await dropPending(receiptId);
    await chrome.runtime.sendMessage({ type: "corpus-capture-watch" }).catch(() => {});
  } catch (error) {
    if (inFlight && inFlight.uploading) {
      try {
        await finalizeCapture(inFlight.receiptId, inFlight.rows, {
          complete: false, discovered: inFlight.discovered, overflow: inFlight.overflow,
        }, { config: inFlight.config });
      } catch (_) { /* the receiver's stale-hold sweep is the backstop */ }
    }
    if (session) closeReview();
    if (receiptId) await dropPending(receiptId);
    $("#status").textContent = "失敗";
    $("#result").textContent = String((error && error.message) || error);
    $("#result").className = "error";
  } finally {
    running = false;
    inFlight = null;
    await showTab();
    await refreshReceipts();
  }
}

async function resumeReview(entry) {
  if (running || session) return;
  let fresh;
  try {
    fresh = await readReceipt(entry.receipt_id);
  } catch (error) {
    $("#result").textContent = `讀不到收據（${error.message}）`;
    return;
  }
  if (fresh.state !== "held") {
    await dropPending(entry.receipt_id);
    await rememberReceipt({ receipt_id: entry.receipt_id, title: entry.title, url: entry.url, ...fresh });
    await refreshReceipts();
    return;
  }
  running = true;
  $("#capture").disabled = true;
  try {
    const review = await openReview({
      receiptId: entry.receipt_id, title: entry.title, detectedDoi: entry.detected_doi,
      doiSource: entry.doi_source, pageMeta: entry.page_metadata,
    });
    const closed = await finalizeCapture(entry.receipt_id, entry.finalize.rows, {
      complete: entry.finalize.complete, discovered: entry.finalize.discovered,
      overflow: entry.finalize.overflow, review,
    });
    await dropPending(entry.receipt_id);
    await rememberReceipt({
      receipt_id: entry.receipt_id, title: (review && review.metadata.title) || entry.title,
      url: entry.url, captured_at: entry.captured_at, state: (closed && closed.state) || "received",
      ...(review ? { review_decision: review.decision, doi: review.doi } : {}),
    });
    await chrome.runtime.sendMessage({ type: "corpus-capture-watch" }).catch(() => {});
  } catch (error) {
    $("#result").textContent = `確認未送達（${error.message}）；接收端會在逾時後自行結案。`;
    $("#result").className = "error";
  } finally {
    running = false;
    await showTab();
    await refreshReceipts();
  }
}

// ------------------------------------------------------------------ wiring --

async function takeCaptureRequest() {
  const stored = await chrome.storage.session.get([CAPTURE_REQUEST_KEY]);
  const request = stored[CAPTURE_REQUEST_KEY];
  if (!request || request.windowId !== windowId) return;
  await chrome.storage.session.remove(CAPTURE_REQUEST_KEY);
  if (Date.now() - Number(request.at || 0) > REQUEST_MAX_AGE_MS) return;
  await startCapture(request.tabId);
}

window.addEventListener("pagehide", () => {
  // Uploads die with this page. Publish what arrived now, as the progress tab
  // did; a capture waiting only for its review stays held and can be resumed.
  if (!inFlight || !inFlight.uploading) return;
  try {
    finalizeCapture(inFlight.receiptId, inFlight.rows, {
      complete: false, discovered: inFlight.discovered, overflow: inFlight.overflow,
    }, { keepalive: true, config: inFlight.config });
  } catch (_) { /* the stale-hold sweep is the backstop */ }
});

$("#capture").addEventListener("click", async () => {
  const tab = await targetTab();
  if (tab) await startCapture(tab.id);
});

$("#options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

chrome.tabs.onActivated.addListener((info) => {
  if (info.windowId === windowId) showTab();
});
chrome.tabs.onUpdated.addListener((_tabId, change, tab) => {
  if (tab.windowId === windowId && tab.active && (change.status === "complete" || change.title)) showTab();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[CAPTURE_REQUEST_KEY] && changes[CAPTURE_REQUEST_KEY].newValue) {
    takeCaptureRequest();
  }
});

async function main() {
  buildFields();
  windowId = (await chrome.windows.getCurrent()).id;
  await showTab();
  await refreshReceipts();
  await takeCaptureRequest();
}

main();
