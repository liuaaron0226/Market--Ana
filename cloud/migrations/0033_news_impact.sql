-- 消息改成「影響＋要看的條件＋準備」寫法（本人 2026-11-20「共識達成」；.scratch/impact-watch/spec.md、票 01）。
-- 每則消息（市場三組、個股判讀）與每一輪的總結各多三段；舊的列是 NULL，畫面照舊。
--   effect       影響：影響到誰、多大（一句）
--   conditions   接下來要看的條件：JSON 字串陣列，0～2 個；沒有是 NULL
--   prepare      先做好的準備：JSON 字串陣列，0～2 句「如果…就…」；沒有是 NULL
--   owner_effect 點名站主自選股／庫存的那一句：只回給站主（cloud/lib/market-news.js），朋友拿不到
-- 個股判讀（stock_news_marks）是大家共用的，所以沒有 owner_effect。
-- 這一張只存文字；條件交給系統盯（盯項）是後面的票，會有自己的資料表。
ALTER TABLE market_news_runs ADD COLUMN effect TEXT;
ALTER TABLE market_news_runs ADD COLUMN owner_effect TEXT;
ALTER TABLE market_news_runs ADD COLUMN conditions TEXT;
ALTER TABLE market_news_runs ADD COLUMN prepare TEXT;

ALTER TABLE market_news_items ADD COLUMN effect TEXT;
ALTER TABLE market_news_items ADD COLUMN owner_effect TEXT;
ALTER TABLE market_news_items ADD COLUMN conditions TEXT;
ALTER TABLE market_news_items ADD COLUMN prepare TEXT;

ALTER TABLE stock_news_marks ADD COLUMN effect TEXT;
ALTER TABLE stock_news_marks ADD COLUMN conditions TEXT;
ALTER TABLE stock_news_marks ADD COLUMN prepare TEXT;
