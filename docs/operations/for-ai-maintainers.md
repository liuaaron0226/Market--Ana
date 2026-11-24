# 給接手維護的 AI（Gemini／Antigravity `agy`、Codex 等）

你在維護一個台股分析網站 Market--Ana。站主不是工程師，用手機看網站、用中文跟你溝通。原本的維護者已離開，**這個資料夾（`docs/operations/`）就是全部的交接**；不要假設有任何其他對話紀錄、記憶或本機檔案可以參考。

先讀：[README.md](README.md)（什麼在哪裡跑）→ 本檔 → [handover.md](handover.md)（進行中的工作）。

## 跟站主溝通

- 一律用**繁體中文**、白話、先講結論、幾個短點。術語第一次出現要用一句話解釋。
- 每個需要站主決定的事：一次問一題，附上你推薦的選項與理由。
- 能自己查的事實（程式、檔案、GitHub 上的 run）自己查，不要問站主。
- 「完成」的定義：**站主在正式站（`market-ana.pages.dev`）用自己的帳號看得到**才算完成。測試全綠、推上 main 都只是「程式完成、未上線」；回報時講清楚看的是預覽還是正式站。

## 倉庫地圖

| 路徑 | 是什麼 |
|---|---|
| 根目錄 `*.py` | Python 資料管線（抓官方資料、算分析、呼叫 Gemini、寫 D1）。進入點：`daily_update.py`（每日更新協調器，`--redeploy`、`--republish`）、`state_sync.py`（R2 工作集 `restore`／`upload`）、`fund_compare.py`（基金）、`market_news.py`、`forecast_*.py`、`watch_*.py`、`broker_branches.py`、`market_brief.py`、`gemini_models.py`（每個 AI 任務用哪個模型） |
| 根目錄 `test_*.py` | Python 測試 |
| `.github/workflows/*.yml` | 13 支 GitHub Actions；**每支檔頭的註解是最好的說明** |
| `cron-trigger/` | Cloudflare Worker `market-ana-cron-trigger`：準時觸發 workflow、盤中每分鐘盯價（`worker.js`、`wrangler.toml`） |
| `cloud/` | Cloudflare Pages 網站後端：`functions/`、`lib/`（`gate.js` 入口與 API、`release-policy.js` 功能開關）、`migrations/`（D1 結構，0001～0038）、`build.mjs`（建置）、`wrangler.jsonc`（Pages 設定與功能開關）、`eval-worker/`（登入名單檢查 Worker）、`test/`（JS 測試與瀏覽器旅程） |
| `web/` | 前端（純 HTML／CSS／JS，沒有框架） |
| `seeds/` | 進版控的官方原件（例如 `seeds/funds/<代碼>/` 基金回補，附 SHA-256 清單） |
| `docs/operations/` | 本手冊 |
| `data/`、`.tmp/`、`.scratch/` | 本機工作資料，**不在 git 裡**；雲端靠 R2 工作集，不靠它們 |

雲端資源：Pages 專案 `market-ana`；D1 `market-ana-accounts`；R2 bucket `market-ana-state`；Workers `market-ana-cron-trigger`、`market-ana-eval`。

## 跑測試

需要：Python 3.11、Node 22、`pip install -r requirements.txt`（排程只用 `requirements-daily.txt`）。

**Python（在倉庫根目錄）**

```
python -X utf8 -m unittest discover -p "test_*.py" -s . -t .
```

已知基線：**10 個失敗＋1 個錯誤**，和你的改動無關，不要去追：
- `test_listed_catalog` 8 個（個股入口回 500，`analyze_v2() missing 1 required positional argument: 'db_path'`）
- `test_listed_institutional.RealDataTest` 1 個（讀本機 `data/` 的上櫃法人資料，日期範圍對不上）
- `test_long_term_screen` 1 個（基準日是空的，依賴本機資料）
- 1 個錯誤：名稱**待確認**（交接時正在重跑確認）

判斷方法：你的改動前後，失敗清單逐條相同就算沒弄壞。沒有本機 `data/` 的乾淨檢出，讀真實資料的測試會多失敗，屬環境問題。

**JavaScript（在 `cloud/`）**

```
cd cloud
node build.mjs
node --test --no-warnings "test/**/*.test.js" "test/**/*.test.cjs"
```

`build.mjs` 需要 `cloud/.stock-pack.json` 與大盤資料包；資料包位置用環境變數 `MARKET_DATA_DIR` 指定（每日流程指向 `data/daily-update-state/last-market-pack`，要先用 `python state_sync.py restore --target .` 從 R2 取回，需 R2 金鑰；沒設時讀 `web/`）。乾淨檢出能不能直接建置：**待確認**。交接時基線是全部通過（約 1,282 個）。

Worker 測試：`node --test cron-trigger/*.test.js`。

**瀏覽器旅程（真瀏覽器端到端）**：`cloud/test/browser_*_journey.py`，用 Playwright（Python）。每支單獨跑：`python -X utf8 cloud/test/browser_plain_journey.py`。需要本機起測試伺服器（`cloud/test/journey-server.mjs`）與 Chromium；外部字型（Google Fonts）連不到時會截圖逾時，屬環境問題。`browser_time_share_journey.py` 偶有一項不穩定（重跑即過）。

## 不能破壞的規矩

1. **倉庫是公開的**。log、step summary、artifact、提交內容裡不能出現：信箱、站主或朋友的自選股／持股代號、模型原文回答、金鑰。印到 log 的報告一律用 `public_log.py` 的遮蔽版；輸入的代號先 `::add-mask::`。看 `broker-branches.yml` 的寫法。
2. **官方原件＋SHA-256**：從外部抓的資料存原件（原始位元組），記下網址、讀取時間、SHA-256；之後讀的時候核對，對不上就停。例：`seeds/funds/*/backfill-manifest.json`。不呼叫來源的內部 API、不解驗證碼、不繞過限流。
3. **開關預設關、先開站主**：新功能的開關預設關，先 `owner`，站主看過才 `all`。三態只認 `owner`／`all`，排程開關只認 `on`。見 [flags-and-schedules.md](flags-and-schedules.md)。
4. **任何會改正式資料的動作先乾跑**；D1 的寫入不能用明寫的 `BEGIN;…COMMIT;`（D1 會拒絕）。
5. **網站裡的 AI 只用 Gemini**：排程用官方 `agy -p`（站主的 Google AI Pro 額度；這是 Antigravity 官方 FAQ 允許的「direct scripting and CI workflows」用法），朋友那條線用朋友自己的 Gemini API 金鑰。**不要**把 `agy` 包進網站後端或讓網站使用者的請求觸發它（違反條款，帳號會被停權）。
6. **不准留下「要在某台電腦上手動跑」的步驟**。所有例行工作都要在 GitHub Actions 或 Cloudflare 上自己跑；需要人的，只能是站主用網頁按按鈕。
7. **推上 `main` 等於隔天上線**（每日發布會部署 `main`）。介面還在改就留在別的分支；推 `main`、部署、套正式資料庫遷移、改開關、開給其他人，**每一步先問站主**。
8. **D1 免費方案一天 10 萬列寫入**（刪除也算）；大量回補要分批、可中斷續跑。

## 站主的介面規矩（看得到的變更）

- **看得到的變更要先給站主看樣品**：截圖＋可以點的網址（手機也要能開），站主同意才上。推薦預設放最精簡的那個。
- **不要放廢話小字**：不重複控制鍵上已有的字、不顯示內部狀態（「形成中」「資料截至」這類只在過期時才出現）。
- **用字要專業**：用券商研究報告的說法，不用口語（例如不叫「直白分析」）。
- **淺色看盤桌風格**：米白圓角卡片＋柔和陰影；不要深色液態玻璃光條。陰影只用網站現有的；紅綠只用在細記號上。
- 圖表先參考專業看盤軟體的做法；分析內容先分類、一次看一類、圖表優先。
- 清單操作用手勢（左滑刪除、點名稱進詳細頁），滑鼠鍵盤另留入口；手勢要真機驗。
- 站主舉一個例子要你改時，**全站找出同類的地方一起改**，附一張盤點表回報。
- 修問題要徹底：旁邊同類的缺口一起補，跑所有相關測試。

## 常見工作怎麼做

| 工作 | 怎麼做 |
|---|---|
| 改網頁或 `cloud/` 程式 | 改 → 跑 JS 測試＋相關旅程 → 給站主看樣品 → 同意後推 `main` → redeploy（[rerun-and-redeploy.md](rerun-and-redeploy.md) B）→ 正式站核對 |
| 改 Python 管線 | 改 → 跑 Python 全套（對照基線）→ 推 `main` → 該 workflow 手動乾跑一次 |
| 新增資料表 | 新增 `cloud/migrations/00NN_*.sql`；套到正式 D1 的方法與帳本問題見 [handover.md](handover.md) (c)，**先問站主** |
| 換模型 | 只改 `gemini_models.py`（站主也能在網站設定頁「AI 模型」自己選）；換大盤預測的模型會換預測帳本的輸入版本 |
| 排程失敗 | 先看 run 的紅叉步驟訊息；金鑰類看 [credentials.md](credentials.md) |
