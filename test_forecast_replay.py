"""歷史重播（本人 2026-11-17「要，重播當參考」）的執行程式入口。

假模型經真的子程序邊界呼叫：讀提示裡的 INPUT_JSON，照抄基準日、每一格挑第一個能用的上方與下方候選，
並把收到的輸入存下來，好檢查它有沒有看到「當時看不到」的資料。
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from datetime import datetime

import forecast_ledger as fl
import forecast_replay as rp
import forecast_replay_chips as chips
from test_forecast_replay_chips import FakeHttp, bfi82u, taifex
from test_forecast_run import analysis, weekdays

NOON = datetime.fromisoformat("2026-11-17T12:00:00+08:00")

# 方法稿 0.4 寫了 2026-10-12 至 11-13 的實際走勢（〈逐日對照〉）。
METHOD_04 = "# 大盤分析方法\n\n版本：0.4，2026-11-15。\n"
# 2025-12-01 起 230 個平日（最後一天 2026-10-16）：04-01 之前已有八十多根，走勢標籤算得出來。
DATES = weekdays("2025-12-01", 230)


def next_weekdays(day, count):
    out, current = [], date.fromisoformat(day)
    while len(out) < count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            out.append(current.isoformat())
    return out


class ReplayTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ledger = self.tmp / "replay-ledger"
        self.method = self.tmp / "MARKET_METHOD.md"
        self.method.write_text(METHOD_04, encoding="utf-8")
        self.seen = self.tmp / "seen.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def analysis_file(self, payload):
        path = self.tmp / "analysis-data.js"
        path.write_text("window.MARKET_ANALYSIS = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
        return path

    def model(self, *, fail=False):
        script = self.tmp / "fake_agy.py"
        script.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "prompt=json.loads(sys.stdin.buffer.readline().decode('utf-8'))['message']['content']\n"
            "data=json.loads(prompt.split('INPUT_JSON:\\n',1)[1])\n"
            f"Path({str(self.seen)!r}).open('a',encoding='utf-8').write(json.dumps(data,ensure_ascii=False)+'\\n')\n"
            f"if {fail!r}:\n    sys.stderr.write('quota exceeded\\n'); raise SystemExit(1)\n"
            "cells={}\n"
            "for h in ('1','5','20'):\n"
            "    ok=[c for c in data['candidates'] if c.get('horizon') in (None,int(h))]\n"
            "    up=[c['id'] for c in ok if c['side']=='above'][-1]\n"
            "    down=[c['id'] for c in ok if c['side']=='below'][0]\n"
            "    cells[h]={'strong_line':up,'weak_line':down,'zone':'between','reason':'量價配合','reason_refs':[],"
            "'invalidation':'跌破轉弱線','invalidation_refs':[]}\n"
            "out={'base_date':data['base_date'],'regime':'多頭延續','cells':cells}\n"
            "env={'event':'result','result':{'status':'SUCCESS','structured_output':out}}\n"
            "sys.stdout.buffer.write(json.dumps(env,ensure_ascii=False).encode('utf-8')+b'\\n')\n",
            encoding="utf-8")
        return script

    def inputs(self):
        if not self.seen.exists():
            return []
        return [json.loads(line) for line in self.seen.read_text(encoding="utf-8").splitlines()]

    def run_replay(self, payload=None, *, fail=False, **kwargs):
        options = dict(start="2026-04-01", end="2026-09-08", editions=("evening", "morning"), now=lambda: NOON)
        options.update(kwargs)
        return rp.run_replay(self.analysis_file(payload or analysis(DATES)), ledger=self.ledger,
                             method_path=self.method, model_bin=self.model(fail=fail), model="gemini-3.8-flash",
                             fetched_at="2026-11-17T12:00:00+08:00", **options)


class PlanTests(ReplayTestCase):
    def test_only_days_after_the_model_cutoff_with_chips_are_replayable(self):
        # 法人只從 06-01 起才有：之前的日子缺籌碼
        payload = analysis(DATES)
        payload["institutional"] = [row for row in payload["institutional"] if row["date"] >= "2026-06-01"]
        plan = rp.plan_replay(payload, method_text=METHOD_04, model="gemini-3.8-flash",
                              start="2026-03-02", end="2026-09-08")
        self.assertEqual((plan["dates"][0], plan["dates"][-1]), ("2026-06-01", "2026-09-08"))
        self.assertEqual(plan["skipped"]["沒有當天的法人或期貨資料"][0], "2026-04-01")
        self.assertEqual(plan["skipped"]["模型知識截止日（2026-03）以前"][0], "2026-03-02")
        # 早晚兩版各一份：模型呼叫次數至少是份數，每份不合格最多重試一次
        self.assertEqual(plan["forecasts"], 2 * len(plan["dates"]))
        self.assertEqual(plan["model_calls"], {"at_least": plan["forecasts"], "at_most": 2 * plan["forecasts"]})

    def test_days_whose_outcome_the_method_text_already_describes_are_never_replayed(self):
        # 方法稿 0.4 寫了 10-12 起的行情：二十日到期日在 10-12（含）以後、基準日在 11-13 以前的不重播。
        # （09-08 的上限已經擋在前面；這一道是以後換方法稿或放寬上限時的保險。）
        original = rp.REPLAY_UNTIL
        rp.REPLAY_UNTIL = "2026-12-31"
        self.addCleanup(setattr, rp, "REPLAY_UNTIL", original)
        plan = rp.plan_replay(analysis(DATES), method_text=METHOD_04, model="gemini-3.8-flash",
                              start="2026-04-01", end="2026-12-31")
        last_clean = DATES[DATES.index("2026-10-12") - 21]  # 二十個交易日後剛好是 10-09
        self.assertEqual(plan["dates"][-1], last_clean)
        spoiled = plan["skipped"]["方法稿已寫了這段行情的結果"]
        self.assertEqual((spoiled[0], spoiled[-1]), (DATES[DATES.index(last_clean) + 1], DATES[-1]))

    def test_the_owners_upper_bound_of_september_eighth_is_never_passed(self):
        # 本人 2026-11-17：基準日上限 09-08；迄日填得再晚也一樣
        plan = rp.plan_replay(analysis(weekdays("2025-12-01", 260)), method_text=METHOD_04, model="gemini-3.8-flash",
                              start="2026-04-01", end="2026-12-31")
        self.assertEqual(plan["dates"][-1], "2026-09-08")

    def test_backfilled_chips_make_the_days_before_the_pack_replayable(self):
        payload = analysis(DATES)
        payload["institutional"] = [row for row in payload["institutional"] if row["date"] >= "2026-06-01"]
        payload["futures"] = [row for row in payload["futures"] if row["date"] >= "2026-06-01"]
        needed = chips.needed_dates(payload, start="2026-04-01")
        http = FakeHttp({"https://www.twse.com.tw/": bfi82u(-5),
                         "https://www.taifex.com.tw/": taifex([d for d in DATES if d < "2026-06-01"])})
        folder = self.tmp / "chips-backfill"
        chips.fetch(folder, needed, http=http, now=lambda: "x", pause=0)
        plan = rp.plan_replay(chips.merge(payload, chips.load(folder)), method_text=METHOD_04,
                              model="gemini-3.8-flash", start="2026-04-01", end="2026-09-08")
        self.assertEqual(plan["dates"][0], "2026-04-01")
        self.assertNotIn("沒有當天的法人或期貨資料", plan["skipped"])

    def test_days_before_the_models_knowledge_cutoff_are_never_replayed(self):
        plan = rp.plan_replay(analysis(weekdays("2026-02-02", 120)), method_text=METHOD_04, model="gemini-3.8-flash",
                              start="2026-01-01", end="2026-12-31")
        self.assertTrue(plan["dates"])
        self.assertEqual(plan["dates"][0], "2026-04-01")
        self.assertIn("2026-03-31", plan["skipped"]["模型知識截止日（2026-03）以前"])

    def test_an_unknown_model_or_method_version_is_refused_until_someone_checks_it(self):
        with self.assertRaisesRegex(ValueError, "知識截止日"):
            rp.plan_replay(analysis(DATES), method_text=METHOD_04, model="gemini-9", start="2026-04-01", end="2026-12-31")
        with self.assertRaisesRegex(ValueError, "方法稿"):
            rp.plan_replay(analysis(DATES), method_text="版本：0.9\n", model="gemini-3.8-flash",
                           start="2026-04-01", end="2026-12-31")


class RunTests(ReplayTestCase):
    def test_each_day_is_written_as_replay_evening_and_morning_seeing_only_what_was_known_then(self):
        log = self.run_replay(end="2026-04-03")
        records = fl.read_ledger(self.ledger)["forecasts"]
        self.assertEqual([(r["base_date"], r["edition"]) for r in records],
                         [(d, e) for d in ("2026-04-01", "2026-04-02", "2026-04-03") for e in ("evening", "morning")])
        self.assertEqual({(r["kind"], r["run_mode"]) for r in records}, {("forecast", "replay")})
        self.assertEqual(records[0]["replay"]["model_knowledge_cutoff"], "2026-03")
        self.assertEqual(records[0]["replay"]["fetched_at"], "2026-11-17T12:00:00+08:00")
        for seen in self.inputs():
            self.assertEqual(seen["index_recent"][-1]["date"], seen["base_date"])
            self.assertLessEqual(seen["data_cutoffs"]["institutional"], seen["base_date"])
        self.assertEqual((log["written"], log["stop_reason"]), (6, None))

    def test_lessons_only_see_outcomes_that_had_expired_by_the_base_date(self):
        self.run_replay(end="2026-04-08", editions=("evening",))

        def totals(day):
            counts = next(seen for seen in self.inputs() if seen["base_date"] == day)["lessons"]["counts"]
            return {h: item["hits"] + item["misses"] for h, item in counts.items()}

        self.assertEqual(totals("2026-04-01"), {"1": 0, "5": 0, "20": 0})
        self.assertEqual(totals("2026-04-02"), {"1": 1, "5": 0, "20": 0})  # 04-01 的一日格 04-02 到期
        self.assertEqual(totals("2026-04-07"), {"1": 4, "5": 0, "20": 0})
        self.assertEqual(totals("2026-04-08"), {"1": 5, "5": 1, "20": 0})  # 04-01 的五日格 04-08 到期

    def test_a_budget_stops_the_run_and_the_next_run_continues_without_rewriting(self):
        first = self.run_replay(end="2026-04-03", max_forecasts=3)
        self.assertEqual((first["written"], first["stop_reason"]), (3, "到了這次的份數上限"))
        calls_after_first = len(self.inputs())
        second = self.run_replay(end="2026-04-03", max_forecasts=3)
        self.assertEqual(second["written"], 3)
        self.assertEqual(len(self.inputs()) - calls_after_first, 3)  # 已寫的不再呼叫模型
        self.assertEqual(len(fl.read_ledger(self.ledger)["forecasts"]), 6)

    def test_no_more_than_forty_forecasts_a_day_across_runs(self):
        # 本人 2026-11-17「分天跑，每天最多 40 份」：同一天觸發兩次也一樣
        first = self.run_replay(max_forecasts=30, log_output=self.ledger / "runs" / "1.json")
        self.assertEqual(first["written"], 30)
        second = self.run_replay(max_forecasts=30, log_output=self.ledger / "runs" / "2.json")
        self.assertEqual((second["written"], second["stop_reason"]), (10, "今天已寫滿 40 份"))
        tomorrow = self.run_replay(max_forecasts=30, now=lambda: datetime.fromisoformat("2026-11-18T12:00:00+08:00"),
                                   log_output=self.ledger / "runs" / "3.json")
        self.assertEqual(tomorrow["written"], 30)

    def test_it_only_runs_between_the_morning_and_evening_forecasts(self):
        # 07:20 晨間版、收盤後晚間版的前後都不跑，免得剛好把額度用完害正式的漏記
        for moment in ("2026-11-17T08:00:00+08:00", "2026-11-17T18:30:00+08:00", "2026-11-17T02:00:00+08:00"):
            log = self.run_replay(end="2026-04-03", now=lambda moment=moment: datetime.fromisoformat(moment))
            self.assertEqual(log["written"], 0, moment)
            self.assertIn("可重播的時段", log["stop_reason"])
        self.assertEqual(self.inputs(), [])

    def test_a_model_failure_stops_the_run_and_locks_nothing(self):
        log = self.run_replay(end="2026-04-03", fail=True)
        self.assertEqual(log["written"], 0)
        self.assertIn("模型呼叫失敗", log["stop_reason"])
        self.assertEqual(fl.read_ledger(self.ledger)["forecasts"], [])
        self.assertEqual(len(self.inputs()), 1)  # 第一次失敗就停，不再往下打

    def test_the_database_file_goes_to_the_replay_tables_and_reconciles_with_all_data(self):
        sql = self.tmp / "replay.sql"
        self.run_replay(end="2026-04-03", sql_output=sql)
        text = sql.read_text(encoding="utf-8")
        self.assertIn("INSERT INTO forecast_replay_records", text)
        self.assertIn("INSERT INTO forecast_replay_summaries", text)
        self.assertNotIn("INSERT INTO forecast_records", text)
        # 最後用全部日 K 對帳：三天的一日、五日、二十日格都已到期
        self.assertEqual(len(fl.read_ledger(self.ledger)["reconciliations"]), 6 * 3)


if __name__ == "__main__":
    unittest.main()
