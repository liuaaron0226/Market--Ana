-- 首頁「消息」第二輪（本人 2026-11-20；.scratch/market-news/plan-round2.md）。
-- 1) 個股新聞加回台灣財經媒體四家與公開資訊觀測站重大訊息：0031 的 watch_news 把來源寫死在 CHECK 裡，
--    每加一家就要重建一次，這次拿掉那條限制（來源由 watch_news.py 的白名單管）。舊的列原樣搬過去。
CREATE TABLE watch_news_v3 (
  code          TEXT NOT NULL,          -- 對到的股票代號；'' = 中央社對不到站主關注中的股票（大環境消息）
  link          TEXT NOT NULL,
  source        TEXT NOT NULL,          -- yahoo／cna-*／gnews-*／mops
  title         TEXT NOT NULL,
  summary       TEXT NOT NULL,
  published_at  TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, link)
);
INSERT INTO watch_news_v3 (code, link, source, title, summary, published_at, fetched_at)
  SELECT code, link, source, title, summary, published_at, fetched_at FROM watch_news;
DROP TABLE watch_news;
ALTER TABLE watch_news_v3 RENAME TO watch_news;
CREATE INDEX watch_news_published ON watch_news(published_at);
CREATE INDEX watch_news_code ON watch_news(code, published_at);

-- 2) 個股新聞的判讀：每則只判一次、大家共用（站主的自選用站主的模型；朋友自選裡還沒人判過的，用朋友自己的金鑰）。
--    relevant 0 = 和這家公司無關（只是撞到名字），存起來是為了不再重判。
--    kind：earnings 財報營收／guidance 法說財測／deal 訂單合作／policy 政策法規／rating 法人評等／incident 異常事件／other。
CREATE TABLE stock_news_marks (
  code        TEXT NOT NULL,
  link        TEXT NOT NULL,
  relevant    INTEGER NOT NULL CHECK (relevant IN (0, 1)),
  kind        TEXT NOT NULL CHECK (kind IN ('earnings', 'guidance', 'deal', 'policy', 'rating', 'incident', 'other')),
  tone        TEXT NOT NULL CHECK (tone IN ('up', 'down', 'flat')),
  impact      INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 3),
  title       TEXT NOT NULL,
  translated  INTEGER NOT NULL DEFAULT 0,
  reason      TEXT NOT NULL,
  judged_by   TEXT NOT NULL,             -- 'owner' 或 'friend'（不存 email）
  judged_at   TEXT NOT NULL,
  PRIMARY KEY (code, link)
);

-- 3) 市場新聞多一欄類別：policy 政策／earnings 財報／deal 合作訂單／flows 法人／macro 總經／other。
--    顯示先照影響大小、再照類別。舊的列是 NULL。
ALTER TABLE market_news_items ADD COLUMN kind TEXT;
