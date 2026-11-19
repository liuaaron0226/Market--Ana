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
# 那時的「思考高」沒有接到判斷呼叫上，agy 實際一直收到 medium。本人 11-19 晚（設定頁 AI 模型上線前）題目
# 「這次上線要怎麼處理」選「先維持中（推薦）」：預設照實際在跑的中，上線不換組；要高由本人在設定頁自己改。
WATCH_JUDGE_EFFORT = "medium"
# 朋友解讀：用朋友自己的 Gemini API 金鑰（不是 agy；朋友的金鑰用不到 Argon），Flash、思考高（API 的 thinkingLevel）。
FRIEND_DIGEST = FLASH
FRIEND_DIGEST_EFFORT = "high"
# 首頁「籌碼與資金」白話（daily-update.yml 的 PLAIN_MODEL／PLAIN_EFFORT）與站主 AI 解讀（ai-digest.yml 的 --model）
# 在排程檔裡寫名稱；這裡記本人選的值，test_gemini_models 核對兩邊一致。
PLAIN = ARGON
PLAIN_EFFORT = "medium"
DIGEST = ARGON
DIGEST_EFFORT = "medium"

# 本人 2026-11-19 晚：上面五組（預測、判斷、提名、白話、站主解讀）改成站主在設定頁「AI 模型」自己選，排程開跑時讀
# （gemini_settings.py）；這裡的值是「還沒選過」或「讀不到設定」時用的預設。朋友解讀不在設定頁（朋友自己的金鑰）。
MODELS = (ARGON, FLASH)
EFFORTS = ("low", "medium", "high")
