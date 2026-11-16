-- 開放給朋友 02 票（2026-11-16）：每位朋友用自己的 Gemini 金鑰產生的大盤、籌碼解讀，每人每天每類一列。
-- 另開一張表、不在站主的 ai_daily 加欄位：站主讀的查詢（MAX(data_date) FROM ai_daily）一個字都不用改，
-- 朋友的結果也不可能混進站主那份。內容只保存通過數字／代號驗證的輸出；未產生時 content_json 一定是 NULL。
CREATE TABLE friend_ai_daily (
  email         TEXT NOT NULL,
  data_date     TEXT NOT NULL CHECK (data_date GLOB '????-??-??'),
  digest_type   TEXT NOT NULL CHECK (digest_type IN ('market', 'flow')),
  content_json  TEXT,
  input_sha256  TEXT NOT NULL CHECK (length(input_sha256) = 64),
  model         TEXT NOT NULL,
  input_tokens  INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  total_tokens  INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
  status        TEXT NOT NULL CHECK (status IN ('generated', 'unproduced')),
  error_code    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (email, data_date, digest_type),
  CHECK ((status = 'generated' AND content_json IS NOT NULL) OR (status = 'unproduced' AND content_json IS NULL))
);
CREATE INDEX friend_ai_daily_date ON friend_ai_daily(data_date DESC);
