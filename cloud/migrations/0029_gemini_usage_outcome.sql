-- 設定頁 Gemini 用量：多記思考程度、成功或失敗、是不是乾跑（本人 2026-11-19「現在就都處理完成」；owner 派工：
-- 首頁「籌碼與資金」白話分析呼叫 Gemini 時要記進用量，任務名稱、模型、思考程度、次數、成功或失敗、日期）。
-- 目前只有白話分析（任務 daily-update）會填這三欄；其他排程照 0024 的欄位寫，三欄留空／0，設定頁照舊顯示。
-- outcome：ok＝模型寫的白話通過核對（開關 on 時就是要發布的那份；之後的步驟失敗、網站沒換，這裡照樣是 ok）；
--          failed＝模型沒回來、逾時、或核對沒過改用程式套句。agy 沒準備好、根本沒呼叫到 Gemini 的那一班不寫。
-- dry_run：1＝開關是 dry（呼叫了模型、只寫進執行紀錄、網站沒換），設定頁另外標「乾跑」，不算正式次數。
-- 編號 0029 為暫定，由 owner 分配；套用到正式資料庫前要先問本人。沒套用之前，用量 API 會退回舊查法（不顯示這三欄）。
ALTER TABLE gemini_usage_snapshots ADD COLUMN effort TEXT;
ALTER TABLE gemini_usage_snapshots ADD COLUMN outcome TEXT;
ALTER TABLE gemini_usage_snapshots ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0;
