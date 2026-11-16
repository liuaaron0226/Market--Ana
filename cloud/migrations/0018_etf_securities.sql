-- 個股頁五塊新功能 12 票（2026-11-16，規格 .scratch/stock-signals/spec.md「ETF 納入」）：
-- 名單內的股票型 ETF 名錄，跟著個股資料版本走、和普通股名錄（listed_securities）分開放。
-- listed_securities 的 CHECK 只收普通股，ETF 不混進去；首頁選股與其他讀 listed_securities 的地方因此完全不受影響。
-- 只有 ETF 開關對這個人是開的時候，搜尋與加自選才會讀這張表。ETF 的分析結果和普通股一樣放 stock_analysis。
CREATE TABLE etf_securities (
  version          TEXT NOT NULL,
  code             TEXT NOT NULL,
  name             TEXT NOT NULL,
  market           TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
  -- 股票型（無字尾）或主動式股票型（A 字尾），字和 etf_classification 的分類一致；槓桿、反向、債券等不進這張表
  etf_type         TEXT NOT NULL CHECK (etf_type IN ('股票型', '主動式股票型')),
  latest_bar_date  TEXT,
  available_bars   INTEGER NOT NULL CHECK (available_bars >= 0),
  analysis_status  TEXT NOT NULL CHECK (analysis_status IN ('available', 'insufficient', 'stale', 'unavailable')),
  -- 和普通股同一把尺的成交金額名次（靠遲滯保留者可大於 300）
  universe_rank    INTEGER NOT NULL CHECK (universe_rank >= 1),
  universe_rule    TEXT NOT NULL,
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);
CREATE INDEX etf_securities_name ON etf_securities(version, name, code);
