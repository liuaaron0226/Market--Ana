# 接手演練缺口報告（dry run）

- 演練日：2026-11-24；對象：`main` @ `fb93c3a`（乾淨複本，沒有 `data/`、`.scratch/`、`STATUS.md`、`ANALYSIS_PLAN.md`）。
- 環境：Linux、Node v22.23.5、npm 10.9.9、Python 3.13.18（workflow 用的是 3.11）、PyYAML 6.0.3 與 requests 2.34.2 原本就裝著。
- 全程唯讀：沒改產品程式、沒觸發 workflow、沒呼叫需要金鑰的服務、沒部署。
- 演練當下 `main` 裡沒有 `docs/`、`AGENTS.md`、`GEMINI.md`、`CLAUDE.md`；「不綁 Claude 的操作手冊」還沒進 `main`，以下缺口有一部分會被它補掉。

## (a) 一句話結論

**測試跑得起來，但只能靠猜指令，而且跑不到基準的數字**：倉庫沒有任何一份文件寫測試指令；照 `cloud/package.json` 與 README 裡唯一一句 `python -m unittest -v` 推出來後，node 是 1244 過／1 個檔載入失敗，Python 是 2393 個、failures=1、errors=17，和基準（1282 全過；2440、failures=10、errors=1）對不上，差異幾乎都來自沒進版控的資料檔。瀏覽器旅程測試在乾淨環境一組都跑不起來。

## (b) 缺口清單（依嚴重度）

### 1. 沒有任何說明這個網站的文件；README 講的是另一件事，還指向不存在的檔案

- 卡在哪：第 1 步，一打開 README。
- 實際情況：`README.md:1-56` 整份在講 YouTube 直播分析工具，結尾寫「沒有提交、推送或部署」（`README.md:56`），完全沒提台股網站、Cloudflare、GitHub Actions。`README.md:7` 與 `README.md:10` 連到 `STATUS.md`、`ANALYSIS_PLAN.md`，兩個檔都被 `.gitignore:57-58` 排除；`README.md:8-9,11` 連到的 `reports/` 也被排除（`.gitignore:17`）。
- 影響：題目給的基準「寫在 STATUS.md」——接手者看不到 STATUS.md，連基準是多少都不知道。
- 文件應補：README 開頭換成「這是台股分析網站；每日更新由 `.github/workflows/daily-update.yml` 跑，前端在 `web/`，雲端在 `cloud/`，排程觸發在 `cron-trigger/`；操作手冊在 `docs/handover/…`」，並把測試基準數字寫進版控裡的檔案。

### 2. Gemini 登入檔（`AGY_LOGIN_BUNDLE`）怎麼做、過期怎麼換，倉庫裡找不到

- 卡在哪：AI 判讀停擺時的處理。
- 實際情況：八支 workflow 用這個 secret；格式只能從 `.github/workflows/agy-auth-probe.yml:40` 反推（base64 的 tar.gz，解到 HOME）。做法與風險寫在 `.scratch/g4-gemini-runner/CI-AUTH.md`（`.github/workflows/agy-auth-probe.yml:5`），不在倉庫。
- 更糟的是「還原登入檔」那幾步都是 continue-on-error（見 (d) 第 4 點），登入過期時 run 仍顯示成功，網站只是悄悄改用程式套句或沒有新解讀。
- 文件應補：「在哪台機器用哪個指令登入 agy、打包哪個資料夾、`gh secret set AGY_LOGIN_BUNDLE` 怎麼下、多久會過期、用 `agy-auth-probe` 手動按一次確認」。

### 3. 金鑰沒有清單：名稱、放在哪三個地方、各自要什麼權限、什麼時候到期

- 卡在哪：第 1 步「有哪些金鑰」。
- 實際情況：名稱只能從 workflow 的 `secrets.*` 逐一抄（清單見 (d) 附錄 C）。放的地方有三處，只在註解裡零星提到：GitHub Secrets、Pages 專案機密、cron-trigger Worker 機密（`cron-trigger/wrangler.toml:26-27`、`cloud/wrangler.jsonc:12-13`、`cron-trigger/set-push-keys.mjs:28-30`）。`CLOUDFLARE_API_TOKEN` 要哪些權限沒有寫（只能從 `.github/workflows/daily-update.yml:113-124` 猜至少要 D1 與 Pages）；`GITHUB_DISPATCH_TOKEN` 只寫要「Actions: write」（`cron-trigger/worker.js:3`），沒寫到期日——它過期時 Cloudflare 的準時觸發全部失效。
- 文件應補：一張表「金鑰名稱／放在哪（GitHub、Pages、Worker）／用途／最小權限／到期日／怎麼換」，不寫值。

### 4. node 測試：一個檔案因為缺 `cloud/.stock-pack.json` 整個載入失敗

- 卡在哪：第 2 步。
- 指令與錯誤：`cd cloud && npm test` → `not ok 39 - test/intraday-client.test.js`，`Error: ENOENT: no such file or directory, open '…/cloud/.stock-pack.json'`（`cloud/test/intraday-client.test.js:17`）。
- 原因：這個檔由每日流程產生、放在 R2 工作集（`state_sync.py:51`），不在版控，也沒有測試用的樣本。
- 文件應補：「node 測試要先有 `cloud/.stock-pack.json`；沒有 R2 金鑰時這個檔的 15 個測試跑不了，屬於已知差異」，或是把測試改讀 `cloud/test/fixtures/` 的樣本（屬程式修改，本次沒動）。

### 5. Python 測試：17 個錯誤裡 14 個是缺本機 `data/`，3 個是寫死的 Windows 路徑

- 卡在哪：第 2 步。
- 指令與錯誤：`python3 -m unittest`（根目錄）→ `Ran 2393 tests … FAILED (failures=1, errors=17, skipped=56)`。
  - 4 個模組匯入就失敗：`live_server.py:76` 在匯入時讀 `data/listed-catalog/raw/twse-isin-listed-20261108.meta.json`（`test_candidates_api`、`test_listed_catalog`、`test_live_server_funds`、`test_screens_api`）。
  - 10 個缺 `data/` 底下的檔案（`data/otc-catalog/raw/…`、`data/stock-history/stock-history.sqlite`、`data/fundamentals-history/meta.json`）。
  - 3 個是 `ValueError: … is not in the subpath of 'C:/Users/User/Desktop/Market--Ana'`（`logs/fetch_analysis_data.py:14` 寫死根目錄，`test_fetch_analysis_current_month`）。
  - 1 個失敗只在非 Windows 出現：`test_etf_raw_restore.py:72`，預期 `['2026-11-04', '2026-11-05']`、實得多一筆 `'2026-11-06'`。
  - 另有 56 個因「不在本機」被跳過。
- 文件應補：「乾淨 clone 沒有 `data/` 時的預期結果是 failures=1、errors=17、skipped=56；要對上基準得先 `python state_sync.py restore --target .`（需要 R2 金鑰）」，並註明基準是在 Windows、有 `data/` 的機器上量的。

### 6. 測試指令與相依套件沒有寫，三處要自己試

- `cloud/test/*.py`（14 個檔）用 `python3 -m unittest discover -s cloud/test` 會直接失敗：`ImportError: 'test_stock_history' module incorrectly imported`（根目錄也有同名檔）。只能一個一個跑：`python3 -m unittest cloud/test/test_xxx.py`。
- `cloud/test/kchart-gestures.test.cjs`（23 個測試）不在 `npm test` 的樣式 `test/**/*.test.js` 裡（`cloud/package.json:6`），要另外跑；`cron-trigger/` 的 16 個測試沒有 `test` script（`cron-trigger/package.json`）。
- 7 個測試檔 `import yaml`（例如 `test_plain_daily_workflow.py`），`requirements.txt` 與 `requirements-daily.txt` 都沒列 PyYAML；旅程測試要的 `playwright`、設計工具要的 `Pillow` 也沒列。這次環境剛好裝著 PyYAML 才沒撞到。
- 文件應補：一段「跑測試」：四條指令（cloud node、cjs、cron-trigger、根目錄 Python、cloud Python 逐檔）加一份 `requirements-dev.txt`（PyYAML、playwright）。

### 7. 瀏覽器旅程測試在乾淨環境一組都跑不起來

- 指令與錯誤：`pip install playwright==1.63.0` 後 `python cloud/test/browser_terrain_journey.py`、`python cloud/test/browser_time_share_journey.py` 都以 `Page.goto: net::ERR_CONNECTION_REFUSED` 結束。真正原因在前面：`journey-server.mjs` 起不來（`Cannot find module '…/cloud/functions/_market-pack.js'`），因為要先 `node build.mjs`，而 `build.mjs` 需要 `web/analysis-data.js`、`cloud/.stock-pack.json`、`.screens-pack.json`、`.compare-pack.json`、`.data-status.json`（`cloud/build.mjs:57-108`），全部不在版控。
- 16 支旅程裡 15 支會先跑 `build.mjs`，多數還把截圖寫到 `.scratch/` 底下。
- 文件應補：「旅程測試要先還原 R2 工作集再 `node build.mjs`；沒有金鑰時不能跑」，以及安裝 Python 版 playwright 與瀏覽器的指令。

### 8. 沒有任何 workflow 會跑測試

- `.github/workflows/` 的 13 支都沒有 `npm test`、`unittest` 或 `pytest`。測試只在站主電腦上、由 Claude 在對話裡跑。接手後改程式不會有任何自動檢查。
- 文件應補：明講「推到 main 前要自己在本機跑測試」；長期應加一支 push 觸發的測試 workflow（屬程式修改，本次沒動）。

### 9. 退回上一版：資料會自動退，網頁不會，手動怎麼退沒有步驟

- `daily_update.py:1154-1155` 寫明：部署或驗證失敗時 D1 自動切回上一版，但「Pages 若已上傳新版，回退要另用 Pages 的部署回退（不自動做）」。手動指令只在 `cloud/import_d1_seed.py:9,30` 的說明裡。程式改壞要退版時的順序只有一句註解（`.github/workflows/daily-update.yml:5`：先退 Worker）。
- 文件應補：「退資料：`python cloud/import_d1_seed.py rollback`；退網頁：Cloudflare Pages 後台 Deployments 選上一版 Rollback；退程式：先 revert `cron-trigger`，再 revert workflow」。

### 10. 部署指令散在三個檔的第一行註解，cron-trigger 的找不到

- 網站：`cloud/wrangler.jsonc:1`；登入檢查 Worker：`cloud/eval-worker/wrangler.jsonc:1`；資料表遷移只有 `cloud/import_d1_seed.py:4` 一句「不在本工具內」。`cron-trigger/` 沒有任何一句寫怎麼部署（只能猜是在該目錄 `npx wrangler@4 deploy`），而改排程時間一定要重新部署它。
- 文件應補：「三個可部署的東西各自的指令與先後順序；新的 migration 檔要先 `wrangler d1 migrations apply market-ana-accounts --remote`」。

### 11. 功能開關分兩處，沒有總表

- GitHub repository variables 13 個（見附錄 C）控制排程要不要真的寫；Pages 變數 13 個在 `cloud/wrangler.jsonc:10-51`。GitHub 那邊現在各是開還是關，倉庫裡看不到。
- 文件應補：一張「開關名稱／放哪／目前值／關掉的後果」表。

### 12. 程式註解大量指向看不到的規格與對話

- 190 行、145 個檔案提到 `.scratch/…`（規格、資料約定、票號）；「本人 YYYY-MM-DD『…』」式的決定出處指向 `ANALYSIS_PLAN.md`。接手者讀得懂程式在做什麼，但查不到「為什麼這樣定」。清單見 (d) 第 3 點。
- 文件應補：把仍然有效的規格（至少 `daily-update-design.md`、`forecast-ledger/spec.md`、`stock-analysis-contract-v2.md`、`market-news/consensus.md`、`impact-watch/spec.md`、`CI-AUTH.md`）去掉個人資訊後搬進 `docs/`。

## 第 1 題逐項評分

| 問題 | 評分 | 實際要去哪裡看 |
|---|---|---|
| 每天怎麼更新 | 要自己翻程式 | `.github/workflows/daily-update.yml:1-9`（檔頭註解）、`cron-trigger/worker.js:20-40`（哪個時段觸發哪支）、`cron-trigger/wrangler.toml:6-17` |
| 怎麼部署 | 要自己翻程式；cron-trigger 找不到 | 見缺口 10 |
| 出事要看哪裡 | 要自己翻程式 | GitHub Actions 該次 run 的摘要（`daily-update.yml:555`）與 artifact `daily-update-log-<日期>`（`daily-update.yml:616`）；Cloudflare 端只知道 `cron-trigger/wrangler.toml:31-32` 開了 observability |
| 怎麼重跑 | 要自己翻程式 | `daily-update.yml:19-60` 的手動輸入：`dry_run`（預設 true，要正式跑得改成 false）、`redeploy`、`republish`；說明在 `daily-update.yml:6-7` |
| 怎麼退回上一版 | 資料：要翻程式；網頁：找不到 | 見缺口 9 |
| 有哪些金鑰、放在哪 | 名稱要翻程式；製作方法、權限、到期日找不到 | 見缺口 2、3 與附錄 C |

## (c) 測試結果與基準對照

| 項目 | 指令（在乾淨複本） | 結果 | 耗時 | 基準 | 差異 |
|---|---|---|---|---|---|
| node 安裝 | `cd cloud && npm install` | 成功，沒有任何相依套件 | <1 秒 | — | 會產生沒被忽略的 `cloud/package-lock.json`，別誤提交 |
| node 主測試 | `cd cloud && npm test` | 1245 個：1244 過、1 失敗（結束碼 1） | 28 秒 | 1282 全過 | 失敗的是 `intraday-client.test.js` 整檔載不進來（缺 `.stock-pack.json`，內含 15 個測試） |
| node 手勢測試 | `cd cloud && node --test --no-warnings test/kchart-gestures.test.cjs` | 23 個全過 | <1 秒 | 同上 | 不在 `npm test` 內 |
| cron-trigger | `cd cron-trigger && node --test --no-warnings` | 16 個全過 | <1 秒 | 不明 | — |
| Python 安裝 | `pip install -r requirements-daily.txt` | 只有 requests，環境已有 | — | — | PyYAML 沒列（環境剛好有） |
| Python 根目錄 | `python3 -m unittest` | 2393 個：failures=1、errors=17、skipped=56（結束碼 1） | 43 秒 | 2440：failures=10、errors=1 | 見缺口 5 |
| Python cloud | `python3 -m unittest cloud/test/test_*.py`（逐檔 14 次） | 9 檔跑了 151 個（errors=5，都在 `test_listed_stock_build.py`：缺 `.scratch/chart-screening-workbench/stock-analysis-contract-v2.example.json` 與資料庫）；5 檔匯入失敗（缺 `data/`） | 2 秒 | 不明 | `discover` 不能用（缺口 6） |
| 瀏覽器旅程 | `python cloud/test/browser_terrain_journey.py`、`browser_time_share_journey.py` | 兩組都失敗（結束碼 1） | 各約 20 秒 | 不明 | 缺建置產物（缺口 7） |

對基準的推算（是推測，倉庫沒有寫基準怎麼算）：

- node：1244 + 15（`intraday-client` 載得進來時）+ 23（`.cjs`）= 1282，剛好等於基準。所以基準應該是「`npm test` 加 `.cjs`、不含 cron-trigger、而且機器上有 `.stock-pack.json`」。
- Python：我的 2393 少了 4 個匯入失敗的模組，補回去落在 2440 附近說得通；所以基準應該是根目錄 `python -m unittest`、不含 `cloud/test`。基準的 failures=10、errors=1 和我的 failures=1、errors=17 **是不同的一批**：我這邊的錯誤來自缺 `data/`，基準那 10 個失敗在乾淨環境多半被跳過或沒觸發，我無法從倉庫得知是哪 10 個。
- 沒做的：沒有還原 R2 工作集（需要金鑰），所以沒辦法在「有資料」的狀態重現基準；沒用 Python 3.11 重跑。

## (d) 清單

### 第 3 點：只有 Claude 或站主電腦才用得上的東西

**依賴 Claude 技能、`.claude/` 或某次對話**

- `analysis.py:167`：到 `~/.claude/plugins/cache/claude-video/watch` 找 Claude 的 `/watch` 技能腳本；`analysis.py:171,373` 與 `README.md:19` 同一件事。沒有 Claude 時影片轉錄功能不能用（和網站每日更新無關）。
- `.gitignore:65-66`：`.claude/settings.local.json`；`.gitignore:22`：`.taste-skill/`。
- `.gitignore:55-58`：`STATUS.md`、`ANALYSIS_PLAN.md` 因為「含本人原話與 session UUID」不入庫——接手狀態本身只存在站主電腦。
- 指向某次對話的編號：`cloud/wrangler.jsonc:20`、`MARKET_METHOD.md:24`、`forecast_experience.py:9`、`logs/astra-20261107-ticket06-api-receipt.md:3`。
- 設計改版代號（Claude worktree 名稱）：`cloud/lib/build-page.js:13`、`cloud/test/build-page.test.js:1`、`logs/check_cloud_stock.py:34`。
- 文件與註解裡的「助手」指的是 Claude（`MARKET_METHOD.md` 約 30 處，例如 `:37`、`:76`、`:187`；`cron-trigger/wrangler.toml:26`「助手不經手」）。
- 指向沒進版控的 `ANALYSIS_PLAN.md`／`STATUS.md`：見附錄 B。

**站主電腦絕對路徑**

- `README.md`：17
- `cloud/build_compare_pack.py`：87
- `cloud/build_screens_pack.py`：118
- `cloud/market_pack_daily.py`：34
- `design-previews/3d-redesign/pro-samples/compare-liked.py`：12
- `design-previews/3d-redesign/pro-samples/compare-signals.py`：12
- `logs/build_analysis_data.py`：27
- `logs/build_chips_data.py`：7
- `logs/build_stock_detail.py`：9
- `logs/check_analysis_html.py`：7、17
- `logs/check_html_report.py`：5、32
- `logs/check_live.py`：15
- `logs/check_minimal.py`：16
- `logs/check_minute_app.py`：5
- `logs/check_visual_preview.py`：2、3、4、5、11
- `logs/fetch_analysis_data.py`：14
- `sector_rotation.py`：4
- `test_sector_rotation.py`：13
- `test_twon_analyze_v2.py`：2204
- `test_twon_fixtures.py`：16

其中會影響執行的：`logs/fetch_analysis_data.py:14`、`logs/build_analysis_data.py:27`、`logs/build_chips_data.py:7`、`logs/build_stock_detail.py:9` 寫死根目錄，每日流程靠 `cloud/market_pack_daily.py:34-42` 在執行時把這一行字串換掉才跑得動；改這四個檔的那一行會讓每日更新壞掉。`test_twon_analyze_v2.py:2204`、`test_twon_fixtures.py:16`、`test_sector_rotation.py:13` 指向另一個資料夾（`Desktop/claude/…`），那些測試在別台機器永遠被跳過。

**指向 `.scratch/` 的地方（190 行、145 檔）**

最常被引用、但倉庫裡沒有的檔案或資料夾：

- `.scratch/impact-watch`（20 次）
- `.scratch/stock-signals/spec.md`（15 次）
- `.scratch/fund-as-stock/spec.md`（13 次）
- `.scratch/broker-branches`（13 次）
- `.scratch/market-brief`（12 次）
- `.scratch/watchlist-watch/spec.md`（10 次）
- `.scratch/market-news-night`（10 次）
- `.scratch/market-news/consensus.md`（9 次）
- `.scratch/chart-screening-workbench/stock-analysis-contract-v2.md`（8 次）
- `.scratch/index-intraday`（7 次）
- `.scratch/impact-watch/spec.md`（7 次）
- `.scratch/stock-facts/spec.md`（6 次）
- `.scratch/forecast-ledger/spec.md`（6 次）
- `.scratch/market-brief/contract.md`（3 次）

workflow、設定檔與文件裡的（完整清單見附錄 A）：

- `.github/workflows/agy-auth-probe.yml`：5
- `.github/workflows/broker-branches.yml`：1
- `.github/workflows/daily-update.yml`：1、352、377、443、458
- `.github/workflows/forecast-ledger.yml`：2
- `.github/workflows/forecast-morning.yml`：2
- `.github/workflows/market-news.yml`：1、7、206、242
- `.github/workflows/watch-ledger.yml`：2
- `.gitignore`：55
- `NEWS_SOURCES.md`：47
- `announcements.py`：1
- `bollinger.py`：4、8
- `broker_branches.py`：1、3
- `cloud/build_stock_pack.py`：8
- `cloud/impact-watch-push.mjs`：1
- `cloud/import_d1_seed.py`：3
- `cloud/lib/broker-branches.js`：1
- `cloud/lib/build-page.js`：7
- `cloud/lib/forecast.js`：1
- `cloud/lib/gate.js`：418、625
- `cloud/lib/impact-watch-push.js`：1
- `cloud/lib/impact-watch.js`：1、52
- `cloud/lib/index-intraday.js`：1
- `cloud/lib/market-brief.js`：1
- `cloud/lib/market-news-push.js`：2
- `cloud/lib/market-news.js`：1
- `cloud/lib/release-policy.js`：75、115、125、135、141
- `cloud/lib/watch-judgments.js`：1
- `cloud/market-news-push.mjs`：1
- `cloud/migrations/0010_forecast_ledger.sql`：1
- `cloud/migrations/0017_watch_ledger.sql`：1
- `cloud/migrations/0018_etf_securities.sql`：1
- `cloud/migrations/0019_watch_push.sql`：1
- `cloud/migrations/0021_watch_ready_notices.sql`：1
- `cloud/migrations/0025_fund_codes.sql`：1
- `cloud/migrations/0026_fund_securities.sql`：1
- `cloud/migrations/0031_market_news.sql`：1
- `cloud/migrations/0032_stock_news_marks.sql`：1
- `cloud/migrations/0033_news_impact.sql`：1
- `cloud/migrations/0034_impact_watch.sql`：1
- `cloud/migrations/0035_market_brief.sql`：1
- `cloud/migrations/0036_broker_branches.sql`：1
- `cloud/migrations/0037_market_news_push.sql`：1
- `cloud/test/branch-pane.test.js`：1
- `cloud/test/broker-branches-api.test.js`：1
- `cloud/test/browser_apply_journey.py`：8、26
- `cloud/test/browser_branch_pane_journey.py`：1
- `cloud/test/browser_drawing_journey.py`：2445
- `cloud/test/browser_impact_stock_journey.py`：1
- `cloud/test/browser_impact_watch_journey.py`：1
- `cloud/test/browser_index_intraday_journey.py`：1
- `cloud/test/browser_level_journey.py`：17
- `cloud/test/browser_margin_use_journey.py`：7、36
- `cloud/test/browser_market_brief_journey.py`：1
- `cloud/test/browser_market_news_journey.py`：1
- `cloud/test/browser_plain_journey.py`：1
- `cloud/test/browser_portfolio_journey.py`：16
- `cloud/test/browser_settings_journey.py`：5、27
- `cloud/test/fund-distributions.test.js`：1
- `cloud/test/fund-holdings-api.test.js`：1
- `cloud/test/impact-watch-api.test.js`：1、164
- `cloud/test/impact-watch-push.test.js`：1
- `cloud/test/index-intraday-api.test.js`：1
- `cloud/test/journey-server.mjs`：21、43
- `cloud/test/kchart-relief.test.js`：1
- `cloud/test/market-brief-api.test.js`：1
- `cloud/test/market-brief-panel.test.js`：1
- `cloud/test/market-news-api.test.js`：1、271
- `cloud/test/market-news-push.test.js`：1
- `cloud/test/plain-footnote.test.js`：62
- `cloud/test/stock-api.test.js`：9
- `cloud/test/stock-etfs-api.test.js`：1
- `cloud/test/stock-facts-api.test.js`：1
- `cloud/test/stock-funds-api.test.js`：1
- `cloud/test/stock-signals-api.test.js`：1
- `cloud/test/stock-signals.test.js`：1
- `cloud/test/test_etf_on_site_build.py`：3
- `cloud/test/test_fund_distributions.py`：3
- `cloud/test/test_fund_on_site_build.py`：3
- `cloud/test/test_fund_strategy_levels.py`：1
- `cloud/wrangler.jsonc`：35、38、41、44
- `cron-trigger/worker.js`：29
- `design-previews/3d-redesign/check-scroll-lock.py`：3
- `design-previews/3d-redesign/journey.py`：5
- `design-previews/3d-redesign/preview-server.mjs`：17、151
- `design-previews/3d-redesign/pro-samples/build-portfolio-desk.py`：4
- `design-previews/3d-redesign/pro-samples/build-portfolio-material.py`：4
- `design-previews/3d-redesign/pro-samples/compare-liked.py`：1、12
- `design-previews/3d-redesign/pro-samples/compare-signals.py`：2、12
- `etf_classification.py`：1
- `etf_history.py`：1
- `finhunt_screens.py`：4、5
- `forecast_ledger.py`：3、116
- `foreign_flow.py`：3
- `fund_compare.py`：4
- `fund_distributions.py`：1
- `fund_stock.py`：1
- `impact_watch.py`：1
- `impact_watch_news.py`：1
- `liquid_universe.py`：6
- `market_brief.py`：1、3
- `market_news.py`：4、33、59
- `overseas_peers.py`：1
- `plain_daily.py`：14
- `sector_rotation.py`：5
- `strategy_engine.py`：3
- `strategy_v02.py`：1
- `test_announcements.py`：1
- `test_broker_branches.py`：1
- `test_broker_branches_workflow.py`：1
- `test_impact_watch.py`：1
- `test_impact_watch_news.py`：1
- `test_impact_watch_stocks.py`：1
- `test_market_brief.py`：1
- `test_market_brief_workflow.py`：1
- `test_market_news.py`：1、443、916
- `test_market_news_night_workflow.py`：1
- `test_sector_rotation.py`：14
- `test_twon_analyze_v2.py`：3、402、1027、1335、1542、2204
- `test_twon_fixtures.py`：16
- `test_watch_ledger.py`：1
- `test_watch_news_gnews.py`：3
- `trading_days.py`：1
- `twon_analysis/__init__.py`：3、4
- `twon_analysis/analyze.py`：1
- `twon_analysis/analyze_v2.py`：4
- `twon_analysis/conditions.py`：1
- `twon_analysis/facts.py`：1
- `twon_analysis/limit_up_momentum.py`：1、7、86
- `twon_analysis/margin_maintenance.py`：1、18
- `twon_analysis/profiles.py`：1
- `twon_analysis/scores.py`：1
- `twon_analysis/signals.py`：1
- `watch_ledger.py`：3
- `web/branch-pane.js`：1、6
- `web/forecast.js`：1
- `web/fund-distributions.js`：1
- `web/market-brief.js`：1
- `web/market-news.js`：1
- `web/portfolio.css`：250
- `web/stock-facts.js`：1
- `web/stock-signals.css`：1
- `web/stock-signals.js`：1
- `web/watch-judgments.js`：1
- `web/watch-peers.js`：1
- `wyckoff_lpsy.py`：4、82

**依賴本機 `data/` 的測試**：見缺口 5；匯入時就讀 `data/` 的是 `live_server.py:76`，預設路徑寫在 `cloud/build_stock_pack.py:39-44`。

### 第 4 點：`.github/workflows/`

**觸發方式**（13 支全部都有 `workflow_dispatch`，都能在 GitHub 網頁 Actions 頁手動按；沒有「只能靠排程」的 workflow）

| workflow | GitHub 排程 | Cloudflare 準時觸發 | 接在別支後面 | 備註 |
|---|---|---|---|---|
| daily-update | 有（`:15-18`，四個時段） | 有（`cron-trigger/worker.js:21-24`） | — | 兩邊都會觸發；同一天成功過就回 already-done |
| market-news | — | 有（`worker.js:27-34`，五班） | announcements 完成後（`:16-18`） | |
| announcements | — | 有（`worker.js:36`） | — | 沒有 dry_run |
| forecast-morning | —（`:13-15` 說明為何不用） | 有（`worker.js:40`） | — | |
| forecast-ledger、ai-digest、friend-ai-digest、broker-branches | — | — | daily-update 完成後 | |
| watch-ledger | — | — | forecast-ledger、forecast-morning 完成後（`:13-16`） | |
| forecast-replay | 有（`:20`） | — | — | 變數 `MARKET_ANA_REPLAY_SCHEDULE=on` 才真的寫 |
| level-daily | 有（`:6`） | — | — | |
| agy-auth-probe、eodhd-probe | — | — | — | 只能手動 |

要注意的三件事：

1. 手動按時 `dry_run` 預設都是 true（daily-update `:29`、ai-digest `:20`、forecast-ledger `:26`、forecast-morning `:22`、friend-ai-digest `:24`、level-daily `:17`、market-news `:31`、watch-ledger `:29`、broker-branches `:48`）。要真的重跑得改成 false。
2. 真正「只能靠排程、網頁按不到」的是 Cloudflare 那一段：每分鐘盯價（`cron-trigger/worker.js:12`、`cron-trigger/wrangler.toml:17`）不經過 GitHub；而且四支 workflow 的準時觸發都靠那支 Worker 與它的 `GITHUB_DISPATCH_TOKEN`。
3. 接在別支後面的那幾支，上游沒成功發布就不做事；手動按是唯一的補跑方式。

**continue-on-error 的步驟（共 64 步；失敗時 run 仍顯示成功）**

- `.github/workflows/ai-digest.yml`（5 步）
  - `:59` 下載觸發 run 的正式發布證據
  - `:209` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:223` 下載官方 agy 1.5.3 並核對 SHA-256
  - `:234` 還原登入檔到隔離 HOME
  - `:297` 記下 Gemini 剩餘用量（設定頁用；失敗不影響這支排程）
- `.github/workflows/broker-branches.yml`（1 步）
  - `:72` 下載觸發 run 的正式發布證據
- `.github/workflows/daily-update.yml`（18 步）
  - `:190` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:204` 白話分析：下載官方 agy 1.5.3 並核對 SHA-256
  - `:215` 白話分析：還原登入檔到隔離 HOME（只給 agy 子行程用）
  - `:250` 公司重大訊息：補齊原件、抓當日、存回 R2
  - `:262` 個股新聞標題：抓 Google News 大媒體與中央社，寫進 D1
  - `:272` 基金對照 0050：抓集保開放資料淨值、配息與央行匯率，存回 R2
  - `:326` 國外同類股行情（只在發布成功時）
  - `:343` 個股新聞名單：股票池前一交易日成交金額前 50 與英文簡稱（只在發布成功時）
  - `:358` 盯項每日檢查：用剛發布的大盤資料判斷成立／否定／到期（只在發布成功時）
  - `:386` 消息型盯項：準備 agy（白話分析沒準備時才下載、還原登入檔）
  - `:407` 消息型盯項每日檢查：新抓到、對得上關鍵詞的新聞交給 Flash 判斷（只在發布成功時）
  - `:430` 消息型盯項：記進 Gemini 用量（設定頁用；失敗不影響每日更新）
  - `:449` 盯項有結果時推播（只送站主）
  - `:471` 盤勢解讀：讀國際股市與當天消息（D1，唯讀）
  - `:486` 盤勢解讀：產生晚間這一班（只在發布成功時）
  - `:502` 盤勢解讀：寫進 D1（失敗不影響每日更新）
  - `:518` 盤勢解讀：記進 Gemini 用量（設定頁用；失敗不影響每日更新）
  - `:593` 白話分析：記進 Gemini 用量（設定頁用；失敗不影響每日更新）
- `.github/workflows/forecast-ledger.yml`（7 步）
  - `:58` 下載觸發 run 的正式發布證據
  - `:172` 抓海外原件（不擋預測）
  - `:188` 首頁國外指數寫進網站資料庫（不擋預測）
  - `:201` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:214` 下載官方 agy 1.5.3 並核對 SHA-256
  - `:225` 還原登入檔到隔離 HOME
  - `:305` 記下 Gemini 剩餘用量（設定頁用；失敗不影響這支排程）
- `.github/workflows/forecast-morning.yml`（6 步）
  - `:138` 抓海外原件（不擋預測）
  - `:153` 首頁國外指數寫進網站資料庫（不擋預測）
  - `:166` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:179` 下載官方 agy 1.5.3 並核對 SHA-256
  - `:190` 還原登入檔到隔離 HOME
  - `:269` 記下 Gemini 剩餘用量（設定頁用；失敗不影響這支排程）
- `.github/workflows/forecast-replay.yml`（3 步）
  - `:140` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:168` 抓海外原件（不擋重播）
  - `:262` 記下 Gemini 剩餘用量（設定頁用；失敗不影響這支排程）
- `.github/workflows/friend-ai-digest.yml`（2 步）
  - `:68` 下載觸發 run 的正式發布證據
  - `:266` 排程中途失敗：替還沒有結果的朋友寫下原因
- `.github/workflows/market-news.yml`（12 步）
  - `:125` 讀設定頁的 AI 模型（讀不到照 gemini_models.py 的預設跑）
  - `:138` 下載官方 agy 1.5.3 並核對 SHA-256
  - `:149` 還原登入檔到隔離 HOME
  - `:160` 個股新聞：抓 Google News 大媒體與中央社，寫進 D1
  - `:175` 讀可用的朋友金鑰（密文；朋友 AI 解讀開著才讀）
  - `:213` 重大消息推播（只送站主；失敗不影響這一輪）
  - `:227` 記進 Gemini 用量（設定頁用；失敗不影響這一輪）
  - `:256` 盤勢解讀：取回最近一次大盤資料包（R2，唯讀）
  - `:270` 盤勢解讀：讀國際股市與這一班消息（D1，唯讀）
  - `:283` 盤勢解讀：產生這一班（接在消息成功之後）
  - `:295` 盤勢解讀：寫進 D1（失敗不影響這一輪消息）
  - `:310` 盤勢解讀：記進 Gemini 用量（設定頁用；失敗不影響這一輪消息）
- `.github/workflows/watch-ledger.yml`（10 步）
  - `:68` 下載大盤預測晚間版的執行紀錄
  - `:78` 下載大盤預測晨間版的執行紀錄
  - `:188` 取回環境層要的預測原件（唯讀；失敗不擋）
  - `:198` 再抓一次官方公告（不上傳）
  - `:217` 讀設定頁的 AI 模型（本人 11-19 晚；讀不到照 gemini_models.py 的預設跑）
  - `:230` 下載官方 agy 1.5.3 並核對 SHA-256
  - `:241` 還原登入檔到隔離 HOME
  - `:308` 送「可以留意」推播
  - `:323` 提名國外同類股（站主確認後才使用）
  - `:379` 記下 Gemini 剩餘用量（設定頁用；失敗不影響這支排程）

最容易誤判的幾類：「還原登入檔到隔離 HOME」「下載官方 agy」（Gemini 登入壞了也是綠燈）、「寫進 D1」類（盤勢解讀沒寫進去也是綠燈）、推播類。看到綠燈不代表 AI 內容有更新，要點進 run 看各步驟的圖示。

### 附錄 A：所有提到 `.scratch/` 的位置

- `.github/workflows/agy-auth-probe.yml`：5
- `.github/workflows/broker-branches.yml`：1
- `.github/workflows/daily-update.yml`：1、352、377、443、458
- `.github/workflows/forecast-ledger.yml`：2
- `.github/workflows/forecast-morning.yml`：2
- `.github/workflows/market-news.yml`：1、7、206、242
- `.github/workflows/watch-ledger.yml`：2
- `.gitignore`：55
- `NEWS_SOURCES.md`：47
- `announcements.py`：1
- `bollinger.py`：4、8
- `broker_branches.py`：1、3
- `cloud/build_stock_pack.py`：8
- `cloud/impact-watch-push.mjs`：1
- `cloud/import_d1_seed.py`：3
- `cloud/lib/broker-branches.js`：1
- `cloud/lib/build-page.js`：7
- `cloud/lib/forecast.js`：1
- `cloud/lib/gate.js`：418、625
- `cloud/lib/impact-watch-push.js`：1
- `cloud/lib/impact-watch.js`：1、52
- `cloud/lib/index-intraday.js`：1
- `cloud/lib/market-brief.js`：1
- `cloud/lib/market-news-push.js`：2
- `cloud/lib/market-news.js`：1
- `cloud/lib/release-policy.js`：75、115、125、135、141
- `cloud/lib/watch-judgments.js`：1
- `cloud/market-news-push.mjs`：1
- `cloud/migrations/0010_forecast_ledger.sql`：1
- `cloud/migrations/0017_watch_ledger.sql`：1
- `cloud/migrations/0018_etf_securities.sql`：1
- `cloud/migrations/0019_watch_push.sql`：1
- `cloud/migrations/0021_watch_ready_notices.sql`：1
- `cloud/migrations/0025_fund_codes.sql`：1
- `cloud/migrations/0026_fund_securities.sql`：1
- `cloud/migrations/0031_market_news.sql`：1
- `cloud/migrations/0032_stock_news_marks.sql`：1
- `cloud/migrations/0033_news_impact.sql`：1
- `cloud/migrations/0034_impact_watch.sql`：1
- `cloud/migrations/0035_market_brief.sql`：1
- `cloud/migrations/0036_broker_branches.sql`：1
- `cloud/migrations/0037_market_news_push.sql`：1
- `cloud/test/branch-pane.test.js`：1
- `cloud/test/broker-branches-api.test.js`：1
- `cloud/test/browser_apply_journey.py`：8、26
- `cloud/test/browser_branch_pane_journey.py`：1
- `cloud/test/browser_drawing_journey.py`：2445
- `cloud/test/browser_impact_stock_journey.py`：1
- `cloud/test/browser_impact_watch_journey.py`：1
- `cloud/test/browser_index_intraday_journey.py`：1
- `cloud/test/browser_level_journey.py`：17
- `cloud/test/browser_margin_use_journey.py`：7、36
- `cloud/test/browser_market_brief_journey.py`：1
- `cloud/test/browser_market_news_journey.py`：1
- `cloud/test/browser_plain_journey.py`：1
- `cloud/test/browser_portfolio_journey.py`：16
- `cloud/test/browser_settings_journey.py`：5、27
- `cloud/test/fund-distributions.test.js`：1
- `cloud/test/fund-holdings-api.test.js`：1
- `cloud/test/impact-watch-api.test.js`：1、164
- `cloud/test/impact-watch-push.test.js`：1
- `cloud/test/index-intraday-api.test.js`：1
- `cloud/test/journey-server.mjs`：21、43
- `cloud/test/kchart-relief.test.js`：1
- `cloud/test/market-brief-api.test.js`：1
- `cloud/test/market-brief-panel.test.js`：1
- `cloud/test/market-news-api.test.js`：1、271
- `cloud/test/market-news-push.test.js`：1
- `cloud/test/plain-footnote.test.js`：62
- `cloud/test/stock-api.test.js`：9
- `cloud/test/stock-etfs-api.test.js`：1
- `cloud/test/stock-facts-api.test.js`：1
- `cloud/test/stock-funds-api.test.js`：1
- `cloud/test/stock-signals-api.test.js`：1
- `cloud/test/stock-signals.test.js`：1
- `cloud/test/test_etf_on_site_build.py`：3
- `cloud/test/test_fund_distributions.py`：3
- `cloud/test/test_fund_on_site_build.py`：3
- `cloud/test/test_fund_strategy_levels.py`：1
- `cloud/wrangler.jsonc`：35、38、41、44
- `cron-trigger/worker.js`：29
- `design-previews/3d-redesign/check-scroll-lock.py`：3
- `design-previews/3d-redesign/journey.py`：5
- `design-previews/3d-redesign/preview-server.mjs`：17、151
- `design-previews/3d-redesign/pro-samples/build-portfolio-desk.py`：4
- `design-previews/3d-redesign/pro-samples/build-portfolio-material.py`：4
- `design-previews/3d-redesign/pro-samples/compare-liked.py`：1、12
- `design-previews/3d-redesign/pro-samples/compare-signals.py`：2、12
- `etf_classification.py`：1
- `etf_history.py`：1
- `finhunt_screens.py`：4、5
- `forecast_ledger.py`：3、116
- `foreign_flow.py`：3
- `fund_compare.py`：4
- `fund_distributions.py`：1
- `fund_stock.py`：1
- `impact_watch.py`：1
- `impact_watch_news.py`：1
- `liquid_universe.py`：6
- `market_brief.py`：1、3
- `market_news.py`：4、33、59
- `overseas_peers.py`：1
- `plain_daily.py`：14
- `sector_rotation.py`：5
- `strategy_engine.py`：3
- `strategy_v02.py`：1
- `test_announcements.py`：1
- `test_broker_branches.py`：1
- `test_broker_branches_workflow.py`：1
- `test_impact_watch.py`：1
- `test_impact_watch_news.py`：1
- `test_impact_watch_stocks.py`：1
- `test_market_brief.py`：1
- `test_market_brief_workflow.py`：1
- `test_market_news.py`：1、443、916
- `test_market_news_night_workflow.py`：1
- `test_sector_rotation.py`：14
- `test_twon_analyze_v2.py`：3、402、1027、1335、1542、2204
- `test_twon_fixtures.py`：16
- `test_watch_ledger.py`：1
- `test_watch_news_gnews.py`：3
- `trading_days.py`：1
- `twon_analysis/__init__.py`：3、4
- `twon_analysis/analyze.py`：1
- `twon_analysis/analyze_v2.py`：4
- `twon_analysis/conditions.py`：1
- `twon_analysis/facts.py`：1
- `twon_analysis/limit_up_momentum.py`：1、7、86
- `twon_analysis/margin_maintenance.py`：1、18
- `twon_analysis/profiles.py`：1
- `twon_analysis/scores.py`：1
- `twon_analysis/signals.py`：1
- `watch_ledger.py`：3
- `web/branch-pane.js`：1、6
- `web/forecast.js`：1
- `web/fund-distributions.js`：1
- `web/market-brief.js`：1
- `web/market-news.js`：1
- `web/portfolio.css`：250
- `web/stock-facts.js`：1
- `web/stock-signals.css`：1
- `web/stock-signals.js`：1
- `web/watch-judgments.js`：1
- `web/watch-peers.js`：1
- `wyckoff_lpsy.py`：4、82

### 附錄 B：提到沒進版控的 `ANALYSIS_PLAN.md`／`STATUS.md` 的位置

- `.github/workflows/forecast-replay.yml`：1
- `.gitignore`：57、58
- `MARKET_METHOD.md`：13、24、257、281
- `NEWS_SOURCES.md`：3、47
- `README.md`：7、10
- `broker_branches.py`：3
- `cloud/wrangler.jsonc`：15
- `design-previews/unslop-20261109/zoom_check.py`：1
- `forecast_experience.py`：3
- `forecast_ledger.py`：770、797
- `forecast_replay.py`：3
- `fund_compare.py`：3
- `gemini_models.py`：3、22
- `gemini_settings.py`：3
- `test_forecast_ledger.py`：1146
- `test_gemini_models.py`：1、37
- `test_watch_inputs.py`：471
- `twon_analysis/analyze.py`：7
- `wyckoff_lpsy.py`：3

### 附錄 C：金鑰與開關的名稱（只有名稱，沒有值）

GitHub Secrets（workflow 用到的）：

- `AGY_LOGIN_BUNDLE`：agy-auth-probe、ai-digest、daily-update、forecast-ledger、forecast-morning、forecast-replay、market-news、watch-ledger
- `CLOUDFLARE_ACCOUNT_ID`：ai-digest、announcements、broker-branches、daily-update、forecast-ledger、forecast-morning、forecast-replay、friend-ai-digest、level-daily、market-news、watch-ledger
- `CLOUDFLARE_API_TOKEN`：ai-digest、broker-branches、daily-update、forecast-ledger、forecast-morning、forecast-replay、friend-ai-digest、level-daily、market-news、watch-ledger
- `EODHD_API_KEY`：daily-update、eodhd-probe、forecast-ledger、forecast-morning、forecast-replay、level-daily、watch-ledger
- `FRIEND_AI_MASTER_KEY`：friend-ai-digest、market-news
- `FUGLE_API_KEY`：forecast-ledger、forecast-morning、market-news、watch-ledger
- `GITHUB_TOKEN`（GitHub 自動提供，不用自己放）：ai-digest、broker-branches、forecast-ledger、friend-ai-digest、watch-ledger
- `MASSIVE_API_KEY`：daily-update、watch-ledger
- `OWNER_EMAIL`：ai-digest、broker-branches、daily-update、market-news、watch-ledger
- `R2_ACCESS_KEY_ID`：ai-digest、announcements、daily-update、forecast-ledger、forecast-morning、forecast-replay、friend-ai-digest、level-daily、market-news、watch-ledger
- `R2_SECRET_ACCESS_KEY`：ai-digest、announcements、daily-update、forecast-ledger、forecast-morning、forecast-replay、friend-ai-digest、level-daily、market-news、watch-ledger
- `VAPID_PRIVATE_KEY`：daily-update、market-news、watch-ledger
- `VAPID_PUBLIC_KEY`：daily-update、market-news、watch-ledger

GitHub repository variables：

- `MARKET_ANA_BROKER_BRANCHES_ROW_BUDGET`：broker-branches
- `MARKET_ANA_BROKER_BRANCHES_SCHEDULE`：broker-branches
- `MARKET_ANA_FORECAST_MORNING`：forecast-morning
- `MARKET_ANA_FORECAST_SCHEDULE`：forecast-ledger
- `MARKET_ANA_FRIEND_AI`：friend-ai-digest、market-news
- `MARKET_ANA_MARKET_BRIEF_SCHEDULE`：daily-update、market-news
- `MARKET_ANA_MARKET_NEWS_PUSH`：market-news
- `MARKET_ANA_MARKET_NEWS_SCHEDULE`：market-news
- `MARKET_ANA_PLAIN_DAILY`：daily-update
- `MARKET_ANA_REPLAY_DAILY_LIMIT`：forecast-replay
- `MARKET_ANA_REPLAY_SCHEDULE`：forecast-replay
- `MARKET_ANA_STRATEGIES_ENABLED`：ai-digest、level-daily
- `MARKET_ANA_WATCH_SCHEDULE`：watch-ledger

Cloudflare 端機密（Pages 專案 `market-ana` 與 Worker `market-ana-cron-trigger`，名稱出自 `cloud/lib/`、`cron-trigger/`）：`OWNER_EMAIL`、`FUGLE_API_KEY`、`EODHD_API_KEY`、`MASSIVE_API_KEY`、`FRIEND_AI_MASTER_KEY`、`VAPID_PUBLIC_KEY`、`VAPID_PRIVATE_KEY`、`GITHUB_DISPATCH_TOKEN`（只在 Worker）。哪個放在哪一邊，倉庫裡只有 `cron-trigger/wrangler.toml:26-27` 與 `cron-trigger/set-push-keys.mjs:28-30` 兩處提到，沒有完整對照。
