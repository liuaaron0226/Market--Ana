-- 自選股關注的判斷與對帳（規格 .scratch/watchlist-watch/spec.md，E 票第 8 片）：網站讀的那一份。
-- 唯一的事實來源是關注帳本的鎖定原件（watch_ledger.py 寫在工作集）；這裡只放給網站顯示的欄位，
-- 並帶著原件的雜湊。三張表都只能新增：資料庫層擋掉修改與刪除（和 0010 預測帳本同一套）。
-- 套用到正式資料庫前要先問本人。編號暫定 0014，由 owner 統一分配。
CREATE TABLE watch_judgments (
  judgment_id     TEXT PRIMARY KEY,
  code            TEXT NOT NULL,
  base_date       TEXT NOT NULL CHECK (length(base_date) = 10),
  edition         TEXT NOT NULL CHECK (edition IN ('evening', 'morning')),
  method_version  TEXT NOT NULL,
  input_version   TEXT NOT NULL,
  run_mode        TEXT NOT NULL CHECK (run_mode IN ('trial', 'official')),
  kind            TEXT NOT NULL CHECK (kind IN ('judgment', 'miss')),
  status          TEXT CHECK (status IS NULL OR status IN ('還沒到', '接近', '可以留意')),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL,
  -- 帳本規則：一檔、一個基準日、一版只有一筆（方法或輸入改版也不會多出第二筆）。
  UNIQUE (code, base_date, edition)
);
CREATE INDEX watch_judgments_base_date ON watch_judgments (base_date, code);

CREATE TABLE watch_reconciliations (
  judgment_id     TEXT NOT NULL REFERENCES watch_judgments (judgment_id),
  horizon         INTEGER NOT NULL CHECK (horizon IN (5, 20)),
  code            TEXT NOT NULL,
  edition         TEXT NOT NULL CHECK (edition IN ('evening', 'morning')),
  expiry_date     TEXT NOT NULL CHECK (length(expiry_date) = 10),
  beat_index      INTEGER NOT NULL CHECK (beat_index IN (0, 1)),
  record_json     TEXT NOT NULL,
  record_sha256   TEXT NOT NULL CHECK (length(record_sha256) = 64),
  published_at    TEXT NOT NULL,
  PRIMARY KEY (judgment_id, horizon)
);
CREATE INDEX watch_reconciliations_code ON watch_reconciliations (code, horizon, edition);

-- 彙總由帳本模組算好再發布（和對帳同一套算法），網站只讀最新一份。
CREATE TABLE watch_summaries (
  summary_sha256  TEXT PRIMARY KEY CHECK (length(summary_sha256) = 64),
  as_of           TEXT NOT NULL CHECK (length(as_of) = 10),
  summary_json    TEXT NOT NULL,
  published_at    TEXT NOT NULL
);
CREATE INDEX watch_summaries_as_of ON watch_summaries (as_of, published_at);

-- INSERT OR REPLACE 會先刪掉舊列，UPDATE／DELETE 觸發器擋不到；同一筆換了雜湊（內容不同）一律拒絕。
-- 同一檔、同一天、同一版但識別不同（方法或輸入改版後重跑）也拒絕。同內容重發由 ON CONFLICT DO NOTHING 吸收。
CREATE TRIGGER watch_judgments_no_replace BEFORE INSERT ON watch_judgments
WHEN EXISTS (SELECT 1 FROM watch_judgments
             WHERE (judgment_id = NEW.judgment_id OR (code = NEW.code AND base_date = NEW.base_date AND edition = NEW.edition))
               AND record_sha256 <> NEW.record_sha256)
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_reconciliations_no_replace BEFORE INSERT ON watch_reconciliations
WHEN EXISTS (SELECT 1 FROM watch_reconciliations
             WHERE judgment_id = NEW.judgment_id AND horizon = NEW.horizon AND record_sha256 <> NEW.record_sha256)
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_judgments_no_update BEFORE UPDATE ON watch_judgments
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_judgments_no_delete BEFORE DELETE ON watch_judgments
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_reconciliations_no_update BEFORE UPDATE ON watch_reconciliations
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_reconciliations_no_delete BEFORE DELETE ON watch_reconciliations
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_summaries_no_update BEFORE UPDATE ON watch_summaries
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
CREATE TRIGGER watch_summaries_no_delete BEFORE DELETE ON watch_summaries
BEGIN SELECT RAISE(ABORT, 'watch ledger is append-only'); END;
