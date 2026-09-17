"use strict";

import { DEFAULT_API_BASE, REVIEW_KEY, normalizeBase } from "./capture.js";

const apiBase = document.querySelector("#apiBase");
const serviceToken = document.querySelector("#serviceToken");
const review = document.querySelector("#review");
const status = document.querySelector("#status");

chrome.storage.local.get(["apiBase", "serviceToken", REVIEW_KEY]).then((stored) => {
  apiBase.value = stored.apiBase || DEFAULT_API_BASE;
  serviceToken.value = stored.serviceToken || "";
  review.checked = stored[REVIEW_KEY] !== false;
});

document.querySelector("#settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const origin = normalizeBase(apiBase.value || DEFAULT_API_BASE);
    // Stored in chrome.storage.local, which is device-local and never synced:
    // this is the deployment mutation secret, and a synced copy would put it on
    // every machine signed into the profile.
    await chrome.storage.local.set({
      apiBase: origin,
      serviceToken: serviceToken.value.trim(),
      [REVIEW_KEY]: review.checked,
    });
    apiBase.value = origin;
    status.textContent = "已儲存。";
    status.className = "ok";
  } catch (error) {
    status.textContent = `設定無效：${error.message}`;
    status.className = "error";
  }
});
