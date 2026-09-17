"use strict";

import { safeReaderUrl } from "./net-policy.js";
import {
  listReceipts,
  profileForUrl,
  profileRegistry,
  readReceipt,
  rememberReceipt,
  settings,
} from "./capture.js";
import { openCaptureTab } from "./launch.js";

const STATE_LABEL = {
  held: "正文已收下，附件上傳中",
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

// The origin the receiver was configured with. A receipt's `reader_url` is a
// string the receiver chose, and this is what it has to match before the popup
// will offer it as a link: an extension surface the reader trusts must not be a
// launchpad to wherever a compromised or merely misconfigured receiver points.
let apiOrigin = "";

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
    if (row.attachments_summary) state.textContent += `｜${row.attachments_summary}`;
    item.append(title, state);
    const reader = safeReaderUrl(row.reader_url, apiOrigin);
    if (reader) {
      const link = document.createElement("a");
      link.href = reader;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = "在 Reader 開啟";
      item.append(link);
    } else if (row.reader_url) {
      // Say so rather than dropping it silently: a receipt whose reader link was
      // refused is a receiver problem the operator needs to see.
      const note = document.createElement("div");
      note.className = "state";
      note.textContent = "收據附的 Reader 連結不在設定的接收端網域，未顯示";
      item.append(note);
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
  try { apiOrigin = (await settings()).apiBase; } catch (_) { apiOrigin = ""; }
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
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    // The capture runs in its own tab: this popup closes the moment the reader
    // clicks anywhere else, and an attachment upload must not close with it.
    await openCaptureTab(tab);
    say("已在旁邊的分頁開始存入（含 PDF、附錄、音訊、影片）。可以繼續閱讀；完成後這裡會列出收據。", "ok");
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
  say("按「存入 corpus」（或 Alt+Shift+S；Mac 為 Control+Shift+S）把目前這頁連同 PDF、附錄、音訊與影片存進 corpus。");
  pollTimer = setInterval(refresh, 5000);
});
