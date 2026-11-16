-- 自選股關注 B 票（2026-11-16）：國外同類股名單與國外行情。整個功能只有站主讀得到（入口在 lib/watch-peers.js）。
-- 編號：0012 在立體重設計分支、0013 是公司重大訊息；多使用者線也在排變更檔，合併時由 owner 再確認沒有撞號。

-- 名單的現況：每一檔關注股一份。symbol 用正規化後的寫法（NVDA.US、005930.KO、N225.INDX、8035.TSE）。
-- status：nominated（Gemini 提名、等站主確認，G 包才會產生）／confirmed（使用中）／removed（移除，保留列好讓再加回來）。
CREATE TABLE watch_peers (
  email      TEXT NOT NULL,
  code       TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  name       TEXT NOT NULL,
  market     TEXT NOT NULL CHECK (market IN ('US', 'KR', 'INDEX', 'JP')),
  status     TEXT NOT NULL CHECK (status IN ('nominated', 'confirmed', 'removed')),
  reason     TEXT,
  added_by   TEXT NOT NULL CHECK (added_by IN ('owner', 'gemini')),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (email, code, symbol)
);
CREATE INDEX watch_peers_symbol ON watch_peers(status, symbol);

-- 名單的每一次改動：只能新增，資料庫層擋掉修改與刪除。
CREATE TABLE watch_peer_events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  email   TEXT NOT NULL,
  code    TEXT NOT NULL,
  symbol  TEXT NOT NULL,
  name    TEXT NOT NULL,
  action  TEXT NOT NULL CHECK (action IN ('nominate', 'confirm', 'add', 'remove')),
  at      TEXT NOT NULL
);
CREATE INDEX watch_peer_events_code ON watch_peer_events(email, code, id);
CREATE TRIGGER watch_peer_events_no_update BEFORE UPDATE ON watch_peer_events
BEGIN
  SELECT RAISE(ABORT, 'watch_peer_events is append-only');
END;
CREATE TRIGGER watch_peer_events_no_delete BEFORE DELETE ON watch_peer_events
BEGIN
  SELECT RAISE(ABORT, 'watch_peer_events is append-only');
END;

-- 國外日線：每日更新後由 overseas_peers.py 寫入（保留約一年）。不分使用者，只經站主的入口讀出。
-- 來源條款要求終止使用時能整批刪除：`python overseas_peers.py purge --source massive|eodhd --yes`。
CREATE TABLE overseas_quotes (
  symbol     TEXT NOT NULL,
  date       TEXT NOT NULL,
  open       REAL,
  high       REAL,
  low        REAL,
  close      REAL NOT NULL CHECK (close > 0),
  volume     REAL,
  source     TEXT NOT NULL CHECK (source IN ('massive', 'eodhd')),
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);

-- 每一檔最後一次抓取的結果。抓不到時保留舊日線、這裡記原因（not_found／quota／format／network／no_key）。
CREATE TABLE overseas_fetch_status (
  symbol       TEXT PRIMARY KEY,
  source       TEXT NOT NULL CHECK (source IN ('massive', 'eodhd')),
  attempted_at TEXT NOT NULL,
  ok           INTEGER NOT NULL CHECK (ok IN (0, 1)),
  succeeded_at TEXT,
  problem      TEXT
);
