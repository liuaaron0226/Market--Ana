"""盤勢解讀產生器（market_brief.py）在命令列入口的契約（.scratch/market-brief/contract.md，票 02）。

假的模型回答 → 檢查寫出的 SQL（實際套進 0035 的表）、payload、印出來的報告與不公開的報告。
用法：python -m unittest test_market_brief
"""
from __future__ import annotations

import contextlib
import io
import json
import re
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
MONDAY = "2026-11-09"          # 資料包（週五 11-06）之後的第一個交易日：白天的班用前一交易日的籌碼
ORDER = ["overseas", "news", "chips", "tech"]
MIGRATIONS = ROOT / "cloud/migrations"


def all_lines(payload):
    """payload 裡所有會顯示的句子。"""
    return [payload["one_line"]] + [line for cat in payload["cats"]
                                    for line in (cat["say"], cat.get("effect", ""), *cat.get("conditions", []), *cat.get("prepare", []))]


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


class Base(unittest.TestCase):
    """命令列入口的共用工具：暫存資料夾裡放一份資料包，跑 run、把 SQL 套進 0035 的表。"""

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


class Cli(Base):
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
        self.assertEqual([cat["key"] for cat in payload["cats"]], ORDER)
        for cat in payload["cats"][2:]:
            self.assertEqual(sorted(cat), ["conditions", "data_date", "effect", "key", "prepare", "quiet", "say"])
            self.assertEqual((cat["data_date"], cat["quiet"]), (DAY, False))
        # 沒有 --inputs：國際股市、市場消息各只有一句
        self.assertEqual(payload["cats"][:2], [{"key": "overseas", "say": "國際股市資料尚未更新", "quiet": True},
                                               {"key": "news", "say": "本時段無影響大盤的新消息", "quiet": True}])
        self.assertEqual((printed["status"], printed["source"], printed["attempts"], printed["categories"]),
                         ("ok", "ai", 1, ORDER))
        self.assertEqual(printed["quiet"], {"overseas": True, "news": True, "chips": False, "tech": False})

    def test_numbers_come_from_the_facts_not_from_the_model(self):
        payload = self.payload(self.run_cli(Model(good()))[2])
        chips, tech = payload["cats"][2:]
        self.assertEqual(payload["one_line"], "籌碼面與技術面同步偏空，外資現貨連 60 天賣超。")
        self.assertEqual(chips["say"], "外資現貨與期貨部位同步偏空：外資現貨今天賣超 912 億，外資台指期淨空單比一週前增加 4,279 口。")
        self.assertEqual(tech["say"], "指數跌破短期均線：加權指數在 20 日均線之下。")
        self.assertRegex(tech["conditions"][0], r"^收盤是否站回 20 日均線（約 [\d,]+ 點）$")
        texts = all_lines(payload)
        self.assertFalse([line for line in texts if "{" in line or "}" in line], "代號都換成了事實")

    def test_the_model_only_sees_the_facts_of_this_shift(self):
        model = Model(good())
        self.run_cli(model)
        prompt = model.prompts[0]
        for needed in ("FACTS:", "foreign_today", "ma20_side", "如果…就…", "chips", "tech"):
            self.assertIn(needed, prompt)

    def test_more_than_two_conditions_are_cut_and_missing_ones_are_filled(self):
        many = with_cat("chips", conditions=["外資現貨是否轉為買超", "投信是否持續買超", "融資餘額是否止穩"], prepare=[])
        chips = self.payload(self.run_cli(Model(many))[2])["cats"][2]
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
        chips, tech = payload["cats"][2:]
        self.assertEqual(chips["say"], "籌碼面偏空：外資現貨連 60 天賣超，外資台指期淨空單 99,930 口。")
        self.assertEqual(tech["say"], "技術面偏弱：加權指數在 20 日均線之下。")
        for cat in payload["cats"][2:]:
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
                self.assertEqual([cat["key"] for cat in payload["cats"]], ORDER)
        printed, _, sql, _ = self.run_cli(Model(good()), "--date", "2026-11-09", slot="morning")
        payload = self.payload(sql)
        self.assertEqual((payload["trade_date"], payload["cats"][2]["data_date"]), ("2026-11-09", DAY), "白天的班用前一交易日的籌碼")

    # ---- 公開的輸出 ----

    def test_what_is_printed_has_no_model_text(self):
        bad = with_cat("chips", say="外資現貨賣超 912 億，賣壓沉重。")
        printed, private, _, out = self.run_cli(Model(bad, good()))
        for secret in ("賣壓沉重", "首當其衝", "同步偏空", "均線反壓", "one_line", "payload"):
            self.assertNotIn(secret, out)
        self.assertEqual(sorted(printed), ["attempts", "categories", "data_dates", "dry_run", "effort", "failure", "inputs", "lead",
                                           "model", "model_called", "quiet", "rejected", "slot", "source", "status", "trade_date"])
        self.assertEqual((printed["model"], printed["effort"]), (mb.MODEL_DEFAULT, mb.EFFORT_DEFAULT))
        self.assertEqual(private["payload"]["cats"][2]["effect"], "賣壓主要來自外資，權值股首當其衝。")
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


# ---- 票 03／04／06：國際股市、市場消息、白天三班 ----

OVERSEAS_CAT = {"key": "overseas", "say": "國際股市多數收高，費半領漲：{ov_sox}。", "effect": "半導體權值股開盤具支撐。",
                "conditions": ["美股科技股是否延續漲勢"],
                "prepare": ["如果美股科技股轉弱，就先確認台股半導體權值股是否同步回落。"]}
NEWS_CAT = {"key": "news", "say": "消息面偏多：金管會祭出限空令。", "effect": "融券放空受限，權值股賣壓減輕。",
            "conditions": ["金管會是否公布正式名單"], "prepare": ["如果限空令範圍擴大，就先確認融券部位的影響。"]}
OWNER_ONLY = "站主的鴻海庫存首當其衝"    # owner_effect 與站主自選的內容：不能進查詢結果、提示或 payload


def full(*, overseas=None, news=None, **overrides):
    """四類都寫的回答；overseas／news 給 False 就不寫那一類（那一類這一班只有一句時）。"""
    answer = good(**overrides)
    extra = [dict(OVERSEAS_CAT, **(overseas or {})) if overseas is not False else None,
             dict(NEWS_CAT, **(news or {})) if news is not False else None]
    answer["cats"] = [cat for cat in extra if cat] + answer["cats"]
    return answer


def d1_schema():
    """查詢要讀的四張表（加上不該讀的那一張），照遷移檔原文建。"""
    def table(file, name):
        return re.search(rf"CREATE TABLE {name} \(.*?\n\);", (MIGRATIONS / file).read_text(encoding="utf-8"), re.S).group(0)
    alters = [line for file in ("0032_stock_news_marks.sql", "0033_news_impact.sql")
              for line in re.findall(r"ALTER TABLE market_news_items ADD COLUMN [^;]+;", (MIGRATIONS / file).read_text(encoding="utf-8"))]
    # market_news_runs 用 0037 重建後的樣子（一天六班、含 0033 的欄位）
    runs = table("0037_market_news_push.sql", "market_news_runs_v2").replace("market_news_runs_v2", "market_news_runs")
    return "\n".join([table("0016_watch_peers.sql", "overseas_quotes"), table("0016_watch_peers.sql", "overseas_fetch_status"), runs,
                      *(table("0031_market_news.sql", name) for name in ("market_news_items", "market_news_user_items")),
                      *alters])


SYMBOLS = {"dji": "DJI.INDX", "ixic": "IXIC.INDX", "gspc": "GSPC.INDX", "sox": "SOX.INDX", "n225": "N225.INDX", "ks11": "KS11.INDX"}
MOVES = {"dji": 0.5, "ixic": 1.2, "gspc": 0.8, "sox": 3.1, "n225": -0.4, "ks11": 0.2}   # 最後一根對前一根（%）


def bars(last, previous, *, only=None, moves=MOVES):
    """每個指數兩根日線：previous 收 1000、last 照 moves 漲跌。only 給了就只有那幾個。"""
    return [(SYMBOLS[key], day, close) for key, move in moves.items() if only is None or key in only
            for day, close in ((previous, 1000.0), (last, 1000.0 * (1 + move / 100)))]


LIMIT_NEWS = {"id": "n1", "grp": "tw", "kind": "policy", "tone": "up", "impact": 3, "title": "金管會祭出限空令",
              "reason": "限制平盤下放空", "effect": "融券放空受限，權值股賣壓減輕。"}
FLOW_NEWS = {"id": "n2", "grp": "tw", "kind": "flows", "tone": "down", "impact": 2, "title": "外資賣超逾 600 億",
             "reason": "外資連續賣超", "effect": "權值股承壓。"}
SMALL_NEWS = {"id": "n3", "grp": "us", "kind": "other", "tone": "flat", "impact": 1, "title": "某公司舉辦週年活動",
              "reason": "例行活動", "effect": "影響有限。"}


def news_run(day, slot, *, status="ok", taiex=None, items=(LIMIT_NEWS, FLOW_NEWS, SMALL_NEWS),
             summary="美股收高帶動台股開高，金管會祭出限空令。"):
    indices = [{"name": "費城半導體", "change_pct": 3.1, "date": "2026-11-06"}]
    if taiex:
        indices.insert(0, {"name": "加權", "change_pct": taiex[0], "date": taiex[1]})
    return {"run_id": f"{day}-{slot}", "trade_date": day, "slot": slot, "status": status, "summary": summary if status == "ok" else None,
            "effect": "限空令減輕權值股賣壓。" if status == "ok" else None, "indices": indices, "items": list(items)}


class Categories(Base):
    def d1(self, trade_date, *, quotes=(), status=(), runs=()):
        """把列放進和 D1 同結構的表，跑 inputs_sql 那一句，存成 wrangler --json 的樣子；回傳檔案路徑。"""
        db = sqlite3.connect(":memory:")
        db.executescript(d1_schema())
        db.executemany("INSERT INTO overseas_quotes (symbol, date, close, source, fetched_at) VALUES (?, ?, ?, 'eodhd', 'x')", quotes)
        db.executemany("INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at) VALUES (?, 'eodhd', 'x', ?, ?)", status)
        for run in runs:
            db.execute("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, effect, owner_effect) "
                       "VALUES (?, ?, ?, 'x', ?, ?, ?, ?, ?)",
                       (run["run_id"], run["trade_date"], run["slot"], run["status"], run["summary"],
                        json.dumps(run["indices"], ensure_ascii=False), run["effect"], OWNER_ONLY))
            for item in run["items"]:
                db.execute("INSERT INTO market_news_items (run_id, item_id, grp, market, kind, tone, impact, title, reason, source, link, "
                           "published_at, effect, owner_effect) VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, '中央通訊社', 'https://example.test/' || ?, "
                           "'2026-11-09T00:00:00Z', ?, ?)",
                           (run["run_id"], item["id"], item["grp"], item["kind"], item["tone"], item["impact"], item["title"],
                            item["reason"], item["id"], item["effect"], OWNER_ONLY))
            db.execute("INSERT INTO market_news_user_items (email, run_id, code, link, tone, impact, title, reason) "
                       "VALUES ('owner@example.test', ?, '2317', 'https://example.test/own', 'down', 3, ?, ?)",
                       (run["run_id"], OWNER_ONLY, OWNER_ONLY))
        rows = [{"row_type": kind, "data": data} for kind, data in db.execute(mb.inputs_sql(trade_date))]
        path = self.folder / "inputs.json"
        path.write_text("wrangler 的提示文字\n" + json.dumps([{"results": rows, "success": True, "meta": {}}], ensure_ascii=False), encoding="utf-8")
        return path

    def brief(self, model, trade_date=MONDAY, slot="morning", **tables):
        """回傳（payload, 印出來的報告, 不公開的報告, 整段 stdout）。"""
        printed, private, sql, out = self.run_cli(model, "--date", trade_date, "--inputs", str(self.d1(trade_date, **tables)), slot=slot)
        return self.payload(sql), printed, private, out

    def cat(self, payload, key):
        return next(cat for cat in payload["cats"] if cat["key"] == key)

    # ---- 國際股市（票 03） ----

    def test_overseas_changes_are_computed_by_the_program_with_their_own_dates(self):
        model = Model(full(news=False))
        payload, printed, _, _ = self.brief(model, quotes=bars(DAY, "2026-11-05"))   # 週一盤前：最近一個海外交易日是上週五
        overseas = self.cat(payload, "overseas")
        self.assertEqual(overseas["markets"], [
            {"key": "dji", "name": "道瓊", "change_pct": 0.5, "date": DAY}, {"key": "ixic", "name": "那斯達克", "change_pct": 1.2, "date": DAY},
            {"key": "gspc", "name": "標普 500", "change_pct": 0.8, "date": DAY}, {"key": "sox", "name": "費城半導體", "change_pct": 3.1, "date": DAY},
            {"key": "n225", "name": "日經 225", "change_pct": -0.4, "date": DAY}, {"key": "ks11", "name": "韓國綜合", "change_pct": 0.2, "date": DAY}])
        self.assertEqual((overseas["data_date"], overseas["quiet"], payload["source"]), (DAY, False, "ai"))
        self.assertEqual(overseas["say"], "國際股市多數收高，費半領漲：費城半導體上漲 3.1%（11/06）。")
        self.assertNotIn("home", overseas, "盤前沒有台股的即時漲跌，那一班消息也沒帶")
        self.assertEqual((printed["inputs"]["markets"], printed["quiet"]["overseas"]), (list(SYMBOLS), False))
        self.assertIn("ov_sox", model.prompts[0])
        self.assertIn("標普 500 上漲 0.8%（11/06）", model.prompts[0])

    def test_the_model_cannot_write_overseas_numbers_or_reverse_the_direction(self):
        up = {key: abs(move) for key, move in MOVES.items()}
        for answer, word in ((full(news=False, overseas={"say": "費城半導體大漲 3.1%。"}), "數字"),
                             (full(news=False, overseas={"say": "美股收低：{ov_sox}。"}), "方向矛盾"),
                             (full(news=False, overseas={"say": "國際股市收高：{foreign_today}。"}), "不屬於這一類")):
            with self.subTest(word):
                _, _, private, _ = self.brief(Model(answer, answer), quotes=bars(DAY, "2026-11-05", moves=up))
                self.assertIn(word, private["rejected"][0]["problems"][0]["reason"])

    def test_an_index_whose_fetch_was_missed_is_left_out(self):
        # 週二盤前：美股週一的那一根沒抓到（最後一次成功是週一台北 18:30，美股還沒收），日韓有週一的
        quotes = bars(DAY, "2026-11-05", only=("dji", "ixic", "gspc", "sox")) + bars(MONDAY, DAY, only=("n225", "ks11"))
        status = [(symbol, 1, "2026-11-09T10:30:00Z") for symbol in SYMBOLS.values()]
        payload, printed, _, _ = self.brief(Model("model_timeout"), "2026-11-10", quotes=quotes, status=status)
        overseas = self.cat(payload, "overseas")
        self.assertEqual([row["key"] for row in overseas["markets"]], ["n225", "ks11"])
        self.assertEqual((overseas["data_date"], overseas["quiet"], payload["source"]), (MONDAY, False, "template"))
        self.assertEqual(overseas["say"], "國際股市漲跌互見：韓國綜合上漲 0.2%（11/09），日經 225 下跌 0.4%（11/09）。")

    def test_a_market_holiday_keeps_the_last_session_when_the_fetch_was_made_after_it(self):
        # 週五盤前，美股週四感恩節休市：最後一根是週三，但週五台北 07:00 抓過、成功 → 是休市不是沒抓到
        quotes = bars("2026-11-25", "2026-11-24", only=("dji", "ixic", "gspc", "sox")) + bars("2026-11-26", "2026-11-25", only=("n225", "ks11"))
        fetched = [(symbol, 1, "2026-11-26T23:00:00Z") for symbol in SYMBOLS.values()]
        payload, _, _, _ = self.brief(Model("model_timeout"), "2026-11-27", quotes=quotes, status=fetched)
        self.assertEqual({row["key"]: row["date"] for row in self.cat(payload, "overseas")["markets"]},
                         {"dji": "2026-11-25", "ixic": "2026-11-25", "gspc": "2026-11-25", "sox": "2026-11-25",
                          "n225": "2026-11-26", "ks11": "2026-11-26"})
        failed = [(symbol, 0, "2026-11-25T23:00:00Z") for symbol in SYMBOLS.values()]
        payload, _, _, _ = self.brief(Model("model_timeout"), "2026-11-27", quotes=quotes, status=failed)
        self.assertEqual([row["key"] for row in self.cat(payload, "overseas")["markets"]], ["n225", "ks11"], "最後一次沒抓成：不當成休市")

    def test_when_every_index_is_stale_the_category_is_one_sentence_and_the_model_is_not_asked_about_it(self):
        model = Model(good())
        for tables in ({"quotes": bars("2026-11-03", "2026-11-02")},                    # 都停在上週二
                       {"quotes": [row for row in bars(DAY, "2026-11-05") if row[1] == DAY]},   # 各只有一根，算不出漲跌
                       {}):                                                             # 表是空的
            with self.subTest(tables=tables):
                payload, printed, _, _ = self.brief(model, **tables)
                self.assertEqual(self.cat(payload, "overseas"), {"key": "overseas", "say": "國際股市資料尚未更新", "quiet": True})
                self.assertEqual((payload["source"], printed["quiet"]["overseas"], printed["inputs"]["markets"]), ("ai", True, []))
                self.assertNotIn("overseas（國際股市）", model.prompts[-1])
                self.assertNotIn("ov_", model.prompts[-1])

    def test_the_evening_shift_adds_the_home_index_from_the_pack(self):
        payload, _, _, _ = self.brief(Model(full(news=False, overseas={"say": "國際股市多數收高，台股逆勢走弱：{ov_sox}，{ov_home}。"})),
                                      DAY, "evening", quotes=bars("2026-11-05", "2026-11-04"))
        overseas = self.cat(payload, "overseas")
        self.assertEqual(overseas["home"], {"name": "加權指數", "change_pct": -4.5, "date": DAY})
        self.assertEqual(overseas["say"], "國際股市多數收高，台股逆勢走弱：費城半導體上漲 3.1%（11/05），加權指數 11/06 下跌 4.5%。")
        self.assertNotIn("intraday", self.cat(payload, "tech"))

    def test_the_six_indices_are_the_ones_the_site_fetches(self):
        import overseas_peers
        self.assertEqual({symbol for _, symbol, _, _ in mb.OVERSEAS}, set(overseas_peers.HOME_INDICES))
        self.assertEqual({key: symbol for key, symbol, _, _ in mb.OVERSEAS}, SYMBOLS)

    # ---- 市場消息（票 04） ----

    def test_news_comes_from_the_latest_run_of_the_day_with_shared_fields_only(self):
        model = Model(full(overseas=False))
        path = self.d1(MONDAY, runs=[news_run(MONDAY, "morning"), news_run("2026-11-06", "close")])
        self.assertNotIn(OWNER_ONLY, path.read_text(encoding="utf-8"), "查詢不讀 owner_effect 與任何人的自選")
        printed, _, sql, out = self.run_cli(model, "--date", MONDAY, "--inputs", str(path), slot="morning")
        payload = self.payload(sql)
        news = self.cat(payload, "news")
        self.assertEqual(news, {**NEWS_CAT, "data_date": MONDAY, "quiet": False})
        self.assertEqual((payload["source"], printed["inputs"]["news_run"], printed["inputs"]["news_items"]), ("ai", "morning", 2))
        prompt = model.prompts[0]
        for needed in ("NEWS:", "金管會祭出限空令", "外資賣超逾 600 億", "美股收高帶動台股開高", "news（市場消息）"):
            self.assertIn(needed, prompt)
        for secret in (OWNER_ONLY, "owner_effect", "owner@example.test", "某公司舉辦週年活動", "example.test"):
            self.assertNotIn(secret, prompt)
        self.assertNotIn("金管會", out, "印出來的報告沒有模型寫的句子")

    def test_owner_only_fields_never_reach_the_prompt_even_if_the_input_file_has_them(self):
        path = self.d1(MONDAY, runs=[news_run(MONDAY, "morning")])
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw[raw.index("["):])
        for row in payload[0]["results"]:
            data = json.loads(row["data"])
            data.update({"owner_effect": OWNER_ONLY, "my_track": [{"code": "站主的代號"}], "email": "owner@example.test"})
            row["data"] = json.dumps(data, ensure_ascii=False)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        model = Model(full(overseas=False))
        printed, private, _, out = self.run_cli(model, "--date", MONDAY, "--inputs", str(path), slot="morning")
        self.assertEqual(printed["source"], "ai")
        for text in (model.prompts[0], out, json.dumps(private, ensure_ascii=False)):
            for secret in (OWNER_ONLY, "站主的代號", "my_track", "owner@example.test"):
                self.assertNotIn(secret, text)

    def test_news_numbers_and_names_must_come_from_the_supplied_news(self):
        runs = [news_run(MONDAY, "morning")]
        for say, word in (("外資賣超逾 900 億，消息面偏空。", "數字"), ("輝達 NVIDIA 財報亮眼，消息面偏多。", "NVIDIA"),
                          ("你的持股受限空令影響。", "讀者"), ("消息面偏多，建議買進權值股。", "買賣指令"),
                          ("消息面偏多：{foreign_today}。", "不用代號")):
            with self.subTest(say):
                answer = full(overseas=False, news={"say": say})
                _, printed, private, _ = self.brief(Model(answer, answer), runs=runs)
                self.assertEqual(printed["source"], "template")
                self.assertIn(word, private["rejected"][0]["problems"][0]["reason"])
        payload, printed, _, _ = self.brief(Model(full(overseas=False, news={"say": "外資賣超逾 600 億，金管會祭出限空令。"})), runs=runs)
        self.assertEqual((printed["source"], self.cat(payload, "news")["say"]), ("ai", "外資賣超逾 600 億，金管會祭出限空令。"))

    def test_without_impactful_news_the_category_is_one_sentence(self):
        quiet = {"key": "news", "say": "本時段無影響大盤的新消息", "quiet": True}
        for runs in ([], [news_run(MONDAY, "morning", items=[SMALL_NEWS])], [news_run(MONDAY, "morning", status="unclassified")],
                     [news_run("2026-11-06", "close")],        # 別天的
                     [news_run(MONDAY, "close")]):             # 這一班之後的
            with self.subTest(runs=[run["run_id"] + run["status"] for run in runs]):
                model = Model(good())
                payload, printed, _, _ = self.brief(model, runs=runs)
                self.assertEqual(self.cat(payload, "news"), quiet)
                self.assertEqual((payload["source"], printed["quiet"]["news"]), ("ai", True))
                self.assertNotIn("NEWS:", model.prompts[0])

    def test_each_shift_reads_the_latest_run_not_after_it(self):
        runs = [news_run(MONDAY, "morning", summary="盤前：美股收高。"), news_run(MONDAY, "midday", summary="盤中：台股走高。"),
                news_run(MONDAY, "close", summary="收盤：台股收高。")]
        for slot, expected in (("morning", "盤前"), ("midday", "盤中"), ("close", "收盤"), ("evening", "收盤")):
            with self.subTest(slot):
                model = Model("model_timeout")
                payload, printed, _, _ = self.brief(model, MONDAY, slot, runs=runs)
                self.assertIn(f'"summary":"{expected}', model.prompts[0])
                self.assertEqual(printed["inputs"]["news_run"], "close" if slot == "evening" else slot)
                self.assertTrue(self.cat(payload, "news")["say"].startswith(expected), "套句用那一班自己的總結（所有人看得到的那一句）")

    def test_the_three_added_news_slots_do_not_change_what_each_shift_reads(self):
        # 首頁消息加開 06:30、18:30、23:00 三班（本人 2026-11-24）：盤勢解讀仍是四班；18:00 那一班讀到收盤後為止，
        # 之後才有的盤後、美股開盤兩班不讀（20:30 重跑時也一樣）；清晨那一班排在盤前之前。
        self.assertEqual(sorted(mb.NEWS_SLOT_ORDER, key=mb.NEWS_SLOT_ORDER.get), ["us_close", "morning", "midday", "close", "evening", "us_open"])
        self.assertEqual(mb.SLOTS, ("morning", "midday", "close", "evening"), "盤勢解讀自己的班次不變")
        runs = [news_run(MONDAY, "us_close", summary="清晨：美股收高。"), news_run(MONDAY, "morning", summary="盤前：美股收高。"),
                news_run(MONDAY, "midday", summary="盤中：台股走高。"), news_run(MONDAY, "close", summary="收盤：台股收高。"),
                news_run(MONDAY, "evening", summary="盤後：外資期貨翻多。"), news_run(MONDAY, "us_open", summary="美股開盤：費半開高。")]
        for slot, expected, run_slot in (("morning", "盤前", "morning"), ("midday", "盤中", "midday"), ("close", "收盤", "close"),
                                         ("evening", "收盤", "close")):
            with self.subTest(slot):
                model = Model("model_timeout")
                _, printed, _, _ = self.brief(model, MONDAY, slot, runs=runs)
                self.assertIn(f'"summary":"{expected}', model.prompts[0])
                self.assertEqual(printed["inputs"]["news_run"], run_slot)
                for later in ("盤後：", "美股開盤："):
                    self.assertNotIn(later, model.prompts[0])
        model = Model("model_timeout")
        _, printed, _, _ = self.brief(model, MONDAY, "morning", runs=[runs[0], runs[4]])
        self.assertEqual(printed["inputs"]["news_run"], "us_close", "盤前那一班還沒寫成時，讀當天清晨那一班")
        sql = mb.inputs_sql(MONDAY)
        self.assertIn("slot IN ('us_close', 'morning', 'midday', 'close')", sql)
        for later in ("'evening'", "'us_open'"):
            self.assertNotIn(later, sql)

    # ---- 白天三班（票 06） ----

    def test_daytime_sentences_cannot_call_the_previous_session_today(self):
        for answer in (with_cat("chips", say="今天外資現貨續賣：{foreign_today}。"), with_cat("chips", effect="今日賣壓主要來自外資。"),
                       with_cat("tech", conditions=["本日收盤是否站回 {ma20_line}"]),
                       good(one_line="今天籌碼面偏空，{foreign_streak}。")):
            with self.subTest(answer=answer):
                _, printed, private, _ = self.brief(Model(answer, answer))
                self.assertEqual(printed["source"], "template")
                self.assertIn("不能寫「今天」", private["rejected"][0]["problems"][0]["reason"])
        dated = with_cat("chips", say="{price_date} 外資現貨續賣：{foreign_today}。")
        dated["one_line"] = "今天盤前留意國際股市，籌碼面仍偏空。"      # 沒引用籌碼數字的結論可以講今天
        payload, printed, _, _ = self.brief(Model(dated))
        self.assertEqual((printed["source"], payload["trade_date"], payload["slot"]), ("ai", MONDAY, "morning"))
        chips = self.cat(payload, "chips")
        self.assertEqual((chips["say"], chips["data_date"]), ("11/06 外資現貨續賣：外資現貨 11/06 賣超 912 億。", DAY))
        self.assertEqual(self.cat(payload, "tech")["data_date"], DAY)

    def test_the_prompt_tells_the_model_to_write_the_date_only_when_the_pack_is_older(self):
        model = Model(good())
        self.brief(model)
        for needed in ("不可寫「今天」「今日」「本日」", "{price_date}", "11/06", "外資現貨 11/06 賣超 912 億"):
            self.assertIn(needed, model.prompts[0])
        self.assertNotIn("外資現貨今天", model.prompts[0])
        evening = Model(good())
        self.run_cli(evening)
        self.assertNotIn("不可寫「今天」", evening.prompts[0])
        self.assertIn("外資現貨今天賣超 912 億", evening.prompts[0])
        payload = self.payload(self.run_cli(Model(with_cat("chips", say="今天外資現貨續賣：{foreign_today}。")))[2])
        self.assertEqual(payload["source"], "ai", "18:00 那一班的籌碼就是今天的")

    def test_the_midday_shift_carries_the_live_index_from_the_news_run(self):
        runs = [news_run(MONDAY, "midday", taiex=(2.04, MONDAY))]
        answer = full(overseas={"say": "國際股市多數收高，台股同步走高：{ov_sox}，{ov_home}。"})
        answer = {**answer, "cats": [dict(cat, say="指數盤中反彈，仍在短期均線之下：{intraday}，{ma20_side}。") if cat["key"] == "tech" else cat
                                     for cat in answer["cats"]]}
        payload, printed, _, _ = self.brief(Model(answer), MONDAY, "midday", quotes=bars(DAY, "2026-11-05"), runs=runs)
        self.assertEqual(printed["source"], "ai")
        tech, overseas = self.cat(payload, "tech"), self.cat(payload, "overseas")
        self.assertEqual(tech["intraday"], {"change_pct": 2.0, "date": MONDAY})
        self.assertEqual(tech["say"], "指數盤中反彈，仍在短期均線之下：加權指數 11/09 盤中上漲 2.0%，加權指數在 20 日均線之下。")
        self.assertEqual(overseas["home"], {"name": "加權指數", "change_pct": 2.0, "date": MONDAY})
        borrowed = {**answer, "cats": [dict(cat, say="籌碼面偏空：{intraday}。") if cat["key"] == "chips" else cat for cat in answer["cats"]]}
        _, _, private, _ = self.brief(Model(borrowed, borrowed), MONDAY, "midday", quotes=bars(DAY, "2026-11-05"), runs=runs)
        self.assertIn("不屬於這一類", private["rejected"][0]["problems"][0]["reason"])

    def test_before_the_open_the_home_index_is_the_previous_close_and_tech_has_no_intraday(self):
        for taiex, home in (((-4.51, DAY), {"name": "加權指數", "change_pct": -4.5, "date": DAY}), ((0.0, MONDAY), None),
                            ((1.0, "2026-11-04"), None), ((1.0, "不是日期"), None)):
            with self.subTest(taiex=taiex):
                payload, _, _, _ = self.brief(Model("model_timeout"), quotes=bars(DAY, "2026-11-05"),
                                              runs=[news_run(MONDAY, "morning", taiex=taiex)])
                self.assertEqual(self.cat(payload, "overseas").get("home"), home)
                self.assertNotIn("intraday", self.cat(payload, "tech"))

    def test_the_template_is_valid_for_every_shift(self):
        for slot, day in (("morning", MONDAY), ("midday", MONDAY), ("close", MONDAY), ("evening", DAY)):
            for tables in ({}, {"quotes": bars(DAY if slot != "evening" else "2026-11-05", "2026-11-04"),
                                "runs": [news_run(day, "morning", taiex=(1.5, day))]}):
                with self.subTest(slot=slot, inputs=bool(tables)):
                    payload, printed, _, _ = self.brief(Model("model_timeout"), day, slot, **tables)
                    self.assertEqual((printed["status"], payload["source"], payload["slot"], payload["trade_date"]), ("ok", "template", slot, day))
                    self.assertEqual([cat["key"] for cat in payload["cats"]], ORDER)
                    active = [cat for cat in payload["cats"] if not cat["quiet"]]
                    self.assertEqual(len(active), 4 if tables else 2)
                    self.assertIn(payload["lead"], [cat["key"] for cat in active])
                    for cat in payload["cats"]:
                        if cat["quiet"]:
                            self.assertEqual(sorted(cat), ["key", "quiet", "say"])
                            continue
                        # 票 02 的前端讀的欄位都在
                        self.assertLessEqual({"key", "say", "effect", "conditions", "prepare", "data_date", "quiet"}, set(cat))
                        self.assertTrue(cat["say"] and cat["effect"])
                        self.assertTrue(1 <= len(cat["conditions"]) <= 2 and 1 <= len(cat["prepare"]) <= 2)
                        self.assertTrue(all("如果" in line and "就" in line for line in cat["prepare"]))
                    lines = all_lines(payload)
                    self.assertFalse([line for line in lines if "{" in line or "}" in line])
                    self.assertFalse([line for line in lines if re.search("買進|賣出|加碼|減碼|停損", line)])
                    if slot != "evening":
                        dated = [line for cat in payload["cats"] if cat["key"] in ("chips", "tech") for line in all_lines({"one_line": "", "cats": [cat]})]
                        self.assertFalse([line for line in dated if re.search("今天|今日|本日", line)])
                        self.assertIn("（11/06）", self.cat(payload, "chips")["say"])

    def test_an_unreadable_input_file_only_silences_the_two_categories(self):
        path = self.folder / "inputs.json"
        for text in ("wrangler 沒有回東西", '[{"results": [{"row_type": "quote", "data": "不是 JSON"}, 7]}]', None):
            with self.subTest(text=text):
                path.unlink(missing_ok=True)
                if text is not None:
                    path.write_text(text, encoding="utf-8")
                printed, _, sql, _ = self.run_cli(Model(good()), "--date", MONDAY, "--inputs", str(path), slot="close")
                payload = self.payload(sql)
                self.assertEqual((printed["status"], payload["source"]), ("ok", "ai"))
                self.assertEqual([cat["quiet"] for cat in payload["cats"]], [True, True, False, False])

    # ---- 查詢 ----

    def inputs_sql(self, *args, now=NOW):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = mb.main(["inputs-sql", *args], now=now)
        self.assertEqual(code, 0)
        return out.getvalue().strip()

    def test_the_query_is_one_statement_over_shared_tables_only(self):
        sql = self.inputs_sql("--slot", "morning", "--date", MONDAY)
        self.assertEqual(sql, mb.inputs_sql(MONDAY).strip())
        self.assertEqual((sql.count(";"), sql.count("\n")), (1, 0), "一句、一行：wrangler --command 直接吃")
        self.assertNotRegex(sql, r"owner_effect|market_news_user_items|watchlist|email|INSERT|UPDATE|DELETE")
        self.assertIn("'2026-11-09'", sql)
        for symbol in SYMBOLS.values():
            self.assertIn(f"'{symbol}'", sql)

    def test_the_query_uses_the_same_trade_date_as_the_run(self):
        pack = str(self.folder / "analysis-data.js")
        self.assertIn(f"trade_date = '{DAY}'", self.inputs_sql("--slot", "evening", "--analysis", pack))
        self.assertIn("trade_date = '2026-11-07'", self.inputs_sql("--slot", "midday", now=datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)))
        self.assertEqual(self.inputs_sql("--slot", "evening", "--analysis", str(self.folder / "no-such.js")), "", "讀不到資料包：不印、照樣結束 0")
        self.assertEqual(self.inputs_sql("--slot", "close", "--date", "2026-13-40"), "")


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
