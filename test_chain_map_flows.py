"""AI 產業鏈資金版圖每日建置（chain_map_flows.py）：只用小型合成資料，不連網。"""
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

import chain_map_flows as cm
from overseas_peers import QuotaExceeded

CHAIN = {"nodes": [
    {"id": "foundry", "tw": [["2330", "晶圓代工"], ["6488", "矽晶圓"]], "us": [["NVDA", "GPU"], ["PSTG", "儲存"]]},
    {"id": "gpu", "tw": [["2330", "代工"]], "us": [["NVDA", "GPU"]]},
]}
TW_CODES, US_CODES = ["2330", "6488"], ["NVDA", "PSTG"]


def make_db(path: Path, ddl: str, rows_sql: str, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute(ddl)
    con.executemany(rows_sql, rows)
    con.commit()
    con.close()


def write_tw(data: Path, listed_prices, otc_prices=(), listed_inst=(), otc_inst=()):
    """價格列 (date, code, turnover 元, close)；法人列 (date, code, foreign_net 股, trust_net 股)。"""
    price_ddl = "CREATE TABLE daily (date TEXT, code TEXT, turnover INTEGER, close REAL)"
    inst_ddl = "CREATE TABLE institutional (date TEXT, code TEXT, foreign_net INTEGER, trust_net INTEGER)"
    make_db(data / cm.PRICE_DBS[0], price_ddl, "INSERT INTO daily VALUES (?,?,?,?)", listed_prices)
    make_db(data / cm.PRICE_DBS[1], price_ddl, "INSERT INTO daily VALUES (?,?,?,?)", otc_prices)
    make_db(data / cm.INST_PAIRS[0][0], inst_ddl, "INSERT INTO institutional VALUES (?,?,?,?)", listed_inst)
    make_db(data / cm.INST_PAIRS[1][0], inst_ddl, "INSERT INTO institutional VALUES (?,?,?,?)", otc_inst)


def bootstrap(months=("2026-08", "2026-09", "2026-10"), inst0=2):
    n = len(months)
    return {"months": list(months), "inst0": inst0, "notes": [], "owner": {},
            "tw": {"2330": {"t": [100.0 + k for k in range(n)], "i": [5.0] * (n - inst0)},
                   "6488": {"t": [1.0] * n, "i": [0.5] * (n - inst0)}},
            "us": {"NVDA": {"t": [500.0] * n}}}


class FakeMassive:
    pause = 12.5

    def __init__(self, days=None, fail_on=None):
        self.days, self.fail_on, self.calls = days or {}, fail_on, []

    def grouped(self, day):
        iso = day.isoformat()
        self.calls.append(iso)
        if iso == self.fail_on:
            raise QuotaExceeded()
        results = self.days.get(iso, [])
        return results, json.dumps({"results": results}).encode()


def bar(ticker, v, vw=None, h=None, l=None, c=None):
    row = {"T": ticker, "v": v, "h": h, "l": l, "c": c}
    if vw is not None:
        row["vw"] = vw
    return row


def weekday_bars(ym, per_day):
    return {d: per_day(d) for d in cm.month_weekdays(ym)}


MIGRATION_0039 = """
CREATE TABLE chain_map_definitions (version TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL CHECK (json_valid(payload)),
  created_at TEXT NOT NULL);
CREATE TABLE chain_map_flows (version TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload TEXT NOT NULL CHECK (json_valid(payload)),
  built_at TEXT NOT NULL);
"""


class FakeD1:
    """D1 /query 的替身：本機 SQLite、?1 綁定參數；表不在時和 D1Http 一樣丟 MissingTable。"""

    def __init__(self, tables=True):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        if tables:
            self.con.executescript(MIGRATION_0039)
        self.writes = 0

    def query(self, sql, params=()):
        try:
            rows = [dict(r) for r in self.con.execute(sql, list(params))]
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                raise cm.MissingTable("no table") from error
            raise
        if not sql.lstrip().upper().startswith("SELECT"):
            self.writes += 1
            self.con.commit()
        return rows

    def rows(self):
        return [dict(r) for r in self.con.execute("SELECT version, as_of, built_at FROM chain_map_flows ORDER BY built_at")]

    def latest(self):
        row = self.con.execute("SELECT payload FROM chain_map_flows ORDER BY built_at DESC, version DESC LIMIT 1").fetchone()
        return json.loads(row["payload"]) if row else None


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.raw = root / "data", root / "data/chain-map/raw"
        self.d1 = FakeD1()
        self.boot = None

    def put_boot(self, payload):
        self.boot = payload   # 正式環境是 R2 chain-map/bootstrap/ 的樣品原檔（見 BootstrapFromR2Test）

    def build(self, now, source=None, **kw):
        kw.setdefault("d1", self.d1)
        kw.setdefault("bootstrap", self.boot)
        return cm.build(chain=CHAIN, data_dir=self.data, raw_dir=self.raw,
                        source=source, now_utc=now, sleep=lambda s: None, **kw)

    def payload(self):
        return self.d1.latest()


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class MonthlyAggregationTest(unittest.TestCase):
    def test_turnover_and_institutional_times_close_in_hundred_millions(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_tw(data,
                     listed_prices=[("2026-10-30", "2330", 3e10, 1000.0), ("2026-11-02", "2330", 2e10, 1000.0),
                                    ("2026-11-03", "2330", 1e10, 1100.0), ("2026-11-03", "9999", 5e10, 10.0)],
                     otc_prices=[("2026-11-02", "6488", 4e8, 500.0), ("2026-10-30", "6488", None, 500.0)],
                     listed_inst=[("2026-10-30", "2330", 1000, 0), ("2026-11-02", "2330", 100_000, -20_000),
                                  ("2026-11-03", "2330", None, 10_000)],
                     otc_inst=[("2026-10-30", "6488", 0, 0), ("2026-11-02", "6488", 200_000, 0)])
            tw = cm.load_tw(data, TW_CODES)
            self.assertEqual(tw["t"]["2330"]["2026-11"], 3e10)
            self.assertEqual(tw["t"]["6488"]["2026-11"], 4e8)
            self.assertNotIn("9999", tw["t"], "不在定義裡的代號不算")
            # (100000-20000)×1000 + (0+10000)×1100
            self.assertEqual(tw["i"]["2330"]["2026-11"], 80_000 * 1000 + 10_000 * 1100)
            self.assertEqual(tw["i"]["6488"]["2026-11"], 200_000 * 500)
            self.assertEqual((tw["through"], tw["inst_first"], tw["first"]["2330"]), ("2026-11-02", "2026-10-30", "2026-10-30"),
                             "行情最後一天取兩個市場較早的那一天")

    def test_missing_working_set_file_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cm.ChainMapError):
                cm.load_tw(Path(tmp), TW_CODES)

    def test_us_day_turnover_prefers_vwap_and_records_the_fallback(self):
        self.assertEqual(cm.day_turnover(bar("X", 10, vw=2.5, h=9, l=1, c=2)), (25.0, "vw"))
        self.assertEqual(cm.day_turnover(bar("X", 10, h=9, l=1, c=2)), (40.0, "hlc3"))
        self.assertIsNone(cm.day_turnover(bar("X", None, vw=2)))


class MergeRuleTest(unittest.TestCase):
    """起始資料 2026-08～10；雲端台股行情從 2026-09-15 起（工作集被裁掉之前的看不到）、法人從 2026-10-01 起。"""

    def setUp(self):
        self.env = Env()
        self.env.put_boot(bootstrap())
        prices, inst = [], []
        for d in ("2026-09-15", "2026-10-01", "2026-10-15", "2026-11-02", "2026-11-20", "2026-12-01"):
            prices.append((d, "2330", 1e10, 1000.0))
            if d >= "2026-10-01":
                inst.append((d, "2330", 100_000, 0))   # 10 萬股 × 1000 元 = 1 億
        prices.append(("2026-10-15", "6488", 2e8, 100.0))   # 6488 在雲端只從 10-15 才有（上市晚／被裁）
        write_tw(self.env.data, prices, [("2026-12-01", "0000", 1, 1)], inst, [("2026-10-01", "0000", 0, 0)])
        # 美股 11 月每個平日都有（11-26 感恩節休市回空）
        self.us = weekday_bars("2026-11", lambda d: [] if d == "2026-11-26" else
                               [bar("NVDA", 1e8, vw=200.0), bar("P", 1e6, vw=50.0)])
        self.us.update({"2026-12-01": [bar("NVDA", 1e8, vw=100.0), bar("P", 1e6, vw=50.0)]})

    def tearDown(self):
        self.env.tmp.cleanup()

    def test_full_month_uses_cloud_partial_or_missing_uses_bootstrap(self):
        summary = self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertEqual(p["months"], ["2026-08", "2026-09", "2026-10", "2026-11"])
        t = p["tw"]["2330"]["t"]
        self.assertEqual(t[0], 100.0, "08 雲端沒有 → 起始資料")
        self.assertEqual(t[1], 101.0, "09 雲端只有半個月 → 不混進去，用起始資料")
        self.assertEqual(t[2], 200, "10 雲端整個月都有 → 雲端（兩天各 100 億）")
        self.assertEqual(t[3], 200, "11 起始資料沒有 → 雲端")
        self.assertEqual(p["tw"]["6488"]["t"][2], 1.0, "6488 雲端從 10-15 才有 → 10 月仍用起始資料")
        self.assertEqual(p["tw"]["6488"]["t"][3], 0, "完整月份沒有成交就是 0")
        self.assertEqual(summary["status"], "ok")
        self.assertEqual((summary["as_of"], summary["tw_through"]), ("2026-11", "2026-12-01"))
        self.assertEqual(set(p), {"months", "inst0", "notes", "tw", "us", "partial"}, "chain-map-flows/v1＋partial；沒有 owner")
        self.assertEqual(self.env.d1.rows()[-1], {"version": "2026-12-02", "as_of": "2026-11", "built_at": "2026-12-02T12:30:00Z"},
                         "version＝台北建置日、as_of＝最後一個完整月份")

    def test_institutional_is_null_before_coverage_and_inst0_comes_from_the_data(self):
        self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertEqual(p["inst0"], 2, "起始資料的法人從 10 月起")
        self.assertEqual(p["tw"]["2330"]["i"], [5.0, 2.0], "10 月法人雲端從 10-01 起不算整月涵蓋 → 起始資料；11 月雲端兩天各 1 億")
        self.assertEqual(p["tw"]["6488"]["i"], [0.5, 0])
        self.assertIn("2026-10 起", p["notes"][0])

    def test_us_months_use_massive_once_covered_and_the_note_says_from_when(self):
        summary = self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertEqual(p["us"]["NVDA"]["t"][:3], [500.0] * 3)
        self.assertEqual(p["us"]["NVDA"]["t"][3], 20 * 1e8 * 200 / 1e9, "20 個交易日（21 個平日扣感恩節）")
        self.assertEqual(summary["us_massive_from"], "2026-11")
        self.assertIn("2026-11 起為 Massive", p["notes"][1])
        self.assertIn("2026-10 以前", p["notes"][1])
        self.assertEqual(summary["us_holidays_recorded"], 1)

    def test_renamed_ticker_continues_under_the_definition_code(self):
        self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertEqual(p["us"]["PSTG"]["t"], [None, None, None, r_us(20 * 1e6 * 50)],
                         "起始資料沒有 PSTG：之前是 null（不是 0），Massive 有 P 之後才開始")
        self.assertEqual(cm.massive_ticker("PSTG", "2026-04-16"), "PSTG")
        self.assertEqual(cm.massive_ticker("PSTG", "2026-04-17"), "P")
        self.assertEqual(cm.massive_ticker("P", "2026-01-05"), "PSTG", "定義改寫成 P 也接得上舊代號")
        self.assertEqual(cm.massive_ticker("MOD", "2026-11-02"), "MOD")

    def test_unfinished_month_is_separate_and_not_in_months(self):
        self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertNotIn("2026-12", p["months"])
        part = p["partial"]
        self.assertEqual(part["month"], "2026-12")
        self.assertEqual(part["through"], {"tw": "2026-12-01", "us": "2026-12-01"})
        self.assertEqual(part["tw"]["2330"], {"t": 100, "i": 1.0})
        self.assertEqual(part["us"]["NVDA"], {"t": 10.0})
        self.assertEqual(len(p["tw"]["2330"]["t"]), len(p["months"]))

    def test_months_computed_once_survive_when_the_working_set_trims_old_prices(self):
        self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        self.assertEqual(self.env.payload()["tw"]["2330"]["t"][3], 200)
        # 隔天工作集把 11 月以前的行情裁掉（只剩 12 月）：11 月仍是上一次算好的，不會退回成 0 或起始資料
        write_tw(self.env.data, [("2026-12-01", "2330", 1e10, 1000.0)], [("2026-12-01", "0000", 1, 1)],
                 [("2026-12-01", "2330", 100_000, 0)], [("2026-12-01", "0000", 0, 0)])
        summary = self.env.build(utc("2026-12-03T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = self.env.payload()
        self.assertEqual(summary["base"], "latest")
        self.assertEqual([r["version"] for r in self.env.d1.rows()], ["2026-12-02", "2026-12-03"])
        self.assertEqual(p["months"][-1], "2026-11")
        self.assertEqual((p["tw"]["2330"]["t"][3], p["tw"]["2330"]["i"][-1]), (200, 2.0))

    def test_inst0_waits_for_the_month_every_traded_company_has_a_value(self):
        # 法人回補到更早，但只有不裁的 2330 有那時的收盤：那幾個月整月留空，inst0 不提前
        env = Env()
        self.addCleanup(env.tmp.cleanup)
        boot = bootstrap()
        env.put_boot(boot)
        days_2330 = ("2026-07-01", "2026-08-03", "2026-09-01", "2026-10-01", "2026-11-02", "2026-12-01")
        write_tw(env.data,
                 [(d, "2330", 1e10, 1000.0) for d in days_2330] +
                 [(d, "6488", 1e8, 10.0) for d in ("2026-09-15", "2026-10-01", "2026-11-02")],
                 [("2026-12-01", "0000", 1, 1)],
                 [(d, "2330", 100_000, 0) for d in days_2330],
                 [("2026-07-01", "0000", 0, 0)])
        env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=50)
        p = env.payload()
        self.assertEqual(p["inst0"], 2, "08、09 月 6488 算不出法人 → 仍從起始資料的 10 月起")
        self.assertIn("更早的月份缺逐日收盤價", p["notes"][0])

    def test_a_month_waits_until_both_markets_have_finished_it(self):
        # 只抓到 11 月一半（每次上限 5 次）：台股 11 月已完整，但美股沒有 → 11 月還不進 months
        summary = self.env.build(utc("2026-12-02T12:30:00"), FakeMassive(self.us), max_calls=5)
        p = self.env.payload()
        self.assertEqual(p["months"][-1], "2026-10")
        self.assertEqual(p["partial"]["month"], "2026-11")
        self.assertEqual(p["partial"]["through"]["us"], "2026-11-06")
        self.assertEqual(p["partial"]["through"]["tw"], "2026-11-30", "台股整月已有：涵蓋到月底")
        self.assertEqual(summary["status"], "catching_up")
        self.assertEqual(cm.exit_code(summary), 0)


def r_us(usd):
    return cm.r(usd / 1e9, 1000)


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        self.sleeps = []
        real = cm.catch_up

        def catch(*args, **kwargs):
            kwargs.setdefault("sleep", self.sleeps.append)
            return real(*args, **kwargs)
        self.patch = unittest.mock.patch.object(cm, "catch_up", catch)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_calls_are_spaced_for_the_five_per_minute_limit(self):
        days = weekday_bars("2026-11", lambda d: [bar("NVDA", 1, vw=1), bar("P", 1, vw=1)])
        cm.catch_up(FakeMassive(days), self.raw, US_CODES, start="2026-11-01", now_utc=utc("2026-11-06T12:30:00"))
        self.assertEqual(self.sleeps, [12.5, 12.5, 12.5], "4 次呼叫、中間停 3 次，第一次前不停")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_days_after_an_outage_are_fetched_oldest_first_and_never_refetched(self):
        days = weekday_bars("2026-11", lambda d: [bar("NVDA", 1, vw=1), bar("P", 1, vw=1)])
        src = FakeMassive(days)
        first = cm.catch_up(src, self.raw, US_CODES, start="2026-11-01", now_utc=utc("2026-11-06T12:30:00"), max_calls=3)
        self.assertEqual(src.calls, ["2026-11-02", "2026-11-03", "2026-11-04"])
        self.assertEqual((first["fetched"], first["left"]), (3, 1))
        # 停擺幾天後再跑：只補沒存過的
        src.calls.clear()
        second = cm.catch_up(src, self.raw, US_CODES, start="2026-11-01", now_utc=utc("2026-11-11T12:30:00"), max_calls=20)
        self.assertEqual(src.calls, ["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"])
        self.assertEqual(second["left"], 0)

    def test_the_current_us_day_is_never_requested(self):
        src = FakeMassive()
        # 台北 11-03 20:30 = 美東 11-03 07:30：11-02 已收盤、過了美東午夜 → 要 11-02；11-03 不要
        cm.catch_up(src, self.raw, US_CODES, start="2026-11-02", now_utc=utc("2026-11-03T12:30:00"))
        self.assertEqual(src.calls, ["2026-11-02"])
        src.calls.clear()
        # 美東 11-02 晚上 21:00（UTC 11-03 02:00）：11-02 的資料還不一定有 → 不要
        cm.catch_up(src, self.raw, US_CODES, start="2026-11-03", now_utc=utc("2026-11-03T02:00:00"))
        self.assertEqual(src.calls, [])

    def test_a_holiday_is_recorded_once_and_not_asked_again(self):
        src = FakeMassive({})   # 回空 = 休市
        report = cm.catch_up(src, self.raw, US_CODES, start="2026-11-26", now_utc=utc("2026-11-27T12:30:00"))
        self.assertEqual(report["holidays"], 1)
        saved = json.loads((self.raw / "us-2026-11-26.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["holiday"])
        self.assertEqual(saved["response_sha256"], __import__("hashlib").sha256(b'{"results": []}').hexdigest())
        src.calls.clear()
        cm.catch_up(src, self.raw, US_CODES, start="2026-11-26", now_utc=utc("2026-11-27T12:30:00"))
        self.assertEqual(src.calls, [])

    def test_extract_keeps_only_definition_tickers_and_records_the_basis(self):
        src = FakeMassive({"2026-11-02": [bar("NVDA", 10, vw=2), bar("P", 4, h=3, l=1, c=2), bar("AAPL", 1, vw=1)]})
        report = cm.catch_up(src, self.raw, US_CODES, start="2026-11-02", now_utc=utc("2026-11-03T12:30:00"))
        saved = json.loads((self.raw / "us-2026-11-02.json").read_text(encoding="utf-8"))
        self.assertEqual(set(saved["tickers"]), {"NVDA", "P"})
        self.assertEqual((saved["tickers"]["NVDA"]["basis"], saved["tickers"]["P"]["basis"]), ("vw", "hlc3"))
        self.assertEqual(saved["tickers"]["P"]["turnover"], 8.0)
        self.assertEqual(report["hlc3"], 1)
        self.assertNotIn("apiKey", json.dumps(saved))

    def test_a_quota_error_stops_fetching_and_keeps_what_was_saved(self):
        days = weekday_bars("2026-11", lambda d: [bar("NVDA", 1, vw=1), bar("P", 1, vw=1)])
        src = FakeMassive(days, fail_on="2026-11-04")
        report = cm.catch_up(src, self.raw, US_CODES, start="2026-11-01", now_utc=utc("2026-11-10T12:30:00"))
        self.assertEqual((report["fetched"], report["error"]), (2, "quota"))
        self.assertEqual(sorted(p.name for p in self.raw.iterdir()), ["us-2026-11-02.json", "us-2026-11-03.json"])

    def test_a_response_missing_most_companies_is_not_stored(self):
        src = FakeMassive({"2026-11-02": [bar("AAPL", 1, vw=1)]})
        report = cm.catch_up(src, self.raw, ["NVDA", "PSTG", "AMD", "MU"], start="2026-11-02",
                             now_utc=utc("2026-11-03T12:30:00"))
        self.assertEqual(report["error"], "format")
        self.assertFalse((self.raw / "us-2026-11-02.json").exists())


class FakeStore:
    def __init__(self, objects=None):
        self.objects, self.puts = dict(objects or {}), []

    def keys(self, prefix=""):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def get(self, key):
        return self.objects.get(key)

    def put(self, key, data):
        self.puts.append(key)
        self.objects[key] = data


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        write_tw(self.env.data, [("2026-11-02", "2330", 1e10, 1000.0)], [("2026-11-02", "6488", 1e8, 10.0)],
                 [("2026-11-02", "2330", 1, 1)], [("2026-11-02", "6488", 1, 1)])
        self.days = {"2026-11-02": [bar("NVDA", 1, vw=1), bar("P", 1, vw=1)]}

    def tearDown(self):
        self.env.tmp.cleanup()

    def test_dry_run_reads_d1_and_r2_but_writes_nothing(self):
        self.env.put_boot(bootstrap())
        writes = self.env.d1.writes
        store = FakeStore()
        out = Path(self.env.tmp.name) / "payload.json"
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store, dry_run=True, output=out)
        self.assertEqual(store.puts, [])
        self.assertEqual(self.env.d1.writes, writes)
        self.assertEqual(self.env.d1.rows(), [])
        self.assertFalse(out.exists())
        self.assertEqual((summary["status"], summary["months"], summary["us_days_fetched"]), ("ok", 3, 1))
        self.assertEqual((summary["first_month"], summary["last_month"]), ("2026-08", "2026-10"))
        line = cm._status_line(summary)
        self.assertNotIn("NVDA", line, "摘要只有數量與月份範圍，沒有個股數字")
        for key in ('"tw":{', '"us":{', '"t":', '"i":'):
            self.assertNotIn(key, line)

    def test_a_real_run_uploads_new_day_files_only_and_writes_one_row(self):
        self.env.put_boot(bootstrap())
        old = b'{"schema": "chain-map-us-day/v1", "date": "2026-10-30", "tickers": {}, "holiday": true}'
        store = FakeStore({cm.R2_PREFIX + "us-2026-10-30.json": old})
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store)
        self.assertEqual(store.puts, [cm.R2_PREFIX + "us-2026-11-02.json", cm.CURRENT_KEY])
        self.assertEqual((summary["r2_downloaded"], summary["r2_uploaded"], summary["version"]), (1, 1, "2026-11-03"))
        self.assertEqual(len(self.env.d1.rows()), 1)
        current = json.loads(store.objects[cm.CURRENT_KEY].decode("utf-8"))
        self.assertEqual({k: current[k] for k in ("schema", "version", "as_of", "built_at")},
                         {"schema": "chain-map-flows-r2/v1", "version": "2026-11-03", "as_of": "2026-10",
                          "built_at": "2026-11-03T12:30:00Z"})
        self.assertEqual(current["payload"], self.env.payload(), "R2 與 D1 是同一份 payload")

    def test_the_r2_current_is_written_before_d1(self):
        self.env.put_boot(bootstrap())
        order = []
        store = FakeStore()
        put = store.put
        store.put = lambda key, data: (order.append(("r2", key)), put(key, data))
        query = self.env.d1.query

        def logged(sql, params=()):
            if not sql.lstrip().upper().startswith("SELECT"):
                order.append(("d1", sql.split()[0]))
            return query(sql, params)
        self.env.d1.query = logged
        self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store)
        self.assertEqual(order[1:3], [("r2", cm.CURRENT_KEY), ("d1", "INSERT")])

    def test_the_next_build_uses_the_fresher_of_d1_and_r2_current_as_base(self):
        self.env.put_boot(bootstrap())
        newer = {**bootstrap(), "notes": ["R2 那份"]}
        newer["tw"]["2330"]["t"] = [7.0, 7.0, 7.0]
        store = FakeStore({cm.CURRENT_KEY: json.dumps(cm.current_object(newer, version="2026-11-02", as_of="2026-10",
                                                                         built_at="2026-11-02T12:30:00Z")).encode()})
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store, dry_run=True)
        self.assertEqual(summary["base"], "r2_current", "D1 沒有排程列 → 用 R2 那份")
        self.env.d1.query("INSERT INTO chain_map_flows VALUES ('2026-11-03', '2026-10', ?1, '2026-11-03T12:30:00Z')",
                          [json.dumps(bootstrap())])
        summary = self.env.build(utc("2026-11-04T12:30:00"), FakeMassive(self.days), store=store, dry_run=True)
        self.assertEqual(summary["base"], "latest", "D1 那列較新 → 用 D1")
        store.objects[cm.CURRENT_KEY] = b"{"
        self.assertEqual(cm.load_current(store), (None, None), "R2 那份壞了 → 當成沒有")

    def test_no_bootstrap_is_a_clear_failure(self):
        with self.assertRaises(cm.MissingBootstrap):
            self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=FakeStore())

    def test_tables_not_created_yet_still_saves_us_days_and_says_so_plainly(self):
        self.env.put_boot(bootstrap())
        self.env.d1 = FakeD1(tables=False)
        store = FakeStore()
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store)
        self.assertEqual(summary["status"], "skipped_no_table_r2")
        self.assertEqual(store.puts, [cm.R2_PREFIX + "us-2026-11-02.json", cm.CURRENT_KEY])
        self.assertEqual(cm.exit_code(summary), 0)
        self.assertIn("只寫進 R2；網站照樣讀得到 2026-08～2026-10", cm.plain_message(summary))
        # 沒有 R2（沒給 --r2）：和以前一樣，只說表還沒建
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days))
        self.assertEqual((summary["status"], cm.exit_code(summary)), ("skipped_no_table", 0))
        self.assertEqual(cm.plain_message(summary), "資料表還沒建立：到 Actions 按 d1-migrate")

    def test_the_d1_daily_write_limit_after_r2_success_is_only_a_warning(self):
        self.env.put_boot(bootstrap())

        def full(sql, params=()):
            if sql.lstrip().upper().startswith("SELECT"):
                return []
            raise cm.D1WriteLimit("limit")
        self.env.d1.query = full
        store = FakeStore()
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store)
        self.assertEqual((summary["status"], cm.exit_code(summary), summary["d1_status"]), ("d1_write_limit_r2", 0, "d1_write_limit"))
        self.assertEqual(store.puts, [cm.R2_PREFIX + "us-2026-11-02.json", cm.CURRENT_KEY], "日檔與網站那份都已上傳")
        self.assertIn("免費寫入額度已用完，這一班只寫進 R2", cm.plain_message(summary))
        # R2 那份也寫不進去：照舊是紅色
        def broken(key, data):
            if key == cm.CURRENT_KEY:
                raise OSError("R2 PUT → HTTP 500")
        store.put = broken
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days), store=store)
        self.assertEqual((summary["status"], cm.exit_code(summary), summary["r2_current"]), ("d1_write_limit", 1, False))
        self.assertIn("免費寫入額度已用完", cm.plain_message(summary))

        def http(url, body, headers):
            return 429, json.dumps({"success": False, "errors": [
                {"message": "Exceeded D1's free tier daily row write limit; upgrade or wait"}]}).encode()
        with self.assertRaises(cm.D1WriteLimit):
            cm.D1Http("acc", "tok", "db", http=http).query("INSERT INTO x VALUES (1)")

    def test_the_step_summary_gets_one_plain_line(self):
        out = Path(self.env.tmp.name) / "summary.md"
        with unittest.mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(out)}):
            cm.write_step_summary({"status": "ok", "first_month": "2021-01", "last_month": "2026-11", "us_days_fetched": 3})
        self.assertEqual(out.read_text(encoding="utf-8"), "### AI 產業鏈資金版圖\n- 已更新：2021-01～2026-11（美股這一班補 3 天）\n")


class BootstrapFromR2Test(unittest.TestCase):
    def test_the_newest_sample_is_used_owner_dropped_and_pstg_renamed(self):
        sample = {**bootstrap(), "owner": {"US NVDA": "gpu"}}
        sample["us"]["PSTG"] = {"t": [1.0, 2.0, 3.0]}
        store = FakeStore({cm.BOOTSTRAP_PREFIX + "stock_flows-2026-09.json": b"{}",
                           cm.BOOTSTRAP_PREFIX + "stock_flows-2026-10.json": json.dumps(sample).encode()})
        boot, key = cm.load_bootstrap(store)
        self.assertEqual(key, "chain-map/bootstrap/stock_flows-2026-10.json")
        self.assertNotIn("owner", boot)
        self.assertEqual((boot["us"]["P"]["t"], "PSTG" in boot["us"]), ([1.0, 2.0, 3.0], False))
        self.assertEqual(cm.load_bootstrap(FakeStore()), (None, None))

    def test_a_definition_with_p_continues_the_sample_pstg_history(self):
        env = Env()
        self.addCleanup(env.tmp.cleanup)
        sample = bootstrap()
        sample["us"]["PSTG"] = {"t": [1.0, 2.0, 3.0]}
        store = FakeStore({cm.BOOTSTRAP_PREFIX + "stock_flows-2026-10.json": json.dumps(sample).encode()})
        write_tw(env.data, [("2026-12-01", "2330", 1, 1.0)], [("2026-12-01", "0000", 1, 1)],
                 [("2026-12-01", "2330", 0, 0)], [("2026-12-01", "0000", 0, 0)])
        days = weekday_bars("2026-11", lambda d: [bar("NVDA", 1, vw=1), bar("P", 1e9, vw=2.0)])
        chain = {"nodes": [{"id": "x", "tw": [["2330", ""], ["6488", ""]], "us": [["NVDA", ""], ["P", ""]]}]}
        cm.build(d1=env.d1, chain=chain, store=store, data_dir=env.data, raw_dir=env.raw, source=FakeMassive(days),
                 now_utc=utc("2026-12-02T12:30:00"), sleep=lambda s: None, max_calls=50)
        self.assertEqual(env.payload()["us"]["P"]["t"], [1.0, 2.0, 3.0, 42.0], "樣品的 PSTG 接上 Massive 的 P（21 天 × 2 十億）")


class BuildStatusTest(unittest.TestCase):
    setUp = BuildTest.setUp
    tearDown = BuildTest.tearDown

    def test_no_key_still_builds_but_the_step_shows_red(self):
        self.env.put_boot(bootstrap())
        summary = self.env.build(utc("2026-11-03T12:30:00"), None)
        self.assertEqual(summary["status"], "no_key")
        self.assertEqual(cm.exit_code(summary), 1)

    def test_a_fetch_failure_is_red(self):
        self.env.put_boot(bootstrap())
        summary = self.env.build(utc("2026-11-03T12:30:00"), FakeMassive(self.days, fail_on="2026-11-02"))
        self.assertEqual((summary["status"], cm.exit_code(summary)), ("fetch_failed", 1))

    def test_cli_prints_one_machine_readable_status_line(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()), \
                unittest.mock.patch.dict("os.environ", {"MASSIVE_API_KEY": ""}):
            code = cm.main(["build"])   # 沒有 --d1：沒有樣品那一列
        status = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual((code, status["status"]), (1, "no_bootstrap"))
        self.assertEqual({"status", "as_of", "months", "us_days_fetched"} - set(status), set())


class D1WriteTest(unittest.TestCase):
    def test_same_day_rerun_overwrites_and_old_rows_are_trimmed_but_not_the_bootstrap(self):
        d1 = FakeD1()
        d1.query("INSERT INTO chain_map_flows VALUES ('bootstrap-2026-10', '2026-10', '{}', '2026-11-24T00:00:00Z')")
        for day in range(1, 18):
            cm.write_flows(d1, json.dumps({"d": day}), version=f"2026-12-{day:02d}", as_of="2026-11",
                           built_at=f"2026-12-{day:02d}T12:30:00Z")
        cm.write_flows(d1, json.dumps({"d": "again"}), version="2026-12-17", as_of="2026-11", built_at="2026-12-17T13:00:00Z")
        versions = [r["version"] for r in d1.rows()]
        self.assertEqual(versions[0], "bootstrap-2026-10")
        self.assertEqual(len(versions), 1 + cm.KEEP_ROWS)
        self.assertEqual(versions[-1], "2026-12-17")
        self.assertEqual(d1.latest(), {"d": "again"})

    def test_the_payload_travels_as_a_bound_parameter_whatever_its_size(self):
        sent = []

        def http(url, body, headers):
            sent.append(json.loads(body))
            return 200, json.dumps({"success": True, "result": [{"success": True, "results": []}]}).encode()
        d1 = cm.D1Http("acc", "tok", "db", http=http)
        big = json.dumps({"x": "台" * 200_000, "y": "'"}, ensure_ascii=False)
        cm.write_flows(d1, big, version="2026-12-01", as_of="2026-11", built_at="b")
        self.assertEqual(sent[0]["params"][2], big)
        self.assertNotIn("台", sent[0]["sql"])
        self.assertLess(len(sent[0]["sql"]), 1000)

    def test_a_missing_table_reply_is_recognised(self):
        def http(url, body, headers):
            return 400, json.dumps({"success": False, "errors": [{"message": "no such table: chain_map_flows: SQLITE_ERROR"}]}).encode()
        with self.assertRaises(cm.MissingTable):
            cm.D1Http("acc", "tok", "db", http=http).query("SELECT 1")

        def other(url, body, headers):
            return 500, b"oops"
        with self.assertRaises(cm.ChainMapError) as caught:
            cm.D1Http("acc", "tok", "db", http=other).query("SELECT 1")
        self.assertNotIsInstance(caught.exception, cm.MissingTable)

    def test_the_database_id_comes_from_wrangler_config(self):
        with unittest.mock.patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "acc", "CLOUDFLARE_API_TOKEN": "tok"}):
            d1 = cm.D1Http.from_env()
        self.assertIn("/d1/database/391bc764-2dd8-47b3-b9f9-93b9521c59e7/query", d1.url)


class DefinitionTest(unittest.TestCase):
    def test_codes_come_from_the_definition_and_the_repo_seed_loads(self):
        self.assertEqual(cm.definition(CHAIN), (TW_CODES, US_CODES))
        chain, version = cm.load_definition(None)
        tw, us = cm.definition(chain)
        self.assertEqual(version, "file")
        self.assertEqual((len(tw), len(us)), (120, 100))
        self.assertIn("P", us, "基準版用 Everpure 的新代號 P")

    def test_the_latest_d1_definition_wins_and_a_broken_one_falls_back_to_the_seed(self):
        d1 = FakeD1()
        d1.query("INSERT INTO chain_map_definitions VALUES ('owner-1', 'owner', ?1, '2026-12-01T00:00:00Z')", [json.dumps(CHAIN)])
        chain, version = cm.load_definition(d1)
        self.assertEqual((version, cm.definition(chain)), ("owner-1", (TW_CODES, US_CODES)))
        d1.query("INSERT INTO chain_map_definitions VALUES ('owner-2', 'owner', '{\"nodes\": []}', '2026-12-02T00:00:00Z')")
        self.assertEqual(cm.load_definition(d1)[1], "file")
        self.assertEqual(cm.load_definition(FakeD1(tables=False))[1], "file")

    def test_bootstrap_shape_is_checked(self):
        cm.validate_bootstrap(bootstrap())
        bad = bootstrap()
        bad["tw"]["2330"]["i"] = [1.0, 2.0]   # 3 個月、inst0＝2 → 只能有 1 個
        with self.assertRaises(cm.ChainMapError):
            cm.validate_bootstrap(bad)
        with self.assertRaises(cm.ChainMapError):
            cm.validate_bootstrap({**bootstrap(), "months": ["2026-08", "2026-10", "2026-11"]})


if __name__ == "__main__":
    unittest.main()
