-- 大盤事前預測帳本（規格 .scratch/forecast-ledger/spec.md，B 票第 3 片）：網站讀的那一份。
-- 唯一的事實來源是帳本的鎖定原件（forecast_ledger.py 寫在工作集）；這裡只放給網站顯示的欄位，
-- 並帶著原件的雜湊。三張表都只能新增：資料庫層擋掉修改與刪除。
-- 套用到正式資料庫前要先問本人。
CREATE TABLE forecast_records (
  forecast_id     TEXT PRIMARY KEY,
  base_date       TEXT NOT NULL CHECK (length(base_date) = 10),
  edition         TEXT NOT NULL CHECK (edition IN ('evening', 'morning')),
  method_version  TEXT NOT NULL,
  input_version   TEXT NOT NULL,
  run_mode        TEXT NOT NULL CHECK (run_mode IN ('trial', 'official')),
  kind            TEXT NOT NULL CHECK (kind IN ('forecast', 'miss')),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL
);
CREATE INDEX forecast_records_base_date ON forecast_records (base_date);

CREATE TABLE forecast_reconciliations (
  forecast_id     TEXT NOT NULL REFERENCES forecast_records (forecast_id),
  horizon         INTEGER NOT NULL CHECK (horizon IN (1, 5, 20)),
  expiry_date     TEXT NOT NULL CHECK (length(expiry_date) = 10),
  actual_zone     TEXT NOT NULL CHECK (actual_zone IN ('above', 'between', 'below')),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL,
  PRIMARY KEY (forecast_id, horizon)
);

-- 彙總由帳本模組算好再發布（和對帳同一套算法），網站只讀最新一份。
CREATE TABLE forecast_summaries (
  summary_sha256  TEXT PRIMARY KEY CHECK (length(summary_sha256) = 64),
  as_of           TEXT NOT NULL CHECK (length(as_of) = 10),
  summary_json    TEXT NOT NULL,
  published_at    TEXT NOT NULL
);
CREATE INDEX forecast_summaries_as_of ON forecast_summaries (as_of, published_at);

CREATE TRIGGER forecast_records_no_update BEFORE UPDATE ON forecast_records
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_records_no_delete BEFORE DELETE ON forecast_records
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_reconciliations_no_update BEFORE UPDATE ON forecast_reconciliations
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_reconciliations_no_delete BEFORE DELETE ON forecast_reconciliations
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_summaries_no_update BEFORE UPDATE ON forecast_summaries
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
CREATE TRIGGER forecast_summaries_no_delete BEFORE DELETE ON forecast_summaries
BEGIN SELECT RAISE(ABORT, 'forecast ledger is append-only'); END;
