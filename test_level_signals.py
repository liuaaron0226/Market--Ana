import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from unittest.mock import patch
from pathlib import Path

from level_signals import LevelStateMergeError, OriginalStore, build_international_features, build_values, load_international_sources, merge_level_state, parse_cbc_usdtwd, parse_eodhd_sp500, parse_ofr_fsi_csv, parse_sox_xlsx, parse_vix_csv, trading_day_status, validate_sox_years, write_daily_sql


def sox_xlsx(rows):
    strings = ["Trade Date", "Index Value", "Net Change", "High", "Low"]
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    body = ['<row r="1">' + "".join(f'<c r="{column}1" t="s"><v>{index}</v></c>' for index, column in enumerate("ABCDE")) + "</row>"]
    for row_number, (observed, value) in enumerate(rows, start=2):
        serial = (date.fromisoformat(observed) - date(1899, 12, 30)).days
        body.append(f'<row r="{row_number}"><c r="A{row_number}" s="1"><v>{serial}</v></c><c r="B{row_number}"><v>{value}</v></c></row>')
    worksheet = '<?xml version="1.0" encoding="utf-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(body) + "</sheetData></worksheet>"
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", f'<?xml version="1.0" encoding="utf-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>')
            archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        return buffer.getvalue()


class LevelSignalStorageTests(unittest.TestCase):
    def test_cboe_vix_parser_normalizes_dates_and_stops_at_cutoff(self):
        content = b"DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2005,13,14,12,13.5\n01/03/2005,14,15,13,14.5\n01/04/2005,15,16,14,15.5\n"
        self.assertEqual(parse_vix_csv(content, "2005-01-03"), [
            {"date": "2005-01-02", "close": 13.5},
            {"date": "2005-01-03", "close": 14.5},
        ])

    def test_ofr_credit_parser_uses_credit_component_not_headline_fsi(self):
        content = b"Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility\n2005-01-03,9.9,-0.25,0,0,0,0\n2005-01-04,8.8,-0.10,0,0,0,0\n"
        self.assertEqual(parse_ofr_fsi_csv(content, "2005-01-04"), [
            {"date": "2005-01-03", "credit": -0.25},
            {"date": "2005-01-04", "credit": -0.10},
        ])

    def test_nasdaq_sox_xlsx_parser_handles_excel_dates_and_reverse_order(self):
        content = sox_xlsx([("2005-01-04", 401.25), ("2005-01-03", 399.5)])
        self.assertEqual(parse_sox_xlsx(content, "2005-01-04"), [
            {"date": "2005-01-03", "close": 399.5},
            {"date": "2005-01-04", "close": 401.25},
        ])

    def test_international_features_use_lagged_three_year_percentiles(self):
        start = date(2025, 1, 1)
        days = [(start + timedelta(days=index)).isoformat() for index in range(42)]
        vix = [{"date": days[index], "close": value} for index, value in enumerate([10, 20, 30, 40, 35])]
        sox_values = [100] * 5 + [100, 99, 98, 97, 96, 90]
        sox = [{"date": days[index], "close": value} for index, value in enumerate(sox_values)]
        credit_values = [0] * 41 + [10]
        credit = [{"date": days[index], "credit": value} for index, value in enumerate(credit_values)]
        features = build_international_features(days[-1], vix, sox, credit, minimum_history=4)
        self.assertEqual(features["vix"]["observed_at"], days[4])
        self.assertEqual(features["vix"]["percentile"], 0.75)
        self.assertEqual(features["vix"]["high_10d"], 40)
        self.assertAlmostEqual(features["sox"]["return_5d"], -0.10)
        self.assertEqual(features["sox"]["percentile"], 0)
        self.assertEqual(features["credit"]["change_20d"], 10)
        self.assertEqual(features["credit"]["percentile"], 1)

    def test_sox_completeness_is_checked_year_by_year_against_vix_calendar(self):
        vix = [
            {"date": "2005-01-03", "close": 15}, {"date": "2005-12-30", "close": 16},
            {"date": "2006-01-03", "close": 17}, {"date": "2006-01-04", "close": 18},
        ]
        sox = [
            {"date": "2005-01-03", "close": 400}, {"date": "2005-12-30", "close": 401},
            {"date": "2006-01-03", "close": 402},
        ]
        report = validate_sox_years(sox, vix, start_year=2005, end_year=2006, minimum_coverage=1.0, minimum_full_year_rows=2, max_calendar_gap=370)
        self.assertTrue(report["years"]["2005"]["complete"])
        self.assertFalse(report["years"]["2006"]["complete"])
        self.assertEqual(report["years"]["2006"]["missing_vix_dates"], ["2006-01-04"])
        self.assertFalse(report["complete"])

    def test_eodhd_sp500_parser_uses_unadjusted_close_and_requires_200_rows(self):
        start = date(2025, 1, 1)
        rows = [
            {"date": (start + timedelta(days=index)).isoformat(), "close": 7000 + index, "adjusted_close": 1}
            for index in range(201)
        ]
        parsed = parse_eodhd_sp500(json.dumps(rows).encode(), "2026-11-10")
        self.assertEqual(parsed["history_count"], 201)
        self.assertEqual(parsed["close"], 7200)
        self.assertEqual(parsed["ma200"], sum(range(7001, 7201)) / 200)
        with self.assertRaises(ValueError):
            parse_eodhd_sp500(json.dumps(rows[:199]).encode(), "2026-11-10")

    def test_eodhd_secret_is_used_for_transport_but_never_persisted(self):
        token = "test-secret-must-not-leak"
        start = date(2025, 1, 1)
        eodhd = json.dumps([
            {"date": (start + timedelta(days=index)).isoformat(), "close": 7000 + index}
            for index in range(201)
        ]).encode()
        ofr = ["Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility"]
        ofr.extend(f"{(date(2023, 1, 1) + timedelta(days=index)).isoformat()},0,{index / 1000},0,0,0,0" for index in range(650))

        class FakePacer:
            def __init__(self): self.urls = []
            def fetch(self, url, **_kwargs):
                self.urls.append(url)
                return eodhd

        with tempfile.TemporaryDirectory() as temp:
            store = OriginalStore(Path(temp), "2026-11-10")
            store.directory.mkdir(parents=True)
            (store.directory / "ofr-fsi-credit.raw").write_text("\n".join(ofr), encoding="utf-8")
            pacer = FakePacer()
            with patch.dict("os.environ", {"EODHD_API_KEY": token}):
                international, quality = load_international_sources("2026-11-10", store, allow_network=True, pacer=pacer)
            self.assertEqual(international["sp500"]["history_count"], 201)
            self.assertEqual(quality["sp500"]["status"], "ok")
            self.assertIn(token, pacer.urls[0])
            for path in Path(temp).rglob("*"):
                if path.is_file():
                    self.assertNotIn(token.encode(), path.read_bytes(), path)

    def test_international_source_failures_become_missing_signals_instead_of_aborting(self):
        class FailingPacer:
            def fetch(self, _url, **_kwargs):
                raise RuntimeError("network unavailable")

        with tempfile.TemporaryDirectory() as temp:
            store = OriginalStore(Path(temp), "2026-11-10")
            with patch.dict("os.environ", {"EODHD_API_KEY": "test-secret"}):
                international, quality = load_international_sources("2026-11-10", store, allow_network=True, pacer=FailingPacer())
        self.assertEqual(international, {"sp500": {}, "credit": {}})
        self.assertEqual(quality["sp500"]["status"], "missing")
        self.assertEqual(quality["ofr_credit"]["status"], "missing")

    def test_daily_values_wire_eodhd_and_ofr_while_vix_sox_stay_unavailable(self):
        taiex = [{"date": f"2025-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}", "close": 100 + index} for index in range(200)]
        international = {
            "sp500": {"observed_at": "2026-11-10", "close": 7487.5, "ma200": 7330.91, "history_count": 252},
            "credit": {"observed_at": "2026-11-07", "value": 0.5, "change_20d": 0.7, "percentile": 0.96},
        }
        with patch("level_signals.load_taiex", return_value=taiex) as load_mock, patch("level_signals.breadth_features", return_value=(60, 10)) as breadth_mock, patch("level_signals.trading_day_status", return_value=True):
            values = build_values("2026-11-10", {"percentile": 0.5, "depreciation_20d": 0.01, "observed_at": "2026-11-09"}, international=international)
        load_mock.assert_called_once_with("2026-11-09")
        breadth_mock.assert_called_once_with("2026-11-09")
        self.assertEqual(values["sp500_close"], 7487.5)
        self.assertEqual(values["sp500_ma200"], 7330.91)
        self.assertEqual(values["credit_stress_change_percentile"], 0.96)
        self.assertIn("vix", values["unavailable_signals"])
        self.assertIn("sox_5d", values["unavailable_signals"])
        self.assertNotIn("sp500_trend", values["unavailable_signals"])
        self.assertNotIn("credit_stress", values["unavailable_signals"])

    def test_original_store_writes_bytes_metadata_and_sha_manifest_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            store = OriginalStore(Path(temp), "2026-11-10")
            entry = store.write("cboe-vix", b"DATE,CLOSE\n11/09/2026,20.5\n", "https://example.test/vix.csv", "2026-11-10T00:10:00Z")
            manifest = store.finish()
            expected = hashlib.sha256(b"DATE,CLOSE\n11/09/2026,20.5\n").hexdigest()
            self.assertEqual(entry["sha256"], expected)
            self.assertEqual(manifest["files"][0]["sha256"], expected)
            self.assertEqual((Path(temp) / "2026-11-10" / "cboe-vix.raw").read_bytes(), b"DATE,CLOSE\n11/09/2026,20.5\n")
            self.assertFalse(any(path.suffix == ".tmp" for path in (Path(temp) / "2026-11-10").iterdir()))

    def test_premarket_trading_day_does_not_require_same_day_close(self):
        self.assertTrue(trading_day_status("2026-11-10", holidays=set()))
        self.assertFalse(trading_day_status("2026-11-10", holidays={"2026-11-10"}))
        self.assertFalse(trading_day_status("2026-11-08", holidays=set()))

    def test_premarket_trading_day_uses_calendar_not_same_day_close(self):
        self.assertTrue(trading_day_status("2026-11-10", holidays=set()))
        self.assertFalse(trading_day_status("2026-11-08", holidays=set()))
        self.assertFalse(trading_day_status("2026-11-10", holidays={"2026-11-10"}))

    def test_cbc_usdtwd_parser_computes_twenty_day_depreciation_without_future_data(self):
        rows = []
        for index in range(650):
            year = 2023 + index // 250
            month = (index % 250) // 21 + 1
            day = index % 21 + 1
            rows.append(f"<row><Date>{year:04d}-{month:02d}-{day:02d}</Date><NTD_USD>{30 + index / 1000:.3f}</NTD_USD></row>")
        payload = ("<root>" + "".join(rows) + "</root>").encode()
        parsed = parse_cbc_usdtwd(payload, "2025-12-31")
        self.assertEqual(parsed["history_count"], 650)
        self.assertGreater(parsed["depreciation_20d"], 0)
        self.assertIsNotNone(parsed["percentile"])

    def test_daily_sql_is_versioned_append_only_and_quotes_json(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "daily.sql"
            payload = {"data_date": "2026-11-10", "rule_version": "cash-level-v2.1", "recommended_level": 80, "note": "本人決定"}
            write_daily_sql(target, payload, "a" * 64, "2026-11-10T08:30:00+08:00", "2026-11-10T00:31:00Z")
            sql = target.read_text(encoding="utf-8")
            self.assertNotIn("BEGIN", sql)
            self.assertEqual(sql.count("INSERT INTO level_daily"), 1)
            self.assertIn("MAX(version)", sql)
            self.assertIn(", 0\nFROM level_daily", sql)
            self.assertIn("cash-level-v2.1", sql)
            self.assertIn(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), sql)
            connection = sqlite3.connect(":memory:")
            migration = (Path(__file__).parent / "cloud" / "migrations" / "0006_cash_and_level.sql").read_text(encoding="utf-8")
            connection.executescript(migration)
            connection.executescript(sql)
            connection.executescript(sql)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM level_daily").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT payload FROM level_strategy_state WHERE key='current'").fetchone()[0], "{}")

            corrected = dict(payload, recommended_level=65, state={"recommended_level": 65})
            write_daily_sql(target, corrected, "b" * 64, "2026-11-10T08:30:00+08:00", "2026-11-10T01:00:00Z")
            connection.executescript(target.read_text(encoding="utf-8"))
            versions = connection.execute("SELECT version,is_current FROM level_daily ORDER BY version").fetchall()
            self.assertEqual(versions, [(1, 0), (2, 1)])
            self.assertEqual(json.loads(connection.execute("SELECT payload FROM level_strategy_state WHERE key='current'").fetchone()[0]), {"recommended_level": 65})
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE level_daily SET payload='{}' WHERE data_date='2026-11-10' AND version=1")


class LevelStateMergeTests(unittest.TestCase):
    def test_d1_newer_than_r2_replaces_local_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "state.json"
            source = root / "d1.json"
            target.write_text('{"last_processed_date":"2026-11-09","recommended_level":65}', encoding="utf-8")
            remote = {"last_processed_date": "2026-11-10", "recommended_level": 80}
            source.write_text(json.dumps([{"success": True, "results": [{"data_date": "2026-11-10", "payload": json.dumps(remote)}]}]), encoding="utf-8")

            result = merge_level_state(source, target, run_date="2026-11-10")

            self.assertEqual(result["selected"], "d1")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), remote)

    def test_older_d1_keeps_newer_r2_and_true_empty_can_initialize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "state.json"
            source = root / "d1.json"
            local = {"last_processed_date": "2026-11-10", "recommended_level": 65}
            target.write_text(json.dumps(local), encoding="utf-8")
            remote = {"last_processed_date": "2026-11-09", "recommended_level": 80}
            source.write_text(json.dumps([{"success": True, "results": [{"data_date": "2026-11-09", "payload": json.dumps(remote)}]}]), encoding="utf-8")
            self.assertEqual(merge_level_state(source, target, run_date="2026-11-10")["selected"], "r2")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), local)

            target.unlink()
            source.write_text(json.dumps([{"success": True, "results": []}]), encoding="utf-8")
            self.assertEqual(merge_level_state(source, target, run_date="2026-11-10"), {
                "selected": "empty", "last_processed_date": None,
            })
            self.assertFalse(target.exists())

    def test_read_error_malformed_or_future_state_fails_closed_without_touching_r2(self):
        cases = (
            None,
            "{bad-json",
            json.dumps([{"success": False, "results": []}]),
            json.dumps([{"results": []}]),
            json.dumps([{"success": True, "results": [{"data_date": "2026-11-10", "payload": "{bad"}]}]),
            json.dumps([{"success": True, "results": [{"data_date": "2026-99-99", "payload": json.dumps({"last_processed_date": "2026-99-99"})}]}]),
            json.dumps([{"success": True, "results": [{"data_date": "20261110", "payload": json.dumps({"last_processed_date": "20261110"})}]}]),
            json.dumps([{"success": True, "results": [{"data_date": "2026-11-11", "payload": json.dumps({"last_processed_date": "2026-11-11"})}]}]),
        )
        for index, content in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "state.json"
                source = root / "d1.json"
                original = {"last_processed_date": "2026-11-09", "recommended_level": 65}
                target.write_text(json.dumps(original), encoding="utf-8")
                if content is not None:
                    source.write_text(content, encoding="utf-8")
                with self.assertRaises(LevelStateMergeError):
                    merge_level_state(source, target, run_date="2026-11-10")
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)

    def test_workflows_fail_closed_use_domain_cursors_and_keep_shared_non_canceling_lock(self):
        root = Path(__file__).resolve().parent
        level = (root / ".github/workflows/level-daily.yml").read_text(encoding="utf-8")
        daily = (root / ".github/workflows/daily-update.yml").read_text(encoding="utf-8")
        digest = (root / ".github/workflows/ai-digest.yml").read_text(encoding="utf-8")
        self.assertNotIn('level-state.json" || true', level)
        self.assertIn("level_signals.py merge-state", level)
        self.assertLess(level.index("level_signals.py merge-state"), level.index("level_signals.py daily"))
        self.assertIn('state_sync.py upload --data-date "$level_state_date" --cursor-domain level', level)
        self.assertIn('["last_processed_date"]', level)
        self.assertIn('state_sync.py upload --data-date "${{ steps.day.outputs.date }}" --cursor-domain market', daily)
        for workflow in (level, daily, digest):
            self.assertIn("group: market-state-write", workflow)
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertIn("require_iso_date", workflow)


if __name__ == "__main__":
    unittest.main()
