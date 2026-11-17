-- 基金當成一般個股 01 票（本人 2026-11-18「我希望你就把它當成一般個股」，規格 .scratch/fund-as-stock/spec.md）：
-- 使用者的自選、庫存、搜尋紀錄、畫線要能放基金（代碼例如 BLKTENAU，8 碼；市場「境外基金」）。
-- SQLite 不能改 CHECK，所以四張表照原樣重建：只把代號長度放寬到 4～12、市場多收「境外基金」，其餘欄位、預設值、上限觸發器、索引不變。
-- 欄位順序和舊表相同，資料原樣搬過去。代號規則和 cloud/lib/codes.js 的 CODE_PATTERN 是同一個，要調整得兩邊一起改。
-- 編號 0025 暫定，由 owner 分配；套用到正式資料庫前要先問本人。測試直接讀這個檔建表，結構只在這裡維護。

CREATE TABLE user_watchlist_v2 (
  email        TEXT NOT NULL,
  code         TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
  name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market       TEXT NOT NULL CHECK (market IN ('上市', '上櫃', '境外基金')),
  horizon      TEXT NOT NULL DEFAULT 'none' CHECK (horizon IN ('none', 'short', 'long')),
  in_universe  INTEGER NOT NULL CHECK (in_universe IN (0, 1)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  watching     INTEGER NOT NULL DEFAULT 0 CHECK (watching IN (0, 1)),
  watch_since  TEXT,
  group_name   TEXT NOT NULL DEFAULT '' CHECK (length(group_name) <= 12),
  PRIMARY KEY (email, code)
);
INSERT INTO user_watchlist_v2 (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since, group_name)
  SELECT email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since, group_name FROM user_watchlist;
DROP TABLE user_watchlist;
ALTER TABLE user_watchlist_v2 RENAME TO user_watchlist;
CREATE INDEX user_watchlist_recent ON user_watchlist(email, updated_at DESC, code);
CREATE INDEX user_watchlist_watching ON user_watchlist(email, watching, watch_since, code);
CREATE TRIGGER user_watchlist_limit BEFORE INSERT ON user_watchlist
WHEN NOT EXISTS (SELECT 1 FROM user_watchlist WHERE email = NEW.email AND code = NEW.code)
  AND (SELECT COUNT(*) FROM user_watchlist WHERE email = NEW.email) >= 100
BEGIN
  SELECT RAISE(ABORT, 'watchlist limit reached');
END;
CREATE TRIGGER user_watchlist_watch_limit BEFORE UPDATE OF watching ON user_watchlist
WHEN NEW.watching = 1 AND OLD.watching = 0
  AND (SELECT COUNT(*) FROM user_watchlist WHERE email = NEW.email AND watching = 1) >= 20
BEGIN
  SELECT RAISE(ABORT, 'watch limit reached');
END;

CREATE TABLE user_holdings_v2 (
  email          TEXT NOT NULL,
  code           TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
  name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market         TEXT NOT NULL CHECK (market IN ('上市', '上櫃', '境外基金')),
  shares         REAL NOT NULL CHECK (shares > 0 AND shares <= 1000000000),
  average_cost   REAL NOT NULL CHECK (average_cost > 0 AND average_cost <= 10000000),
  note           TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
  in_universe    INTEGER NOT NULL CHECK (in_universe IN (0, 1)),
  source         TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'gemini')),
  source_ref     TEXT,
  last_price     REAL CHECK (last_price IS NULL OR last_price >= 0),
  price_as_of    TEXT,
  price_source   TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (email, code)
);
INSERT INTO user_holdings_v2 (email, code, name, market, shares, average_cost, note, in_universe, source, source_ref, last_price, price_as_of, price_source, created_at, updated_at)
  SELECT email, code, name, market, shares, average_cost, note, in_universe, source, source_ref, last_price, price_as_of, price_source, created_at, updated_at FROM user_holdings;
DROP TABLE user_holdings;
ALTER TABLE user_holdings_v2 RENAME TO user_holdings;
CREATE INDEX user_holdings_recent ON user_holdings(email, updated_at DESC, code);
CREATE TRIGGER user_holdings_limit BEFORE INSERT ON user_holdings
WHEN NOT EXISTS (SELECT 1 FROM user_holdings WHERE email = NEW.email AND code = NEW.code)
  AND (SELECT COUNT(*) FROM user_holdings WHERE email = NEW.email) >= 100
BEGIN
  SELECT RAISE(ABORT, 'holdings limit reached');
END;

CREATE TABLE user_search_history_v2 (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
  name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market      TEXT NOT NULL CHECK (market IN ('上市', '上櫃', '境外基金')),
  viewed_at   TEXT NOT NULL,
  PRIMARY KEY (email, code)
);
INSERT INTO user_search_history_v2 (email, code, name, market, viewed_at)
  SELECT email, code, name, market, viewed_at FROM user_search_history;
DROP TABLE user_search_history;
ALTER TABLE user_search_history_v2 RENAME TO user_search_history;
CREATE INDEX user_search_history_recent ON user_search_history(email, viewed_at DESC, code);

CREATE TABLE user_drawings_v2 (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
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
INSERT INTO user_drawings_v2 (email, code, id, kind, date1, price1, date2, price2, text, created_at, updated_at)
  SELECT email, code, id, kind, date1, price1, date2, price2, text, created_at, updated_at FROM user_drawings;
DROP TABLE user_drawings;
ALTER TABLE user_drawings_v2 RENAME TO user_drawings;
CREATE TRIGGER user_drawings_limit BEFORE INSERT ON user_drawings
WHEN NOT EXISTS (SELECT 1 FROM user_drawings WHERE email = NEW.email AND code = NEW.code AND id = NEW.id)
  AND (SELECT COUNT(*) FROM user_drawings WHERE email = NEW.email AND code = NEW.code) >= 200
BEGIN
  SELECT RAISE(ABORT, 'drawings limit reached');
END;
