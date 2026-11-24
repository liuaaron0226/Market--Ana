# 維運按鈕：資料庫變更、部署排程器、保活

三件以前要在電腦上打指令的事，現在都是 GitHub 網頁上的按鈕。入口：`https://github.com/liuaaron0226/Market--Ana/actions` → 左邊點 workflow 名稱 → 右邊 **Run workflow**，分支選 `main`。

共同規則：**預設是乾跑**（只檢查、用白話說明會做什麼、什麼都不改）。先按一次看 Summary（摘要），看懂了再取消勾選 `dry_run` 按第二次。正式執行只能從 `main` 跑。

## 1. `d1-migrate`：套用資料庫變更

**什麼時候用**：程式更新附帶新的資料庫變更（`cloud/migrations/00NN_*.sql`），交接或 AI 告訴你「要套遷移」時。重按沒有副作用。

1. Run workflow，全部用預設（`dry_run` 勾著、`reconcile_only` 不勾）。
2. 等綠勾，點進去看 Summary：它會用白話列出「要補記帳的」與「要套用的」。
3. 沒問題就再按一次，這次**取消勾選 `dry_run`**。
4. 好的結果：綠勾；Summary 說補記帳、套用完成；再按一次乾跑會說沒有要做的事。

背景（給 AI）：正式資料庫的記帳表 `d1_migrations` 原本只記到 0032，0033～0038 是直接執行過、沒記帳的。這支會**先逐項核對**資料表、欄位、索引、觸發器確實存在，只在記帳表補一筆，**絕不重跑它們的 SQL**；然後才用 wrangler 依序套用真正還沒套的。記帳和資料庫對不上（只套一半、順序不對、已記帳的東西不見）就拒絕、什麼都不寫，印出哪裡不一致——這時交給 AI，不要硬套。`reconcile_only`＝只補記帳不套新的，平常不用勾。**補帳完成前，任何人都不得執行 `wrangler d1 migrations apply`**（會重跑 0033～0038）；之後也一律用這個按鈕，不要手打。

失敗時：
- 摘要說「今天寫入額度用完」→ D1 免費方案每天寫入上限用完了，**台北早上 8 點以後再按**（見 [daily-check.md](daily-check.md)〈D1 寫入額度用完〉）。
- 其他 → 交給 AI，附上摘要內容。

## 2. `deploy-cron-trigger`：部署排程器（Worker）

**什麼時候用**：`cron-trigger/` 的程式改了（排程時段、盯價、資料更新提醒），推上 `main` 之後。**推上 main 不會自動部署排程器**，一定要按這個。

1. Run workflow，`dry_run` 勾著：跑排程器自己的測試＋試打包，不上傳。
2. 綠了再按一次，取消勾選 `dry_run`：同樣先測試、通過才部署；Summary 印出新的版本編號與排程時段。
3. 好的結果：綠勾、有版本編號。下一個排程時段（例如整點那一班）Actions 有新 run 出現。

注意：
- 正式部署需要 GitHub secret `CLOUDFLARE_API_TOKEN` 有 **Account › Workers Scripts › Edit** 權限。**交接時還沒驗證過有沒有**；第一次正式部署若紅叉、訊息是權限不足（403、Authentication error），到 Cloudflare → My Profile → API Tokens 把那把權杖加上這個權限（或建一把新的換掉 secret），見 [credentials.md](credentials.md)。
- 排程器的機密設定（`GITHUB_DISPATCH_TOKEN`、`FUGLE_API_KEY`、`VAPID_*`、`OWNER_EMAIL`）在 Cloudflare 上，部署不會動到它們。
- `market-ana-eval`（登入名單檢查 Worker）還沒有按鈕；它很少需要改。
- 排程器的開關（`MARKET_ANA_WATCHLIST_WATCH`、`MARKET_ANA_DATA_HEALTH`）寫在 `cron-trigger/wrangler.toml`，**和網站的開關分開**；改了也要按這個部署才生效。

**退回上一版排程器**：Cloudflare → Workers & Pages → `market-ana-cron-trigger` → **Deployments** → 上一個版本 → **Rollback**（選單名稱以畫面為準）。確認：版本號變回去，下一個排程時段 Actions 有新 run。之後要修好程式再按一次 `deploy-cron-trigger`。

## 3. `workflow-keepalive`：防止 GitHub 停用排程

**背景**：公開倉庫連續 60 天沒有活動，GitHub 會停用有排程的 workflow；停用後連排程器送來的觸發也會被拒，網站就停更。

**平常不用管**：它自動接在每天 08:00 的 `announcements` 與每一班 `market-news` 後面跑，把被停用的重新啟用（站主自己手動停用的不碰）。

確認它有在跑：Actions → `workflow-keepalive`，每天早上 08:00 之後應該有一筆綠勾。

手動跑：Run workflow，`dry_run` 勾著＝只列出每支排程目前的狀態；取消勾選＝真的去啟用。

**交接時還在定稿**：預定再加上每月一次的「心跳」提交、自己的每週排程，以及「必要的 workflow 沒在啟用狀態就紅叉」。定稿後以 `.github/workflows/workflow-keepalive.yml` 檔頭為準。
