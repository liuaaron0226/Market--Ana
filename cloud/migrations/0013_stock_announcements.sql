-- 個股頁事實區塊 D 票（2026-11-16）：公司重大訊息，跟著個股資料版本走。
-- 每個版本、每一檔一列：payload 是那一檔「近 90 天、由新到舊」的公告清單（來源是公開資訊觀測站的官方 CSV，
-- 政府資料開放授權第 1 版）。整張表每次發布由原件重算，只透過 cloud/import_d1_seed.py 寫入。
-- 編號：立體重設計分支已有 0012_watchlist_group.sql，這裡用 0013；多使用者線也在排變更檔，合併時要再確認沒有撞號。
CREATE TABLE stock_announcements (
  version   TEXT NOT NULL,
  code      TEXT NOT NULL,
  as_of     TEXT NOT NULL,
  payload   TEXT NOT NULL CHECK (json_valid(payload) AND json_extract(payload, '$.schema') IS 'stock-announcements/v1'),
  PRIMARY KEY (version, code),
  FOREIGN KEY (version) REFERENCES stock_data_versions(version) ON DELETE CASCADE
);
