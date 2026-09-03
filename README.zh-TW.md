# corpus-capture — 一鍵把目前這篇文章存進你自己的庫

在瀏覽器讀到一篇文章，按一下，把整頁（含圖表）打包成單一自含 HTML，送進你自己架的
收件端點。你已經登入的期刊 session 就是取得全文與高解析圖檔的憑據；擴充本身不登入、
不存帳密、不在背景蒐集。

它做的事和 SingleFile 同一類，多兩樣：**出版社設定檔**（用文件結構挑圖表，不用猜）與
**開放的入庫協定**（接收端由你自己實作）。

英文版見 [`README.md`](README.md)；協定見 [`docs/protocol.md`](docs/protocol.md)。

## 這個 repo 有什麼

| 路徑 | 內容 |
|---|---|
| `extension/` | MV3 擴充，也就是 producer |
| `profiles/capture_profiles.json` | 出版社設定檔註冊表，以及約束它的 `schema.json` |
| `python/corpus_capture/` | 接收端要用的函式庫：同一份註冊表、同一套圖表挑選、sidecar 契約 |
| `docs/protocol.md` | 線上契約：端點、envelope、sidecar、離線路徑 |
| `docs/receiver-reference.md` | 一百行左右的最小接收端範例 |
| `fixtures/` | 真實出版社頁面的骨架：保留結構，內文只留每段第一句 |
| `tests/` | 以上每一條的把關 |

## 安裝

1. `chrome://extensions` → 開「開發人員模式」。
2. 「載入未封裝項目」→ 選 `extension/` 資料夾。
3. 點擴充圖示 →「設定 API 位址與 token」，填入你的接收端點與 token。

**沒有內建預設端點。** 接收端是你自己架的，位址通常在私有網路上，寫死一個既對別人
沒用、也等於洩漏架設者的位置。位址必須是 https（localhost 可用 http）。

## 用法

在文章頁按圖示。popup 上方的徽章會顯示這個網站的支援狀態：

| 徽章 | 意思 |
|---|---|
| 已支援本網站之打包 | 這個出版社有設定檔，而且有 fixture 測試在擋回歸 |
| 通用模式 | 沒有專屬設定檔，用通用選擇器擷取；sidecar 會記 `profile=generic` |
| 未支援 | 已知有阻擋，徽章會寫出實測到的原因 |

按下按鈕後狀態列依序顯示：內嵌進度 → 打包結果（文章圖表數量與名稱、其餘標為裝飾的
張數）→ 收據 id → 入庫後給閱讀連結。popup 關掉沒關係，service worker 會繼續輪詢。

## 圖表是用結構挑的，不是用大小

實測過一次：用「檔案大到一定程度就算圖」去挑，結果拿到五張廣告（封面、banner、logo），
文章真正的三張圖表一張都沒有。大圖就只是大圖，只有它在文件中的位置能說明它屬於誰。

所以挑選規則是結構性的，三階：

1. 出版社自己標記的圖表元素（設定檔提供選擇器，例如 NEJM 的 `figure.graphic`）；
2. 文章容器內任何 `<figure>`；
3. `alt` 自稱是圖表的 `<img>`。

第三階有個容易寫錯的地方：NEJM 寫的是 `alt="Figure1"`，中間沒有空格，所以
`^(Figure|Table)\b` 比對不到——`e` 和 `1` 都是 word character，中間沒有邊界。
就這一個 case，差別是抓到三張圖還是零張。

打包時只序列化**文章容器**加上 `<head>`（後者是 metadata）。頁面其餘部分——側欄、
彈窗、行銷區塊、第三方 iframe——不是文章，不該進入一個聲稱自己是文章的檔案。

沒被列入 manifest 的圖片仍然會內嵌（維持頁面完整），但標記為 `decorative`，讓下游
不會把刊頭當成研究結果。

## 出版社設定檔

設定檔是**資料**，由接收端提供（`GET /api/v1/capture/profiles`），擴充啟動時抓取並
快取；抓不到就用快取，快取也沒有就用通用選擇器。一份資料同時驅動擴充、抓取腳本與
接收端的對應，不會有三份會各自漂移的複本。

`status` 是量測結果不是計畫：要有 fixture 測試通過才算 `supported`；`unsupported`
必須寫出實際觀察到的 `reason`。

要新增設定檔就對 `profiles/capture_profiles.json` 發 PR。想標成 `supported`，用
`tools/make_capture_fixture.py` 產一份骨架 fixture：保留 `<head>`、文章容器、圖表
元素與說明，其餘文字每一段只留第一句。

## 接收端是你的

- `POST /api/v1/intake/html`——bytes 加上這一頁自己宣告的東西，附 SHA-256，接收端
  必須自己重算驗證。回 `202` 與收據 id：bytes 收到了，但 artifact 還不存在。
- `GET /api/v1/intake/{receipt_id}`——後來怎麼了。
- `GET /api/v1/capture/profiles`——註冊表，純資料。

producer 只交出 bytes 與觀察，不交出身分：envelope 在結構上就放不進 DOI，這一頁對
自己的宣告改走 payload 旁邊的 sidecar，當作 producer 證據。這是哪一件作品、放在哪、
授權是什麼、能不能發表，都是瀏覽器判斷不了的事。

端點連不上時改存本機下載目錄 `corpus-capture/`，`.html` 與 `.json` 同一個檔名字幹。
4xx 不走這條路：伺服器拒絕這份 payload，換條路只是把同一個拒絕搬到後面。

## 隱私

- token 存在 `chrome.storage.local`（不同步），不會離開這台機器。
- 擴充只在你按下按鈕時讀取當前分頁，不背景收集。
- 圖片與樣式從擴充端帶 credentials 抓取，因為出版社常用 session cookie 擋高解析圖；
  這些請求只送到頁面本來就會請求的來源。
- 除了你自己設定的端點以外，不送到任何地方。

## 範圍

這個工具打包的是你本來就看得到的頁面，供你自己存檔。它不繞過任何存取控制、不爬站、
不批次下載。能留什麼、能不能再散布，取決於你和出版社的約定，不是這個工具。

fixture 是骨架：保留結構與 metadata，內文每段只留第一句，圖片換成 1×1 佔位圖；有
測試直接對 commit 進去的 bytes 檢查這件事。

## 授權

MIT，見 [`LICENSE`](LICENSE)。
