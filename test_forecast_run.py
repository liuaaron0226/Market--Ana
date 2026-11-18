"""晚間執行程式的入口（C 票第 6、7 片；本人 2026-11-15 同意的測試接縫 1）。

給一份「每日更新已發布」的大盤資料檔（window.MARKET_ANALYSIS）＋假模型命令列程式（經真的子程序邊界），
檢查帳本、對帳、寫給資料庫的指令檔與執行紀錄。預期值都是手算或手推的。
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import forecast_ledger as fl
import forecast_run as fr
import overseas_inputs as oi
from test_overseas_inputs import FakeNet, eodhd

TAIPEI = timezone(timedelta(hours=8))
METHOD = "# 大盤分析方法\n\n版本：0.3，2026-11-15。\n"


def weekdays(start, count):
    day, out = date.fromisoformat(start), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# 80 個交易日，收盤從 100 每天加 1 到 179；高低各 ±0.5（相鄰兩根剛好相接，沒有缺口）；法人每天買超。
#   短期：收盤 > 五日線、五日線 > 十日線、每天都漲所以 RSI = 100 → 至少三項偏多 → 偏多
#   中期：收盤 > 月線、月線 > 季線、近二十日上漲、二十日法人買超 → 偏多
#   所以固定規則三格都選線上。收盤創新高，上方只有距離關卡；五日線 = (175+…+179)/5 = 177 在下方。
DATES = weekdays("2026-07-01", 80)
BASE = DATES[-1]


def analysis(dates=DATES, *, institutional_until=None, futures_until=None):
    daily = [{"date": d, "open": 100 + i - 0.3, "high": 100 + i + 0.5, "low": 100 + i - 0.5, "close": 100 + i,
              "amount": 300_000_000_000} for i, d in enumerate(dates)]
    inst_dates = [d for d in dates if d <= (institutional_until or dates[-1])]
    fut_dates = [d for d in dates if d <= (futures_until or dates[-1])]
    return {
        "meta": {"cutoff_date": dates[-1]},
        "daily": daily,
        "institutional": [{"date": d, "foreign": {"net": 5_000_000_000}, "trust": {"net": 1_000_000_000},
                           "dealer": {"net": -500_000_000}} for d in inst_dates],
        "futures": [{"date": d, "foreign_oi_net_contracts": -1000} for d in fut_dates],
    }


def cell(strong, weak, zone):
    return {"strong_line": strong, "weak_line": weak, "zone": zone, "reason": "創高後量價配合", "reason_refs": [],
            "invalidation": "跌破短均線代表看錯", "invalidation_refs": [weak]}


def answer(base=BASE):
    return {"base_date": base, "regime": "多頭延續", "cells": {
        "1": cell("dist_up_1", "ma5", "above"), "5": cell("dist_up_5", "ma5", "between"),
        "20": cell("dist_up_20", "ma5", "above")}}


class RunTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ledger = self.tmp / "ledger"
        self.method = self.tmp / "MARKET_METHOD.md"
        self.method.write_text(METHOD, encoding="utf-8")
        self.calls = self.tmp / "calls.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def analysis_file(self, payload, name="analysis-data.js"):
        path = self.tmp / name
        path.write_text("window.MARKET_ANALYSIS = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
        return path

    def model(self, structured):
        script = self.tmp / "fake_agy.py"
        envelope = json.dumps({"event": "result", "result": {"status": "SUCCESS", "structured_output": structured}},
                              ensure_ascii=False)
        script.write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.stdin.buffer.readline()\n"
            f"p=Path({str(self.calls)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
            f"sys.stdout.buffer.write({envelope.encode('utf-8')!r} + b'\\n')\n", encoding="utf-8")
        return script

    def calls_made(self):
        return len(self.calls.read_text()) if self.calls.exists() else 0

    def run_evening(self, payload=None, *, now=None, structured=None, model_name="gemini-3.8-flash", **kwargs):
        return fr.run_evening(
            self.analysis_file(payload or analysis()), ledger=self.ledger, method_path=self.method,
            model_bin=self.model(structured or answer()), model=model_name,
            now=lambda: now or datetime.fromisoformat(f"{BASE}T17:40:00+08:00"), **kwargs)


class EveningRunTests(RunTestCase):
    def test_writes_a_trial_evening_forecast_and_the_database_file(self):
        sql = self.tmp / "out" / "forecast.sql"
        log = self.tmp / "out" / "run.json"
        result = self.run_evening(sql_output=sql, log_output=log)

        self.assertEqual(result["outcome"], "written")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual((record["kind"], record["edition"], record["base_date"], record["run_mode"]),
                         ("forecast", "evening", BASE, "trial"))
        self.assertEqual(record["model"], "gemini-3.8-flash")
        self.assertEqual(record["method_version"], "0.3")
        self.assertEqual([record["cells"][h]["fixed_rule_zone"] for h in ("1", "5", "20")], ["above"] * 3)
        self.assertEqual(record["cells"]["1"]["weak_line"], {"id": "ma5", "name": "五日線", "value": 177})
        self.assertEqual(record["data_cutoffs"], {"index": BASE, "institutional": BASE, "futures": BASE})
        self.assertIn("INSERT INTO forecast_records", sql.read_text(encoding="utf-8"))
        written = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual((written["outcome"], written["base_date"], written["kind"], written["dry_run"]),
                         ("written", BASE, "forecast", False))

    def test_running_twice_keeps_the_first_record_and_does_not_call_the_model_again(self):
        self.run_evening()
        first = fl.read_ledger(self.ledger)["forecasts"]
        again = self.run_evening(structured=answer() | {"regime": "盤整"})
        self.assertEqual(again["outcome"], "unchanged")
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], first)
        self.assertEqual(self.calls_made(), 1)

    def test_missing_institutional_data_before_the_deadline_writes_nothing(self):
        result = self.run_evening(analysis(institutional_until=DATES[-2]))
        self.assertEqual(result["outcome"], "not_ready")
        self.assertIn("institutional", result["detail"])
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], [])
        self.assertEqual(self.calls_made(), 0)

    def test_requested_date_that_is_not_the_published_data_date_writes_nothing(self):
        result = self.run_evening(date=DATES[-2])
        self.assertEqual(result["outcome"], "not_ready")
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], [])

    def test_past_the_end_of_the_base_date_records_a_miss_with_its_reason(self):
        log = self.tmp / "run.json"
        result = self.run_evening(now=datetime.fromisoformat(f"{BASE}T00:00:00+08:00") + timedelta(days=1, minutes=5),
                                  log_output=log)
        self.assertEqual((result["outcome"], result["kind"], result["miss_reason"]), ("written", "miss", "deadline_passed"))
        self.assertEqual(json.loads(log.read_text(encoding="utf-8"))["miss_reason"], "deadline_passed")

    def test_model_input_carries_turnover_indicators_and_all_three_trend_labels(self):
        self.run_evening()
        model_input = fl.read_ledger(self.ledger)["forecasts"][0]["input"]
        self.assertEqual(model_input["index_recent"][-1]["amount"], 300_000_000_000)
        self.assertEqual([p["key"] for p in model_input["indicators"]], ["short", "medium", "long"])
        self.assertEqual(model_input["indicators"][0]["conclusion"], "偏多")
        self.assertIn("ma5", model_input["indicators"][0]["metrics"])
        # 80 根不夠算長期（要 240 根），長期標記資料不足、不擋預測
        self.assertEqual(model_input["trend_labels"], {"short": "偏多", "medium": "偏多", "long": None})

    def test_trading_days_with_no_run_are_locked_as_misses(self):
        two_back = DATES[:-2]
        self.run_evening(analysis(two_back), structured=answer(two_back[-1]),
                         now=datetime.fromisoformat(f"{two_back[-1]}T17:40:00+08:00"))
        result = self.run_evening()
        # 前一個交易日整天沒跑：今天補一筆漏記（過了期限），不呼叫模型
        self.assertEqual(result["backfilled_misses"], [DATES[-2]])
        records = {r["base_date"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual((records[DATES[-2]]["kind"], records[DATES[-2]]["miss_reason"]), ("miss", "deadline_passed"))
        self.assertEqual(records[BASE]["kind"], "forecast")
        self.assertEqual(self.calls_made(), 2)

    def test_a_new_model_series_does_not_backfill_from_the_old_models_records(self):
        # 本人 2026-11-17 換 Argon：Argon 這一系列從第一份起算，Flash 最後一份與今天之間的空檔不算 Argon 漏記。
        two_back = DATES[:-2]
        self.run_evening(analysis(two_back), structured=answer(two_back[-1]),
                         now=datetime.fromisoformat(f"{two_back[-1]}T17:40:00+08:00"))
        result = self.run_evening(model_name="gemini-4-argon")
        self.assertEqual(result["backfilled_misses"], [])
        self.assertEqual(sorted((r["base_date"], r["model"]) for r in fl.read_ledger(self.ledger)["forecasts"]),
                         [(two_back[-1], "gemini-3.8-flash"), (BASE, "gemini-4-argon")])

    def test_an_empty_ledger_does_not_backfill_anything(self):
        result = self.run_evening()
        self.assertEqual(result["backfilled_misses"], [])
        self.assertEqual(len(fl.read_ledger(self.ledger)["forecasts"]), 1)

    def test_each_run_reconciles_earlier_forecasts_that_have_expired(self):
        early = DATES[:-1]
        self.run_evening(analysis(early), structured=answer(early[-1]),
                         now=datetime.fromisoformat(f"{early[-1]}T17:40:00+08:00"))
        result = self.run_evening()
        # 前一天那份的一日格在今天到期：收盤 179，轉弱線五日線 176、轉強線一日距離關卡在 178 上方
        self.assertEqual(result["reconciliation"]["appended"], 1)
        outcome = fl.read_ledger(self.ledger)["reconciliations"][0]
        self.assertEqual((outcome["horizon"], outcome["expiry_date"], outcome["expiry_close"]), (1, BASE, 179))

    def test_cells_expiring_today_reach_the_lesson_layer_of_todays_forecast(self):
        # E 票：先對帳再寫，今天到期的那一格（前一天那份的一日格）已經是今天這份的教訓
        early = DATES[:-1]
        self.run_evening(analysis(early), structured=answer(early[-1]),
                         now=datetime.fromisoformat(f"{early[-1]}T17:40:00+08:00"))
        log = self.run_evening()
        today = {r["base_date"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}[BASE]
        counts = today["input"]["lessons"]["counts"]
        self.assertEqual(counts["1"]["hits"] + counts["1"]["misses"], 1)
        self.assertEqual(log["experience"]["lessons_counts"], counts)
        # 80 根不到一年，相似盤勢不歸類
        self.assertEqual(log["experience"]["similar_today"], {"regime": None, "since": None})

    def test_dry_run_also_sees_cells_expiring_today_without_touching_the_ledger(self):
        early = DATES[:-1]
        self.run_evening(analysis(early), structured=answer(early[-1]),
                         now=datetime.fromisoformat(f"{early[-1]}T17:40:00+08:00"))
        before = fl.read_ledger(self.ledger)
        log = self.run_evening(dry_run=True)
        counts = log["experience"]["lessons_counts"]
        self.assertEqual(counts["1"]["hits"] + counts["1"]["misses"], 1)
        self.assertEqual(fl.read_ledger(self.ledger), before)


class DryRunTests(RunTestCase):
    def test_dry_run_leaves_the_real_ledger_untouched_and_reports_the_answer(self):
        sql = self.tmp / "forecast.sql"
        log = self.tmp / "run.json"
        result = self.run_evening(dry_run=True, sql_output=sql, log_output=log)
        self.assertEqual(result["dry_run"], True)
        self.assertFalse(self.ledger.exists() and any(self.ledger.rglob("*.json")))
        self.assertFalse(sql.exists())
        report = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(report["kind"], "forecast")
        self.assertEqual(report["record"]["regime"], "多頭延續")
        self.assertEqual(report["record"]["cells"]["1"]["strong_line"]["id"], "dist_up_1")
        self.assertEqual(report["attempts"], [])

    def test_dry_run_can_replay_an_earlier_published_day(self):
        replay = DATES[-3]
        result = self.run_evening(dry_run=True, as_of=replay, structured=answer(replay),
                                  now=datetime.fromisoformat("2026-11-15T10:00:00+08:00"))
        # 重播：資料截到那一天、時鐘停在那天收盤後，所以不會因為期限而漏記
        self.assertEqual((result["base_date"], result["kind"]), (replay, "forecast"))
        self.assertEqual(result["record"]["cells"]["1"]["weak_line"]["value"], 175)  # (173+…+177)/5

    def test_replaying_an_earlier_day_is_only_allowed_in_a_dry_run(self):
        with self.assertRaises(ValueError):
            self.run_evening(as_of=DATES[-3])


def next_weekday(day, steps=1):
    current = date.fromisoformat(day)
    while steps:
        current += timedelta(days=1)
        if current.weekday() < 5:
            steps -= 1
    return current.isoformat()


SESSION = next_weekday(BASE)  # 基準日之後的下一個開盤日（晨間版）


def at_taipei(day, clock_time):
    return datetime.fromisoformat(f"{day}T{clock_time}+08:00")


class MorningTestCase(RunTestCase):
    def run_morning(self, payload=None, *, session=SESSION, now=None, structured=None, holidays=frozenset(), **kwargs):
        return fr.run_morning(
            self.analysis_file(payload or analysis()), ledger=self.ledger, method_path=self.method,
            model_bin=self.model(structured or answer()), model="gemini-3.8-flash", session_date=session,
            holidays=set(holidays), now=lambda: now or at_taipei(session, "07:30:00"), **kwargs)

    def overseas(self, reference, name):
        # 那斯達克：基準日前一天、基準日（美國日期）各有一根；其他來源都沒抓到
        routes = {"https://eodhd.com/api/eod/IXIC.INDX": eodhd([(DATES[-3], 100), (DATES[-2], 101), (BASE, 103.02)])}
        folder = self.tmp / name
        oi.fetch_sources(folder, reference_date=reference, eodhd_token="KEY", fetch=FakeNet(routes),
                         now=lambda: "2026-10-20T23:00:00Z")
        return folder


class MorningRunTests(MorningTestCase):
    def test_writes_a_morning_forecast_on_the_previous_trading_day_before_the_open(self):
        log = self.tmp / "run.json"
        result = self.run_morning(log_output=log)
        self.assertEqual((result["outcome"], result["edition"], result["base_date"], result["session_date"]),
                         ("written", "morning", BASE, SESSION))
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual((record["kind"], record["edition"], record["base_date"]), ("forecast", "morning", BASE))
        self.assertEqual(record["deadline"], f"{SESSION}T08:45:00+08:00")
        self.assertEqual(json.loads(log.read_text(encoding="utf-8"))["edition"], "morning")

    def test_morning_and_evening_of_the_same_base_date_are_two_records_and_morning_never_sees_evening(self):
        self.run_evening()
        self.run_morning()
        records = {r["edition"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual({e: r["base_date"] for e, r in records.items()}, {"evening": BASE, "morning": BASE})
        self.assertNotIn(records["evening"]["forecast_id"], json.dumps(records["morning"]["input"], ensure_ascii=False))
        self.assertNotIn("evening", json.dumps(records["morning"]["input"], ensure_ascii=False))

    def test_holidays_and_weekends_write_nothing(self):
        saturday = next(d for d in (date.fromisoformat(BASE) + timedelta(days=n) for n in range(1, 8)) if d.weekday() == 5)
        for session, holidays in ((saturday.isoformat(), set()), (SESSION, {SESSION})):
            result = self.run_morning(session=session, holidays=holidays)
            self.assertEqual(result["outcome"], "holiday")
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], [])
        self.assertEqual(self.calls_made(), 0)

    def test_the_base_date_skips_a_holiday_before_the_session(self):
        later = next_weekday(BASE, 2)
        result = self.run_morning(session=later, holidays={SESSION})
        self.assertEqual((result["outcome"], result["base_date"]), ("written", BASE))

    def test_base_date_data_not_yet_published_writes_nothing_before_the_deadline(self):
        result = self.run_morning(analysis(DATES[:-1]))
        self.assertEqual((result["outcome"], result["base_date"]), ("not_ready", BASE))
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], [])
        self.assertEqual(self.calls_made(), 0)

    def test_an_unscheduled_closure_is_never_locked_as_a_morning_miss(self):
        # BASE 那天臨時休市（颱風，不在休市日曆）：日 K 沒有 BASE。下一個早上看到的是 DATES[-2]，不寫、不記漏記；
        # 再下一個開盤日照常寫，補漏記也只看日 K 實際有資料的日子。
        earlier = DATES[:-1]
        self.run_morning(analysis(earlier), session=BASE, structured=answer(earlier[-1]))
        late = self.run_morning(analysis(earlier), now=at_taipei(SESSION, "08:50:00"))
        self.assertEqual((late["outcome"], late["base_date"]), ("not_ready", BASE))
        after = next_weekday(SESSION)
        result = self.run_morning(analysis(earlier + [SESSION]), session=after, structured=answer(SESSION))
        self.assertEqual((result["outcome"], result["base_date"], result["backfilled_misses"]), ("written", SESSION, []))
        kinds = {r["base_date"]: r["kind"] for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual(kinds, {DATES[-2]: "forecast", SESSION: "forecast"})

    def test_not_locked_fifteen_minutes_before_the_open_is_a_miss(self):
        result = self.run_morning(now=at_taipei(SESSION, "08:46:00"))
        self.assertEqual((result["outcome"], result["kind"], result["miss_reason"]), ("written", "miss", "deadline_passed"))
        self.assertEqual(self.calls_made(), 0)

    def test_missed_mornings_are_locked_as_misses_and_never_rewritten(self):
        earlier = DATES[:-2]
        self.run_morning(analysis(earlier), session=DATES[-2], structured=answer(earlier[-1]))
        result = self.run_morning()
        # DATES[-2] 那一份晨間版（開盤日是基準日 BASE）整個早上沒跑：今天補一筆漏記
        self.assertEqual(result["backfilled_misses"], [DATES[-2]])
        records = {r["base_date"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual((records[DATES[-2]]["kind"], records[DATES[-2]]["miss_reason"]), ("miss", "deadline_passed"))
        self.assertEqual(records[BASE]["kind"], "forecast")

    def test_a_dry_run_can_replay_a_past_morning_with_the_clock_before_the_open(self):
        log = self.tmp / "run.json"
        result = self.run_morning(dry_run=True, replay=True, log_output=log,
                                  now=datetime.fromisoformat("2026-11-15T10:00:00+08:00"))
        self.assertEqual((result["outcome"], result["kind"], result["base_date"]), ("written", "forecast", BASE))
        self.assertEqual(result["locked_at"], f"{SESSION}T07:30:00+08:00")
        self.assertFalse(self.ledger.exists() and any(self.ledger.rglob("*.json")))
        with self.assertRaises(ValueError):
            self.run_morning(replay=True)

    def test_morning_runs_never_reconcile(self):
        early = DATES[:-1]
        self.run_evening(analysis(early), structured=answer(early[-1]),
                         now=datetime.fromisoformat(f"{early[-1]}T17:40:00+08:00"))
        self.run_morning()
        self.assertEqual(fl.read_ledger(self.ledger)["reconciliations"], [])


class OfficialStartTests(MorningTestCase):
    """F 票：正式起算日（本人點頭後設定）之後的基準日記為正式；之前的照舊是試跑。帳本在寫的當下就鎖定，不能事後改。"""

    def test_evening_and_morning_from_the_official_start_are_official_and_earlier_ones_trial(self):
        two_back = DATES[:-2]
        self.run_evening(analysis(two_back), structured=answer(two_back[-1]), official_since=DATES[-2],
                         now=datetime.fromisoformat(f"{two_back[-1]}T17:40:00+08:00"))
        sql = self.tmp / "forecast.sql"
        self.run_evening(official_since=DATES[-2], sql_output=sql)
        self.run_morning(official_since=DATES[-2])
        modes = {(r["base_date"], r["edition"]): (r["kind"], r["run_mode"]) for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual(modes, {
            (two_back[-1], "evening"): ("forecast", "trial"),
            (DATES[-2], "evening"): ("miss", "official"),  # 起算日當天整天沒跑：補記的漏記也算正式
            (BASE, "evening"): ("forecast", "official"),
            (BASE, "morning"): ("forecast", "official"),
        })
        # 發布給網站的彙總帶著起算日（累積中／可下結論由它算）
        self.assertIn(f'"official_since":"{DATES[-2]}"', sql.read_text(encoding="utf-8"))

    def test_the_module_default_is_used_when_no_start_is_given(self):
        self.assertIsNone(fl.OFFICIAL_SINCE)  # 本人點頭之前不設定：全部是試跑
        self.run_evening()
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"][0]["run_mode"], "trial")


class OverseasRunTests(MorningTestCase):
    def test_evening_sees_the_us_close_of_the_night_before_and_morning_the_close_that_just_finished(self):
        self.run_evening(overseas_dir=self.overseas(BASE, "evening"))
        self.run_morning(overseas_dir=self.overseas(SESSION, "morning"))
        records = {r["edition"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}
        self.assertEqual(records["evening"]["overseas_status"]["us_nasdaq"]["latest_date"], DATES[-2])
        self.assertEqual(records["morning"]["overseas_status"]["us_nasdaq"]["latest_date"], BASE)
        # 本人 11-16 選「交」：晨間版交給模型的輸入裡就有剛收盤那一晚的那斯達克
        morning = records["morning"]
        nasdaq = morning["input"]["overseas"]["markets"]["us_nasdaq"]
        self.assertEqual(nasdaq["recent"][-1], {"date": BASE, "close": 103.02, "change_pct": 2.0})  # 103.02 ÷ 101 − 1
        self.assertEqual(morning["input"]["data_cutoffs"]["us_nasdaq"], BASE)
        self.assertNotIn("record_only", morning)  # 沒有只存不交的東西
        self.assertIn("us_sp500", {item["key"] for item in morning["missing_inputs"]})

    def test_originals_are_kept_in_the_ledger_and_the_record_points_at_them(self):
        self.run_evening(overseas_dir=self.overseas(BASE, "evening"))
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        kept = [item for item in record["sources"] if item["status"] == "ok"]
        self.assertEqual([item["name"] for item in kept], ["eodhd-IXIC.INDX"])
        self.assertTrue((self.ledger / kept[0]["archived_as"]).exists())

    def test_a_dry_run_keeps_no_originals(self):
        self.run_evening(dry_run=True, overseas_dir=self.overseas(BASE, "evening"))
        self.assertFalse((self.ledger / "sources").exists())


class ForeignFlowLayerTests(RunTestCase):
    """G 票：晚間版的輸入多一層外資讀數，從工作集的資料庫與期貨原件算，跟著預測一起鎖進帳本。"""

    def data_root(self):
        import sqlite3

        import foreign_futures
        from test_foreign_futures import FakePost, csv_bytes, row
        root = self.tmp / "data"
        (root / "listed-institutional").mkdir(parents=True)
        (root / "stock-history").mkdir(parents=True)
        con = sqlite3.connect(root / "listed-institutional/listed_institutional.sqlite")
        con.execute("CREATE TABLE institutional (date TEXT, code TEXT, foreign_net INTEGER)")
        # 最後三天外資賣超台積電 100 萬股（× 收盤 1000 = -10 億），之前每天買超 100 萬股
        con.executemany("INSERT INTO institutional VALUES (?,?,?)",
                        [(d, "2330", -1_000_000 if i >= len(DATES) - 3 else 1_000_000) for i, d in enumerate(DATES)])
        con.commit(); con.close()
        con = sqlite3.connect(root / "stock-history/stock-history.sqlite")
        con.execute("CREATE TABLE daily (date TEXT, code TEXT, close REAL, turnover INTEGER)")
        con.executemany("INSERT INTO daily VALUES (?,?,?,?)", [(d, "2330", 1000.0, 100_000_000_000) for d in DATES])
        con.commit(); con.close()
        slash = BASE.replace("-", "/")
        foreign_futures.fetch_day(root / "foreign-futures", BASE, sleep=lambda s: None, post=FakePost({
            "TXF": csv_bytes(row(slash, "臺股期貨", "外資及陸資", -1200)),
            "MXF": csv_bytes(row(slash, "小型臺指期貨", "外資及陸資", 400))}))
        return root

    def test_layer_is_locked_with_the_forecast(self):
        empty_seeds = self.tmp / "no-seeds"
        result = self.run_evening(data_root=self.data_root(), seeds_root=empty_seeds)
        self.assertEqual(result["outcome"], "written")
        # 那斯達克與美元的來源屬 D 票，還沒接上
        self.assertEqual(result["foreign_flow_missing"], ["nasdaq_last_session", "dollar_and_twd_vs_foreign"])
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        layer = record["input"]["foreign_flow"]
        readings = layer["readings"]
        self.assertEqual(readings["streak_and_cumulative"]["streak"], {"side": "sell", "days": 3})
        self.assertEqual(readings["tsmc_split"]["tsmc_billion"], -10.0)
        self.assertEqual(readings["futures_net_position"]["net_contracts"], -1100)   # -1200 + 400/4
        self.assertIn("不代表之後會續漲或續跌", layer["notes"]["spot"])
        self.assertEqual(layer["sources"]["futures_to"], BASE)
        self.assertIn("foreign_flow", record["input"]["parameters"]["layers"])

    def test_nasdaq_numbers_are_sealed_in_the_record_but_not_given_to_the_model(self):
        root = self.data_root()
        real = fr.load_foreign_sources
        nasdaq = [{"date": d, "close": 100 + i} for i, d in enumerate(DATES[:-1])]

        def with_nasdaq(data_root, seeds_root=fr.SEEDS_ROOT):
            sources = real(data_root, seeds_root)
            sources["nasdaq"] = nasdaq
            sources["nasdaq_in_prompt"] = False      # 條款開關關掉時
            return sources

        fr.load_foreign_sources = with_nasdaq
        try:
            self.run_evening(data_root=root, seeds_root=self.tmp / "no-seeds")
        finally:
            fr.load_foreign_sources = real
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(record["input"]["foreign_flow"]["readings"]["nasdaq_last_session"]["status"], "withheld")
        self.assertNotIn("record_only", record["input"]["foreign_flow"])
        kept = record["record_only"]["foreign_flow"]["nasdaq_last_session"]
        self.assertEqual(kept["us_close_date"], DATES[-2])

    def test_without_data_the_forecast_is_still_written_and_the_gap_is_named(self):
        result = self.run_evening()
        self.assertEqual(result["outcome"], "written")
        self.assertEqual(result["foreign_flow_missing"], ["nasdaq_last_session", "streak_and_cumulative", "tsmc_split",
                                                          "dollar_and_twd_vs_foreign", "futures_net_position"])


class LicensedRunTests(MorningTestCase):
    """J 票：晚間、晨間版寫之前抓加權指數的富果分時、查大環境新聞；只用寫的那一刻以前已經有的。"""

    def fake_fugle(self, day):
        class Fake:
            def candles(self, symbol):
                assert symbol == "IX0001"
                return {"date": day, "data": [
                    {"date": f"{day}T09:00:00.000+08:00", "open": 100, "high": 101, "low": 99, "close": 100},
                    {"date": f"{day}T13:30:00.000+08:00", "open": 100, "high": 103, "low": 100, "close": 102}]}
        return Fake()

    def news(self, rows):
        return lambda sql: rows if "FROM watch_news" in sql and "code = ''" in sql else []

    def test_evening_gets_the_base_dates_intraday_and_earlier_news(self):
        rows = [{"code": "", "link": "a", "source": "cna-finance", "title": "美股收高", "summary": "",
                 "published_at": f"{BASE}T05:00:00Z", "fetched_at": f"{BASE}T06:00:00Z"},
                {"code": "", "link": "b", "source": "cna-finance", "title": "寫完之後才抓到", "summary": "",
                 "published_at": f"{BASE}T09:00:00Z", "fetched_at": f"{BASE}T10:00:00Z"}]
        result = self.run_evening(fugle=self.fake_fugle(BASE), news_query=self.news(rows))   # 時鐘 17:40（09:40Z）
        self.assertEqual(result["outcome"], "written")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(record["input"]["intraday"]["summary"]["close"], 102)
        # 執行紀錄（乾跑時上傳成產物）看得出富果那一層有值、是哪一天的
        self.assertEqual(result["licensed"]["intraday"], {"status": "ok", "data_date": BASE})
        self.assertEqual(result["licensed"]["news"]["status"], "ok")
        self.assertEqual([item["title"] for item in record["input"]["news"]["items"]], ["美股收高"])

    def test_a_dry_run_replay_of_an_earlier_day_never_fetches_and_says_why(self):
        # 乾跑重播舊日期：富果與新聞一律缺（不用今天的資料冒充），也不去抓
        calls = []

        class Counting:
            def candles(self, symbol):
                calls.append(symbol)
                return {}

        result = self.run_evening(dry_run=True, as_of=DATES[-2], fugle=Counting(), news_query=lambda sql: calls.append(sql))
        self.assertEqual(calls, [])
        self.assertEqual(result["licensed"]["intraday"]["status"], "missing")
        self.assertIn("重播", result["licensed"]["news"]["reason"])

    def test_the_us_papers_join_the_market_news(self):
        # 本人 2026-11-19：加入美國前幾大報社；寫之前直接抓 RSS，只用寫的那一刻以前發布的
        us = [{"source": "wsj-markets", "link": "w", "title": "Stocks fall", "summary": "",
               "published_at": f"{BASE}T03:00:00Z", "fetched_at": f"{BASE}T09:40:00Z"}]
        asked = []

        def feeds(names, *, now):
            asked.append(tuple(names))
            return us, {}

        result = self.run_evening(news_query=self.news([]), feeds=feeds)
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(asked, [("cnbc-markets", "nyt-business", "wsj-markets")])
        self.assertEqual([item["title"] for item in record["input"]["news"]["us_items"]], ["Stocks fall"])
        self.assertEqual(record["input"]["news"]["us_items"][0]["source"], "華爾街日報 市場")
        # 執行紀錄（乾跑產物）不含完整輸入：新聞的筆數直接寫在 licensed.news
        self.assertEqual((result["licensed"]["news"]["items"], result["licensed"]["news"]["us_items"]), (0, 1))
        self.assertEqual(result["licensed"]["news"]["status"], "ok")

    def test_morning_never_sees_the_session_days_intraday(self):
        # 晨間版：富果回的是開盤日（例如時鐘之後已經進入新的一天）就整塊缺，不會看到開盤日的盤中
        result = self.run_morning(fugle=self.fake_fugle(SESSION))
        self.assertEqual(result["outcome"], "written")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(record["input"]["intraday"]["status"], "missing")
        self.assertIn("intraday", {item["key"] for item in record["missing_inputs"]})

    def test_without_a_key_or_a_connection_both_are_missing_and_the_forecast_is_written(self):
        result = self.run_evening()
        self.assertEqual(result["outcome"], "written")
        self.assertTrue({"intraday", "news"} <= set(result["licensed_missing"]))


class MarketLayersRunTests(MorningTestCase):
    """I 票：晚間、晨間版都從工作集讀大盤五層（融資讀 analysis-data.js 旁邊的 chips-data.js）。"""

    def chips_file(self):
        # 最後 6 天融資餘額 1000、1010、1005、1020、1030、1040.5（億元）
        balances = [1000.0, 1010.0, 1005.0, 1020.0, 1030.0, 1040.5]
        margin = [{"date": d, "margin_balance_billion": b, "margin_units": 2_000_000, "short_units": 60_000}
                  for d, b in zip(DATES[-6:], balances)]
        (self.tmp / "chips-data.js").write_text(
            "window.MARKET_CHIPS = " + json.dumps({"margin": margin}) + ";\n", encoding="utf-8")

    def test_evening_layer_is_sealed_and_missing_layers_are_named(self):
        self.chips_file()
        result = self.run_evening(data_root=self.tmp / "data", seeds_root=self.tmp / "no-seeds")
        self.assertEqual(result["outcome"], "written")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        margin = record["input"]["market_margin"]
        self.assertEqual((margin["status"], margin["data_date"]), ("ok", BASE))
        self.assertEqual(margin["change_5d_billion"], 40.5)     # 1040.5 − 1000
        self.assertEqual(record["input"]["otc_index"]["status"], "missing")
        self.assertEqual(result["market_layers_missing"], ["otc_index", "sector_share", "screens", "ofr_stress"])
        missing = {item["key"] for item in record["missing_inputs"]}
        self.assertTrue({"otc_index", "sector_share", "screens", "ofr_stress"} <= missing)
        self.assertNotIn("market_margin", missing)
        names = {item["key"]: item.get("name") for item in record["missing_inputs"]}
        self.assertEqual(names["ofr_stress"], "OFR 金融壓力指數")   # 網站「缺漏」那一列顯示的名字

    def test_ofr_is_fetched_before_writing_and_cut_at_what_was_published(self):
        # 本人 2026-11-18 晚「預測時直接抓」：假的 OFR 回應一路到基準日；只用基準日往前第二個平日（含）以前的分數。
        from test_market_layers import ofr_csv
        import market_layers
        calls = []

        def fetch():
            calls.append(1)
            return market_layers.parse_ofr(ofr_csv(BASE))

        result = self.run_evening(ofr_fetch=fetch)
        self.assertEqual((result["outcome"], len(calls)), ("written", 1))
        layer = fl.read_ledger(self.ledger)["forecasts"][0]["input"]["ofr_stress"]
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["observed_at"], market_layers.ofr_cutoff(BASE, "evening", None))
        self.assertLess(layer["observed_at"], BASE)

    def test_an_ofr_timeout_leaves_that_layer_missing_and_the_forecast_is_still_written(self):
        def timed_out():
            raise TimeoutError("timed out")

        result = self.run_evening(ofr_fetch=timed_out)
        self.assertEqual(result["outcome"], "written")
        self.assertIn("ofr_stress", result["market_layers_missing"])
        self.assertEqual(result["market_layers_errors"]["ofr_stress"], "TimeoutError")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(record["input"]["ofr_stress"]["status"], "missing")
        self.assertIn("ofr_stress", {item["key"] for item in record["missing_inputs"]})

    def test_the_command_line_fetches_ofr_unless_told_not_to(self):
        import market_layers
        seen = {}
        real = fr.run_evening
        fr.run_evening = lambda *args, **kwargs: seen.update(kwargs) or {}
        try:
            base = ["--analysis", "a.js", "--ledger", "l", "--method", "m", "--agy-bin", "agy"]
            fr.main(base)
            self.assertIs(seen["ofr_fetch"], market_layers.fetch_ofr)
            fr.main(base + ["--no-ofr-fetch"])
            self.assertIsNone(seen["ofr_fetch"])
        finally:
            fr.run_evening = real

    def test_morning_reads_the_same_layers(self):
        self.chips_file()
        result = self.run_morning(data_root=self.tmp / "data", seeds_root=self.tmp / "no-seeds")
        self.assertEqual(result["outcome"], "written")
        record = fl.read_ledger(self.ledger)["forecasts"][0]
        self.assertEqual(record["input"]["market_margin"]["data_date"], BASE)


class OverseasFeedsForeignFlowTests(MorningTestCase):
    """D 票與 G 票接在一起（owner 2026-11-16 整合）：海外層的那斯達克序列就是外資讀數 1 用的那一份。"""

    def test_the_nasdaq_series_feeds_the_foreign_flow_reading_of_each_edition(self):
        # 晚間版看基準日前一晚的美股；晨間版看開盤日之前剛收盤的那一晚（就是基準日當晚）。
        self.run_evening(overseas_dir=self.overseas(BASE, "evening"))
        self.run_morning(overseas_dir=self.overseas(SESSION, "morning"))
        records = {r["edition"]: r for r in fl.read_ledger(self.ledger)["forecasts"]}
        evening = records["evening"]["input"]["foreign_flow"]["readings"]["nasdaq_last_session"]
        morning = records["morning"]["input"]["foreign_flow"]["readings"]["nasdaq_last_session"]
        self.assertEqual((evening["status"], evening["us_close_date"], evening["change_pct"]), ("ok", DATES[-2], 1.0))
        self.assertEqual((morning["status"], morning["us_close_date"], morning["change_pct"]), ("ok", BASE, 2.0))

    def test_without_overseas_data_the_reading_is_missing_and_the_forecast_is_still_written(self):
        result = self.run_evening()
        self.assertEqual(result["outcome"], "written")
        self.assertIn("nasdaq_last_session", result["foreign_flow_missing"])

if __name__ == "__main__":
    unittest.main()
