# 資料更新狀態：提醒的意思與第一步要看什麼

設定（齒輪）→「資料更新」那一籤，一份資料一列：名稱、最後更新時間、狀態。
手機收到「資料更新異常」或「資料已恢復」的通知，點了會直接打開這一籤。
每份資料出問題時只通知一次、恢復時再通知一次；一天最多 4 則。台股休市日與週末不會因為「沒更新」而通知。

## 四種狀態

| 狀態 | 意思 |
|---|---|
| 正常 | 最近一班有按時更新，內容也是完整的。 |
| 延遲 | 最近一班過了期限還沒更新（例如每日行情過了 21:30 還停在前一天）。 |
| 停止更新 | 連續兩班以上沒有更新。 |
| 部分異常 | 有更新，但內容降級：改用程式套句、記為漏記、部分項目抓取失敗、基金淨值缺日或數字對不上。 |
| 無法檢查 | 這一份的檢查本身出錯（程式查錯欄位、資料庫讀不到）。不會推播；請交給維護的 AI 看 `cloud/lib/data-health.js`。 |

## 「資料庫寫入額度用完」

網站用 Cloudflare D1 免費方案，每天最多寫入 10 萬列，台北早上 8 點重算。用完時看得到資料，但新增自選、畫線、排程寫入都會失敗。
這時會收到「資料庫寫入額度用完」：同一天最多 4 則（台北 11:30、15:30、20:30、06:30 這幾個時段才送）。不用處理，早上 8 點後自動恢復；
如果連續幾天都這樣，代表某支排程寫太多，請交給維護的 AI 查是哪一支。

## 收到通知時先看哪裡

所有排程都在 GitHub 的 Actions 頁（倉庫 → Actions），每一支排程的名稱寫在下表最右欄。
點進那一支最近一次的執行，紅色叉叉是失敗；綠色勾勾但這裡顯示異常，就是「某一步失敗但被略過」，展開黃色驚嘆號那一步看原因。

| 名稱 | 什麼時候該更新 | 延遲或停止更新：先看 | 部分異常：通常是 | 排程 |
|---|---|---|---|---|
| 每日行情 | 交易日 21:30 前 | Actions 有沒有跑、最後一步的錯誤；整頁都沒有新的執行＝排程器或 GitHub 權杖出問題（見下面「全部同時延遲」） | — | daily-update |
| 籌碼卡解讀 | 跟著每日行情 | — | AI 沒有回應或內容沒通過核對，改用程式套句。連續幾天都這樣＝Gemini 登入檔（AGY_LOGIN_BUNDLE）失效或額度用完，照登入檔手冊重做 | daily-update |
| 首頁消息 | 交易日 12:45、15:15 前 | market-news 有沒有跑 | 模型沒有完成分類，只有標題（Gemini 額度或登入檔） | market-news |
| 盤勢解讀 | 交易日 12:45、15:15、22:30 前 | market-news、daily-update | 改用程式套句（同上，Gemini） | market-news、daily-update |
| 大盤預測 | 交易日 08:45、22:30 前 | forecast-morning、forecast-ledger | 記為漏記：那一班太晚跑或模型沒回來 | forecast-morning、forecast-ledger |
| 自選股判斷 | 交易日 09:15、23:00 前 | watch-ledger（接在大盤預測後面，預測沒跑它也不會跑） | 有幾檔記為漏記 | watch-ledger |
| 追蹤事項 | 交易日 22:30 前 | daily-update 裡「盯項每日檢查」那一步 | — | daily-update |
| 主力分點 | 交易日 23:30 前 | broker-branches | 有幾檔抓取失敗：來源網站改版或擋連線 | broker-branches |
| 個股新聞 | 交易日 22:30 前 | daily-update 裡「個股新聞標題」那一步（Google News 常回 429，隔天通常會好） | — | daily-update、market-news |
| 國外行情 | 交易日 22:30 前 | daily-update 裡「國外同類股行情」那一步 | 有幾檔抓取失敗：EODHD／Massive 額度或金鑰到期 | daily-update |
| 基金淨值 | 淨值日不晚於 4 個交易日前 | daily-update 裡「基金對照 0050」那一步；漏抓超過約 3 個營業日要到集保網頁補 | — | daily-update |
| 現金水位 | 交易日 09:30 前（開關有開才會出現） | level-daily | — | level-daily |
| 今日 AI 解讀 | 交易日 22:30 前（開關有開才會出現） | ai-digest | 解讀未完成（Gemini） | ai-digest |
| 資金版圖 | 交易日 22:30 前（每日排程寫過資料庫才會出現；只看資料庫，R2 那一份沒有檢查） | 資金版圖的排程 | — | — |
| 排程保活 | 最近 8 天內至少成功一次（超過 16 天＝停止；資料庫套過 0041 才會出現） | Actions 左邊的 workflow-keepalive：紅色就點進去看摘要。寫「必要的卻被手動停用」＝到那一支按「Enable workflow」；寫「啟用失敗（HTTP 403）」或「心跳 commit 推不上 main」＝GitHub 的權限設定被改過（Settings → Actions → General → Workflow permissions 要允許寫入，main 不能擋 github-actions 推送）。整頁都沒有新的執行＝保活本身被停用，按「Enable workflow」再手動跑一次 | — | workflow-keepalive |

## 全部同時延遲

好幾份資料同時「延遲」或「停止更新」，而且 Actions 頁沒有新的執行：

1. 排程是由 Cloudflare 的排程器（market-ana-cron-trigger）去叫 GitHub 的，它用的權杖是 `GITHUB_DISPATCH_TOKEN`。權杖到期就整個停：GitHub → Settings → Developer settings → Personal access tokens 重新產生，再到 Cloudflare 的 Workers → market-ana-cron-trigger → Settings → Variables and Secrets 換掉。
2. 公開倉庫 60 天沒有任何提交，GitHub 會把有排程的 workflow 停用：Actions 頁上方會有黃色提示，按「Enable workflow」。
   平常不會發生：workflow-keepalive 每 25 天自動提交一次 `ops/heartbeat.txt`，並把被停用的排程重新啟用（上表「排程保活」那一列）。
3. Cloudflare 權杖（`CLOUDFLARE_API_TOKEN`）到期：每一支排程的第一步「驗證 Cloudflare token」就會失敗。

## 誰在檢查

檢查跑在 Cloudflare 的排程器上（不是 GitHub），每個交易日約 11 次、週末每天 1 次；所以 GitHub 整個停擺也照樣會通知。
判斷規則全部寫在 `cloud/lib/data-health.js` 最上面的 `RULES` 表（名稱、期限、晚幾天算正常），要改門檻只改那一張表。
