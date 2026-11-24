# 金鑰與登入（只寫名稱，不寫值）

## 三個存放地方

| 存放地方 | 在哪個網頁改 | 用在哪 |
|---|---|---|
| **GitHub secret**（倉庫的密碼設定） | `https://github.com/liuaaron0226/Market--Ana/settings/secrets/actions` | 所有 GitHub workflow |
| **GitHub variable**（倉庫的開關設定） | 同一頁上方切到 **Variables** 分頁 | 排程開關，見 [flags-and-schedules.md](flags-and-schedules.md) |
| **Cloudflare Pages secret**（網站的密碼設定） | Cloudflare 後台 → Workers & Pages → `market-ana` → Settings → Variables and Secrets（**正式 Production 與預覽 Preview 各一份**） | 網站後端 |
| **Cloudflare Worker secret** | Cloudflare 後台 → Workers & Pages → `market-ana-cron-trigger`（或 `market-ana-eval`）→ Settings → Variables and Secrets | 觸發器、盯價、登入檢查 |

（Cloudflare 後台選單的確切名稱可能隨改版不同：**待確認**，以畫面為準。）

### 在 GitHub 網頁換一個 secret 的值

1. 打開上面的 GitHub secret 網址。
2. 找到那個名稱，按鉛筆（Update）。
3. 貼上新值 → **Update secret**。舊值會被覆蓋，看不到也不用看。
4. 回 Actions 手動跑一次會用到它的 workflow（乾跑）確認。

### 在 Cloudflare 網頁換一個 secret 的值

1. 打開上表的 Cloudflare 位置，找到那個名稱 → 編輯 → 貼上新值 → 儲存（類型選 Secret／Encrypt）。
2. **Pages 的 secret 要等下一次部署才生效**：跑一次 redeploy（[rerun-and-redeploy.md](rerun-and-redeploy.md) B）。Worker 的 secret 存了就生效。

## 全部金鑰一覽

「到期日」欄目前全部**未盤點**（交接時沒查到）：站主要到「去哪裡看」那一頁查一次，寫進 [owner-todo.md](owner-todo.md)。

| 名稱 | 做什麼用 | 放在哪 | 去哪裡看／重發 | 失效時會怎樣 |
|---|---|---|---|---|
| `AGY_LOGIN_BUNDLE` | Gemini（agy）登入檔，排程用它呼叫 Gemini | GitHub secret | 見下方〈重做 Gemini 登入檔〉；撤銷舊授權：`myaccount.google.com/connections` | **run 仍是綠的**，但白話分析、盤勢解讀退成「依當天數字自動整理」；盯項、消息解讀停更；預測與自選股判斷記成「漏記」 |
| `CLOUDFLARE_API_TOKEN` | 讓 GitHub 讀寫 D1、部署網站 | GitHub secret | Cloudflare → 右上頭像 → My Profile → API Tokens（權限至少要 D1 編輯、Cloudflare Pages 編輯；`deploy-cron-trigger` 正式部署另需 Account › Workers Scripts › Edit，**有沒有還沒驗證**） | `daily-update` 在「驗證 Cloudflare token」就紅叉，整天不發布；`d1-migrate`、`deploy-cron-trigger` 也不能用 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 帳號編號（不是密碼，不會過期） | GitHub secret | Cloudflare 後台首頁右側 | 同上 |
| `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` | 讀寫 R2 工作集與原件 | GitHub secret | Cloudflare → R2 → Manage R2 API Tokens | `daily-update` 在「檢查金鑰」或「從 R2 還原工作集」紅叉 |
| `OWNER_EMAIL` | 站主是誰（網站認站主、排程挑站主的自選股） | GitHub secret；Pages secret（正式＋預覽）；Worker `market-ana-cron-trigger` 與 `market-ana-eval` 的 secret | 不會過期 | 網站沒有它整站回 503 |
| `FUGLE_API_KEY` | 富果即時報價（盤中報價、五檔、分時、盯價） | Pages secret；Worker `market-ana-cron-trigger`；GitHub secret | 富果開發者網站的 API 金鑰頁（網址**待確認**） | 盤中報價、分時、到價推播停；預測少一層資料 |
| `EODHD_API_KEY` | 國外指數與國外同類股日線（每天 20 次） | GitHub secret；Pages secret | eodhd.com 會員後台 | 國外指數、同類股標「過期」；可用 `eodhd-probe` 檢查 |
| `MASSIVE_API_KEY` | 美股日線（國外同類股；資金版圖美股每日成交金額） | GitHub secret；Pages secret | massive.com 會員後台 | 同類股美股那幾檔標「沒有金鑰」或「過期」；資金版圖美股最新月份不動 |
| `VAPID_PUBLIC_KEY`、`VAPID_PRIVATE_KEY` | 手機推播的簽章金鑰 | GitHub secret；Pages secret；Worker `market-ana-cron-trigger` | 不會過期 | 推播送不出去。**三處必須同一組**；換新的要用電腦跑 `node cron-trigger/set-push-keys.mjs`（需要 `gh` 與 `wrangler` 登入），換完每台手機要在自選股頁重新打開推播一次。**待簡化** |
| `FRIEND_AI_MASTER_KEY` | 把朋友存的 Gemini 金鑰加密存進資料庫的主金鑰（32 bytes、base64） | GitHub secret；Pages secret | 不會過期 | **不要換**：換了以後所有朋友存過的金鑰都解不開，要每位朋友重新輸入 |
| `GITHUB_DISPATCH_TOKEN` | Worker 準時去「按」GitHub workflow 用的 GitHub 權杖（只需這個倉庫的 Actions 寫入權限） | Worker `market-ana-cron-trigger` secret | GitHub → 右上頭像 → Settings → Developer settings → Personal access tokens（Fine-grained）→ 看到期日、Regenerate | **所有準時觸發都停**：每日更新只剩 GitHub 自己的排程（會晚好幾小時）；晨間預測、消息、公告停；Worker 只記 log，**不會通知任何人** |
| `GITHUB_TOKEN` | GitHub 自動給每個 run 的權杖 | 自動 | 不用管 | — |
| `PUSH_CONTACT` | 推播簽章裡的聯絡網址（選填，沒設用網站網址） | Pages／Worker（選填） | — | — |

### 換 `CLOUDFLARE_API_TOKEN`

1. Cloudflare → 右上頭像 → My Profile → API Tokens → **Create Token**（自訂）。權限（Account 層級，只選這個帳號）：
   - D1：Edit（每日更新、`d1-migrate`）
   - Cloudflare Pages：Edit（部署網站）
   - Workers Scripts：Edit（`deploy-cron-trigger`）
   - 每日更新還會跑 `wrangler d1 list`、`pages project list` 做唯讀檢查，上面三項已包含讀取。
   - 是否還需要其他權限：**待確認**（以 `daily-update` 乾跑能不能通過「驗證 Cloudflare token」為準）。
2. 複製新值 → GitHub secret `CLOUDFLARE_API_TOKEN` → Update。
3. 驗證：`daily-update` 以 `redeploy` 勾、`dry_run` 勾跑一次，「驗證 Cloudflare token（唯讀）」那一步綠；`d1-migrate` 乾跑綠。要驗**寫入**：等當天正式的每日更新成功（或 `redeploy` 正式跑一次）。
4. 新的確認能用之後，回 API Tokens 把**舊的那把刪掉**（Roll／Delete）。不要先刪舊的。

### 換 R2 金鑰（`R2_ACCESS_KEY_ID`＋`R2_SECRET_ACCESS_KEY`，**一定成對換**）

1. Cloudflare → R2 → Manage R2 API Tokens → Create API Token：權限 **Object Read & Write**，範圍限 bucket `market-ana-state`。
2. 建立後畫面會同時給 Access Key ID 與 Secret Access Key（**只顯示一次**）。兩個都抄下來。
3. GitHub secret `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` **兩個都換**（只換一個一定失敗）。
4. 驗證讀取：`daily-update` 乾跑，「從 R2 還原工作集」那一步綠。驗證寫入：`announcements` 手動跑一次（會把原件存回 R2），或等下一次正式每日更新成功（「同步工作集回 R2」那一步綠）。
5. 都綠了再回 R2 API Tokens 刪掉舊的。

### 換 `GITHUB_DISPATCH_TOKEN`（手機可做）

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens。
2. 找到給觸發器用的那一把（名稱由站主當初取，**待確認**），按 Regenerate，或新建一把：Repository access 只選 `Market--Ana`，Permissions → Actions：Read and write。
3. 複製新值。
4. Cloudflare → Workers & Pages → `market-ana-cron-trigger` → Settings → Variables and Secrets → `GITHUB_DISPATCH_TOKEN` → 編輯貼上 → 儲存。
5. 確認：下一個整點時段（例如 18:00）Actions 出現新的 `daily-update` run，事件寫 `workflow_dispatch`。

## 重做 Gemini 登入檔（`AGY_LOGIN_BUNDLE`）

**什麼時候要做**：連續兩天網站出現「依當天數字自動整理」或「漏記」，或手動跑 `agy-auth-probe` 紅叉。

**原理**：排程在全新的 GitHub 雲端電腦上跑官方 `agy`，需要一份「已經登入過 Google AI Pro 的 `.gemini` 資料夾」。我們在 Linux 上登入一次，把那個資料夾打包成一行文字（base64），貼進 GitHub secret。登入檔會自己續期，**沒有固定到期日**；Google 撤銷授權、改密碼、或很久沒用才會失效。

> ⚠️ 這三支腳本原本放在這台電腦的 `.tmp/g4-probe/`，**那個資料夾不在 git 裡**。下方〈附錄〉已經把內容完整抄進來，換電腦也能照做。

### 做法 1：在這台電腦的 WSL（已實測可行，2026-11-10）

需要：Windows 的 WSL（Ubuntu）；一個瀏覽器；站主的 Google AI Pro 帳號。

1. 打開「Ubuntu」（WSL）視窗。
2. 建一個資料夾、把附錄的三支腳本存進去：`mkdir -p ~/agy-login && cd ~/agy-login`，再用 `nano wsl-install.sh` 貼上附錄 A 的內容存檔（`Ctrl+O`、Enter、`Ctrl+X`），B、C 同理。
3. 執行 `bash wsl-install.sh`。好的結果：最後印出 `agy.tgz: OK` 和一個 `antigravity` 的路徑。印出 `FAILED` → 下載的檔案不對，停下來交給 AI。
4. 執行 `bash wsl-login.sh`。它會印出一個 Google 網址：
   1. 複製網址，用瀏覽器打開，登入**站主的 Google AI Pro 帳號**，同意授權。
   2. 頁面會給一段授權碼，複製後貼回 Ubuntu 視窗、按 Enter。
   3. 好的結果：印出「登入完成」和幾個檔案路徑（其中有 `antigravity-oauth-token`）。
5. 執行 `bash wsl-export.sh`。好的結果：印出「已寫入 /mnt/c/Users/User/Desktop/AGY_LOGIN_BUNDLE.txt：N 字元」。（Windows 使用者名稱不是 `User` 的電腦，要先把腳本裡那個路徑改掉。）
6. 打開桌面的 `AGY_LOGIN_BUNDLE.txt`，全選複製（是一整行很長的文字）。
7. GitHub → 倉庫 Settings → Secrets and variables → Actions → `AGY_LOGIN_BUNDLE` → Update → 貼上 → Update secret。
8. **刪掉桌面的 `AGY_LOGIN_BUNDLE.txt`**，資源回收筒也清掉（它等於你的 Google 登入）。
9. 確認：Actions → `agy-auth-probe` → Run workflow（`model` 留預設）→ 綠勾，且「跑一次最小的 agy -p」那一步印 `status: SUCCESS`。

### 做法 2：不用自己的電腦（**未實測，待確認**）

Google Cloud Shell（`shell.cloud.google.com`，瀏覽器裡的一台 Linux）理論上可以照做法 1 的第 2～4 步做；第 5 步改成把 `wsl-export.sh` 的輸出位置換成 Cloud Shell 自己的家目錄，再用 Cloud Shell 的「下載檔案」或直接 `cat` 出來複製。要先有人實測一次才能寫成正式步驟。

### 登入檔外洩時

1. `myaccount.google.com/connections` → 找到 Antigravity 的授權 → 移除存取權。
2. 照上面重做一份新的、更新 secret。

### agy 本身改版時（交給 AI 做）

排程固定用官方 `agy` **1.5.3**（Linux 版），並核對 SHA-256；模型名稱寫死在 `gemini_models.py`（`gemini-4-argon`、`gemini-3.8-flash`）。如果 Google 下架這個版本或改模型名稱，`agy-auth-probe` 會紅、網站會退成套句。要改的地方：所有 workflow 裡下載 agy 的那一步（網址與 SHA-256）、`gemini_models.py`、`agy-auth-probe.yml` 的預設 `model`。改完先跑 `agy-auth-probe` 確認。

## 附錄：登入檔腳本原文（從 `.tmp/g4-probe/` 抄錄，不含任何憑證）

### A. `wsl-install.sh`

```bash
#!/bin/bash
# 在 WSL 隔離目錄安裝官方 agy 1.5.3（不碰既有 ~/.gemini）
set -e
d="$HOME/g4-agy"
mkdir -p "$d"
cd "$d"
if [ ! -f agy.tgz ]; then
  curl -fsSL -o agy.tgz https://github.com/google-antigravity/antigravity-cli/releases/download/1.5.3/agy_cli_linux_x64.tar.gz
fi
echo "1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807  agy.tgz" | sha256sum -c
tar xzf agy.tgz
find "$d" -name antigravity -type f
```

### B. `wsl-login.sh`

```bash
#!/bin/bash
# 用隔離的 HOME 讓官方 agy 走無瀏覽器登入，不碰 WSL 既有 ~/.gemini。
# 不印出任何憑證內容；登入後只列出新產生檔案的路徑與大小。
set -e
iso="$HOME/g4-agy/home"
mkdir -p "$iso"
export HOME="$iso"
unset DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY
cd "$iso"
echo "=== 下一步會印出 Google 授權網址：用瀏覽器開啟、登入 AI Pro 帳號，把頁面上的授權碼貼回這裡 ==="
"$HOME/../antigravity" acp --login
echo "=== 登入完成，新產生的檔案（只列路徑與大小，不印內容）==="
find "$iso" -type f -newer "$HOME/../agy.tgz" -printf '%s\t%m\t%p\n' 2>/dev/null | sort -k3
```

### C. `wsl-export.sh`

```bash
#!/bin/bash
# 把隔離 HOME 的 agy 登入檔打包成一行 base64，寫進 Windows 桌面的暫存檔，給本人用網頁貼進 GitHub secret。
# 不印出任何內容；只印出檔案大小與 SHA 前 12 碼供核對。
set -e
iso="$HOME/g4-agy/home"
out="/mnt/c/Users/User/Desktop/AGY_LOGIN_BUNDLE.txt"
[ -d "$iso/.gemini" ] || { echo "找不到 $iso/.gemini，請先跑登入"; exit 1; }
tar czf - -C "$iso" .gemini | base64 -w0 > "$out"
printf '已寫入 %s：%s 字元，SHA %s\n' "$out" "$(wc -c < "$out")" "$(sha256sum "$out" | cut -c1-12)"
```

排程端怎麼用這份 secret（給 AI 對照）：`printf '%s' "$AGY_LOGIN_BUNDLE" | tr -d ' \r\n' | base64 -d | tar xzf - -C "$RUNNER_TEMP/agy-home"`，再用 `HOME=$RUNNER_TEMP/agy-home` 執行 agy。所以打包內容必須是以 `.gemini/` 開頭的 tar.gz。
