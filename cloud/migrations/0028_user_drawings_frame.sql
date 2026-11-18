-- 60 分 K 也能畫線（本人 2026-11-18：各畫各的，60 分畫的線只在 60 分出現，錨點記到那一根 60 分 K 開始的時分；
-- 日／週／月畫的照舊只在日／週／月）。多一欄 frame：day（日／週／月共用，原本的資料一律是它）／hour（60 分）。
-- 錨點格式跟著 frame：day 是「YYYY-MM-DD」（10 字），hour 是「YYYY-MM-DDTHH:MM」（16 字，台北時間）。
-- SQLite 改不了 CHECK，照 0025 的做法重建：欄位、資料、主鍵與每一檔 200 條的觸發器原樣搬（這張表沒有其他索引）。
CREATE TABLE user_drawings_v3 (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
  id          TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 40),
  kind        TEXT NOT NULL CHECK (kind IN ('trend', 'hline', 'ray', 'rect', 'fib', 'text')),
  frame       TEXT NOT NULL DEFAULT 'day' CHECK (frame IN ('day', 'hour')),
  date1       TEXT NOT NULL,
  price1      REAL NOT NULL CHECK (price1 > 0 AND price1 <= 10000000),
  date2       TEXT,
  price2      REAL CHECK (price2 IS NULL OR (price2 > 0 AND price2 <= 10000000)),
  text        TEXT NOT NULL DEFAULT '' CHECK (length(text) <= 200),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (email, code, id),
  CHECK ((date2 IS NULL) = (price2 IS NULL)),
  CHECK (length(date1) = CASE frame WHEN 'hour' THEN 16 ELSE 10 END),
  CHECK (date2 IS NULL OR length(date2) = CASE frame WHEN 'hour' THEN 16 ELSE 10 END)
);
INSERT INTO user_drawings_v3 (email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at)
  SELECT email, code, id, kind, 'day', date1, price1, date2, price2, text, created_at, updated_at FROM user_drawings;
DROP TABLE user_drawings;
ALTER TABLE user_drawings_v3 RENAME TO user_drawings;
CREATE TRIGGER user_drawings_limit BEFORE INSERT ON user_drawings
WHEN NOT EXISTS (SELECT 1 FROM user_drawings WHERE email = NEW.email AND code = NEW.code AND id = NEW.id)
  AND (SELECT COUNT(*) FROM user_drawings WHERE email = NEW.email AND code = NEW.code) >= 200
BEGIN
  SELECT RAISE(ABORT, 'drawings limit reached');
END;
