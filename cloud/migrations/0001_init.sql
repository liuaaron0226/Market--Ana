-- 票 05 帳號申請與核准。站主不存在本表，由環境變數 OWNER_EMAIL 判定，不能被停用或降權。
CREATE TABLE users (
  email       TEXT PRIMARY KEY,
  nickname    TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'disabled')),
  applied_at  TEXT NOT NULL,
  decided_at  TEXT,
  decided_by  TEXT
);

-- 申請與管理動作紀錄（只新增）。
CREATE TABLE audit (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      TEXT NOT NULL,
  actor   TEXT NOT NULL,
  action  TEXT NOT NULL,
  target  TEXT NOT NULL,
  detail  TEXT
);

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
