-- 開放給朋友 01 票（2026-11-16）：朋友自己的 Gemini 金鑰，每人一列。
-- 金鑰只存 AES-256-GCM 密文：主金鑰在網站 secret（FRIEND_AI_MASTER_KEY），本表與任何入口都沒有原文。
-- 附加驗證資料（AAD）是 lib/friend-ai.js 的 AAD_PREFIX＋email，密文搬到別人名下會解不開。
-- 刪除金鑰、停權都是直接刪掉整列，不留密文。編號暫用 0014，由產品 owner 統一分配。
CREATE TABLE friend_gemini_keys (
  email               TEXT PRIMARY KEY,
  ciphertext          TEXT NOT NULL,            -- base64，含 GCM 驗證標籤
  iv                  TEXT NOT NULL,            -- base64，12 bytes，每次保存都換新的
  alg                 TEXT NOT NULL CHECK (alg = 'AES-256-GCM'),
  master_key_id       TEXT NOT NULL,            -- 主金鑰的指紋（SHA-256 前 8 碼），用來發現 Pages 與 GitHub 兩邊主金鑰不一致
  consent_at          TEXT NOT NULL,
  consent_version     TEXT NOT NULL,            -- 與 lib/friend-ai.js 的 CONSENT_VERSION 不同＝同意失效
  status              TEXT NOT NULL CHECK (status IN ('active', 'paused')),
  last_success_at     TEXT,
  last_failure_reason TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
-- 人數上限由資料表自己擋，不能用併發繞過入口的前置檢查。數字和 lib/friend-ai.js 的 FRIEND_KEY_LIMIT 是同一個，要調整得兩邊一起改。
CREATE TRIGGER friend_gemini_keys_limit BEFORE INSERT ON friend_gemini_keys
WHEN (SELECT COUNT(*) FROM friend_gemini_keys) >= 10
BEGIN
  SELECT RAISE(ABORT, 'friend key limit reached');
END;
