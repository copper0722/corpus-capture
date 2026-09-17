"use strict";

// Where a capture runs. Not in the toolbar popup: Chrome closes the popup the
// moment the reader clicks anywhere else, and a capture that dies with it
// leaves the page held at the receiver and the attachments lost halfway. A
// progress tab the extension opens beside the article lives until it is done,
// in the background, while the reader keeps reading.

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
