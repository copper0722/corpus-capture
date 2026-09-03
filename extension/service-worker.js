"use strict";

// The popup cannot own the wait. A receiver ingests on its own schedule, often
// minutes after the bytes arrive, so a capture is typically still `received`
// long after the window that submitted it has closed. This worker keeps asking,
// updates the stored receipt, and marks the toolbar icon when something lands,
// so reopening the popup shows the outcome instead of a stale "sent".
import { listReceipts, readReceipt, rememberReceipt } from "./capture.js";

const ALARM = "corpus-capture-poll";
const SETTLED = new Set([
  "admitted", "duplicate", "supplement_attached", "unsupported", "error", "downloaded",
]);
// Past this the drain is not merely late, it is broken, and repeating the poll
// forever would hide that behind a spinner instead of leaving evidence.
const GIVE_UP_MS = 6 * 60 * 60 * 1000;

async function poll() {
  const rows = await listReceipts();
  const pending = rows.filter(
    (row) =>
      row.receipt_id &&
      !SETTLED.has(row.state) &&
      Date.now() - Date.parse(row.captured_at || 0) < GIVE_UP_MS
  );
  let landed = 0;
  for (const row of pending) {
    try {
      const fresh = await readReceipt(row.receipt_id);
      await rememberReceipt({ ...row, ...fresh });
      if (SETTLED.has(fresh.state)) landed += 1;
    } catch (_) { /* keep the row; a failed poll says nothing about the capture */ }
  }
  const remaining = (await listReceipts()).filter(
    (row) => row.receipt_id && !SETTLED.has(row.state)
  ).length;
  await chrome.action.setBadgeText({ text: remaining ? String(remaining) : "" });
  if (landed) await chrome.action.setBadgeBackgroundColor({ color: "#1a7f37" });
  if (!remaining) await chrome.alarms.clear(ALARM);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) poll();
});

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.type !== "corpus-capture-watch") return undefined;
  chrome.alarms.create(ALARM, { periodInMinutes: 1 });
  poll().then(() => respond({ ok: true })).catch(() => respond({ ok: false }));
  return true;
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM, { periodInMinutes: 1 });
});
