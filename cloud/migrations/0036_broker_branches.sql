-- 主力分點（本人 2026-11-24「共識達成」，.scratch/broker-branches/contract.md）：券商分點每日買賣超。
-- 抓取腳本 broker_branches.py 寫入（只抓站主的自選股與庫存）；個股 API /api/stocks/<code>/branch-history 讀。
-- 這三張表只增不刪：來源失敗或改版時保留舊資料，失敗原因的種類記在 broker_branch_stocks.last_error。

CREATE TABLE IF NOT EXISTS broker_branches (
  branch_id  TEXT PRIMARY KEY,          -- 來源網址的 b 參數原樣（四碼，或 16 個十六進位字元）
  bhid       TEXT NOT NULL,             -- 來源網址的 BHID（所屬券商）
  name       TEXT NOT NULL,             -- 顯示名稱（例：臺銀、國泰-敦南）
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS broker_branch_days (
  code      TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  date      TEXT NOT NULL,              -- YYYY-MM-DD
  buy       REAL NOT NULL,              -- 張
  sell      REAL NOT NULL,
  net       REAL NOT NULL,              -- 買進 − 賣出
  PRIMARY KEY (code, branch_id, date)
);
CREATE TABLE IF NOT EXISTS broker_branch_stocks (
  code            TEXT PRIMARY KEY,
  backfilled_from TEXT,                 -- 已回補到的最早日期；NULL＝還沒回補
  updated_through TEXT,                 -- 資料到哪一天
  last_attempt_at TEXT,
  last_error      TEXT                  -- 失敗原因的種類（HTTP 狀態碼、format-changed…）；成功是 NULL
);
