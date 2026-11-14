"""集保股權分散表 → 大戶持股（本人 2026-11-14「可以，用集保」：主力買賣超沒有合法免費來源，改看大戶持股變化）。

樣本列逐字取自集保開放資料 2026-11-13 的檔案（2330、6488）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_history import Response
from tdcc_holders import parse, refresh, series

HEADER = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%"
ROWS_2330 = """20261113,2330  ,1,2710169,321536615,1.23
20261113,2330  ,2,478361,915362472,3.52
20261113,2330  ,3,55402,395556030,1.52
20261113,2330  ,4,18166,222664385,0.85
20261113,2330  ,5,8459,148651087,0.57
20261113,2330  ,6,8044,196784307,0.75
20261113,2330  ,7,3751,130282983,0.50
20261113,2330  ,8,2077,93695608,0.36
20261113,2330  ,9,4172,290513368,1.12
20261113,2330  ,10,2088,292230378,1.12
20261113,2330  ,11,1326,372159054,1.43
20261113,2330  ,12,583,284267615,1.09
20261113,2330  ,13,341,237404385,0.91
20261113,2330  ,14,226,203333688,0.78
20261113,2330  ,15,1466,21827933991,84.17
20261113,2330  ,16,3,5899,0.00
20261113,2330  ,17,3294631,25932370067,100.00"""
ROWS_6488 = """20261113,6488  ,12,29,14448107,2.73
20261113,6488  ,13,15,9961639,1.88
20261113,6488  ,14,7,6078595,1.15
20261113,6488  ,15,49,348971316,66.07
20261113,6488  ,16,0,0,0.00
20261113,6488  ,17,178737,528113725,100.00"""
WARRANT = "20261113,03001X,15,0,0,0.00\n20261113,03001X,17,12,500000,100.00"


def csv(*blocks: str, header: str = HEADER) -> bytes:
    return ("﻿" + header + "\n" + "\n".join(blocks) + "\n").encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_big_holders_are_the_400_lot_and_1000_lot_brackets(self) -> None:
        day, holders = parse(csv(ROWS_2330, ROWS_6488))
        self.assertEqual(day, "2026-11-13")
        # 千張以上＝第 15 級；400 張以上＝第 12～15 級（0.91+0.78+1.09+84.17）。
        self.assertEqual(holders["2330"], {"pct_1000": 84.17, "holders_1000": 1466, "pct_400": 86.95,
                                           "holders_400": 2616, "holders": 3294631})
        self.assertEqual(holders["6488"], {"pct_1000": 66.07, "holders_1000": 49, "pct_400": 71.83,
                                           "holders_400": 100, "holders": 178737})

    def test_warrants_and_other_non_stock_codes_are_left_out(self) -> None:
        _day, holders = parse(csv(ROWS_2330, WARRANT))
        self.assertEqual(list(holders), ["2330"])

    def test_a_file_with_other_columns_or_mixed_dates_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse(csv(ROWS_2330, header="資料日期,證券代號,持股分級,人數,股數"))
        with self.assertRaises(ValueError):
            parse(csv(ROWS_2330, ROWS_6488.replace("20261113", "20261106")))
        with self.assertRaises(ValueError):
            parse(b"<html>blocked</html>")


class RefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "tdcc-holders"
        self.calls = 0

    def get(self, content: bytes, status: int = 200):
        def fetch(url, params):
            self.calls += 1
            return Response(status, "text/csv", content, url)
        return fetch

    def test_each_weekly_file_is_stored_once_under_its_own_date(self) -> None:
        first = refresh(self.out, get=self.get(csv(ROWS_2330, ROWS_6488)))
        self.assertEqual(first, {"status": "appended", "requests": 1, "data_date": "2026-11-13", "stocks": 2})
        saved = json.loads((self.out / "weekly/2026-11-13.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["holders"]["2330"]["pct_1000"], 84.17)
        again = refresh(self.out, get=self.get(csv(ROWS_2330)))   # 同一週再抓：不覆寫已存的那一週
        self.assertEqual((again["status"], again["stocks"]), ("already-present", 2))

    def test_a_refused_or_unreadable_download_changes_nothing(self) -> None:
        self.assertEqual(refresh(self.out, get=self.get(b"", 403))["status"], "stopped")
        self.assertEqual(refresh(self.out, get=self.get(b"<html></html>"))["status"], "stopped")
        self.assertFalse((self.out / "weekly").exists())

    def test_series_gives_one_stock_week_by_week_up_to_a_day(self) -> None:
        refresh(self.out, get=self.get(csv(ROWS_2330, ROWS_6488)))
        later = ROWS_2330.replace("20261113", "20261120").replace(",1466,21827933991,84.17", ",1470,21850000000,84.30")
        refresh(self.out, get=self.get(csv(later)))
        rows = series(self.out, "2330", "9999-12-31")
        self.assertEqual([(row["date"], row["pct_1000"], row["holders_1000"]) for row in rows],
                         [("2026-11-13", 84.17, 1466), ("2026-11-20", 84.3, 1470)])
        self.assertEqual([row["date"] for row in series(self.out, "2330", "2026-11-19")], ["2026-11-13"])
        self.assertEqual([row["date"] for row in series(self.out, "6488", "9999-12-31")], ["2026-11-13"])  # 後一週沒有它
        self.assertEqual(series(self.out, "9999", "9999-12-31"), [])


if __name__ == "__main__":
    unittest.main()
