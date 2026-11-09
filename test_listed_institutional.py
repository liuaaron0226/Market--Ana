"""E3：上市三大法人（TWSE T86）原件解析、既有原件重用與專用資料庫契約。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import listed_institutional as li


ROOT = Path(__file__).resolve().parent

FIELDS = [
    "證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
    "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
    "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
    "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
    "自營商買賣超股數(避險)", "三大法人買賣超股數",
]
# 取自 2026-11-06 官方原件 00632R 一列（逐欄對照用）。
ROW_00632R = ["00632R", "元大台灣50反1   ", "11,931,500", "11,159,000", "772,500", "0", "0", "0", "0", "0", "0",
              "197,929,503", "6,766,000", "7,539,000", "-773,000", "238,122,026", "39,419,523", "198,702,503",
              "198,702,003"]
ROW_2330 = ["2330", "台積電", "1,000", "0", "1,000", "0", "0", "0", "500", "0", "500",
            "-200", "0", "100", "-100", "0", "100", "-100", "1,300"]


def t86_json(day: str = "20261106", rows: list[list[str]] | None = None, fields: list[str] | None = None,
             stat: str = "OK") -> bytes:
    rows = [ROW_00632R, ROW_2330] if rows is None else rows
    payload = {"stat": stat, "date": day, "title": "三大法人買賣超日報", "fields": fields or FIELDS,
               "data": rows, "selectType": "ALLBUT0999", "total": len(rows)}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def dump(db: Path) -> list[tuple]:
    connection = sqlite3.connect(db)
    try:
        return list(connection.execute("SELECT * FROM institutional ORDER BY date, code"))
    finally:
        connection.close()


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"", content_type: str = "application/json;charset=UTF-8"):
        self.status, self.content, self.content_type, self.url = status, content, content_type, "https://www.twse.com.tw/x"


class ParseTest(unittest.TestCase):
    def test_columns_match_raw_row(self):
        records = li.parse_t86_json(t86_json(), "20261106")
        row = next(r for r in records if r["code"] == "00632R")
        self.assertEqual(row["date"], "2026-11-06")
        self.assertEqual(row["name"], "元大台灣50反1")
        self.assertEqual(row["foreign_ex_dealer_net"], 772_500)
        self.assertEqual(row["foreign_dealer_net"], 0)
        self.assertEqual(row["foreign_net"], 772_500)
        self.assertEqual(row["trust_net"], 0)
        self.assertEqual(row["dealer_self_net"], -773_000)
        self.assertEqual(row["dealer_hedge_net"], 198_702_503)
        self.assertEqual(row["dealer_net"], 197_929_503)
        self.assertEqual(row["total_net"], 198_702_003)

    def test_components_sum_to_total(self):
        for row in li.parse_t86_json(t86_json(), "20261106"):
            self.assertEqual(row["foreign_net"] + row["trust_net"] + row["dealer_net"], row["total_net"])
            self.assertEqual(row["dealer_self_net"] + row["dealer_hedge_net"], row["dealer_net"])

    def test_missing_value_stays_none(self):
        row = list(ROW_2330)
        row[7] = "--"
        records = li.parse_t86_json(t86_json(rows=[row]), "20261106")
        self.assertIsNone(records[0]["foreign_dealer_net"])
        self.assertIsNone(records[0]["foreign_net"])
        self.assertEqual(records[0]["foreign_ex_dealer_net"], 1_000)

    def test_rejects_wrong_date_status_fields_and_count(self):
        with self.assertRaisesRegex(ValueError, "日期"):
            li.parse_t86_json(t86_json(day="20261105"), "20261106")
        with self.assertRaisesRegex(ValueError, "stat"):
            li.parse_t86_json(t86_json(stat="很抱歉，沒有符合條件的資料!"), "20261106")
        with self.assertRaisesRegex(ValueError, "欄位"):
            li.parse_t86_json(t86_json(fields=FIELDS[:-1] + ["其他"]), "20261106")
        payload = json.loads(t86_json())
        payload["total"] = 5
        with self.assertRaisesRegex(ValueError, "筆數"):
            li.parse_t86_json(json.dumps(payload).encode("utf-8"), "20261106")

    def test_unwraps_existing_snapshot_format(self):
        wrapped = json.dumps({"fetched_at_utc": "x", "source_url": "y", "data": json.loads(t86_json())}).encode("utf-8")
        self.assertEqual(len(li.parse_t86_json(wrapped, "20261106")), 2)


class FetchAndBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "listed-institutional"
        self.snapshots = Path(self.tmp.name) / "t86"
        self.snapshots.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _get(self, responses: dict[str, FakeResponse], calls: list[str]):
        def get(session, url, params):
            calls.append(params["date"])
            return responses[params["date"]]
        return get

    def test_fetch_skips_existing_snapshot_and_rate_limits(self):
        wrapped = {"fetched_at_utc": "x", "source_url": "y", "data": json.loads(t86_json(day="20261105"))}
        (self.snapshots / "t86-20261105.json").write_text(json.dumps(wrapped, ensure_ascii=False), encoding="utf-8")
        calls, sleeps = [], []
        responses = {d: FakeResponse(content=t86_json(day=d)) for d in ("20261104", "20261106")}
        summary = li.fetch(self.base, ["20261104", "20261105", "20261106"], snapshot_dir=self.snapshots,
                           get=self._get(responses, calls), sleep=sleeps.append)
        self.assertEqual(calls, ["20261104", "20261106"])
        self.assertEqual(sleeps, [li.MIN_INTERVAL_SECONDS])
        self.assertEqual(summary["fetched"], 2)
        self.assertEqual(summary["reused_snapshot"], 1)
        raw = self.base / "raw" / "20261104.json"
        sidecar = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()
        self.assertEqual(sidecar, [hashlib.sha256(raw.read_bytes()).hexdigest(), raw.name])

    def test_fetch_stops_on_429_and_bad_format(self):
        calls = []
        summary = li.fetch(self.base, ["20261104", "20261106"], snapshot_dir=self.snapshots,
                           get=self._get({"20261104": FakeResponse(status=429)}, calls), sleep=lambda s: None)
        self.assertEqual(calls, ["20261104"])
        self.assertIn("429", summary["stopped"]["reason"])
        summary = li.fetch(self.base, ["20261104"], snapshot_dir=self.snapshots,
                           get=self._get({"20261104": FakeResponse(content=t86_json(day="20261103"))}, []),
                           sleep=lambda s: None)
        self.assertIn("非預期格式", summary["stopped"]["reason"])
        self.assertFalse((self.base / "raw" / "20261104.json").exists())

    def test_build_is_idempotent_and_query_matches_otc_shape(self):
        wrapped = {"fetched_at_utc": "x", "source_url": "y", "data": json.loads(t86_json(day="20261105"))}
        (self.snapshots / "t86-20261105.json").write_text(json.dumps(wrapped, ensure_ascii=False), encoding="utf-8")
        li.fetch(self.base, ["20261105", "20261106"], snapshot_dir=self.snapshots,
                 get=self._get({"20261106": FakeResponse(content=t86_json())}, []), sleep=lambda s: None)
        db = self.base / "listed_institutional.sqlite"
        first = li.build_db(self.base, db, days=["20261105", "20261106"])
        dump1 = dump(db)
        second = li.build_db(self.base, db, days=["20261105", "20261106"])
        dump2 = dump(db)
        self.assertEqual(first, second)
        self.assertEqual(dump1, dump2)
        self.assertEqual(first["days"], 2)
        self.assertEqual(li.institutional_days(db), ["2026-11-05", "2026-11-06"])
        got = li.institutional(db, "2330", ["2026-11-06", "2026-11-04"])
        self.assertEqual(got[0], {"foreign": 1_000, "trust": 500, "dealer": -200, "total": 1_300})
        self.assertIsNone(got[1])

    def test_build_rejects_tampered_raw(self):
        li.fetch(self.base, ["20261106"], snapshot_dir=self.snapshots,
                 get=self._get({"20261106": FakeResponse(content=t86_json())}, []), sleep=lambda s: None)
        (self.base / "raw" / "20261106.json").write_bytes(t86_json(rows=[ROW_2330]))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            li.build_db(self.base, self.base / "x.sqlite", days=["20261106"])


@unittest.skipUnless((ROOT / "data/listed-institutional/listed_institutional.sqlite").exists(), "正式庫尚未建立")
class RealDataTest(unittest.TestCase):
    db = ROOT / "data/listed-institutional/listed_institutional.sqlite"

    def test_real_db_covers_otc_trading_days_and_sums(self):
        days = li.institutional_days(self.db)
        otc_days = sorted(f"{p.stem[:4]}-{p.stem[4:6]}-{p.stem[6:]}" for p in (ROOT / "data/otc-history/raw").glob("*.csv"))
        self.assertEqual(days, otc_days)
        connection = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        bad = connection.execute(
            "SELECT COUNT(*) FROM institutional WHERE foreign_net + trust_net + dealer_net != total_net"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(bad, 0)

    def test_real_2330_matches_snapshot_raw(self):
        raw = json.loads((ROOT / "data/market-snapshots/stock-detail/t86/t86-20261106.json").read_text(encoding="utf-8"))["data"]
        cell = dict(zip(raw["fields"], next(r for r in raw["data"] if r[0].strip() == "2330")))
        value = lambda name: int(cell[name].replace(",", ""))
        got = li.institutional(self.db, "2330", ["2026-11-06"])[0]
        self.assertEqual(got["foreign"], value("外陸資買賣超股數(不含外資自營商)") + value("外資自營商買賣超股數"))
        self.assertEqual(got["trust"], value("投信買賣超股數"))
        self.assertEqual(got["dealer"], value("自營商買賣超股數"))
        self.assertEqual(got["total"], value("三大法人買賣超股數"))


if __name__ == "__main__":
    unittest.main()
