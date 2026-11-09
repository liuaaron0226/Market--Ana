"""E2：上櫃三大法人、本益比／淨值比、櫃買指數原件解析與專用資料庫契約。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import otc_market_data as omd


ROOT = Path(__file__).resolve().parent

INST_HEADER = (
    "代號,名稱,外資及陸資(不含外資自營商)-買進股數,外資及陸資(不含外資自營商)-賣出股數,"
    "外資及陸資(不含外資自營商)-買賣超股數,外資自營商-買進股數,外資自營商-賣出股數,外資自營商-買賣超股數,"
    "外資及陸資-買進股數,外資及陸資-賣出股數,外資及陸資-買賣超股數,投信-買進股數,投信-賣出股數,投信-買賣超股數,"
    "自營商(自行買賣)-買進股數,自營商(自行買賣)-賣出股數,自營商(自行買賣)-買賣超股數,"
    "自營商(避險)-買進股數,自營商(避險)-賣出股數,自營商(避險)-買賣超股數,"
    "自營商-買進股數,自營商-賣出股數,自營商-買賣超股數,三大法人買賣超股數合計"
)
# 取自 2026-11-06 官方原件 6488 一列（逐欄對照用）。
INST_6488 = (
    '"6488","環球晶","6,608,694","5,553,824","1,054,870","0","0","0","6,608,694","5,553,824","1,054,870",'
    '"45,570","1,835,000","-1,789,430","205,226","210,147","-4,921","394,427","421,791","-27,364",'
    '"599,653","631,938","-32,285","-766,845"'
)


def inst_csv(day_title: str = "115年11月06日", rows: list[str] | None = None, header: str = INST_HEADER) -> bytes:
    rows = [INST_6488] if rows is None else rows
    body = [f"{day_title} 三大法人日交易資訊(含普通股、鉅額、零股、綜合帳戶之投信買賣成交量)依股票代碼排序", header, *rows,
            f'"共{len(rows)}筆"', '"說明："']
    return ("\r\n".join(body) + "\r\n").encode("cp950")


VAL_HEADER = "股票代號,公司名稱,本益比,每股股利(註),股利年度,殖利率(%),股價淨值比,財報年/季"


def val_csv(roc_day: str = "115/11/06", rows: list[str] | None = None, header: str = VAL_HEADER) -> bytes:
    rows = [
        '"6488","環球晶          ","28.83","7.70000000","114","1.50","1.85","115Q3"',
        '"9949","琉園            ","N/A","2.20000000","114","9.24","2.17","115Q3"',
    ] if rows is None else rows
    body = ["上櫃股票個股本益比、殖利率、股價淨值比(依日期查詢)", f"資料日期:{roc_day}", "產業類別:全部產業", header, *rows,
            f'"共{len(rows)}筆"']
    return ("\r\n".join(body) + "\r\n").encode("cp950")


def index_json(month: str = "115/10", rows: list[list[str]] | None = None) -> bytes:
    rows = [
        ["2026/10/01", "417.48", "421.21", "417.09", "418.82", "1.75"],
        ["2026/10/02", "419.94", "426.93", "419.94", "426.93", "8.11"],
    ] if rows is None else rows
    payload = {"date": "20261001", "tables": [{
        "title": "櫃買指數(月查詢)", "date": month, "totalCount": len(rows),
        "fields": ["日期", "開市", "最高", "最低", "收市", "漲/跌"], "data": rows,
    }]}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class InstitutionalParseTest(unittest.TestCase):
    def test_parses_6488_columns_in_shares_matching_original(self) -> None:
        [row] = omd.parse_institutional_csv(inst_csv(), "20261106")
        self.assertEqual(row["date"], "2026-11-06")
        self.assertEqual(row["code"], "6488")
        self.assertEqual(row["name"], "環球晶")
        self.assertEqual(row["foreign_ex_dealer_net"], 1_054_870)
        self.assertEqual(row["foreign_dealer_net"], 0)
        self.assertEqual(row["foreign_net"], 1_054_870)
        self.assertEqual(row["trust_net"], -1_789_430)
        self.assertEqual(row["dealer_self_net"], -4_921)
        self.assertEqual(row["dealer_hedge_net"], -27_364)
        self.assertEqual(row["dealer_net"], -32_285)
        self.assertEqual(row["total_net"], -766_845)

    def test_total_matches_official_definition(self) -> None:
        # 官方註：合計＝外資及陸資(不含外資自營商)＋投信＋自營商；外資自營商已含在自營商內。
        [row] = omd.parse_institutional_csv(inst_csv(), "20261106")
        self.assertEqual(row["total_net"], row["foreign_ex_dealer_net"] + row["trust_net"] + row["dealer_net"])

    def test_wrong_date_title_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "資料日期"):
            omd.parse_institutional_csv(inst_csv(day_title="115年11月05日"), "20261106")

    def test_unexpected_header_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "欄位"):
            omd.parse_institutional_csv(inst_csv(header=INST_HEADER.replace("投信-買賣超股數", "投信-淨額")), "20261106")

    def test_blank_cell_stays_none_not_zero(self) -> None:
        row = INST_6488.replace('"-1,789,430"', '""')
        [parsed] = omd.parse_institutional_csv(inst_csv(rows=[row]), "20261106")
        self.assertIsNone(parsed["trust_net"])

    def test_footer_count_must_match_rows(self) -> None:
        content = inst_csv().decode("cp950").replace('"共1筆"', '"共2筆"').encode("cp950")
        with self.assertRaisesRegex(ValueError, "筆數"):
            omd.parse_institutional_csv(content, "20261106")


class ValuationParseTest(unittest.TestCase):
    def test_parses_valuation_and_keeps_na_as_none(self) -> None:
        rows = {row["code"]: row for row in omd.parse_valuation_csv(val_csv(), "20261106")}
        self.assertEqual(rows["6488"]["name"], "環球晶")
        self.assertEqual(rows["6488"]["pe"], 28.83)
        self.assertEqual(rows["6488"]["dividend_per_share"], 7.7)
        self.assertEqual(rows["6488"]["dividend_year_roc"], 114)
        self.assertEqual(rows["6488"]["dividend_yield_pct"], 1.5)
        self.assertEqual(rows["6488"]["pb"], 1.85)
        self.assertEqual(rows["6488"]["fiscal_period"], "115Q3")
        self.assertIsNone(rows["9949"]["pe"])
        self.assertEqual(rows["9949"]["pb"], 2.17)

    def test_wrong_date_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "資料日期"):
            omd.parse_valuation_csv(val_csv(roc_day="115/11/05"), "20261106")

    def test_unexpected_header_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "欄位"):
            omd.parse_valuation_csv(val_csv(header=VAL_HEADER.replace("本益比", "PER")), "20261106")


class ValuationWriteTest(unittest.TestCase):
    def test_records_follow_e1_columns_and_period_format(self) -> None:
        import valuation_history

        rows = omd.parse_valuation_csv(val_csv(), "20261106")
        records = omd.to_valuation_records(rows)
        for record in records:
            self.assertEqual(set(record), set(valuation_history.COLUMNS) - {"market"})
        by_code = {r["code"]: r for r in records}
        self.assertEqual(by_code["6488"]["fin_period_roc"], "115/3")  # 與上市 BWIBBU「115/2」同格式
        self.assertEqual(by_code["6488"]["pe"], 28.83)
        self.assertIsNone(by_code["9949"]["pe"])                     # 虧損／N/A 保留 None
        self.assertIsNone(by_code["6488"]["close"])                  # TPEx 原件沒有收盤價，不從別處補
        self.assertEqual(by_code["6488"]["schema"], "tpex8")

    def test_replace_market_writes_otc_only_and_keeps_listed(self) -> None:
        import valuation_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / "valuation.sqlite"
            listed = [{"date": "2026-11-06", "code": "2330", "name": "台積電", "close": 1.0, "dividend_yield_pct": 1.0,
                       "dividend_year_roc": 114, "pe": 20.0, "pb": 5.0, "fin_period_roc": "115/2", "schema": "v8"}]
            valuation_history.replace_market(db, "上市", listed, [])
            omd.write_raw(base / "tpex", "20261106.csv", val_csv())
            summary = omd.build_tpex_valuation(base / "tpex", db)
            self.assertEqual((summary["market"], summary["rows"], summary["pe_missing_rows"]), ("上櫃", 2, 1))
            connection = sqlite3.connect(db)
            try:
                counts = dict(connection.execute("SELECT market, COUNT(*) FROM valuation GROUP BY market").fetchall())
                days = connection.execute("SELECT market, raw_file, rows, schema FROM source_days").fetchall()
            finally:
                connection.close()
            self.assertEqual(counts, {"上市": 1, "上櫃": 2})
            self.assertEqual(days, [("上櫃", "tpex/raw/20261106.csv", 2, "tpex8")])


class IndexParseTest(unittest.TestCase):
    def test_parses_month_rows(self) -> None:
        rows = omd.parse_index_month_json(index_json(), "202610")
        self.assertEqual(rows[0], {"date": "2026-10-01", "open": 417.48, "high": 421.21, "low": 417.09,
                                   "close": 418.82, "change": 1.75})
        self.assertEqual(len(rows), 2)

    def test_wrong_month_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "月份"):
            omd.parse_index_month_json(index_json(month="115/09"), "202610")

    def test_row_outside_month_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "月份"):
            omd.parse_index_month_json(index_json(rows=[["2026/09/30", "1", "1", "1", "1", "0"]]), "202610")


class Response:
    def __init__(self, status: int, content_type: str, content: bytes, url: str = "u") -> None:
        self.status, self.content_type, self.content, self.url = status, content_type, content, url


class FetchTest(unittest.TestCase):
    def test_stops_on_429_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            calls: list[dict] = []

            def get(_session, _url, params):
                calls.append(params)
                return Response(429, "text/html", b"")

            summary = omd.fetch_dataset("institutional", out, ["20261106", "20261105"], get=get, sleep=lambda _s: None)
            self.assertEqual(summary["stopped"]["reason"], "HTTP 429")
            self.assertEqual(len(calls), 1)
            self.assertFalse(any((out / "raw").glob("*.csv")) if (out / "raw").exists() else False)

    def test_existing_raw_not_refetched_and_interval_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            sleeps: list[float] = []
            content = inst_csv()

            def get(_session, _url, params):
                title = "115年11月05日" if params["date"] == "2026/11/05" else "115年11月06日"
                return Response(200, "application/csv;charset=MS950", inst_csv(day_title=title))

            first = omd.fetch_dataset("institutional", out, ["20261105", "20261106"], get=get, sleep=sleeps.append)
            self.assertEqual(first["fetched"], 2)
            self.assertEqual(sleeps, [omd.MIN_INTERVAL_SECONDS])
            self.assertGreaterEqual(omd.MIN_INTERVAL_SECONDS, 5.0)
            raw = out / "raw" / "20261106.csv"
            self.assertEqual(raw.read_bytes(), content)
            sidecar = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()
            self.assertEqual(sidecar[0], hashlib.sha256(content).hexdigest())

            def boom(*_args):
                raise AssertionError("既有原件不應重抓")

            second = omd.fetch_dataset("institutional", out, ["20261105", "20261106"], get=boom, sleep=sleeps.append)
            self.assertEqual((second["fetched"], second["existing"]), (0, 2))

    def test_unexpected_content_type_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = omd.fetch_dataset(
                "valuation", Path(tmp), ["20261106"],
                get=lambda *_a: Response(200, "text/html", b"<html>"), sleep=lambda _s: None,
            )
            self.assertIn("Content-Type", summary["stopped"]["reason"])


class BuildAndLookupTest(unittest.TestCase):
    def _seed(self, base: Path) -> None:
        inst_dir = base / "raw-institutional"
        index_dir = base / "raw-index"
        for day, title in (("20261105", "115年11月05日"), ("20261106", "115年11月06日")):
            omd.write_raw(inst_dir, f"{day}.csv", inst_csv(day_title=title))
        omd.write_raw(index_dir, "202610.json", index_json())

    def test_build_is_idempotent_and_lookup_matches_twse_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._seed(base)
            db1, db2 = base / "a.sqlite", base / "b.sqlite"
            s1 = omd.build_market_db(base, db1)
            s2 = omd.build_market_db(base, db2)
            self.assertEqual(s1, s2)
            def dump(path: Path) -> list[str]:
                connection = sqlite3.connect(path)
                try:
                    return list(connection.iterdump())
                finally:
                    connection.close()

            self.assertEqual(dump(db1), dump(db2))
            self.assertEqual(omd.institutional_days(db1), ["2026-11-05", "2026-11-06"])
            [missing, row] = omd.institutional(db1, "6488", ["2026-11-04", "2026-11-06"])
            self.assertIsNone(missing)
            # 與 twon_analysis.chips.institutional 同形：foreign 含外資自營商、單位股。
            self.assertEqual(row, {"foreign": 1_054_870, "trust": -1_789_430, "dealer": -32_285, "total": -766_845})
            self.assertEqual(omd.index_range(db1, "2026-10-01", "2026-10-31")[1]["close"], 426.93)

    def test_build_rejects_tampered_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._seed(base)
            (base / "raw-institutional" / "raw" / "20261106.csv").write_bytes(inst_csv() + b"x")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                omd.build_market_db(base, base / "c.sqlite")


class RealDataTest(unittest.TestCase):
    """若已抓官方原件，逐份重解析以確認格式在整個區間一致。"""

    def test_real_institutional_raw_parses(self) -> None:
        raw_dir = ROOT / "data/otc-history/raw-institutional/raw"
        paths = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
        if not paths:
            self.skipTest("尚無上櫃法人原件")
        for path in paths:
            rows = omd.parse_institutional_csv(path.read_bytes(), path.stem)
            self.assertTrue(rows, path.name)

    def test_real_valuation_and_index_raw_parse(self) -> None:
        val_paths = sorted((ROOT / "data/valuation-history/tpex/raw").glob("*.csv"))
        index_paths = sorted((ROOT / "data/otc-history/raw-index/raw").glob("*.json"))
        if not val_paths or not index_paths:
            self.skipTest("尚無上櫃本益比或指數原件")
        for path in val_paths:
            self.assertTrue(omd.parse_valuation_csv(path.read_bytes(), path.stem), path.name)
        for path in index_paths:
            self.assertTrue(omd.parse_index_month_json(path.read_bytes(), path.stem), path.name)


if __name__ == "__main__":
    unittest.main()
