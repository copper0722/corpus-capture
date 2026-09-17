"use strict";

// One capture, start to finish, for one article tab: the page with its figures,
// then every file the page links, into one bundle.
//
// Online the page is HELD at the receiver until its attachments have followed
// it, so the drain never builds a bundle from the page alone. Offline the same
// files land in the download directory beside the page, under the names the
// sidecar lists.

import { allCaptured, collectAttachments, discoverAttachments } from "./attachments.js";
import {
  capturePage,
  downloadAttachment,
  downloadFallback,
  downloadName,
  finalizeCapture,
  profileForUrl,
  profileRegistry,
  rememberReceipt,
  settings,
  submitCapture,
  uploadAttachment,
} from "./capture.js";

export const KIND_LABEL = {
  pdf: "PDF", supplement: "附錄", audio: "音訊", video: "影片", other: "附件",
};

export function summarizeAttachments(rows, discoveredCount) {
  if (!discoveredCount) return "頁面未連結附件";
  const captured = rows.filter((row) => row.status === "captured");
  const duplicates = rows.filter((row) => row.status === "duplicate");
  const failed = rows.filter((row) => row.status !== "captured" && row.status !== "duplicate");
  const counts = {};
  for (const row of captured) counts[row.kind] = (counts[row.kind] || 0) + 1;
  const got = Object.entries(counts)
    .map(([kind, n]) => `${KIND_LABEL[kind] || kind}×${n}`).join("、");
  const unique = discoveredCount - duplicates.length;
  const parts = [`附件 ${captured.length}/${unique}${got ? `（${got}）` : ""}`];
  if (duplicates.length) parts.push(`${duplicates.length} 個重複連結已合併`);
  if (failed.length) {
    parts.push(`缺 ${failed.length}：${failed.slice(0, 3)
      .map((row) => `${KIND_LABEL[row.kind] || row.kind} ${row.reason}`).join("；")}`
      + `${failed.length > 3 ? "…" : ""}`);
  }
  return parts.join("，");
}

/**
 * Capture `tabId`. Reports through `onStatus(text)` and `onRow(rows)`, and
 * hands `onHeld({ receiptId, rows, config, discovered })` the live state of a
 * held capture so the caller can close it if its own page goes away.
 */
export async function runCapture(tabId, { onStatus = () => {}, onRow = () => {}, onHeld = () => {} } = {}) {
  const tab = await chrome.tabs.get(tabId);
  if (!/^https?:/i.test(tab.url || "")) throw new Error("需要一個 http(s) 文章分頁");
  const registry = await profileRegistry();
  const profile = registry ? profileForUrl(registry, tab.url) : null;
  const capturedAt = new Date();

  onStatus("擷取頁面中…");
  const capture = await capturePage(tabId, (done, total) => {
    onStatus(`內嵌圖片與樣式 ${done}/${total}…`);
  }, profile);
  onStatus("找附件中（PDF、附錄、音訊、影片）…");
  const discovered = await discoverAttachments(tabId, profile);
  const candidates = discovered.attachments || [];
  const figureNames = (capture.figures || []).map((figure) => figure.label).join("、");
  onStatus(
    `已打包 ${(capture.html.length / 1024 / 1024).toFixed(1)} MB`
    + `，文章圖表 ${(capture.figures || []).length}${figureNames ? `（${figureNames}）` : ""}`
    + `，找到附件 ${candidates.length} 個`
    + `${discovered.overflow ? `（另 ${discovered.overflow} 個超過上限）` : ""}，送出中…`
  );

  const rows = [];
  const collect = (sink, prefix) => collectAttachments({
    tabId,
    pageUrl: capture.final_url || capture.url,
    profile,
    discovered: candidates,
    sink,
    rows,
    onProgress: (row, stage, position, total) => {
      const what = `${KIND_LABEL[row.kind] || row.kind}${row.label ? `「${row.label.slice(0, 40)}」` : ""}`;
      const size = row.bytes ? ` ${(row.bytes / 1024 / 1024).toFixed(1)} MB` : "";
      onStatus(`${prefix}附件 ${position + 1}/${total}：${stage === "storing" ? "存入" : "抓取"} ${what}${size}…`);
      onRow(rows);
    },
  });
  const base = {
    title: capture.title, url: capture.url, doi: capture.doi,
    captured_at: capturedAt.toISOString(),
  };

  try {
    const hold = candidates.length > 0;
    const receipt = await submitCapture(capture, capturedAt, { holdAttachments: hold });
    let state = receipt.state || "received";
    let note = "";
    if (hold) {
      onHeld({
        receiptId: receipt.receipt_id, rows, config: await settings(),
        discovered: candidates.length, overflow: discovered.overflow || 0,
      });
      await rememberReceipt({ ...base, receipt_id: receipt.receipt_id, state: "held" });
      await collect((meta, bytes) => uploadAttachment(receipt.receipt_id, meta, bytes), "已收下正文，");
      onRow(rows);
      const complete = rows.length === candidates.length && allCaptured(rows)
        && !discovered.overflow;
      try {
        const closed = await finalizeCapture(receipt.receipt_id, rows, {
          complete, discovered: candidates.length, overflow: discovered.overflow,
        });
        state = (closed && closed.state) || "received";
      } catch (error) {
        // The receiver keeps the held capture and publishes it on its own once
        // the hold is stale, with whatever arrived. Nothing already sent is lost.
        state = "held";
        note = `收尾未送達（${error.message}），接收端會自行結案`;
      }
    }
    const summary = summarizeAttachments(rows, candidates.length);
    await rememberReceipt({
      ...base, receipt_id: receipt.receipt_id, state, attachments_summary: summary,
    });
    return {
      mode: "online", receiptId: receipt.receipt_id, state, rows, summary, note,
      complete: !hold || (rows.length === candidates.length && allCaptured(rows)),
    };
  } catch (error) {
    // A 4xx is the server refusing this payload; downloading it would only
    // move the same refusal to the drain. Only a transport failure earns the
    // offline path.
    if (error.status && error.status < 500) throw error;
    onStatus(`API 連不上（${error.message}），改存到 Downloads/corpus-capture/…`);
    const stem = downloadName(capture.doi, capture.url, capturedAt);
    rows.length = 0;
    if (candidates.length) {
      await collect((meta, bytes) => downloadAttachment(stem, meta, bytes), "離線存檔，");
    }
    await downloadFallback(capture, capturedAt, { rows, discovered: candidates.length });
    const summary = summarizeAttachments(rows, candidates.length);
    await rememberReceipt({
      ...base, receipt_id: null, state: "downloaded", download_stem: stem,
      attachments_summary: summary,
    });
    return {
      mode: "offline", stem, rows, summary, note: `API 連不上（${error.message}）`,
      complete: rows.length === candidates.length && allCaptured(rows),
    };
  }
}
