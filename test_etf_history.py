"""股票型 ETF 的日線進行情庫（11 票）：從現成的每日行情原件回補，原件雜湊不符的那一天視為缺資料，重跑不重複；
每日附加自動帶上已收錄的 ETF；回補後的 ETF 走 0050 那條路算得出個股分析結果。"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path

import daily_ingest as di
import etf_history as eh
import otc_history as oh
import stock_history as sh
from test_daily_ingest import OTC_6488, FakeCatalog, Recorder, dump
from test_etf_classification import LISTED_ROWS, OTC_ROWS, write_catalog
from test_otc_history import csv_bytes
from test_stock_history import ROW_0050, ROW_0052_ETF, ROW_2330, official_payload

ROW_0056 = ["0056", "元大高股息"] + ROW_0052_ETF[2:]
ROW_00631L = ["00631L", "元大台灣50正2"] + ROW_0052_ETF[2:]
OTC_00888 = ["00888", "永豐台灣ESG"] + OTC_6488[2:]
OTC_00679B = ["00679B", "元大美債20年"] + OTC_6488[2:]
LISTED_DAY_ROWS = [ROW_0050, ROW_0056, ROW_00631L, ROW_2330]


def listed_raw(out: Path, day: str, rows=None) -> None:
    sh.write_once(sh.raw_file(out, day), gzip.compress(official_payload(day, rows or LISTED_DAY_ROWS)))


def _otc_raw_plain(out: Path, day: str, content: bytes) -> None:
    """與 otc_history.fetch_history 存的一樣：原件＋「雜湊  檔名」的 sidecar。"""
    path = out / "raw" / f"{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(content).hexdigest()}  {path.name}\n",
                                                     encoding="ascii")


def codes_on(db: Path, day: str) -> dict[str, str]:
    connection = sqlite3.connect(db)
    try:
        return dict(connection.execute("SELECT code, kind FROM daily WHERE date = ?", (day,)))
    finally:
        connection.close()


def source_rows(db: Path) -> dict[str, int]:
    connection = sqlite3.connect(db)
    try:
        return dict(connection.execute("SELECT date, rows FROM source_days"))
    finally:
        connection.close()


class ListedBackfillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.db = self.out / "stock-history.sqlite"
        for day in ("20261105", "20261106"):
            listed_raw(self.out, day)
        sh.build_database(self.out, self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_backfills_only_the_given_etfs_with_kind_etf(self) -> None:
        self.assertNotIn("0056", codes_on(self.db, "2026-11-05"))
        summary = eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertEqual(summary["status"], "backfilled")
        self.assertEqual(summary["rows"], 2)
        for day in ("2026-11-05", "2026-11-06"):
            self.assertEqual(codes_on(self.db, day), {"0050": "etf", "0056": "etf", "2330": "common"})
        self.assertEqual(source_rows(self.db), {"2026-11-05": 3, "2026-11-06": 3})
        self.assertEqual(eh.recorded_codes(self.db), {"0056"})
        # 價量照原件解析，和 0050 走同一個 parse_row
        self.assertEqual(sh.lookup(self.db, "2026-11-05", "0056")["close"], 37.19)

    def test_same_day_rerun_does_not_duplicate(self) -> None:
        eh.backfill(self.db, self.out, "上市", {"0056"})
        before = dump(self.db, "daily", "date, code")
        again = eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertEqual(again["status"], "up-to-date")
        self.assertEqual(dump(self.db, "daily", "date, code"), before)
        forced = eh.backfill(self.db, self.out, "上市", {"0056"}, force=True)
        self.assertEqual(forced["rows"], 0)
        self.assertEqual(dump(self.db, "daily", "date, code"), before)
        self.assertEqual(source_rows(self.db), {"2026-11-05": 3, "2026-11-06": 3})

    def test_raw_whose_hash_does_not_match_is_treated_as_missing(self) -> None:
        tampered = official_payload("20261106", [ROW_0050, ["0056", "元大高股息"] + ROW_0052_ETF[2:7] + ["99.99"] + ROW_0052_ETF[8:], ROW_2330])
        sh.raw_file(self.out, "20261106").write_bytes(gzip.compress(tampered))
        summary = eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertEqual(summary["hash_mismatch_days"], ["2026-11-06"])
        self.assertIn("0056", codes_on(self.db, "2026-11-05"))
        self.assertNotIn("0056", codes_on(self.db, "2026-11-06"))

    def test_missing_raw_day_is_counted_not_zero_filled(self) -> None:
        sh.raw_file(self.out, "20261105").unlink()
        summary = eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertEqual(summary["missing_raw_days"], {"count": 1, "first": "2026-11-05", "last": "2026-11-05"})
        self.assertNotIn("0056", codes_on(self.db, "2026-11-05"))

    def test_daily_append_carries_recorded_etfs_and_matches_rebuild_plus_backfill(self) -> None:
        other = self.out / "incremental"
        listed_raw(other, "20261105")
        db = other / "stock-history.sqlite"
        sh.build_database(other, db)
        eh.backfill(db, other, "上市", {"0056"})
        rec = Recorder({("MI_INDEX", "20261106"): sh.Response(200, "application/json",
                                                              official_payload("20261106", LISTED_DAY_ROWS), "u")})
        self.assertEqual(di.listed_quotes_day(other, db, "2026-11-06", get=rec.two_arg, sleep=lambda s: None)["status"],
                         "appended")
        self.assertEqual(codes_on(db, "2026-11-06"), {"0050": "etf", "0056": "etf", "2330": "common"})
        eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertEqual(dump(db, "daily", "date, code"), dump(self.db, "daily", "date, code"))
        self.assertEqual(source_rows(db), source_rows(self.db))
        self.assertEqual(di.listed_quotes_day(other, db, "2026-11-06", get=rec.two_arg, sleep=lambda s: None)["status"],
                         "already-present")

    def test_without_recorded_etfs_daily_append_adds_no_etf_rows(self) -> None:
        other = self.out / "plain"
        listed_raw(other, "20261105")
        db = other / "stock-history.sqlite"
        sh.build_database(other, db)
        rec = Recorder({("MI_INDEX", "20261106"): sh.Response(200, "application/json",
                                                              official_payload("20261106", LISTED_DAY_ROWS), "u")})
        di.listed_quotes_day(other, db, "2026-11-06", get=rec.two_arg, sleep=lambda s: None)
        self.assertEqual(eh.recorded_codes(db), set())
        self.assertEqual(codes_on(db, "2026-11-06"), {"0050": "etf", "2330": "common"})

    def test_force_rescans_and_fills_a_day_whose_raw_came_back(self) -> None:
        raw = sh.raw_file(self.out, "20261106")
        good = raw.read_bytes()
        raw.unlink()
        eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertNotIn("0056", codes_on(self.db, "2026-11-06"))
        raw.write_bytes(good)
        self.assertEqual(eh.backfill(self.db, self.out, "上市", {"0056"})["status"], "up-to-date")
        self.assertEqual(eh.backfill(self.db, self.out, "上市", {"0056"}, force=True)["rows"], 1)
        self.assertIn("0056", codes_on(self.db, "2026-11-06"))


class OtcBackfillTest(unittest.TestCase):
    def test_otc_etf_backfill_and_daily_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _otc_raw_plain(out, "20261105", csv_bytes(OTC_6488, OTC_00888, OTC_00679B, day="115/11/05"))
            catalog = FakeCatalog({"6488"})
            db = out / "otc_history.sqlite"
            oh.build_database(out, db, catalog)
            self.assertEqual(codes_on(db, "2026-11-05"), {"6488": "common"})
            summary = eh.backfill(db, out, "上櫃", {"00888"})
            self.assertEqual((summary["status"], summary["rows"]), ("backfilled", 1))
            self.assertEqual(codes_on(db, "2026-11-05"), {"6488": "common", "00888": "etf"})
            content = csv_bytes(OTC_6488, OTC_00888, OTC_00679B, day="115/11/06")
            rec = Recorder({("dailyQuotes", "2026/11/06"): oh.Response(200, "application/csv;charset=MS950", content, "u")})
            self.assertEqual(di.otc_quotes_day(out, db, "2026-11-06", catalog, get=rec.three_arg,
                                               sleep=lambda s: None)["status"], "appended")
            self.assertEqual(codes_on(db, "2026-11-06"), {"6488": "common", "00888": "etf"})
            self.assertEqual(source_rows(db), {"2026-11-05": 2, "2026-11-06": 2})


class RefreshTest(unittest.TestCase):
    def test_refresh_backfills_every_stock_type_etf_in_the_catalog_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, name, rows in (("data/listed-catalog/raw", "twse-isin-listed-20261108", LISTED_ROWS),
                                       ("data/otc-catalog/raw", "tpex-isin-otc-20261108", OTC_ROWS)):
                (root / folder).mkdir(parents=True)
                write_catalog(root / folder, name, rows)
            listed = root / "data/stock-history"
            listed_raw(listed, "20261105")
            sh.build_database(listed, listed / "stock-history.sqlite")
            otc = root / "data/otc-history"
            _otc_raw_plain(otc, "20261105", csv_bytes(OTC_6488, OTC_00888, OTC_00679B, day="115/11/05"))
            oh.build_database(otc, otc / "otc_history.sqlite", FakeCatalog({"6488"}))
            result = eh.refresh(root)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["上市"]["codes"], ["0056"])          # 0050 本來就在庫裡；00631L、00714 不是股票型
            self.assertEqual(result["上櫃"]["codes"], ["00888"])         # 00679B 債券型不收
            self.assertEqual(codes_on(listed / "stock-history.sqlite", "2026-11-05"),
                             {"0050": "etf", "0056": "etf", "2330": "common"})
            self.assertEqual(eh.refresh(root)["上市"]["status"], "up-to-date")
            (otc / "otc_history.sqlite").unlink()
            self.assertEqual(eh.refresh(root)["status"], "incomplete")   # 行情庫不在就不報 ok


def trading_days(count: int) -> list[str]:
    days, cursor = [], date(2026, 9, 1)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return days


class EtfAnalysisTest(unittest.TestCase):
    """回補進庫的股票型 ETF 走 0050 那條路：類別是 ETF、沒有基本面、沒有漲停動能。"""

    def test_backfilled_stock_etf_gets_an_analysis_result(self) -> None:
        from twon_analysis.analyze_v2 import analyze_v2

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            days = trading_days(70)
            for i, day in enumerate(days):
                price = f"{37 + (i % 5) * 0.1:.2f}"
                row = ["0056", "元大高股息", "1,000,000", "100", "37,190,000", price, price, price, price,
                       "<p style= color:red>+</p>", "0.10", price, "2", price, "9", "0.00"]
                listed_raw(out, day, [ROW_2330, row])
            db = out / "stock-history.sqlite"
            sh.build_database(out, db)
            eh.backfill(db, out, "上市", {"0056"})
            with unittest.mock.patch("twon_analysis.analyze_v2.valuation_snapshot", return_value=None), \
                    unittest.mock.patch("twon_analysis.analyze_v2.revenue_series", return_value=[]):
                result = analyze_v2("0056", sh.iso_date(days[-1]), db_path=db, otc_db_path=None,
                                    capital_actions={}, reference_events={})
        self.assertFalse(result["qa"]["blocking"])
        self.assertEqual((result["code"], result["kind"], result["market"]), ("0056", "etf", "上市"))
        self.assertEqual(result["qa"]["bars"], 70)
        self.assertIsNotNone(result["facts"])
        self.assertIsNone(result["facts"]["fundamentals"])
        self.assertNotIn("fundamentals", result["facts"]["blocks"])
        self.assertIsNone(result["signals"]["limit_up_momentum"])


if __name__ == "__main__":
    unittest.main()
