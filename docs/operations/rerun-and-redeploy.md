# 重跑、重新部署、同一天重新發布（全部在 GitHub 網頁上做）

入口：`https://github.com/liuaaron0226/Market--Ana/actions`。手機也可以，但按鈕比較小，建議用電腦或平板的瀏覽器。

> **永遠先乾跑一次**（`dry_run` 勾著），綠了再取消勾選正式跑。手動觸發時 `dry_run` 預設就是勾著的。

## 先懂一件事：推上 `main` 就會上線

- 每日更新、重新部署都用 `main` 分支的程式建置網站。所以**任何推上 `main` 的改動，會在下一次每日發布（平日約 18:00）或下一次手動 redeploy 時出現在正式站**。
- 只改 Python（資料處理、AI 排程）的改動不需要部署，下一次那支 workflow 跑的時候就生效。
- 改 `cloud/wrangler.jsonc`（功能開關）一定要部署（redeploy 或下一次發布）才生效。
- 改 `cron-trigger/`（Worker 排程器）**不會**因為推上 main 而生效，要另外按 `deploy-cron-trigger`（[ops-buttons.md](ops-buttons.md)）。
- 新增資料庫變更（`cloud/migrations/`）要另外按 `d1-migrate`（[ops-buttons.md](ops-buttons.md)）。
- 還在改、還沒給站主看過的東西，放別的分支，不要推 `main`。

## A. 重跑失敗的 run

適用：某一筆 run 紅叉，原因是暫時性的（網路、Cloudflare API 503、來源限流）。

1. Actions → 點那支 workflow → 點紅叉那一筆 run。
2. 右上角 **Re-run jobs** → **Re-run failed jobs**。
3. 好的結果：重跑那一筆變綠勾。
4. 還是紅：點進紅叉的步驟看訊息。訊息寫 token、金鑰、401、403 → [credentials.md](credentials.md)；其他看不懂的 → 交給 AI（[for-ai-maintainers.md](for-ai-maintainers.md)）。

注意：
- **Re-run 沒有乾跑**：排程觸發的 run（事件是 `schedule` 或排程器送的）重跑時一律是正式執行，日期也會照當下重新判斷。想先試、或要指定某一天，不要用 Re-run，改用 **Run workflow** 手動跑、`date` 填那一天、先勾 `dry_run`。
- `daily-update` 同一資料日已經成功過，重跑會回 `already-done`（不會重做），這是正常的。要用新程式重算同一天 → 看 C。

## B. 只重新部署程式（redeploy）

適用：改了網頁或 `cloud/` 的程式、或改了 `cloud/wrangler.jsonc` 的開關，資料不用重算，想現在就上線。

1. Actions → 左邊 `daily-update` → 右邊 **Run workflow**。
2. 分支選 `main`。
3. 填：
   - `redeploy`：**勾**
   - `dry_run`：**勾**（先乾跑）
   - 其他留空、`republish` 不勾（兩個不能同時勾）。
4. 按 **Run workflow**，等綠勾（約 5～15 分鐘）。
5. 再跑一次，這次 `dry_run` **取消勾選**、`redeploy` 勾。
6. 好的結果：綠勾；Summary 的 JSON 有 `"status": "success"`、`"redeployed": true`。打開網站看改動出現。

它**會**做：用最後一次正式發布時保存的建置輸入，配上 `main` 現在的程式，重建並部署網站；部署後自動檢查。
**它不是單純的部署**——正式跑（`dry_run` 取消）時，同一支 workflow 裡這些步驟照樣會跑、會寫 R2／D1、可能推播：公司重大訊息原件、個股新聞標題（Google News，可能被限流）、基金淨值、盯項檢查（數字型與消息型，消息型會用到 Gemini）與盯項推播。
它**不會**做：不重算行情、不切換資料版本、不同步工作集、**不觸發**晚上的 AI 排程（預測、判斷、AI 解讀、主力分點都不會跑）。`date` 欄位會被忽略。乾跑（`dry_run` 勾著）時上面那些步驟只抓不寫。
失敗時：Summary 的 `failed_step` 寫 `check_active` → 線上資料版本和保存的輸入對不上（例如中途有人重新發布過）；等下一次正式發布即可，或交給 AI 查。

## C. 同一天用新程式重新發布（republish）

適用：今天已經發布了，但修了算法或資料處理的程式，要今天的資料用新程式重算再發布一次。

> ⚠️ **平常日不要做，除非真的必要。** 重新發布會把整天的資料再寫一次資料庫，2026-11-24 晚就是 republish 加上主力分點整理，把 D1 免費方案「每天 10 萬列」的寫入額度用完，之後所有寫入失敗到隔天 08:00（[daily-check.md](daily-check.md)〈D1 寫入額度用完〉）。能等明天正常發布就等。

1. Actions → `daily-update` → **Run workflow**，分支 `main`。
2. 填：
   - `republish`：**勾**
   - `date`：留空＝台北今天。**只能填「目前線上那一版」的資料日**；晚上做就是今天，隔天早上（還沒有新發布前）要補做就填前一個交易日。
   - `dry_run`：**勾**（先乾跑）
3. 乾跑綠了，再跑一次取消 `dry_run`。
4. 好的結果：Summary 有 `"published": true` 和新的 `republish_version`（原版本名後面加 `-r年月日時分`）。原版留在資料庫當「上一版」，必要時可退回。

它**會**做：整套每日更新再跑一次並發布新版本，而且**等於一次新的正式發布**——所以下面這些會跟著再跑一次：
- `daily-update` 裡發布後的步驟：國外同類股行情、個股新聞名單、盯項檢查與推播、**盤勢解讀晚間那一班會重寫**（用 Gemini）、白話分析（用 Gemini）。
- 接在後面的 workflow：`forecast-ledger`（晚間預測；帳本只新增不覆寫，同一天已寫過的不會改）、`watch-ledger`、`friend-ai-digest`、`ai-digest`（總開關關著時不寫）、`broker-branches`。
- 會多吃一次 Gemini 額度與 EODHD 額度（EODHD 每天 20 次）。

它**不會**做：不能重新發布比線上舊的日子（會在 `republish_check` 失敗）。

## D. 退回上一版（發布後網站壞了）

網站有兩樣東西要分開看：**資料版本**（D1 裡的 active／previous）和**網頁部署**（Cloudflare Pages 的每一次部署）。

**自動的部分**：每日更新在切換資料版本之後，部署或部署後檢查失敗，會自動把 D1 的資料版本切回上一版。**網頁部署不會自動退回**（`daily_update.py` 的 `remote_rollback` 只切 D1）。

**看目前是哪一版**：Cloudflare → Storage & Databases → D1 → `market-ana-accounts` → Console，執行
`SELECT active_version, previous_version FROM stock_data_state;`
（版本名像 `liquid-20261124-…-v2`，中間是資料日；結尾 `-r年月日時分` 的是 republish 出來的。）

**退回網頁部署**（手機或電腦都可以）：
1. Cloudflare → Workers & Pages → `market-ana` → **Deployments**。
2. 找到上一個正常的 Production 部署 → 右邊「…」→ **Rollback to this deployment**（選單名稱以畫面為準）。
3. 確認：重新整理網站，壞掉的地方回到原樣。

**資料版本也要退時**（例如新資料算錯）：在同一個 D1 Console 執行
`UPDATE stock_data_state SET active_version = previous_version, previous_version = active_version, updated_at = datetime('now') WHERE key = 'current';`
（就是 `cloud/import_d1_seed.py rollback` 做的事：兩個版本互換，不刪任何東西；再執行一次就換回來。）這是正式資料庫的寫入，先問站主；寫入額度用完時會失敗。

**和 R2 建置輸入的關係**：R2 工作集保存的是「最後一次發布」的建置輸入。資料版本退回之後，`redeploy` 會拒絕（`check_active`：線上版本和保存的輸入對不上），這是保護，不是壞掉。修好程式後，等下一次正式發布（或對線上那一天 republish）就會恢復一致。

**確認**：首頁資料日、`SELECT active_version …` 的結果、Pages Deployments 最上面那一筆，三者對得上。

## E. 補抓 ETF 行情原件（etf_raw_days）

見 [data-gaps.md](data-gaps.md)。

## F. 其他 workflow 的手動觸發（都預設乾跑）

| workflow | 輸入 | 用途 |
|---|---|---|
| `forecast-ledger` | `date`、`dry_run`、`as_of`（乾跑重播某一天） | 晚間預測沒寫成時補寫（`date` 必須等於已發布資料的最後一天） |
| `forecast-morning` | `dry_run`、`as_of` | 晨間預測；過了 08:45 期限才跑會記漏記，事後補不回來 |
| `watch-ledger` | `edition`（evening／morning）、`dry_run`、`as_of`、`rewrite_today` | 自選股判斷 |
| `market-news` | `slot`（us_close／morning／midday／close／evening／us_open）、`dry_run` | 補跑某一班消息；正式寫入還要 `MARKET_ANA_MARKET_NEWS_SCHEDULE=on` |
| `friend-ai-digest`、`ai-digest` | `date`、`dry_run` | 朋友／站主 AI 解讀 |
| `broker-branches` | `mode`（backfill／daily／scheduled／trim）、`code`、`limit`、`dry_run` | 主力分點；見 [data-gaps.md](data-gaps.md) |
| `level-daily` | `date`、`dry_run` | 現金水位（總開關關著時只能乾跑） |
| `forecast-replay` | `start`、`end`、`max_forecasts`、`plan_only` | 歷史重播（`plan_only` 勾著只算次數不呼叫模型） |
| `agy-auth-probe` | `model` | 檢查 Gemini 登入檔還能不能用（只呼叫一次模型） |
| `eodhd-probe` | `symbols` | 檢查 EODHD 金鑰（每個代號用掉 1 次額度） |
| `announcements` | （不用填） | 補抓公司重大訊息原件與基金淨值（`fund-nav`），重跑沒有副作用 |
| `d1-migrate`、`deploy-cron-trigger`、`workflow-keepalive` | `dry_run` 等 | 見 [ops-buttons.md](ops-buttons.md) |
| `institutional-backfill` | `dry_run`、`days`、`start` | 法人歷史回補，見 [data-gaps.md](data-gaps.md) |
