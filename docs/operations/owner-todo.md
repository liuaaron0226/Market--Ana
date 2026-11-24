# 只有站主本人能做的事

全部用網頁或手機就能做。做完在前面打勾。GitHub 的按鈕都在 `https://github.com/liuaaron0226/Market--Ana/actions` → 左邊點名稱 → 右邊 **Run workflow**（分支選 `main`）。

## 1. 明天（2026-11-25）早上 08:00 以後，先做這件

- [ ] 按 `d1-migrate` 一次，**全部用預設**（`dry_run` 勾著）。等綠勾，點進去看摘要：應該寫「補記帳 0033～0038」與「套用 0039、0040」。
- [ ] 摘要對了，再按一次 `d1-migrate`，這次**取消勾選 `dry_run`**。等綠勾。
- 為什麼要 08:00 以後：資料庫今晚的寫入額度用完了，08:00 才重置。
- 沒做之前：資金版圖頁會顯示「尚未就緒」；手機收不到「資料更新異常」推播（設定頁「資料更新」那一籤已經能看）。
- 摘要寫「今天寫入額度用完」→ 晚一點再按；寫其他看不懂的錯誤或「不一致、拒絕」→ 不要硬按，交給 AI（附上摘要）。

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

## 4. 金鑰到期日（查一次，寫在這裡，並設手機提醒到期前一週換）

- [ ] `GITHUB_DISPATCH_TOKEN`：GitHub → 頭像 → Settings → Developer settings → Personal access tokens → 到期日：＿＿＿＿（**最重要**：過期後所有準時排程都停）
- [ ] `CLOUDFLARE_API_TOKEN`：Cloudflare → 頭像 → My Profile → API Tokens → 到期日：＿＿＿＿。同一頁順便看它有沒有 **Workers Scripts：Edit** 權限（`deploy-cron-trigger` 正式部署要用）：有／沒有
- [ ] `R2_ACCESS_KEY_ID`／`R2_SECRET_ACCESS_KEY`：Cloudflare → R2 → Manage R2 API Tokens → 到期日：＿＿＿＿
- [ ] `FUGLE_API_KEY`：富果開發者網站 → API 金鑰 → 到期日：＿＿＿＿
- [ ] `EODHD_API_KEY`：eodhd.com 會員後台 → 狀態：＿＿＿＿
- [ ] `MASSIVE_API_KEY`：massive.com 會員後台 → 狀態：＿＿＿＿
- [ ] Gemini 登入檔 `AGY_LOGIN_BUNDLE`：沒有到期日。每月按一次 `agy-auth-probe`（預設值）看是否綠勾；紅了照 [credentials.md](credentials.md) 重做（目前要用這台電腦的 WSL）。

換金鑰的步驟在 [credentials.md](credentials.md)。

## 5. 用 iPhone 實際試

- [ ] 資金版圖：網址列打 `market-ana.pages.dev/chain-map`（首頁還沒有入口）。點層、環節、個股能展開收合；說明板能拖；底部不被擋。（要先做完第 1 項）
- [ ] 首頁資金地形：上下滑能捲頁、左右拖能轉、點小樓不跳頁。
- [ ] 分析工具選單（首頁大盤與個股頁）。
- [ ] 首頁動效「播一陣子就停」可不可以接受。
- [ ] 用一個朋友的帳號看：盯項、盤勢解讀、海外分時、即時報價、預測對帳、資金版圖都看得到。

## 6. 等你決定的事（AI 來問時再回答）

- 資金版圖：首頁入口放哪、點公司要不要連到個股頁、當月未過完要不要顯示。
- 主力分點要不要開給朋友：你說「我希望我股票池的股票都有」——是指抓「股票池全部」還是別的範圍？
- 收到行情商來函時，照 [flags-and-schedules.md](flags-and-schedules.md)〈行情商來函或帳號被停時怎麼關〉改回只給自己。
- [handover.md](handover.md) 每一項最後一欄「要站主同意的」。

## 每天（選做，兩分鐘）

- [ ] 照 [daily-check.md](daily-check.md) 看一次，或看設定（齒輪）→「資料更新」。
