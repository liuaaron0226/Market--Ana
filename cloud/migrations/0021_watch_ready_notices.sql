-- 自選股關注 G 票（規格 .scratch/watchlist-watch/spec.md〈推播〉）：早晚的判斷把某一檔變成「可以留意」時推播一次。
-- 去重的單位是「同一檔、同一個轉變日」：晚間版與隔天早上的晨間版都在同一個基準日變成「可以留意」，只通知一次。
-- 先記再送（和觸價紀錄 watch_touches 同一個做法）：兩次執行重疊也不會重複通知。只能新增。
-- 編號 0021 由 owner 分配（2026-11-16）。套用到正式資料庫前要先問本人。
CREATE TABLE watch_ready_notices (
  code            TEXT NOT NULL,
  base_date       TEXT NOT NULL CHECK (length(base_date) = 10),
  judgment_id     TEXT NOT NULL,
  edition         TEXT NOT NULL CHECK (edition IN ('evening', 'morning')),
  trigger_value   REAL,
  noticed_at      TEXT NOT NULL,
  devices         INTEGER NOT NULL CHECK (devices >= 0),   -- 記下這筆時登記著幾台（要送的台數，不是送達的台數）
  PRIMARY KEY (code, base_date)
);
CREATE TRIGGER watch_ready_notices_no_update BEFORE UPDATE ON watch_ready_notices
BEGIN SELECT RAISE(ABORT, 'watch ready notices are append-only'); END;
CREATE TRIGGER watch_ready_notices_no_delete BEFORE DELETE ON watch_ready_notices
BEGIN SELECT RAISE(ABORT, 'watch ready notices are append-only'); END;
