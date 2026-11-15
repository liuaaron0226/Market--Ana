-- 自選的群組（本人 2026-11-16 定案）：自己取名、一檔只放一個群組；短期／長期（horizon）照舊另外標。
-- 群組沒有自己的資料表：某個名字有股票用著，這個群組就存在；最後一檔移走就不見。既有的每一檔都是未分組（空字串）。
-- 長度上限和 lib/user-portfolio.js 的 GROUP_MAX 是同一個，要調整得兩邊一起改。
ALTER TABLE user_watchlist ADD COLUMN group_name TEXT NOT NULL DEFAULT '' CHECK (length(group_name) <= 12);
