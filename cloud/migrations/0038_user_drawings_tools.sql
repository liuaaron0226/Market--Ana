-- 分析工具再加四種（本人 2026-11-24：量測、平行通道、水平價位線＋到價提醒、區間成交量分布）。
-- SQLite 改不了 CHECK，照 0028 的做法重建：原本的欄位、資料、主鍵與每一檔 200 條的觸發器原樣搬。
--   kind 多四種：measure 量測（兩個錨點）、channel 平行通道（三個錨點）、alert 到價提醒的水平價位線（一個）、vprofile 區間成交量分布（兩個）。
--   date3／price3：平行通道的第三個錨點——日期和第一個錨點同一天，價位＝第一個錨點的價位＋通道的寬度（平行線就是原本那條上下平移這麼多）。
--   到價提醒（只有 kind = 'alert' 的線會開；只開給站主，由每分鐘盯價的排程 cloud/lib/watch-tick.js 比價）：
--     alert             1＝開著、還沒通知過；通知之後排程把它關成 0（線留著）。
--     alert_side        開著時價格在線的哪一邊：up＝價格在線下面，漲到線（成交價 >= 線）就通知；down＝價格在線上面，跌到線就通知；
--                       NULL＝畫的時候不知道現價，排程第一次看到報價時補上。
--     alert_name        通知標題要寫的股名（畫的時候頁面上的名稱；不在自選股裡的股票排程查不到名稱）。
--     alert_fired_at／alert_fired_price  通知的時間與當時的成交價；重新打開提醒時清掉。
-- 這一支沒套之前：原本六種照舊能存能讀（user-drawings.js 退回舊欄位），新的四種存不上去（頁面顯示「未儲存」、之後自動補存），
-- 排程讀不到提醒就當作沒有，原本的觸發線盯價不受影響。
CREATE TABLE user_drawings_v4 (
  email       TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 12),
  id          TEXT NOT NULL CHECK (length(id) BETWEEN 8 AND 40),
  kind        TEXT NOT NULL CHECK (kind IN ('trend', 'hline', 'ray', 'rect', 'fib', 'text', 'measure', 'channel', 'alert', 'vprofile')),
  frame       TEXT NOT NULL DEFAULT 'day' CHECK (frame IN ('day', 'hour')),
  date1       TEXT NOT NULL,
  price1      REAL NOT NULL CHECK (price1 > 0 AND price1 <= 10000000),
  date2       TEXT,
  price2      REAL CHECK (price2 IS NULL OR (price2 > 0 AND price2 <= 10000000)),
  date3       TEXT,
  price3      REAL CHECK (price3 IS NULL OR (price3 > 0 AND price3 <= 10000000)),
  text        TEXT NOT NULL DEFAULT '' CHECK (length(text) <= 200),
  alert       INTEGER NOT NULL DEFAULT 0 CHECK (alert IN (0, 1)),
  alert_side  TEXT CHECK (alert_side IS NULL OR alert_side IN ('up', 'down')),
  alert_name  TEXT NOT NULL DEFAULT '' CHECK (length(alert_name) <= 40),
  alert_fired_at    TEXT,
  alert_fired_price REAL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (email, code, id),
  CHECK ((date2 IS NULL) = (price2 IS NULL)),
  CHECK ((date3 IS NULL) = (price3 IS NULL)),
  CHECK (date3 IS NULL OR date2 IS NOT NULL),
  CHECK (alert = 0 OR kind = 'alert'),
  CHECK (length(date1) = CASE frame WHEN 'hour' THEN 16 ELSE 10 END),
  CHECK (date2 IS NULL OR length(date2) = CASE frame WHEN 'hour' THEN 16 ELSE 10 END),
  CHECK (date3 IS NULL OR length(date3) = CASE frame WHEN 'hour' THEN 16 ELSE 10 END)
);
INSERT INTO user_drawings_v4 (email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at)
  SELECT email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at FROM user_drawings;
DROP TABLE user_drawings;
ALTER TABLE user_drawings_v4 RENAME TO user_drawings;
CREATE TRIGGER user_drawings_limit BEFORE INSERT ON user_drawings
WHEN NOT EXISTS (SELECT 1 FROM user_drawings WHERE email = NEW.email AND code = NEW.code AND id = NEW.id)
  AND (SELECT COUNT(*) FROM user_drawings WHERE email = NEW.email AND code = NEW.code) >= 200
BEGIN
  SELECT RAISE(ABORT, 'drawings limit reached');
END;
-- 排程每分鐘只讀開著提醒的那幾條：只替它們建索引（平常是零到幾條）。
CREATE INDEX user_drawings_alert ON user_drawings (email, code) WHERE alert = 1;
