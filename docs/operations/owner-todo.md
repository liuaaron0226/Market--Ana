# 只有站主本人能做的事

每一項都只要網頁或手機。做完在前面打勾。

## 金鑰到期日（查一次，寫在這裡）

- [ ] `GITHUB_DISPATCH_TOKEN`：GitHub → 頭像 → Settings → Developer settings → Personal access tokens → 看到期日：＿＿＿＿（**最重要**：過期後所有準時排程都停，而且不會通知）
- [ ] `CLOUDFLARE_API_TOKEN`：Cloudflare → 頭像 → My Profile → API Tokens → 看到期日：＿＿＿＿
- [ ] `R2_ACCESS_KEY_ID`／`R2_SECRET_ACCESS_KEY`：Cloudflare → R2 → Manage R2 API Tokens → 看到期日：＿＿＿＿
- [ ] `FUGLE_API_KEY`：富果開發者網站 → API 金鑰頁 → 看到期日：＿＿＿＿
- [ ] `EODHD_API_KEY`：eodhd.com 會員後台 → 方案與金鑰狀態：＿＿＿＿
- [ ] `MASSIVE_API_KEY`：massive.com 會員後台 → 金鑰狀態：＿＿＿＿
- [ ] Gemini 登入檔 `AGY_LOGIN_BUNDLE`：沒有固定到期日；每月手動跑一次 GitHub Actions 的 `agy-auth-probe` 看是否綠勾。紅了照 [credentials.md](credentials.md) 重做（目前要用這台電腦的 WSL）。
- [ ] 在手機或行事曆設提醒：每個到期日前一週換新。

## 退訂 Claude 時

- [ ] 打開 Claude 桌面 App → 排程任務 → 把「【交易日 18:30】Market--Ana 水位紙上追蹤」（`market-ana-level-paper-track`）**關掉**。這項追蹤已決定停止，不搬上雲端；已記錄的資料留在原處（見 [handover.md](handover.md) (g)）。
- [ ] GitHub → Settings → Notifications → Actions：確認「失敗時寄信」有打開（目前出事時唯一的通知管道）。

## 等你同意才會做的正式動作

- [ ] [handover.md](handover.md) 每一項最後一欄「要你同意的」：推上 main、部署、套正式資料庫、改開關、開給其他人。AI 來問時再回答即可。

## 要你用 iPhone 實際試的

- [ ] 首頁資金地形：上下滑能捲頁、左右拖能轉、點小樓不跳頁。
- [ ] 分析工具選單（首頁大盤與個股頁）。
- [ ] 首頁動效「播一陣子就停」可不可以接受。
- [ ] 從清單點進個股、按返回，回到原來的清單（做好後）。
- [ ] 資金版圖頁（做好後）：說明板拖動、底部安全區。

## 每天（選做，兩分鐘）

- [ ] 照 [daily-check.md](daily-check.md) 看一次。
