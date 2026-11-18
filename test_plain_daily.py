"""首頁「籌碼與資金」白話分析每日重寫（本人 11-17 定案）的契約。

用法：python -m unittest test_plain_daily
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path

import plain_daily as pd


def trading_days(end: str, count: int) -> list[str]:
    day, out = date.fromisoformat(end), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return out[::-1]


def market(*, foreign_today=-911.88, foreign_prev=-1024.61, trust_today=266.58, futures_now=-99930,
           futures_week=-95651, margin_now=5340.53, margin_prev=5764.09, short_now=242766, short_prev=277051,
           close=39701.64, prev_close=41574.72, high=40435.83, low=37837.05, amount_now=1312516977574,
           closes_before=None):
    """合成一份和正式資料包同欄位的大盤＋籌碼資料（資料日 2026-11-06）。"""
    days = trading_days("2026-11-06", 260)
    base = closes_before or [42000 + (i % 7) * 30 for i in range(len(days) - 2)]
    closes = [*base, prev_close, close]
    daily = [{"date": d, "open": c, "high": c + 50, "low": c - 50, "close": c, "amount": 1_000_000_000_000}
             for d, c in zip(days, closes)]
    daily[-1].update({"high": high, "low": low, "amount": amount_now})
    recent = days[-60:]
    foreign = [{"date": d, "net_billion": -500.0, "trust_billion": 100.0} for d in recent]
    foreign[-2].update({"net_billion": foreign_prev})
    foreign[-1].update({"net_billion": foreign_today, "trust_billion": trust_today})
    futures = [{"date": d, "net_contracts": -80000 - i * 100} for i, d in enumerate(recent)]
    futures[-6]["net_contracts"] = futures_week
    futures[-1]["net_contracts"] = futures_now
    margin = [{"date": d, "margin_balance_billion": 6000.0 + i, "margin_units": 1, "short_units": 215000, "index_close": 1}
              for i, d in enumerate(recent)]
    margin[-2].update({"margin_balance_billion": margin_prev, "short_units": short_prev})
    margin[-1].update({"margin_balance_billion": margin_now, "short_units": short_now})
    analysis = {"meta": {"cutoff_date": "2026-11-06"}, "daily": daily}
    chips = {"analysis_cutoff": "2026-11-06", "margin_latest_date": recent[-1],
             "foreign": foreign, "futures": futures, "margin": margin}
    return analysis, chips


def facts_text(bundle) -> dict[str, str]:
    return {key: fact["text"] for key, fact in bundle["facts"].items()}


def facts_short(bundle) -> dict[str, str]:
    return {key: fact["short"] for key, fact in bundle["facts"].items()}


def numbers_in(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?", text)


def all_text(plain) -> list[str]:
    texts = [plain["one_line"], *plain["worry"], *plain["relief"]]
    for point in plain["points"]:
        texts += [point["title"], *point["lines"]]
    return texts


def good_ai(**overrides):
    """一份照規則寫的模型輸出：自由文字沒有數字，數字一律用 {代號}。"""
    output = {
        "data_date": "2026-11-06",
        "one_line": "短線還是偏空；{foreign_streak}，期貨空單也在加。",
        "points": [
            {"key": "foreign", "title": "現貨和期貨都在偏空", "lines": ["{foreign_streak}，{foreign_today}。", "{futures_net}，{futures_week}。"]},
            {"key": "trust", "title": "有在買但抵不過", "lines": ["{trust_5d}，{trust_vs_foreign}。"]},
            {"key": "margin", "title": "借錢買股的錢大減", "lines": ["{margin_balance}，{margin_change}。"]},
            {"key": "short", "title": "大跌當天空單回補", "lines": ["{short_units}，{short_change}。"]},
            {"key": "position", "title": "短線很弱", "lines": ["今天{close}，{index_change}。"]},
            {"key": "volume", "title": "換手很劇烈", "lines": ["{amount}，{amount_ratio}，{close_in_range}。"]},
        ],
        "worry": ["外資期貨空單還在增加。", "收盤跌破{day_low}，長下影就失效。"],
        "relief": ["外資現貨轉為買超，或期貨空單開始減少。"],
    }
    output.update(overrides)
    return output


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.bundle = pd.build_facts(*market())
        self.short = facts_short(self.bundle)
        self.text = facts_text(self.bundle)

    def test_data_dates_follow_each_source(self):
        self.assertEqual(self.bundle["data_dates"], {"price_and_foreign": "2026-11-06", "foreign": "2026-11-06",
                                                     "futures": "2026-11-06", "margin": "2026-11-06"})

    def test_foreign_and_futures_facts_carry_their_direction(self):
        self.assertEqual(self.short["foreign_today"], "現貨今天賣超 912 億")
        self.assertEqual(self.short["foreign_streak"], "現貨連 60 天賣超")
        self.assertTrue(self.short["foreign_20d"].startswith("現貨近 20 個交易日累計賣超約 1"))
        self.assertTrue(self.short["foreign_20d"].endswith("兆"))
        self.assertEqual(self.short["futures_net"], "台指期淨空單 99,930 口")
        self.assertEqual(self.short["futures_week"], "淨空單比一週前增加 4,279 口")

    def test_every_number_carries_its_own_subject_outside_the_points(self):
        # 結論、擔心、安心代入的是完整句：數字前面一定是它自己的主角，模型沒辦法把外資的數字寫成投信的
        expected = {"foreign_today": "外資", "futures_net": "外資", "futures_week": "外資台指期", "trust_today": "投信",
                    "margin_balance": "融資", "margin_change": "融資", "short_units": "融券", "short_change": "融券",
                    "close": "加權指數", "index_change": "加權指數", "amount": "大盤", "amount_ratio": "成交金額"}
        for key, subject in expected.items():
            with self.subTest(key):
                self.assertTrue(self.text[key].startswith(subject), self.text[key])

    def test_trust_margin_short_and_volume_facts(self):
        self.assertEqual(self.short["trust_today"], "今天買超 267 億")
        self.assertEqual(self.short["margin_balance"], "餘額 5,341 億")
        self.assertEqual(self.short["margin_change"], "比前一天減少 424 億")
        self.assertIn("單日減少金額是近 60 個交易日最多", self.short["margin_rank"])
        self.assertEqual(self.short["short_units"], "餘額約 24.3 萬張")
        self.assertEqual(self.short["short_change"], "比前一天減少約 3.4 萬張")
        self.assertEqual(self.short["amount"], "成交 13,125 億")

    def test_position_facts(self):
        self.assertEqual(self.short["close"], "收 39,702 點")
        self.assertEqual(self.short["index_change"], "下跌 1,873 點（4.5%）")
        self.assertEqual(self.short["day_low"], "盤中最低 37,837 點")
        self.assertEqual(self.short["close_in_range"], "收在當天高檔")
        self.assertEqual(self.short["ma20_side"], "在 20 日均線之下")
        self.assertIn("ma240_side", self.short)

    def test_year_line_is_left_out_when_history_is_too_short(self):
        analysis, chips = market()
        analysis["daily"] = analysis["daily"][-100:]
        self.assertNotIn("ma240_side", facts_text(pd.build_facts(analysis, chips)))

    def test_margin_published_a_day_late_keeps_its_own_date(self):
        analysis, chips = market()
        chips["margin"] = chips["margin"][:-1]
        bundle = pd.build_facts(analysis, chips)
        self.assertEqual(bundle["data_dates"]["margin"], "2026-11-05")
        self.assertEqual(bundle["facts"]["margin_date"]["text"], "11/05")
        self.assertIn("（資料到 11/05）", json.dumps(pd.template_plain(bundle), ensure_ascii=False))

    def test_lagging_foreign_or_futures_keep_the_index_date_and_say_their_own(self):
        # 審查抓到：舊版取三者最早的日子當資料日，指數的數字被標成前一天，畫面還會整份藏起來
        analysis, chips = market()
        chips["foreign"] = chips["foreign"][:-1]
        chips["futures"] = chips["futures"][:-1]
        bundle = pd.build_facts(analysis, chips)
        self.assertEqual(bundle["data_dates"]["price_and_foreign"], "2026-11-06")
        self.assertEqual((bundle["data_dates"]["foreign"], bundle["data_dates"]["futures"]), ("2026-11-05", "2026-11-05"))
        self.assertTrue(bundle["facts"]["foreign_today"]["short"].startswith("現貨 11/05 "))
        self.assertTrue(bundle["facts"]["trust_today"]["short"].startswith("11/05 "))
        self.assertIn("（11/05）", bundle["facts"]["futures_net"]["short"])
        self.assertIsNotNone(pd.template_plain(bundle))

    def test_turning_needs_the_previous_day_to_go_the_other_way(self):
        flat_before = pd.build_facts(*market(foreign_today=120.0, foreign_prev=0.0))
        self.assertNotIn("foreign_streak", flat_before["facts"])
        turned = pd.build_facts(*market(foreign_today=120.0, foreign_prev=-50.0))
        self.assertEqual(turned["facts"]["foreign_streak"]["short"], "現貨由賣轉買")

    def test_only_aggregate_public_market_numbers_go_into_the_prompt(self):
        prompt = pd.build_prompt(self.bundle)
        for forbidden in ("watchlist", "holdings", "portfolio", "@", "Fugle", "fugle", "code"):
            self.assertNotIn(forbidden, prompt)
        self.assertIn("{代號}", prompt)
        self.assertIn("現貨今天賣超 912 億", prompt)

    def test_prompt_topics_come_from_the_same_table_the_checker_uses(self):
        prompt = pd.build_prompt(self.bundle)
        for key, topics in pd.SECTION_TOPICS.items():
            self.assertIn(f"{key}（{pd.SUBJECTS[key]}）只能用 topic 為 {'、'.join(topics)} 的代號", prompt)


class TemplateTest(unittest.TestCase):
    SCENARIOS = {
        "賣壓": {},
        "外資轉買、空單減少、融資增加": dict(foreign_today=320.5, foreign_prev=-50.0, futures_now=-60000, futures_week=-70000,
                                         margin_now=5800.0, margin_prev=5700.0, short_now=300000, short_prev=280000,
                                         close=43000.0, prev_close=42500.0, high=43100.0, low=42400.0),
        "期貨淨多單、量縮": dict(futures_now=12000, futures_week=8000, amount_now=500_000_000_000, foreign_today=0.0),
        "投信持平": dict(trust_today=-400.0),
    }

    def test_every_scenario_produces_the_same_shape_the_page_reads(self):
        for name, kwargs in self.SCENARIOS.items():
            with self.subTest(name):
                plain = pd.template_plain(pd.build_facts(*market(**kwargs)))
                self.assertEqual([p["key"] for p in plain["points"]], list(pd.POINT_KEYS))
                for point in plain["points"]:
                    subject, _, judgement = point["title"].partition("：")
                    self.assertEqual(subject, pd.SUBJECTS[point["key"]])
                    self.assertTrue(judgement)
                    self.assertTrue(point["lines"])
                self.assertTrue(plain["one_line"] and plain["worry"] and plain["relief"])
                self.assertEqual(plain["source"], "template")

    def test_every_number_in_the_template_comes_from_a_fact(self):
        for name, kwargs in self.SCENARIOS.items():
            with self.subTest(name):
                bundle = pd.build_facts(*market(**kwargs))
                allowed = {n for fact in bundle["facts"].values() for n in numbers_in(fact["text"])}
                for text in all_text(pd.template_plain(bundle)):
                    self.assertLessEqual(set(numbers_in(text)), allowed, text)

    def test_template_passes_the_same_check_as_the_model(self):
        for name, kwargs in self.SCENARIOS.items():
            with self.subTest(name):
                bundle = pd.build_facts(*market(**kwargs))
                plain, problems = pd.validate(pd.template_raw(bundle), bundle)
                self.assertEqual(problems, [])
                self.assertIsNotNone(plain)

    def test_flat_trust_week_is_not_called_selling(self):
        bundle = pd.build_facts(*market(trust_today=-400.0))   # 近 5 日 100×4−400＝0
        self.assertEqual(bundle["signals"]["trust_5d"], 0)
        self.assertEqual(pd.template_plain(bundle)["points"][1]["title"], "投信：幾乎沒動作")

    def test_foreign_worry_says_foreign_so_the_chip_panel_can_find_it(self):
        plain = pd.template_plain(pd.build_facts(*market()))
        self.assertTrue(any("外資" in item for item in plain["worry"]))


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.bundle = pd.build_facts(*market())

    def check(self, output):
        return pd.validate(output, self.bundle)

    def test_a_good_answer_is_rendered_with_checked_numbers(self):
        plain, problems = self.check(good_ai())
        self.assertEqual(problems, [])
        foreign = plain["points"][0]
        self.assertEqual(foreign["title"], "外資：現貨和期貨都在偏空")
        self.assertEqual(foreign["lines"][0], "現貨連 60 天賣超，現貨今天賣超 912 億。")
        self.assertEqual(plain["one_line"], "短線還是偏空；外資現貨連 60 天賣超，期貨空單也在加。")
        self.assertEqual(plain["worry"][1], "收盤跌破今天盤中最低 37,837 點，長下影就失效。")
        self.assertFalse(re.search(r"\{[a-z0-9_]+\}", json.dumps(plain, ensure_ascii=False)))

    def test_points_come_back_in_the_page_order(self):
        output = good_ai()
        output["points"] = output["points"][::-1]
        plain, problems = self.check(output)
        self.assertEqual(problems, [])
        self.assertEqual([p["key"] for p in plain["points"]], list(pd.POINT_KEYS))

    def rejected(self, output):
        plain, problems = self.check(output)
        self.assertIsNone(plain)
        self.assertTrue(problems)
        return " ".join(problems)

    def test_numbers_written_by_the_model_are_rejected(self):
        for text in ("外資今天賣超九百億。", "外資今天賣超 912 億。", "外資連７天賣超。", "台積電 2330 被賣最多。", "十一月以來一路賣。"):
            with self.subTest(text):
                self.assertIn("數字", self.rejected(good_ai(one_line=text)))

    def test_common_words_with_yi_are_allowed(self):
        plain, problems = self.check(good_ai(one_line="外資一直在賣，盤中一度跌深，進一步的方向還要看期貨。"))
        self.assertEqual(problems, [])

    def test_unknown_or_off_topic_placeholders_are_rejected(self):
        self.assertIn("代號", self.rejected(good_ai(one_line="外資{foreign_secret}。")))
        output = good_ai()
        output["points"][1]["lines"] = ["投信{foreign_today}。"]   # 投信那一點拿外資的數字
        self.assertIn("不屬於", self.rejected(output))

    def test_a_number_used_outside_its_point_still_names_its_own_subject(self):
        # 審查抓到：結論可以用任何事實。代入的是完整句，「投信{foreign_today}」讀出來仍是外資的數字，不會變成投信的
        plain, _ = self.check(good_ai(one_line="投信{foreign_today}。"))
        self.assertEqual(plain["one_line"], "投信外資現貨今天賣超 912 億。")

    def test_shape_problems_are_rejected(self):
        output = good_ai()
        output["points"] = output["points"][:5]
        self.assertIn("六", self.rejected(output))
        output = good_ai()
        output["points"][1]["key"] = "foreign"
        self.assertIn("六", self.rejected(output))
        self.assertIn("資料日", self.rejected(good_ai(data_date="2026-11-05")))
        self.assertIn("欄位", self.rejected({**good_ai(), "extra": 1}))
        output = good_ai()
        output["points"][0]["title"] = "外資：偏空"
        self.assertIn("冒號", self.rejected(output))

    def test_banned_words_and_contradictions_are_rejected(self):
        self.assertIn("禁止", self.rejected(good_ai(one_line="模型認為短線偏空。")))
        for text in ("外資轉買，短線轉強。", "外資買進，短線轉強。", "外資大買權值股。"):
            with self.subTest(text):
                self.assertIn("矛盾", self.rejected(good_ai(one_line=text)))
        output = good_ai()
        output["points"][3]["lines"] = ["融券增加，放空的人變多。"]
        self.assertIn("矛盾", self.rejected(output))

    def test_conditions_in_worry_and_relief_may_name_the_opposite_direction(self):
        plain, problems = self.check(good_ai(relief=["外資轉買，或期貨空單減少。"]))
        self.assertEqual(problems, [])


class ProduceTest(unittest.TestCase):
    def setUp(self):
        self.analysis, self.chips = market()
        self.calls = []

    def asker(self, *results):
        def ask(prompt, schema):
            self.calls.append((prompt, schema))
            result = results[min(len(self.calls) - 1, len(results) - 1)]
            if isinstance(result, Exception):
                raise result
            return result
        return ask

    def test_off_does_not_call_the_model(self):
        plain, audit = pd.produce(self.analysis, self.chips, mode="off", ask_model=self.asker(("ok", good_ai(), "")))
        self.assertIsNone(plain)
        self.assertEqual(self.calls, [])
        self.assertEqual(audit["mode"], "off")

    def test_unknown_mode_is_off(self):
        self.assertEqual(pd.normalize_mode("ON "), "on")
        for value in (None, "", "true", "yes", "1"):
            self.assertEqual(pd.normalize_mode(value), "off")

    def test_on_with_a_good_answer_publishes_the_ai_text(self):
        plain, audit = pd.produce(self.analysis, self.chips, mode="on", ask_model=self.asker(("ok", good_ai(), "")))
        self.assertEqual(plain["source"], "ai")
        self.assertEqual(plain["model"], pd.MODEL_DEFAULT)
        self.assertEqual(plain["data_dates"]["price_and_foreign"], "2026-11-06")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], pd.OUTPUT_SCHEMA)
        self.assertEqual((audit["source"], audit["model"], audit["effort"]), ("ai", "gemini-3.8-flash", "medium"))

    def test_on_with_a_failed_or_bad_answer_falls_back_to_the_template(self):
        for result in (("model_failed", None, "結束碼 1"), ("ok", good_ai(one_line="外資賣超九百億。"), ""), RuntimeError("boom")):
            with self.subTest(result=str(result)[:40]):
                self.calls = []
                plain, audit = pd.produce(self.analysis, self.chips, mode="on", ask_model=self.asker(result))
                self.assertEqual(plain["source"], "template")
                self.assertIsNone(plain["model"])
                self.assertTrue(plain["fallback_reason"])
                self.assertEqual(audit["source"], "template")
                self.assertEqual(len(self.calls), 1, "失敗不重試，一天只用一次額度")

    def test_dry_run_calls_the_model_but_publishes_nothing(self):
        plain, audit = pd.produce(self.analysis, self.chips, mode="dry", ask_model=self.asker(("ok", good_ai(), "")))
        self.assertIsNone(plain)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(audit["candidate"]["source"], "ai")
        self.assertEqual(audit["template"]["source"], "template")

    def test_broken_numbers_never_raise(self):
        chips = copy.deepcopy(self.chips)
        chips["foreign"] = []
        plain, audit = pd.produce(self.analysis, chips, mode="on", ask_model=self.asker(("ok", good_ai(), "")))
        self.assertIsNone(plain)
        self.assertIn("facts_failed", audit["failure"])
        self.assertEqual(self.calls, [])


class AgyCallerTest(unittest.TestCase):
    def test_missing_agy_is_a_failure_not_a_crash(self):
        status, structured, _ = pd.agy_caller(None, home=None)("prompt", pd.OUTPUT_SCHEMA)
        self.assertEqual(status, "agy_unavailable")
        self.assertIsNone(structured)

    def test_agy_gets_its_own_home_the_chosen_model_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            seen = folder / "seen.json"
            script = folder / "fake_agy.py"
            script.write_text(
                "import json,os,sys\n"
                f"open({str(seen)!r},'w',encoding='utf-8').write(json.dumps({{'home':os.environ.get('HOME',''),'args':sys.argv[1:],"
                "'env':sorted(os.environ)}))\n"
                "sys.stdin.buffer.readline()\n"
                "print(json.dumps({'event':'result','result':{'status':'SUCCESS','structured_output':{'ok':1}}}))\n",
                encoding="utf-8")
            home = folder / "agy-home"
            home.mkdir()
            planted = {"CLOUDFLARE_API_TOKEN": "x", "R2_SECRET_ACCESS_KEY": "x", "R2_ACCESS_KEY_ID": "x", "EODHD_API_KEY": "x"}
            with unittest.mock.patch.dict("os.environ", planted):
                status, structured, _ = pd.agy_caller(script, home=home, model="gemini-test", effort="high")("prompt", pd.OUTPUT_SCHEMA)
            self.assertEqual((status, structured), ("ok", {"ok": 1}))
            observed = json.loads(seen.read_text(encoding="utf-8"))
            self.assertEqual(observed["home"], str(home))
            self.assertEqual(observed["args"][observed["args"].index("--model") + 1], "gemini-test")
            self.assertEqual(observed["args"][observed["args"].index("--effort") + 1], "high")
            self.assertFalse(set(planted) & {name.upper() for name in observed["env"]})
            self.assertIn("PATH", {name.upper() for name in observed["env"]})


class RewritePackTest(unittest.TestCase):
    """每日更新的入口：從 workflow 的環境變數讀模式、模型，紀錄寫進執行紀錄資料夾。"""

    def setUp(self):
        self.analysis, self.chips = market()
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_dir = Path(self.tmp.name) / "plain-daily"
        self.calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def run_pack(self, environ, result=None):
        def ask(prompt, schema):
            self.calls += 1
            return result or ("ok", good_ai(), "")
        return pd.rewrite_pack(self.analysis, self.chips, day="2026-11-06", environ=environ, audit_dir=self.audit_dir, ask_model=ask)

    def audit(self):
        return json.loads((self.audit_dir / "2026-11-06.json").read_text(encoding="utf-8"))

    def test_off_or_unset_calls_nothing_and_writes_nothing(self):
        for environ in ({}, {"PLAIN_DAILY_MODE": "off"}, {"PLAIN_DAILY_MODE": "maybe"}):
            with self.subTest(environ=environ):
                plain, summary = self.run_pack(environ)
                self.assertIsNone(plain)
                self.assertEqual(summary, {"mode": "off"})
        self.assertEqual(self.calls, 0)
        self.assertFalse(self.audit_dir.exists())

    def test_on_publishes_and_records_the_model_from_the_workflow(self):
        plain, summary = self.run_pack({"PLAIN_DAILY_MODE": "on", "PLAIN_MODEL": "gemini-x", "PLAIN_EFFORT": "low"})
        self.assertEqual(plain["source"], "ai")
        self.assertEqual(plain["model"], "gemini-x")
        self.assertEqual((summary["model"], summary["effort"], summary["published"]), ("gemini-x", "low", True))
        self.assertEqual(self.audit()["published"]["source"], "ai")

    def test_failed_model_publishes_the_program_text(self):
        plain, summary = self.run_pack({"PLAIN_DAILY_MODE": "on"}, ("model_timeout", None, "超過"))
        self.assertEqual(plain["source"], "template")
        self.assertEqual(summary["failure"], "model_timeout")

    def test_dry_records_what_would_be_published_but_returns_nothing(self):
        plain, summary = self.run_pack({"PLAIN_DAILY_MODE": "dry"})
        self.assertIsNone(plain)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.audit()["candidate"]["source"], "ai")
        self.assertNotIn("candidate", summary, "印在最後一行的摘要只放摘要")

    def test_unwritable_audit_folder_is_reported_not_swallowed(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        plain, summary = pd.rewrite_pack(self.analysis, self.chips, day="2026-11-06", environ={"PLAIN_DAILY_MODE": "on"},
                                         audit_dir=blocker / "sub", ask_model=lambda p, s: ("ok", good_ai(), ""))
        self.assertEqual(plain["source"], "ai")
        self.assertIn("audit_error", summary)

    def test_without_an_agy_the_workflow_settings_still_give_readable_text(self):
        plain, summary = pd.rewrite_pack(self.analysis, self.chips, day="2026-11-06", audit_dir=self.audit_dir,
                                         environ={"PLAIN_DAILY_MODE": "on", "PLAIN_AGY_BIN": "", "PLAIN_AGY_HOME": ""})
        self.assertEqual((plain["source"], summary["failure"]), ("template", "agy_unavailable"))


class AssignmentFileTest(unittest.TestCase):
    def test_written_file_is_what_the_build_reads(self):
        plain = pd.template_plain(pd.build_facts(*market()))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain-data.js"
            pd.write_plain(path, plain)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("window.MARKET_PLAIN = "))
            self.assertEqual(json.loads(text[len("window.MARKET_PLAIN = "):].strip().rstrip(";")), plain)


class CommandLineTest(unittest.TestCase):
    """owner 用真資料乾跑：讀一個大盤資料包資料夾，把結果寫成檔案，不碰網站。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        analysis, chips = market()
        for name, prefix, payload in (("analysis-data.js", "window.MARKET_ANALYSIS = ", analysis),
                                      ("chips-data.js", "window.MARKET_CHIPS = ", chips)):
            (self.folder / name).write_text(prefix + json.dumps(payload) + ";", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_a_model_it_shows_the_program_text(self):
        audit, plain_js = self.folder / "audit.json", self.folder / "plain-data.js"
        code = pd.main(["--market-dir", str(self.folder), "--mode", "on", "--audit", str(audit), "--write-plain", str(plain_js)])
        self.assertEqual(code, 0)
        result = json.loads(audit.read_text(encoding="utf-8"))
        self.assertEqual(result["source"], "template")
        self.assertEqual(result["failure"], "agy_unavailable")
        self.assertTrue(plain_js.read_text(encoding="utf-8").startswith(pd.PREFIX))

    def test_dry_mode_never_writes_a_plain_file(self):
        audit, plain_js = self.folder / "audit.json", self.folder / "plain-data.js"
        pd.main(["--market-dir", str(self.folder), "--mode", "dry", "--audit", str(audit), "--write-plain", str(plain_js)])
        self.assertFalse(plain_js.exists())
        self.assertEqual(json.loads(audit.read_text(encoding="utf-8"))["candidate"]["source"], "template")


class UsageFieldsTest(unittest.TestCase):
    """記進設定頁 Gemini 用量的欄位（11-19）：呼叫到模型才記；成功＝模型寫的白話通過核對；乾跑另外標。"""

    def audit(self, mode, result):
        analysis, chips = market()
        with tempfile.TemporaryDirectory() as tmp:
            pd.rewrite_pack(analysis, chips, day="2026-11-06", environ={"PLAIN_DAILY_MODE": mode, "PLAIN_MODEL": "gemini-4-argon"},
                            audit_dir=Path(tmp), ask_model=lambda prompt, schema: result)
            return json.loads((Path(tmp) / "2026-11-06.json").read_text(encoding="utf-8"))

    def test_success_failure_and_dry(self):
        self.assertEqual(pd.usage_fields(self.audit("on", ("ok", good_ai(), ""))),
                         {"model": "gemini-4-argon", "effort": "medium", "outcome": "ok", "dry_run": False})
        self.assertEqual(pd.usage_fields(self.audit("on", ("model_failed", None, "x")))["outcome"], "failed")
        self.assertEqual(pd.usage_fields(self.audit("on", ("ok", good_ai(one_line="外資賣超九百億。"), "")))["outcome"], "failed")
        self.assertEqual(pd.usage_fields(self.audit("dry", ("ok", good_ai(), ""))),
                         {"model": "gemini-4-argon", "effort": "medium", "outcome": "ok", "dry_run": True})

    def test_not_recorded_when_the_model_was_never_reached(self):
        self.assertIsNone(pd.usage_fields({"mode": "off"}))
        self.assertIsNone(pd.usage_fields(self.audit("on", ("agy_unavailable", None, "agy 沒有安裝成功"))))
        self.assertIsNone(pd.usage_fields({"mode": "on", "source": None, "failure": "facts_failed: ValueError"}))
        self.assertIsNone(pd.usage_fields(None))

    def test_a_missing_model_is_left_empty_not_guessed(self):
        audit = {"mode": "on", "source": "ai", "data_dates": {"price_and_foreign": "2026-11-06"}}
        self.assertEqual(pd.usage_fields(audit), {"model": None, "effort": None, "outcome": "ok", "dry_run": False})


if __name__ == "__main__":
    unittest.main()
