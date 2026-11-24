# 交接：現況與待辦

寫給接手的 AI。**接下來幾天由 Codex 維護，之後只剩站主本人與 Gemini（Antigravity）。** 所以：做不完的事要留下能讓站主或 Gemini 照做的步驟，不要留下只有你自己看得懂的半成品。

開工前先讀 [for-ai-maintainers.md](for-ai-maintainers.md) 的規矩。**推上 main、部署、套正式資料庫、改開關、開給其他人，每一步都先問站主。** 常駐要求：整個網站要能自己獨立運轉——不留下要人在某台電腦上手動跑的步驟；網站的 AI 只用 Gemini。

---

## A. 網站現在是什麼狀態（2026-11-24 深夜，台北）

### 進行中的事故：D1 寫入額度用完

- 正式資料庫 D1 撞到免費方案「每天 10 萬列寫入」上限，**台北 11-25 08:00（UTC 00:00）重置**。起因：同一天做了 `republish`，又做了主力分點整理（trim）。
- **讀取正常，所有寫入都失敗。** 每一個 API 請求都會先在 `api_hits` 寫一列（`cloud/lib/gate.js` 的限流 `consume()`），所以當晚一度大部分資料 API 回 503「暫時無法處理」。
- **已修補並上線**：commit `64d9960`（redeploy run 45406759106 成功）——限流在 D1 寫入失敗時改用記憶體限流；讀庫存時價格快取寫不進去也不再 503。正式站用站主帳號核對過：`/api/watchlist`、`/api/market/overview`、`/api/stocks/<代號>/analysis`、`/api/stocks/<代號>/live`、`/api/market-brief`、`/api/impact-watch`、`/api/indices/n225/intraday` 都回 200。**讀取已恢復；寫入仍要等 08:00。**
- **到 08:00 前**：使用者存不了自選股、庫存、畫線；23:00、06:30 兩班消息與 07:20 晨間預測**寫不進去**。之後能不能補發**沒有驗證過**；不補的話首頁消息停在 18:30 那一班，晨間預測是空的（記漏記）。
- **沒有驗證過**：08:00 前使用者存自選股、庫存、畫線失敗時，畫面上看到什麼（預期是頁面的錯誤訊息、不是空白畫面，但沒人實際看過）；開給朋友的六項功能在這段期間朋友那邊的樣子也沒看過。
- 額度用完期間的副作用：
  - 限流退成每個執行個體各自的記憶體限流，**比平常寬鬆**；朋友共用的報價額度可能被超過，富果可能回 429（盤中每分鐘盯價會漏）。富果來信抱怨或 429 一直出現：把 `MARKET_ANA_LIVE_QUOTES` 改成 `owner` 再 redeploy（做法同 [flags-and-schedules.md](flags-and-schedules.md)〈行情商來函或帳號被停時怎麼關〉）。
  - 一般的資料更新提醒、到價與觸發線推播**也會停**（`cloud/lib/watch-tick.js` 先寫資料庫才送）。沒收到推播不代表沒事。
  - 「寫入額度用完」那則推播只在排程器的 UTC 03:30／07:30／12:30／22:30 時段送（台北 11:30、15:30、20:30、隔天 06:30），都掛在週一到週五那一行排程上（台北 06:30 那班是週二到週六）：台北 20:30 以後才用完，要到隔天 06:30 才知道；週末用完就不會通知。
  - 排程器那一班執行失敗只看得到 Cloudflare 後台（Workers & Pages → `market-ana-cron-trigger` → Logs／Cron events），**不會寄信**。
- 站主決定**不買付費方案**（之後可能再考慮）。
- 實測：限流本身今天只寫了約 1,600 列（819 次請求、2 位使用者），不是主因；大戶是 `republish`／每日發布與主力分點的回補、整理。2026-11-24 晚已把 repository variable `MARKET_ANA_BROKER_BRANCHES_ROW_BUDGET` 設成 `12000` 暫時止血（C3）。

### 已在正式站（main）

- 保活第一版（`workflow-keepalive`）、本手冊、法人歷史回補 workflow（第一批當晚在跑；寫 R2 不寫 D1）、設定頁「資料更新」那一籤、基金淨值第二個抓取點、維運按鈕（`d1-migrate`、`deploy-cron-trigger`）、資金版圖每日建置那一步、六項功能開給所有核准的人（盯項、盤勢解讀、海外分時、預測對帳、富果即時行情、資金版圖；見 [flags-and-schedules.md](flags-and-schedules.md)）。
- **commit `64d9960`（同一批修補）**：
  - 資金版圖：D1 沒有資料時改從 R2 讀（`chain-map/current/flows.json`，bucket `market-ana-state`；Pages 有 R2 綁定 `STATE`）。正式站 `/api/chain-map` 回 `available: true`、`source: r2`、版本 `bootstrap-2026-10`，頁面有資料。
  - 資料更新狀態：每日行情改從版本名讀資料日；正式站 11 項都是正常；D1 寫不進去時排程器照樣在允許的時段送推播（一天最多 4 則），那一班排程會明確失敗；規則的 SQL 用真的遷移檔測過；基金缺口也納入。
  - `d1-migrate`：能補完「只套一半、每句都是 IF NOT EXISTS」的遷移。
  - `market-news.yml`：加了 `set -o pipefail`，D1 寫入失敗不再被藏起來。
  - 排程器（cron-trigger）：2026-11-24 深夜從 `64d9960` 部署（Version ID `2d5d8c62-eefb-47f3-bb34-6f2c17996d5f`），保活強化合併後又重新部署一次；**現在的 Version ID 看 Cloudflare → Workers & Pages → `market-ana-cron-trigger` → Deployments**（`2d5d8c62-…` 是上一版）。排程時段不變（5 個）。之後 `cron-trigger/` 或它打包進去的 `cloud/lib/data-health*.js` 有改，都要按 `deploy-cron-trigger`（[ops-buttons.md](ops-buttons.md)）。
- **返回回到原清單**：2026-11-24 深夜已併入 main（見 `git log` 的 “Back returns…”）並**已部署**（redeploy run 45410562195 成功）。正式站用站主帳號核對：個股頁有載入 `NavReturn`、返回鍵帶 `data-nav-back`、API 都 200。**正式站沒有實際點過返回流程，也沒有 iPhone 實測。** 合併後測試：JS 1365、返回旅程 163/163、`market_index` 75/75、`time_share` 103/103。
- **保活強化**：已併入 main（merge commit `19b6a65`；站主同意每月自動提交一次心跳）。GitHub 上乾跑、正式都綠：三支必要的 workflow（`daily-update`、`forecast-replay`、`level-daily`）和保活自己都回「照常呼叫啟用：成功」，並推了第一個心跳 commit `1f2f0fb`（只動 `ops/heartbeat.txt`），下一次心跳在 25 天後。`tests.yml` 第一次在 GitHub（Linux）跑綠：排程器 19、JS 1353、Python 2216（run 45414352113）。`cloud/migrations/0041_ops_runs.sql` 已在 main，明天 `d1-migrate` 會一起套。
- **設計升級（首頁、個股頁、庫存）**：2026-11-24 深夜已併入 main 並部署（見 `git log` 的 “Merge site design upgrade”）。詳見 C8。
- GitHub 上的 `main` 與 `release/core-v1` 內容相同；站主電腦上沒有未推送的工作；**沒有未合併的分支**（設計與保活的分支還留在 GitHub，但都已合併）。

---

## B. 明早 8 點後第一件事：`d1-migrate`

站主版步驟在 [owner-todo.md](owner-todo.md) 第 1 項。給 AI 的重點：

1. Actions → `d1-migrate` → Run workflow，全部預設（乾跑）。摘要應該列**四步**：
   - 補記帳 0033～0038（只補記帳表，不重跑）；
   - 補完 **0039**——它**只建了一半**：11-24 晚手動套用時被寫入額度切斷，只有 `chain_map_definitions` 那張表存在。重跑是安全的，每一句都是 `IF NOT EXISTS`；
   - 套用 0040（資料更新提醒）；
   - 套用 0041（`0041_ops_runs.sql`，保活紀錄）。
   （2026-11-24 深夜的乾跑 run 45408272724 是綠勾、列了前三步——當時 0041 還沒進 main。）
2. 再跑一次，取消勾選 `dry_run`。
3. 成功＝綠勾、摘要寫「資料庫已是最新」；再按一次乾跑會說沒有要做的事。
4. 之後：資金版圖頁現在已經從 R2 讀到資料（樣品那一版，到 2026-10）；下一次 `daily-update` 正式發布（台北 18:00）會寫出第一份 D1 的完整資料。0041 套用後，設定頁「資料更新」那一籤多一列「排程保活」。
5. **0040 沒套之前，資料更新提醒一則都不會送**——連「寫入額度用完」那則也不會：`cloud/lib/data-health-alert.js` 約 99～115 行在檢查寫入之前就因為沒有 `data_health_alerts` 表回 skipped／no_table。所以按 `d1-migrate` 也就是打開提醒的那一步。08:00 後第一個時段可能收到「資料已恢復」之類的通知，是正常的。程式修正見 C14。
6. **補帳完成前不得執行 `wrangler d1 migrations apply`。**

---

## C. Codex 幾天內做得完的（依優先順序）

每一項：分支／commit、做到哪、還缺、怎麼驗收、要不要站主同意。

### C1. 08:00 之後的驗收
- **做到哪**：等時間到。
- **要驗**：`d1-migrate`（B，四步）；設定頁出現「排程保活」；網站寫入恢復（存一檔自選股試試）；正式站實際點一次「從清單進個股再返回」；資金版圖頁在 18:00 發布後改讀 D1（`/api/chain-map` 的 `source` 不再是 `r2`）；資料更新推播會送；08:00 `announcements` 跑完後 `workflow-keepalive` 自己出現一筆綠勾。
- **同意**：`d1-migrate` 正式執行由站主按。

### C2. 限流不要每個請求都寫 D1
- **還缺**：`cloud/lib/gate.js` 的 `consume()` 改成先用記憶體、或分批／抽樣寫；資料更新狀態多一項「今天 D1 寫入用量」（規則表在 `cloud/lib/data-health.js` 的 `RULES`）。
- **注意**：本機旅程測試伺服器放寬的 `rate` 物件少了幾個 bucket，限流的 INSERT 綁不上參數（`Provided value cannot be bound to SQLite parameter 4`），修補之後會**默默**退成記憶體限流。正式站所有 bucket 都有定義所以沒影響，但這表示設定錯的 bucket 不再大聲失敗；重做限流時要讓「缺 bucket」明確報錯。
- **驗收**：JS 測試＋實際請求後 `api_hits` 寫入量下降；設定頁看得到用量。
- **同意**：上線要問（設定頁多一列是看得到的變更）。

### C3. 主力分點的批次預算要算「寫入次數」，不是列數
- **背景**：D1 的寫入額度算的是列寫入×（1＋索引數）＋刪除，還要扣掉當天其他工作的用量。相關：`broker_branches.py` 約 69、120、691 行；遷移 0036 有複合主鍵索引；`broker-branches.yml` 預設一次兩檔。
- **站主想要**分點資料涵蓋整個網站股票池（約 350 檔）。一檔約 2.5 萬列，等於 1,700 萬～2,600 萬次寫入，免費額度要一年以上。**待站主決定：升級付費方案後才做，或縮到自選與庫存。維護者不得自己開始這個回補。** 範圍還沒變成公開的股票池前，`MARKET_ANA_BROKER_BRANCHES` 維持 `owner`（「哪幾檔有資料」會洩漏站主名單）。
- **現況**：`MARKET_ANA_BROKER_BRANCHES_ROW_BUDGET=12000`（2026-11-24 晚設定的止血值）。
- **驗收**：批次跑完當天 D1 寫入量在預算內。
- **同意**：擴大範圍、改開關。

### C4. 保活強化：觀察
- **狀態**：已併入 main（`19b6a65`），驗證見 A。
- **還要看**：每週自動那一班（週一 03:17 UTC＝台北週一 11:17）；08:00 `announcements` 之後那一班；25 天後第二個心跳 commit；`d1-migrate` 套 0041 後設定頁的「排程保活」。
- **同意**：不需要（站主已同意每月心跳）。

### C5. ~~`market-news.yml` 管線沒有 `pipefail`~~（已完成，`64d9960`）

### C6. 審查發現、還沒修的
- 每日發布會重送沒變的列；額度檢查沒算索引、刪除與其他工作（`cloud/import_d1_seed.py` 約 256、265、524 行；`watch_news.py` 約 521 行每次重寫全部英文簡稱）。
- 遇到額度錯誤的處理不一致：`daily_update.py` 約 1128 行吞掉清理失敗；`import_d1_seed.py` 約 454 行對額度錯誤重試三次；資金版圖建置在美股抓取前被 D1 拒絕時的訊息。
- `d1_migrate.py` 約 425～455 行補帳核對索引與觸發器時只比名稱。

### C7. 返回回到原清單
- **是什麼**：從首頁任何清單（籌碼排行、成交金額、選股清單、資金地形的強勢股、自選股、庫存、搜尋、國外同類股…）點進個股頁，返回時回到同一個清單、同一個浮板與排行、同一個捲動位置；直接開網址進個股頁才回大盤。返回鍵與手機上一頁一樣。站主 2026-11-24 指定要修整類入口，不只一處。
- **狀態**：2026-11-24 深夜已併入 main 並部署（redeploy run 45410562195）；測試與正式站核對見 A。
- **還缺**：正式站實際點一次返回流程（還沒人點過）；站主 iPhone 實測。
- **設計線量測到的後續**：個股頁自己的狀態（展開的類別、選中的事實分頁）離開再回來不會還原；首頁「籌碼選股」清單那個入口沒有量測。
- **待站主決定**：要不要放寬 `Cache-Control: no-store`，讓瀏覽器的整頁快取能用（安全上的取捨寫在 `cloud/lib/gate.js` 約 936 行）；返回鍵上的字要不要改（看得到的變更，帶樣張問）。

### C8. 設計升級（首頁、個股頁、庫存）
- **狀態**：2026-11-24 深夜已併入 main 並部署（見 `git log` 的 “Merge site design upgrade”；站主確認過）。合併後在有 `data/` 的檢出測試：JS 1365；旅程 返回 163/163、time-share 103/103、drawing 420/420、settings 160/160、market index 75/75、margin use 25/25、chain map 45/45、portfolio 結束碼 0。
- **還缺**：站主在**正式站用 iPhone** 實看，第一項：選中的類別鍵要有白色浮起框。設定、申請、管理、Gemini 這幾頁還沒接上設計層。完整清單、通過標準、真機檢查在倉庫根目錄 `DESIGN_UPGRADE.md` 第 10 節。
- **退回做法**：拿掉 `web/minimal.html`、`web/stock.html`、`web/portfolio.html` 各兩行引用，和 `cloud/lib/build-page.js` 裡那兩個檔名，然後 redeploy。
- 設計的動畫層讓「關閉／切換類別」的手指點擊延遲 140 ms，旅程測試要跟著等。
- **規矩**：站主既有的介面定案不因升級作廢；其他頁之後也是一頁一頁、先給前後對照、站主同意才套。
- **同意**：其他頁每一頁都要。

### C8b. 既有的旅程失敗（和今晚的改動無關）
- `cloud/test/browser_level_journey.py` 結束碼 1，卡在 `waiting for get_by_text("目前股票水位偏低")`。有沒有設計層都一樣失敗。原因沒查。

### C9. 補發事故中遺失的消息與晨間預測
- 先查 R2 裡有沒有那幾班的原件，再決定能不能補發。沒驗證前不要承諾站主。

### C10. 法人歷史回補
- 站主照 [owner-todo.md](owner-todo.md) 第 2 項繼續按，到摘要寫補完（約共 2 次正式執行）。
- 之後要決定股價保留天數：雲端只留約 1,270 個交易日，所以 2021-01～約 2021-08 的「法人×收盤價」補完仍是空的。

### C11. 資金版圖的未定事項
- 首頁入口、點公司連到個股頁（用返回回原清單那個模組）、當月未過完的顯示（資料裡已有 `partial` 欄位，頁面不讀）——都要問站主。
- 名單維護「Gemini 提名、站主同意」：已定案、沒做。現況與手動改法見 [chain-map-companies.md](chain-map-companies.md)。
- 已知名單修正：`PSTG` → `P`（Everpure，程式已處理代號）、`MOD` 改名 Modexus Solutions。

### C12. 自選股關注的朋友版
- 沒做：要每人各自的判斷、朋友自己的 Gemini 金鑰、每人推播、行情商額度。`MARKET_ANA_WATCHLIST_WATCH` 維持 `owner`。

### C13. 設定頁旅程測試用隨機埠
- 曾抽到瀏覽器禁用的埠（2049，`ERR_UNSAFE_PORT`）；要排除不安全的埠。

### C14. 額度用完時會出錯的小地方（當晚沒修，除非另有說明）
- **資料更新提醒在沒有表時直接跳過**：`cloud/lib/data-health-alert.js` 約 99～115 行，`no_table` 時應該丟錯，或先做寫入測試再判斷有沒有表，否則 0040 沒套時連「寫入額度用完」都不會通知。
- ~~資金版圖 D1 讀取錯誤變 503~~（已修，2026-11-24 深夜：`cloud/lib/chain-map.js` 的 D1 讀取錯誤不再蓋掉 R2 的資料，名單讀不到時退回基準版）。
- **資金版圖寫 R2 失敗沒聲音**：`chain_map_flows.py` 約 645～670 行只有警告或完全沒提示。
- **60 分 K 快取寫入失敗被吞掉**：`cloud/lib/hourly-candles.js` 約 64～78 行；額度用完時每次請求都重新向富果要歷史資料。
- **排程器自己的失敗沒有通知**：只在 Cloudflare 後台 Logs 看得到。

### 小項（有空就做）
- `GLIACU` 的級別成立日：`fund_compare.py` 的 `FUNDS["GLIACU"]["class_launch"]` 是 `2021-07-02`，只來自 Yahoo 奇摩基金（非一手），標了 `TODO 待核實`。找基金公開說明書或集保頁面核對。

---

## D. 幾天內做不完、之後只能靠站主或 Gemini 的

| 事項 | 為什麼做不完 | 之後怎麼辦 |
|---|---|---|
| 主力分點回補整個股票池 | 免費寫入額度要一年以上（C3） | 待站主決定（付費方案後才做，或縮到自選與庫存）；維護者不得自己開始 |
| `CLOUDFLARE_API_TOKEN` 有沒有 Workers Scripts：Edit | 只有站主能在 Cloudflare 看 | 站主照 [owner-todo.md](owner-todo.md) 第 4 項查；`deploy-cron-trigger` 若報驗證錯誤就是這個原因 |
| 金鑰到期提醒與到期日清單 | 到期日只能站主到各家網頁看 | 站主照 [owner-todo.md](owner-todo.md) 第 4 項填 |
| 基金淨值缺口自動補 | 找不到允許的歷史來源（站主定案：不打集保背後的資料介面、不解驗證碼） | 手動補：[`seeds/funds/README.md`](../../seeds/funds/README.md) |
| Gemini 登入檔過期時重做 | 要 WSL＋瀏覽器 | [credentials.md](credentials.md)〈重做 Gemini 登入檔〉 |
| 需要這台電腦 `data/` 或 `.scratch/` 的事 | 不在 git 裡 | 站主 Google 雲端硬碟資料夾「Market-Ana 備份 2026-11-24」有兩份筆記（`STATUS.md`、`ANALYSIS_PLAN.md`）與精簡版 `.scratch`（分成 3 個檔，附一份說明怎麼接回去）；**`data/`（約 9 GB）與 `.tmp/`（約 7 GB）沒有備份** |
| 候選水位規則的紙上追蹤 | 已決定停止，沒有搬上雲端 | 站主關掉 Claude 桌面 App 的排程任務 `market-ana-level-paper-track`；舊紀錄 `paper-log.jsonl` 在 `.scratch/strategy-validation-20261111/level-redesign/`（不在 git） |

### 只存在這台電腦、或依賴原維護者工具的東西

| 東西 | 影響 |
|---|---|
| `data/`、`.scratch/`、`.tmp/`、`STATUS.md`、`ANALYSIS_PLAN.md` | 不在 git。`data/` 是累積型原件（大戶持股週檔、期貨原件）唯一的重建來源 |
| `analysis.py` 約 164～175、294 行的舊直播分析流程 | 依賴原維護者的影片工具，現在不能用；網站不靠它 |
| `cloud/import_d1_seed.py` 第 3 行、`agy-auth-probe.yml` 第 5 行的註解 | 指到 `.scratch/` 裡不存在的文件；照程式本身理解 |
| `logs/check_*.py`、`logs/fetch_analysis_data.py`、`test_daily_update.py`、`cloud/market_pack_daily.py` | 寫死本機路徑，別台電腦會失敗（[for-ai-maintainers.md](for-ai-maintainers.md)） |
| `cloud/eval-worker/` 的部署、換推播金鑰（`cron-trigger/set-push-keys.mjs`） | 還沒有網頁按鈕，要本機登入 `wrangler`／`gh` |
