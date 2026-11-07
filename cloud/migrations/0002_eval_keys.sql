-- External Evaluation Worker 的簽章金鑰（只在 D1，不進 repo）。
CREATE TABLE eval_keys (
  name  TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
