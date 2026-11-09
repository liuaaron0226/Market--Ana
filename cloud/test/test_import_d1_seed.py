"""M1：正式 D1 分批匯入工具的契約測試（只用記憶體 SQLite 與小型 seed，不碰遠端）。

用法：PYTHONPATH=. python cloud/test/test_import_d1_seed.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud import import_d1_seed as importer


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "cloud" / "migrations").glob("000[1-4]_*.sql"))
EXAMPLE = json.loads((ROOT / "cloud" / "test" / "fixtures" / "stock-analysis-v2.example.json").read_text(encoding="utf-8"))
PROTECTED = ("users", "audit", "eval_keys", "api_hits", "d1_migrations")
VERSION = "liquid-20261108-test-v2"


def _payload(code: str, market: str, kind: str = "common", name: str = "測試") -> dict:
    payload = json.loads(json.dumps(EXAMPLE))
    payload.update({"code": code, "market": market, "kind": kind, "name": name, "as_of": "2026-11-06"})
    # 刻意放入單引號與反斜線：SQL 字串必須正確跳脫，不能截斷或注入。
    payload["note"] = "it's a \\ test ' quote"
    return payload


def write_seed(directory: Path, *, analyses: int = 3, outside: int = 4) -> Path:
    manifest = {
        "version": VERSION, "source_date": "2026-11-08", "source_url": "https://example.test/isin",
        "source_sha256": "b" * 64, "built_at": "2026-11-08T18:55:35Z", "catalog_count": analyses - 1,
        "analysis_count": analyses, "universe_rule": "liquid-v1", "compatibility_codes": ["0050"],
    }
    lines = [{"type": "version", "data": manifest}]
    codes = [f"{1101 + i}" for i in range(analyses - 1)]
    for index, code in enumerate(codes):
        lines.append({"type": "listed_security", "data": {
            "code": code, "name": f"名稱{code}", "market": "上市" if index % 2 == 0 else "上櫃",
            "security_type": "普通股", "isin": f"TW000{code}004", "listed_date": "1962/02/09",
            "industry": "水泥工業", "cfi_code": "ESVUFR", "latest_bar_date": "2026-11-06",
            "available_bars": 2638, "analysis_status": "available", "universe_rank": index + 1,
            "universe_rule": "liquid-v1"}})
    for index, code in enumerate(codes):
        market = "上市" if index % 2 == 0 else "上櫃"
        lines.append({"type": "stock_analysis", "data": {"code": code, "as_of": "2026-11-06",
                                                         "payload": _payload(code, market)}})
    lines.append({"type": "stock_analysis", "data": {"code": "0050", "as_of": "2026-11-06",
                                                     "payload": _payload("0050", "上市", kind="etf")}})
    for index in range(outside):
        lines.append({"type": "universe_outside", "data": {
            "code": f"{2101 + index}", "name": f"外{index}", "market": "上市",
            "universe_rank": 500 + index, "reason": "below_rank", "universe_rule": "liquid-v1"}})
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "seed.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n", encoding="utf-8")
    return directory


def fresh_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute("INSERT INTO users VALUES ('a@example.test','甲','approved','t',NULL,NULL)")
    connection.commit()
    return connection


def apply_batches(connection: sqlite3.Connection, batches: list) -> None:
    for batch in batches:
        connection.executescript(batch.sql)


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.seed_dir = write_seed(Path(self.tmp.name) / "seed")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def plan(self, **kwargs) -> list:
        return importer.plan_batches(importer.load_seed(self.seed_dir), **kwargs)

    def test_every_batch_is_under_safety_margin(self) -> None:
        batches = self.plan()
        self.assertTrue(batches)
        for batch in batches:
            self.assertLess(len(batch.sql.encode("utf-8")), importer.MAX_BATCH_BYTES)
        self.assertEqual(importer.MAX_BATCH_BYTES, 90_000)

    def test_large_payload_gets_its_own_batch(self) -> None:
        batches = self.plan(max_batch_bytes=30_000)
        analysis_batches = [b for b in batches if b.table == "stock_analysis"]
        self.assertTrue(all(b.row_count == 1 for b in analysis_batches))

    def test_oversized_single_row_is_rejected_not_truncated(self) -> None:
        with self.assertRaisesRegex(ValueError, "單列"):
            self.plan(max_batch_bytes=5_000)

    def test_sql_never_touches_protected_tables_or_drops(self) -> None:
        for batch in self.plan():
            lowered = batch.sql.lower()
            for table in PROTECTED:
                self.assertIsNone(re.search(rf"\b{table}\b", lowered), (table, batch.name))
            self.assertNotIn("drop ", lowered)
            self.assertNotIn("stock_data_state", lowered, "staging 匯入不得切換 active")
            self.assertNotIn("delete", lowered)

    def test_batch_names_are_stable_for_resume(self) -> None:
        first = [(b.name, b.sha256) for b in self.plan()]
        second = [(b.name, b.sha256) for b in self.plan()]
        self.assertEqual(first, second)

    def test_counts_rows_written(self) -> None:
        batches = self.plan()
        self.assertEqual(sum(b.row_count for b in batches), 1 + 2 + 3 + 4)

    def test_rejects_unknown_record_type_and_bad_counts(self) -> None:
        seed = importer.load_seed(self.seed_dir)
        seed.manifest["analysis_count"] = 99
        with self.assertRaisesRegex(ValueError, "analysis_count"):
            importer.plan_batches(seed)


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.seed_dir = write_seed(Path(self.tmp.name) / "seed")
        self.seed = importer.load_seed(self.seed_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_import_matches_seed_byte_for_byte_and_leaves_state_untouched(self) -> None:
        connection = fresh_db()
        apply_batches(connection, importer.plan_batches(self.seed))
        stored = dict(connection.execute(
            "SELECT code, payload FROM stock_analysis WHERE version = ?", (VERSION,)).fetchall())
        for row in self.seed.analyses:
            self.assertEqual(stored[row["code"]], importer.compact_json(row["payload"]))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM listed_securities").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM universe_outside").fetchone()[0], 4)
        self.assertIsNone(connection.execute("SELECT * FROM stock_data_state").fetchone())
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_rerunning_all_batches_is_idempotent(self) -> None:
        connection = fresh_db()
        batches = importer.plan_batches(self.seed)
        apply_batches(connection, batches)
        apply_batches(connection, batches)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_analysis").fetchone()[0], 3)

    def test_verify_sql_reports_counts(self) -> None:
        connection = fresh_db()
        apply_batches(connection, importer.plan_batches(self.seed))
        row = connection.execute(importer.verify_sql(VERSION)).fetchone()
        self.assertEqual(row, (1, 2, 3, 4))

    def test_activate_then_rollback_swaps_versions(self) -> None:
        connection = fresh_db()
        connection.execute(
            "INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, "
            "catalog_count, analysis_count) VALUES ('old-v1','2026-11-01','u',?, 't', 0, 0)", ("a" * 64,))
        connection.execute("INSERT INTO stock_data_state VALUES ('current','old-v1',NULL,'t')")
        apply_batches(connection, importer.plan_batches(self.seed))
        connection.executescript(importer.activate_sql(self.seed, "2026-11-09T00:00:00Z"))
        self.assertEqual(connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state").fetchone(), (VERSION, "old-v1"))
        connection.executescript(importer.rollback_sql("2026-11-09T00:01:00Z"))
        self.assertEqual(connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state").fetchone(), ("old-v1", VERSION))

    def test_activate_on_empty_state_inserts_current(self) -> None:
        connection = fresh_db()
        apply_batches(connection, importer.plan_batches(self.seed))
        connection.executescript(importer.activate_sql(self.seed, "2026-11-09T00:00:00Z"))
        self.assertEqual(connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state").fetchone(), (VERSION, None))

    def test_activate_refuses_when_staging_incomplete(self) -> None:
        connection = fresh_db()
        batches = importer.plan_batches(self.seed)
        apply_batches(connection, batches[:-1])
        with self.assertRaises(sqlite3.IntegrityError):
            connection.executescript(importer.activate_sql(self.seed, "2026-11-09T00:00:00Z"))
        self.assertIsNone(connection.execute("SELECT * FROM stock_data_state").fetchone())

    def test_activate_sql_touches_only_state_table(self) -> None:
        lowered = importer.activate_sql(self.seed, "t").lower() + importer.rollback_sql("t").lower()
        for table in PROTECTED:
            self.assertIsNone(re.search(rf"\b{table}\b", lowered), table)
        self.assertNotIn("drop ", lowered)
        self.assertNotIn("delete", lowered)


class ProgressTest(unittest.TestCase):
    def test_resume_skips_batches_already_done_with_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = importer.load_seed(write_seed(Path(tmp) / "seed"))
            batches = importer.plan_batches(seed)
            progress = importer.Progress(Path(tmp) / "progress.json", target="local")
            progress.mark_done(batches[0])
            progress.mark_done(batches[1])
            reloaded = importer.Progress(Path(tmp) / "progress.json", target="local")
            pending = [b.name for b in batches if not reloaded.is_done(b)]
            self.assertEqual(pending, [b.name for b in batches[2:]])

    def test_changed_batch_content_is_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = importer.load_seed(write_seed(Path(tmp) / "seed"))
            batch = importer.plan_batches(seed)[0]
            progress = importer.Progress(Path(tmp) / "progress.json", target="local")
            progress.mark_done(batch)
            changed = importer.Batch(batch.name, batch.table, batch.row_count, batch.sql + "\n-- changed\n")
            self.assertFalse(progress.is_done(changed))

    def test_progress_is_per_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = importer.load_seed(write_seed(Path(tmp) / "seed"))
            batch = importer.plan_batches(seed)[0]
            importer.Progress(Path(tmp) / "p.json", target="local").mark_done(batch)
            self.assertFalse(importer.Progress(Path(tmp) / "p.json", target="remote").is_done(batch))


def write_variant(directory: Path, *, version: str, change_code: str | None = None, drop_outside: int = 0,
                  extra_outside: int = 0) -> Path:
    """以 write_seed 為底產生新版本：可改一檔分析、刪減或增加名單外列，其餘逐列相同。"""
    base_dir = write_seed(directory)
    lines = [json.loads(line) for line in (base_dir / "seed.jsonl").read_text(encoding="utf-8").splitlines() if line]
    lines[0]["data"]["version"] = version
    out = []
    outside_seen = 0
    for line in lines:
        if line["type"] == "stock_analysis" and line["data"]["code"] == change_code:
            line["data"]["payload"]["note"] = "changed today"
        if line["type"] == "universe_outside":
            outside_seen += 1
            if outside_seen <= drop_outside:
                continue
        out.append(line)
    for index in range(extra_outside):
        out.append({"type": "universe_outside", "data": {
            "code": f"{3101 + index}", "name": f"新外{index}", "market": "上櫃", "universe_rank": 700 + index,
            "reason": "below_rank", "universe_rule": "liquid-v1"}})
    (base_dir / "seed.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n",
                                         encoding="utf-8")
    return base_dir


class IncrementalTest(unittest.TestCase):
    """U2：每日增量匯入。未變列從 base 版本沿用，變動列送原文；新版本內容必須和完整匯入逐列相同。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.base = importer.load_seed(write_seed(root / "base"))
        self.new = importer.load_seed(write_variant(root / "new", version="liquid-20261109-test-v2",
                                                    change_code="1102", drop_outside=1, extra_outside=2))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _db_with_active_base(self) -> sqlite3.Connection:
        connection = fresh_db()
        apply_batches(connection, importer.plan_batches(self.base))
        connection.executescript(importer.activate_sql(self.base, "2026-11-08T00:00:00Z"))
        return connection

    def _snapshot(self, connection: sqlite3.Connection, version: str) -> dict:
        out = {}
        for table, columns in importer._TABLE_COLUMNS.items():
            cols = ", ".join(columns[1:])
            out[table] = sorted(connection.execute(f"SELECT {cols} FROM {table} WHERE version = ?", (version,)).fetchall())
        return out

    def test_incremental_result_equals_full_import_row_for_row(self) -> None:
        incremental = self._db_with_active_base()
        batches, stats = importer.plan_incremental(self.new, self.base)
        apply_batches(incremental, batches)
        full = self._db_with_active_base()
        apply_batches(full, importer.plan_batches(self.new))
        self.assertEqual(self._snapshot(incremental, self.new.version), self._snapshot(full, self.new.version))
        self.assertEqual(incremental.execute(importer.verify_sql(self.new.version)).fetchone(), (1, 2, 3, 5))
        # activate 的完整性檢查照舊通過；base 版本不受影響。
        incremental.executescript(importer.activate_sql(self.new, "2026-11-09T00:00:00Z"))
        self.assertEqual(incremental.execute("SELECT active_version, previous_version FROM stock_data_state").fetchone(),
                         (self.new.version, self.base.version))
        self.assertEqual(incremental.execute(importer.verify_sql(self.base.version)).fetchone(), (1, 2, 3, 4))

    def test_only_changed_rows_are_sent_as_payload(self) -> None:
        _, stats = importer.plan_incremental(self.new, self.base)
        self.assertEqual(stats["sent"]["stock_analysis"], 1)       # 只有 1102 變了
        self.assertEqual(stats["copied"]["stock_analysis"], 2)
        self.assertEqual(stats["sent"]["listed_securities"], 0)
        self.assertEqual(stats["copied"]["universe_outside"], 3)   # 原 4 列刪 1，新增 2 列送原文
        self.assertEqual(stats["sent"]["universe_outside"], 2)
        full_bytes = sum(len(b.sql.encode()) for b in importer.plan_batches(self.new))
        self.assertLess(stats["payload_bytes_sent"], full_bytes)

    def test_incremental_sql_never_touches_protected_tables_or_drops(self) -> None:
        batches, _ = importer.plan_incremental(self.new, self.base)
        for batch in batches:
            lowered = batch.sql.lower()
            for table in PROTECTED:
                self.assertIsNone(re.search(rf"\b{table}\b", lowered), (table, batch.name))
            self.assertNotIn("drop ", lowered)
            self.assertNotIn("delete", lowered)
            self.assertNotIn("stock_data_state", lowered)
            self.assertLess(len(batch.sql.encode()), importer.MAX_BATCH_BYTES)

    def test_incremental_is_rerunnable_and_resume_safe(self) -> None:
        connection = self._db_with_active_base()
        batches, _ = importer.plan_incremental(self.new, self.base)
        apply_batches(connection, batches)
        apply_batches(connection, batches)
        self.assertEqual(connection.execute(importer.verify_sql(self.new.version)).fetchone(), (1, 2, 3, 5))

    def test_falls_back_to_full_when_active_is_not_base(self) -> None:
        self.assertEqual(importer.choose_base(self.base.version, self.base)[0], self.base)
        chosen, reason = importer.choose_base("something-else", self.base)
        self.assertIsNone(chosen)
        self.assertIn("not-base", reason)
        self.assertIsNone(importer.choose_base(None, None)[0])

    def test_same_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "同一個版本"):
            importer.plan_incremental(self.base, self.base)

    def test_copy_forward_from_missing_base_rows_leaves_activate_blocked(self) -> None:
        """遠端 base 其實不完整（例如被手動刪過）：沿用列補不齊，activate 必須擋下而不是切到半套。"""
        connection = fresh_db()
        base_batches = importer.plan_batches(self.base)
        apply_batches(connection, base_batches[:-1])  # 名單外列沒寫完
        batches, _ = importer.plan_incremental(self.new, self.base)
        apply_batches(connection, batches)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.executescript(importer.activate_sql(self.new, "2026-11-09T00:00:00Z"))


class GroupingTest(unittest.TestCase):
    def test_grouping_keeps_statement_order_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = importer.load_seed(write_seed(Path(tmp) / "seed", analyses=6, outside=10))
            statements = importer.plan_batches(seed, max_batch_bytes=20_000)
            grouped = importer.group_batches(statements, group_bytes=60_000)
            self.assertLess(len(grouped), len(statements))
            self.assertEqual("".join(g.sql for g in grouped), "".join(s.sql for s in statements))
            self.assertTrue(grouped[0].sql.startswith("INSERT OR IGNORE INTO stock_data_versions"))
            self.assertEqual(sum(g.row_count for g in grouped), sum(s.row_count for s in statements))
            connection = fresh_db()
            apply_batches(connection, grouped)
            self.assertEqual(connection.execute(importer.verify_sql(VERSION)).fetchone(), (1, 5, 6, 10))

    def test_zero_group_bytes_keeps_m1_names_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = importer.load_seed(write_seed(Path(tmp) / "seed"))
            statements = importer.plan_batches(seed)
            self.assertEqual([(b.name, b.sha256) for b in importer.group_batches(statements, 0)],
                             [(b.name, b.sha256) for b in statements])


class CliSafetyTest(unittest.TestCase):
    def test_remote_without_confirm_is_dry_run(self) -> None:
        args = importer.parse_args(["import", "--seed-dir", "x", "--remote"])
        self.assertFalse(importer.will_execute(args))

    def test_remote_with_confirm_executes(self) -> None:
        args = importer.parse_args(["import", "--seed-dir", "x", "--remote", "--confirm"])
        self.assertTrue(importer.will_execute(args))

    def test_local_requires_flag_to_execute(self) -> None:
        self.assertFalse(importer.will_execute(importer.parse_args(["import", "--seed-dir", "x"])))
        self.assertTrue(importer.will_execute(importer.parse_args(["import", "--seed-dir", "x", "--local"])))

    def test_local_and_remote_are_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            importer.parse_args(["import", "--seed-dir", "x", "--local", "--remote"])


if __name__ == "__main__":
    unittest.main()
