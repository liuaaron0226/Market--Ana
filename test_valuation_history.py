"""valuation_history：證交所 BWIBBU_d 個股本益比／殖利率／股價淨值比歷史的公開契約測試。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import valuation_history as vh

ROOT = Path(__file__).resolve().parent

FIELDS_NEW = ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]
FIELDS_OLD = ["證券代號", "證券名稱", "本益比", "殖利率(%)", "股價淨值比"]


def payload(day: str, fields: list[str], rows: list[list], stat: str = "OK") -> bytes:
    body = {"stat": stat, "date": day, "title": "個股日本益比、殖利率及股價淨值比", "fields": fields,
            "data": rows, "selectType": "ALL", "total": len(rows)}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_new_format_maps_every_column_and_keeps_loss_pe_as_none(self) -> None:
        content = payload("20261106", FIELDS_NEW, [
            ["1101", "台泥", "26.75", "2.99", 114, "-", "0.87", "115/2"],
            ["1102", "亞泥", "36.55", "6.29", 114, "10.10", "0.70", "115/2"],
        ])
        records = vh.parse_payload(content, "20261106")
        self.assertEqual(records[0], {
            "date": "2026-11-06", "code": "1101", "name": "台泥", "close": 26.75,
            "dividend_yield_pct": 2.99, "dividend_year_roc": 114, "pe": None, "pb": 0.87,
            "fin_period_roc": "115/2", "schema": "v8",
        })
        self.assertEqual(records[1]["pe"], 10.10)
        self.assertIsNone(records[0]["pe"], "虧損公司本益比必須保留 None，不能補 0")

    def test_old_format_without_close_or_period_keeps_missing_columns_none(self) -> None:
        content = payload("20160129", FIELDS_OLD, [["1103", "嘉泥", "-", "3.32", "0.47"]])
        record = vh.parse_payload(content, "20160129")[0]
        self.assertEqual(record["schema"], "v5")
        self.assertIsNone(record["close"])
        self.assertIsNone(record["dividend_year_roc"])
        self.assertIsNone(record["fin_period_roc"])
        self.assertIsNone(record["pe"])
        self.assertEqual((record["dividend_yield_pct"], record["pb"]), (3.32, 0.47))

    def test_rejects_wrong_date_missing_fields_bad_row_and_unknown_token(self) -> None:
        good = [["1101", "台泥", "26.75", "2.99", 114, "-", "0.87", "115/2"]]
        with self.assertRaisesRegex(ValueError, "日期"):
            vh.parse_payload(payload("20261105", FIELDS_NEW, good), "20261106")
        with self.assertRaisesRegex(ValueError, "欄位"):
            vh.parse_payload(payload("20261106", ["證券代號", "證券名稱", "收盤價"], [["1101", "台泥", "1"]]), "20261106")
        with self.assertRaisesRegex(ValueError, "欄位數"):
            vh.parse_payload(payload("20261106", FIELDS_NEW, [["1101", "台泥", "26.75"]]), "20261106")
        with self.assertRaisesRegex(ValueError, "數值"):
            vh.parse_payload(payload("20261106", FIELDS_NEW, [["1101", "台泥", "26.75", "N/A", 114, "-", "0.87", "115/2"]]), "20261106")

    def test_real_raw_row_matches_original_column_by_column(self) -> None:
        raws = sorted((ROOT / "data/valuation-history/twse/raw").glob("*.json"))
        if not raws:
            self.skipTest("尚未抓取原件")
        raw = raws[-1]
        original = json.loads(raw.read_bytes().decode("utf-8"))
        records = {r["code"]: r for r in vh.parse_payload(raw.read_bytes(), raw.stem)}
        self.assertEqual(len(records), len(original["data"]))
        mapping = {"本益比": "pe", "股價淨值比": "pb", "殖利率(%)": "dividend_yield_pct", "收盤價": "close"}
        for row in original["data"][:50]:
            cell = dict(zip(original["fields"], row))
            parsed = records[cell["證券代號"]]
            for field, key in mapping.items():
                if field not in cell:
                    continue
                text = str(cell[field]).replace(",", "").strip()
                expected = None if text in ("-", "", "--") else float(text)
                self.assertEqual(parsed[key], expected, f"{raw.name} {cell['證券代號']} {field}")


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "valuation.sqlite"
        self.raw = self.root / "twse"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put_raw(self, day: str, rows: list[list], fields: list[str] = FIELDS_NEW) -> None:
        content = payload(day, fields, rows)
        target = self.raw / "raw" / f"{day}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.with_name(target.name + ".sha256").write_text(
            f"{hashlib.sha256(content).hexdigest()}  {target.name}\n", encoding="ascii")

    def test_build_market_is_idempotent_and_keeps_other_market_rows(self) -> None:
        self.put_raw("20261105", [["2330", "台積電", "1000", "1.5", 114, "25.00", "7.00", "115/2"]])
        self.put_raw("20261106", [["2330", "台積電", "1010", "1.4", 114, "25.25", "7.07", "115/2"]])
        vh.replace_market(self.db, "上櫃", [
            {"date": "2026-11-06", "code": "6488", "name": "環球晶", "close": None, "dividend_yield_pct": 2.0,
             "dividend_year_roc": None, "pe": 20.0, "pb": 2.0, "fin_period_roc": None, "schema": "tpex"},
        ], [{"date": "2026-11-06", "raw_file": "x.csv", "sha256": "0" * 64, "rows": 1, "schema": "tpex"}])
        first = vh.build_twse(self.raw, self.db)
        second = vh.build_twse(self.raw, self.db)
        self.assertEqual(first, second)
        self.assertEqual((first["days"], first["rows"]), (2, 2))
        connection = sqlite3.connect(self.db)
        try:
            markets = dict(connection.execute("SELECT market, COUNT(*) FROM valuation GROUP BY market"))
        finally:
            connection.close()
        self.assertEqual(markets, {"上市": 2, "上櫃": 1})

    def test_build_refuses_sha_mismatch_without_touching_existing_db(self) -> None:
        self.put_raw("20261106", [["2330", "台積電", "1010", "1.4", 114, "25.25", "7.07", "115/2"]])
        vh.build_twse(self.raw, self.db)
        (self.raw / "raw" / "20261106.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            vh.build_twse(self.raw, self.db)
        self.assertEqual(vh.as_of(self.db, "2026-11-06", code="2330")["pe"], 25.25)

    def test_as_of_never_returns_future_rows(self) -> None:
        self.put_raw("20261105", [["2330", "台積電", "1000", "1.5", 114, "25.00", "7.00", "115/2"]])
        self.put_raw("20261106", [["2330", "台積電", "1010", "1.4", 114, "25.25", "7.07", "115/2"]])
        vh.build_twse(self.raw, self.db)
        self.assertIsNone(vh.as_of(self.db, "2026-11-04", code="2330"))
        self.assertEqual(vh.as_of(self.db, "2026-11-05", code="2330")["pe"], 25.00)
        row = vh.as_of(self.db, "2026-11-07", code="2330")
        self.assertEqual((row["date"], row["pe"]), ("2026-11-06", 25.25))
        snapshot = vh.as_of(self.db, "2026-11-05")
        self.assertEqual([r["date"] for r in snapshot], ["2026-11-05"])

    def test_as_of_keeps_loss_pe_none(self) -> None:
        self.put_raw("20261106", [["1101", "台泥", "26.75", "2.99", 114, "-", "0.87", "115/2"]])
        vh.build_twse(self.raw, self.db)
        self.assertIsNone(vh.as_of(self.db, "2026-11-06", code="1101")["pe"])


class PercentileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "valuation.sqlite"
        rows = [
            # 水泥 6 家，其中 1 家虧損 PE None；PB 皆有
            ("1101", None, 0.9), ("1102", 10.0, 0.7), ("1103", 12.0, 0.5),
            ("1104", 14.0, 1.0), ("1108", 16.0, 1.2), ("1109", 20.0, 1.5),
            # 食品只有 2 家，同業不足
            ("1201", 15.0, 1.1), ("1203", 18.0, 1.3),
        ]
        records = [{"date": "2026-11-06", "code": c, "name": c, "close": 10.0, "dividend_yield_pct": 1.0,
                    "dividend_year_roc": 114, "pe": pe, "pb": pb, "fin_period_roc": "115/2", "schema": "v8"}
                   for c, pe, pb in rows]
        vh.replace_market(self.db, "上市", records,
                          [{"date": "2026-11-06", "raw_file": "f", "sha256": "0" * 64, "rows": len(records), "schema": "v8"}])
        self.industry = {c: ("水泥工業" if c.startswith("11") else "食品工業") for c, _, _ in rows}.get

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_percentile_excludes_loss_pe_from_peers(self) -> None:
        result = vh.industry_percentile(self.db, "2026-11-06", "1103", "pe", self.industry)
        self.assertEqual((result["peers_total"], result["peers_with_value"]), (6, 5))
        # 12 在 [10,12,14,16,20] 中：低於者 1，等於者 1 → (1 + 0.5) / 5
        self.assertAlmostEqual(result["percentile"], 0.3)
        self.assertEqual(result["industry"], "水泥工業")

    def test_loss_company_has_no_pe_percentile(self) -> None:
        result = vh.industry_percentile(self.db, "2026-11-06", "1101", "pe", self.industry)
        self.assertIsNone(result["percentile"])
        self.assertEqual(result["reason"], "value_missing")
        self.assertIsNotNone(vh.industry_percentile(self.db, "2026-11-06", "1101", "pb", self.industry)["percentile"])

    def test_extremes_and_too_few_peers(self) -> None:
        low = vh.industry_percentile(self.db, "2026-11-06", "1102", "pe", self.industry)
        high = vh.industry_percentile(self.db, "2026-11-06", "1109", "pe", self.industry)
        self.assertAlmostEqual(low["percentile"], 0.1)
        self.assertAlmostEqual(high["percentile"], 0.9)
        few = vh.industry_percentile(self.db, "2026-11-06", "1201", "pe", self.industry)
        self.assertIsNone(few["percentile"])
        self.assertEqual(few["reason"], "too_few_peers")

    def test_unknown_industry_and_no_data(self) -> None:
        self.assertEqual(vh.industry_percentile(self.db, "2026-11-06", "9999", "pe", self.industry)["reason"], "no_data")
        self.assertEqual(vh.industry_percentile(self.db, "2026-11-06", "1102", "pe", lambda code: None)["reason"],
                         "industry_unknown")
        self.assertEqual(vh.industry_percentile(self.db, "2026-11-05", "1102", "pe", self.industry)["reason"], "no_data")
        with self.assertRaises(ValueError):
            vh.industry_percentile(self.db, "2026-11-06", "1102", "close", self.industry)


class FetchTest(unittest.TestCase):
    def test_fetch_stops_on_429_skips_existing_and_spaces_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            sleeps: list[float] = []
            calls: list[str] = []

            def get(session, url, params):
                calls.append(params["date"])
                if params["date"] == "20261106":
                    return vh.Response(429, "text/html", b"", url)
                return vh.Response(200, "application/json", payload(params["date"], FIELDS_NEW, [
                    ["2330", "台積電", "1000", "1.5", 114, "25.00", "7.00", "115/2"]]), url)

            summary = vh.fetch_history(out, ["20261104", "20261105", "20261106", "20261107"], get=get, sleep=sleeps.append)
            self.assertEqual(calls, ["20261104", "20261105", "20261106"])
            self.assertEqual(summary["fetched"], 2)
            self.assertEqual(summary["stopped"]["reason"], "HTTP 429")
            self.assertTrue(all(s >= 5.0 for s in sleeps))
            calls.clear()
            again = vh.fetch_history(out, ["20261104", "20261105"], get=get, sleep=sleeps.append)
            self.assertEqual((calls, again["existing"]), ([], 2))
            meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual([f["date"] for f in meta["files"]], ["20261104", "20261105"])

    def test_fetch_records_gap_for_no_data_and_stops_on_non_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)

            def get(session, url, params):
                if params["date"] == "20260101":
                    return vh.Response(200, "application/json", payload("20260101", FIELDS_NEW, [], stat="很抱歉，沒有符合條件的資料!"), url)
                return vh.Response(200, "text/html", b"<html>", url)

            summary = vh.fetch_history(out, ["20260101", "20260102"], get=get, sleep=lambda s: None)
            self.assertEqual(summary["gaps"][0]["date"], "20260101")
            self.assertIn("非 JSON", summary["stopped"]["reason"])
            self.assertFalse((out / "raw" / "20260101.json").exists())


if __name__ == "__main__":
    unittest.main()
