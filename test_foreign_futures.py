"""外資台指期部位的每日累積（G 票第 14 片）：期交所「三大法人－區分各期貨契約」原件的解析、保存與讀回。

只驗公開介面：給原件位元組（或假的下載函式），看存下來的原件、讀回的部位與錯誤。預期值都是手寫的固定數字。
"""
import json
import tempfile
import unittest
from pathlib import Path

import foreign_futures as ff

HEADER = ("日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),"
          "多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,"
          "空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)")


def row(day, name, who, net_oi):
    return f"{day},{name},{who},1,1,1,1,0,0,10,10,{10 - net_oi},10,{net_oi},0"


def csv_bytes(*rows):
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("cp950")


TXF_DAY = csv_bytes(row("2026/11/16", "臺股期貨", "自營商", 5), row("2026/11/16", "臺股期貨", "投信", 900),
                    row("2026/11/16", "臺股期貨", "外資及陸資", -1200))
MXF_DAY = csv_bytes(row("2026/11/16", "小型臺指期貨", "自營商", 7), row("2026/11/16", "小型臺指期貨", "外資及陸資", 400))
ERROR_PAGE = '<!DOCTYPE HTML><html><script>alert("DateTime error");</script></html>'.encode("utf-8")


class FakePost:
    def __init__(self, bodies):
        self.bodies, self.calls = dict(bodies), []

    def __call__(self, url, data):
        self.calls.append((url, dict(data)))
        body = self.bodies[data["commodityId"]]
        return ff.Response(200, "application/octet-stream;charset=MS950", body, url)


class ParseTest(unittest.TestCase):
    def test_reads_only_the_foreign_row_of_the_requested_contract(self):
        self.assertEqual(ff.parse_contracts_csv(TXF_DAY, "TXF"), {"2026-11-16": -1200})
        self.assertEqual(ff.parse_contracts_csv(MXF_DAY, "MXF"), {"2026-11-16": 400})

    def test_older_files_name_foreign_investors_without_mainland(self):
        content = csv_bytes(row("2023/11/16", "臺股期貨", "外資", -300))
        self.assertEqual(ff.parse_contracts_csv(content, "TXF"), {"2023-11-16": -300})

    def test_error_page_is_not_data(self):
        with self.assertRaises(ff.NotContractsCsv):
            ff.parse_contracts_csv(ERROR_PAGE, "TXF")

    def test_same_day_twice_with_different_numbers_is_refused(self):
        content = csv_bytes(row("2026/11/16", "臺股期貨", "外資及陸資", -1), row("2026/11/16", "臺股期貨", "外資及陸資", -2))
        with self.assertRaises(ValueError):
            ff.parse_contracts_csv(content, "TXF")


class FetchAndPositionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "foreign-futures"
        self.seed = Path(self.tmp.name) / "seed.json"
        self.seed.write_text(json.dumps({"schema": ff.SEED_SCHEMA, "rows": [
            {"date": "2026-11-12", "txf": -1000, "mxf": 0},
            {"date": "2026-11-13", "txf": -1100, "mxf": 200},
        ]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fetch_saves_both_contracts_and_positions_combine_seed_and_daily(self):
        post = FakePost({"TXF": TXF_DAY, "MXF": MXF_DAY})
        result = ff.fetch_day(self.store, "2026-11-16", post=post, sleep=lambda s: None)
        self.assertEqual(result["status"], "appended")
        self.assertEqual([call[1]["commodityId"] for call in post.calls], ["TXF", "MXF"])
        self.assertEqual(post.calls[0][1]["queryStartDate"], "2026/11/16")
        self.assertTrue((self.store / "raw" / "TXF-20261116.csv").exists())
        self.assertTrue((self.store / "raw" / "MXF-20261116.csv.sha256").exists())
        # 淨部位＝大台＋小台÷4：-1100 + 200/4 = -1050；-1200 + 400/4 = -1100
        self.assertEqual(ff.positions(self.store, self.seed), [
            {"date": "2026-11-12", "txf": -1000, "mxf": 0, "net": -1000.0},
            {"date": "2026-11-13", "txf": -1100, "mxf": 200, "net": -1050.0},
            {"date": "2026-11-16", "txf": -1200, "mxf": 400, "net": -1100.0},
        ])

    def test_second_fetch_of_the_same_day_sends_nothing(self):
        ff.fetch_day(self.store, "2026-11-16", post=FakePost({"TXF": TXF_DAY, "MXF": MXF_DAY}), sleep=lambda s: None)
        again = FakePost({"TXF": TXF_DAY, "MXF": MXF_DAY})
        self.assertEqual(ff.fetch_day(self.store, "2026-11-16", post=again, sleep=lambda s: None)["status"],
                         "already-present")
        self.assertEqual(again.calls, [])

    def test_not_yet_published_writes_nothing(self):
        post = FakePost({"TXF": ERROR_PAGE, "MXF": ERROR_PAGE})
        self.assertEqual(ff.fetch_day(self.store, "2026-11-16", post=post, sleep=lambda s: None)["status"],
                         "not_published")
        self.assertFalse((self.store / "raw").exists())

    def test_file_without_the_requested_day_writes_nothing(self):
        other = csv_bytes(row("2026/11/13", "臺股期貨", "外資及陸資", -1))
        result = ff.fetch_day(self.store, "2026-11-16", post=FakePost({"TXF": other, "MXF": MXF_DAY}),
                              sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertFalse((self.store / "raw").exists())

    def test_daily_file_disagreeing_with_seed_stops(self):
        ff.fetch_day(self.store, "2026-11-16", post=FakePost({"TXF": TXF_DAY, "MXF": MXF_DAY}), sleep=lambda s: None)
        self.seed.write_text(json.dumps({"schema": ff.SEED_SCHEMA, "rows": [
            {"date": "2026-11-16", "txf": -1, "mxf": 400}]}), encoding="utf-8")
        with self.assertRaises(ff.PositionsConflict):
            ff.positions(self.store, self.seed)

    def test_tampered_raw_file_stops(self):
        ff.fetch_day(self.store, "2026-11-16", post=FakePost({"TXF": TXF_DAY, "MXF": MXF_DAY}), sleep=lambda s: None)
        (self.store / "raw" / "TXF-20261116.csv").write_bytes(TXF_DAY.replace(b"-1200", b"-1300"))
        with self.assertRaises(ff.PositionsConflict):
            ff.positions(self.store, self.seed)

    def test_day_with_only_one_contract_is_left_out(self):
        self.seed.write_text(json.dumps({"schema": ff.SEED_SCHEMA, "rows": [
            {"date": "2026-11-13", "txf": -1100, "mxf": None}]}), encoding="utf-8")
        self.assertEqual(ff.positions(self.store, self.seed), [])


class SeedTest(unittest.TestCase):
    def test_seed_from_downloaded_ranges_skips_error_pages_and_records_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "taifex"
            src.mkdir()
            (src / "TXF_20261112_20261113.csv").write_bytes(csv_bytes(
                row("2026/11/12", "臺股期貨", "外資及陸資", -1000), row("2026/11/13", "臺股期貨", "外資及陸資", -1100)))
            (src / "MXF_20261112_20261113.csv").write_bytes(csv_bytes(
                row("2026/11/12", "小型臺指期貨", "外資及陸資", 0), row("2026/11/13", "小型臺指期貨", "外資及陸資", 200)))
            (src / "TXF_20210101_20210131.csv").write_bytes(ERROR_PAGE)
            out = Path(tmp) / "seed.json"
            summary = ff.build_seed(src, out)
            seed = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(summary["days"], 2)
        self.assertEqual(summary["skipped"], ["TXF_20210101_20210131.csv"])
        self.assertEqual(seed["rows"], [{"date": "2026-11-12", "txf": -1000, "mxf": 0},
                                        {"date": "2026-11-13", "txf": -1100, "mxf": 200}])
        self.assertEqual(sorted(source["file"] for source in seed["sources"]),
                         ["MXF_20261112_20261113.csv", "TXF_20261112_20261113.csv"])
        self.assertTrue(all(len(source["sha256"]) == 64 for source in seed["sources"]))


if __name__ == "__main__":
    unittest.main()
