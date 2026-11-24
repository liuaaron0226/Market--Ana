# 只有站主本人能做的事

全部用網頁或手機就能做。做完在前面打勾。GitHub 的按鈕都在 `https://github.com/liuaaron0226/Market--Ana/actions` → 左邊點名稱 → 右邊 **Run workflow**（分支選 `main`）。

## 1. 明天（2026-11-25）早上 08:00 以後，先做這件：`d1-migrate`

為什麼要 08:00 以後：網站資料庫今晚的「每天寫入額度」用完了，台北 08:00 才重置。在那之前存自選股、庫存、畫線都會失敗，這是正常的，等就好。

**這一步也是打開「資料更新異常」手機通知的開關**：在 `d1-migrate` 成功之前，手機**一則提醒都不會收到**——連「資料庫寫入額度用完」那一則也不會。

**第一次：乾跑（只檢查，不改任何東西）**
- [ ] 打開 `https://github.com/liuaaron0226/Market--Ana/actions`。
- [ ] 左邊清單點 **d1-migrate**（手機在上方「All workflows」選單裡找）。
- [ ] 右邊點 **Run workflow** → 分支 `main` → **什麼都不改**（`dry_run` 是勾著的）→ 綠色 **Run workflow**。
- [ ] 等一兩分鐘，那一筆變成綠勾。點進去，往下看 **Summary**（摘要）。應該寫到這三件事：
  1. 補記帳 0033～0038
  2. 套用 0039
  3. 套用 0040（可能還有 0041）

**第二次：正式執行**
- [ ] 摘要對了，再按一次 **Run workflow**，這次把 `dry_run` 的**勾取消**，再按綠色 **Run workflow**。
- [ ] 成功的樣子：綠勾，摘要寫「**資料庫已是最新**」。想再確認可以再按一次乾跑，它會說沒有要做的事。

**之後會怎樣**
- 資金版圖頁 `market-ana.pages.dev/chain-map`：現在就有資料（到 2026-10 的那一份）；今天傍晚 18:00 那一班每日更新跑完後，會換成每天更新的完整資料。
- 手機會收到「資料更新異常／已恢復」的通知（設定頁「資料更新」那一籤本來就能看）。08:00 後第一次收到「已恢復」之類的通知是正常的。

**失敗時看哪裡**
- 紅叉或摘要寫「今天寫入額度用完」→ 過一兩個小時再從「第一次」重做。
- 摘要寫「不一致」「拒絕」或其他看不懂的字 → **不要一直重按**。把摘要整段複製給接手的 AI（Codex 或 Gemini），請它照 [ops-buttons.md](ops-buttons.md) 處理。

## 2. 法人歷史回補（資金版圖用）

- [ ] 看 Actions → `institutional-backfill` 最新一筆的摘要。寫「還剩…約再按幾次」就再按一次：**取消勾選 `dry_run`**，其他用預設。一次約 2 小時。
- [ ] 重複到摘要寫「全部補完了，不用再按」（總共大約 2 次）。上一次還在跑（黃點）時不用按。

## 3. 退訂 Claude 當天

- [ ] 打開 Claude 桌面 App → 排程任務 → 把「【交易日 18:30】Market--Ana 水位紙上追蹤」（`market-ana-level-paper-track`）**關掉**。這項追蹤已決定停止，不搬上雲端。
- [ ] GitHub → 右上頭像 → Settings → Notifications → 確認 **Actions** 的失敗通知有打開（寄 email）。
- [ ] **把整個專案資料夾 `C:\Users\User\Desktop\Market--Ana` 備份到外接硬碟或雲端硬碟。** 下面這些只存在這台電腦、不在 GitHub 上，電腦壞了就沒了，之後接手的 AI 也需要它們：
  - `data\`（本機資料：累積型原件的唯一備份）
  - `.scratch\`（舊規格與交接草稿、紙上追蹤紀錄）
  - `STATUS.md`、`ANALYSIS_PLAN.md`（進度沿革、你歷次的決定）
  - `.tmp\`（暫存；Gemini 登入檔腳本原件在 `.tmp\g4-probe\`）
  - 注意：備份裡**不要**留 `AGY_LOGIN_BUNDLE.txt` 這類登入檔；備份放在只有你能開的地方。
  - 已經做了一部分：筆記與精簡版 `.scratch` 在你的 Google 雲端硬碟「Market-Ana 備份 2026-11-24」。**還沒備份的是 `data\`（約 9 GB）與 `.tmp\`（約 7 GB）**，建議用外接硬碟。

## 4. 金鑰到期日（查一次，寫在這裡，並設手機提醒到期前一週換）

- [ ] `GITHUB_DISPATCH_TOKEN`：GitHub → 頭像 → Settings → Developer settings → Personal access tokens → 到期日：＿＿＿＿（**最重要**：過期後所有準時排程都停）
- [ ] `CLOUDFLARE_API_TOKEN`：Cloudflare → 頭像 → My Profile → API Tokens → 到期日：＿＿＿＿。同一頁看它有沒有 **Workers Scripts：Edit** 權限：有／沒有＿＿＿＿（**還沒有人確認過**。沒有的話，按 `deploy-cron-trigger` 正式部署會出現驗證錯誤，原因就是這個；加上權限或換一把新的，見 [credentials.md](credentials.md)）
- [ ] `R2_ACCESS_KEY_ID`／`R2_SECRET_ACCESS_KEY`：Cloudflare → R2 → Manage R2 API Tokens → 到期日：＿＿＿＿
- [ ] `FUGLE_API_KEY`：富果開發者網站 → API 金鑰 → 到期日：＿＿＿＿
- [ ] `EODHD_API_KEY`：eodhd.com 會員後台 → 狀態：＿＿＿＿
- [ ] `MASSIVE_API_KEY`：massive.com 會員後台 → 狀態：＿＿＿＿
- [ ] Gemini 登入檔 `AGY_LOGIN_BUNDLE`：沒有到期日。每月按一次 `agy-auth-probe`（預設值）看是否綠勾；紅了照 [credentials.md](credentials.md) 重做（目前要用這台電腦的 WSL）。

換金鑰的步驟在 [credentials.md](credentials.md)。

## 5. 用 iPhone 實際試

- [ ] 資金版圖：網址列打 `market-ana.pages.dev/chain-map`（首頁還沒有入口）。點層、環節、個股能展開收合；說明板能拖；底部不被擋。（要先做完第 1 項）
- [ ] 從首頁的清單（例如籌碼的成交金額排行）點進一檔股票，按左上返回、也試手機的上一頁手勢：要回到剛才那個清單和位置。
- [ ] 首頁資金地形：上下滑能捲頁、左右拖能轉、點小樓不跳頁。
- [ ] 分析工具選單（首頁大盤與個股頁）。
- [ ] 首頁動效「播一陣子就停」可不可以接受。
- [ ] 用一個朋友的帳號看：盯項、盤勢解讀、海外分時、即時報價、預測對帳、資金版圖都看得到。

## 6. 以後程式有改時要記得按的

- [ ] 接手的 AI 改了 `cron-trigger/` 底下的任何檔，或 `cloud/lib/data-health` 開頭的檔，推上 main 之後要按 **`deploy-cron-trigger`**：先預設（乾跑）按一次 → 綠了再取消勾選 `dry_run` 按一次。成功時摘要會顯示一個 **Version ID**。推上 main **不會**自動更新排程器。步驟見 [ops-buttons.md](ops-buttons.md)。
- [ ] 有新的資料庫變更（AI 會說「要套遷移」）時按 `d1-migrate`，做法同第 1 項。

## 7. 等你決定的事（AI 來問時再回答）

- 主力分點要不要涵蓋整個股票池（約 350 檔）：免費方案要一年以上才補得完。選項是「升級付費方案後才做」或「縮到自選與庫存」。你決定前，AI 不會自己開始。

- 資金版圖：首頁入口放哪、點公司要不要連到個股頁、當月未過完要不要顯示。
- 收到行情商來函時，照 [flags-and-schedules.md](flags-and-schedules.md)〈行情商來函或帳號被停時怎麼關〉改回只給自己。
- [handover.md](handover.md) C 段每一項標「同意」的那一行。

## 每天（選做，兩分鐘）

- [ ] 照 [daily-check.md](daily-check.md) 看一次，或看設定（齒輪）→「資料更新」。
