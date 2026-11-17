"""每個任務用哪個 Gemini 模型：只在這裡改，排程檔不另外寫模型名稱。

本人 2026-11-17 定案（ANALYSIS_PLAN.md〈Gemini 依任務分模型〉）：AI Pro 方案只有 Flash 與 Argon，
比較複雜的分析交給 Argon；自選股每日判斷（每次最多 40 次）與朋友解讀維持 Flash。

名稱是本人帳號 ``agy models`` 列出的基本名稱（2026-11-17 核實：gemini-4-argon-{low,medium,high}、
gemini-3.8-flash-{low,medium,high}）；呼叫時另帶 ``--effort medium``。亂填名稱 agy 會直接拒絕，不會悄悄換模型。
知識截止日與出處記在 ``forecast_replay.MODEL_KNOWLEDGE``。

Argon 和 Flash 在 Antigravity 是同一個「Gemini 模型」額度群組，共用 5 小時與每週上限
（https://antigravity.google/docs/cli/commands/usage）；換模型會多吃同一池額度，排程照舊「失敗就停」。

換大盤預測的模型會換預測帳本的輸入版本（``forecast_ledger.PARAMETERS["model"]``），新舊系列分開對帳。
"""

ARGON = "gemini-4-argon"
FLASH = "gemini-3.8-flash"

# 大盤事前預測：晚間版、晨間版、歷史重播（重播必須和正式同一個模型，成績才能當參考）。
FORECAST = ARGON
# 自選股關注：替關注股提名國外同類股（每檔只問一次）。
WATCH_NOMINATE = ARGON
# 首頁「籌碼與資金」白話分析（每天收盤資料發布後重寫）。
PLAIN_DAILY = ARGON
# 自選股關注每日判斷：每次排程最多 40 次呼叫，量大，維持 Flash。
WATCH_JUDGE = FLASH
# 朋友解讀：用朋友自己的 Gemini API 金鑰（不是 agy），維持 Flash。
FRIEND_DIGEST = FLASH
