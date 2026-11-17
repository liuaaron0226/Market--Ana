-- 基金當成一般個股 03 票（本人 2026-11-18「我希望你就把它當成一般個股」，規格 .scratch/fund-as-stock/spec.md）：
-- 基金名錄（目前只有貝萊德世界科技 A10，代碼 BLKTENAU），跟著個股資料版本走，和普通股、ETF 名錄分開放。
-- 搜尋與個股入口把它和普通股、ETF 一起查（類別「基金」、市場「境外基金」），只在基金開關對這個人是開的時候。
-- 基金的分析結果和普通股一樣放 stock_analysis（kind = "fund"）；淨值序列放 stock_history。
-- 編號 0026 暫定，由 owner 分配；套用到正式資料庫前要先問本人。測試直接讀這個檔建表，結構只在這裡維護。
CREATE TABLE fund_securities (
  version          TEXT NOT NULL,
  -- 集保基金代碼；規則和 cloud/lib/codes.js 的 CODE_PATTERN 相同（4～12 碼大寫英數）
  code             TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12 AND code NOT GLOB '*[^0-9A-Z]*'),
  name             TEXT NOT NULL,   -- 短名，例如「貝萊德世界科技 A10」；搜尋結果顯示這個
  full_name        TEXT NOT NULL,   -- 集保登記的全名，搜尋也比對它
  currency         TEXT NOT NULL CHECK (length(currency) = 3),
  isin             TEXT,
  -- 淨值資料日（最新一筆淨值的日期）與淨值筆數；欄名和 etf_securities 一致，搜尋可以同一套 UNION
  latest_bar_date  TEXT,
  available_bars   INTEGER NOT NULL CHECK (available_bars >= 0),
  analysis_status  TEXT NOT NULL CHECK (analysis_status IN ('available', 'insufficient', 'stale', 'unavailable')),
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);
CREATE INDEX fund_securities_name ON fund_securities(version, name, code);
