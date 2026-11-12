-- G4-2R：每日 AI 解讀。內容只保存通過數字／代號驗證的 structured_output。
CREATE TABLE ai_daily (
  data_date     TEXT NOT NULL CHECK (data_date GLOB '????-??-??'),
  digest_type   TEXT NOT NULL CHECK (digest_type IN ('market', 'flow', 'stock')),
  subject       TEXT NOT NULL,
  content_json  TEXT,
  input_sha256  TEXT NOT NULL CHECK (length(input_sha256) = 64),
  model         TEXT NOT NULL,
  input_tokens  INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  total_tokens  INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
  status        TEXT NOT NULL CHECK (status IN ('generated', 'partial', 'unproduced')),
  error_code    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (data_date, digest_type, subject),
  CHECK ((status = 'generated' AND content_json IS NOT NULL) OR status != 'generated')
);
CREATE INDEX ai_daily_latest ON ai_daily(data_date DESC, digest_type, subject);
