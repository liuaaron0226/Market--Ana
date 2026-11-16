"""歷史重播（本人 2026-11-17「要，重播當參考」）的執行程式入口。

假模型經真的子程序邊界呼叫：讀提示裡的 INPUT_JSON，照抄基準日、每一格挑第一個能用的上方與下方候選，
並把收到的輸入存下來，好檢查它有沒有看到「當時看不到」的資料。
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import forecast_ledger as fl
import forecast_replay as rp
from test_forecast_run import analysis, weekdays

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
        options = dict(start="2026-04-01", end="2026-12-31", editions=("evening", "morning"))
        options.update(kwargs)
        return rp.run_replay(self.analysis_file(payload or analysis(DATES)), ledger=self.ledger,
                             method_path=self.method, model_bin=self.model(fail=fail), model="gemini-3.8-flash",
                             fetched_at="2026-11-17T12:00:00+08:00", **options)


class PlanTests(ReplayTestCase):
    def test_only_days_after_the_model_cutoff_with_chips_and_no_method_spoilers_are_replayable(self):
        # 法人只從 06-01 起才有：之前的日子缺籌碼。方法稿 0.4 寫了 10-12 起的行情：二十日到期日在 10-12（含）以後的不重播。
        payload = analysis(DATES)
        payload["institutional"] = [row for row in payload["institutional"] if row["date"] >= "2026-06-01"]
        plan = rp.plan_replay(payload, method_text=METHOD_04, model="gemini-3.8-flash",
                              start="2026-03-02", end="2026-12-31")
        last_clean = DATES[DATES.index("2026-10-12") - 21]  # 二十個交易日後剛好是 10-09
        self.assertEqual(plan["dates"][0], "2026-06-01")
        self.assertEqual(plan["dates"][-1], last_clean)
        self.assertEqual(plan["skipped"]["沒有當天的法人或期貨資料"][0], "2026-04-01")
        self.assertIn(DATES[DATES.index(last_clean) + 1], plan["skipped"]["方法稿已寫了這段行情的結果"])
        self.assertEqual(plan["skipped"]["方法稿已寫了這段行情的結果"][-1], DATES[-1])
        # 早晚兩版各一份：模型呼叫次數至少是份數，每份不合格最多重試一次
        self.assertEqual(plan["forecasts"], 2 * len(plan["dates"]))
        self.assertEqual(plan["model_calls"], {"at_least": plan["forecasts"], "at_most": 2 * plan["forecasts"]})

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
