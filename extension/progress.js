"use strict";

import { finalizeCapture } from "./capture.js";
import { KIND_LABEL, runCapture } from "./runner.js";

const params = new URLSearchParams(location.search);
const tabId = Number.parseInt(params.get("tab") || "", 10);
const statusNode = document.querySelector("#status");
const rowsNode = document.querySelector("#rows");
const resultNode = document.querySelector("#result");
const closeButton = document.querySelector("#close");

const STATUS_LABEL = { captured: "已存", duplicate: "重複連結，已合併", failed: "缺" };

// A held capture this page is responsible for closing. If the tab goes away
// before the run finishes, the manifest so far is sent with keepalive, so the
// page is admitted now with what arrived instead of waiting out the hold.
let held = null;
let finished = false;

function setTitle(prefix, text) {
  document.title = `${prefix} ${text}`.slice(0, 80);
}

function renderRows(rows) {
  rowsNode.replaceChildren(...rows.map((row) => {
    const item = document.createElement("li");
    item.className = row.status || "";
    const kind = KIND_LABEL[row.kind] || row.kind;
    const size = row.bytes ? ` ${(row.bytes / 1024 / 1024).toFixed(1)} MB` : "";
    const detail = row.status === "failed" ? `：${row.reason}` : "";
    item.textContent = `${kind}${row.label ? `「${row.label}」` : ""}${size} — `
      + `${STATUS_LABEL[row.status] || row.status}${detail}`;
    return item;
  }));
}

window.addEventListener("pagehide", () => {
  if (finished || !held) return;
  try {
    finalizeCapture(held.receiptId, held.rows, {
      complete: false, discovered: held.discovered, overflow: held.overflow,
    }, { keepalive: true, config: held.config });
  } catch (_) { /* the receiver's stale-hold sweep is the backstop */ }
});

closeButton.addEventListener("click", () => window.close());

async function main() {
  if (!Number.isInteger(tabId)) throw new Error("缺少文章分頁編號");
  const tab = await chrome.tabs.get(tabId);
  document.querySelector("#title").textContent = tab.title || "存入 corpus";
  document.querySelector("#url").textContent = tab.url || "";
  setTitle("⏳", tab.title || "存入 corpus");
  const outcome = await runCapture(tabId, {
    onStatus: (text) => { statusNode.textContent = text; },
    onRow: (rows) => renderRows(rows),
    onHeld: (state) => { held = state; },
  });
  finished = true;
  renderRows(outcome.rows);
  const lines = [
    outcome.mode === "online" ? `收據 ${outcome.receiptId}` : `已離線存檔：${outcome.stem}`,
    outcome.summary,
    outcome.note,
  ].filter(Boolean);
  resultNode.textContent = lines.join("\n");
  resultNode.className = outcome.complete ? "ok" : "error";
  statusNode.textContent = outcome.mode === "online" ? "已送出，等待入庫" : "已存到 Downloads/corpus-capture/";
  setTitle(outcome.complete ? "✅" : "⚠️", tab.title || "存入 corpus");
  await chrome.runtime.sendMessage({ type: "corpus-capture-watch" }).catch(() => {});
  closeButton.hidden = false;
  if (outcome.complete) {
    // Nothing to read here that the toolbar list does not also show.
    setTimeout(() => window.close(), 8000);
  }
}

main().catch(async (error) => {
  if (held && !finished) {
    // The page is already held at the receiver. Close it now with what
    // arrived rather than leaving it to the stale-hold sweep.
    try {
      await finalizeCapture(held.receiptId, held.rows, {
        complete: false, discovered: held.discovered, overflow: held.overflow,
      }, { config: held.config });
    } catch (_) { /* the sweep remains the backstop */ }
  }
  finished = true;
  statusNode.textContent = "失敗";
  resultNode.textContent = String(error && error.message || error);
  resultNode.className = "error";
  setTitle("❌", "存入 corpus 失敗");
  closeButton.hidden = false;
});
