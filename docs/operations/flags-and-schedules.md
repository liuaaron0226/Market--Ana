# 功能開關與排程開關

有兩種開關，改的地方不同：

| 種類 | 放在哪 | 管什麼 | 改了什麼時候生效 |
|---|---|---|---|
| **網站功能開關** | `cloud/wrangler.jsonc` 的 `vars`（在 git 裡） | 網站上誰看得到某個功能 | 推上 `main` 之後，**要部署**才生效：下一次每日發布，或手動 redeploy（[rerun-and-redeploy.md](rerun-and-redeploy.md) B） |
| **排程開關**（repository variable） | GitHub → 倉庫 Settings → Secrets and variables → Actions → **Variables** 分頁（**現值在倉庫檔案裡看不到**，只能到這一頁看） | 排程要不要真的寫入正式資料 | 改了立刻生效（下一次 run 就讀到），不用部署 |
| **排程器開關** | `cron-trigger/wrangler.toml` 的 `[vars]`：`MARKET_ANA_WATCHLIST_WATCH`（盤中盯價）、`MARKET_ANA_DATA_HEALTH`（資料更新推播） | 排程器自己做的事 | **和網站開關分開**：關掉網站的同名開關，盯價與推播照跑。改了要按 `deploy-cron-trigger`（[ops-buttons.md](ops-buttons.md)） |

慣例（不要破壞）：
- 新功能的開關**預設關**；先開 `owner`（只有站主看得到），站主看過說可以才改 `all`。
- 三態開關只認一字不差的 `owner`／`all`，**拼錯或其他值一律算關**。排程開關只認一字不差的 `on`。
- 開關的判斷都在 `cloud/lib/release-policy.js`。

## 網站功能開關（`cloud/wrangler.jsonc`）

站主 2026-11-24 晚：「全部都開」（把自己有的功能開給其他獲准使用者）。下表是**預定值**；交接時 main 上這個檔正在合併，**請打開 `cloud/wrangler.jsonc` 核對一次**（標 ✱ 的是這次改的）。

| 名稱 | 值 | 打開後是什麼 | 誰看得到 |
|---|---|---|---|
| `MARKET_ANA_ENABLED_FEATURES` | `market_trend_v1` | 逗號清單，只接受 `strategy_v1`、`market_trend_v1`：首頁「短中長期走勢」 | 所有核准的人 |
| `MARKET_ANA_FORECAST_LEDGER` | `all` ✱ | 預測對帳頁 | 所有人（其他人只能讀） |
| `MARKET_ANA_STOCK_FACTS` | `all` | 個股頁事實整理區塊 | 所有人 |
| `MARKET_ANA_STOCK_SIGNALS` | `all` | 個股頁五塊（漲停動能、和大盤比、均線排列、融資維持率估、爆大量成交區） | 所有人 |
| `MARKET_ANA_STOCK_ETFS` | `all` | 股票型 ETF 有個股頁、可搜尋 | 所有人 |
| `MARKET_ANA_STOCK_FUNDS` | `all` | 境外基金當成個股 | 所有人 |
| `MARKET_ANA_MARKET_NEWS` | `all` | 首頁「消息」與個股新聞標題 | 所有人（「我的自選」各看各的） |
| `MARKET_ANA_IMPACT_WATCH` | `all` ✱ | 首頁頁首「在盯 N 件」 | 所有人；朋友只看到對象是大盤的；推播只送站主 |
| `MARKET_ANA_MARKET_BRIEF` | `all` ✱ | 盤勢解讀新版（類別鍵＋圖） | 所有人；「追蹤事項」那一類只給站主 |
| `MARKET_ANA_INDEX_INTRADAY` | `all` ✱ | 海外大盤的分時、那斯達克期貨、美元／台幣 | 所有人 |
| `MARKET_ANA_LIVE_QUOTES` | `all` ✱（新） | 富果即時行情：個股即時報價與盤中、當天逐筆、首頁大盤即時、分時、五檔、60 分 K | 所有人。富果條款限本人私用，站主已知風險仍決定開 |
| `MARKET_ANA_CHAIN_MAP` | `all` ✱（新） | 資金版圖頁 `/chain-map` | 所有人 |
| `MARKET_ANA_DATA_HEALTH` | `owner`（新） | 設定頁「資料更新」那一籤 | 只有站主（`all` 也只開站主）。推播在 `cron-trigger/wrangler.toml` 的同名開關（`owner`） |
| `MARKET_ANA_BROKER_BRANCHES` | `owner` | 個股頁「主力分點」副圖 | 只有站主。程式已支援 `all`，但「哪幾檔有分點」等於站主的名單，要先決定抓取範圍（站主說「我希望我股票池的股票都有」，意思待釐清） |
| `MARKET_ANA_WATCHLIST_WATCH` | `owner` | 自選股關注（判斷、到價推播） | 只有站主。朋友版沒做（要每人各自判斷、朋友自己的 Gemini 金鑰、每人推播、行情商額度）。`cron-trigger/wrangler.toml` 也有同名開關 |
| `MARKET_ANA_FRIEND_AI` | `on` | 朋友用自己的 Gemini 金鑰產生解讀 | 核准的朋友（只認 `on`；GitHub variable 同名開關也要 `on`） |
| `MARKET_ANA_STRATEGIES_ENABLED` | **沒設＝關** | 策略總開關：站主 AI 解讀、現金水位、個股操作策略 | 站主 2026-11-14 定案先關 |

管理頁、設定頁、站主自己的私人資料不開給其他人。

## 行情商來函或帳號被停時怎麼關

收到富果、Yahoo、Massive 等資料商來函要求停止轉給別人，或擔心被停權時，把對應功能改回只給站主：

1. 在 GitHub 網頁打開 `cloud/wrangler.jsonc` → 右上鉛筆（Edit）。
2. 找到那一行，把 `"all"` 改成 `"owner"`：

| 開關 | 改成 `owner` 後，其他人失去 |
|---|---|
| `MARKET_ANA_LIVE_QUOTES`（富果） | 個股即時報價、分時、五檔、60 分 K、首頁大盤即時（全部富果資料只給站主） |
| `MARKET_ANA_INDEX_INTRADAY`（Yahoo） | 海外大盤分時、那斯達克期貨、美元／台幣 |
| `MARKET_ANA_CHAIN_MAP`（Massive、Yahoo） | 整個資金版圖頁（其他人打開會是找不到頁面） |

3. 下方 **Commit changes** → 直接提交到 `main`。
4. Actions → `daily-update` → Run workflow：`redeploy` 勾、`dry_run` 勾 → 綠了再跑一次 `dry_run` 取消（[rerun-and-redeploy.md](rerun-and-redeploy.md) B）。
5. 約 10～30 分鐘生效。用一個非站主帳號確認看不到了。

如果是資料商**直接停用金鑰**，連站主自己也會失去那些資料，那是換金鑰的問題，看 [credentials.md](credentials.md)。

注意：網站開關只管網站。排程器的盤中盯價（用富果）有自己的開關 `MARKET_ANA_WATCHLIST_WATCH`（`cron-trigger/wrangler.toml`），要停盯價得改那個檔再按 `deploy-cron-trigger`（[ops-buttons.md](ops-buttons.md)）。

不在這個檔、在 Pages secret 的：`OWNER_EMAIL` 等（見 [credentials.md](credentials.md)）。

### 改一個網站功能開關（例如把盯項開給所有人）

1. 在 GitHub 網頁打開 `cloud/wrangler.jsonc` → 鉛筆（Edit）。
2. 把 `"MARKET_ANA_IMPACT_WATCH": "owner"` 改成 `"all"`；要關掉就把那一行整行刪掉。
3. Commit 到 `main`（這一步就是「上線」的決定，先問過站主）。
4. 跑 redeploy（先乾跑再正式）。
5. 確認：用一個非站主的核准帳號看得到／看不到。

> 有些功能需要正式資料庫已套對應的遷移（migration）；檔案裡每一行的註解有寫。交接時 0039（資金版圖）、0040（資料更新推播）還沒套，見 [owner-todo.md](owner-todo.md) 第一項。

## 排程開關（GitHub repository variables）

這些值**在倉庫裡看不到**。「2026-11-24 晚的值」欄只有四項是當晚實際確認過的；其他寫「未核對」的，請到 GitHub → Settings → Secrets and variables → Actions → **Variables** 看（這一頁只有倉庫擁有者看得到）。

| 名稱 | 管哪支 workflow | 2026-11-24 晚的值 | `on` 時做什麼 |
|---|---|---|---|
| `MARKET_ANA_MARKET_BRIEF_SCHEDULE` | `daily-update`、`market-news` | **`on`**（已確認） | 盤勢解讀每班產生 |
| `MARKET_ANA_MARKET_NEWS_PUSH` | `market-news` | **`on`**（已確認） | 影響大的消息推播到站主手機 |
| `MARKET_ANA_BROKER_BRANCHES_SCHEDULE` | `broker-branches` | **`on`**（已確認） | 每日更新後自動補／更新主力分點 |
| `MARKET_ANA_CHAIN_MAP_SCHEDULE` | `daily-update` | **`on`**（當晚設定） | 資金版圖每日資料建置 |
| `MARKET_ANA_PLAIN_DAILY` | `daily-update` | 未核對（紀錄是 11-19 設 `on`） | 首頁籌碼白話每天由 Gemini 重寫（`dry`＝只寫執行紀錄不發布；其他＝關） |
| `MARKET_ANA_MARKET_NEWS_SCHEDULE` | `market-news` | 未核對 | 消息正式寫進資料庫；不是 `on` 時一律乾跑 |
| `MARKET_ANA_FORECAST_SCHEDULE` | `forecast-ledger` | 未核對 | 晚間預測正式寫入 |
| `MARKET_ANA_FORECAST_MORNING` | `forecast-morning` | 未核對 | 晨間預測正式寫入 |
| `MARKET_ANA_WATCH_SCHEDULE` | `watch-ledger` | 未核對 | 自選股判斷正式寫入 |
| `MARKET_ANA_FRIEND_AI` | `friend-ai-digest`、`market-news` | 未核對 | 朋友解讀 |
| `MARKET_ANA_BROKER_BRANCHES_ROW_BUDGET` | `broker-branches` | 未核對（沒設＝程式預設） | 每輪寫入列數上限（D1 免費方案一天 10 萬列，刪除也算） |
| `MARKET_ANA_REPLAY_SCHEDULE` | `forecast-replay` | 未核對（紀錄是 11-19 關） | 每個交易日 10:00 寫歷史重播 |
| `MARKET_ANA_REPLAY_DAILY_LIMIT` | `forecast-replay` | 未核對（沒設＝1 份） | 每天最多幾份重播 |
| `MARKET_ANA_STRATEGIES_ENABLED` | `ai-digest`、`level-daily` | 未核對 | 只接受 `1`／`true`：站主 AI 解讀、現金水位正式寫入 |

### 改一個排程開關

1. GitHub → 倉庫 Settings → Secrets and variables → Actions → **Variables**。
2. 點名稱旁的鉛筆 → 改值（例如 `on`）→ Update variable。要關就改成 `off` 或刪掉。
3. 不用部署。下一次那支 workflow 跑時生效。

## 排程時間表（台北時間）

觸發來源有兩種：Cloudflare Worker `market-ana-cron-trigger`（準時；設定在 `cron-trigger/wrangler.toml` 與 `worker.js` 的 `JOBS`）、GitHub 自己的 `schedule`（常晚好幾小時）。

| 時間 | 觸發 | workflow |
|---|---|---|
| 每天 08:00 | Worker | `announcements`（含基金淨值 `fund-nav`）→ 跑完接 `market-news` 盤前班（週末不跑）與 `workflow-keepalive` |
| 交易日約 11 次、週末 1 次 | Worker | 資料更新檢查與異常推播（不經 GitHub；要 0040 套了才送） |
| 平日 07:20 | Worker | `forecast-morning` → 接 `watch-ledger` 晨間 |
| 平日 07:30 | GitHub schedule | `level-daily`（總開關關著時不寫） |
| 平日 09:00–13:59 每分鐘 | Worker | 盯價（不經 GitHub） |
| 平日 10:00 | GitHub schedule | `forecast-replay`（開關關著時不寫） |
| 平日 11:30、14:00、18:30、23:00 | Worker | `market-news`（midday、close、evening、us_open） |
| 週二到週六 06:30 | Worker | `market-news`（us_close） |
| 平日 15:30、16:30、18:00、20:30 | Worker＋GitHub schedule（兩邊都會觸發，同一天成功後其餘回 already-done） | `daily-update` → 發布成功後接 `forecast-ledger`→`watch-ledger`、`ai-digest`、`friend-ai-digest`、`broker-branches` |

注意：Cloudflare 免費方案整個帳號最多 5 個排程時段，**已經用滿**。新排程要併進既有時段（見 `cron-trigger/wrangler.toml` 的註解），或改用 GitHub schedule（會延遲）。改 Worker 排程後要按 `deploy-cron-trigger` 部署（[ops-buttons.md](ops-buttons.md)）。

## GitHub 60 天沒活動會停用排程

公開倉庫 60 天沒有任何活動，GitHub 會停用含 `schedule` 的 workflow，停用後連排程器送來的觸發也會被拒。`workflow-keepalive` 每天自動把它們重新啟用（[ops-buttons.md](ops-buttons.md)）。萬一還是被停用，Actions 頁會出現黃色提示，按 **Enable workflow** 就恢復。
