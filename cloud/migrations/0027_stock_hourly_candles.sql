-- 60 分 K 歷史（本人 2026-11-18 核准：照提案抓富果歷史分 K、往回 6 個月、所有核准使用者都看得到）。
-- 第一次有人看某一檔的 60 分時，向富果 /historical/candles?timeframe=60 抓近 6 個月存在這裡；之後每天只補缺的那幾天。
-- 開頁直接讀這張表，不重抓。今天盤中那幾根不存（還在形成），由即時服務的 1 分 K 合成後接在最後。
-- 富果資料只給畫面，不進 Gemini 提示。結構只在這裡維護，測試直接讀這個檔建表（cloud/test/helpers.js）。
CREATE TABLE stock_hourly_candles (
  code    TEXT NOT NULL,
  t       TEXT NOT NULL,   -- 那一根開始的時間，台北時間 ISO，例如 2026-11-17T09:00:00.000+08:00
  open    REAL NOT NULL,
  high    REAL NOT NULL,
  low     REAL NOT NULL,
  close   REAL NOT NULL,
  volume  INTEGER,         -- 股（富果給的是張，存的時候 ×1000，和日 K 同單位）
  PRIMARY KEY (code, t)
);

-- 每一檔補到哪一天（含）：下一次只抓這一天之後到昨天；抓的區間裡富果沒有資料（假日）也算補過。
CREATE TABLE stock_hourly_state (
  code        TEXT PRIMARY KEY,
  through     TEXT NOT NULL,   -- YYYY-MM-DD
  fetched_at  TEXT NOT NULL
);
