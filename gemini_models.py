"""每個任務用哪個 Gemini 模型：只在這裡改，排程檔不另外寫模型名稱。

本人 2026-11-17 定案（ANALYSIS_PLAN.md〈Gemini 依任務分模型〉）：AI Pro 方案只有 Flash 與 Argon，
事前預測（晚間、晨間、歷史重播）交給 Argon；同日更正「籌碼與資金白話分析、自選股提名同類股可以用 flash，
其他不變」，所以自選股提名、每日判斷與朋友解讀都是 Flash。思考程度一律 medium（本人沒有要調高），
``ledger_core`` 呼叫時帶 ``--effort medium``。白話分析（``ai_digest``）由另一個對話負責，不在這裡設定。

名稱是本人帳號 ``agy models`` 列出的基本名稱（2026-11-17 核實：gemini-4-argon-{low,medium,high}、
gemini-3.8-flash-{low,medium,high}）；呼叫時另帶 ``--effort medium``。亂填名稱 agy 會直接拒絕，不會悄悄換模型。
知識截止日與出處記在 ``forecast_replay.MODEL_KNOWLEDGE``。

Argon 和 Flash 在 Antigravity 是同一個「Gemini 模型」額度群組，共用 5 小時與每週上限
（https://antigravity.google/docs/cli/commands/usage）；本人帳號 ``agy -p /usage``（不呼叫模型）另外列出
「Gemini 4 Argon Weekly Limit」，Argon 還有自己的每週上限。換模型會多吃額度，排程照舊「失敗就停」。

換大盤預測的模型會換預測帳本的輸入版本（``forecast_ledger.input_version(model)`` 把模型算進雜湊），新舊系列分開對帳。
"""

ARGON = "gemini-4-argon"
FLASH = "gemini-3.8-flash"

# 本人 2026-11-18 晚（ANALYSIS_PLAN〈各 AI 任務的模型與思考程度〉）：「用量一個禮拜會重置，所以都使用 ARGON 好像可以」，
# 逐項選：每日判斷、白話分析、站主解讀改 Argon，三項思考「中」；提名同類股、朋友解讀留 Flash，思考「高」。

# 大盤事前預測：晚間版、晨間版、歷史重播（重播必須和正式同一個模型，成績才能當參考）。
FORECAST = ARGON
FORECAST_EFFORT = "medium"
# 自選股關注：替關注股提名國外同類股（每檔只問一次）。Flash、思考高。
WATCH_NOMINATE = FLASH
WATCH_NOMINATE_EFFORT = "high"
# 自選股關注每日判斷：每次排程最多 40 次呼叫。11-18 晚改 Argon；11-19 下午 Argon 剩 33%（實測一次呼叫約每週額度 1%，
# 判斷每天約 12 次是最大宗），本人選「判斷改 Flash（推薦）」、思考高，Argon 留給大盤預測與白話（換模型＝關注帳本新輸入版本）。
WATCH_JUDGE = FLASH
# 那時的「思考高」沒有接到判斷呼叫上，agy 實際一直收到 medium（設定頁 AI 模型那一版才接上）。本人 11-19 晚先選
# 「先維持中」，隨後對 owner 說「Flash還是改用高好了」：用 Flash 的任務一律高，判斷也是高（上線即換一組成績，見交接）。
WATCH_JUDGE_EFFORT = "high"
# 朋友解讀：用朋友自己的 Gemini API 金鑰（不是 agy；朋友的金鑰用不到 Argon），Flash、思考高（API 的 thinkingLevel）。
FRIEND_DIGEST = FLASH
FRIEND_DIGEST_EFFORT = "high"
# 主要模型回 503（太忙）或逾時時退到官方的 latest 別名（2026-11-19：同一把朋友金鑰打 gemini-3.8-flash 連續 503）。
# 和存金鑰時的試呼叫（cloud/lib/friend-ai.js 的 TRIAL_URL）、首頁消息的朋友呼叫（market_news.FRIEND_FALLBACK_MODEL）
# 同一個名稱。別名指到哪一代由 Google 決定、不一定收 thinkingLevel，所以備援那次不帶思考程度。
FRIEND_DIGEST_FALLBACK = "gemini-flash-latest"
# 首頁「籌碼與資金」白話（daily-update.yml 的 PLAIN_MODEL／PLAIN_EFFORT）與站主 AI 解讀（ai-digest.yml 的 --model）
# 在排程檔裡寫名稱；這裡記本人選的值，test_gemini_models 核對兩邊一致。
PLAIN = ARGON
PLAIN_EFFORT = "medium"
DIGEST = ARGON
DIGEST_EFFORT = "medium"
# 首頁「消息」（本人 2026-11-19「Flash 整批判（推薦）」）：交易日三輪、每輪一次，把各市場新聞標成利多／利空、翻成中文、
# 寫「今天為什麼這樣走」；站主的「我的自選」併在同一次。設定頁 AI 模型多這一項（market-news.yml 讀）。
NEWS = FLASH
NEWS_EFFORT = "high"

# 本人 2026-11-19 晚：上面五組（預測、判斷、提名、白話、站主解讀）改成站主在設定頁「AI 模型」自己選，排程開跑時讀
# （gemini_settings.py）；這裡的值是「還沒選過」或「讀不到設定」時用的預設。朋友解讀不在設定頁（朋友自己的金鑰）。
MODELS = (ARGON, FLASH)
EFFORTS = ("low", "medium", "high")
