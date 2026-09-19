# Chrome Web Store submission record

Kept current with every change to `extension/` (Chrome's
[build-with-AI guidance](https://developer.chrome.com/docs/extensions/ai/build-with-ai)):
a permission added to the manifest without a justification here fails
`tests/test_chrome_web_store.py`.

## Single purpose

Save the article the reader is viewing -- the page with its figures, and the
files the article itself links (PDF, supplementary files, audio, video) -- to a
receiver the reader configured, in one action, after the reader has confirmed
or corrected the article's DOI and bibliographic details in the side panel. On
that receiver's own reading page, the same side panel shows the bibliographic
details of the saved work the page displays.

## Permission justifications

| Permission | Why it is needed |
|---|---|
| `activeTab` | The capture starts from the side panel or the keyboard command and reads only the article tab the reader is on. |
| `sidePanel` | The toolbar button opens a side panel beside the article, where the reader starts the capture, watches its progress, and confirms or corrects the detected DOI and bibliographic details before the article is saved. |
| `scripting` | Serializes the article container and discovers its attachment links inside the page; fetches same-origin attachments from the page so the publisher sees the reader's own session and Referer. On the receiver's own reading page, reads the bibliographic `<meta>` it declares for the work on screen and watches for the next one. |
| `storage` | Keeps the receiver address, the reader's service token, the cached publisher profile registry and the last receipts on this device. |
| `downloads` | Offline fallback: when the receiver is unreachable, the page, its sidecar and its attachments are saved to the download folder. |
| `alarms` | Polls the receiver for the admission state of recent captures and updates the toolbar badge. |
| `<all_urls>` (host) | Articles live on any publisher's site; figures and publisher-profile video hosts must be fetched from the extension, where the page's CORS rules do not block them. Every fetch passes the network policy in `extension/net-policy.js` (https only, no private or loopback hosts, cookies only to the page's own origin). |
| `commands` (manifest key) | `Alt+Shift+S` / `Control+Shift+S` opens the side panel and starts a capture of the current article. |

## Remote code

None. Every script ships in the package; captured pages are sanitized and never
executed.

## Data use (privacy practices)

- Collected: the content of the page the reader chose to save, its attachment
  files, the page's bibliographic metadata, and the DOI and bibliographic
  details the reader confirmed or corrected.
- Sent only to the receiver address the reader entered in the options page. No
  analytics, no third-party transfer, no sale, not used for advertising or
  credit decisions.
- Stored on the device: receiver address, service token, the review setting,
  profile registry cache, the last twenty receipts, and captures still waiting
  for the reader's confirmation. No cookies, passwords or browsing history are read
  or stored.
- Attachments are fetched with the reader's existing session only from the
  article's own origin; files from a profile-listed media host are fetched
  anonymously.
- On the receiver's own reading page, the panel reads the bibliographic
  `<meta>` that page declares and sends the declared DOI to that receiver's
  lookup; nothing from it is stored or sent anywhere else.

## Store listing

- Name: Corpus Capture
- Summary: 把目前這篇文章頁（含圖片）連同 PDF、supplement、音訊與影片附件，一鍵存入 corpus 的同一個 bundle；側欄可先確認並修正 DOI 與書目資料，在 corpus 的閱讀頁則顯示該篇的書目資料。
- Category: Productivity

## Version history

| Version | Date | Change |
|---|---|---|
| 1.4.4 | 2026-09-19 | A page that declares several identifiers under one key (Science: a publisher id, then the DOI) has its declared DOI recognized instead of one inferred from the address. No permission change. |
| 1.4.3 | 2026-09-19 | A captioned figure inside the article's abstract (a visual or graphical abstract with no figure label) is recorded in the figure manifest. No permission change. |
| 1.4.2 | 2026-09-19 | A page the browser is translating is refused instead of being saved as a half-translated source; the panel says so and asks the reader to show the original first. No permission change. |
| 1.4.1 | 2026-09-19 | A page that declares its DOI as `publication_doi` (a Science news story) is recognized; a news page's byline, date and publisher are read from its JSON-LD when no `<meta>` gives them; a declared timestamp is shown as its day. A settled capture is listed only beside its own page. No permission change. |
| 1.4.0 | 2026-09-18 | On the receiver's own reading page the side panel becomes a read-only card of the work that page shows (its declared `citation_*` metadata, completed from the receiver's record for its DOI), following the page as it changes; nothing is captured there. The panel shows only values and states that need action: headings, instructions and notes about the normal case are gone. No permission change. |
| 1.3.2 | 2026-09-18 | The side panel and the settings page show the extension's version, so a reader can see which build is loaded. |
| 1.3.1 | 2026-09-17 | Preview fixes from the first real capture: a PDF linked on another site is no longer listed as the article's file; an ahead-of-print page's placeholder volume and issue are not proposed; the page's full publication date is kept; the page is read once when the panel opens. No permission change. |
| 1.3.0 | 2026-09-17 | Side panel replaces the popup: capture runs beside the article, and the reader confirms or corrects the DOI and bibliographic details before the receiver publishes the capture. New permission: `sidePanel`. Screenshots need refreshing. |
| 1.2.0 | 2026-09-17 | One capture also saves the article's PDF, supplements, audio and video. |

## Status

Not submitted. The public repository release (v0.2.0) waits for the security
audit required before publication.
