"""盤勢解讀產生器（market_brief.py）在命令列入口的契約（.scratch/market-brief/contract.md，票 02）。

假的模型回答 → 檢查寫出的 SQL（實際套進 0035 的表）、payload、印出來的報告與不公開的報告。
用法：python -m unittest test_market_brief
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import gemini_usage
import market_brief as mb
from test_plain_daily import market   # 和白話分析同一份合成資料（資料日 2026-11-06：外資賣超、淨空單增加、指數在均線之下）

ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "cloud/migrations/0035_market_brief.sql"
NOW = datetime(2026, 11, 6, 10, 31, tzinfo=timezone.utc)   # 台北 18:31
DAY = "2026-11-06"


def good(**overrides):
    """照規則寫的回答：自由文字沒有數字，數字一律 {代號}。"""
    answer = {
        "one_line": "籌碼面與技術面同步偏空，{foreign_streak}。",
        "lead": "tech",
        "cats": [
            {"key": "chips", "say": "外資現貨與期貨部位同步偏空：{foreign_today}，{futures_week}。",
             "effect": "賣壓主要來自外資，權值股首當其衝。",
             "conditions": ["外資現貨是否轉為買超"],
             "prepare": ["如果外資現貨轉為買超，就先確認台指期淨空單是否同步減少。"]},
            {"key": "tech", "say": "指數跌破短期均線：{ma20_side}。",
             "effect": "反彈易遇均線反壓。",
             "conditions": ["收盤是否站回 {ma20_line}"],
             "prepare": ["如果收盤站回 {ma20_line}，就先確認外資現貨是否同步轉買。"]},
        ],
    }
    answer.update(overrides)
    return answer


def with_cat(key, **fields):
    answer = good()
    answer["cats"] = [{**cat, **fields} if cat["key"] == key else cat for cat in answer["cats"]]
    return answer


class Model:
    """假的模型：照順序回答；記下每一次的提示。回答是字串時當成失敗的狀態。"""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        answer = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        return (answer, None, "模型說了一句不該公開的話") if isinstance(answer, str) else ("ok", answer, "")


class Cli(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.pack(*market())

    def pack(self, analysis, chips):
        (self.folder / "analysis-data.js").write_text("window.MARKET_ANALYSIS = " + json.dumps(analysis) + ";\n", encoding="utf-8")
        (self.folder / "chips-data.js").write_text("window.MARKET_CHIPS = " + json.dumps(chips) + ";\n", encoding="utf-8")

    def run_cli(self, model, *extra, slot="evening"):
        """回傳（印出來的報告, 不公開的報告, SQL, 整段 stdout）。"""
        sql, report, out = self.folder / "brief.sql", self.folder / "private" / "report.json", io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mb.main(["run", "--slot", slot, "--analysis", str(self.folder / "analysis-data.js"),
                            "--sql-output", str(sql), "--report", str(report), *extra], ask_model=model, now=NOW)
        self.assertEqual(code, 0)
        printed = out.getvalue()
        return (json.loads(printed.strip().splitlines()[-1]), json.loads(report.read_text(encoding="utf-8")),
                sql.read_text(encoding="utf-8"), printed)

    def stored(self, sql, before=""):
        """把 SQL 套進 0035 的表，回傳所有列。"""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(MIGRATION.read_text(encoding="utf-8"))
        db.executescript(before)
        db.executescript(sql)
        return [dict(row) for row in db.execute("SELECT * FROM market_brief_runs ORDER BY trade_date, slot")]

    def payload(self, sql):
        rows = self.stored(sql)
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0]["payload"])

    # ---- 合格的回答 ----

    def test_a_valid_answer_becomes_the_payload_of_the_contract(self):
        printed, _, sql, _ = self.run_cli(Model(good()))
        row = self.stored(sql)[0]
        self.assertEqual((row["trade_date"], row["slot"], row["source"], row["model"], row["generated_at"]),
                         (DAY, "evening", "ai", mb.MODEL_DEFAULT, "2026-11-06T18:31:00+08:00"))
        payload = json.loads(row["payload"])
        self.assertEqual({key: payload[key] for key in ("schema", "trade_date", "slot", "generated_at", "source", "lead")},
                         {"schema": "market-brief/v1", "trade_date": DAY, "slot": "evening",
                          "generated_at": "2026-11-06T18:31:00+08:00", "source": "ai", "lead": "tech"})
        self.assertEqual(sorted(payload), ["cats", "generated_at", "lead", "one_line", "schema", "slot", "source", "trade_date"])
        self.assertEqual([cat["key"] for cat in payload["cats"]], ["chips", "tech"])
        for cat in payload["cats"]:
            self.assertEqual(sorted(cat), ["conditions", "data_date", "effect", "key", "prepare", "quiet", "say"])
            self.assertEqual((cat["data_date"], cat["quiet"]), (DAY, False))
        self.assertEqual((printed["status"], printed["source"], printed["attempts"], printed["categories"]),
                         ("ok", "ai", 1, ["chips", "tech"]))

    def test_numbers_come_from_the_facts_not_from_the_model(self):
        payload = self.payload(self.run_cli(Model(good()))[2])
        chips, tech = payload["cats"]
        self.assertEqual(payload["one_line"], "籌碼面與技術面同步偏空，外資現貨連 60 天賣超。")
        self.assertEqual(chips["say"], "外資現貨與期貨部位同步偏空：外資現貨今天賣超 912 億，外資台指期淨空單比一週前增加 4,279 口。")
        self.assertEqual(tech["say"], "指數跌破短期均線：加權指數在 20 日均線之下。")
        self.assertRegex(tech["conditions"][0], r"^收盤是否站回 20 日均線（約 [\d,]+ 點）$")
        texts = [payload["one_line"]] + [line for cat in payload["cats"]
                                         for line in (cat["say"], cat["effect"], *cat["conditions"], *cat["prepare"])]
        self.assertFalse([line for line in texts if "{" in line or "}" in line], "代號都換成了事實")

    def test_the_model_only_sees_the_facts_of_this_shift(self):
        model = Model(good())
        self.run_cli(model)
        prompt = model.prompts[0]
        for needed in ("FACTS:", "foreign_today", "ma20_side", "如果…就…", "chips", "tech"):
            self.assertIn(needed, prompt)

    def test_more_than_two_conditions_are_cut_and_missing_ones_are_filled(self):
        many = with_cat("chips", conditions=["外資現貨是否轉為買超", "投信是否持續買超", "融資餘額是否止穩"], prepare=[])
        chips = self.payload(self.run_cli(Model(many))[2])["cats"][0]
        self.assertEqual(chips["conditions"], ["外資現貨是否轉為買超", "投信是否持續買超"])
        self.assertTrue(chips["prepare"], "模型沒寫的因應用程式套句補上")
        self.assertTrue(all("如果" in line and "就" in line for line in chips["prepare"]))

    def test_a_lead_outside_the_categories_falls_back_to_the_first(self):
        printed, _, sql, _ = self.run_cli(Model(good(lead="overseas")))
        self.assertEqual((self.payload(sql)["lead"], printed["source"], printed["attempts"]), ("chips", "ai", 1))

    # ---- 不合格的回答 ----

    def test_an_invalid_answer_is_asked_again_with_the_reason(self):
        bad = with_cat("chips", say="外資現貨賣超 912 億，賣壓沉重。")
        model = Model(bad, good())
        printed, private, sql, _ = self.run_cli(model)
        self.assertEqual((printed["source"], printed["attempts"], printed["rejected"]), ("ai", 2, 1))
        self.assertEqual(len(model.prompts), 2)
        self.assertIn("上一次的回答被退回", model.prompts[1])
        self.assertIn("外資現貨賣超 912 億，賣壓沉重。", model.prompts[1])
        self.assertEqual(self.payload(sql)["source"], "ai")
        problem = private["rejected"][0]["problems"][0]
        self.assertIn("數字", problem["reason"])
        self.assertEqual(problem["text"], "外資現貨賣超 912 億，賣壓沉重。")

    def test_two_invalid_answers_fall_back_to_the_template(self):
        bad = with_cat("tech", effect="建議買進權值股。")
        model = Model(bad, bad)
        printed, _, sql, _ = self.run_cli(model)
        self.assertEqual((printed["status"], printed["source"], printed["attempts"], printed["failure"]),
                         ("ok", "template", 2, "validation_failed"))
        self.assertEqual(len(model.prompts), 2)
        row = self.stored(sql)[0]
        self.assertEqual((row["source"], row["model"]), ("template", None))
        payload = json.loads(row["payload"])
        self.assertEqual(payload["source"], "template")
        self.assertEqual(payload["one_line"], "盤勢偏空：籌碼面偏空、技術面偏弱。")
        chips, tech = payload["cats"]
        self.assertEqual(chips["say"], "籌碼面偏空：外資現貨連 60 天賣超，外資台指期淨空單 99,930 口。")
        self.assertEqual(tech["say"], "技術面偏弱：加權指數在 20 日均線之下。")
        for cat in payload["cats"]:
            self.assertTrue(cat["effect"])
            self.assertTrue(1 <= len(cat["conditions"]) <= 2 and 1 <= len(cat["prepare"]) <= 2)
            self.assertTrue(all("如果" in line and "就" in line for line in cat["prepare"]))

    def rejected_reason(self, answer):
        printed, private, _, _ = self.run_cli(Model(answer, answer))
        self.assertEqual(printed["source"], "template")
        return private["rejected"][0]["problems"][0]["reason"]

    def test_trading_instructions_are_rejected(self):
        for answer in (with_cat("chips", effect="外資賣壓沉重，建議減碼權值股。"),
                       with_cat("chips", prepare=["如果外資轉為買超，就逢低布局權值股。"]),
                       with_cat("tech", prepare=["如果收盤站回 {ma20_line}，就進場。"]),
                       good(one_line="籌碼偏空，應該停損出場。")):
            with self.subTest(answer=answer):
                self.assertIn("買賣指令", self.rejected_reason(answer))

    def test_a_prepare_that_is_not_a_scenario_is_rejected(self):
        self.assertIn("如果…就…", self.rejected_reason(with_cat("tech", prepare=["留意均線位置。"])))

    def test_numbers_that_are_not_from_the_facts_are_rejected(self):
        for answer in (with_cat("chips", say="外資現貨賣超 900 億。"),
                       with_cat("tech", effect="指數距離均線還有兩千點。"),
                       with_cat("tech", say="指數跌破均線：{ma99_side}。"),
                       good(one_line="外資連五天賣超。")):
            with self.subTest(answer=answer):
                reason = self.rejected_reason(answer)
                self.assertTrue("數字" in reason or "不在事實表" in reason, reason)

    def test_a_category_cannot_borrow_the_facts_of_another(self):
        self.assertIn("不屬於這一類", self.rejected_reason(with_cat("tech", say="技術面偏弱：{foreign_today}。")))

    def test_a_direction_against_the_numbers_is_rejected(self):
        self.assertIn("方向矛盾", self.rejected_reason(with_cat("chips", say="外資買超，籌碼面轉強。")))
        self.assertIn("方向矛盾", self.rejected_reason(good(one_line="指數收紅，盤勢偏多。")))

    def test_a_missing_category_is_rejected(self):
        answer = good()
        answer["cats"] = answer["cats"][:1]
        self.assertIn("少了 tech", self.rejected_reason(answer))

    # ---- 模型沒回來、資料不夠 ----

    def test_a_model_failure_uses_the_template_without_asking_again(self):
        model = Model("model_timeout")
        printed, _, sql, out = self.run_cli(model)
        self.assertEqual((printed["status"], printed["source"], printed["attempts"], printed["failure"], printed["model_called"]),
                         ("ok", "template", 1, "model_timeout", True))
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(self.payload(sql)["source"], "template")
        self.assertNotIn("不該公開", out)
        self.assertEqual(printed["detail_chars"], len("模型說了一句不該公開的話"))

    def test_without_agy_the_template_is_written_and_no_call_is_counted(self):
        sql, out = self.folder / "brief.sql", io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mb.main(["run", "--slot", "evening", "--analysis", str(self.folder / "analysis-data.js"),
                            "--sql-output", str(sql), "--agy-bin", str(self.folder / "no-such-agy")], now=NOW)
        printed = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((code, printed["source"], printed["model_called"], printed["failure"]), (0, "template", False, "agy_unavailable"))
        self.assertEqual(self.payload(sql.read_text(encoding="utf-8"))["source"], "template")

    def test_a_model_that_raises_never_fails_the_command(self):
        def broken(prompt, schema):
            raise RuntimeError("boom")
        printed, _, sql, _ = self.run_cli(broken)
        self.assertEqual((printed["status"], printed["source"], printed["failure"]), ("ok", "template", "model_failed"))
        self.assertEqual(len(self.stored(sql)), 1)

    def test_too_little_data_writes_nothing_and_still_exits_zero(self):
        analysis, chips = market()
        analysis["daily"] = analysis["daily"][-5:]
        self.pack(analysis, chips)
        model = Model(good())
        printed, _, sql, _ = self.run_cli(model)
        self.assertEqual((printed["status"], sql, model.prompts), ("failed", "", []))
        self.assertTrue(printed["failure"].startswith("facts_failed"))

    def test_a_missing_pack_writes_nothing_and_still_exits_zero(self):
        (self.folder / "chips-data.js").unlink()
        printed, _, sql, _ = self.run_cli(Model(good()))
        self.assertEqual((printed["status"], sql), ("failed", ""))
        self.assertTrue(printed["failure"].startswith("input_failed"))

    # ---- SQL ----

    def test_the_sql_has_no_transaction_and_cleans_up_after_35_days(self):
        _, _, sql, _ = self.run_cli(Model(good()))
        self.assertNotRegex(sql.upper(), r"\b(BEGIN|COMMIT|ROLLBACK)\b")
        self.assertIn("DELETE FROM market_brief_runs WHERE trade_date < '2026-10-02';", sql)
        old = ("INSERT INTO market_brief_runs VALUES ('2026-10-01', 'evening', 'x', 'ai', NULL, '{}');"
               "INSERT INTO market_brief_runs VALUES ('2026-10-02', 'evening', 'x', 'ai', NULL, '{}');")
        self.assertEqual([row["trade_date"] for row in self.stored(sql, old)], ["2026-10-02", DAY])

    def test_running_the_same_shift_again_replaces_the_row(self):
        _, _, first, _ = self.run_cli(Model("model_failed"))
        _, _, second, _ = self.run_cli(Model(good()))
        rows = self.stored(second, first)
        self.assertEqual([(row["trade_date"], row["slot"], row["source"]) for row in rows], [(DAY, "evening", "ai")])
        self.assertEqual(json.loads(rows[0]["payload"])["source"], "ai")

    def test_a_dry_run_asks_the_model_but_writes_no_sql(self):
        model = Model(good())
        printed, private, sql, _ = self.run_cli(model, "--dry-run")
        self.assertEqual((sql, printed["dry_run"], printed["source"], len(model.prompts)), ("", True, "ai", 1))
        self.assertEqual(private["payload"]["source"], "ai")

    # ---- 時段 ----

    def test_every_slot_is_accepted_and_daytime_shifts_are_dated_today(self):
        for slot in ("morning", "midday", "close"):
            with self.subTest(slot):
                printed, _, sql, _ = self.run_cli(Model(good()), slot=slot)
                payload = self.payload(sql)
                self.assertEqual((printed["status"], payload["slot"], payload["trade_date"]), ("ok", slot, DAY))
                self.assertEqual([cat["key"] for cat in payload["cats"]], ["chips", "tech"])
        printed, _, sql, _ = self.run_cli(Model(good()), "--date", "2026-11-09", slot="morning")
        payload = self.payload(sql)
        self.assertEqual((payload["trade_date"], payload["cats"][0]["data_date"]), ("2026-11-09", DAY), "白天的班用前一交易日的籌碼")

    # ---- 公開的輸出 ----

    def test_what_is_printed_has_no_model_text(self):
        bad = with_cat("chips", say="外資現貨賣超 912 億，賣壓沉重。")
        printed, private, _, out = self.run_cli(Model(bad, good()))
        for secret in ("賣壓沉重", "首當其衝", "同步偏空", "均線反壓", "one_line", "payload"):
            self.assertNotIn(secret, out)
        self.assertEqual(sorted(printed), ["attempts", "categories", "data_dates", "dry_run", "effort", "failure", "lead", "model",
                                           "model_called", "rejected", "slot", "source", "status", "trade_date"])
        self.assertEqual((printed["model"], printed["effort"]), (mb.MODEL_DEFAULT, mb.EFFORT_DEFAULT))
        self.assertEqual(private["payload"]["cats"][0]["effect"], "賣壓主要來自外資，權值股首當其衝。")
        self.assertNotIn("sql", private)

    def test_the_model_and_effort_follow_the_settings_page(self):
        with mock.patch.dict("os.environ", {"AI_PLAIN_MODEL": "gemini-3.8-flash", "AI_PLAIN_EFFORT": "high"}):
            printed, _, sql, _ = self.run_cli(Model(good()))
        self.assertEqual((printed["model"], printed["effort"], self.stored(sql)[0]["model"]), ("gemini-3.8-flash", "high", "gemini-3.8-flash"))

    # ---- 用量 ----

    def usage(self, report_text):
        report, sql, out = self.folder / "printed.json", self.folder / "usage.sql", io.StringIO()
        if report_text is not None:
            report.write_text(report_text, encoding="utf-8")
        with mock.patch.object(gemini_usage, "read_usage", lambda agy_bin: [{"id": "argon", "remaining": 0.7}]), \
                contextlib.redirect_stdout(out):
            code = mb.main(["usage", "--report", str(report), "--run-id", "88", "--agy-bin", "agy", "--sql-output", str(sql)])
        self.assertEqual(code, 0)
        return sql.read_text(encoding="utf-8")

    def test_one_usage_row_per_shift_that_reached_the_model(self):
        _, _, _, out = self.run_cli(Model(with_cat("chips", say="外資賣超 900 億。"), good()))
        sql = self.usage("前面的訊息\n" + out)
        self.assertEqual(sql.count("INSERT INTO gemini_usage_snapshots"), 1, "重問那一次不另外記")
        for needed in ("'market-brief'", "'88'", f"'{mb.MODEL_DEFAULT}'", f"'{mb.EFFORT_DEFAULT}'", "'ok'", "argon"):
            self.assertIn(needed, sql)
        _, _, _, out = self.run_cli(Model("model_timeout"))
        self.assertIn("'failed'", self.usage(out))

    def test_no_usage_row_without_a_model_call(self):
        _, _, _, dry = self.run_cli(Model(good()), "--dry-run")
        for text in (dry, json.dumps({"status": "ok", "source": "template", "model_called": False, "dry_run": False}),
                     "不是報告", None):
            with self.subTest(text=text):
                (self.folder / "printed.json").unlink(missing_ok=True)
                self.assertEqual(self.usage(text), "")


class Migration(unittest.TestCase):
    def test_the_table_is_the_one_in_the_contract(self):
        db = sqlite3.connect(":memory:")
        db.executescript(MIGRATION.read_text(encoding="utf-8"))
        db.executescript(MIGRATION.read_text(encoding="utf-8"))   # IF NOT EXISTS：重套不會壞
        columns = [(row[1], row[2], row[3], row[5]) for row in db.execute("PRAGMA table_info(market_brief_runs)")]
        self.assertEqual(columns, [("trade_date", "TEXT", 1, 1), ("slot", "TEXT", 1, 2), ("generated_at", "TEXT", 1, 0),
                                   ("source", "TEXT", 1, 0), ("model", "TEXT", 0, 0), ("payload", "TEXT", 1, 0)])


if __name__ == "__main__":
    unittest.main()
