-- 資料更新狀態的推播提醒（2026-11-24，本人退訂 Claude 後沒有人盯排程；cloud/lib/data-health-alert.js）。
-- 一份資料一列：出問題（延遲、停止更新、內容降級）時開一筆，送出「出問題」那一則；恢復正常時收掉，送出「已恢復」那一則。
-- 送之前先寫時間認領（兩次排程重疊時只有一邊認領得到），沒有任何裝置收到就把認領放掉、下一次排程再送。
-- 同一份資料恢復後又出問題：同一列重新開（舊的時間蓋掉）。每天最多送幾則由程式數這張表裡今天的送出時間。
-- 編號 0040（0039 是另一條分支的）；套用到正式資料庫前要先問本人。沒套之前提醒什麼都不做，設定頁照樣看得到狀態。
CREATE TABLE IF NOT EXISTS data_health_alerts (
  key            TEXT PRIMARY KEY,                 -- cloud/lib/data-health.js RULES 的 key
  state          TEXT NOT NULL CHECK (state IN ('late', 'failed', 'degraded')),   -- 開的時候是哪一種
  since          TEXT,                             -- 問題從什麼時候開始（判斷給的時間，ISO）
  opened_at      TEXT NOT NULL,                    -- 這一次問題第一次被看到
  start_sent_at  TEXT,                             -- 「出問題」那一則送出的時間（NULL＝還沒送）
  closed_at      TEXT,                             -- 恢復的時間（NULL＝問題還在）
  end_sent_at    TEXT                              -- 「已恢復」那一則送出的時間
);
