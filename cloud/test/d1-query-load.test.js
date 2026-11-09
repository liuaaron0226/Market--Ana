import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./helpers.js";

// 與 gate.js 的個股分析查詢相同（契約 v2：每檔一筆，不帶方案）。
const ANALYSIS_SQL =
  "SELECT a.as_of, a.payload FROM stock_data_state s " +
  "JOIN stock_analysis a ON a.version = s.active_version " +
  "WHERE s.key = 'current' AND a.code = ?";

test("單次個股分析 point lookup 只讀 active state 與目標分析各一列", async () => {
  const db = fakeD1();
  await db.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-08", "https://example.test", "5".repeat(64), "2026-11-08T00:00:00Z", 1, 1).run();
  await db.prepare("INSERT INTO stock_data_state VALUES ('current', ?, NULL, ?)")
    .bind("v1", "2026-11-08T00:00:00Z").run();
  await db.prepare("INSERT INTO stock_analysis VALUES (?, ?, ?, ?)")
    .bind("v1", "1102", "2026-11-06", JSON.stringify({ schema: "stock-analysis/v2", code: "1102" })).run();

  db.resetMetrics();
  const row = await db.prepare(ANALYSIS_SQL).bind("1102").first();

  assert.equal(JSON.parse(row.payload).code, "1102");
  assert.equal(db.metrics.selectQueries, 1);
  assert.equal(db.metrics.rowsReturned, 1);
  assert.equal(db.metrics.logicalRowsRead, 2);
  assert.equal(db.metrics.fullScanQueries, 0);
});

test("0004 拒絕非契約 v2 的分析 payload", async () => {
  const db = fakeD1();
  await db.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-08", "https://example.test", "5".repeat(64), "2026-11-08T00:00:00Z", 1, 1).run();
  await assert.rejects(
    db.prepare("INSERT INTO stock_analysis VALUES (?, ?, ?, ?)").bind("v1", "1102", "2026-11-06", JSON.stringify({ code: "1102" })).run(),
  );
});
