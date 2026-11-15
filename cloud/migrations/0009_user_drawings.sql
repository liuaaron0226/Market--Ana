-- 畫線工具（本人 2026-11-14「共識達成」）：每位使用者在每一檔個股 K 線上畫的線，存雲端、跟帳號。
-- 錨點存「交易日日期＋價格」（圖上顯示的還原價），不存像素或 K 棒序號；日、週、月 K 共用同一份。
-- kind 先列出第一批六種，之後各票只需放寬 API 的白名單，不必重建資料表。
CREATE TABLE user_drawings (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 6),
  id          TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 40),
  kind        TEXT NOT NULL CHECK (kind IN ('trend', 'hline', 'ray', 'rect', 'fib', 'text')),
  date1       TEXT NOT NULL CHECK (length(date1) = 10),
  price1      REAL NOT NULL CHECK (price1 > 0 AND price1 <= 10000000),
  date2       TEXT CHECK (date2 IS NULL OR length(date2) = 10),
  price2      REAL CHECK (price2 IS NULL OR (price2 > 0 AND price2 <= 10000000)),
  text        TEXT NOT NULL DEFAULT '' CHECK (length(text) <= 200),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (email, code, id),
  CHECK ((date2 IS NULL) = (price2 IS NULL))
);
-- 每個帳號每一檔最多 200 條；覆寫既有的那一條不算新增。
CREATE TRIGGER user_drawings_limit BEFORE INSERT ON user_drawings
WHEN NOT EXISTS (SELECT 1 FROM user_drawings WHERE email = NEW.email AND code = NEW.code AND id = NEW.id)
  AND (SELECT COUNT(*) FROM user_drawings WHERE email = NEW.email AND code = NEW.code) >= 200
BEGIN
  SELECT RAISE(ABORT, 'drawings limit reached');
END;
