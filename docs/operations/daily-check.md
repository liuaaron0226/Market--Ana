# 每天兩分鐘檢查（手機就能做）

建議時間：**平日晚上 19:00 以後**（18:00 那一班每日更新通常在 18:30 前後跑完）。

## 第一步：看網站（1 分鐘）

打開 `https://market-ana.pages.dev`（要登入）。

| 看哪裡 | 正常 | 不正常 |
|---|---|---|
| 首頁大盤圖卡、籌碼卡 | 最後一根是今天（交易日）；**圖卡標題下沒有小字** | 圖卡標題下出現「最近一次沒更新到」或「資料截至 某日」→ 那一塊過期了 |
| 首頁「盤勢解讀」／籌碼白話 | 有一句結論，沒有附註 | 附註寫「依當天數字自動整理」→ 那一班 Gemini 沒寫成，改用程式套句（資料沒問題，AI 那段退化了） |
| 首頁「消息」 | 有今天的幾班 | 某一組寫「本時段無資料」→ 那一班來源沒抓到 |
| 個股頁新聞／公告區 | 有新聞 | 寫「今天沒抓到」或「MM-DD 之後沒抓到」→ 該來源這天失敗（常見原因：Google News 限流） |
| 預測對帳頁 | 今天有晚間版、早上有晨間版 | 記成「漏記」→ 那一份沒寫成（多半是 Gemini 登入或額度問題） |
| 設定（齒輪）→ AI 模型／用量 | 用量有今天的紀錄 | 出現「上一時段（時間）沒讀到你的設定，用了預設」→ 那一班讀資料庫失敗，用了預設模型；偶發一次不用處理 |

「依當天數字自動整理」或「漏記」**連續兩天**出現時，原因可能是 Gemini 登入檔失效，也可能只是模型沒回來、逾時、額度用完或寫的內容沒通過核對。先分辨：Actions → `agy-auth-probe` → Run workflow（預設值）。
- 紅叉 → 登入檔失效，照 [credentials.md](credentials.md)〈重做 Gemini 登入檔〉。
- 綠勾 → 登入沒問題，多半是額度或模型暫時的問題；過一兩天還是這樣就交給 AI 查 `daily-update` 執行摘要「首頁白話分析」那段的失敗原因。

## 第二步：看 GitHub（1 分鐘）

1. 手機瀏覽器打開 `https://github.com/liuaaron0226/Market--Ana/actions`（要登入 GitHub）。
2. 左邊（手機在上方「All workflows」選單）依序點這幾支，看最上面一筆：

| workflow 名稱 | 今天應該看到 |
|---|---|
| `daily-update` | 今天 15:30～20:30 有四筆左右；**至少一筆綠勾**，其他可能是灰色（等待／已完成）或綠勾 |
| `forecast-ledger` | 晚上一筆綠勾 |
| `forecast-morning` | 早上 07:20 左右一筆綠勾 |
| `watch-ledger` | 早晚各一筆綠勾 |
| `market-news` | 白天、晚上各班綠勾 |
| `broker-branches` | 每日更新後一筆綠勾 |

3. 顏色的意思：綠勾＝成功；紅叉＝失敗；灰色斜線＝被跳過（正常，例如休市或條件不成立）；黃點＝正在跑。

## 「綠勾但其實有問題」怎麼看

很多步驟設成「失敗不擋發布」，所以 run 是綠的，但裡面某一步失敗了：

1. 點進那一筆 run → 點 `update`（或該工作名稱）。
2. 步驟清單裡有**紅叉、但整體仍是綠勾**的那幾步，就是「部分失敗」。常見的：
   - 「基金對照 0050…」紅叉 → 基金淨值有缺口或來源失敗，看 [data-gaps.md](data-gaps.md)。
   - 「白話分析：還原登入檔…」或「消息型盯項：準備 agy」紅叉 → Gemini 登入檔問題，看 [credentials.md](credentials.md)。
   - 「個股新聞標題…」紅叉 → 新聞來源限流，隔天通常自己好。
3. run 頁面最下方的 **Summary**（摘要）有每日更新的結果 JSON：`"status": "success"`＋`"published": true` 才代表今天真的發布了新資料；`"status": "waiting"` 代表官方資料還沒出來、等下一班。

## 什麼時候要動手

| 看到 | 做什麼 |
|---|---|
| `daily-update` 今天四班都沒有綠勾，或 20:30 那班紅叉 | [rerun-and-redeploy.md](rerun-and-redeploy.md)〈重跑失敗的 run〉 |
| 今天一筆 run 都沒有（連灰色都沒有） | 觸發器沒在動：可能是 `GITHUB_DISPATCH_TOKEN` 過期（[credentials.md](credentials.md)），或排程被 GitHub 停用（Actions 頁會有黃色提示，按「Enable workflow」；平常由 `workflow-keepalive` 自動處理，見 [ops-buttons.md](ops-buttons.md)） |
| 紅叉訊息寫 Cloudflare token 無效 | [credentials.md](credentials.md)〈CLOUDFLARE_API_TOKEN〉 |
| 紅叉訊息寫 `exceeded D1's free tier daily row write limit` | 下面〈D1 寫入額度用完〉 |
| 紅叉訊息寫「The job was not started because … spending limit」 | GitHub Actions 免費時數用完（倉庫是公開的，正常不會發生；若倉庫又被改回私人就會） |

## D1 寫入額度用完（2026-11-24 晚發生過）

正式資料庫（D1）是 Cloudflare 免費方案，**每天最多寫 10 萬列**（刪除也算），**台北早上 08:00 重置**（UTC 00:00）。

- **看到什麼**：run 紅叉，訊息含 `exceeded D1's free tier daily row write limit`；或 `d1-migrate` 摘要寫「今天寫入額度用完」；網站上存自選股、庫存、畫線失敗。
- **用完時**：所有寫入都失敗到 08:00——使用者存不了東西；有寫入的排程（消息各班、07:20 晨間預測）什麼都沒記到，晨間預測記成漏記。
- **怎麼辦**：沒有辦法提早恢復。**等台北 08:00 之後**，把失敗的 run 重跑（[rerun-and-redeploy.md](rerun-and-redeploy.md) A）。晨間預測過了 08:45 補不回來。
- **通知的限制（沒收到推播不代表沒事）**：
  - 「寫入額度用完」的推播只在台北 11:30、15:30、20:30 與隔天 06:30 那幾班排程送（都掛在週一到週五的排程上）：晚上 20:30 以後才用完，要到隔天 06:30 才通知；週末用完不會通知。
  - 正式資料庫還沒套 0040 之前，**任何資料更新提醒都不會送**，連這一則也不會（見 [owner-todo.md](owner-todo.md) 第 1 項）。
  - 額度用完期間，一般的資料更新提醒、到價與觸發線推播也都會停。
  - 排程器（Cloudflare）自己某一班失敗不會寄信，只看得到 Cloudflare 後台：Workers & Pages → `market-ana-cron-trigger` → Logs／Cron events。
- **額度用完期間的即時報價**：防止濫用的限流會變寬鬆，朋友的報價請求可能超過富果的額度，富果回 429 時盤中盯價會漏。富果來信抱怨或一直 429：把 `MARKET_ANA_LIVE_QUOTES` 改成 `owner` 再 redeploy（[flags-and-schedules.md](flags-and-schedules.md)〈行情商來函或帳號被停時怎麼關〉）。
- **怎麼避免**：平常日**不要做 `republish`**（重新發布會整套重寫一次），除非真的必要；主力分點的整理、回補、大量回補一天只做一種。付費的 Workers 方案可以解除這個上限，但站主目前的原則是不用付費方案。

## 出事時怎麼知道

- 設定（齒輪）→「資料更新」：每份資料的最後更新時間與狀態（目前只有站主看得到）。說明見 [data-health.md](data-health.md)。
- 手機推播「資料更新異常／已恢復」：由排程器送，一天最多 4 則，說明見 [data-health.md](data-health.md)。正式資料庫套了 0040 之後才會正常記錄（見 [owner-todo.md](owner-todo.md) 第一項）。2026-11-25 08:00 寫入額度重置後，第一個時段可能收到「資料已恢復」之類的通知，是正常的。
- GitHub 預設的失敗通知信（GitHub → Settings → Notifications → Actions，確認有打開）。
- 還沒有的：金鑰到期提醒。
