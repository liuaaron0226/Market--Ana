-- P2：每位 Cloudflare Access 使用者各自的自選股、庫存與最近搜尋。
CREATE TABLE user_watchlist (
  email        TEXT NOT NULL,
  code         TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 6),
  name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market       TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
  horizon      TEXT NOT NULL DEFAULT 'none' CHECK (horizon IN ('none', 'short', 'long')),
  in_universe  INTEGER NOT NULL CHECK (in_universe IN (0, 1)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (email, code)
);
CREATE INDEX user_watchlist_recent ON user_watchlist(email, updated_at DESC, code);
CREATE TRIGGER user_watchlist_limit BEFORE INSERT ON user_watchlist
WHEN NOT EXISTS (SELECT 1 FROM user_watchlist WHERE email = NEW.email AND code = NEW.code)
  AND (SELECT COUNT(*) FROM user_watchlist WHERE email = NEW.email) >= 100
BEGIN
  SELECT RAISE(ABORT, 'watchlist limit reached');
END;

-- source/source_ref 預留給後續 Gemini 截圖匯入；仍寫進同一列，不建立第二份庫存。
CREATE TABLE user_holdings (
  email          TEXT NOT NULL,
  code           TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 6),
  name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market         TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
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
CREATE INDEX user_holdings_recent ON user_holdings(email, updated_at DESC, code);
CREATE TRIGGER user_holdings_limit BEFORE INSERT ON user_holdings
WHEN NOT EXISTS (SELECT 1 FROM user_holdings WHERE email = NEW.email AND code = NEW.code)
  AND (SELECT COUNT(*) FROM user_holdings WHERE email = NEW.email) >= 100
BEGIN
  SELECT RAISE(ABORT, 'holdings limit reached');
END;

CREATE TABLE user_search_history (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 6),
  name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  market      TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
  viewed_at   TEXT NOT NULL,
  PRIMARY KEY (email, code)
);
CREATE INDEX user_search_history_recent ON user_search_history(email, viewed_at DESC, code);
