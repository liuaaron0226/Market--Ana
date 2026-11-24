# 資料缺口與回補

## 1. 基金淨值缺口（三檔境外基金）

### 為什麼會有缺口

站上的境外基金（`BLKTENAU` 貝萊德世界科技 A10、`AGIFFVM` 安聯收益成長、`GLIACU` 摩根多重收益）每天由 `daily-update` 的「基金對照 0050…」那一步，從**集保結算所的開放資料**抓淨值與配息、從央行抓匯率。開放資料**只留最近約 4 個交易日**，所以排程連續漏抓超過約 3 個營業日，中間那幾天就再也抓不到，形成缺口。（每天抓到的抽取檔存在 R2 的 `funds/raw/`；歷史回補原件存在倉庫的 `seeds/funds/<代碼>/`。）

### 怎麼知道有缺口

1. GitHub Actions → `daily-update` 當天那筆 run：整體綠勾，但步驟「基金對照 0050：抓集保開放資料淨值、配息與央行匯率，存回 R2」是**紅叉**。
2. 點進那一筆 run 的 **Summary**，「基金對照資料」那一段 JSON 的 `checks` 底下，每檔基金一個清單：
   - `{"kind": "gap", "from": "日期", "to": "日期", "weekdays": N}` → **缺口**，這段要手動補。
   - `{"kind": "nav-conflict", ...}` → 同一天兩份數字不同（保留先到的那份），交給 AI 查。
   - `stale` 類 → 超過 6 個平日沒有新淨值，多半是來源晚發，先等一兩天。
   - `seed-missing` → 這檔還沒有歷史回補原件。
   - `sources` 裡某項 `"status": "failed"` → 當天來源下載失敗，隔天通常自己好。

### 手動補缺口（要用電腦瀏覽器；目前沒有自動補，見 handover.md (f)）

做法和第一次回補一樣：**用瀏覽器以人的速度打開集保「基金資訊觀測站」網頁、照畫面點選**，只記錄網頁自己收到的回應；不直接呼叫它的 API、遇到驗證碼就停。

1. 用電腦的 Chrome／Edge 打開（把 `<集保代碼>` 換成那一檔：`BLKTENAU`、`AGIF-FVM`、`GLIACU`）：
   `https://www.fundclear.com.tw/fund-basic-info?site=offshore&key=<集保代碼>&tab=fund-nav`
2. 按 `F12` 打開開發者工具 → **Network（網路）** 分頁。
3. 在網頁上點能涵蓋缺口的期間（例如「三個月」）。
4. Network 清單裡找到 `query-nav-value/picture` 那一筆 → **Response（回應）** → 全部複製。回應是一個 JSON 陣列，每筆有 `navDate`、`nav`。
5. 把**缺口那幾天**整理成 CSV，每行 `YYYY-MM-DD,淨值原字串`，由舊到新，最後一行也要換行。格式和既有的 `seeds/funds/<站上代碼>/nav-backfill.csv` 一樣，例如：
   ```
   2026-11-10,10.520000
   2026-11-11,10.310000
   ```
   （淨值照網頁原字串抄，不要四捨五入。）
6. 存成新檔 `seeds/funds/<站上代碼>/nav-backfill-2.csv`（已有 `-2` 就用 `-3`，依序）。**注意：資料夾用站上代碼**（`AGIFFVM`，沒有連字號）。
7. 算這個檔的 SHA-256：
   - Windows：`certutil -hashfile nav-backfill-2.csv SHA256`
   - Mac／Linux：`sha256sum nav-backfill-2.csv`
8. 打開同一個資料夾的 `backfill-manifest.json`，在 `files` 陣列最後加一筆（照既有那筆的欄位）：
   ```json
   {
     "file": "nav-backfill-2.csv",
     "kind": "nav",
     "page_url": "https://www.fundclear.com.tw/fund-basic-info?site=offshore&key=<集保代碼>&tab=fund-nav",
     "page_action": "淨值期間點「三個月」",
     "response_of": "POST /api/offshore/fund-basic/query-nav-value/picture",
     "read_at": "讀取時間（ISO 格式，例如 2026-12-01T10:00:00Z）",
     "format": "只留 navDate（改成 YYYY-MM-DD）與 nav 原字串，依日期由舊到新、每行一筆、結尾換行。",
     "rows": 筆數,
     "first": "第一天",
     "last": "最後一天",
     "sha256": "第 7 步算出來的值（小寫）"
   }
   ```
9. 兩個檔一起提交到 `main`（這兩個檔**不可以讓 Git 改換行**，`seeds/funds/.gitattributes` 已設定 `-text`；用 GitHub 網頁上傳也可以）。
10. 確認：下一次 `daily-update`，「基金對照 0050」那一步的 `checks` 裡那個 `gap` 不見了、步驟變綠。SHA-256 對不上時，程式會停在 `回補原件 … 的 SHA-256 和清單不符`，那一檔不會被算錯，改正後再跑即可。

程式怎麼合併（給 AI）：`fund_compare.load_seed()` 照 manifest 順序讀所有 `kind: nav` 的檔、逐檔核對 SHA-256，`merge_navs()` 依日期合併，同一天先到的為準（回補原件優先於每日抽取檔），所以補檔只會補沒有的日子。

## 2. 其他回補（都是 GitHub 網頁上的手動輸入）

| 回補 | 在哪 | 什麼時候用 | 怎麼做 |
|---|---|---|---|
| ETF 每日行情原件 | `daily-update` 的 `etf_raw_days` | ETF 的日 K 根數太少（雲端工作集只留最近幾天原件）。2026-11-16 站主同意 130 天 | `etf_raw_days` 填 1～300 的數字、**`dry_run` 取消勾選**、**`republish` 勾**（重新發布目前線上那一天）。它會向證交所／櫃買重抓原件，位元組雜湊要和行情庫當初記的相同才放回；一天都沒補回就整個 run 失敗、不發布。這一項不能乾跑 |
| 主力分點一檔 | `broker-branches`，`mode=backfill`、`code=代號` | 某檔副圖沒有資料 | 先 `dry_run` 勾著跑一次，再取消。一檔近三年約 2 萬多列；D1 一天寫入上限 10 萬列，一天最多補 2～3 檔 |
| 主力分點：自選與庫存中還沒補的 | `broker-branches`，`mode=backfill`、`code` 空白、`limit=2` | 新加了自選股 | 同上。每天排程（開關 `on` 時）本來就會自動補約 2 檔 |
| 主力分點：整理超過上限的分點 | `broker-branches`，`mode=trim` | 某檔分點太多 | 不連來源，只刪資料 |
| 事前預測歷史重播 | `forecast-replay` | 站主要看過去的重播參考成績 | 先 `plan_only` 勾著看要幾次 Gemini；真的跑要站主同意（吃 AI Pro 每週額度） |
| 晚間預測漏寫 | `forecast-ledger` 手動，`dry_run` 取消 | 當天晚間版沒寫成 | `date` 必須等於已發布資料的最後一天；晨間版過了 08:45 就不能補 |
| 公司重大訊息原件 | `announcements` 手動 | 某天早上 08:00 那班沒跑 | 直接 Run workflow；官方快照只留前一天，隔太久就補不回來 |

## 3. 只能靠這台電腦重建的（缺口，見 handover.md）

- 雲端工作集只有 R2 `state/` 一份，雜湊不符時還原會直接停。累積型原件（大戶持股週檔、期貨原件）壞了，目前只能用這台電腦的 `data/` 重建；這份本機資料新不新**待確認**。
- 某個交易日四班全部失敗、隔天才恢復時，前一天的資料會不會自動補進來：**待確認**（`daily-update` 的 `date` 可以手動填過去的日子，但發布有「不能比線上舊」的檢查，能不能補過去的日子沒有實測過）。
