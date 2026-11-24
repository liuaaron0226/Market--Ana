# Market--Ana 營運手冊（總覽）

這份手冊寫給兩種人：

- **站主本人**：用手機或瀏覽器處理常見狀況（看今天有沒有更新、重跑、補金鑰）。
- **接手維護的 AI**（Gemini／Antigravity `agy`）：從沒看過這個專案也能照著做。請先讀 [for-ai-maintainers.md](for-ai-maintainers.md)。

> 這個倉庫是**公開的**。手冊裡只寫金鑰的「名稱」，不寫值；不寫信箱、不寫站主關注或持有哪些股票。

## 名詞（第一次出現先解釋）

| 名詞 | 一句話解釋 |
|---|---|
| GitHub Actions | GitHub 提供的雲端電腦，照排程幫我們跑程式。每一支排程叫一個 **workflow**，每跑一次叫一個 **run**。 |
| Cloudflare Pages | 放網站的地方。網站網址 `market-ana.pages.dev`。 |
| D1 | Cloudflare 的資料庫，名字 `market-ana-accounts`（會員、自選股、預測、消息…都在這）。 |
| R2 | Cloudflare 的檔案倉庫，bucket 名字 `market-ana-state`（每日更新用的「工作集」、原件）。 |
| Worker（cron-trigger） | Cloudflare 上的小程式 `market-ana-cron-trigger`，準時去「按」GitHub 的 workflow，另外盤中每分鐘盯價、推播。 |
| agy | Google 官方的 Antigravity CLI，排程用它呼叫 Gemini（吃站主的 Google AI Pro 額度）。 |
| secret／variable | GitHub 或 Cloudflare 裡存設定的地方。secret＝密碼類、看不到值；variable＝開關類、看得到值。 |
| 乾跑（dry run） | 照樣跑、但不改任何正式資料，用來先試。 |

## 什麼東西在哪裡跑

| 做什麼 | 在哪裡跑 | 什麼時候（台北時間） | 停了你會注意到什麼 |
|---|---|---|---|
| 每日更新：抓收盤資料、重建網站、發布 | GitHub workflow `daily-update`（由 Worker 準時觸發，GitHub 自己的排程當備援） | 平日 15:30、16:30、18:00、20:30（四個時段，官方資料出來那一班才真的發布） | 首頁、個股頁的數字停在舊的一天；圖卡出現「最近一次沒更新到」 |
| 公司重大訊息原件 | `announcements` | 每天 08:00（含週末） | 個股頁公告少了新的 |
| 首頁「消息」 | `market-news` | 平日 08:00（接在公告後）、11:30、14:00、18:30、23:00；週二到週六 06:30 | 消息停在舊的一班、或寫「本時段無資料」 |
| 大盤事前預測晚間版＋對帳 | `forecast-ledger`（接在 daily-update 發布後） | 每日更新發布後 | 預測頁今天沒有晚間版、或記成漏記 |
| 大盤事前預測晨間版 | `forecast-morning`（Worker 觸發） | 平日 07:20 | 預測頁早上沒有晨間版、記成漏記 |
| 自選股每日判斷 | `watch-ledger`（接在兩個預測後面） | 預測跑完後 | 自選股的判斷停在舊的一天 |
| 朋友的 AI 解讀 | `friend-ai-digest`（接在 daily-update 後） | 每日更新發布後 | 朋友那邊的解讀沒更新（用朋友自己的金鑰） |
| 站主 AI 解讀 | `ai-digest`（接在 daily-update 後） | 每日更新發布後 | 目前總開關關著，平常不寫（見 [flags-and-schedules.md](flags-and-schedules.md)） |
| 主力分點 | `broker-branches`（接在 daily-update 後） | 每日更新發布後 | 個股頁主力分點副圖停在舊日期 |
| 現金水位（產品規則） | `level-daily` | 平日 07:30 | 目前總開關關著，平常不寫 |
| 事前預測歷史重播 | `forecast-replay` | 平日 10:00（開關打開才寫） | 只影響重播參考成績 |
| 盤中即時盯價、到價推播 | Worker `market-ana-cron-trigger` | 平日 09:00–13:59 每分鐘 | 手機收不到到價推播 |
| 登入名單檢查 | Worker `market-ana-eval` | 每次登入 | 核准的人登不進去 |
| 手動檢查用 | `agy-auth-probe`（Gemini 登入檔是否還有效）、`eodhd-probe`（EODHD 金鑰） | 只能手動 | — |

全部都在雲端跑，**不需要這台電腦**。例外與缺口見 [handover.md](handover.md)。

## 出事時開哪一份

| 狀況 | 打開 |
|---|---|
| 想知道今天有沒有正常更新 | [daily-check.md](daily-check.md) |
| 某支 workflow 紅了、要重跑；改了程式要上線；同一天要重新發布 | [rerun-and-redeploy.md](rerun-and-redeploy.md) |
| 某個金鑰過期、AI 文字變成「依當天數字自動整理」、Gemini 登入失效 | [credentials.md](credentials.md) |
| 基金淨值有缺口、要補抓歷史 | [data-gaps.md](data-gaps.md) |
| 想開／關某個功能、某個排程 | [flags-and-schedules.md](flags-and-schedules.md) |
| 交給 Gemini／Antigravity 修東西 | [for-ai-maintainers.md](for-ai-maintainers.md) |
| 交接時還在做的事 | [handover.md](handover.md) |
| 只有站主本人能做的事 | [owner-todo.md](owner-todo.md) |
| 資金版圖的公司名單 | [chain-map-companies.md](chain-map-companies.md) |

## 三條最重要的規則

1. **推上 `main` 等於上線**：下一次每日發布（或手動 redeploy）就會把 `main` 的程式放到正式站。還在改的東西放別的分支。
2. **任何會改正式資料的手動操作，先跑一次乾跑**（`dry_run` 勾著），看綠了再正式跑。
3. **不准留下「要在某台電腦上手動跑」的步驟**。新功能都要能在 GitHub／Cloudflare 上自己跑。
