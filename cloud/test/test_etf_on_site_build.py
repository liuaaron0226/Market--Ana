"""12 票（ETF 上站）資料端：每日建置把名單內的股票型 ETF 寫成 ETF 名錄列與個股分析列，D1 匯入跟著寫。

規格 .scratch/stock-signals/spec.md「ETF 納入」：名單內 ETF 進名錄（類別 ETF，不混進普通股）、有個股分析結果；
0050 照舊走相容入口（分析只有一筆），但也在 ETF 名錄裡讓開的人搜尋得到。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud import build_stock_pack as build
from cloud import import_d1_seed as importer

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = [ROOT / "cloud/migrations/0004_listed_stock_data.sql", ROOT / "cloud/migrations/0018_etf_securities.sql"]
EXAMPLE = json.loads((ROOT / "cloud/test/fixtures/stock-analysis-v2.example.json").read_text(encoding="utf-8"))
DATA_DATE = "2026-11-09"

UNIVERSE = {
    "rule_version": "liquid-v1",
    "data_date": DATA_DATE,
    "members": [],
    # 名單（含槓桿、債券）只供籌碼排行；網站只收 stock_etf_members。
    "etf_members": [{"code": c, "name": n, "market": "上市", "equivalent_rank": r} for c, n, r in
                    [("0050", "元大台灣50", 4), ("00631L", "元大台灣50正2", 19), ("0056", "元大高股息", 56)]],
    "stock_etf_members": [
        {"code": "0050", "name": "元大台灣50", "market": "上市", "equivalent_rank": 4, "etf_type": "股票型"},
        {"code": "00981A", "name": "主動統一台股增長", "market": "上市", "equivalent_rank": 41, "etf_type": "主動式股票型"},
        {"code": "0056", "name": "元大高股息", "market": "上市", "equivalent_rank": 56, "etf_type": "股票型"},
        {"code": "00409A", "name": "主動復華全球50", "market": "上市", "equivalent_rank": 379, "etf_type": "主動式股票型"},
        {"code": "00888", "name": "永豐台灣ESG", "market": "上櫃", "equivalent_rank": 270, "etf_type": "股票型"},
    ],
}
BARS = {"0050": (DATA_DATE, 1200), "00981A": (DATA_DATE, 120), "0056": (DATA_DATE, 1200),
        "00409A": (DATA_DATE, 3), "00888": (DATA_DATE, 900)}


def etf_payload(code: str, *, market: str = "上市", kind: str = "etf") -> dict:
    return {**EXAMPLE, "code": code, "kind": kind, "market": market, "as_of": DATA_DATE,
            "requested_as_of": "9999-12-31", "qa": {"blocking": False, "bars": 120}}


def analyzer(code: str, _as_of: str) -> dict:
    return etf_payload(code, market="上櫃" if code == "00888" else "上市")


class EtfRowsTests(unittest.TestCase):
    def test_stock_etf_members_become_directory_rows_with_rank_and_type(self) -> None:
        rows, analyses, failures = build.etf_rows(UNIVERSE, DATA_DATE, {}, analyzer, BARS.get)
        self.assertEqual(
            [(r["code"], r["name"], r["market"], r["etf_type"], r["universe_rank"], r["analysis_status"]) for r in rows],
            [("0050", "元大台灣50", "上市", "股票型", 4, "available"),
             ("00981A", "主動統一台股增長", "上市", "主動式股票型", 41, "available"),
             ("0056", "元大高股息", "上市", "股票型", 56, "available"),
             ("00888", "永豐台灣ESG", "上櫃", "股票型", 270, "available"),
             ("00409A", "主動復華全球50", "上市", "主動式股票型", 379, "insufficient")])
        self.assertTrue(all(r["universe_rule"] == "liquid-v1" and r["latest_bar_date"] == DATA_DATE for r in rows))
        # 0050 的分析由相容入口給（只有一筆）；日線不足的不給分析；槓桿型不在名錄也沒有分析。
        self.assertEqual(sorted(row["code"] for row in analyses), ["0056", "00888", "00981A"])
        self.assertEqual(failures, [])

    def test_analysis_that_is_not_an_etf_or_disagrees_on_market_is_dropped_and_reported(self) -> None:
        def wrong(code: str, as_of: str) -> dict:
            return etf_payload(code, kind="common") if code == "0056" else analyzer(code, as_of)

        rows, analyses, failures = build.etf_rows(UNIVERSE, DATA_DATE, {}, wrong, BARS.get)
        self.assertEqual({r["code"]: r["analysis_status"] for r in rows}["0056"], "unavailable")
        self.assertNotIn("0056", [row["code"] for row in analyses])
        self.assertEqual([f["code"] for f in failures], ["0056"])

    def test_universe_without_stock_etf_layer_gives_no_rows(self) -> None:
        self.assertEqual(build.etf_rows({**UNIVERSE, "stock_etf_members": []}, DATA_DATE, {}, analyzer, BARS.get), ([], [], []))
        universe = {key: value for key, value in UNIVERSE.items() if key != "stock_etf_members"}
        self.assertEqual(build.etf_rows(universe, DATA_DATE, {}, analyzer, BARS.get), ([], [], []))


def staging_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    return connection


def seed_parts() -> tuple[dict, list[dict], list[dict], list[dict]]:
    rows, etf_analyses, _ = build.etf_rows(UNIVERSE, DATA_DATE, {}, analyzer, BARS.get)
    compatibility = [{"code": "0050", "as_of": DATA_DATE, "payload": etf_payload("0050")}]
    analyses = etf_analyses + compatibility
    manifest = {"version": "liquid-20261109-test-v2", "source_date": "2026-11-08", "source_url": "https://isin.twse.com.tw",
                "source_sha256": "5" * 64, "built_at": "2026-11-09T12:00:00Z", "catalog_count": 0,
                "analysis_count": len(analyses), "compatibility_codes": ["0050"], "data_date": DATA_DATE,
                "etf_count": len(rows)}
    return manifest, [], analyses, rows


class EtfSeedTests(unittest.TestCase):
    def test_staging_install_writes_etf_directory_and_analyses(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        connection = staging_db()
        build.install_staging_version(connection, manifest, securities, analyses, etfs=rows)
        self.assertEqual(connection.execute("SELECT code, etf_type, universe_rank FROM etf_securities ORDER BY universe_rank").fetchall(),
                         [("0050", "股票型", 4), ("00981A", "主動式股票型", 41), ("0056", "股票型", 56),
                          ("00888", "股票型", 270), ("00409A", "主動式股票型", 379)])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM listed_securities").fetchone()[0], 0, "ETF 不混進普通股名錄")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_analysis").fetchone()[0], 4)

    def test_seed_check_rejects_etf_analysis_without_directory_row_and_available_row_without_analysis(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with self.assertRaises(ValueError):
            build._validate_seed(manifest, securities, analyses, [row for row in rows if row["code"] != "0056"])
        with self.assertRaises(ValueError):
            build._validate_seed({**manifest, "analysis_count": len(analyses) - 1}, securities,
                                 [row for row in analyses if row["code"] != "0056"], rows)
        with self.assertRaises(ValueError):
            build._validate_seed({**manifest, "etf_count": 1}, securities, analyses, rows)

    def test_seed_file_round_trips_through_d1_import_into_etf_table(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with tempfile.TemporaryDirectory() as tmp:
            build._write_seed_jsonl(Path(tmp) / "seed.jsonl", manifest, securities, analyses, etfs=rows)
            seed = importer.load_seed(Path(tmp))
        self.assertEqual([row["code"] for row in seed.etfs], [row["code"] for row in rows])
        self.assertIn("etf_securities", importer.STAGING_TABLES)
        self.assertIn("etf_securities", importer.OPTIONAL_TABLES, "0018 還沒套到資料庫時，匯入略過這張表，其他照常")
        connection = staging_db()
        for batch in importer.plan_batches(seed):
            connection.executescript(batch.sql)
        self.assertEqual(connection.execute("SELECT code, name, market, etf_type, latest_bar_date, available_bars, analysis_status, "
                                            "universe_rank, universe_rule FROM etf_securities WHERE code = '00888'").fetchone(),
                         ("00888", "永豐台灣ESG", "上櫃", "股票型", DATA_DATE, 900, "available", 270, "liquid-v1"))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_analysis").fetchone()[0], 4)


class EtfImportCheckTests(unittest.TestCase):
    def test_import_refuses_a_seed_whose_etf_rows_do_not_match_the_manifest(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with tempfile.TemporaryDirectory() as tmp:
            build._write_seed_jsonl(Path(tmp) / "seed.jsonl", {**manifest, "etf_count": len(rows) + 1}, securities, analyses,
                                    etfs=rows)
            seed = importer.load_seed(Path(tmp))
        with self.assertRaises(ValueError):
            importer.plan_batches(seed)

    def test_incremental_import_always_sends_etf_rows_instead_of_copying_from_the_previous_version(self) -> None:
        # 前一版匯入時正式資料庫可能還沒有 0018（ETF 表被略過），從前一版「複製」會複製到空的；ETF 名錄只有幾十列，每次整份送。
        manifest, securities, analyses, rows = seed_parts()
        with tempfile.TemporaryDirectory() as tmp:
            build._write_seed_jsonl(Path(tmp) / "base.jsonl", {**manifest, "version": "liquid-20261106-base-v2"}, securities,
                                    analyses, etfs=rows)
            (Path(tmp) / "b").mkdir()
            (Path(tmp) / "base.jsonl").replace(Path(tmp) / "b" / "seed.jsonl")
            base = importer.load_seed(Path(tmp) / "b")
            build._write_seed_jsonl(Path(tmp) / "seed.jsonl", manifest, securities, analyses, etfs=rows)
            seed = importer.load_seed(Path(tmp))
        batches, stats = importer.plan_incremental(seed, base)
        self.assertEqual((stats["copied"]["etf_securities"], stats["sent"]["etf_securities"]), (0, len(rows)))
        self.assertGreater(stats["copied"]["stock_analysis"], 0, "其他表照舊沿用前一版")
        self.assertTrue(any(batch.table == "etf_securities" and "VALUES" in batch.sql for batch in batches))
