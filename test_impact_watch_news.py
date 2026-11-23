"""消息型盯項（本人 2026-11-20，.scratch/impact-watch 票 05）：模型寫「在等什麼被報導」→ 盯項；每天由模型讀後續新聞判斷。

假的模型、本機 SQLite 套同一份變更檔；不呼叫任何模型、不連網路。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gemini_usage
import impact_watch as iw
import impact_watch_news as iwn
import market_news as mn
import test_impact_watch as tiw
import test_market_news as tmn
import trading_days as td
import watch_news as wn

ROOT = Path(__file__).resolve().parent
NOW = tiw.NOW                       # 2026-11-19 台北 14:05：建立的那一班
DAY = "2026-11-19"
NEXT_DAY, NEXT_NOW = "2026-11-20", "2026-11-20T10:00:00Z"
EXPORT = {"what": "美方公布正式的出口管制清單", "keywords": ["出口管制", "清單"]}


def with_wait(wait_for):
    """假的 Flash：照原本的回答，再讓它寫在等的消息。wait_for(kind, 這一則或總結) → wait 的內容（None＝不寫）。"""
    def change(kind, output, _asked):
        for entry in output["items"] if kind == "items" else [output] if kind == "summary" else []:
            value = wait_for(kind, entry)
            if value is not None:
                entry["wait"] = value
    return tmn.Reply(change)


def in_summary(value):
    return with_wait(lambda kind, entry: value if kind == "summary" else None)


def news_items(d1):
    return [{**row, "params": json.loads(row["params"])} for row in d1.watch_items() if row["kind"] == "news"]


class CreateTests(unittest.TestCase):
    """消息排程：模型寫「在等什麼被報導」→ 消息型盯項。"""

    def test_what_the_model_is_waiting_for_becomes_a_news_type_item(self):
        d1 = tiw.NewsD1(tiw.facts())
        report, _, ask = tmn.run(d1, ask=in_summary(EXPORT))
        self.assertTrue(all("wait" in text for kind, text in zip(ask.kinds, ask.prompts) if kind in ("items", "summary")), "規則有寫給模型")
        [item] = news_items(d1)
        self.assertEqual((item["kind"], item["subject"], item["label"], item["status"], item["impact"], item["visibility"]),
                         ("news", "market", "美方公布正式的出口管制清單", "watching", 3, "all"))
        self.assertEqual(item["params"], {"keywords": ["出口管制", "清單"], "since": NOW}, "記下關鍵詞與從什麼時候開始等")
        self.assertEqual((item["source_block"], item["source_ref"], item["created_date"]), ("market_news", "2026-11-19-close#summary", DAY))
        self.assertEqual((item["expires_on"], item["days_left"]), (td.add_trading_days(DAY, 20, tiw.HOLIDAYS), 20), "和數字型同一個到期日")
        self.assertEqual(json.loads(item["prepare"]), ["如果加權跌破月線，就先檢查持股比重"], "原本寫的準備跟著存")
        watch = report["diagnosis"]["watch"]
        self.assertEqual((watch["news"], watch["created"]), ({"written": 1, "same_event": 0}, [item["id"]]))

    def test_it_is_written_even_when_no_numeric_list_is_on_offer(self):
        d1 = tiw.NewsD1(None)   # 還沒有每日檢查的數字：沒有數字型清單
        tmn.run(d1, ask=in_summary(EXPORT))
        self.assertEqual([item["label"] for item in news_items(d1)], ["美方公布正式的出口管制清單"])

    def test_minor_news_does_not_start_one_and_a_major_item_does(self):
        d1 = tiw.NewsD1(tiw.facts())
        tmn.run(d1, ask=with_wait(lambda kind, entry: {"what": f"等{entry['tone']}的後續", "keywords": [f"後續{entry['tone']}", "公布"]}
                                  if kind == "items" else None))
        stored = {row["item_id"]: row["impact"] for row in d1.query("SELECT item_id, impact FROM market_news_items")}
        made = news_items(d1)
        self.assertTrue(made)
        self.assertTrue(all(stored[item["source_ref"].split("#")[1]] >= mn.WATCH_MIN_IMPACT for item in made))
        low = tmn.Reply(lambda kind, output, asked: [entry.update(impact="1", wait=EXPORT) for entry in output.get("items", [])])
        d2 = tiw.NewsD1(tiw.facts())
        tmn.run(d2, ask=low)
        self.assertEqual(news_items(d2), [], "影響最小的消息不建")

    def test_the_same_event_is_only_watched_once(self):
        d1 = tiw.NewsD1(tiw.facts())
        tmn.run(d1, ask=in_summary(EXPORT), slot="morning")
        same = [{"what": "美國正式公布管制清單", "keywords": ["清單", "出口管制"]},                # 關鍵詞一樣、順序不同
                {"what": "美方出口管制清單出爐", "keywords": ["出口管制", "清單", "美國"]},        # 多一個詞：包含原本那一組
                {"what": "美國商務部公布清單", "keywords": ["美國", "清單", "出口 管制"]}]          # 共有兩個（空白不算）
        for slot, wait in zip(("midday", "close", "midday"), same):
            report, _, _ = tmn.run(d1, ask=in_summary(wait), slot=slot)
            self.assertEqual(report["diagnosis"]["watch"]["news"], {"written": 1, "same_event": 1}, wait)
        self.assertEqual([item["label"] for item in news_items(d1)], ["美方公布正式的出口管制清單"])
        tmn.run(d1, ask=in_summary({"what": "台積電法說會給出明年展望", "keywords": ["台積電", "法說會"]}), slot="close")
        self.assertEqual(len(news_items(d1)), 2, "不同的事照建")
        tmn.run(d1, ask=in_summary({"what": "輝達公布清單上的客戶", "keywords": ["輝達", "清單"]}), slot="close")
        self.assertEqual(len(news_items(d1)), 3, "只共有一個詞不算同一件")

    def test_one_shift_writing_the_same_event_twice_keeps_the_bigger_one(self):
        d1 = tiw.NewsD1(tiw.facts())
        tmn.run(d1, ask=with_wait(lambda kind, entry: EXPORT))   # 每一則與總結都寫同一件
        [item] = news_items(d1)
        self.assertEqual(item["impact"], 3)

    def test_a_just_resolved_event_still_on_the_home_page_is_not_rebuilt(self):
        d1 = tiw.NewsD1(tiw.facts())
        tmn.run(d1, ask=in_summary(EXPORT), slot="morning")
        d1.db.execute("UPDATE impact_watch_items SET status = 'met', result_date = ?, show_until = ?", (DAY, "2026-11-24"))
        tmn.run(d1, ask=in_summary({"what": "出口管制清單公布", "keywords": ["清單", "出口管制", "公布"]}), slot="close")
        self.assertEqual([item["status"] for item in news_items(d1)], ["met"])

    def test_it_shares_the_limit_of_ten_with_the_numeric_items(self):
        db = tiw.Db()
        numeric = [{"condition_key": f"k{index}", "label": f"條件 {index}", "params": {}, "note": None, "impact": 2,
                    "prepare": None, "source_ref": "x"} for index in range(iw.MAX_WATCHING)]
        db.create(numeric, trade_date="2026-11-18")
        wait = {**iwn.news_pick({"wait": EXPORT}), "impact": 3, "prepare": None, "source_ref": "2026-11-19-close#summary"}
        kept, same = iwn.fresh([wait], iwn.existing(db.query, DAY), now=NOW)
        report = db.create(kept)
        watching = db.items("status = 'watching'")
        self.assertEqual((len(watching), same, len(report["closed_for_room"])), (iw.MAX_WATCHING, 0, 1))
        self.assertEqual([row["label"] for row in watching if row["kind"] == "news"], ["美方公布正式的出口管制清單"])
        minor = {**iwn.news_pick({"wait": {"what": "央行公布理監事會議結果", "keywords": ["央行", "理監事"]}}), "impact": 1,
                 "prepare": None, "source_ref": "x"}
        report = db.create(iwn.fresh([minor], iwn.existing(db.query, DAY), now=NOW)[0])
        self.assertEqual((report["created"], report["over_limit"]), ([], 1), "滿了、影響力又比較低的不建")

    def test_a_wait_that_is_not_usable_is_ignored_and_the_news_is_unchanged(self):
        bad = ["等出口管制清單", {"what": "只有一句"}, {"what": "", "keywords": ["出口管制"]}, {"what": "沒有合格的詞", "keywords": ["A", 3, " "]},
               {"what": "只有一個詞", "keywords": ["出口管制"]},
               {"what": "太長" * 30, "keywords": ["出口管制", "清單"]}]
        for value in bad:
            d1 = tiw.NewsD1(tiw.facts())
            report, _, _ = tmn.run(d1, ask=in_summary(value))
            self.assertEqual(news_items(d1), [], value)
            self.assertNotIn("news", report["diagnosis"]["watch"])
            self.assertEqual((report["run_status"], len(d1.query("SELECT 1 FROM market_news_items"))), ("ok", 4))

    def test_a_wait_that_reads_like_an_order_is_dropped_and_counted(self):
        d1 = tiw.NewsD1(tiw.facts())
        report, _, _ = tmn.run(d1, ask=in_summary({"what": "清單公布後建議買進半導體", "keywords": ["出口管制", "清單"]}))
        self.assertEqual(news_items(d1), [])
        self.assertEqual(report["diagnosis"]["points"]["text_rejected"], {"wait": 1})
        self.assertEqual(d1.query("SELECT summary FROM market_news_runs")[0]["summary"], "台股今天下跌，主要是外資賣超。", "總結照樣在")

    def test_keywords_are_cleaned_and_capped_at_three(self):
        pick = iwn.news_pick({"wait": {"what": "美方公布清單。", "keywords": ["「出口管制」", " 清單 ", "清單", "美國", "商務部", "x"]}})
        self.assertEqual((pick["label"], pick["params"]["keywords"]), ("美方公布清單", ["出口管制", "清單", "美國"]))

    def test_without_the_table_the_news_still_goes_in(self):
        d1 = tiw.NewsD1(None, schema=tmn.SCHEMA)   # 0034 還沒套
        report, _, _ = tmn.run(d1, ask=in_summary(EXPORT))
        self.assertEqual(report["diagnosis"]["watch"]["error"], "OperationalError")
        self.assertEqual((report["run_status"], len(d1.query("SELECT 1 FROM market_news_items"))), ("ok", 4))

    def test_the_schema_the_model_gets_has_the_new_field(self):
        self.assertIs(mn.ITEMS_SCHEMA["properties"]["items"]["items"]["properties"]["wait"], iwn.WAIT_SCHEMA)
        self.assertEqual(mn.SUMMARY_SCHEMA["properties"]["wait"]["required"], ["what", "keywords"])


class Judge:
    """假的 Flash：answer(INPUT) → verdicts；記下每一次的提示。"""

    def __init__(self, answer, status="ok"):
        self.answer, self.status, self.inputs = answer, status, []

    def __call__(self, prompt, schema):
        assert schema is iwn.VERDICT_SCHEMA
        payload = json.JSONDecoder().raw_decode(prompt[prompt.index("INPUT:") + 6:])[0]
        self.inputs.append(payload)
        status = self.status(len(self.inputs)) if callable(self.status) else self.status
        if status != "ok":
            return status, None, "壞了"
        return "ok", {"verdicts": self.answer(payload)}, ""


def said(verdict, pick=0, reason="美國商務部公布清單，納入多家晶片廠"):
    """每一件都回同一種判斷；依據那一件的第 pick 則新聞。"""
    def answer(payload):
        return [{"id": entry["id"], "verdict": verdict,
                 **({"news": entry["news"][pick]["id"], "reason": reason} if verdict != "waiting" else {})}
                for entry in payload["waiting"]]
    return answer


class CheckDb(tiw.NewsD1):
    def __init__(self):
        super().__init__(None)
        self.runs = 0

    def watch(self, wait=EXPORT, *, day=DAY, now=NOW, impact=3):
        pick = {**iwn.news_pick({"wait": wait}), "impact": impact, "prepare": ["如果清單比預期嚴，就先檢查半導體佔持股多少"],
                "source_ref": f"{day}-close#summary"}
        kept, _ = iwn.fresh([pick], iwn.existing(self.query, day), now=now)
        script, report = iw.creation_sql(kept, iw.active_items(self.query, day), trade_date=day, now=now, holidays=tiw.HOLIDAYS)
        self.execute(script)
        return report["created"][0]

    def news(self, fetched_at, *rows):
        """一輪消息：rows 是 (標題, 連結) 或 (標題, 連結, 一句理由)。"""
        self.runs += 1
        run_id = f"run{self.runs}"
        self.db.execute("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status) VALUES (?, ?, ?, ?, 'ok')",
                        (run_id, fetched_at[:10], mn.SLOTS[self.runs % 3], fetched_at))
        for index, row in enumerate(rows):
            title, link, reason = (*row, "一句理由")[:3]
            self.db.execute("INSERT INTO market_news_items (run_id, item_id, grp, market, kind, tone, impact, title, translated, reason, "
                            "source, link, published_at) VALUES (?, ?, 'us', '', 'policy', 'down', 3, ?, 0, ?, '路透', ?, ?)",
                            (run_id, f"n{index + 1}", title, reason, link, fetched_at))

    def check(self, ask, day=NEXT_DAY, now=NEXT_NOW, **kwargs):
        return iwn.check(day, now=now, query=self.query, execute=self.execute, ask=ask, holidays=tiw.HOLIDAYS, **kwargs)

    def item(self, item_id):
        row = self.query(f"SELECT * FROM impact_watch_items WHERE id = '{item_id}'")[0]
        return {**row, "params": json.loads(row["params"])}


def sqlite_usage():
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    for name in ("0024", "0029"):
        db.executescript(next((ROOT / "cloud/migrations").glob(f"{name}_*.sql")).read_text(encoding="utf-8"))
    return db


def never(prompt, schema):
    raise AssertionError("不該呼叫模型")


LIST_NEWS = ("美國公布晶片出口管制清單，輝達在列", "https://example.com/list")


class CheckTests(unittest.TestCase):
    """每日檢查：把之後抓到、對得上關鍵詞的新聞交給模型判斷。"""

    def test_with_no_matching_news_the_model_is_not_called(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-19T03:30:00Z", ("美國考慮擴大出口管制清單", "https://example.com/before"))     # 建立之前就抓到的
        db.news("2026-11-20T06:05:00Z", ("外資賣超台股 300 億", "https://example.com/other"),            # 對不上
                ("美國考慮擴大出口管制清單", "https://example.com/before"),                               # 舊的那一則又出現在新的一輪
                ("美國擴大出口管制", "https://example.com/one"))                                          # 兩個詞只對到一個
        report = db.check(never)
        self.assertEqual((report["checked"], report["with_news"], report["model_calls"], report["watching"]), (1, 0, 0, 1))
        item = db.item(item_id)
        self.assertEqual((item["status"], item["days_left"]), ("watching", 19), "照樣更新還剩幾個交易日")

    def test_met_records_the_result_and_the_news_it_rests_on(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", ("出口管制清單傳本週公布", "https://example.com/rumor"), LIST_NEWS)
        judge = Judge(said("met", pick=0))
        report = db.check(judge)
        [asked] = judge.inputs[0]["waiting"]
        self.assertEqual((asked["wait"], asked["since"]), ("美方公布正式的出口管制清單", DAY))
        self.assertEqual({row["title"] for row in asked["news"]}, {"出口管制清單傳本週公布", LIST_NEWS[0]})
        self.assertNotIn("link", asked["news"][0], "連結不給模型，依據用編號指")
        picked = asked["news"][0]["title"]
        link = {"出口管制清單傳本週公布": "https://example.com/rumor", LIST_NEWS[0]: LIST_NEWS[1]}[picked]
        item = db.item(item_id)
        self.assertEqual((item["status"], item["result_date"], item["days_left"]), ("met", NEXT_DAY, None))
        self.assertEqual(item["result_note"], "成立：美國商務部公布清單，納入多家晶片廠。")
        self.assertEqual(item["params"]["evidence"], {"title": picked, "link": link, "source": "路透"})
        self.assertEqual(item["params"]["keywords"], ["出口管制", "清單"], "原本的參數還在")
        self.assertEqual(item["show_until"], td.add_trading_days(NEXT_DAY, iw.KEEP_RESULT_TRADING_DAYS, tiw.HOLIDAYS))
        self.assertIsNone(item["push_sent_at"], "等推播那一步來送")
        self.assertEqual((report["model_calls"], [row["id"] for row in report["met"]]), (1, [item_id]))

    def test_failed_says_so(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", ("美國宣布暫緩出口管制清單", "https://example.com/off"))
        report = db.check(Judge(said("failed", reason="美方宣布暫緩，清單不會公布")))
        item = db.item(item_id)
        self.assertEqual((item["status"], item["result_note"]), ("failed", "否定：美方宣布暫緩，清單不會公布。"))
        self.assertEqual(item["params"]["evidence"]["link"], "https://example.com/off")
        self.assertEqual([row["id"] for row in report["failed"]], [item_id])

    def test_not_yet_is_remembered_so_the_same_news_is_not_asked_twice(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", ("出口管制清單傳本週公布", "https://example.com/rumor"))
        report = db.check(Judge(said("waiting")))
        self.assertEqual((report["model_calls"], report["watching"], report["invalid"]), (1, 1, []))
        self.assertEqual((db.item(item_id)["status"], db.item(item_id)["params"]["checked_through"]), ("watching", NEXT_NOW))
        db.news("2026-11-23T00:05:00Z", ("出口管制清單傳本週公布", "https://example.com/rumor"))   # 同一則還在新的一輪裡
        self.assertEqual(db.check(never, day="2026-11-23", now="2026-11-23T10:00:00Z")["model_calls"], 0)
        db.news("2026-11-24T00:05:00Z", LIST_NEWS)
        judge = Judge(said("met"))
        db.check(judge, day="2026-11-24", now="2026-11-24T10:00:00Z")
        self.assertEqual([row["title"] for row in judge.inputs[0]["waiting"][0]["news"]], [LIST_NEWS[0]], "只給新的那一則")
        self.assertEqual(db.item(item_id)["status"], "met")

    def test_an_answer_that_does_not_hold_up_counts_as_not_yet_and_is_logged(self):
        answers = {
            "missing": lambda payload: [],
            "bad_verdict": lambda payload: [{"id": "w1", "verdict": "maybe"}],
            "unknown_news": lambda payload: [{"id": "w1", "verdict": "met", "news": "n99", "reason": "清單公布了"}],
            "no_reason": lambda payload: [{"id": "w1", "verdict": "met", "news": "w1n1", "reason": " "}],
            "reason_too_long": lambda payload: [{"id": "w1", "verdict": "met", "news": "w1n1", "reason": "清單" * 40}],
            "trade_advice": lambda payload: [{"id": "w1", "verdict": "met", "news": "w1n1", "reason": "清單公布，建議賣出半導體"}],
        }
        for problem, answer in answers.items():
            db = CheckDb()
            item_id = db.watch()
            db.news("2026-11-20T00:05:00Z", LIST_NEWS)
            report = db.check(Judge(answer))
            item = db.item(item_id)
            self.assertEqual((item["status"], item["result_note"], item["days_left"]), ("watching", None, 19), problem)
            self.assertEqual(report["invalid"], [{"id": item_id, "problem": problem}])
            self.assertNotIn("checked_through", item["params"], "不合格的不算判過：下次再問")
            self.assertEqual(db.check(Judge(said("met")), day="2026-11-23", now="2026-11-23T10:00:00Z")["met"][0]["id"], item_id)

    def test_a_reply_that_is_not_an_object_is_logged_for_every_item(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        report = db.check(lambda prompt, schema: ("ok", "不是 JSON", ""))
        self.assertEqual((report["invalid"], db.item(item_id)["status"]), ([{"id": item_id, "problem": "missing"}], "watching"))
        for odd in ({"verdicts": 3}, {"verdicts": [{"id": ["w1"], "verdict": "met"}, {"id": "w1", "verdict": ["met"]}]}):
            db1 = CheckDb()
            item_id = db1.watch()
            db1.news("2026-11-20T00:05:00Z", LIST_NEWS)
            report = db1.check(lambda prompt, schema: ("ok", odd, ""))
            self.assertEqual((len(report["invalid"]), db1.item(item_id)["days_left"]), (1, 19), "形狀再怪也照樣寫剩幾天")
        db2 = CheckDb()
        db2.watch()
        db2.news("2026-11-20T00:05:00Z", LIST_NEWS)
        text = json.dumps({"verdicts": said("met")({"waiting": [{"id": "w1", "news": [{"id": "w1n1"}]}]})}, ensure_ascii=False)
        self.assertEqual(len(db2.check(lambda prompt, schema: ("ok", text, ""))["met"]), 1, "JSON 字串照樣讀")

    def test_a_failed_call_is_tried_once_more_and_then_left_for_tomorrow(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        report = db.check(Judge(said("met"), status="model_timeout"))
        self.assertEqual((report["model_calls"], [call["status"] for call in report["calls"]]), (2, ["model_timeout"] * 2))
        self.assertEqual((report["invalid"], db.item(item_id)["status"]), ([{"id": item_id, "problem": "no_answer"}], "watching"))
        second = Judge(said("met"), status=lambda asked: "model_failed" if asked == 1 else "ok")
        report = db.check(second, day="2026-11-23", now="2026-11-23T10:00:00Z")
        self.assertEqual((report["model_calls"], db.item(item_id)["status"]), (2, "met"), "第二次成功就用")

        def broken(prompt, schema):
            raise RuntimeError("agy 掛了")
        db3 = CheckDb()
        db3.watch()
        db3.news("2026-11-20T00:05:00Z", LIST_NEWS)
        self.assertEqual([call["status"] for call in db3.check(broken)["calls"]], ["RuntimeError", "RuntimeError"])

    def test_every_item_with_news_goes_in_one_call_with_at_most_six_news_each(self):
        db = CheckDb()
        first = db.watch()
        second = db.watch({"what": "台積電法說會給出明年展望", "keywords": ["台積電", "法說會", "展望"]})
        quiet = db.watch({"what": "央行公布理監事會議結果", "keywords": ["央行", "理監事"]})
        db.news("2026-11-20T00:05:00Z", *[(f"出口管制清單第 {index} 則", f"https://example.com/e{index}") for index in range(9)],
                ("台積電法說會登場", "https://example.com/t1"), ("台積電法說會：明年展望優於預期", "https://example.com/t3"),
                ("TSMC guidance beats", "https://example.com/t0", "台積電在法說會上調展望"),
                ("沒有連結的", "not-a-link", "出口管制清單"))
        judge = Judge(lambda payload: [{"id": entry["id"], "verdict": "met", "news": entry["news"][0]["id"], "reason": "台積電法說會上調明年展望"}
                                       if "台積電" in entry["wait"] else {"id": entry["id"], "verdict": "waiting"}
                                       for entry in payload["waiting"]])
        report = db.check(judge)
        self.assertEqual((len(judge.inputs), report["model_calls"], report["with_news"]), (1, 1, 2))
        tsmc, export = sorted(judge.inputs[0]["waiting"], key=lambda entry: "台積電" not in entry["wait"])
        self.assertEqual(len(export["news"]), iwn.NEWS_PER_ITEM)
        self.assertEqual(tsmc["news"][0]["title"], "台積電法說會：明年展望優於預期", "對到最多個詞的排最前面")
        self.assertEqual({row["title"] for row in tsmc["news"]}, {"台積電法說會登場", "台積電法說會：明年展望優於預期", "TSMC guidance beats"},
                         "標題對不上、一句理由對得上的也算")
        ids = [row["id"] for entry in judge.inputs[0]["waiting"] for row in entry["news"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([db.item(key)["status"] for key in (first, second, quiet)], ["watching", "met", "watching"])
        self.assertEqual(db.item(second)["params"]["evidence"]["link"], "https://example.com/t3")
        self.assertNotIn("checked_through", db.item(quiet)["params"], "沒有新聞的那一筆沒被判過")
        # 放不下的那三則沒有被跳過：下一個交易日接著給，給完才往前推
        self.assertNotIn("checked_through", db.item(first)["params"])
        later = Judge(said("waiting"))
        db.check(later, day="2026-11-23", now="2026-11-23T10:00:00Z")
        [rest] = later.inputs[0]["waiting"]
        self.assertEqual(len(rest["news"]), 3)
        self.assertFalse({row["title"] for row in rest["news"]} & {row["title"] for row in export["news"]}, "看過的不再給")
        self.assertEqual((db.item(first)["params"]["checked_through"], "seen" in db.item(first)["params"]), ("2026-11-23T10:00:00Z", False))
        self.assertEqual(db.check(never, day="2026-11-24", now="2026-11-24T10:00:00Z")["model_calls"], 0)

    def test_expiry_is_silent_and_a_result_on_the_last_day_still_counts(self):
        last = td.add_trading_days(DAY, 20, tiw.HOLIDAYS)
        db = CheckDb()
        item_id = db.watch()
        report = db.check(never, day=last, now=f"{last}T10:00:00Z")
        item = db.item(item_id)
        self.assertEqual((item["status"], item["result_note"], item["show_until"], report["expired"]), ("expired", None, None, [item_id]))
        db2 = CheckDb()
        item_id = db2.watch()
        db2.news(f"{last}T00:05:00Z", LIST_NEWS)
        db2.check(Judge(said("met")), day=last, now=f"{last}T10:00:00Z")
        self.assertEqual(db2.item(item_id)["status"], "met")

    def test_numeric_items_and_the_numeric_check_leave_each_other_alone(self):
        db = CheckDb()
        news_id = db.watch()
        db.create = lambda picks: db.execute(iw.creation_sql(picks, iw.active_items(db.query, DAY), trade_date=DAY, now=NOW,
                                                             holidays=tiw.HOLIDAYS)[0])
        db.create([tiw.pick("below_ma20")])
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        judge = Judge(said("met"))
        report = db.check(judge)
        self.assertEqual((report["checked"], len(judge.inputs[0]["waiting"])), (1, 1))
        self.assertEqual(db.query("SELECT status FROM impact_watch_items WHERE kind = 'numeric'")[0]["status"], "watching")
        db2 = CheckDb()
        news_id = db2.watch()
        script, numeric = iw.check_sql(db2.query("SELECT * FROM impact_watch_items WHERE status = 'watching'"), tiw.facts(end=NEXT_DAY),
                                       now=NEXT_NOW, holidays=tiw.HOLIDAYS)
        db2.execute(script)
        self.assertEqual((numeric["checked"], db2.item(news_id)["status"], db2.item(news_id)["days_left"]), (0, "watching", 20))

    def test_a_dry_run_asks_but_writes_nothing(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        report = db.check(Judge(said("met")), dry_run=True)
        self.assertEqual((len(report["met"]), report["dry_run"], db.item(item_id)["status"]), (1, True, "watching"))

    def test_without_the_table_or_without_items_nothing_happens(self):
        bare = tmn.D1()
        report = iwn.check(NEXT_DAY, now=NEXT_NOW, query=bare.query, execute=bare.execute, ask=never)
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(CheckDb().check(never)["checked"], 0)

    def test_unreadable_news_leaves_everything_waiting(self):
        db = CheckDb()
        item_id = db.watch()
        db.db.execute("DROP TABLE market_news_items")
        report = db.check(never)
        self.assertEqual((report["news_error"], db.item(item_id)["days_left"]), ("OperationalError", 19))


class WiringTests(unittest.TestCase):
    """排程與用量：每日檢查掛在哪、呼叫次數怎麼進設定頁。"""

    def run_main(self, db, asker, *extra):
        with tempfile.TemporaryDirectory() as folder:
            analysis = Path(folder) / "analysis-data.js"
            pack = {"daily": tiw.daily(tiw.RISING, end=NEXT_DAY), "institutional": tiw.flows((120, -80, -60), end=NEXT_DAY)}
            analysis.write_text("window.ANALYSIS = " + json.dumps(pack) + ";\n", encoding="utf-8")
            printed = []
            with mock.patch.object(wn, "_wrangler_query", db.query), mock.patch.object(wn, "_wrangler_execute", db.execute), \
                    mock.patch.object(mn, "_owner_asker", lambda *args: asker), mock.patch("builtins.print", printed.append), \
                    mock.patch.object(td, "known_holidays", lambda day: tiw.HOLIDAYS):
                log = Path(folder) / "log" / "report.json"
                self.assertEqual(iwn.main(["check", "--analysis", str(analysis), "--report", str(log), *extra]), 0)
                return json.loads(printed[-1]), json.loads(log.read_text(encoding="utf-8"))

    def test_the_command_checks_the_day_that_was_just_published(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        report, logged = self.run_main(db, Judge(said("met")))
        self.assertEqual((report["data_date"], report["model_calls"], report["model"], report["effort"]),
                         (NEXT_DAY, 1, "gemini-3.8-flash", "high"))
        # 倉庫公開之後：印出來的是遮過的那一份（模型寫的理由只留字數），完整的留在執行紀錄
        import public_log
        self.assertEqual(report, public_log.impact_watch_news(logged))
        self.assertEqual(logged["met"][0]["id"], item_id)
        self.assertTrue(logged["met"][0]["note"].startswith("成立"), "完整的報告（含模型寫的理由）留在執行紀錄")
        self.assertEqual([entry["id"] for entry in report["met"]["market"]], [item_id])
        self.assertNotIn("note", report["met"]["market"][0])
        self.assertEqual(report["met"]["stocks"], 0)
        self.assertEqual(db.item(item_id)["status"], "met")

    def test_each_model_call_is_one_usage_row_and_no_call_is_none(self):
        db = CheckDb()
        db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        usage = sqlite_usage()
        failed_then_ok = Judge(said("met"), status=lambda asked: "model_failed" if asked == 1 else "ok")
        report, _ = self.run_main(db, failed_then_ok)
        usage.executescript(iwn.usage_sql(report, [{"id": "flash"}], run_id="777", taken_at=NEXT_NOW))
        rows = [dict(row) for row in usage.execute("SELECT task, run_id, model, buckets_json FROM gemini_usage_snapshots ORDER BY run_id")]
        self.assertEqual([(row["task"], row["run_id"], row["model"]) for row in rows],
                         [("impact-watch", "777-1", "gemini-3.8-flash"), ("impact-watch", "777-2", "gemini-3.8-flash")])
        self.assertEqual(json.loads(rows[0]["buckets_json"]), [{"id": "flash"}])
        quiet, _ = self.run_main(CheckDb(), never)
        for nothing in (quiet, {**report, "dry_run": True}, None, {"status": "skipped"}):
            self.assertEqual(iwn.usage_sql(nothing, None, run_id="778", taken_at=NEXT_NOW), "")

    def test_an_agy_that_is_not_there_is_not_a_call(self):
        db = CheckDb()
        item_id = db.watch()
        db.news("2026-11-20T00:05:00Z", LIST_NEWS)
        report = db.check(lambda prompt, schema: ("agy_unavailable", None, "agy 沒有安裝成功"))
        self.assertEqual((report["model_calls"], len(report["calls"]), report["invalid"]), (0, 1, [{"id": item_id, "problem": "no_answer"}]))

    def test_the_usage_command_writes_the_rows_for_the_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            report, sql = Path(folder) / "report.json", Path(folder) / "usage.sql"
            report.write_text("警告：{不是報告}\n" + json.dumps({"status": "ok", "model_calls": 1, "model": "gemini-3.8-flash",
                                                              "dry_run": False}) + "\n", encoding="utf-8")
            with mock.patch.object(gemini_usage, "read_usage", lambda agy_bin: [{"id": "flash", "remaining": 0.8}]):
                self.assertEqual(iwn.main(["usage", "--report", str(report), "--run-id", "9", "--agy-bin", "agy", "--sql-output", str(sql)]), 0)
            self.assertIn("'impact-watch', '9-1'", sql.read_text(encoding="utf-8"))
            self.assertEqual(iwn.main(["usage", "--report", str(Path(folder) / "none.json"), "--run-id", "9", "--sql-output", str(sql)]), 0)
            self.assertEqual(sql.read_text(encoding="utf-8"), "", "沒有報告（那一步沒跑）就不記")

    def test_the_usage_page_knows_the_task(self):
        self.assertEqual(gemini_usage.model_for_task(iwn.TASK), "gemini-3.8-flash")
        self.assertIn(f"'{iwn.TASK}': '盯項讀新聞'", (ROOT / "web/settings.js").read_text(encoding="utf-8"))

    def test_the_daily_update_runs_it_after_the_numeric_check_and_before_the_push(self):
        text = (ROOT / ".github/workflows/daily-update.yml").read_text(encoding="utf-8")
        numeric = text.index("python impact_watch.py check")
        news = text.index("python impact_watch_news.py check")
        usage = text.index("python impact_watch_news.py usage")
        push = text.index("node cloud/impact-watch-push.mjs")
        cleanup = text.index("- name: 清除 agy 登入檔與執行檔")
        self.assertTrue(numeric < news < usage < push < cleanup)
        step = text[news - 2500:usage + 600]
        for needed in ("AGY_LOGIN_BUNDLE", "1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807", "impact-watch-usage.sql",
                       "continue-on-error: true", "steps.day.outputs.dry_run == ''"):
            self.assertIn(needed, step)


if __name__ == "__main__":
    unittest.main()
