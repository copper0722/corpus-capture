"use strict";

// Where a capture runs. Normally in the side panel, which stays open beside the
// article while the reader keeps reading and reviews the identity. The progress
// tab below is the fallback for a browser without the side panel API: a tab the
// extension opens beside the article, which lives until the capture is done.

//: Where the keyboard shortcut leaves "capture this tab" for the side panel it
//: just opened. Session storage: gone with the browser, never synced.
export const CAPTURE_REQUEST_KEY = "captureRequest";

export function isCapturableUrl(url) {
  return /^https?:/i.test(String(url || ""));
}

export async function openCaptureTab(tab) {
  if (!tab || !Number.isInteger(tab.id) || !isCapturableUrl(tab.url)) {
    throw new Error("需要一個 http(s) 文章分頁");
  }
  return chrome.tabs.create({
    url: chrome.runtime.getURL(`progress.html?tab=${tab.id}`),
    index: Number.isInteger(tab.index) ? tab.index + 1 : undefined,
    openerTabId: tab.id,
    active: false,
  });
}
