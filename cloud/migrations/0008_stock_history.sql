-- 個股 5 年歷史日線（本人 2026-11-14：先做 5 年、放進資料庫）。
-- 每檔一列、每天覆寫同一列，不分版本：個股分析 payload 已帶最近 120 根，這裡只是讓圖能往前拉，
-- 晚一天更新也不影響最新價格。價格口徑與個股分析相同（分割、面額變更、減資、配股已還原；現金股利未還原）。
CREATE TABLE stock_history (
  code       TEXT PRIMARY KEY,
  data_date  TEXT NOT NULL,
  -- 用 IS 而不是 =：缺 schema 時 json_extract 回 NULL，「NULL = 值」在 CHECK 中會被當成通過。
  payload    TEXT NOT NULL CHECK (json_valid(payload) AND json_extract(payload, '$.schema') IS 'stock-history/v1')
);
