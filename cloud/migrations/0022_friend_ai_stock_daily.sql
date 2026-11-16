-- 開放給朋友 03 票（2026-11-17）：每位朋友用自己的 Gemini 金鑰、根據「他自己」的自選股與庫存產生的個股判斷，
-- 每人每天一列。每檔的結果放在 content_json.items（只含通過數字／代號驗證、而且是他自己持股的那幾檔）。
-- 另開一張表：站主的 ai_daily、朋友大盤與籌碼的 friend_ai_daily 都不動。
--   generated／partial：至少一檔產生，content_json 一定有內容
--   unproduced：當天沒有產生（金鑰、額度、驗證、資料沒齊），content_json 一定是 NULL
--   no_holdings：自選股和庫存都是空的，沒有呼叫個股判斷，不算失敗
CREATE TABLE friend_ai_stock_daily (
  email         TEXT NOT NULL,
  data_date     TEXT NOT NULL CHECK (data_date GLOB '????-??-??'),
  content_json  TEXT,
  input_sha256  TEXT NOT NULL CHECK (length(input_sha256) = 64),
  model         TEXT NOT NULL,
  input_tokens  INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  total_tokens  INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
  status        TEXT NOT NULL CHECK (status IN ('generated', 'partial', 'unproduced', 'no_holdings')),
  error_code    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (email, data_date),
  CHECK ((status IN ('generated', 'partial') AND content_json IS NOT NULL)
      OR (status IN ('unproduced', 'no_holdings') AND content_json IS NULL))
);
CREATE INDEX friend_ai_stock_daily_date ON friend_ai_stock_daily(data_date DESC);
