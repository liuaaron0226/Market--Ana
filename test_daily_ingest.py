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
import otc_corporate_actions as oca
import otc_history as oh
import otc_market_data as omd
import stock_history as sh
import valuation_history as vh
from test_listed_institutional import ROW_2330 as T86_2330, t86_json
from test_otc_corporate_actions import ETF_DIVIDEND, payload as actions_payload
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

    def test_market_margin_summary_saved_for_homepage_cards(self) -> None:
        # 11-11 本人糾正首頁融資／融券卡停在 11-06：大盤信用交易總表（MI_MARGN selectType=MS）每天都要抓。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = {"stat": "OK", "date": "20261110", "tables": [{"title": "信用交易統計", "data": [["融資"]]}]}
            seen = []

            def get(url, params):
                seen.append(params.get("selectType"))
                return sh.Response(200, "application/json", json.dumps(summary).encode(), "u")
            done = di.listed_chip_detail_day(root, "2026-11-10", get=get, sleep=lambda s: None,
                                             datasets=("margin_summary",))
            self.assertEqual(done["margin_summary"]["status"], "saved")
            self.assertEqual(seen, ["MS"])
            saved = json.loads((root / "data/market-snapshots/margin/mi-margn-20261110.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["data"], summary)
            self.assertIn("margin_summary", di.CHIP_SOURCES)

    def test_default_chip_datasets_include_market_margin_summary(self) -> None:
        import inspect
        default = inspect.signature(di.listed_chip_detail_day).parameters["datasets"].default
        self.assertIn("margin_summary", default)

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


class OtcCorporateActionsTest(unittest.TestCase):
    def test_daily_fetch_merges_official_event_without_changing_listed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "data/corporate-actions/events.json"
            events.parent.mkdir(parents=True)
            listed = {"date": "2025-01-02", "code": "2330", "type": "除息", "close_before": 1000.0,
                      "reference": 995.0, "value": 5.0, "limit_up": 1094.0, "limit_down": 896.0,
                      "source": "TWT49U-202501.json.gz"}
            events.write_text(json.dumps([listed], ensure_ascii=False), encoding="utf-8")
            calls = []

            def get(session, url, params):
                calls.append(params)
                return oca.Response(200, "application/json", actions_payload(ETF_DIVIDEND), url)

            result = di.otc_corporate_actions_day(root, "2025-01-02", get=get, sleep=lambda _: None)
            self.assertEqual(result["status"], "appended")
            self.assertEqual(result["events"]["otc"], 1)
            merged = json.loads(events.read_text(encoding="utf-8"))
            self.assertEqual(next(e for e in merged if e["code"] == "2330"), listed)
            self.assertEqual(next(e for e in merged if e["code"] == "00950B")["value"], 0.08)
            self.assertEqual(len(calls), 1)
            again = di.otc_corporate_actions_day(root, "2025-01-02", get=get, sleep=lambda _: None)
            self.assertEqual(again["status"], "already-present")
            self.assertEqual(len(calls), 1)

    def test_corrupt_meta_stops_without_refetching_or_removing_existing_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "data/corporate-actions/events.json"
            events.parent.mkdir(parents=True)
            existing = oca.parse_exdailyq(actions_payload(ETF_DIVIDEND), source="TPEx-exDailyQ-seed.json")
            events.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            raw_dir = events.parent / "raw/TPEx-exDailyQ"
            oca.fetch_periods(raw_dir, [("seed", "2025-01-01", "2025-01-02")],
                              get=lambda *_: oca.Response(200, "application/json", actions_payload(ETF_DIVIDEND), "u"),
                              sleep=lambda _: None)
            (raw_dir / "meta.json").write_text("{broken", encoding="utf-8")
            before = events.read_bytes()
            calls = []
            def should_not_fetch(*_):
                calls.append(1)
                return oca.Response(200, "application/json", actions_payload(), "u")

            result = di.otc_corporate_actions_day(root, "2025-01-06", get=should_not_fetch, sleep=lambda _: None)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(calls, [])
            self.assertEqual(events.read_bytes(), before)
            self.assertEqual((raw_dir / "meta.json").read_text(encoding="utf-8"), "{broken")

    def test_daily_fetch_extends_coverage_over_non_trading_calendar_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "data/corporate-actions/events.json"
            events.parent.mkdir(parents=True)
            events.write_text("[]", encoding="utf-8")
            raw_dir = events.parent / "raw/TPEx-exDailyQ"
            oca.fetch_periods(raw_dir, [("seed", "2025-01-01", "2025-01-02")],
                              get=lambda *_: oca.Response(200, "application/json", actions_payload(), "u"),
                              sleep=lambda _: None)
            calls = []

            def get(session, url, params):
                calls.append(params)
                return oca.Response(200, "application/json", actions_payload(), "u")

            di.otc_corporate_actions_day(root, "2025-01-06", get=get, sleep=lambda _: None)
            self.assertEqual((calls[0]["startDate"], calls[0]["endDate"]), ("2025/01/03", "2025/01/06"))
            self.assertTrue(oca.coverage_covers(oca.verified_coverage(raw_dir), "2025-01-01", "2025-01-06"))


class CapitalChangeSourcesTest(unittest.TestCase):
    """還原價需要的官方資料每天跟上：上市公司行動（events.json）與補充資料（capital-supplement.json）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.events = self.root / "data/corporate-actions/events.json"
        self.events.parent.mkdir(parents=True)
        self.events.write_text("[]", encoding="utf-8")
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params):
        from stock_history import Response
        from test_capital_supplement import (PVCHG_FIELDS, REVIVT_FIELDS, ROW_0050, ROW_2464_DETAIL, TWTCAU_FIELDS,
                                             detail, tpex, twse)
        from test_corporate_actions import TWT49U_FIELDS
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, dict(params)))
        mixed = ["115年09月02日", "2464", "盟立", "184.50", "182.30", "2.192145", "權息", "202.00", "164.50", "184.00",
                 "184.02", "2464,20260902", "", "", ""]
        body = {"TWT49U": twse(TWT49U_FIELDS, [mixed]), "TWTCAU": twse(TWTCAU_FIELDS, [ROW_0050]),
                "revivt": tpex(REVIVT_FIELDS, []), "pvChgRslt": tpex(PVCHG_FIELDS, []),
                "etfSplitRslt": tpex(PVCHG_FIELDS, []), "TWT49UDetail": detail([ROW_2464_DETAIL]),
                }.get(name, json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}, ensure_ascii=False).encode("utf-8"))
        return Response(200, "application/json", body, url)

    def test_listed_actions_are_refreshed_for_a_window_around_the_data_day(self) -> None:
        result = di.listed_corporate_actions_day(self.root, "2026-09-10", get=self.get, sleep=lambda _: None)
        self.assertEqual((result["status"], result["requests"]), ("refreshed", 3))
        self.assertEqual({(name, p["startDate"], p["endDate"]) for name, p in self.calls},
                         {(name, "20260727", "20261010") for name in ("TWT49U", "TWTB8U", "TWTAUU")})
        [event] = json.loads(self.events.read_text(encoding="utf-8"))
        self.assertEqual((event["code"], event["type"], event["source"]), ("2464", "除權息", "TWT49U-202609.json.gz"))

    def test_supplement_follows_with_splits_and_the_cash_part_of_new_listed_ex_rights(self) -> None:
        di.listed_corporate_actions_day(self.root, "2026-09-10", get=self.get, sleep=lambda _: None)
        self.calls.clear()
        result = di.capital_supplement_day(self.root, "2026-09-10", get=self.get, sleep=lambda _: None)
        self.assertEqual((result["status"], result["requests"]), ("refreshed", 5))
        saved = json.loads((self.root / "data/corporate-actions/capital-supplement.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["cash_dividends"], {"2464|2026-09-02": 0.471403})
        self.assertEqual(saved["events"], [])  # 0050 的分割在 2025-06，不在這次的視窗內
        again = di.capital_supplement_day(self.root, "2026-09-10", get=self.get, sleep=lambda _: None)
        self.assertEqual(again["requests"], 4)

    def test_a_refusal_is_reported_as_stopped_and_never_raises(self) -> None:
        from stock_history import Response
        refused = lambda url, params: Response(403, "text/html", b"", url)  # noqa: E731
        self.assertEqual(di.listed_corporate_actions_day(self.root, "2026-09-10", get=refused, sleep=lambda _: None)["status"],
                         "stopped")
        self.assertEqual(di.capital_supplement_day(self.root, "2026-09-10", get=refused, sleep=lambda _: None)["status"],
                         "stopped")


class BigHolderSourceTest(unittest.TestCase):
    """集保大戶持股每週一份（本人 2026-11-14「可以，用集保」）：這一週已經存了就不再下載。"""

    def test_weekly_file_is_fetched_once_per_week(self) -> None:
        from stock_history import Response
        from test_tdcc_holders import ROWS_2330, csv
        calls = []

        def get(url, params):
            calls.append((url, dict(params)))
            return Response(200, "text/csv", csv(ROWS_2330), url)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = di.tdcc_holders_day(root, "2026-11-16", get=get)   # 週一：最近一個週五是 11-13
            self.assertEqual((first["status"], first["data_date"], first["requests"]), ("appended", "2026-11-13", 1))
            self.assertEqual(calls, [("https://opendata.tdcc.com.tw/getOD.ashx", {"id": "1-5"})])
            self.assertTrue((root / "data/tdcc-holders/weekly/2026-11-13.json").is_file())
            again = di.tdcc_holders_day(root, "2026-11-18", get=get)   # 同一週稍後：不再下載
            self.assertEqual((again["status"], again["requests"]), ("already-present", 0))
            self.assertEqual(len(calls), 1)
            di.tdcc_holders_day(root, "2026-11-20", get=get)           # 新的週五：再問一次
            self.assertEqual(len(calls), 2)


class TradingCalendarTest(unittest.TestCase):
    def test_parses_official_holiday_schedule_roc_dates(self) -> None:
        payload = [{"Name": "國慶日", "Date": "1151009", "Weekday": "五", "Description": ""},
                   {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五", "Description": "國曆新年開始交易。"},
                   {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Weekday": "四", "Description": ""}]
        holidays = di.parse_holiday_schedule(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertIn("2026-10-09", holidays)
        self.assertIn("2026-02-12", holidays)
        self.assertNotIn("2026-01-02", holidays)  # 「開始交易日」是交易日，不是休市


class OtcCreditTest(unittest.TestCase):
    """個股頁事實區塊 E 票：上櫃融資融券與借券。最近 20 個交易日缺哪天補哪天（本人 11-16「補抓 20 個交易日」）。"""

    MARGIN_FIELDS = ['代號', '名稱', '前資餘額(張)', '資買', '資賣', '現償', '資餘額', '資屬證金', '資使用率(%)', '資限額',
                     '前券餘額(張)', '券賣', '券買', '券償', '券餘額', '券屬證金', '券使用率(%)', '券限額', '資券相抵(張)', '備註']
    SBL_FIELDS = ['股票代號', '股票名稱', '前日餘額', '賣出', '買進', '現券', '當日餘額', '限額', '前日餘額', '當日賣出',
                  '當日還券', '當日調整數額', '當日餘額', '次一營業日可借券賣出限額', '備註']

    class Response:
        def __init__(self, status, payload=None):
            self.status, self.content_type = status, 'application/json;charset=UTF-8'
            self.content = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
            self.url = ''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        db = self.root / 'data/otc-history/otc_history.sqlite'
        db.parent.mkdir(parents=True)
        connection = sqlite3.connect(db)
        connection.execute('CREATE TABLE daily (date TEXT, code TEXT)')
        for day in ('2026-11-10', '2026-11-11', '2026-11-12'):
            connection.execute("INSERT INTO daily VALUES (?, '6488')", (day,))
        connection.commit()
        connection.close()

    def payload(self, kind, compact, empty=False):
        fields = self.MARGIN_FIELDS if kind == 'margin' else self.SBL_FIELDS
        row = ['6488', '環球晶'] + ['1,000'] * (len(fields) - 2)
        return {'stat': 'ok', 'date': compact, 'tables': [{'fields': fields, 'data': [] if empty else [row]}]}

    def run_day(self, day, *, unpublished=(), blocked_after=None, backfill_days=20):
        calls, waits = [], []

        def get(_session, url, params):
            calls.append((url, params['date']))
            if blocked_after is not None and len(calls) > blocked_after:
                return self.Response(403)
            compact = params['date'].replace('/', '')
            kind = 'margin' if url.endswith('/balance') else 'sbl'
            return self.Response(200, self.payload(kind, compact, empty=compact in unpublished))
        result = di.otc_credit_day(self.root, day, get=get, sleep=waits.append, backfill_days=backfill_days)
        return result, calls, waits

    def files(self):
        return sorted(str(p.relative_to(self.root / 'data/otc-credit')).replace('\\', '/')
                      for p in (self.root / 'data/otc-credit').rglob('*.json'))

    def test_first_run_backfills_the_recent_trading_days_then_only_what_is_missing(self):
        result, calls, waits = self.run_day('2026-11-12')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['saved'], 6)                     # 3 個交易日 × 融資融券、借券
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(waits), 5)                          # 同一個網域：請求之間都等
        self.assertEqual(self.files(), ['margin/20261110.json', 'margin/20261111.json', 'margin/20261112.json',
                                        'sbl/20261110.json', 'sbl/20261111.json', 'sbl/20261112.json'])
        again, calls_again, _ = self.run_day('2026-11-12')
        self.assertEqual((again['saved'], len(calls_again)), (0, 0))

    def test_backfill_is_limited_to_the_most_recent_days(self):
        result, calls, _ = self.run_day('2026-11-12', backfill_days=2)
        self.assertEqual(sorted({date for _, date in calls}), ['2026/11/11', '2026/11/12'])

    def test_a_day_not_published_yet_is_not_written_and_is_named(self):
        result, _, _ = self.run_day('2026-11-12', unpublished={'20261112'})
        self.assertEqual(result['not_published'], ['2026-11-12 margin', '2026-11-12 sbl'])
        self.assertNotIn('margin/20261112.json', self.files())

    def test_a_refusal_stops_the_run_and_keeps_what_was_saved(self):
        result, calls, _ = self.run_day('2026-11-12', blocked_after=2)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.files()), 2)

    def test_a_changed_format_or_wrong_day_stops_without_writing(self):
        def run(payload_fix):
            calls = []

            def get(_session, url, params):
                calls.append(url)
                compact = params['date'].replace('/', '')
                payload = self.payload('margin' if url.endswith('/balance') else 'sbl', compact)
                payload_fix(payload)
                return self.Response(200, payload)
            return di.otc_credit_day(self.root, '2026-11-12', get=get, sleep=lambda s: None, backfill_days=1)
        renamed = run(lambda p: p['tables'][0].__setitem__('fields', ['代號', '名稱', '餘額']))
        self.assertEqual(renamed['status'], 'stopped')
        self.assertIn('欄位', renamed['reason'])
        wrong_day = run(lambda p: p.__setitem__('date', '20261111'))
        self.assertIn('資料日不符', wrong_day['reason'])
        no_day = run(lambda p: p.pop('date'))
        self.assertEqual(no_day['status'], 'stopped')
        self.assertEqual(self.files(), [])

    def test_a_connection_error_stops_and_keeps_what_was_saved(self):
        calls = []

        def get(_session, url, params):
            calls.append(url)
            if len(calls) == 2:
                raise TimeoutError('slow')
            compact = params['date'].replace('/', '')
            return self.Response(200, self.payload('margin' if url.endswith('/balance') else 'sbl', compact))
        result = di.otc_credit_day(self.root, '2026-11-12', get=get, sleep=lambda s: None)
        self.assertEqual(result['status'], 'stopped')
        self.assertIn('TimeoutError', result['reason'])
        self.assertEqual(result['saved'], 1)

    def test_raw_files_carry_fetch_time_source_and_hash(self):
        self.run_day('2026-11-12', backfill_days=1)
        raw = json.loads((self.root / 'data/otc-credit/margin/20261112.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(raw), ['data', 'fetched_at_utc', 'sha256', 'source_url'])
        self.assertEqual(raw['data']['date'], '20261112')
        self.assertEqual(len(raw['sha256']), 64)

    def test_it_is_an_optional_dataset(self):
        self.assertIn('otc_credit', di.OPTIONAL_DATASETS)


if __name__ == "__main__":
    unittest.main()
