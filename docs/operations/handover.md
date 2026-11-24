# 交接：進行中的工作

寫給接手的 AI（Codex 或 Gemini／Antigravity）。每一項都是獨立的工作線。開工前先讀 [for-ai-maintainers.md](for-ai-maintainers.md) 的規矩；**推上 main、部署、套正式資料庫、改開關、開給其他人，每一步都先問站主**。

「分支」與「狀態」兩欄由交接人最後填寫；分支要用 `git branch -a` 確認還在。

站主的常駐要求（2026-11-24）：「整個網站都要可以自己獨立運轉」。所有工作線的驗收條件都包含：**不留下要人在某台電腦上手動跑的步驟**；網站的 AI 只用 Gemini。

**交接時唯一待執行的正式動作**：2026-11-25 台北 08:00 後，站主按 `d1-migrate`（先乾跑、再正式）——補記帳 0033～0038、套用 0039、0040。見 [owner-todo.md](owner-todo.md) 第 1 項。

---

## (a) 返回回到原來的清單

- **是什麼**：從首頁的清單（籌碼排行、成交金額、選股清單、資金地形點樓後的強勢股、自選股、庫存、搜尋、國外同類股…）點進個股頁，按返回要回到**剛才那個清單、同一個浮板、同一個排行、同一個捲動位置**；直接開網址進個股頁時才回大盤。頁面上的返回鍵與手機的「上一頁」手勢都一樣。
- **站主原話**：「我從籌碼成交金額點進去看某檔股票但我要返回看其他交金額大的股票他會直接回到主要大盤，不是回到我剛剛點進去股票頁面前的成交金額那頁」。站主說這是「舉例」：要修的是這一整類入口。
- **已知原因**：個股頁的返回鍵是寫死的連結（`web/stock.html` 的 `<a class="bd-back" href="minimal.html">`），一定是重新打開首頁；首頁的浮板狀態（`web/minimal.js` 的 `openPanel()`／`closePanel()`）沒有寫進網址、歷史紀錄或 `sessionStorage`。
- **分支**：＿＿＿＿
- **狀態**：＿＿＿＿
- **還缺什麼**：＿＿＿＿；iPhone 實測（首頁有即時行情輪詢，iPhone 會不會整頁還原沒驗過）。
- **怎麼驗收**：每個入口各點一次進個股、按返回鍵與手機上一頁各一次，回到原位置；直接開個股網址按返回回大盤。瀏覽器旅程測試要涵蓋。
- **要站主同意的**：返回鍵上的字要不要從「大盤」改掉（看得到的變更，要帶樣張問）；上線。

## (b) 資金版圖（AI 產業鏈）

決策與公司名單：[chain-map-companies.md](chain-map-companies.md)。

- **是什麼**：獨立一頁 `/chain-map`，12 層 → 環節 → 個股逐層展開，看 2021-01 起每個月資金在 AI 產業鏈哪裡（台股成交金額占比、台股法人買賣超、美股成交金額占比）。**視覺與互動照站主驗收過的樣品 v4 原封不動。**
- **站主原話**：「做得非常好，這個全部設計風格元件等等都非常好請完全實裝進去我的網站」；「AI 產業鏈資金版圖 我希望他之後會每天自動更新且不需要你的幫助」。
- **已上線（main）**：頁面 `web/chain-map.*`、`cloud/lib/chain-map*.js`；名單 `seeds/ai-chain-map.json`；資料表 `cloud/migrations/0039_chain_map.sql`（**正式庫還沒套**，套之前頁面顯示「尚未就緒」）；開關 `MARKET_ANA_CHAIN_MAP`（站主決定 `all`，核對 `cloud/wrangler.jsonc`）。
- **每日更新（已接上）**：`daily-update` 的「AI 產業鏈資金版圖：每月資金資料」一步，跑 `chain_map_flows.py build --d1 --r2`；開關 repository variable `MARKET_ANA_CHAIN_MAP_SCHEDULE=on`。美股用 Massive 全市場單日行情（每次最多 20 次呼叫，停擺後從最舊缺日往後補）；歷史起始資料在 R2 `market-ana-state/chain-map/bootstrap/stock_flows-2026-10.json`。各種狀態與重建方法寫在 `chain_map_flows.py` 檔頭。**驗收**：0039 套用後，隔一個交易日最新月份的數字有變。

| 子項 | 狀態 |
|---|---|
| 法人歷史 2021-01～2025-10 | 站主已同意正式執行；`institutional-backfill` 第一批 2026-11-24 晚已開始，每按一次接著補，約共 2 次（[data-gaps.md](data-gaps.md)）。限制：雲端股價只留約 1,270 個交易日，所以法人×收盤價在 2021-01～約 2021-08 補完後仍是空的 |
| 名單維護 | 已定案「Gemini 提名、站主同意」，**沒做**。現在的切換點：在 D1 `chain_map_definitions` 加一列 `source='owner'` 的新定義，排程就改用它。要做：Gemini 每季提名、站主在網站上按同意、連續幾天抓不到資料的那檔頁面標出來；提名與同意的畫面要先帶樣張問站主 |
| 首頁入口 | **未定**（現在要自己打網址） |
| 點公司連到個股頁 | **未定**；若要連，就屬於 (a) 的同一類入口 |
| 當月未過完的顯示 | **未定**。資料裡已有 `partial` 欄位，頁面目前不讀 |

- **要站主同意的**：三個未定題；名單提名畫面的樣張。

## (c) 部署排程器、套資料庫變更：已做成網頁按鈕

- **已上線**：`d1-migrate`（`d1_migrate.py`）、`deploy-cron-trigger`。站主操作見 [ops-buttons.md](ops-buttons.md)。
- **背景**：正式 D1 的記帳表 `d1_migrations` 只記到 0032，0033～0038 是直接執行過的。`d1-migrate` 先逐項核對再只補記帳、**絕不重跑**，然後套真正沒套的；不一致就拒絕。**補帳完成前不得執行 `wrangler d1 migrations apply`**，也不要手寫 SQL 補帳——一律用這個按鈕。
- **待觀察**：`d1-migrate` 第一次正式執行（2026-11-25 08:00 後）；`deploy-cron-trigger` 正式部署需要 `CLOUDFLARE_API_TOKEN` 有 Account › Workers Scripts › Edit，**沒驗證過**。
- **還沒有按鈕的**：`market-ana-eval`（`cloud/eval-worker/`）的部署；換推播金鑰（`cron-trigger/set-push-keys.mjs` 要本機登入 `gh`、`wrangler`）。

## (d) 60 天無活動的保活：已上線

- `workflow-keepalive`（`workflow_keepalive.py`）接在 `announcements`、`market-news` 後面，把被 GitHub 60 天規則停用的 workflow 重新啟用；不碰網站。2026-11-24 晚在 GitHub 上乾跑與正式都驗過（三支有排程的 workflow 重新啟用成功）。
- **還在定稿**：加每月心跳提交、自己的每週排程、必要 workflow 沒啟用就失敗。最終做法：＿＿＿＿
- **待觀察**：2026-11-25 08:00 `announcements` 跑完後，它要自己出現一筆綠勾。

## (e) 失敗與過期通知、設定頁「資料更新」、金鑰到期提醒

- **已上線**：設定（齒輪）→「資料更新」那一籤（開關 `MARKET_ANA_DATA_HEALTH=owner`，規則表在 `cloud/lib/data-health.js` 的 `RULES`）；異常／恢復推播由排程器送（`cron-trigger/wrangler.toml` 同名開關 `owner`，排程器 2026-11-24 晚已部署），**要等 0040 套用後才會送**。說明：[data-health.md](data-health.md)。
- **沒做**：金鑰到期提醒；每一步的 workflow 結果記錄（現在「綠勾但某一步失敗」只靠資料新舊與降級判斷）。
- **要站主同意的**：新的提醒畫面或推播內容。

## (f) 基金淨值缺口

- **已上線**：第二個每日抓取點（`announcements` 的 `fund-nav` 工作，每天 08:00 含週末）；執行摘要用白話寫缺口；手動補的步驟在 [`seeds/funds/README.md`](../../seeds/funds/README.md)。
- **沒有自動補**：找不到允許的歷史來源（站主 2026-11-17 定案：不打集保網站背後的資料介面、不解驗證碼）。若之後找到合法來源，新增抓取要先問站主。

## (g) 候選水位規則的紙上追蹤：**已決定停止**

- 候選規則 `cash-level-v3-candidate` 的紙上追蹤原本是 Claude 桌面 App 的排程任務 `market-ana-level-paper-track`（每個交易日 18:30）在這台電腦跑。和雲端的 `level-daily.yml`（產品規則 `cash-level-v2.1`）不是同一件事。
- **決定**：停止，沒有搬上雲端。已有紀錄留在這台電腦 `C:\Users\User\Desktop\Market--Ana\.scratch\strategy-validation-20261111\level-redesign\paper-log.jsonl`（不在 git 裡）。
- 站主要把那個排程任務關掉（[owner-todo.md](owner-todo.md) 第 3 項）。不需要分支。

## (h) 全站動態與元件設計升級

- **是什麼**：站主要求把原網站的動態效果與元件設計提升到資金版圖樣品的水準。
- 設計文件位置：＿＿＿＿（合併後填這一行）
- 既有的站主定案（版面、內容、文案、手勢、不放補充說明小字…）不因升級而作廢，要動到先問站主；每一頁都先給改版前後對照，站主同意才套。

## (i) `GLIACU` 的級別成立日（`class_launch`）

- `fund_compare.py` 的 `FUNDS["GLIACU"]["class_launch"]` 現在是 `2021-07-02`，只來自 Yahoo 奇摩基金（晨星資料，非一手），程式裡標了 `TODO 待核實`。
- **還缺**：找一手來源（基金公開說明書、集保頁面）核對；不同就改值並記出處。影響個股頁「成立以來」那一列的起點。

## (k) 只存在這台電腦、或只有原維護者工具才能用的東西

接手前要知道：下面這些**不在 git 裡**或**依賴已不存在的工具**，換電腦就沒有。

| 東西 | 在哪 | 影響 |
|---|---|---|
| 本機資料 `data/` | 站主電腦 `C:\Users\User\Desktop\Market--Ana\data\` | 雲端只有 R2 工作集；累積型原件（大戶持股週檔、期貨原件）壞了只能靠它重建；部分測試要它 |
| `.scratch/`、`.tmp/` | 同一個資料夾 | 舊規格、票、交接草稿、Gemini 登入檔腳本原件（腳本已抄進 [credentials.md](credentials.md)）、紙上追蹤紀錄 `paper-log.jsonl` 與 `paper_track.py` |
| `STATUS.md`、`ANALYSIS_PLAN.md` | 同一個資料夾，刻意不進 git | 進度沿革與站主歷次定案的原文；手冊已改寫進必要的部分 |
| `analysis.py` 的舊直播分析流程 | 約 164～175、294 行 | 依賴原維護者的影片工具，現在不能用；網站不靠它 |
| 指向不存在文件的註解 | `cloud/import_d1_seed.py` 第 3 行、`.github/workflows/agy-auth-probe.yml` 第 5 行 | 指到 `.scratch/` 裡的文件，照程式本身理解即可 |
| 寫死本機路徑的測試與工具 | `logs/check_*.py` 多支、`logs/fetch_analysis_data.py`、`test_daily_update.py`、`cloud/market_pack_daily.py` | 在別台電腦會失敗，見 [for-ai-maintainers.md](for-ai-maintainers.md) |

站主已列入待辦：把整個專案資料夾備份到外接硬碟或雲端硬碟（[owner-todo.md](owner-todo.md)）。

## (j) 全部功能開放給其他獲准使用者

- **站主原話（2026-11-24）**：「另外我要你把我所擁有的全部功能都開放給我本人以外都可以一起使用」；之後確認清單「全部都開」。
- **已開（`all`，核對 `cloud/wrangler.jsonc`）**：盯項 `MARKET_ANA_IMPACT_WATCH`、盤勢解讀 `MARKET_ANA_MARKET_BRIEF`、海外分時 `MARKET_ANA_INDEX_INTRADAY`、預測對帳 `MARKET_ANA_FORECAST_LEDGER`（改成三態，其他人唯讀）、富果即時行情 `MARKET_ANA_LIVE_QUOTES`（新；條款限本人私用，站主知道風險仍決定開）、資金版圖 `MARKET_ANA_CHAIN_MAP`。分時、五檔、內外盤比、60 分 K 早在 2026-11-18 就已開給所有核准的人。
- **仍只給站主**：
  - 主力分點 `MARKET_ANA_BROKER_BRANCHES`：程式已是三態，但「哪幾檔有分點」等於洩漏站主名單，要先決定抓取範圍；站主說「我希望我股票池的股票都有」，意思待問清楚。
  - 自選股關注 `MARKET_ANA_WATCHLIST_WATCH`：朋友版沒做（每人各自判斷、朋友自己的 Gemini 金鑰、每人推播、行情商額度）。
  - 管理頁、設定頁、站主私人資料。
- 行情商來函時怎麼關：[flags-and-schedules.md](flags-and-schedules.md)〈行情商來函或帳號被停時怎麼關〉。
- **分支／狀態**：＿＿＿＿
