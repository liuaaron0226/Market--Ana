-- 每季一份不可覆寫的提名清單。季度與公司決定各自去重。
CREATE TABLE IF NOT EXISTS chain_roster_rounds (
  quarter TEXT PRIMARY KEY,
  base_version TEXT NOT NULL,
  proposals TEXT NOT NULL CHECK(json_valid(proposals) AND json_type(proposals) = 'array'),
  model TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_roster_decisions (
  quarter TEXT NOT NULL REFERENCES chain_roster_rounds(quarter),
  proposal_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('approved','declined')),
  definition_version TEXT REFERENCES chain_map_definitions(version),
  decided_at TEXT NOT NULL,
  PRIMARY KEY (quarter,proposal_id),
  CHECK((decision='approved') = (definition_version IS NOT NULL))
);
