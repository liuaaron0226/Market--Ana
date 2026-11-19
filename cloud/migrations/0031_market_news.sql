-- 首頁「消息」＋個股新聞範圍（本人 2026-11-19「共識達成」，.scratch/market-news/consensus.md）。
-- 個股新聞本身仍存 watch_news（0015），但來源改成 Google News 大媒體（本人「希望你的消息來源更具考靠性」），
-- 0015 的 source CHECK 只認 yahoo 與中央社，所以重建這張表（舊的列原樣搬過去，含 yahoo 的舊標題）。

CREATE TABLE watch_news_v2 (
  code          TEXT NOT NULL,          -- 對到的股票代號；'' = 中央社對不到名單上任何一檔（當天的大環境消息）
  link          TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('yahoo', 'cna-finance', 'cna-intworld', 'cna-technology',
                                                'gnews-reuters', 'gnews-bloomberg', 'gnews-cna', 'gnews-ft',
                                                'gnews-wsj', 'gnews-nikkei')),
  title         TEXT NOT NULL,
  summary       TEXT NOT NULL,
  published_at  TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, link)
);
INSERT INTO watch_news_v2 (code, link, source, title, summary, published_at, fetched_at)
  SELECT code, link, source, title, summary, published_at, fetched_at FROM watch_news;
DROP TABLE watch_news;
ALTER TABLE watch_news_v2 RENAME TO watch_news;
CREATE INDEX watch_news_published ON watch_news(published_at);
CREATE INDEX watch_news_code ON watch_news(code, published_at);

-- 股票池前一交易日成交金額前 50（每日更新發布後整份換掉）。
CREATE TABLE stock_news_pool (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  market TEXT NOT NULL,
  rank INTEGER NOT NULL,
  data_date TEXT NOT NULL
);

-- 證交所／櫃買公開資料的英文簡稱：Google News 用英文名搜大媒體。
CREATE TABLE stock_news_names (
  code TEXT PRIMARY KEY,
  name_en TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 一輪首頁消息（交易日 08:00 盤前、11:30 午盤、14:00 收盤後）：總結與各市場指數由站主的模型寫一份，大家共用。
CREATE TABLE market_news_runs (
  run_id TEXT PRIMARY KEY,                       -- 例如 2026-11-19-close
  trade_date TEXT NOT NULL,
  slot TEXT NOT NULL CHECK (slot IN ('morning', 'midday', 'close')),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'unclassified')),   -- unclassified：模型沒寫成，只有標題
  summary TEXT,
  indices TEXT,                                   -- JSON：[{name, change_pct, note}]
  sources TEXT,                                   -- JSON：每個來源這一輪 ok 或錯誤種類
  model TEXT,
  UNIQUE (trade_date, slot)
);
CREATE INDEX market_news_runs_recent ON market_news_runs (trade_date DESC, slot);

-- 市場那三組（台股、美股、日韓中）的每一則：利多／利空／中性、影響大中小、中文標題、一句理由。
CREATE TABLE market_news_items (
  run_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  grp TEXT NOT NULL CHECK (grp IN ('tw', 'us', 'asia')),
  market TEXT,                                    -- 日韓中那組標「日」「韓」「中」
  tone TEXT NOT NULL CHECK (tone IN ('up', 'down', 'flat')),
  impact INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 3),
  title TEXT NOT NULL,                            -- 中文（外文由模型翻譯）
  translated INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  source TEXT NOT NULL,
  link TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (run_id, item_id)
);

-- 「我的自選」那組的標記：站主用站主的模型、朋友用自己的金鑰；每個人自己一份。
-- 新聞本身在 watch_news，這裡只存判讀（對到 link＋code）。沒有標記的人只看到標題。
CREATE TABLE market_news_user_items (
  email TEXT NOT NULL,
  run_id TEXT NOT NULL,
  code TEXT NOT NULL,
  link TEXT NOT NULL,
  tone TEXT NOT NULL CHECK (tone IN ('up', 'down', 'flat')),
  impact INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 3),
  title TEXT NOT NULL,
  translated INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  PRIMARY KEY (email, run_id, code, link)
);
