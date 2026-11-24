# 功能開關與排程開關

有兩種開關，改的地方不同：

| 種類 | 放在哪 | 管什麼 | 改了什麼時候生效 |
|---|---|---|---|
| **網站功能開關** | `cloud/wrangler.jsonc` 的 `vars`（在 git 裡） | 網站上誰看得到某個功能 | 推上 `main` 之後，**要部署**才生效：下一次每日發布，或手動 redeploy（[rerun-and-redeploy.md](rerun-and-redeploy.md) B） |
| **排程開關**（repository variable） | GitHub → 倉庫 Settings → Secrets and variables → Actions → **Variables** 分頁 | 排程要不要真的寫入正式資料 | 改了立刻生效（下一次 run 就讀到），不用部署 |

慣例（不要破壞）：
- 新功能的開關**預設關**；先開 `owner`（只有站主看得到），站主看過說可以才改 `all`。
- 三態開關只認一字不差的 `owner`／`all`，**拼錯或其他值一律算關**。排程開關只認一字不差的 `on`。
- 開關的判斷都在 `cloud/lib/release-policy.js`。

## 網站功能開關（`cloud/wrangler.jsonc`，目前提交的值）

| 名稱 | 目前值 | 打開後是什麼 | 誰看得到 |
|---|---|---|---|
| `MARKET_ANA_ENABLED_FEATURES` | `market_trend_v1` | 逗號清單，只接受 `strategy_v1`、`market_trend_v1`：首頁「短中長期走勢」 | 所有核准的人 |
| `MARKET_ANA_FORECAST_LEDGER` | `owner` | 預測對帳頁 | 只有站主（只認 `owner`） |
| `MARKET_ANA_STOCK_FACTS` | `all` | 個股頁事實整理區塊 | 所有人 |
| `MARKET_ANA_WATCHLIST_WATCH` | `owner` | 自選股關注（判斷、到價推播） | 只有站主（只認 `owner`）。`cron-trigger/wrangler.toml` 也有同名開關給盯價用 |
| `MARKET_ANA_STOCK_SIGNALS` | `all` | 個股頁五塊（漲停動能、和大盤比、均線排列、融資維持率估、爆大量成交區） | 所有人 |
| `MARKET_ANA_STOCK_ETFS` | `all` | 股票型 ETF 有個股頁、可搜尋 | 所有人 |
| `MARKET_ANA_STOCK_FUNDS` | `all` | 境外基金當成個股（搜尋、個股頁、比較、自選、庫存） | 所有人 |
| `MARKET_ANA_MARKET_NEWS` | `all` | 首頁「消息」與個股新聞標題 | 所有人（「我的自選」各看各的） |
| `MARKET_ANA_IMPACT_WATCH` | `owner` | 首頁頁首「在盯 N 件」 | 只有站主 |
| `MARKET_ANA_MARKET_BRIEF` | `owner` | 盤勢解讀新版（類別鍵＋圖） | 只有站主；關著時照舊顯示舊版解讀 |
| `MARKET_ANA_BROKER_BRANCHES` | `owner` | 個股頁「主力分點」副圖 | 只有站主（只有 `owner`／關兩種，沒有 `all`） |
| `MARKET_ANA_INDEX_INTRADAY` | `owner` | 海外大盤的分時、那斯達克期貨 | 只有站主 |
| `MARKET_ANA_FRIEND_AI` | `on` | 朋友用自己的 Gemini 金鑰產生解讀 | 核准的朋友（只認 `on`；GitHub variable 同名開關也要 `on`） |
| `MARKET_ANA_STRATEGIES_ENABLED` | **沒設＝關** | 策略總開關：站主 AI 解讀、現金水位、個股操作策略 | 站主 2026-11-14 定案先關 |

不在這個檔、在 Pages secret 的：`OWNER_EMAIL` 等（見 [credentials.md](credentials.md)）。

### 改一個網站功能開關（例如把盯項開給所有人）

1. 在 GitHub 網頁打開 `cloud/wrangler.jsonc` → 鉛筆（Edit）。
2. 把 `"MARKET_ANA_IMPACT_WATCH": "owner"` 改成 `"all"`；要關掉就把那一行整行刪掉。
3. Commit 到 `main`（這一步就是「上線」的決定，先問過站主）。
4. 跑 redeploy（先乾跑再正式）。
5. 確認：用一個非站主的核准帳號看得到／看不到。

> 有些功能需要正式資料庫已套對應的遷移（migration）；檔案裡每一行的註解有寫。目前全部已套（到 0038）。

## 排程開關（GitHub repository variables）

「目前值」來自交接紀錄，**站主或 AI 請到 GitHub Variables 頁核對一次**（倉庫公開時這一頁仍只有倉庫擁有者看得到）。

| 名稱 | 管哪支 workflow | 交接時的值 | `on` 時做什麼 |
|---|---|---|---|
| `MARKET_ANA_PLAIN_DAILY` | `daily-update` | `on`（2026-11-19 設） | 首頁籌碼白話每天由 Gemini 重寫（`dry`＝只寫執行紀錄不發布；其他＝關） |
| `MARKET_ANA_MARKET_BRIEF_SCHEDULE` | `daily-update`、`market-news` | `on`（2026-11-23） | 盤勢解讀每班產生 |
| `MARKET_ANA_MARKET_NEWS_SCHEDULE` | `market-news` | `on`（推定，消息每天有在寫；**待確認**） | 消息正式寫進資料庫；不是 `on` 時一律乾跑 |
| `MARKET_ANA_MARKET_NEWS_PUSH` | `market-news` | `on`（2026-11-24） | 影響大的消息推播到站主手機 |
| `MARKET_ANA_FORECAST_SCHEDULE` | `forecast-ledger` | `on`（推定；**待確認**） | 晚間預測正式寫入 |
| `MARKET_ANA_FORECAST_MORNING` | `forecast-morning` | `on`（推定；**待確認**） | 晨間預測正式寫入 |
| `MARKET_ANA_WATCH_SCHEDULE` | `watch-ledger` | `on`（推定；**待確認**） | 自選股判斷正式寫入 |
| `MARKET_ANA_FRIEND_AI` | `friend-ai-digest`、`market-news` | `on`（推定；**待確認**） | 朋友解讀 |
| `MARKET_ANA_BROKER_BRANCHES_SCHEDULE` | `broker-branches` | `on`（2026-11-24） | 每日更新後自動補／更新主力分點 |
| `MARKET_ANA_BROKER_BRANCHES_ROW_BUDGET` | `broker-branches` | 未設（用程式預設） | 每輪寫入列數上限（D1 免費方案一天 10 萬列，刪除也算） |
| `MARKET_ANA_REPLAY_SCHEDULE` | `forecast-replay` | 關（2026-11-19 紀錄） | 每個交易日 10:00 寫歷史重播 |
| `MARKET_ANA_REPLAY_DAILY_LIMIT` | `forecast-replay` | 未設（＝1 份） | 每天最多幾份重播 |
| `MARKET_ANA_STRATEGIES_ENABLED` | `ai-digest`、`level-daily` | 未設或關（**待確認**） | 只接受 `1`／`true`：站主 AI 解讀、現金水位正式寫入 |

### 改一個排程開關

1. GitHub → 倉庫 Settings → Secrets and variables → Actions → **Variables**。
2. 點名稱旁的鉛筆 → 改值（例如 `on`）→ Update variable。要關就改成 `off` 或刪掉。
3. 不用部署。下一次那支 workflow 跑時生效。

## 排程時間表（台北時間）

觸發來源有兩種：Cloudflare Worker `market-ana-cron-trigger`（準時；設定在 `cron-trigger/wrangler.toml` 與 `worker.js` 的 `JOBS`）、GitHub 自己的 `schedule`（常晚好幾小時）。

| 時間 | 觸發 | workflow |
|---|---|---|
| 每天 08:00 | Worker | `announcements` → 跑完接 `market-news` 盤前班（週末不跑） |
| 平日 07:20 | Worker | `forecast-morning` → 接 `watch-ledger` 晨間 |
| 平日 07:30 | GitHub schedule | `level-daily`（總開關關著時不寫） |
| 平日 09:00–13:59 每分鐘 | Worker | 盯價（不經 GitHub） |
| 平日 10:00 | GitHub schedule | `forecast-replay`（開關關著時不寫） |
| 平日 11:30、14:00、18:30、23:00 | Worker | `market-news`（midday、close、evening、us_open） |
| 週二到週六 06:30 | Worker | `market-news`（us_close） |
| 平日 15:30、16:30、18:00、20:30 | Worker＋GitHub schedule（兩邊都會觸發，同一天成功後其餘回 already-done） | `daily-update` → 發布成功後接 `forecast-ledger`→`watch-ledger`、`ai-digest`、`friend-ai-digest`、`broker-branches` |

注意：Cloudflare 免費方案整個帳號最多 5 個排程時段，**已經用滿**。新排程要併進既有時段（見 `cron-trigger/wrangler.toml` 的註解），或改用 GitHub schedule（會延遲）。改 Worker 排程需要重新部署 Worker，目前還要電腦（[handover.md](handover.md) (c)）。

## GitHub 60 天沒活動會停用排程

公開倉庫 60 天沒有任何提交，GitHub 會自動停用含 `schedule` 的 workflow。Worker 觸發的 `workflow_dispatch` 會不會也被擋：**待確認**。對策在 [handover.md](handover.md) (d)。停用時 Actions 頁會出現黃色提示，按 **Enable workflow** 就恢復。
