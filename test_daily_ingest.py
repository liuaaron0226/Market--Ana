"""daily_ingest：每日增量抓取與建庫的契約測試。

重點：
- 增量附加的結果必須和「由全部原件整庫重建」逐列相同（不另寫一套解析）。
- 同一天重跑不重複寫入（冪等）；原件已存在就不發請求。
- 官方「無資料」（尚未公布或休市）不寫原件、不當成 0，也不停整批，回報 not_published。
- 每個資料集只抓指定交易日；月營收與季報依法定截止日判斷該不該抓。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import daily_ingest as di
import listed_institutional as li
import otc_history as oh
import otc_market_data as omd
import stock_history as sh
import valuation_history as vh
from test_listed_institutional import ROW_2330 as T86_2330, t86_json
from test_otc_history import csv_bytes
from test_otc_market_data import index_json, inst_csv, val_csv
from test_stock_history import ROW_0050, ROW_2330, official_payload
from test_valuation_history import FIELDS_NEW, payload as bwibbu_payload

OTC_6488 = ["6488", "環球晶", "564.00", "-55.00 ", "601.00", "619.00", "558.00", "581.36",
            "18,953,974", "11,018,999,667", "40,954", "563.00", "67", "564.00", "76",
            "528,113,725", "564.00", "620.00", "508.00"]


def dump(db: Path, table: str, order: str) -> list[tuple]:
    connection = sqlite3.connect(db)
    try:
        return list(connection.execute(f"SELECT * FROM {table} ORDER BY {order}"))
    finally:
        connection.close()


class FakeCatalog:
    def __init__(self, codes: set[str]) -> None:
        self.codes = codes

    def lookup(self, code: str):
        return object() if code in self.codes else None


class Recorder:
    """依 (url 結尾, 日期參數) 回應；記下每次請求。"""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def two_arg(self, url, params):  # stock_history 形狀
        key = (url.rsplit("/", 1)[-1], str(params.get("date") or params.get("d")))
        self.calls.append(key)
        return self.responses[key]

    def three_arg(self, session, url, params):  # otc_*／listed_institutional／valuation 形狀
        return self.two_arg(url, params)


class ListedQuotesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ok(self, day: str):
        return sh.Response(200, "application/json", official_payload(day, [ROW_0050, ROW_2330]), "u")

    def test_append_matches_full_rebuild_and_is_idempotent(self) -> None:
        rec = Recorder({("MI_INDEX", d): self._ok(d) for d in ("20261105", "20261106")})
        sh.fetch_history(self.out, ["20261105"], get=rec.two_arg, sleep=lambda s: None)
        db = self.out / "stock-history.sqlite"
        sh.build_database(self.out, db)
        result = di.listed_quotes_day(self.out, db, "2026-11-06", get=rec.two_arg, sleep=lambda s: None)
        self.assertEqual(result["status"], "appended")
        again = di.listed_quotes_day(self.out, db, "2026-11-06", get=rec.two_arg, sleep=lambda s: None)
        self.assertEqual(again["status"], "already-present")
        self.assertEqual(rec.calls, [("MI_INDEX", "20261105"), ("MI_INDEX", "20261106")])
        full = self.out / "full.sqlite"
        sh.build_database(self.out, full)
        self.assertEqual(dump(db, "daily", "date, code"), dump(full, "daily", "date, code"))
        self.assertEqual(dump(db, "source_days", "date"), dump(full, "source_days", "date"))

    def test_not_published_writes_nothing_and_does_not_stop(self) -> None:
        empty = sh.Response(200, "application/json", json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode(), "u")
        rec = Recorder({("MI_INDEX", "20261109"): empty})
        db = self.out / "stock-history.sqlite"
        result = di.listed_quotes_day(self.out, db, "2026-11-09", get=rec.two_arg, sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertFalse(sh.raw_file(self.out, "20261109").exists())
        self.assertFalse(db.exists())

    def test_rate_limit_is_reported_as_stopped(self) -> None:
        rec = Recorder({("MI_INDEX", "20261106"): sh.Response(429, "text/html", b"", "u")})
        result = di.listed_quotes_day(self.out, self.out / "x.sqlite", "2026-11-06", get=rec.two_arg,
                                      sleep=lambda s: None)
        self.assertEqual(result["status"], "stopped")
        self.assertIn("429", result["reason"])


class OtcQuotesTest(unittest.TestCase):
    def test_append_matches_full_rebuild_and_respects_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            catalog = FakeCatalog({"6488"})
            other = ["9999", "非名錄"] + OTC_6488[2:]
            responses = {("dailyQuotes", f"2026/11/0{d[-1]}"): oh.Response(
                200, "application/csv;charset=MS950", csv_bytes(OTC_6488, other, day=f"115/11/0{d[-1]}"), "u")
                for d in ("20261105", "20261106")}
            rec = Recorder(responses)
            oh.fetch_history(out, ["20261105"], get=rec.three_arg, sleep=lambda s: None)
            db = out / "otc_history.sqlite"
            oh.build_database(out, db, catalog)
            result = di.otc_quotes_day(out, db, "2026-11-06", catalog, get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "appended")
            self.assertEqual(di.otc_quotes_day(out, db, "2026-11-06", catalog, get=rec.three_arg,
                                               sleep=lambda s: None)["status"], "already-present")
            full = out / "full.sqlite"
            oh.build_database(out, full, catalog)
            self.assertEqual(dump(db, "daily", "date, code"), dump(full, "daily", "date, code"))
            self.assertEqual(dump(db, "source_days", "date"), dump(full, "source_days", "date"))
            self.assertNotIn("9999", {row[1] for row in dump(db, "daily", "date, code")})

    def test_empty_official_day_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            rec = Recorder({("dailyQuotes", "2026/11/09"): oh.Response(
                200, "application/csv;charset=MS950", csv_bytes(day="115/11/09"), "u")})
            result = di.otc_quotes_day(out, out / "otc.sqlite", "2026-11-09", FakeCatalog(set()),
                                       get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "not_published")
            self.assertFalse((out / "raw" / "20261109.csv").exists())


class ListedInstitutionalTest(unittest.TestCase):
    def test_append_matches_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "li"
            snapshots = Path(tmp) / "t86"
            snapshots.mkdir()
            rec = Recorder({("T86", d): _Resp(t86_json(day=d, rows=[T86_2330])) for d in ("20261105", "20261106")})
            li.fetch(base, ["20261105"], snapshot_dir=snapshots, get=rec.three_arg, sleep=lambda s: None)
            db = base / "listed_institutional.sqlite"
            li.build_db(base, db)
            result = di.listed_institutional_day(base, db, "2026-11-06", snapshot_dir=snapshots,
                                                 get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "appended")
            full = Path(tmp) / "full.sqlite"
            li.build_db(base, full)
            self.assertEqual(dump(db, "institutional", "date, code"), dump(full, "institutional", "date, code"))
            self.assertEqual(dump(db, "source_files", "raw_file"), dump(full, "source_files", "raw_file"))

    def test_no_data_stat_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "li"
            snapshots = Path(tmp) / "t86"
            snapshots.mkdir()
            empty = json.dumps({"stat": "很抱歉，沒有符合條件的資料!", "total": 0}, ensure_ascii=False).encode()
            rec = Recorder({("T86", "20261109"): _Resp(empty)})
            result = di.listed_institutional_day(base, base / "x.sqlite", "2026-11-09", snapshot_dir=snapshots,
                                                 get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "not_published")
            self.assertFalse((base / "raw" / "20261109.json").exists())


class _Resp:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/json;charset=UTF-8"):
        self.status, self.content, self.content_type, self.url = status, content, content_type, "u"


class OtcMarketTest(unittest.TestCase):
    def test_institutional_and_index_append_match_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            otc_dir = Path(tmp)
            inst_dir, index_dir = otc_dir / "raw-institutional", otc_dir / "raw-index"
            csv_type = "application/csv;charset=MS950"
            rec = Recorder({
                ("dailyTrade", "2026/11/05"): _Resp(inst_csv(day_title="115年11月05日"), content_type=csv_type),
                ("dailyTrade", "2026/11/06"): _Resp(inst_csv(day_title="115年11月06日"), content_type=csv_type),
                ("Inx_result.php", "115/10"): _Resp(index_json("115/10")),
                ("Inx_result.php", "115/11"): _Resp(index_json("115/11", rows=[
                    ["2026/11/05", "300.00", "305.00", "299.00", "304.00", "1.00"],
                    ["2026/11/06", "304.00", "305.00", "300.00", "304.02", "0.02"]])),
            })
            omd.fetch_dataset("institutional", inst_dir, ["20261105"], get=rec.three_arg, sleep=lambda s: None)
            omd.fetch_dataset("index", index_dir, ["202610"], get=rec.three_arg, sleep=lambda s: None)
            db = otc_dir / "otc_market.sqlite"
            omd.build_market_db(otc_dir, db)
            result = di.otc_market_day(otc_dir, db, "2026-11-06", get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["institutional"]["status"], "appended")
            self.assertEqual(result["index"]["status"], "refreshed")
            full = otc_dir / "full.sqlite"
            omd.build_market_db(otc_dir, full)
            self.assertEqual(dump(db, "institutional", "date, code"), dump(full, "institutional", "date, code"))
            self.assertEqual(dump(db, "tpex_index", "date"), dump(full, "tpex_index", "date"))
            self.assertEqual(dump(db, "source_files", "dataset, raw_file"),
                             dump(full, "source_files", "dataset, raw_file"))

    def test_current_month_index_is_refetched_so_a_new_day_is_not_missed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            otc_dir = Path(tmp)
            index_dir = otc_dir / "raw-index"
            early = index_json("115/11", rows=[["2026/11/05", "300.00", "305.00", "299.00", "304.00", "1.00"]])
            later = index_json("115/11", rows=[["2026/11/05", "300.00", "305.00", "299.00", "304.00", "1.00"],
                                               ["2026/11/06", "304.00", "305.00", "300.00", "304.02", "0.02"]])
            omd.fetch_dataset("index", index_dir, ["202611"], get=Recorder(
                {("Inx_result.php", "115/11"): _Resp(early)}).three_arg, sleep=lambda s: None)
            rows = di.refresh_otc_index_month(index_dir, "2026-11-06", get=Recorder(
                {("Inx_result.php", "115/11"): _Resp(later)}).three_arg, sleep=lambda s: None)
            self.assertEqual(rows["status"], "refreshed")
            raw = index_dir / "raw" / "202611.json"
            self.assertEqual(raw.read_bytes(), later)
            sidecar = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()
            self.assertEqual(sidecar, [hashlib.sha256(later).hexdigest(), raw.name])
            meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(next(f for f in meta["files"] if f["key"] == "202611")["sha256"], sidecar[0])

    def test_index_without_requested_day_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "raw-index"
            only_5th = index_json("115/11", rows=[["2026/11/05", "300.00", "305.00", "299.00", "304.00", "1.00"]])
            result = di.refresh_otc_index_month(index_dir, "2026-11-06", get=Recorder(
                {("Inx_result.php", "115/11"): _Resp(only_5th)}).three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "not_published")
            self.assertFalse((index_dir / "raw" / "202611.json").exists())


class ValuationTest(unittest.TestCase):
    def test_twse_append_matches_build_twse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "twse"
            rows = [["2330", "台積電", "1000", "1.5", 114, "25.00", "7.00", "115/2"]]
            rec = Recorder({("BWIBBU_d", d): vh.Response(200, "application/json", bwibbu_payload(d, FIELDS_NEW, rows), "u")
                            for d in ("20261105", "20261106")})
            vh.fetch_history(out, ["20261105"], get=rec.three_arg, sleep=lambda s: None)
            db = Path(tmp) / "valuation.sqlite"
            vh.build_twse(out, db)
            result = di.twse_valuation_day(out, db, "2026-11-06", get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "appended")
            full = Path(tmp) / "full.sqlite"
            vh.build_twse(out, full)
            self.assertEqual(dump(db, "valuation", "market, date, code"), dump(full, "valuation", "market, date, code"))
            self.assertEqual(dump(db, "source_days", "market, date"), dump(full, "source_days", "market, date"))

    def test_tpex_append_keeps_listed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            twse = Path(tmp) / "twse"
            tpex = Path(tmp) / "tpex"
            db = Path(tmp) / "valuation.sqlite"
            rows = [["2330", "台積電", "1000", "1.5", 114, "25.00", "7.00", "115/2"]]
            vh.fetch_history(twse, ["20261106"], get=Recorder({("BWIBBU_d", "20261106"): vh.Response(
                200, "application/json", bwibbu_payload("20261106", FIELDS_NEW, rows), "u")}).three_arg,
                sleep=lambda s: None)
            vh.build_twse(twse, db)
            before = [r for r in dump(db, "valuation", "market, date, code") if r[0] == "上市"]
            rec = Recorder({("peQryDate", "2026/11/06"): _Resp(val_csv(), content_type="application/csv;charset=MS950")})
            result = di.tpex_valuation_day(tpex, db, "2026-11-06", get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual(result["status"], "appended")
            after = dump(db, "valuation", "market, date, code")
            self.assertEqual([r for r in after if r[0] == "上市"], before)
            self.assertEqual({r[2] for r in after if r[0] == "上櫃"}, {"6488", "9949"})
            self.assertIsNone(next(r for r in after if r[2] == "9949")[7])  # N/A 本益比保留 None


class FundamentalsScheduleTest(unittest.TestCase):
    def test_monthly_revenue_due_after_tenth_and_quarter_after_statutory_deadline(self) -> None:
        # 與 fundamentals_history.available_on 同口徑：2026 起月營收保守用 15 日（11/15 週日順延 11/16）。
        self.assertEqual(di.due_fundamentals(date(2026, 11, 13))["monthly"], ["2026-08", "2026-09"])
        self.assertEqual(di.due_fundamentals(date(2026, 11, 16))["monthly"], ["2026-09", "2026-10"])
        # Q3 季報截止 11/14（週六順延至 11/16）：之前不抓。
        self.assertNotIn("2026-Q3", di.due_fundamentals(date(2026, 11, 13))["quarters"])
        self.assertIn("2026-Q3", di.due_fundamentals(date(2026, 11, 16))["quarters"])

    def test_missing_periods_only_lists_what_meta_lacks(self) -> None:
        meta = {"files": [
            {"dataset": "monthly_revenue", "market": "listed", "period": "2026-09", "source_scope": "domestic"},
            {"dataset": "monthly_revenue", "market": "otc", "period": "2026-09", "source_scope": "domestic"},
            {"dataset": "monthly_revenue", "market": "listed", "period": "2026-09", "source_scope": "foreign"},
            {"dataset": "monthly_revenue", "market": "otc", "period": "2026-09", "source_scope": "foreign"},
        ]}
        missing = di.missing_fundamentals(meta, {"monthly": ["2026-09", "2026-10"], "quarters": []})
        self.assertEqual(missing, {"monthly": ["2026-10"], "quarters": []})


class ChipDetailTest(unittest.TestCase):
    """個股頁上市籌碼（twon_analysis.chips）讀 stock-detail 的 t86／margin 與 fundamentals/twt93u，格式要相容。"""

    def test_t86_detail_copied_from_listed_raw_without_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data/listed-institutional/raw/20261106.json"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(t86_json(day="20261106", rows=[T86_2330]))
            rec = Recorder({})
            result = di.listed_chip_detail_day(root, "2026-11-06", get=rec.two_arg, sleep=lambda s: None,
                                               datasets=("t86",))
            self.assertEqual(result["t86"]["status"], "copied")
            self.assertEqual(rec.calls, [])
            wrapped = json.loads((root / "data/market-snapshots/stock-detail/t86/t86-20261106.json").read_text(
                encoding="utf-8"))
            self.assertEqual(wrapped["data"], json.loads(raw.read_bytes()))

    def test_t86_detail_from_already_wrapped_raw_is_not_double_wrapped(self) -> None:
        # 由既有快照複製進來的上市法人原件本身已包一層 {"data": ...}；chips 只解一層。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data/listed-institutional/raw/20261106.json"
            raw.parent.mkdir(parents=True)
            inner = json.loads(t86_json(day="20261106", rows=[T86_2330]))
            raw.write_text(json.dumps({"fetched_at_utc": "x", "source_url": "y", "data": inner}, ensure_ascii=False),
                           encoding="utf-8")
            di.listed_chip_detail_day(root, "2026-11-06", get=Recorder({}).two_arg, datasets=("t86",))
            wrapped = json.loads((root / "data/market-snapshots/stock-detail/t86/t86-20261106.json").read_text(
                encoding="utf-8"))
            self.assertEqual(wrapped["data"], inner)

    def test_margin_and_borrow_written_in_existing_formats_and_no_data_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            margin = {"stat": "OK", "date": "20261106", "tables": [{"fields": ["代號"], "data": []}]}
            borrow = {"stat": "OK", "date": "20261106", "fields": ["代號"], "data": [["2330"]]}
            empty = {"stat": "很抱歉，沒有符合條件的資料!", "total": 0}
            rec = Recorder({
                ("MI_MARGN", "20261106"): sh.Response(200, "application/json", json.dumps(margin).encode(), "u"),
                ("TWT93U", "20261106"): sh.Response(200, "application/json", json.dumps(borrow).encode(), "u"),
                ("MI_MARGN", "20261109"): sh.Response(200, "application/json", json.dumps(empty).encode(), "u"),
                ("TWT93U", "20261109"): sh.Response(200, "application/json", json.dumps(empty).encode(), "u"),
            })
            sleeps: list[float] = []
            done = di.listed_chip_detail_day(root, "2026-11-06", get=rec.two_arg, sleep=sleeps.append,
                                             datasets=("margin", "twt93u"))
            self.assertEqual((done["margin"]["status"], done["twt93u"]["status"]), ("saved", "saved"))
            self.assertEqual(sleeps, [5.0])
            saved = json.loads((root / "data/market-snapshots/stock-detail/margin/margin-20261106.json").read_text(
                encoding="utf-8"))
            self.assertEqual(saved["data"], margin)
            self.assertEqual(json.loads((root / "data/fundamentals/twt93u/twt93u-20261106.json").read_bytes()), borrow)
            later = di.listed_chip_detail_day(root, "2026-11-09", get=rec.two_arg, sleep=sleeps.append,
                                              datasets=("margin", "twt93u"))
            self.assertEqual((later["margin"]["status"], later["twt93u"]["status"]), ("not_published", "not_published"))
            self.assertFalse((root / "data/market-snapshots/stock-detail/margin/margin-20261109.json").exists())
            again = di.listed_chip_detail_day(root, "2026-11-06", get=rec.two_arg, sleep=sleeps.append,
                                              datasets=("margin", "twt93u"))
            self.assertEqual((again["margin"]["status"], again["twt93u"]["status"]), ("existing", "existing"))


class _FakeRequestsResponse:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.status_code, self.content, self.url = 200, content, "u"
        self.headers = {"content-type": content_type}


class DefaultGetterTest(unittest.TestCase):
    """不注入 get 時，閘道探測要走真實 HTTP 路徑（11-09 首次真抓：探測把 None 當 session 傳給模組 http_get）。

    只 patch requests.get／Session.get，不碰網路；斷言探測確實發出請求，而不是 AttributeError。
    """

    def _patch(self, content: bytes, content_type: str):
        import requests
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(args[-1] if args else kwargs.get("url"))
            return _FakeRequestsResponse(content, content_type)

        originals = (requests.get, requests.Session.get)
        requests.get = lambda url, **kw: fake_get(url, **kw)
        requests.Session.get = lambda self, url, **kw: fake_get(url, **kw)
        self.addCleanup(lambda: (setattr(requests, "get", originals[0]),
                                 setattr(requests.Session, "get", originals[1])))
        return calls

    def test_listed_institutional_probe_without_injected_get(self) -> None:
        empty = json.dumps({"stat": "很抱歉，沒有符合條件的資料!", "total": 0}, ensure_ascii=False).encode()
        calls = self._patch(empty, "application/json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "li"
            snapshots = Path(tmp) / "t86"
            snapshots.mkdir()
            result = di.listed_institutional_day(base, base / "x.sqlite", "2026-11-09", snapshot_dir=snapshots,
                                                 sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertEqual(len(calls), 1)

    def test_twse_valuation_probe_without_injected_get(self) -> None:
        empty = json.dumps({"stat": "很抱歉，沒有符合條件的資料!", "total": 0}, ensure_ascii=False).encode()
        calls = self._patch(empty, "application/json")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "twse"
            result = di.twse_valuation_day(out, Path(tmp) / "v.sqlite", "2026-11-09", sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertEqual(len(calls), 1)

    def test_otc_quotes_probe_without_injected_get(self) -> None:
        header_only = "資料日期:115/11/09\r\n代號,名稱,收盤\r\n".encode("cp950")
        calls = self._patch(header_only, "text/csv")
        with tempfile.TemporaryDirectory() as tmp:
            result = di.otc_quotes_day(Path(tmp) / "otc", Path(tmp) / "o.sqlite", "2026-11-09",
                                       FakeCatalog({"6488"}), sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertEqual(len(calls), 1)

    def test_otc_institutional_probe_without_injected_get(self) -> None:
        empty = json.dumps({"stat": "無資料可供下載", "tables": []}, ensure_ascii=False).encode()
        calls = self._patch(empty, "application/json")
        with tempfile.TemporaryDirectory() as tmp:
            result = di.otc_institutional_day(Path(tmp) / "inst", Path(tmp) / "m.sqlite", "2026-11-09",
                                              sleep=lambda s: None)
        self.assertEqual(result["status"], "not_published")
        self.assertEqual(len(calls), 1)


class TradingCalendarTest(unittest.TestCase):
    def test_parses_official_holiday_schedule_roc_dates(self) -> None:
        payload = [{"Name": "國慶日", "Date": "1151009", "Weekday": "五", "Description": ""},
                   {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五", "Description": "國曆新年開始交易。"},
                   {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Weekday": "四", "Description": ""}]
        holidays = di.parse_holiday_schedule(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertIn("2026-10-09", holidays)
        self.assertIn("2026-02-12", holidays)
        self.assertNotIn("2026-01-02", holidays)  # 「開始交易日」是交易日，不是休市


if __name__ == "__main__":
    unittest.main()
