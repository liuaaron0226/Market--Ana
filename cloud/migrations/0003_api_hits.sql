-- 票 06 市場 API 限速：每人、每種額度、每分鐘一列。用單一語句條件遞增（超限時不寫入），避免並行穿透。
CREATE TABLE api_hits (
  email   TEXT NOT NULL,
  bucket  TEXT NOT NULL,
  minute  INTEGER NOT NULL,
  count   INTEGER NOT NULL,
  PRIMARY KEY (email, bucket, minute)
);
