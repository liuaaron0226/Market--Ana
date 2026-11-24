-- 維運工作最後一次成功的時間（2026-11-24，本人退訂 Claude 後沒有人盯 GitHub）：目前只有 workflow-keepalive 寫。
-- 設定頁「資料更新」的「排程保活」那一列（cloud/lib/data-health.js）讀它：超過 8 天沒成功＝延遲，超過 16 天＝停止，會推播提醒。
-- 一個工作一列：每一輪正式跑完更新 last_attempt_at；成功才更新 last_ok_at。沒套這支之前保活照跑，只是寫不進來（警告），設定頁不顯示這一列。
CREATE TABLE IF NOT EXISTS ops_runs (
  job             TEXT PRIMARY KEY,   -- 例：workflow-keepalive
  last_ok_at      TEXT,               -- 最後一次成功（ISO 8601，UTC）；從沒成功過是 NULL
  last_attempt_at TEXT NOT NULL,      -- 最後一次跑（不論成功與否）
  detail          TEXT                -- 最後一次的簡短結果（程式產生，不含個資）
);
