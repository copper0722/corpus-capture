"use strict";

import {
  capturePage,
  profileForUrl,
  profileRegistry,
  downloadFallback,
  listReceipts,
  readReceipt,
  rememberReceipt,
  submitCapture,
} from "./capture.js";

const STATE_LABEL = {
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

const STATUS_BADGE = {
  supported: "已支援本網站之打包",
  generic: "通用模式",
  unsupported: "未支援",
};

const statusNode = document.querySelector("#status");
const badgeNode = document.querySelector("#profile");
const button = document.querySelector("#capture");
const receiptList = document.querySelector("#receipts");
let pollTimer = null;

function say(text, kind = "") {
  statusNode.textContent = text;
  statusNode.className = kind;
}

function render(rows) {
  receiptList.replaceChildren(...rows.slice(0, 5).map((row) => {
    const item = document.createElement("li");
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = row.title || row.url || row.receipt_id;
    const state = document.createElement("div");
    state.className = "state";
    state.textContent = STATE_LABEL[row.state] || row.state;
    item.append(title, state);
    if (row.reader_url) {
      const link = document.createElement("a");
      link.href = row.reader_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "在 Reader 開啟";
      item.append(link);
    }
    return item;
  }));
}

// Terminal for the popup's purpose: nothing the drain does afterwards changes
// what this window can usefully say, so polling stops rather than spinning.
const SETTLED = new Set([
  "admitted", "duplicate", "supplement_attached", "unsupported", "error", "downloaded",
]);

async function refresh() {
  const rows = await listReceipts();
  render(rows);
  const pending = rows.filter((row) => row.receipt_id && !SETTLED.has(row.state));
  if (!pending.length) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    return;
  }
  for (const row of pending) {
    try {
      const fresh = await readReceipt(row.receipt_id);
      await rememberReceipt({ ...row, ...fresh });
    } catch (_) { /* a poll failure is not a capture failure */ }
  }
  render(await listReceipts());
}

async function run() {
  button.disabled = true;
  const capturedAt = new Date();
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !/^https?:/.test(tab.url || "")) throw new Error("需要一個 http(s) 文章分頁");
    say("擷取頁面中…");
    const capture = await capturePage(tab.id, (done, total) => {
      say(`內嵌圖片與樣式 ${done}/${total}…`);
    }, activeProfile);
    const figureNames = (capture.figures || []).map((figure) => figure.label).join("、");
    say(
      `已打包 ${(capture.html.length / 1024 / 1024).toFixed(1)} MB`
      + `，文章圖表 ${(capture.figures || []).length}${figureNames ? `（${figureNames}）` : ""}`
      + `，其餘 ${(capture.decorative || []).length} 張標為裝飾，送出中…`
    );
    try {
      const receipt = await submitCapture(capture, capturedAt);
      await rememberReceipt({
        receipt_id: receipt.receipt_id,
        state: receipt.state || "received",
        title: capture.title,
        url: capture.url,
        doi: capture.doi,
        captured_at: capturedAt.toISOString(),
      });
      say(`收據 ${receipt.receipt_id}\n等待入庫…`, "ok");
    } catch (error) {
      // A 4xx is the server refusing this payload; downloading it would only
      // move the same refusal to the drain. Only a transport failure earns the
      // offline path.
      if (error.status && error.status < 500) throw error;
      const { stem } = await downloadFallback(capture, capturedAt);
      await rememberReceipt({
        receipt_id: null,
        state: "downloaded",
        title: capture.title,
        url: capture.url,
        doi: capture.doi,
        captured_at: capturedAt.toISOString(),
        download_stem: stem,
      });
      say(`API 連不上（${error.message}）。已存 Downloads/corpus-capture/${stem}.html 與同名 .json，等 inbox 拉取。`, "ok");
    }
    await chrome.runtime.sendMessage({ type: "corpus-capture-watch" }).catch(() => {});
  } catch (error) {
    say(`失敗：${error.message || error}`, "error");
  } finally {
    button.disabled = false;
    await refresh();
    if (!pollTimer) pollTimer = setInterval(refresh, 5000);
  }
}

// The badge is read from the registry, so what the popup promises and what the
// capture actually runs on are the same fact. A site nobody has profiled still
// captures -- in generic mode, and the sidecar records that it was generic, so
// a fallback capture is never later mistaken for a tested one.
let activeProfile = null;

async function showProfile() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const registry = await profileRegistry();
    if (!registry) {
      badgeNode.textContent = "設定檔未載入（仍可用通用模式擷取）";
      badgeNode.className = "badge generic";
      return;
    }
    activeProfile = profileForUrl(registry, tab && tab.url);
    const status = activeProfile.status || "generic";
    const name = activeProfile.matched ? activeProfile.display_name : "";
    badgeNode.textContent = [name, STATUS_BADGE[status] || status]
      .filter(Boolean).join(" — ");
    badgeNode.className = `badge ${status}`;
    if (status === "unsupported" && activeProfile.reason) {
      badgeNode.title = activeProfile.reason;
      badgeNode.textContent += `（${activeProfile.reason}）`;
    }
  } catch (_) {
    badgeNode.textContent = "設定檔未載入（仍可用通用模式擷取）";
    badgeNode.className = "badge generic";
  }
}

button.addEventListener("click", run);
document.querySelector("#options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

showProfile();
refresh().then(() => {
  say("按「存入 corpus」把目前這頁存進 corpus。");
  pollTimer = setInterval(refresh, 5000);
});
