-- 盯項：系統替本人盯的條件（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch/spec.md，票 03）。
-- 由排程寫：market_news.py 在寫消息時建立（模型從程式給的可盯條件清單用編號挑），impact_watch.py check 在每日更新
-- 發布之後判斷成立／否定／到期。網站只讀（cloud/lib/impact-watch.js，開關 MARKET_ANA_IMPACT_WATCH）。
-- 這一張票只有大盤的數字型；欄位先容得下之後的消息型（kind='news'）與個股（subject＝股票代號、visibility='owner'）。
CREATE TABLE impact_watch_items (
  id            TEXT PRIMARY KEY,                 -- 對象:條件編號:建立日
  source_block  TEXT NOT NULL,                    -- 哪一塊 AI 文字寫的：market_news（之後 plain_daily、watch_judgment…）
  source_date   TEXT NOT NULL,                    -- 那一塊哪一天寫的（台北日期）
  source_ref    TEXT,                             -- 那一塊裡的哪一則（消息：輪次#編號 或 輪次#summary）
  subject       TEXT NOT NULL,                    -- 對象：market＝大盤，其他是股票代號
  kind          TEXT NOT NULL CHECK (kind IN ('numeric', 'news')),
  condition_key TEXT NOT NULL,                    -- 條件編號（可盯條件清單裡的 id；消息型之後自訂）
  params        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(params)),   -- 條件的參數（哪一條均線、哪個價位、幾天、依據哪一天的資料）
  label         TEXT NOT NULL,                    -- 條件的人話名稱（程式產生）
  note          TEXT,                             -- 模型附的一句說明
  impact        INTEGER NOT NULL CHECK (impact IN (1, 2, 3)),
  prepare       TEXT CHECK (prepare IS NULL OR json_valid(prepare)),     -- 原本寫的準備（JSON 陣列）
  created_date  TEXT NOT NULL,
  expires_on    TEXT NOT NULL,                    -- 建立後第 20 個交易日
  days_left     INTEGER,                          -- 還剩幾個交易日（每日檢查更新；有結果後是 NULL）
  status        TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching', 'met', 'failed', 'expired')),
  result_date   TEXT,
  result_note   TEXT,                             -- 結果說明（帶實際數字）
  show_until    TEXT,                             -- 有結果後首頁留到哪一天（結果日後第 3 個交易日）
  visibility    TEXT NOT NULL DEFAULT 'all' CHECK (visibility IN ('all', 'owner')),
  push_sent_at  TEXT,                             -- 推播送出的時間（票 04；NULL＝還沒送）
  created_at    TEXT NOT NULL
);
-- 同一對象同一條件同時只盯一筆
CREATE UNIQUE INDEX impact_watch_items_one_watching ON impact_watch_items(subject, condition_key) WHERE status = 'watching';
CREATE INDEX impact_watch_items_shown ON impact_watch_items(status, show_until);

-- 每日檢查順手存下當天的數字（收盤、均線、近 20 日高低、外資買賣超與連續天數），消息排程用它產生可盯條件清單。
CREATE TABLE impact_watch_facts (
  subject    TEXT PRIMARY KEY,
  data_date  TEXT NOT NULL,
  payload    TEXT NOT NULL CHECK (json_valid(payload)),
  updated_at TEXT NOT NULL
);
