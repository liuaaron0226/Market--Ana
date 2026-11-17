-- 設定頁的 Gemini 剩餘用量（本人 2026-11-17「在設定加入一個 gemini 剩餘用量」）：只有站主看得到。
-- 每支用 agy 的排程跑完後由 gemini_usage.py 讀一次 `agy -p /usage --output-format json`（CLI 自己回答、不耗額度）寫一筆；
-- 同一個 run 重跑時覆蓋。buckets_json 是每一格用量原樣（id、群組、5h／weekly、剩餘比例、重置時間），讀不到時是 NULL。
-- model 是這支排程這次用的模型名稱（由排程傳入；模型分配會調整，照實記）。
-- 編號 0024 由 owner 11-17 分配；套用到正式資料庫前要先問本人。gemini_usage.py 直接讀這個檔建表（測試用），結構只在這裡維護。
CREATE TABLE IF NOT EXISTS gemini_usage_snapshots (
  task TEXT NOT NULL,
  run_id TEXT NOT NULL,
  taken_at TEXT NOT NULL,
  calls INTEGER,
  model TEXT,
  buckets_json TEXT,
  PRIMARY KEY (task, run_id)
);
CREATE INDEX IF NOT EXISTS gemini_usage_snapshots_taken_at ON gemini_usage_snapshots (taken_at);
