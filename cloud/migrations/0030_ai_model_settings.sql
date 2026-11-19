-- 設定頁「AI 模型」（本人 2026-11-19 晚：「在設定裡面增加可以讓我手動調整哪個區塊要用甚麼模型思考強度是什麼」）：只有站主看得到、改得了。
-- ai_model_settings：五項任務各一列（forecast、watch_judge、watch_nominate、plain、digest），站主存過才有；沒有的那項用預設
--   （gemini_models.py；設定頁的 cloud/lib/ai-models.js 寫同一組，test_gemini_settings 對帳）。
--   model 只能是 gemini-4-argon／gemini-3.8-flash，effort 只能是 low／medium／high（API 擋、排程讀到別的值也當成沒存）。
-- ai_model_reads：每支用 Gemini 的排程開跑時讀設定的結果（gemini_settings.py），排程跑完記用量時一起寫；
--   source：saved＝讀到站主的設定；unset＝讀到了但站主還沒選過（用預設）；unreadable＝讀不到（用預設，設定頁標出來）。
-- 編號 0030 為暫定，由 owner 分配；套用到正式資料庫前要先問本人。沒套用之前設定頁顯示預設、存不了，排程照預設跑。
CREATE TABLE IF NOT EXISTS ai_model_settings (
  task TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  effort TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_model_reads (
  task TEXT NOT NULL,
  run_id TEXT NOT NULL,
  read_at TEXT NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (task, run_id)
);
CREATE INDEX IF NOT EXISTS ai_model_reads_read_at ON ai_model_reads (read_at);
