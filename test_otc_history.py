from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from otc_history import Response, build_database, fetch_history, lookup_range, parse_csv


HEADER = [
    "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
    "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(千股)",
    "最後賣價", "最後賣量(千股)", "發行股數", "次日參考價", "次日漲停價", "次日跌停價",
]


def csv_bytes(*rows: list[str], day: str = "115/11/06") -> bytes:
    import csv
    import io

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["上櫃股票行情(含等價、零股、盤後、鉅額交易)"])
    writer.writerow([f"資料日期:{day}"])
    writer.writerow(HEADER)
    writer.writerows(rows)
    return stream.getvalue().encode("cp950")


class OtcHistoryTest(unittest.TestCase):
    def test_parse_csv_matches_official_price_and_base_units(self) -> None:
        raw = csv_bytes([
            "6488", "環球晶", "564.00", "-55.00 ", "601.00", "619.00", "558.00", "581.36",
            "18,953,974", "11,018,999,667", "40,954", "563.00", "67", "564.00", "76",
            "528,113,725", "564.00", "620.00", "508.00",
        ])

        record = parse_csv(raw, "20261106")[0]

        self.assertEqual(record["date"], "2026-11-06")
        self.assertEqual(record["code"], "6488")
        self.assertEqual(record["name"], "環球晶")
        self.assertEqual(
            (record["open"], record["high"], record["low"], record["close"]),
            (601.0, 619.0, 558.0, 564.0),
        )
        self.assertEqual(record["change_sign"], "-")
        self.assertEqual(record["change"], -55.0)
        self.assertEqual(record["volume_shares"], 18_953_974)
        self.assertEqual(record["turnover"], 11_018_999_667)
        self.assertEqual(record["trades"], 40_954)
        self.assertFalse(record["suspended"])

    def test_real_official_sample_matches_saved_original(self) -> None:
        raw_path = Path(__file__).resolve().parent / "data/otc-history/raw/20251027.csv"
        if not raw_path.exists():
            self.skipTest("整合原件尚未下載；單元測試仍使用內嵌官方逐欄樣本")
        record = next(row for row in parse_csv(raw_path.read_bytes(), "20251027") if row["code"] == "6488")

        # TPEx 原件 2025/10/27 的 6488 列：收539、漲4、開549、高558、低538、
        # 成交股數5,903,030、成交金額3,211,856,475元、成交7,614筆。
        self.assertEqual(
            (record["open"], record["high"], record["low"], record["close"]),
            (549.0, 558.0, 538.0, 539.0),
        )
        self.assertEqual((record["change_sign"], record["change"]), ("+", 4.0))
        self.assertEqual(record["volume_shares"], 5_903_030)
        self.assertEqual(record["turnover"], 3_211_856_475)
        self.assertEqual(record["trades"], 7_614)

    def test_missing_volume_turnover_and_trades_stay_null(self) -> None:
        raw = csv_bytes([
            "6488", "環球晶", " ---", "---", "---", "---", "---", "---",
            "---", "---", "---", "---", "---", "---", "---", "528,113,725", "500.00", "550.00", "450.00",
        ])

        record = parse_csv(raw, "20261106")[0]

        self.assertIsNone(record["volume_shares"])
        self.assertIsNone(record["turnover"])
        self.assertIsNone(record["trades"])
        self.assertIsNone(record["open"])
        self.assertIsNone(record["close"])

    def test_corporate_action_row_keeps_official_ohlc_without_adjustment(self) -> None:
        raw = csv_bytes([
            "6488", "環球晶", "500.00", "除息", "505.00", "510.00", "498.00", "503.00",
            "1,000", "503,000", "25", "499.00", "10", "500.00", "12",
            "528,113,725", "500.00", "550.00", "450.00",
        ])

        record = parse_csv(raw, "20261106")[0]

        self.assertEqual(record["change_sign"], "X")
        self.assertIsNone(record["change"])
        self.assertEqual(
            (record["open"], record["high"], record["low"], record["close"]),
            (505.0, 510.0, 498.0, 500.0),
        )

    def test_truncated_security_row_is_rejected_instead_of_silently_skipped(self) -> None:
        raw = csv_bytes(
            [
                "6488", "環球晶", "564.00", "-55.00", "601.00", "619.00", "558.00", "581.36",
                "18,953,974", "11,018,999,667", "40,954", "563.00", "67", "564.00", "76",
                "528,113,725", "564.00", "620.00", "508.00",
            ],
            ["9999", "截短資料", "10.00"],
        )

        with self.assertRaisesRegex(ValueError, "9999.*欄位數"):
            parse_csv(raw, "20261106")

    def test_fetch_stops_on_429_keeps_existing_raw_and_resumes(self) -> None:
        calls: list[str] = []
        sleeps: list[float] = []
        first_raw = csv_bytes([
            "6488", "環球晶", "564.00", "-55.00", "601.00", "619.00", "558.00", "581.36",
            "18,953,974", "11,018,999,667", "40,954", "563.00", "67", "564.00", "76",
            "528,113,725", "564.00", "620.00", "508.00",
        ])

        def first_get(session: object, url: str, params: dict) -> Response:
            calls.append(params["date"])
            if len(calls) == 1:
                return Response(200, "application/csv;charset=MS950", first_raw, f"{url}?date=2026/11/06")
            return Response(429, "text/html", b"rate limited", f"{url}?date=2026/11/07")

        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            first = fetch_history(out, ["20261106", "20261107", "20261108"], get=first_get, sleep=sleeps.append)
            saved = out / "raw" / "20261106.csv"
            self.assertEqual(saved.read_bytes(), first_raw)
            self.assertEqual(first["fetched"], 1)
            self.assertEqual(first["requested"], 2)
            self.assertEqual(first["stopped"], {"date": "20261107", "reason": "HTTP 429"})
            self.assertEqual(calls, ["2026/11/06", "2026/11/07"])
            self.assertEqual(sleeps, [5.0])

            second_raw = csv_bytes([
                "6488", "環球晶", "570.00", "6.00", "564.00", "572.00", "563.00", "568.00",
                "1,000", "568,000", "30", "569.00", "10", "570.00", "12",
                "528,113,725", "570.00", "627.00", "513.00",
            ], day="115/11/07")

            def second_get(session: object, url: str, params: dict) -> Response:
                return Response(200, "application/csv;charset=MS950", second_raw, f"{url}?date=2026/11/07")

            resumed = fetch_history(out, ["20261106", "20261107"], get=second_get, sleep=sleeps.append)
            self.assertEqual(saved.read_bytes(), first_raw)
            self.assertEqual(resumed["existing"], 1)
            self.assertEqual(resumed["fetched"], 1)
            self.assertIsNone(resumed["stopped"])
            self.assertTrue((out / "raw" / "20261107.csv.sha256").exists())
            meta = __import__("json").loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["date"] for entry in meta["files"]], ["20261106", "20261107"])
            self.assertEqual(meta["files"][0]["sha256"], hashlib.sha256(first_raw).hexdigest())

    def test_fetch_stops_on_403_or_unexpected_format(self) -> None:
        cases = [
            (Response(403, "text/html", b"forbidden", "https://example.invalid"), "HTTP 403"),
            (Response(200, "text/html", b"verification", "https://example.invalid"), "非預期 Content-Type：text/html"),
            (Response(200, "application/csv;charset=MS950", b"not csv", "https://example.invalid"), "非預期 CSV 格式"),
        ]
        for response, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason), tempfile.TemporaryDirectory() as temporary:
                summary = fetch_history(
                    Path(temporary),
                    ["20261106", "20261107"],
                    get=lambda session, url, params, value=response: value,
                    sleep=lambda seconds: self.fail("停止後不應再等待或發出第二次請求"),
                )
                self.assertEqual(summary["requested"], 1)
                self.assertIn(expected_reason, summary["stopped"]["reason"])

    def test_build_filters_catalog_keeps_missing_prices_and_is_idempotent(self) -> None:
        class Catalog:
            def lookup(self, code: str) -> object | None:
                return object() if code in {"6488", "2064"} else None

        normal = [
            "6488", "環球晶", "564.00", "-55.00 ", "601.00", "619.00", "558.00", "581.36",
            "18,953,974", "11,018,999,667", "40,954", "563.00", "67", "564.00", "76",
            "528,113,725", "564.00", "620.00", "508.00",
        ]
        suspended = [
            "2064", "晉椿", " ---", "--- ", "---", "---", "---", "11.02",
            "0", "0", "0", "11.00", "10", "11.05", "20", "100,000,000", "11.02", "12.10", "9.92",
        ]
        etf = [
            "00679B", "元大美債20年", "26.89", "-0.10 ", "26.99", "27.05", "26.77", "26.90",
            "76,006,293", "2,044,571,976", "9,843", "26.88", "100", "26.89", "100",
            "1,000,000,000", "26.89", "29.57", "24.21",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            raw_dir = out / "raw"
            raw_dir.mkdir()
            raw = csv_bytes(normal, suspended, etf)
            raw_path = raw_dir / "20261106.csv"
            raw_path.write_bytes(raw)
            (raw_dir / "20261106.csv.sha256").write_text(
                f"{hashlib.sha256(raw).hexdigest()}  20261106.csv\n", encoding="ascii"
            )
            db_path = out / "otc_history.sqlite"

            first = build_database(out, db_path, Catalog())
            first_rows = lookup_range(db_path, "6488", "2026-11-01", "2026-11-30")
            second = build_database(out, db_path, Catalog())
            second_rows = lookup_range(db_path, "6488", "2026-11-01", "2026-11-30")

            self.assertEqual(first, second)
            self.assertEqual(first_rows, second_rows)
            self.assertEqual(len(first_rows), 1)
            self.assertEqual(first_rows[0]["kind"], "common")
            self.assertEqual(
                (first_rows[0]["open"], first_rows[0]["high"], first_rows[0]["low"], first_rows[0]["close"]),
                (601.0, 619.0, 558.0, 564.0),
            )
            self.assertEqual(first_rows[0]["volume_shares"], 18_953_974)
            self.assertEqual(first_rows[0]["turnover"], 11_018_999_667)
            self.assertEqual(lookup_range(db_path, "00679B", "2026-11-01", "2026-11-30"), [])
            stopped = lookup_range(db_path, "2064", "2026-11-01", "2026-11-30")[0]
            self.assertIsNone(stopped["open"])
            self.assertIsNone(stopped["high"])
            self.assertIsNone(stopped["low"])
            self.assertIsNone(stopped["close"])
            self.assertEqual(stopped["volume_shares"], 0)
            self.assertTrue(stopped["suspended"])


if __name__ == "__main__":
    unittest.main()
