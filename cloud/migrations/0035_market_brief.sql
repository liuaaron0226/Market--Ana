-- 盤勢解讀（本人 2026-11-23「共識達成」，.scratch/market-brief/contract.md）：一班一份、分類別的解讀。
-- 產生器 market_brief.py 每一班寫一列；同一個（交易日, 時段）重跑就蓋掉，超過 35 天的由產生器清掉（比照首頁消息）。
-- 「最新一份」＝trade_date 最大，同一天照 morning < midday < close < evening 取最後一個。

CREATE TABLE IF NOT EXISTS market_brief_runs (
  trade_date   TEXT NOT NULL,            -- 台北交易日 YYYY-MM-DD
  slot         TEXT NOT NULL,            -- morning | midday | close | evening
  generated_at TEXT NOT NULL,            -- ISO 8601
  source       TEXT NOT NULL,            -- ai | template
  model        TEXT,
  payload      TEXT NOT NULL,            -- JSON（schema market-brief/v1）
  PRIMARY KEY (trade_date, slot)
);
