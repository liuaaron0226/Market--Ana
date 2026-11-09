from __future__ import annotations

import json
import re
import sqlite3
import unittest
from pathlib import Path

from cloud.build_stock_pack import (
    analysis_status,
    install_staging_version,
    rollback_active_version,
)
from twon_analysis.analyze_v2 import MIN_ANALYSIS_BARS, UNDISCLOSED


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "cloud" / "migrations" / "0004_listed_stock_data.sql"
EXAMPLE = ROOT / "cloud" / "test" / "fixtures" / "stock-analysis-v2.example.json"
# 契約 v2 §1／§9：每檔一筆、不輸出自創計分與方案；字串不得出現原站命名。
RETIRED_KEYS = {"preset", "available_presets", "score", "score_complete", "score_note", "dimensions", "coverage",
                "strategies", "strategy_counts", "risk_flags", "trend", "formula", "params_hash", "sources"}
REFERENCE_NAMES = ("2TON", "漲停寶寶", "甜甜價", "甜價", "私藏", "CP值", "抄底", "FIT-V1")


def memory_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    return connection


def seed_old_version(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("old-v1", "2026-11-01", "https://example.test/old", "a" * 64, "2026-11-01T00:00:00Z", 0, 0),
    )
    connection.execute(
        "INSERT INTO stock_data_state VALUES ('current', 'old-v1', NULL, '2026-11-01T00:00:00Z')"
    )
    connection.commit()


def manifest(analysis_count: int = 1) -> dict:
    return {
        "version": "listed-v2",
        "source_date": "2026-11-08",
        "source_url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        "source_sha256": "5" * 64,
        "built_at": "2026-11-08T12:00:00Z",
        "catalog_count": 1,
        "analysis_count": analysis_count,
        "analysis_schema": "stock-analysis/v2",
    }


def security(code: str = "1102", market: str = "上市") -> dict:
    return {
        "code": code,
        "name": "亞泥",
        "market": market,
        "security_type": "普通股",
        "isin": f"TW000{code}002",
        "listed_date": "1962/06/08",
        "industry": "水泥工業",
        "cfi_code": "ESVUFR",
        "latest_bar_date": "2026-11-06",
        "available_bars": 2641,
        "analysis_status": "available",
    }


def v2_payload(code: str = "1102", as_of: str = "2026-11-06", market: str = "上市", kind: str = "common") -> dict:
    return {
        "schema": "stock-analysis/v2",
        "analysis_version": "price-structure-rea-v1",
        "code": code,
        "kind": kind,
        "market": market,
        "as_of": as_of,
        "qa": {"blocking": False, "bars": 300},
        "undisclosed": {"composite_score": dict(UNDISCLOSED)},
    }


def analysis(code: str = "1102", as_of: str = "2026-11-06", market: str = "上市") -> dict:
    return {"code": code, "as_of": as_of, "payload": v2_payload(code, as_of, market)}


class AnalysisStatusTests(unittest.TestCase):
    def test_status_uses_existing_analysis_minimum_and_current_data_date(self) -> None:
        self.assertEqual(MIN_ANALYSIS_BARS, 20)
        self.assertEqual(analysis_status(20, "2026-11-06", False, "2026-11-06"), "available")
        self.assertEqual(analysis_status(19, "2026-11-06", False, "2026-11-06"), "insufficient")
        self.assertEqual(analysis_status(100, "2026-11-04", False, "2026-11-06"), "stale")
        self.assertEqual(analysis_status(100, "2026-11-06", True, "2026-11-06"), "unavailable")
        self.assertEqual(analysis_status(0, None, False, "2026-11-06"), "unavailable")


class SchemaV2Tests(unittest.TestCase):
    def test_stock_analysis_has_one_row_per_code_without_preset(self) -> None:
        connection = memory_db()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(stock_analysis)")]
        self.assertEqual(columns, ["version", "code", "as_of", "payload"])
        primary = [row[1] for row in sorted(connection.execute("PRAGMA table_info(stock_analysis)"), key=lambda r: r[5]) if row[5]]
        self.assertEqual(primary, ["version", "code"])

    def test_schema_rejects_payload_that_is_not_contract_v2(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)",
                ("old-v1", "1102", "2026-11-06", json.dumps({"code": "1102", "preset": {"id": "X"}})),
            )


class VersionActivationTests(unittest.TestCase):
    def test_incomplete_staging_never_changes_active_version(self) -> None:
        connection = memory_db()
        seed_old_version(connection)

        with self.assertRaisesRegex(ValueError, "analysis_count"):
            install_staging_version(connection, manifest(2), [security()], [analysis()])

        state = connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state WHERE key = 'current'"
        ).fetchone()
        self.assertEqual(state, ("old-v1", None))
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM stock_data_versions WHERE version = 'listed-v2'").fetchone()[0],
            0,
        )

    def test_complete_staging_switches_atomically_and_can_roll_back(self) -> None:
        connection = memory_db()
        seed_old_version(connection)

        install_staging_version(connection, manifest(), [security()], [analysis()])
        self.assertEqual(
            connection.execute(
                "SELECT active_version, previous_version FROM stock_data_state WHERE key = 'current'"
            ).fetchone(),
            ("listed-v2", "old-v1"),
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM stock_analysis WHERE version = 'listed-v2'").fetchone()[0],
            1,
        )

        rollback_active_version(connection, "2026-11-08T12:05:00Z")
        self.assertEqual(
            connection.execute(
                "SELECT active_version, previous_version FROM stock_data_state WHERE key = 'current'"
            ).fetchone(),
            ("old-v1", "listed-v2"),
        )

    def test_payload_must_belong_to_its_code(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        bad = analysis()
        bad["payload"]["code"] = "2330"

        with self.assertRaisesRegex(ValueError, "payload"):
            install_staging_version(connection, manifest(), [security()], [bad])

        self.assertEqual(
            connection.execute("SELECT active_version FROM stock_data_state WHERE key = 'current'").fetchone()[0],
            "old-v1",
        )

    def test_payload_must_be_contract_v2_without_retired_fields(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        for mutate in (
            lambda p: p.update({"schema": "stock-analysis/v1"}),
            lambda p: p.update({"preset": {"id": "MOMENTUM-V1"}}),
            lambda p: p.update({"score": 61}),
            lambda p: p.update({"strategies": []}),
        ):
            bad = analysis()
            mutate(bad["payload"])
            with self.assertRaisesRegex(ValueError, "契約"):
                install_staging_version(connection, manifest(), [security()], [bad])

    def test_duplicate_analysis_row_for_one_code_is_rejected(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        with self.assertRaisesRegex(ValueError, "重複"):
            install_staging_version(connection, manifest(2), [security()], [analysis(), analysis()])

    def test_payload_market_must_match_catalog_market(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        with self.assertRaisesRegex(ValueError, "市場"):
            install_staging_version(connection, manifest(), [security()], [analysis(market="上櫃")])

    def test_stale_stock_keeps_one_analysis_at_its_last_successful_date(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        stale = security()
        stale["latest_bar_date"] = "2026-11-04"
        stale["analysis_status"] = "stale"

        install_staging_version(connection, manifest(), [stale], [analysis(as_of="2026-11-04")])

        self.assertEqual(
            connection.execute("SELECT analysis_status FROM listed_securities WHERE code = '1102'").fetchone()[0],
            "stale",
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM stock_analysis WHERE code = '1102' AND as_of = '2026-11-04'").fetchone()[0],
            1,
        )

    def test_insufficient_stock_has_no_analysis_row(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        short = security()
        short["analysis_status"] = "insufficient"
        with self.assertRaisesRegex(ValueError, "不屬於可分析"):
            install_staging_version(connection, manifest(), [short], [analysis()])

    def test_declared_etf_compatibility_row_does_not_enter_listed_catalog(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        etf = {"code": "0050", "as_of": "2026-11-06", "payload": v2_payload("0050", kind="etf")}
        version = manifest(2)
        version["compatibility_codes"] = ["0050"]

        install_staging_version(connection, version, [security()], [analysis(), etf])

        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM listed_securities WHERE version = 'listed-v2'").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM stock_analysis WHERE version = 'listed-v2' AND code = '0050'").fetchone()[0],
            1,
        )


class TwoMarketTests(unittest.TestCase):
    def test_otc_common_stock_can_be_installed_with_its_analysis(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        otc = security("6488", "上櫃")
        otc.update({"name": "環球晶", "available_bars": 250})

        install_staging_version(connection, manifest(), [otc], [analysis("6488", market="上櫃")])

        self.assertEqual(
            connection.execute("SELECT market FROM listed_securities WHERE code = '6488'").fetchone()[0],
            "上櫃",
        )

    def test_unknown_market_is_still_rejected_by_schema(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        bad = security()
        bad["market"] = "興櫃"
        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            install_staging_version(connection, manifest(), [bad], [analysis(market="興櫃")])

    def test_catalog_population_merges_listed_and_otc_without_overlap(self) -> None:
        from cloud.build_stock_pack import catalog_securities

        items = catalog_securities()
        markets: dict[str, int] = {}
        for item in items:
            markets[item.market] = markets.get(item.market, 0) + 1
        self.assertEqual(markets, {"上市": 1058, "上櫃": 897})
        codes = [item.code for item in items]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(next(item for item in items if item.code == "6488").market, "上櫃")

    def test_history_state_covers_otc_codes_from_otc_db(self) -> None:
        # T1（11-09）：行情庫每日增量後最新日會前進；改讀凍結在 2026-11-06 的副本，不隨正式庫漂移。
        import tempfile
        from unittest.mock import patch

        import cloud.build_stock_pack as build
        import frozen_history

        with tempfile.TemporaryDirectory() as tmp:
            stock = frozen_history.frozen_copy(frozen_history.STOCK_HISTORY_DB, Path(tmp) / "s.sqlite", "2026-11-06")
            otc = frozen_history.frozen_copy(frozen_history.OTC_HISTORY_DB, Path(tmp) / "o.sqlite", "2026-11-06")
            with patch.object(build, "HISTORY_DB", stock), patch.object(build, "OTC_HISTORY_DB", otc):
                data_date, suspended = build._history_state()
        self.assertEqual(data_date, "2026-11-06")
        self.assertIn("6488", suspended)
        self.assertIn("1102", suspended)

    def test_build_refuses_mismatched_market_data_dates(self) -> None:
        import tempfile
        from unittest.mock import patch

        import cloud.build_stock_pack as build

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(build, "_otc_data_date", return_value="2026-11-05"):
            with self.assertRaisesRegex(ValueError, "不一致"):
                build.build_version(Path(tmp) / "out", selected_codes=("1102",), analyzer=lambda *a, **k: {})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_analysis_status_signature_stays_compatible(self) -> None:
        # live_server 以位置參數呼叫；新增市場支援不得改名或改變既有參數。
        self.assertEqual(analysis_status(250, "2026-11-06", False, "2026-11-06"), "available")


class LiquidUniverseBuildTests(unittest.TestCase):
    """V2：本人「整個網站都只做大量股」；0004 只放大量股母體，名錄附名次與規則版本。"""

    def test_listed_securities_has_rank_and_rule_columns(self) -> None:
        connection = memory_db()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(listed_securities)")]
        self.assertIn("universe_rank", columns)
        self.assertIn("universe_rule", columns)
        version_columns = [row[1] for row in connection.execute("PRAGMA table_info(stock_data_versions)")]
        self.assertIn("universe_rule", version_columns)

    def test_rank_is_recorded_for_member(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        member = security()
        member.update(universe_rank=319, universe_rule="liquid-v1")
        version = manifest()
        version["universe_rule"] = "liquid-v1"
        install_staging_version(connection, version, [member], [analysis()])
        self.assertEqual(
            connection.execute("SELECT universe_rank, universe_rule FROM listed_securities WHERE code = '1102'").fetchone(),
            (319, "liquid-v1"),
        )
        self.assertEqual(
            connection.execute("SELECT universe_rule FROM stock_data_versions WHERE version = 'listed-v2'").fetchone()[0],
            "liquid-v1",
        )

    def test_outside_rows_record_rank_or_reason(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        version = manifest()
        version["universe_rule"] = "liquid-v1"
        outside = [{"code": "1101", "name": "台泥", "market": "上市", "universe_rank": 512, "reason": "below_rank",
                    "universe_rule": "liquid-v1"},
                   {"code": "5906", "name": "台南-KY", "market": "上市", "universe_rank": None,
                    "reason": "suspended_on_data_date", "universe_rule": "liquid-v1"}]
        install_staging_version(connection, version, [security()], [analysis()], outside)
        self.assertEqual(
            connection.execute("SELECT code, universe_rank, reason FROM universe_outside ORDER BY code").fetchall(),
            [("1101", 512, "below_rank"), ("5906", None, "suspended_on_data_date")],
        )

    def test_outside_rows_from_universe(self) -> None:
        from cloud.build_stock_pack import outside_rows

        class Item:
            def __init__(self, code: str, market: str = "上市") -> None:
                self.code, self.name, self.market = code, f"名{code}", market

        universe = {"rule_version": "liquid-v1", "members": [{"code": "2330", "rank": 1}],
                    "excluded": {"5906": "suspended_on_data_date"}}
        ranked = [{"code": "2330", "rank": 1}, {"code": "1101", "rank": 512}]
        rows = outside_rows([Item("2330"), Item("1101"), Item("5906"), Item("9999")], universe, ranked)
        self.assertEqual([(r["code"], r["universe_rank"], r["reason"]) for r in rows],
                         [("1101", 512, "below_rank"), ("5906", None, "suspended_on_data_date"),
                          ("9999", None, "no_trading_data")])

    def test_filter_keeps_only_universe_members(self) -> None:
        from cloud.build_stock_pack import filter_to_universe

        class Item:
            def __init__(self, code: str) -> None:
                self.code = code

        universe = {"rule_version": "liquid-v1", "members": [{"code": "2330", "rank": 1}, {"code": "1102", "rank": 319}],
                    "core_holdings": ["0050"], "etf_members": [{"code": "00878", "equivalent_rank": 98}]}
        items, ranks = filter_to_universe([Item("2330"), Item("1101"), Item("1102")], universe)
        self.assertEqual([item.code for item in items], ["2330", "1102"])
        self.assertEqual(ranks, {"2330": 1, "1102": 319})


class SampleBuildV2Tests(unittest.TestCase):
    """真資料小樣本：建置器預設呼叫 analyze_v2，每檔一筆，seed 與直接分析逐位元一致。"""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        import cloud.build_stock_pack as build

        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "sample"
        cls.manifest = build.build_version(cls.out, selected_codes=("2330", "6488"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def rows(self) -> dict[str, dict]:
        connection = sqlite3.connect(f"file:{(self.out / 'staging.sqlite').as_posix()}?mode=ro", uri=True)
        try:
            return {code: json.loads(payload) for code, payload in connection.execute("SELECT code, payload FROM stock_analysis")}
        finally:
            connection.close()

    def test_one_analysis_per_code_plus_etf_compatibility(self) -> None:
        self.assertEqual(sorted(self.rows()), ["0050", "2330", "6488"])
        self.assertEqual(self.manifest["analysis_count"], 3)
        self.assertEqual(self.manifest["analysis_schema"], "stock-analysis/v2")
        self.assertNotIn("presets", self.manifest)

    def test_seed_equals_direct_analyze_v2_bytes(self) -> None:
        from twon_analysis.analyze_v2 import analyze_v2

        compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        rows = self.rows()
        for code in ("2330", "6488"):
            self.assertEqual(compact(rows[code]), compact(analyze_v2(code, "9999-12-31")), code)
        self.assertEqual(rows["6488"]["market"], "上櫃")
        self.assertEqual(rows["2330"]["market"], "上市")

    def test_payloads_carry_no_retired_fields_or_reference_names(self) -> None:
        for code, payload in self.rows().items():
            self.assertFalse(RETIRED_KEYS & payload.keys(), code)
            self.assertEqual(payload["undisclosed"]["composite_score"], UNDISCLOSED, code)
            text = json.dumps(payload, ensure_ascii=False)
            for name in REFERENCE_NAMES:
                self.assertNotIn(name, text, f"{code}:{name}")
            self.assertIsNone(re.search(r'"[TCVIF]0[1-6]"', text), code)


class ContractExampleGroupingTests(unittest.TestCase):
    """雲端 fixture 與契約例檔的條件分組，必須和 analyze_v2 的規則表一致（V05 第三輪改為近似）。"""

    CONTRACT_EXAMPLE = ROOT / ".scratch" / "chart-screening-workbench" / "stock-analysis-contract-v2.example.json"

    def assert_grouping(self, path: Path) -> None:
        from twon_analysis.analyze_v2 import APPROXIMATE_RULES, CONFIRMED_RULES

        conditions = json.loads(path.read_text(encoding="utf-8"))["conditions"]
        self.assertEqual(set(conditions["confirmed"]), set(CONFIRMED_RULES), path.name)
        self.assertEqual(set(conditions["approximate"]), set(APPROXIMATE_RULES), path.name)
        for group, rules in (("confirmed", CONFIRMED_RULES), ("approximate", APPROXIMATE_RULES)):
            for key, (_, _, _, rule, verified) in rules.items():
                self.assertEqual(conditions[group][key]["rule"], rule, f"{path.name}:{key}")
                self.assertEqual(conditions[group][key]["verified"], verified, f"{path.name}:{key}")
        self.assertEqual(conditions["pending"]["count"], 24 - len(CONFIRMED_RULES) - len(APPROXIMATE_RULES))

    def test_cloud_fixture_grouping_matches_analyze_v2(self) -> None:
        self.assert_grouping(EXAMPLE)

    def test_contract_example_grouping_matches_analyze_v2(self) -> None:
        self.assert_grouping(self.CONTRACT_EXAMPLE)


if __name__ == "__main__":
    unittest.main()
