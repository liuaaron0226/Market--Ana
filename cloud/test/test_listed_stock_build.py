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
        "analysis_version": "price-structure-rea-v2",
        "code": code,
        "kind": kind,
        "market": market,
        "as_of": as_of,
        "qa": {"blocking": False, "bars": 300},
        "strategy": {
            "strategy_version": "support-pressure-strategy-v1",
            "recommendation": "觀望",
            "prices": {"entry": None, "add": None, "stop": None, "target": None},
            "reason": "資料不足時保留明確觀望狀態，不補造操作價位。",
            "evidence_strength": "有限",
            "signals": {"positive": 0, "risk": 0, "institutional_day": None, "institutional_5d": None},
        },
        "institutional": [],
        "undisclosed": {"composite_score": dict(UNDISCLOSED)},
    }


def analysis(code: str = "1102", as_of: str = "2026-11-06", market: str = "上市") -> dict:
    return {"code": code, "as_of": as_of, "payload": v2_payload(code, as_of, market)}


def analysis_with_institutional(rows: list[dict], *, day, five) -> dict:
    row = analysis()
    row["payload"]["institutional"] = rows
    row["payload"]["strategy"]["signals"]["institutional_day"] = day
    row["payload"]["strategy"]["signals"]["institutional_5d"] = five
    return row


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

    def test_payload_requires_strategy_and_institutional_fields(self) -> None:
        for missing in ("strategy", "institutional"):
            with self.subTest(missing), self.assertRaisesRegex(ValueError, missing):
                connection = memory_db()
                seed_old_version(connection)
                bad = analysis()
                del bad["payload"][missing]
                install_staging_version(connection, manifest(), [security()], [bad])

    def test_payload_rejects_null_or_malformed_new_fields(self) -> None:
        def set_strategy(value):
            return lambda payload: payload.__setitem__("strategy", value)

        def change_strategy(path: str, value):
            def mutate(payload):
                target = payload["strategy"]
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
            return mutate

        cases = (
            ("strategy-list", set_strategy([]), "strategy"),
            ("recommendation", change_strategy("recommendation", "買進"), "strategy"),
            ("price-string", change_strategy("prices.entry", "2080"), "strategy"),
            ("signal-float", change_strategy("signals.positive", 1.5), "strategy"),
            ("institutional-null", lambda p: p.__setitem__("institutional", None), "institutional"),
            ("institutional-object", lambda p: p.__setitem__("institutional", {}), "institutional"),
            ("institutional-row-missing", lambda p: p.__setitem__("institutional", [
                {"date": "2026-11-06", "foreign": 1, "trust": 2, "total": 3}
            ]), "institutional"),
            ("institutional-null-value", lambda p: p.__setitem__("institutional", [
                {"date": "2026-11-06", "foreign": None, "trust": 2, "dealer": 0, "total": 2}
            ]), "institutional"),
        )
        for label, mutate, message in cases:
            with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                connection = memory_db()
                seed_old_version(connection)
                bad = analysis()
                mutate(bad["payload"])
                install_staging_version(connection, manifest(), [security()], [bad])

    def test_null_strategy_is_a_valid_stored_state(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        row = analysis()
        row["payload"]["strategy"] = None
        install_staging_version(connection, manifest(), [security()], [row])
        stored = json.loads(connection.execute("SELECT payload FROM stock_analysis WHERE code = '1102' AND version = ?",
                                               (manifest()["version"],)).fetchone()[0])
        self.assertIsNone(stored["strategy"])

    def test_malformed_strategy_is_isolated_without_touching_core_fields(self) -> None:
        from cloud.build_stock_pack import _check_payload, _isolate_strategy
        good = analysis()["payload"]
        self.assertIsNone(_isolate_strategy("1102", good))
        self.assertIsNotNone(good["strategy"])

        bad = analysis()["payload"]
        core = json.loads(json.dumps({k: v for k, v in bad.items() if k != "strategy"}))
        bad["strategy"]["recommendation"] = "買進"
        reason = _isolate_strategy("1102", bad)
        self.assertIn("strategy", reason)
        self.assertIsNone(bad["strategy"])
        self.assertEqual({k: v for k, v in bad.items() if k != "strategy"}, core)
        _check_payload("1102", bad)

    def test_unresolved_capital_events_are_listed_in_the_build_report(self) -> None:
        # 該還原卻缺官方數字的事件：該檔照常發布（價格維持實際成交價），但建置清單要看得到是哪一檔、哪一天。
        from cloud.build_stock_pack import _unresolved_capital_failures
        payload = analysis()["payload"]
        self.assertEqual(_unresolved_capital_failures("1102", payload), [])
        payload["qa"]["unresolved_capital_events"] = [
            {"date": "2026-09-02", "type": "除權息", "reason": "缺官方參考價或現金股利"}]
        self.assertEqual(_unresolved_capital_failures("1102", payload), [
            {"code": "1102", "scope": "price_adjustment", "error": "UnresolvedCapitalEvent",
             "message": "2026-09-02 除權息 未還原：缺官方參考價或現金股利"}])

    def test_build_report_counts_which_signal_blocks_the_stocks_have(self) -> None:
        # 五塊資料（規格使用者故事 81）：某一塊算不出來不擋發布，但建置報告要看得到今天有幾檔有哪幾塊。
        from cloud.build_stock_pack import signals_report

        def row(code: str, signals: object) -> dict:
            item = analysis(code)
            item["payload"]["signals"] = signals
            return item

        rows = [row("1101", {"blocks": ["limit_up_momentum", "versus_market", "ma_alignment", "heavy_zones"]}),
                row("1102", {"blocks": ["ma_alignment"], "failed": ["heavy_zones"]}),   # 爆大量成交區算的時候出錯
                row("1103", {"blocks": []}),
                row("1104", None),       # 整塊算不出來（計算出錯時留空）
                analysis("1105")]        # 舊資料：沒有這個鍵
        # 位元組數是五塊資料壓成 JSON 的大小：{"blocks":["limit_up_momentum","versus_market","ma_alignment","heavy_zones"]}＝77、
        # {"blocks":["ma_alignment"],"failed":["heavy_zones"]}＝52、{"blocks":[]}＝13；沒有五塊資料的兩檔不計。
        self.assertEqual(signals_report(rows), {
            "analyses": 5, "without_signals": 2,
            "blocks": {"limit_up_momentum": 1, "versus_market": 1, "ma_alignment": 2, "margin_maintenance": 0, "heavy_zones": 1},
            "failed": {"limit_up_momentum": 0, "versus_market": 0, "ma_alignment": 0, "margin_maintenance": 0, "heavy_zones": 1},
            "payload_bytes": 142, "largest_payload_bytes": 77})
        nothing = {"limit_up_momentum": 0, "versus_market": 0, "ma_alignment": 0, "margin_maintenance": 0, "heavy_zones": 0}
        self.assertEqual(signals_report([]), {
            "analyses": 0, "without_signals": 0, "blocks": nothing, "failed": nothing,
            "payload_bytes": 0, "largest_payload_bytes": 0})

    def test_build_report_carries_the_daily_margin_check(self) -> None:
        # 融資維持率（估）的每日對帳：建置報告要有當天估計的全市場融資金額和官方差多少、有沒有超過門檻。
        import tempfile
        from unittest import mock

        from cloud.build_stock_pack import margin_check_report
        from twon_analysis import margin_maintenance

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "data/margin-history"
            # 起點 2025-11-03 當天 2330 有 100 張、成交均價 250（250,000 元 ÷ 1,000 股）、收 255：
            # 估計的全市場融資金額＝100 張 × 1,000 × 250 × 起點係數 0.9369126317402282 × 0.6＝14,053,689 元；官方 14,054 仟元。
            official = {"stat": "OK", "date": "20251103", "tables": [
                {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
                 "data": [["融資金額(仟元)", "1", "2", "3", "4", "14,054"]]},
                {"fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額"],
                 "data": [["2330", "台積電", "0", "0", "0", "100", "100"]]}]}
            (store / "raw").mkdir(parents=True)
            (store / "raw/margin-20251103.json").write_text(json.dumps({"data": official}, ensure_ascii=False), encoding="utf-8")
            (store / "prices").mkdir()
            (store / "prices/prices-20251103.json").write_text('{"date":"20251103","prices":{"2330":[255.0,1000,250000]}}',
                                                               encoding="utf-8")
            with mock.patch.object(margin_maintenance, "ROOT", Path(tmp)):
                self.assertEqual(margin_check_report("2025-11-03"), {
                    "status": "ok", "data_date": "2025-11-03", "gap_pct": 0.0, "threshold_pct": 8.0,
                    "estimated": 14053689, "official": 14054000, "stocks": 1, "reason": None})
                # 隔天的原件讀不了：對帳本身出錯不擋發布，報告寫明是出錯（不帶本機路徑）。
                (store / "raw/margin-20251104.json").write_text("壞掉的檔", encoding="utf-8")
                (store / "prices/prices-20251104.json").write_text('{"date":"20251104","prices":{}}', encoding="utf-8")
                broken = margin_check_report("2025-11-04")
            self.assertEqual((broken["status"], broken["reason"]), ("error", "JSONDecodeError"))
            self.assertNotIn(tmp, json.dumps(broken, ensure_ascii=False))

    def test_strategy_isolation_never_hides_core_data_defects(self) -> None:
        from cloud.build_stock_pack import _check_payload, _isolate_strategy
        payload = analysis()["payload"]
        payload["institutional"] = [{"date": "2026-11-06", "foreign": 1, "trust": 2, "dealer": 3, "total": 99}]
        with self.assertRaisesRegex(ValueError, "institutional"):
            _isolate_strategy("1102", payload)
        with self.assertRaisesRegex(ValueError, "institutional"):
            _check_payload("1102", payload)

    def test_payload_requires_explicit_nonblocking_qa(self) -> None:
        cases = (
            ("missing", lambda payload: payload.pop("qa")),
            ("empty", lambda payload: payload.__setitem__("qa", {})),
            ("null-blocking", lambda payload: payload.__setitem__("qa", {"blocking": None, "bars": 300})),
            ("missing-bars", lambda payload: payload.__setitem__("qa", {"blocking": False})),
            ("too-few-bars", lambda payload: payload.__setitem__("qa", {"blocking": False, "bars": 19})),
        )
        for label, mutate in cases:
            with self.subTest(label), self.assertRaisesRegex(ValueError, "QA"):
                connection = memory_db()
                seed_old_version(connection)
                bad = analysis()
                mutate(bad["payload"])
                install_staging_version(connection, manifest(), [security()], [bad])

    def test_unknown_or_inconsistent_dates_are_rejected(self) -> None:
        def invalid_as_of():
            row = analysis(as_of="unknown")
            item = security()
            item["latest_bar_date"] = "unknown"
            return item, row

        def compact_as_of():
            row = analysis(as_of="20261106")
            item = security()
            item["latest_bar_date"] = "20261106"
            return item, row

        def catalog_mismatch():
            item = security()
            item.update(latest_bar_date="2026-11-05", analysis_status="stale")
            return item, analysis(as_of="2026-11-06")

        def institutional_future():
            row = analysis()
            row["payload"]["institutional"] = [
                {"date": "2026-11-07", "foreign": 1, "trust": 2, "dealer": 3, "total": 6}
            ]
            return security(), row

        def institutional_unsorted():
            row = analysis()
            row["payload"]["institutional"] = [
                {"date": "2026-11-06", "foreign": 1, "trust": 2, "dealer": 3, "total": 6},
                {"date": "2026-11-05", "foreign": -1, "trust": 0, "dealer": 1, "total": 0},
            ]
            return security(), row

        for label, make_case in (
            ("unknown-as-of", invalid_as_of),
            ("compact-as-of", compact_as_of),
            ("catalog-mismatch", catalog_mismatch),
            ("institutional-future", institutional_future),
            ("institutional-unsorted", institutional_unsorted),
        ):
            with self.subTest(label), self.assertRaisesRegex(ValueError, "資料日|institutional"):
                connection = memory_db()
                seed_old_version(connection)
                item, row = make_case()
                install_staging_version(connection, manifest(), [item], [row])

    def test_institutional_totals_and_strategy_signals_must_match_rows(self) -> None:
        valid_rows = [
            {"date": "2026-10-30", "foreign": 1, "trust": 2, "dealer": 3, "total": 6},
            {"date": "2026-11-02", "foreign": 2, "trust": 3, "dealer": 4, "total": 9},
            {"date": "2026-11-03", "foreign": 3, "trust": 4, "dealer": 5, "total": 12},
            {"date": "2026-11-05", "foreign": 4, "trust": 5, "dealer": 6, "total": 15},
            {"date": "2026-11-06", "foreign": 5, "trust": 6, "dealer": 7, "total": 18},
        ]
        cases = []
        wrong_total = [dict(row) for row in valid_rows]
        wrong_total[-1]["total"] = 999
        cases.append(("total", analysis_with_institutional(wrong_total, day=999, five=1041)))
        cases.append(("institutional_day", analysis_with_institutional(valid_rows, day=999, five=60)))
        cases.append(("institutional_5d", analysis_with_institutional(valid_rows, day=18, five=999)))

        for message, bad in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                connection = memory_db()
                seed_old_version(connection)
                install_staging_version(connection, manifest(), [security()], [bad])

    def test_institutional_five_day_uses_last_five_rows_not_calendar_fill(self) -> None:
        rows = [
            {"date": "2026-10-30", "foreign": 1, "trust": 2, "dealer": 3, "total": 6},
            {"date": "2026-11-02", "foreign": 2, "trust": 3, "dealer": 4, "total": 9},
            {"date": "2026-11-03", "foreign": 3, "trust": 4, "dealer": 5, "total": 12},
            {"date": "2026-11-05", "foreign": 4, "trust": 5, "dealer": 6, "total": 15},
            {"date": "2026-11-06", "foreign": 5, "trust": 6, "dealer": 7, "total": 18},
        ]
        connection = memory_db()
        seed_old_version(connection)
        install_staging_version(
            connection, manifest(), [security()],
            [analysis_with_institutional(rows, day=18, five=60)],
        )
        payload = json.loads(connection.execute("SELECT payload FROM stock_analysis WHERE code = '1102'").fetchone()[0])
        self.assertEqual(payload["strategy"]["signals"]["institutional_5d"], 60)

    def test_less_than_five_institutional_rows_keeps_five_day_null(self) -> None:
        rows = [
            {"date": "2026-10-30", "foreign": 1, "trust": 2, "dealer": 3, "total": 6},
            {"date": "2026-11-02", "foreign": 2, "trust": 3, "dealer": 4, "total": 9},
            {"date": "2026-11-05", "foreign": 3, "trust": 4, "dealer": 5, "total": 12},
            {"date": "2026-11-06", "foreign": 0, "trust": 0, "dealer": 0, "total": 0},
        ]
        connection = memory_db()
        seed_old_version(connection)
        install_staging_version(
            connection, manifest(), [security()],
            [analysis_with_institutional(rows, day=0, five=None)],
        )
        payload = json.loads(connection.execute("SELECT payload FROM stock_analysis WHERE code = '1102'").fetchone()[0])
        self.assertEqual(payload["strategy"]["signals"]["institutional_day"], 0)
        self.assertIsNone(payload["strategy"]["signals"]["institutional_5d"])

    def test_explicit_legal_missing_values_are_preserved(self) -> None:
        connection = memory_db()
        seed_old_version(connection)
        install_staging_version(connection, manifest(), [security()], [analysis()])
        payload = json.loads(connection.execute("SELECT payload FROM stock_analysis WHERE code = '1102'").fetchone()[0])
        self.assertEqual(payload["institutional"], [])
        self.assertEqual(payload["strategy"]["evidence_strength"], "有限")
        self.assertEqual(payload["strategy"]["prices"], {"entry": None, "add": None, "stop": None, "target": None})
        self.assertIsNone(payload["strategy"]["signals"]["institutional_day"])
        self.assertIsNone(payload["strategy"]["signals"]["institutional_5d"])

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

    def test_version_name_uses_market_data_date_not_catalog_date(self) -> None:
        # 11-10 Actions：版本名沿用名錄抓取日（11-08）→ 和 D1 既有版本同名，INSERT OR IGNORE 略過，verify 失敗。
        import cloud.build_stock_pack as build

        name = build.version_name(data_date="2026-11-10", combined_sha256="237d4b8dd208" + "0" * 52,
                                  liquid=True, selected_codes=None)
        self.assertEqual(name, "liquid-20261110-237d4b8dd208-v2")
        sample = build.version_name(data_date="2026-11-10", combined_sha256="a" * 64, liquid=False,
                                    selected_codes=("2330",))
        self.assertTrue(sample.startswith("twomarket-20261110-aaaaaaaaaaaa-"), sample)

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

    def test_build_report_says_which_signal_blocks_were_built(self) -> None:
        # 台積電、環球晶、0050 都有夠長的日線與加權指數可比：三檔都有「和大盤比」與「均線排列」，沒有整塊空掉的。
        report = self.manifest["signals"]
        self.assertEqual((report["analyses"], report["without_signals"]), (3, 0))
        self.assertEqual((report["blocks"]["versus_market"], report["blocks"]["ma_alignment"]), (3, 3))
        # 漲停動能給上市、上櫃普通股（上櫃：本人 2026-11-16 看過 09 票驗證報告後開）：台積電、環球晶有，0050 是 ETF 沒有。
        self.assertEqual(report["blocks"]["limit_up_momentum"], 2)
        self.assertEqual(report["failed"], {"limit_up_momentum": 0, "versus_market": 0, "ma_alignment": 0,
                                            "margin_maintenance": 0, "heavy_zones": 0})
        self.assertIn(report["margin_check"]["status"],
                      ("ok", "over_threshold", "missing_days", "missing_prices", "stale", "no_start", "no_official"))
        self.assertGreater(report["payload_bytes"], 0)

    def test_functions_pack_has_target_date_and_valid_new_fields_per_stock(self) -> None:
        pack = json.loads((self.out / "stock-pack-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["as_of"], self.manifest["data_date"])
        self.assertEqual(pack["analysis_schema"], "stock-analysis/v2")
        self.assertEqual(sorted(pack["results"]), ["0050", "2330"])
        for code, payload in pack["results"].items():
            with self.subTest(code):
                self.assertEqual(payload["as_of"], pack["as_of"])
                self.assertIs(payload["qa"]["blocking"], False)
                self.assertIsInstance(payload["strategy"], dict)
                self.assertIsInstance(payload["institutional"], list)

    def test_seed_equals_direct_analyze_v2_bytes(self) -> None:
        from twon_analysis.analyze_v2 import analyze_v2

        compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        from twon_analysis.facts import with_news

        rows = self.rows()
        for code in ("2330", "6488"):
            # 建置版本時只多做一件事：替事實資料加上名錄的產業別與「消息」按鈕（事實區塊 D 票）；其餘逐位元組相同。
            direct = analyze_v2(code, "9999-12-31")
            industry = rows[code]["facts"]["industry"]
            self.assertTrue(industry, code)
            self.assertNotIn("news", direct["facts"]["blocks"], code)
            blocks = rows[code]["facts"]["blocks"]
            self.assertEqual(blocks.index("news") + 1, blocks.index("technical"), code)
            direct["facts"] = with_news(direct["facts"], industry=industry)
            self.assertEqual(compact(rows[code]), compact(direct), code)
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

    def test_cloud_fixture_carries_the_new_pack_contract(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "stock-analysis/v2")
        self.assertIn(payload["strategy"]["recommendation"], {"持有", "分批進場", "觀望", "減碼"})
        self.assertEqual(set(payload["strategy"]["prices"]), {"entry", "add", "stop", "target"})
        self.assertTrue(payload["institutional"])
        self.assertEqual(set(payload["institutional"][0]), {"date", "foreign", "trust", "dealer", "total"})

    def test_cloud_fixture_institutional_arithmetic_matches_strategy_signals(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        rows = payload["institutional"]
        for row in rows:
            self.assertEqual(row["total"], row["foreign"] + row["trust"] + row["dealer"])
        signals = payload["strategy"]["signals"]
        self.assertEqual(signals["institutional_day"], rows[-1]["total"])
        self.assertEqual(signals["institutional_5d"], sum(row["total"] for row in rows[-5:]))

    def test_contract_example_grouping_matches_analyze_v2(self) -> None:
        self.assert_grouping(self.CONTRACT_EXAMPLE)


if __name__ == "__main__":
    unittest.main()
