-- 票 05／06：版本化的官方上市＋上櫃普通股名錄與預算分析（表名沿用 listed_securities，market 欄區分市場）。
-- S2（2026-11-08）：stock_analysis 改為個股分析契約 v2，每檔一筆。
-- V2（2026-11-09）：本人「整個網站都只做大量股」；listed_securities 只放大量股母體，附名次與規則版本。
-- 先完整寫入 staging version，再以 stock_data_state 單列原子切換；保留 previous_version 供回退。
CREATE TABLE stock_data_versions (
  version          TEXT PRIMARY KEY,
  source_date      TEXT NOT NULL,
  source_url       TEXT NOT NULL,
  source_sha256    TEXT NOT NULL CHECK (length(source_sha256) = 64),
  built_at         TEXT NOT NULL,
  catalog_count    INTEGER NOT NULL CHECK (catalog_count >= 0),
  analysis_count   INTEGER NOT NULL CHECK (analysis_count >= 0),
  -- 母體規則版本（例如 liquid-v1）；NULL 代表全名錄版本（V2 之前的建置）
  universe_rule    TEXT
);

CREATE TABLE listed_securities (
  version           TEXT NOT NULL,
  code              TEXT NOT NULL,
  name              TEXT NOT NULL,
  market            TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
  security_type     TEXT NOT NULL CHECK (security_type = '普通股'),
  isin              TEXT NOT NULL,
  listed_date       TEXT NOT NULL,
  industry          TEXT NOT NULL,
  cfi_code          TEXT NOT NULL CHECK (cfi_code LIKE 'ESV%'),
  latest_bar_date   TEXT,
  available_bars    INTEGER NOT NULL CHECK (available_bars >= 0),
  analysis_status   TEXT NOT NULL CHECK (analysis_status IN ('available', 'insufficient', 'stale', 'unavailable')),
  -- 20 日平均成交金額名次（上市＋上櫃合併）；靠遲滯保留者名次可大於 300
  universe_rank     INTEGER CHECK (universe_rank IS NULL OR universe_rank >= 1),
  universe_rule     TEXT,
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);
CREATE INDEX listed_securities_name ON listed_securities(version, name, code);

-- V2：母體外的官方普通股只存「為什麼不在範圍」（名次或排除原因），不存分析；
-- 讓個股 API 能明確回「不在涵蓋範圍，目前第 N 名」，而不是含糊的「雲端沒有資料」。搜尋不讀這張表。
CREATE TABLE universe_outside (
  version        TEXT NOT NULL,
  code           TEXT NOT NULL,
  name           TEXT NOT NULL,
  market         TEXT NOT NULL CHECK (market IN ('上市', '上櫃')),
  universe_rank  INTEGER CHECK (universe_rank IS NULL OR universe_rank >= 1),
  reason         TEXT NOT NULL CHECK (reason IN ('below_rank', 'insufficient_window', 'suspended_on_data_date', 'no_trading_data')),
  universe_rule  TEXT NOT NULL,
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);

-- 個股分析契約 v2（本人 11-08「自創部分不保留」）：每檔一筆、沒有方案（preset）；payload 必須是 stock-analysis/v2。
-- 0004 從未套用到任何 D1，所以直接改，不另開 0005。
CREATE TABLE stock_analysis (
  version   TEXT NOT NULL,
  code      TEXT NOT NULL,
  as_of     TEXT NOT NULL,
  -- 用 IS 而不是 =：缺 schema 時 json_extract 回 NULL，「NULL = 值」在 CHECK 中會被當成通過。
  payload   TEXT NOT NULL CHECK (json_valid(payload) AND json_extract(payload, '$.schema') IS 'stock-analysis/v2'),
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);

CREATE TABLE stock_data_state (
  key               TEXT PRIMARY KEY CHECK (key = 'current'),
  active_version    TEXT NOT NULL,
  previous_version  TEXT,
  updated_at        TEXT NOT NULL,
  FOREIGN KEY (active_version) REFERENCES stock_data_versions(version),
  FOREIGN KEY (previous_version) REFERENCES stock_data_versions(version)
);
