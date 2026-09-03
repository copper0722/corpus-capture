"use strict";

import { DEFAULT_API_BASE, normalizeBase } from "./capture.js";

const apiBase = document.querySelector("#apiBase");
const serviceToken = document.querySelector("#serviceToken");
const status = document.querySelector("#status");

chrome.storage.local.get(["apiBase", "serviceToken"]).then((stored) => {
  apiBase.value = stored.apiBase || DEFAULT_API_BASE;
  serviceToken.value = stored.serviceToken || "";
});

document.querySelector("#save").addEventListener("click", async () => {
  try {
    const origin = normalizeBase(apiBase.value || DEFAULT_API_BASE);
    // Stored in chrome.storage.local, which is device-local and never synced:
    // this is the deployment mutation secret, and a synced copy would put it on
    // every machine signed into the profile.
    await chrome.storage.local.set({
      apiBase: origin,
      serviceToken: serviceToken.value.trim(),
    });
    apiBase.value = origin;
    status.textContent = "已儲存。";
    status.className = "ok";
  } catch (error) {
    status.textContent = `設定無效：${error.message}`;
    status.className = "error";
  }
});
