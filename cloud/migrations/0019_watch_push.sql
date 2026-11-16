-- 自選股關注 F 票（規格 .scratch/watchlist-watch/spec.md〈推播〉〈盤中盯價〉）：推播訂閱、觸價紀錄、雲端盯價的狀態。
-- 編號 0019 由 owner 分配（2026-11-16）。套用到正式資料庫前要先問本人。

-- 站主每一台裝置的推播訂閱（網頁標準推播的端點與加密用的兩把公開值）。訂閱失效（推播服務回 404／410）時自動刪掉。
CREATE TABLE push_subscriptions (
  endpoint        TEXT PRIMARY KEY CHECK (endpoint LIKE 'https://%' AND length(endpoint) <= 1024),
  email           TEXT NOT NULL,
  p256dh          TEXT NOT NULL CHECK (length(p256dh) BETWEEN 80 AND 100),
  auth            TEXT NOT NULL CHECK (length(auth) BETWEEN 16 AND 30),
  label           TEXT NOT NULL CHECK (length(label) <= 60),
  created_at      TEXT NOT NULL,
  last_sent_at    TEXT,
  last_error      TEXT
);
CREATE INDEX push_subscriptions_email ON push_subscriptions (email, created_at);

-- 觸價紀錄：同一份判斷只記一次、只通知一次。只能新增：資料庫層擋掉修改與刪除。
CREATE TABLE watch_touches (
  judgment_id     TEXT PRIMARY KEY,
  code            TEXT NOT NULL,
  trigger_value   REAL NOT NULL,
  price           REAL NOT NULL,
  quote_time      TEXT NOT NULL,
  touched_at      TEXT NOT NULL,
  devices         INTEGER NOT NULL CHECK (devices >= 0)
);
CREATE INDEX watch_touches_code ON watch_touches (code, touched_at);
CREATE TRIGGER watch_touches_no_update BEFORE UPDATE ON watch_touches
BEGIN SELECT RAISE(ABORT, 'watch touches are append-only'); END;
CREATE TRIGGER watch_touches_no_delete BEFORE DELETE ON watch_touches
BEGIN SELECT RAISE(ABORT, 'watch touches are append-only'); END;

-- 雲端每分鐘盯價：每一檔最後一次查報價的時間（查不完時下一分鐘先查最久沒查的）＋最後一次執行的狀態（key = 'last-run'）。
CREATE TABLE watch_tick_state (
  key             TEXT PRIMARY KEY,
  checked_at      TEXT NOT NULL,
  detail_json     TEXT
);
