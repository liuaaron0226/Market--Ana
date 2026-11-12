-- G4-2W：本人申報現金、每日持股水位建議與可重播狀態。
CREATE TABLE user_cash_current (
  email       TEXT PRIMARY KEY,
  amount_twd  REAL NOT NULL CHECK (amount_twd >= 0 AND amount_twd <= 1000000000000),
  updated_at  TEXT NOT NULL,
  version     INTEGER NOT NULL CHECK (version >= 1)
);

CREATE TABLE user_cash_history (
  email        TEXT NOT NULL,
  version      INTEGER NOT NULL CHECK (version >= 1),
  amount_twd   REAL NOT NULL CHECK (amount_twd >= 0 AND amount_twd <= 1000000000000),
  effective_at TEXT NOT NULL,
  recorded_at  TEXT NOT NULL,
  PRIMARY KEY (email, version)
);
CREATE INDEX user_cash_history_recent ON user_cash_history(email, version DESC);
CREATE TRIGGER user_cash_history_no_update BEFORE UPDATE ON user_cash_history
BEGIN SELECT RAISE(ABORT, 'cash history is append only'); END;
CREATE TRIGGER user_cash_history_no_delete BEFORE DELETE ON user_cash_history
BEGIN SELECT RAISE(ABORT, 'cash history is append only'); END;
CREATE TRIGGER user_cash_history_update_current AFTER INSERT ON user_cash_history
BEGIN
  INSERT INTO user_cash_current (email, amount_twd, updated_at, version)
  VALUES (NEW.email, NEW.amount_twd, NEW.effective_at, NEW.version)
  ON CONFLICT (email) DO UPDATE SET
    amount_twd = excluded.amount_twd,
    updated_at = excluded.updated_at,
    version = excluded.version
  WHERE excluded.version > user_cash_current.version;
END;

-- 同一資料日可有修正版；舊版永不覆寫，is_current 只指向目前顯示版本。
CREATE TABLE level_daily (
  data_date       TEXT NOT NULL,
  version         INTEGER NOT NULL CHECK (version >= 1),
  rule_version    TEXT NOT NULL,
  source_cutoff   TEXT NOT NULL,
  input_sha256    TEXT NOT NULL CHECK (length(input_sha256) = 64),
  payload         TEXT NOT NULL CHECK (json_valid(payload)),
  created_at      TEXT NOT NULL,
  is_current      INTEGER NOT NULL CHECK (is_current IN (0, 1)),
  PRIMARY KEY (data_date, version)
);
CREATE UNIQUE INDEX level_daily_one_current ON level_daily(data_date) WHERE is_current = 1;
CREATE INDEX level_daily_latest ON level_daily(data_date DESC, version DESC);
CREATE TRIGGER level_daily_no_delete BEFORE DELETE ON level_daily
BEGIN SELECT RAISE(ABORT, 'level daily is append only'); END;
CREATE TRIGGER level_daily_payload_immutable BEFORE UPDATE OF
  data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at
ON level_daily
BEGIN SELECT RAISE(ABORT, 'level daily payload is append only'); END;

CREATE TABLE level_strategy_state (
  key             TEXT PRIMARY KEY CHECK (key = 'current'),
  data_date       TEXT NOT NULL,
  rule_version    TEXT NOT NULL,
  payload         TEXT NOT NULL CHECK (json_valid(payload)),
  input_sha256    TEXT NOT NULL CHECK (length(input_sha256) = 64),
  updated_at      TEXT NOT NULL
);
CREATE TRIGGER level_daily_activate AFTER INSERT ON level_daily
BEGIN
  UPDATE level_daily SET is_current = 0
  WHERE data_date = NEW.data_date AND version <> NEW.version AND is_current = 1;
  UPDATE level_daily SET is_current = 1
  WHERE data_date = NEW.data_date AND version = NEW.version;
  INSERT INTO level_strategy_state (key, data_date, rule_version, payload, input_sha256, updated_at)
  VALUES ('current', NEW.data_date, NEW.rule_version, COALESCE(json_extract(NEW.payload, '$.state'), '{}'), NEW.input_sha256, NEW.created_at)
  ON CONFLICT (key) DO UPDATE SET
    data_date = excluded.data_date,
    rule_version = excluded.rule_version,
    payload = excluded.payload,
    input_sha256 = excluded.input_sha256,
    updated_at = excluded.updated_at
  WHERE excluded.data_date >= level_strategy_state.data_date;
END;
