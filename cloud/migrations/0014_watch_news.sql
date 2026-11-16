-- 自選股關注 C 票（2026-11-16）：關注股的新聞標題，只給站主看。
-- 編號暫定 0014：多條工作線同時在排變更檔，合併時由 owner 統一確認沒有撞號。
-- 來源是 Yahoo股市個股新聞 RSS 與中央通訊社產經證券／國際／科技 RSS（條款：個人非商業、標示出處、不得引用全文）。
-- 每則只存標題、前言、連結、出處、發布時間、取得時間；不存全文。由 watch_news.py 每天寫入、同一則不重複存，
-- 發布超過 90 天的由同一支程式清掉。不跟個股資料版本走（關注股是站主的資料，不是每日發布的一部分）。
CREATE TABLE watch_news (
  code          TEXT NOT NULL,          -- 對到的關注股代號；'' = 中央社對不到任何關注股（當天的大環境消息）
  link          TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('yahoo', 'cna-finance', 'cna-intworld', 'cna-technology')),
  title         TEXT NOT NULL,
  summary       TEXT NOT NULL,
  published_at  TEXT NOT NULL,          -- UTC，2026-11-16T07:44:00Z
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (code, link)
);
CREATE INDEX watch_news_published ON watch_news(published_at);

-- 每個來源最近一次抓的結果，畫面用來分「今天沒抓到」與「這幾天沒有消息」。
-- Yahoo 一檔一列（code 是代號）；中央社一個分類一列（code 是 ''）。
CREATE TABLE watch_news_fetches (
  source           TEXT NOT NULL,
  code             TEXT NOT NULL,
  last_attempt_at  TEXT NOT NULL,
  last_success_at  TEXT,
  last_error       TEXT,                -- NULL = 最近一次成功
  PRIMARY KEY (source, code)
);
