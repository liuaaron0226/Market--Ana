-- 首頁「消息」加開晚間與清晨三班＋重大消息推播（本人 2026-11-24，.scratch/market-news-night 票 01）。
--
-- 1. market_news_runs 的時段多三個：evening 盤後 18:30、us_open 美股開盤 23:00、us_close 美股收盤 06:30（台北）。
--    0031 的 CHECK 只認原本三班，SQLite 改不了 CHECK，所以重建這張表（舊的列原樣搬過去；0033 加的四欄一起建）。
--    這一支沒套之前，market_news.py 遇到新的三班會安靜跳過（不抓、不叫模型），原本三班照舊。
--    一天裡的先後：us_close < morning < midday < close < evening < us_open（market_news.SLOTS）。
CREATE TABLE market_news_runs_v2 (
  run_id TEXT PRIMARY KEY,                       -- 例如 2026-11-19-close
  trade_date TEXT NOT NULL,
  slot TEXT NOT NULL CHECK (slot IN ('us_close', 'morning', 'midday', 'close', 'evening', 'us_open')),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'unclassified')),   -- unclassified：模型沒寫成，只有標題
  summary TEXT,
  indices TEXT,                                   -- JSON：[{name, change_pct, note}]
  sources TEXT,                                   -- JSON：每個來源這一輪 ok 或錯誤種類
  model TEXT,
  effect TEXT,
  owner_effect TEXT,
  conditions TEXT,
  prepare TEXT,
  UNIQUE (trade_date, slot)
);
INSERT INTO market_news_runs_v2 (run_id, trade_date, slot, created_at, status, summary, indices, sources, model,
                                 effect, owner_effect, conditions, prepare)
  SELECT run_id, trade_date, slot, created_at, status, summary, indices, sources, model,
         effect, owner_effect, conditions, prepare FROM market_news_runs;
DROP TABLE market_news_runs;
ALTER TABLE market_news_runs_v2 RENAME TO market_news_runs;
CREATE INDEX market_news_runs_recent ON market_news_runs (trade_date DESC, slot);
CREATE INDEX market_news_runs_created ON market_news_runs (created_at);

-- 2. 重大消息推播（影響大的那幾則送到站主的裝置）：送過的記在這裡，同一則只送一次、跨班也不重複。
--    同一則＝同一個連結，或同一個標題（去掉空白與標點之後）；只比最近 48 小時送過的（cloud/lib/market-news-push.js）。
--    先寫這一列認領、再送；沒有任何裝置收到就把這一列刪掉，下一班再試。超過 7 天的列由推播那一步清掉。
CREATE TABLE market_news_pushes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  link TEXT NOT NULL,
  title_key TEXT NOT NULL,          -- 標題去掉空白與標點、英文轉小寫
  run_id TEXT NOT NULL,             -- 第一次送出的那一班
  item_id TEXT NOT NULL,
  sent_at TEXT NOT NULL
);
CREATE INDEX market_news_pushes_link ON market_news_pushes (link, sent_at);
CREATE INDEX market_news_pushes_title ON market_news_pushes (title_key, sent_at);
CREATE INDEX market_news_pushes_sent ON market_news_pushes (sent_at);
