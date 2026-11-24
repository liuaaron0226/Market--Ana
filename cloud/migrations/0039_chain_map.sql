-- AI 產業鏈資金版圖（本人 2026-11-24「完全實裝進去我的網站」，.scratch/ai-chain-map 票 01）。兩張表，網站只讀（cloud/lib/chain-map.js）。
-- 兩張都是「一次建置／一次修改一列」，網站讀的「目前這一份」＝時間最新的那一列（比照 market_brief_runs 取最新一份，沒有另外的指標表）。

-- 1. 產業鏈定義（層、環節、台美公司與角色、上下游、同一家公司的主要環節 primary）。
--    入庫的基準版在 seeds/ai-chain-map.json（build.mjs 包進 Functions）；這張表是之後可以改的那一份
--    （本人 11-24 晚：名單改成「Gemini 提名、站主在網站上核准」，比照國外同類股；提名與核准是之後的票）。
--    表是空的或還沒套這支 migration：網站用入庫的基準版。改一次就新增一列（不覆寫舊的，留著可以退回）。
CREATE TABLE IF NOT EXISTS chain_map_definitions (
  version    TEXT PRIMARY KEY,                               -- 這一版的名字（例：baseline-2026-11-24、owner-2026-12-01T02:03:04Z）
  source     TEXT NOT NULL CHECK (source IN ('baseline', 'owner')),   -- baseline＝照入庫的基準版寫進來；owner＝站主核准過的修改
  payload    TEXT NOT NULL CHECK (json_valid(payload)),      -- 和 seeds/ai-chain-map.json 同一個格式
  created_at TEXT NOT NULL                                   -- ISO 8601（UTC）；最新的那一列就是網站用的那一份
);
CREATE INDEX IF NOT EXISTS chain_map_definitions_created ON chain_map_definitions(created_at);

-- 2. 每月的資金數字。資料不入庫（美股那一半來自 Yahoo 的非官方介面，不能放進公開的 repo），由排程寫。
--    之後的每日排程：version＝台北的建置日 YYYY-MM-DD，同一天重跑就蓋掉（ON CONFLICT），超過保留天數的舊列由排程清掉。
--    第一次的樣品資料：chain_map_bootstrap.py 產生的一次性 SQL（version＝bootstrap-<as_of>），表裡已有排程寫的列就不寫。
--    payload 的格式（約定 chain-map-flows/v1：months、inst0、notes、tw、us）見 cloud/lib/chain-map.js 開頭。
CREATE TABLE IF NOT EXISTS chain_map_flows (
  version  TEXT PRIMARY KEY,                              -- 每日排程用 YYYY-MM-DD；一次性樣品用 bootstrap-YYYY-MM
  as_of    TEXT NOT NULL,                                 -- 資料最後一個完整月份 YYYY-MM（＝payload.months 最後一個）
  payload  TEXT NOT NULL CHECK (json_valid(payload)),     -- 每月的資金數字（JSON）
  built_at TEXT NOT NULL                                  -- 建置時間 ISO 8601（UTC）；最新的那一列就是網站讀的那一份
);
CREATE INDEX IF NOT EXISTS chain_map_flows_built ON chain_map_flows(built_at);
