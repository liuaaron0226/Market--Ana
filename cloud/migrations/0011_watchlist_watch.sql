-- 自選股關注（2026-11-15 規格，A 票）：站主逐檔打開的開關，最多 20 檔。
-- 既有的每一檔預設沒有關注；關注是開關不是刪除，關掉只把 watching 設回 0。
ALTER TABLE user_watchlist ADD COLUMN watching INTEGER NOT NULL DEFAULT 0 CHECK (watching IN (0, 1));
ALTER TABLE user_watchlist ADD COLUMN watch_since TEXT;
CREATE INDEX user_watchlist_watching ON user_watchlist(email, watching, watch_since, code);
-- 上限由資料表自己擋，不能用併發繞過入口的前置檢查。數字和 lib/watch.js 的 WATCH_LIMIT 是同一個，要調整得兩邊一起改。
CREATE TRIGGER user_watchlist_watch_limit BEFORE UPDATE OF watching ON user_watchlist
WHEN NEW.watching = 1 AND OLD.watching = 0
  AND (SELECT COUNT(*) FROM user_watchlist WHERE email = NEW.email AND watching = 1) >= 20
BEGIN
  SELECT RAISE(ABORT, 'watch limit reached');
END;
