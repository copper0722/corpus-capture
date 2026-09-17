"use strict";

// The side panel cannot own the wait. A receiver ingests on its own schedule, often
// minutes after the bytes arrive, so a capture is typically still `received`
// long after the window that submitted it has closed. This worker keeps asking,
// updates the stored receipt, and marks the toolbar icon when something lands,
// so reopening the side panel shows the outcome instead of a stale "sent".
import { listReceipts, readReceipt, rememberReceipt } from "./capture.js";
import { CAPTURE_REQUEST_KEY, openCaptureTab } from "./launch.js";

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

// The toolbar icon opens the side panel, where a capture starts, runs and is
// reviewed. Guarded, so a browser without the API still loads this worker and
// keeps the keyboard path below.
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

async function captureInTab(tab) {
  let target = tab;
  if (!target) {
    [target] = await chrome.tabs.query({ active: true, currentWindow: true });
  }
  try {
    await openCaptureTab(target);
    chrome.alarms.create(ALARM, { periodInMinutes: 1 });
  } catch (_) {
    await chrome.action.setBadgeBackgroundColor({ color: "#cf222e" });
    await chrome.action.setBadgeText({ text: "!" });
  }
}

// The keyboard path: one key opens the side panel and starts the capture there.
// `sidePanel.open` must run inside the keyboard gesture, so nothing is awaited
// before it; the request for the panel is written alongside.
chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== "capture-current-tab") return;
  if (!tab || !chrome.sidePanel || !chrome.sidePanel.open) {
    captureInTab(tab);
    return;
  }
  const opening = chrome.sidePanel.open({ windowId: tab.windowId });
  const request = chrome.storage.session.set({
    [CAPTURE_REQUEST_KEY]: { tabId: tab.id, windowId: tab.windowId, at: Date.now() },
  });
  Promise.all([opening, request])
    .then(() => chrome.alarms.create(ALARM, { periodInMinutes: 1 }))
    .catch(async () => {
      await chrome.storage.session.remove(CAPTURE_REQUEST_KEY).catch(() => {});
      await captureInTab(tab);
    });
});
