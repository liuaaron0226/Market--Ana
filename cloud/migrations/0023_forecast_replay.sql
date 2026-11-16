-- 事前預測的「歷史重播（參考）」成績（本人 2026-11-17「要，重播當參考」；F 票）：網站讀的那一份。
-- 和正式／試跑的三張表（0010）分開存、分開顯示；結構與只能新增的規則相同，run_mode 只允許 replay。
-- 唯一的事實來源是重播帳本的鎖定原件（R2 的 forecast-replay/ 前綴）。
-- 編號由 owner 分配（0022 是朋友 03 的 friend_ai_stock_daily）；套用到正式資料庫前要先問本人。
CREATE TABLE forecast_replay_records (
  forecast_id     TEXT PRIMARY KEY,
  base_date       TEXT NOT NULL CHECK (length(base_date) = 10),
  edition         TEXT NOT NULL CHECK (edition IN ('evening', 'morning')),
  method_version  TEXT NOT NULL,
  input_version   TEXT NOT NULL,
  run_mode        TEXT NOT NULL CHECK (run_mode = 'replay'),
  kind            TEXT NOT NULL CHECK (kind IN ('forecast', 'miss')),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL
);
CREATE INDEX forecast_replay_records_base_date ON forecast_replay_records (base_date);

CREATE TABLE forecast_replay_reconciliations (
  forecast_id     TEXT NOT NULL REFERENCES forecast_replay_records (forecast_id),
  horizon         INTEGER NOT NULL CHECK (horizon IN (1, 5, 20)),
  expiry_date     TEXT NOT NULL CHECK (length(expiry_date) = 10),
  actual_zone     TEXT NOT NULL CHECK (actual_zone IN ('above', 'between', 'below')),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL,
  PRIMARY KEY (forecast_id, horizon)
);

-- 彙總由帳本模組算好再發布（和對帳同一套算法），網站只讀最新一份。
CREATE TABLE forecast_replay_summaries (
  summary_sha256  TEXT PRIMARY KEY CHECK (length(summary_sha256) = 64),
  as_of           TEXT NOT NULL CHECK (length(as_of) = 10),
  summary_json    TEXT NOT NULL,
  published_at    TEXT NOT NULL
);
CREATE INDEX forecast_replay_summaries_as_of ON forecast_replay_summaries (as_of, published_at);

-- INSERT OR REPLACE 會先刪掉舊列，UPDATE／DELETE 觸發器擋不到；同一筆換了雜湊（內容不同）一律拒絕。
-- 同內容重發仍由 ON CONFLICT DO NOTHING 吸收，不多一筆、不改內容。
CREATE TRIGGER forecast_replay_records_no_replace BEFORE INSERT ON forecast_replay_records
WHEN EXISTS (SELECT 1 FROM forecast_replay_records WHERE forecast_id = NEW.forecast_id AND record_sha256 <> NEW.record_sha256)
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_reconciliations_no_replace BEFORE INSERT ON forecast_replay_reconciliations
WHEN EXISTS (SELECT 1 FROM forecast_replay_reconciliations
             WHERE forecast_id = NEW.forecast_id AND horizon = NEW.horizon AND record_sha256 <> NEW.record_sha256)
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_records_no_update BEFORE UPDATE ON forecast_replay_records
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_records_no_delete BEFORE DELETE ON forecast_replay_records
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_reconciliations_no_update BEFORE UPDATE ON forecast_replay_reconciliations
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_reconciliations_no_delete BEFORE DELETE ON forecast_replay_reconciliations
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_summaries_no_update BEFORE UPDATE ON forecast_replay_summaries
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_replay_summaries_no_delete BEFORE DELETE ON forecast_replay_summaries
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
