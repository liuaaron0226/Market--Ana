"""首頁「消息」（本人 2026-11-19「共識達成」，.scratch/market-news/consensus.md）。

假的網路、假的模型、本機 SQLite 套同一份變更檔；不呼叫真的 Gemini。"""
import json
import sqlite3
import unittest
from pathlib import Path

import market_news as mn
import watch_news as wn

ROOT = Path(__file__).resolve().parent
MIGRATIONS = ROOT / "cloud/migrations"
OLD_SCHEMA = "".join((MIGRATIONS / name).read_text(encoding="utf-8")
                     for name in ("0005_user_portfolio.sql", "0011_watchlist_watch.sql", "0015_watch_news.sql", "0031_market_news.sql", "0032_stock_news_marks.sql"))
SCHEMA = OLD_SCHEMA + (MIGRATIONS / "0033_news_impact.sql").read_text(encoding="utf-8")
NOW = "2026-11-19T06:05:00Z"          # 台北 14:05，收盤後那一輪
OWNER = "owner@example.com"
FRIEND = "friend@example.com"


def rss(*items):
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>x</title>'
            f'{"".join(items)}</channel></rss>').encode("utf-8")


def item(title, link, when="Thu, 19 Nov 2026 04:00:00 GMT", summary="summary"):
    return (f"<item><title>{title}</title><link>{link}</link><pubDate>{when}</pubDate>"
            f"<description>{summary}</description></item>")


def gitem(title, n, outlet="https://www.reuters.com", when="Thu, 19 Nov 2026 03:00:00 GMT"):
    return (f"<item><title>{title}</title><link>https://news.google.com/rss/articles/M{n}</link><pubDate>{when}</pubDate>"
            f"<source url=\"{outlet}\">x</source></item>")


class Response:
    def __init__(self, status=200, content=b""):
        self.status, self.content = status, content


def web(overrides=None):
    routes = {feed["key"]: Response(content=rss()) for feed in mn.FEEDS}
    routes.update({
        "cna-finance": Response(content=rss(item("外資賣超台股逾 600 億元", "https://www.cna.com.tw/news/afe/1.aspx"))),
        "wsj-markets": Response(content=rss(item("Chip stocks slide ahead of Nvidia earnings", "https://www.wsj.com/a"))),
        "yonhap-biz": Response(content=rss(item("Korea exports rise 9 pct", "https://en.yna.co.kr/view/1"))),
        "gnews-tw": Response(content=rss(gitem("Taiwan stocks fall as TSMC drops - Reuters", 1))),
        "bbc-business": Response(content=rss(item("Old story", "https://www.bbc.com/news/x", when="Mon, 02 Nov 2026 01:00:00 GMT"))),
    })
    routes.update(overrides or {})
    by_url = {feed["url"]: routes[feed["key"]] for feed in mn.FEEDS}

    def get(url, params=None):
        calls.append(url)
        return by_url[url]
    calls = []
    return get, calls


class D1:
    def __init__(self, schema=SCHEMA):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(schema)
        self.db.execute("CREATE TABLE overseas_quotes (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
        for symbol, closes in (("SOX.INDX", (100.0, 96.9)), ("N225.INDX", (200.0, 197.7))):
            for day, close in zip(("2026-11-17", "2026-11-18"), closes):
                self.db.execute("INSERT INTO overseas_quotes VALUES (?, ?, 0, 0, 0, ?, 0)", (symbol, day, close))
        add = ("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at, watching) "
               "VALUES (?, ?, ?, '上市', 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)")
        self.db.execute(add, (OWNER, "2330", "台積電"))
        self.db.execute(add, (FRIEND, "2454", "聯發科"))
        news = ("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) "
                "VALUES (?, ?, 'gnews-reuters', ?, '', '2026-11-19T02:00:00Z', '2026-11-19T06:00:00Z')")
        self.db.execute(news, ("2330", "https://news.google.com/rss/articles/T1", "TSMC ADR falls 4%"))
        self.db.execute(news, ("2454", "https://news.google.com/rss/articles/M1", "MediaTek wins flagship orders"))
        self.scripts = []

    def query(self, sql):
        return [dict(row) for row in self.db.execute(sql)]

    def execute(self, script):
        self.scripts.append(script)
        self.db.executescript(script)


def model_reply(prompt_items, watch):
    """假的 Flash：每則都判，台股那則利空、韓國那則利多，其他中性；自選的全利空。"""
    items = []
    for row in prompt_items:
        tone = "down" if "外資" in row["title"] or "Taiwan" in row["title"] else "up" if "Korea" in row["title"] else "flat"
        group = {"tw": "tw", "us": "us", "asia": "asia", "global": "us"}[row["group_hint"]]
        items.append({"id": row["id"], "group": group, "market": "韓" if "Korea" in row["title"] else "",
                      "kind": "flows" if "外資" in row["title"] else "policy" if "限空" in row["title"] else "other", "tone": tone, "impact": 3 if tone == "down" else 2, "title": "中文：" + row["title"][:20], "reason": "一句理由",
                      "effect": "權值股壓低指數", "owner_effect": "台積電是外資賣超的重心" if "外資" in row["title"] else "",
                      "conditions": ["外資是否連續賣超"], "prepare": ["如果外資連續賣超，就先檢查權值股佔多少"]})
    return {"summary": "台股今天下跌，主要是外資賣超。", "items": items,
            "watch": [{"id": row["id"], "relevant": "no" if "馬拉松" in row["title"] else "yes",
                       "kind": "earnings" if "營收" in row["title"] else "rating", "tone": "down", "impact": 2,
                       "title": "自選：" + row["title"], "reason": "跟著走弱", "effect": "短線股價承壓",
                       "conditions": ["費半是否止跌"], "prepare": ["如果費半續跌，就先想好能承受的跌幅"]} for row in watch]}


def kind_of(schema):
    return next(kind for key, kind in (("items", "items"), ("watch", "watch"), ("market_commentary", "summary"))
                if key in schema["properties"])


class FakeAsk:
    """假的 Flash：依結構描述回市場新聞一批、自選或總結（一輪分好幾次叫）。"""

    def __init__(self, status="ok"):
        self.status, self.prompts, self.kinds = status, [], []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        self.kinds.append(kind_of(schema))
        # 重問時提示後面接著退回的原因，所以只讀 INPUT: 後面那一個 JSON
        payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("INPUT:") + len("INPUT:"):])
        if self.status != "ok":
            return self.status, None, "壞了"
        kind = kind_of(schema)
        if kind == "summary":
            return "ok", {"market_commentary": "台股今天下跌，主要是外資賣超。", "effect": "半導體權值股跌最多。",
                          "owner_effect": "台積電跟著大盤走弱。", "conditions": ["外資是否連續賣超"],
                          "prepare": ["如果加權跌破月線，就先檢查持股比重"]}, ""
        reply = model_reply(payload.get("items", []), payload.get("watch", []))
        return "ok", {kind: reply[kind]}, ""


def run(d1, *, ask=None, friend_keys=(), friend_ask=None, overrides=None, now=NOW, slot="close"):
    get, calls = web(overrides)
    ask = ask or FakeAsk()
    report = mn.run(slot, trade_date="2026-11-19", now=now, query=d1.query, execute=d1.execute, get=get,
                    sleep=lambda s: None, ask_owner=ask, owner_email=OWNER, friend_keys=list(friend_keys),
                    ask_friend=friend_ask or (lambda key_row, prompt, schema: (_ for _ in ()).throw(AssertionError("no friend"))),
                    taiex=lambda: {"name": "加權", "change_pct": -1.82, "date": "2026-11-19"})
    return report, calls, ask


class FetchTests(unittest.TestCase):
    def test_every_feed_once_and_empty_feeds_count_as_failures(self):
        get, calls = web()
        items, sources = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual(len(calls), len(mn.FEEDS))
        self.assertEqual(sources["cna-finance"], "ok")
        self.assertEqual(sources["ft-markets"], "empty", "回 200 但 0 則也算失敗")
        self.assertNotIn("Old story", [row["title"] for row in items], "超過一天的不送")
        tw = next(row for row in items if row["source_key"] == "gnews-tw")
        self.assertEqual((tw["title"], tw["source"], tw["group_hint"]), ("Taiwan stocks fall as TSMC drops", "路透", "tw"))

    def test_one_broken_feed_does_not_stop_the_rest(self):
        get, _ = web({"wsj-markets": Response(status=500), "cna-finance": Response(content=b"<html>x</html>")})
        items, sources = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual(sources["wsj-markets"], "HTTP 500")
        self.assertTrue(sources["cna-finance"].startswith("format"))
        self.assertIn("Korea exports rise 9 pct", [row["title"] for row in items])

    def test_wire_update_tags_are_dropped_so_updates_are_one_story(self):
        get, _ = web({"yonhap-biz": Response(content=rss(item("(LEAD) Seoul shares end higher", "https://en.yna.co.kr/1"),
                                                          item("(URGENT) Seoul shares end higher", "https://en.yna.co.kr/2")))})
        items, _ = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual([row["title"] for row in items if row["source_key"] == "yonhap-biz"], ["Seoul shares end higher"])

    def test_selection_is_by_importance_not_by_time(self):
        # 2026-11-19：中央社 19:03「金管會提3理由擴大限空」被「每家只取最新 6 則」丟掉。現在照重要性選。
        old_but_big = item("金管會提3理由擴大限空 1028檔股票禁平盤下放空", "https://www.cna.com.tw/news/afe/limit.aspx",
                           when="Wed, 18 Nov 2026 11:03:00 GMT")
        filler = [item(f"某公司歡慶週年活動 {n}", f"https://www.cna.com.tw/news/afe/f{n}.aspx", when=f"Thu, 19 Nov 2026 05:{n:02d}:00 GMT")
                  for n in range(25)]
        get, _ = web({"cna-finance": Response(content=rss(*filler, old_but_big))})
        original = mn.MAX_ITEMS
        try:
            mn.MAX_ITEMS = 6
            items, _ = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        finally:
            mn.MAX_ITEMS = original
        titles = [row["title"] for row in items]
        self.assertEqual(len(items), 6)
        self.assertIn("金管會提3理由擴大限空 1028檔股票禁平盤下放空", titles)
        self.assertEqual(titles[0], "金管會提3理由擴大限空 1028檔股票禁平盤下放空", "台股組排最前面、組內分數高的先")
        self.assertIn("Korea exports rise 9 pct", titles, "各組都有名額")

    def test_policy_earnings_and_deals_outrank_routine_company_news(self):
        score = lambda title, key="cna-finance": mn.priority({"title": title, "summary": "", "source_key": key})  # noqa: E731
        self.assertGreater(score("金管會加碼限空"), score("台積電第3季財報優於預期"))
        self.assertGreater(score("台積電第3季財報優於預期"), score("某公司歡慶 30 週年"))
        self.assertGreater(score("Fed signals rate cut", "bloomberg-markets"), score("A profile of a founder", "bloomberg-markets"))
        self.assertGreater(score("金管會新聞稿", "fsc-news"), score("金管會新聞稿", "cna-finance"))

    def test_the_regulator_search_is_part_of_the_source_list_and_fsc_waits_for_the_owner(self):
        regulators = next(feed for feed in mn.FEEDS if feed["key"] == "gnews-regulators")
        self.assertIn("hl=zh-TW", regulators["url"])
        for word in ("%E9%87%91%E7%AE%A1%E6%9C%83", "cna.com.tw"):   # 金管會
            self.assertIn(word, regulators["url"])
        # 本人 2026-11-20「要加（推薦）」：金管會新聞稿 RSS
        fsc = next(feed for feed in mn.FEEDS if feed["key"] == "fsc-news")
        self.assertTrue(fsc["url"].startswith("https://www.fsc.gov.tw/RSS/Messages"))

    def test_fsc_press_releases_only_carry_a_date_and_their_links_are_unescaped(self):
        fsc_item = ("<item><title><![CDATA[金管會宣布當日收盤價跌幅達3.5%以上，次一交易日不得低於前一交易日收盤價放空]]></title>"
                    "<pubDate>Wed, 18 Nov 2026 00:00:00 GMT</pubDate>"
                    "<link><![CDATA[https://www.fsc.gov.tw/ch/home.jsp?id=96&amp;dataserno=202611180003]]></link>"
                    "<description><![CDATA[<div>為維持證券市場之秩序</div>]]></description></item>")
        get, _ = web({"fsc-news": Response(content=rss(fsc_item))})
        items, sources = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        row = next(row for row in items if row["source_key"] == "fsc-news")
        self.assertEqual(sources["fsc-news"], "ok")
        self.assertEqual(row["link"], "https://www.fsc.gov.tw/ch/home.jsp?id=96&dataserno=202611180003")
        self.assertEqual((row["source"], row["group_hint"]), ("金管會", "tw"))
        self.assertEqual(items[0]["source_key"], "fsc-news", "官方的政策公告排最前面；前一天公布的（只有日期）也在兩天的範圍內")

    def test_fsc_failing_does_not_stop_the_round(self):
        get, _ = web({"fsc-news": Response(status=500)})
        items, sources = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual(sources["fsc-news"], "HTTP 500")
        self.assertTrue(items)
    def test_google_slow_down_skips_the_other_google_queries(self):
        get, calls = web({"gnews-tw": Response(status=429)})
        _, sources = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        google = [feed["key"] for feed in mn.FEEDS if feed["kind"] == "gnews"]
        self.assertEqual(sources["gnews-tw"], "HTTP 429")
        for key in google[1:]:
            self.assertEqual(sources[key], "slowed")

    def test_the_source_list_is_the_one_the_owner_approved(self):
        labels = {feed["label"] for feed in mn.FEEDS}
        for label in ("中央通訊社", "路透", "華爾街日報", "Bloomberg", "金融時報", "日經中文網", "韓聯社", "Korea Herald",
                      "南華早報", "香港電台", "BBC"):
            self.assertTrue(any(label in name for name in labels), label)
        self.assertFalse(any("經濟日報 產業" in name or "鉅亨" in name or "工商" in name for name in labels))


class RunTests(unittest.TestCase):
    def test_one_round_writes_the_run_items_and_owner_watchlist(self):
        d1 = D1()
        report, _, ask = run(d1)
        self.assertEqual(sorted(ask.kinds), ["items", "summary", "watch"], "四則一批＋自選一批＋總結一次")
        self.assertEqual(report["status"], "ok")
        run_row = d1.query("SELECT * FROM market_news_runs")[0]
        self.assertEqual((run_row["run_id"], run_row["slot"], run_row["status"]), ("2026-11-19-close", "close", "ok"))
        self.assertEqual(run_row["summary"], "台股今天下跌，主要是外資賣超。")
        indices = json.loads(run_row["indices"])
        self.assertEqual(indices[0], {"name": "加權", "change_pct": -1.82, "date": "2026-11-19"})
        self.assertIn({"name": "費城半導體", "change_pct": -3.1, "date": "2026-11-18"}, indices)
        items = [tuple(r.values()) for r in d1.query("SELECT grp, kind, tone, impact, title, source, translated FROM market_news_items")]
        self.assertIn(("tw", "flows", "down", 3, "中文：外資賣超台股逾 600 億元", "中央通訊社", 0), items)
        self.assertIn(("asia", "other", "up", 2, "中文：Korea exports rise 9", "韓聯社", 1), items)
        marks = d1.query("SELECT code, relevant, kind, tone, title, judged_by FROM stock_news_marks")
        self.assertEqual(marks, [{"code": "2330", "relevant": 1, "kind": "rating", "tone": "down",
                                  "title": "自選：TSMC ADR falls 4%", "judged_by": "owner"}])
        self.assertEqual((report["owner_watch_sent"], report["owner_watch"]), (1, 1))

    def test_stock_news_is_judged_once_and_only_new_rows_go_to_the_model(self):
        d1 = D1()
        run(d1)
        _, _, again = run(d1)
        self.assertNotIn("watch", again.kinds, "判過的不再送")
        d1.db.execute("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
                      "('2330', 'https://news.google.com/rss/articles/N2', 'gnews-cnyes', '台積電10月營收創高', '', '2026-11-01T02:00:00Z', '2026-11-19T06:00:00Z')")
        _, _, third = run(d1)
        payload = json.loads(third.prompts[third.kinds.index("watch")].split("INPUT:")[1])
        self.assertEqual([row["title"] for row in payload["watch"]], ["台積電10月營收創高"], "18 天前的也判（回補 30 天）")
        self.assertEqual(d1.query("SELECT kind FROM stock_news_marks WHERE link LIKE '%N2'"), [{"kind": "earnings"}])
    def test_the_same_story_from_two_sources_is_sent_once(self):
        d1 = D1()
        d1.db.execute("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
                      "('2330', 'https://news.google.com/rss/articles/T0', 'gnews-cna', 'TSMC ADR falls 4%', '', '2026-11-19T02:05:00Z', '2026-11-19T06:00:00Z')")
        rows = mn.pending_stock_rows(d1.query, {"2330": "台積電"}, NOW, limit=10)
        self.assertEqual([row["title"] for row in rows], ["TSMC ADR falls 4%"])
        self.assertEqual(len(rows[0]["links"]), 2)
        run(d1)
        marks = d1.query("SELECT relevant FROM stock_news_marks WHERE code = '2330' ORDER BY relevant DESC")
        self.assertEqual(marks, [{"relevant": 1}, {"relevant": 0}], "第二個連結記成不顯示，下一輪不再送")
    def test_prompts_carry_items_watchlist_and_a_summary_of_marked_news(self):
        d1 = D1()
        _, _, ask = run(d1)
        payloads = {kind: json.loads(text[text.index("INPUT:") + 6:]) for kind, text in zip(ask.kinds, ask.prompts)}
        self.assertTrue(all("link" not in row for row in payloads["items"]["items"]), "連結不給模型")
        self.assertEqual([row["code"] for row in payloads["watch"]["watch"]], ["2330"])
        self.assertEqual(payloads["summary"]["slot"], "close")
        self.assertEqual(payloads["summary"]["indices"][0]["name"], "加權")
        self.assertTrue(all("tone" in row for row in payloads["summary"]["news"]), "總結拿的是判讀過的")

    def test_a_failed_batch_only_loses_its_own_marks(self):
        class FirstBatchFails(FakeAsk):
            def __call__(self, prompt, schema):
                if kind_of(schema) == "items" and '"id": "n1"' in prompt:
                    self.kinds.append("items")
                    return "no_structured_output", None, "keys=['status'] text=''"
                return super().__call__(prompt, schema)
        original = mn.BATCH_ITEMS
        try:
            mn.BATCH_ITEMS = 2
            d1 = D1()
            report, _, _ = run(d1, ask=FirstBatchFails())
        finally:
            mn.BATCH_ITEMS = original
        self.assertEqual(report["run_status"], "ok")
        self.assertEqual(report["diagnosis"]["calls"]["items1"]["status"], "no_structured_output")
        self.assertEqual(report["diagnosis"]["calls"]["items2"]["kept"], 2)
        rows = d1.query("SELECT title, reason FROM market_news_items")
        self.assertEqual(len(rows), 4, "第一批的兩則照原標題存")
        self.assertEqual(sum(row["reason"] == "" for row in rows), 2)

    def test_unknown_ids_bad_values_and_skipped_items_are_dropped(self):
        d1 = D1()

        class Bad(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "items" in output:
                    output["items"][0]["tone"] = "maybe"
                    output["items"][1]["group"] = "skip"
                    output["items"].append({"id": "n999", "group": "tw", "market": "", "tone": "up", "impact": 1,
                                            "title": "編的", "reason": "編的"})
                return status, output, note
        report, _, _ = run(d1, ask=Bad())
        titles = [row["title"] for row in d1.query("SELECT title FROM market_news_items")]
        self.assertNotIn("編的", titles)
        self.assertEqual(len(titles), 2, "送出四則（台股兩、美股一、韓一；超過一天的不送），壞掉的兩則不存")
        self.assertEqual(report["diagnosis"]["rejected"], {"bad_values": 1, "skip": 1, "unknown_id": 1})

    def test_marks_are_applied_even_without_a_summary_and_from_a_json_string(self):
        # 2026-11-19 正式跑（agy 1.5.3）：model ok 卻整輪「只有標題」——總結沒有就整輪不套，現在改成有判讀就套。
        class NoSummary(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "market_commentary" in output:
                    output["market_commentary"] = ""
                return status, json.dumps(output, ensure_ascii=False), note
        d1 = D1()
        report, _, _ = run(d1, ask=NoSummary())
        self.assertEqual(report["run_status"], "ok")
        self.assertEqual(report["diagnosis"]["summary_chars"], 0)
        run_row = d1.query("SELECT status, summary FROM market_news_runs")[0]
        self.assertEqual((run_row["status"], run_row["summary"]), ("ok", None))
        self.assertTrue(d1.query("SELECT tone FROM market_news_items WHERE tone <> 'flat'"))

    def test_a_summary_that_talks_about_the_task_instead_of_the_market_is_dropped(self):
        # 2026-11-19 本機真 Flash：總結回成「已成功依照指令產生台股收盤新聞摘要 JSON…」——這不是總結，問第二次；還是這樣就不放。
        class Meta(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "market_commentary" in output:
                    output["market_commentary"] = "已成功依照指令產生台股收盤新聞摘要 JSON，並嚴格遵循字數規則。"
                return status, output, note
        d1 = D1()
        report, _, ask = run(d1, ask=Meta())
        self.assertEqual(ask.kinds.count("summary"), 2, "問第二次")
        self.assertEqual(report["run_status"], "ok")
        self.assertIsNone(d1.query("SELECT summary FROM market_news_runs")[0]["summary"])
        self.assertEqual(report["diagnosis"]["calls"]["summary"]["status"], "not_a_summary")
        self.assertTrue(mn.is_summary("台股今天下跌，主要是外資賣超。"))
        self.assertFalse(mn.is_summary("以下是摘要：台股下跌"))

    def test_rejected_items_are_counted_by_reason(self):
        class Bad(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "items" in output:
                    output["items"][0]["id"] = "n999"
                    output["items"][1]["impact"] = None
                return status, output, note
        report, _, _ = run(D1(), ask=Bad())
        self.assertEqual(report["diagnosis"]["rejected"], {"unknown_id": 1, "bad_fields:impact": 1})

    def test_when_the_model_fails_titles_are_kept_without_marks(self):
        d1 = D1()
        report, _, _ = run(d1, ask=FakeAsk(status="model_failed"))
        run_row = d1.query("SELECT status, summary FROM market_news_runs")[0]
        self.assertEqual((run_row["status"], run_row["summary"]), ("unclassified", None))
        self.assertEqual(report["model"], "model_failed")
        items = d1.query("SELECT tone, impact, reason FROM market_news_items")
        self.assertTrue(items)
        self.assertTrue(all(row == {"tone": "flat", "impact": 1, "reason": ""} for row in items))
        self.assertEqual(d1.query("SELECT * FROM market_news_user_items"), [])

    def test_friends_with_a_key_get_their_own_watchlist_marked_with_their_key(self):
        d1 = D1()
        seen = []

        def friend_ask(key_row, prompt, schema):
            seen.append((key_row["email"], prompt))
            payload = json.loads(prompt[prompt.index("INPUT:") + 6:])
            return "ok", {"watch": [{"id": row["id"], "relevant": "yes", "kind": "deal", "tone": "up", "impact": 1,
                                     "title": "朋友：" + row["title"], "reason": "x"} for row in payload["watch"]]}, ""
        run(d1, friend_keys=[{"email": FRIEND, "alg": "AES-256-GCM"}], friend_ask=friend_ask)
        self.assertEqual([email for email, _ in seen], [FRIEND])
        self.assertNotIn("TSMC", seen[0][1], "朋友的金鑰只判他自選裡還沒人判過的")
        rows = d1.query("SELECT code, tone, judged_by FROM stock_news_marks ORDER BY code")
        self.assertEqual(rows, [{"code": "2330", "tone": "down", "judged_by": "owner"}, {"code": "2454", "tone": "up", "judged_by": "friend"}])

    def test_a_friend_does_not_pay_for_news_the_owner_already_judged(self):
        d1 = D1()
        d1.db.execute("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at, watching) "
                      "VALUES (?, '2330', '台積電', '上市', 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)", (FRIEND,))
        d1.db.execute("DELETE FROM watch_news WHERE code = '2454'")
        report, _, _ = run(d1, friend_keys=[{"email": FRIEND}])   # friend_ask 預設會丟錯：不該被叫到
        self.assertEqual(report["friends"], {FRIEND: "nothing_new"})
    def test_a_friend_key_failure_only_skips_that_friend(self):
        d1 = D1()
        report, _, _ = run(d1, friend_keys=[{"email": FRIEND}],
                           friend_ask=lambda key_row, prompt, schema: ("quota", None, "429"))
        self.assertEqual(report["friends"], {FRIEND: "0/1 quota 429"})
        self.assertEqual(d1.query("SELECT code, judged_by FROM stock_news_marks"), [{"code": "2330", "judged_by": "owner"}])
    def test_rerunning_a_slot_replaces_it_and_old_runs_are_removed(self):
        d1 = D1()
        run(d1)
        run(d1)
        self.assertEqual(len(d1.query("SELECT run_id FROM market_news_runs")), 1)
        d1.db.execute("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status) "
                      "VALUES ('2026-10-01-close', '2026-10-01', 'close', '2026-10-01T06:00:00Z', 'ok')")
        d1.db.execute("INSERT INTO market_news_user_items VALUES ('a@b.c', '2026-10-01-close', '2330', 'l', 'up', 1, 't', 0, 'r')")
        run(d1)
        self.assertEqual([r["run_id"] for r in d1.query("SELECT run_id FROM market_news_runs")], ["2026-11-19-close"])
        self.assertEqual(d1.query("SELECT * FROM market_news_user_items WHERE run_id = '2026-10-01-close'"), [])

    def test_quotes_in_titles_are_stored_as_written(self):
        d1 = D1()
        run(d1, overrides={"cna-finance": Response(content=rss(item("外資 'O'Neil'; DROP TABLE x; --", "https://www.cna.com.tw/a")))})
        titles = [r["title"] for r in d1.query("SELECT title FROM market_news_items")]
        self.assertTrue(any(title.startswith("中文：外資 'O'Neil';") for title in titles), titles)


class Reply(FakeAsk):
    """假的 Flash，回答先經過 change(kind, output, 第幾次問這一種) 改寫。"""

    def __init__(self, change):
        super().__init__()
        self.change, self.asked = change, {}

    def __call__(self, prompt, schema):
        status, output, note = super().__call__(prompt, schema)
        kind = kind_of(schema)
        self.asked[kind] = self.asked.get(kind, 0) + 1
        self.change(kind, output, self.asked[kind])
        return status, output, note


class ImpactTests(unittest.TestCase):
    """本人 2026-11-20：消息要講「影響＋要看的條件＋準備」（.scratch/impact-watch/spec.md、票 01）。"""

    def test_every_item_and_the_summary_are_stored_in_four_parts(self):
        d1 = D1()
        report, _, _ = run(d1)
        row = d1.query("SELECT reason, effect, owner_effect, conditions, prepare FROM market_news_items WHERE title LIKE '%外資%'")[0]
        self.assertEqual(row, {"reason": "一句理由", "effect": "權值股壓低指數", "owner_effect": "台積電是外資賣超的重心",
                               "conditions": '["外資是否連續賣超"]', "prepare": '["如果外資連續賣超，就先檢查權值股佔多少"]'})
        self.assertEqual(len(d1.query("SELECT 1 FROM market_news_items WHERE effect IS NOT NULL AND conditions IS NOT NULL AND prepare IS NOT NULL")), 4)
        run_row = d1.query("SELECT summary, effect, owner_effect, conditions, prepare FROM market_news_runs")[0]
        self.assertEqual(run_row, {"summary": "台股今天下跌，主要是外資賣超。", "effect": "半導體權值股跌最多。",
                                   "owner_effect": "台積電跟著大盤走弱。", "conditions": '["外資是否連續賣超"]',
                                   "prepare": '["如果加權跌破月線，就先檢查持股比重"]'})
        mark = d1.query("SELECT effect, conditions, prepare FROM stock_news_marks")[0]
        self.assertEqual(mark, {"effect": "短線股價承壓", "conditions": '["費半是否止跌"]', "prepare": '["如果費半續跌，就先想好能承受的跌幅"]'})
        self.assertEqual(report["diagnosis"]["points"], {"columns": True, "effect": 5, "owner_effect": 1, "conditions": 5, "prepare": 5,
                                                         "summary": ["conditions", "effect", "owner_effect", "prepare"], "text_rejected": {}})

    def test_the_model_is_told_which_stocks_are_the_owners_watchlist_and_holdings(self):
        d1 = D1()
        d1.db.execute("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) "
                      "VALUES (?, '2317', '鴻海', '上市', 1000, 200, 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')", (OWNER,))
        _, _, ask = run(d1)
        payloads = {kind: json.loads(text[text.index("INPUT:") + 6:]) for kind, text in zip(ask.kinds, ask.prompts)}
        mine = [{"code": "2317", "name": "鴻海"}, {"code": "2330", "name": "台積電"}]
        self.assertEqual(payloads["items"]["my_stocks"], mine)
        self.assertEqual(payloads["summary"]["my_stocks"], mine)
        self.assertNotIn("聯發科", json.dumps(payloads, ensure_ascii=False), "朋友的自選不給")
        self.assertNotIn("my_stocks", payloads["watch"], "個股判讀是大家共用的，不帶站主的股票")

    def test_a_line_that_names_none_of_the_owners_stocks_is_not_kept_as_theirs(self):
        def change(kind, output, _n):
            for row in output.get("items", []):
                row["owner_effect"] = "電子股普遍受影響"
        d1 = D1()
        run(d1, ask=Reply(change))
        self.assertEqual(d1.query("SELECT count(*) AS n FROM market_news_items WHERE owner_effect IS NOT NULL")[0]["n"], 0)

    def test_missing_extra_and_overlong_fields_do_not_void_the_item(self):
        def change(kind, output, _n):
            rows = output.get("items", [])
            if rows:
                for key in ("effect", "owner_effect", "conditions", "prepare"):
                    rows[0].pop(key)                      # 少寫：照舊格式存
                rows[1]["confidence"] = "high"             # 多寫不認得的欄位
                rows[1]["conditions"] = ["甲是否發生", "乙是否發生", "丙是否發生"]   # 多寫一個
                rows[2]["conditions"] = "外資是否回補"      # 該給陣列卻給一句
                rows[2]["prepare"] = None
        d1 = D1()
        report, _, _ = run(d1, ask=Reply(change))
        rows = d1.query("SELECT effect, conditions, prepare FROM market_news_items ORDER BY item_id")
        self.assertEqual(len(rows), 4, "四則都在")
        self.assertEqual(rows[0], {"effect": None, "conditions": None, "prepare": None})
        self.assertEqual(json.loads(rows[1]["conditions"]), ["甲是否發生", "乙是否發生"])
        self.assertEqual((rows[2]["conditions"], rows[2]["prepare"]), ('["外資是否回補"]', None))
        self.assertEqual(report["diagnosis"]["rejected"], {})

    def test_a_trade_order_is_sent_back_with_the_reason_and_the_sentence(self):
        def change(kind, output, n):
            if kind == "items" and n == 1:
                output["items"][0]["prepare"] = ["如果跌破月線，就先減碼台積電"]
        d1 = D1()
        ask = Reply(change)
        report, _, _ = run(d1, ask=ask)
        again = [text for kind, text in zip(ask.kinds, ask.prompts) if kind == "items"]
        self.assertEqual(len(again), 2, "被退回就重問一次")
        self.assertNotIn("上一次的回答被退回", again[0])
        for part in ("上一次的回答被退回", "不能寫買賣指令或投資建議", "如果跌破月線，就先減碼台積電", "準備"):
            self.assertIn(part, again[1])
        self.assertTrue(again[1].startswith(again[0]), "規則與輸入不變，只是接上退回的原因")
        self.assertEqual(report["diagnosis"]["calls"]["items1"]["text_retry"], "prepare：不能寫買賣指令或投資建議")
        self.assertEqual(report["diagnosis"]["points"]["text_rejected"], {}, "第二次寫對了")
        self.assertFalse(d1.query("SELECT 1 FROM market_news_items WHERE prepare LIKE '%減碼%'"))
        self.assertEqual(d1.query("SELECT count(*) AS n FROM market_news_items WHERE prepare IS NOT NULL")[0]["n"], 4)

    def test_a_sentence_rejected_twice_is_dropped_but_the_item_stays(self):
        def change(kind, output, _n):
            for row in output.get("items", [])[:1]:
                row["prepare"] = ["先把持股比重看一遍", "如果外資回補，就留意權值股"]   # 第一句不是「如果…就…」
                row["effect"] = "建議逢低買進權值股"
            for row in output.get("watch", []):
                row["prepare"] = ["如果續跌就停損出場"]
        d1 = D1()
        report, _, _ = run(d1, ask=Reply(change))
        row = d1.query("SELECT title, effect, prepare FROM market_news_items ORDER BY item_id LIMIT 1")[0]
        self.assertEqual((row["effect"], row["prepare"]), (None, '["如果外資回補，就留意權值股"]'))
        self.assertEqual(len(d1.query("SELECT 1 FROM market_news_items")), 4)
        self.assertEqual(d1.query("SELECT effect, prepare FROM stock_news_marks")[0], {"effect": "短線股價承壓", "prepare": None})
        self.assertEqual(report["diagnosis"]["points"]["text_rejected"], {"effect": 1, "prepare": 2})
        self.assertEqual(report["diagnosis"]["calls"]["watch1"]["attempts"], 2)

    def test_news_wording_about_others_trading_is_not_a_trade_order(self):
        for text in ("外資賣出台積電逾百億", "投信逢低布局權值股", "晶片股遭獲利了結", "降息不宜過快，外資賣出美債"):
            self.assertIsNone(mn.text_problem("effect", text), text)
        for text in ("建議買進", "可以逢低布局", "應該先減碼", "不妨加碼"):
            self.assertIsNotNone(mn.text_problem("effect", text), text)
        self.assertIsNotNone(mn.text_problem("prepare", "如果跌破月線就賣出"))
        self.assertEqual(mn.text_problem("prepare", "先檢查持股"), "準備要用「如果…就…」寫")
        self.assertIsNone(mn.text_problem("prepare", "如果外資連續賣超，就先檢查權值股佔多少"))

    def test_a_summary_with_a_trade_order_is_asked_again_and_the_reply_is_used(self):
        def change(kind, output, n):
            if kind == "summary" and n == 1:
                output["market_commentary"] = "台股下跌，建議逢低買進。"
        d1 = D1()
        ask = Reply(change)
        report, _, _ = run(d1, ask=ask)
        self.assertEqual(ask.kinds.count("summary"), 2)
        self.assertIn("被退回的那一句（總結）：「台股下跌，建議逢低買進。」", ask.prompts[-1])
        self.assertEqual(d1.query("SELECT summary FROM market_news_runs")[0]["summary"], "台股今天下跌，主要是外資賣超。")
        self.assertEqual(report["diagnosis"]["calls"]["summary"]["status"], "ok")

    def test_when_the_retry_itself_fails_the_first_answer_is_used(self):
        class SecondFails(Reply):
            def __call__(self, prompt, schema):
                if kind_of(schema) == "items" and "上一次的回答被退回" in prompt:
                    self.kinds.append("items")
                    return "model_timeout", None, "逾時"
                return super().__call__(prompt, schema)

        def change(kind, output, _n):
            for row in output.get("items", [])[:1]:
                row["prepare"] = ["如果跌破月線就賣出"]
        d1 = D1()
        report, _, _ = run(d1, ask=SecondFails(change))
        self.assertEqual(report["diagnosis"]["calls"]["items1"]["status"], "ok")
        self.assertEqual(d1.query("SELECT count(*) AS n FROM market_news_items WHERE effect IS NOT NULL")[0]["n"], 4)
        self.assertEqual(d1.query("SELECT count(*) AS n FROM market_news_items WHERE prepare IS NOT NULL")[0]["n"], 3)

    def test_before_the_migration_the_round_is_written_the_old_way(self):
        d1 = D1(OLD_SCHEMA)
        report, _, _ = run(d1)
        self.assertEqual(report["run_status"], "ok")
        self.assertFalse(report["diagnosis"]["points"]["columns"])
        self.assertEqual(len(d1.query("SELECT 1 FROM market_news_items")), 4)
        self.assertEqual(d1.query("SELECT summary FROM market_news_runs")[0]["summary"], "台股今天下跌，主要是外資賣超。")
        self.assertNotIn("effect", d1.scripts[-1])

    def test_rules_ask_for_the_four_parts_and_forbid_trade_orders(self):
        for rules in (mn.ITEM_RULES, mn.WATCH_RULES, mn.SUMMARY_RULES):
            for word in ("effect", "conditions", "prepare", "如果…就…", "都不能寫買賣指令或個人化投資建議"):
                self.assertIn(word, rules)
        for rules in (mn.ITEM_RULES, mn.SUMMARY_RULES):
            self.assertIn("my_stocks", rules)
        self.assertNotIn("owner_effect", mn.WATCH_RULES)
        self.assertIn('impact 是 "1" 的消息可以只寫 effect', mn.ITEM_RULES)


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


class SchemaTests(unittest.TestCase):
    """2026-11-19 乾跑（run 44509863326）：Gemini 拒收「impact 的 enum 是整數」「market 的 enum 有空字串」→ INVALID_ARGUMENT。
    agy 把結構描述轉成 function declaration：enum 只能是非空字串。"""

    def test_every_enum_is_a_list_of_non_empty_strings(self):
        for name, schema in (("ITEMS_SCHEMA", mn.ITEMS_SCHEMA), ("WATCH_SCHEMA", mn.WATCH_SCHEMA), ("SUMMARY_SCHEMA", mn.SUMMARY_SCHEMA)):
            for path, node in _walk(schema):
                if "enum" in node:
                    with self.subTest(schema=name, path=path):
                        self.assertEqual(node.get("type"), "string")
                        self.assertTrue(all(isinstance(v, str) and v for v in node["enum"]), node["enum"])

    def test_impact_and_market_in_the_wire_format_are_read_back(self):
        items = [{"id": "n1", "title": "Korea exports rise", "source": "韓聯社", "link": "https://x", "published_at": "2026-11-19T01:00:00Z",
                  "market_hint": "韓", "group_hint": "asia"},
                 {"id": "n2", "title": "台股下跌", "source": "中央通訊社", "link": "https://y", "published_at": "2026-11-19T02:00:00Z",
                  "market_hint": "", "group_hint": "tw"}]
        output = {"items": [{"id": "n1", "group": "asia", "market": "韓", "tone": "up", "impact": "2", "title": "韓國出口成長", "reason": "x"},
                            {"id": "n2", "group": "tw", "market": "none", "tone": "down", "impact": "3", "title": "台股下跌", "reason": "y"}]}
        rows = {row["id"]: row for row in mn.validate_items(output, items)}
        self.assertEqual((rows["n1"]["impact"], rows["n1"]["market"]), (2, "韓"))
        self.assertEqual((rows["n2"]["impact"], rows["n2"]["market"]), (3, ""))


class Round4Tests(unittest.TestCase):
    """2026-11-19 正式跑 run 44531113573 的四個問題。"""

    def test_the_answer_in_the_response_field_is_used(self):
        # Actions 的 agy 1.5.3：result 事件沒有 structured_output，欄位是 response 等八個
        envelope = {"conversation_id": "dae859cf-1", "denied_actions": [], "duration_seconds": 80, "json_schema": {"type": "object"},
                    "num_turns": 1, "response": '```json\n{"items": [{"id": "n1"}]}\n```', "status": "SUCCESS", "usage": {}}
        status, output, _ = mn.interpret_envelope(envelope)
        self.assertEqual((status, output), ("ok", {"items": [{"id": "n1"}]}))

    def test_an_unusable_response_is_previewed_not_the_conversation_id(self):
        envelope = {"conversation_id": "dae859cf-1", "denied_actions": [{"tool": "web_search"}], "num_turns": 3,
                    "response": "我先查一下最新消息", "status": "SUCCESS", "usage": {}}
        status, _, note = mn.interpret_envelope(envelope)
        self.assertEqual(status, "no_structured_output")
        self.assertIn("我先查一下", note)
        self.assertIn("web_search", note)
        self.assertNotIn("dae859cf", note)

    def test_a_failed_call_is_tried_once_more(self):
        class FailsOnce(FakeAsk):
            failed = False

            def __call__(self, prompt, schema):
                if kind_of(schema) == "items" and not self.failed:
                    self.failed = True
                    self.kinds.append("items")
                    return "no_structured_output", None, "response=''"
                return super().__call__(prompt, schema)
        d1 = D1()
        report, _, ask = run(d1, ask=FailsOnce())
        self.assertEqual(ask.kinds.count("items"), 2)
        self.assertEqual(report["diagnosis"]["calls"]["items1"], {"status": "ok", "attempts": 2, "returned": 4, "kept": 4})

    def test_a_commentary_that_is_itself_json_is_unwrapped(self):
        class Wrapped(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "market_commentary" in output:
                    output["market_commentary"] = '{"summary": "受美國新出口管制影響，台股下跌。"}'
                return status, output, note
        d1 = D1()
        run(d1, ask=Wrapped())
        self.assertEqual(d1.query("SELECT summary FROM market_news_runs")[0]["summary"], "受美國新出口管制影響，台股下跌。")

    def test_watch_news_the_model_calls_unrelated_is_stored_as_unrelated(self):
        d1 = D1()
        d1.db.execute("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
                      "('2330', 'https://news.google.com/rss/articles/R1', 'gnews-cna', '台積電盃馬拉松開跑', '', '2026-11-19T03:00:00Z', '2026-11-19T06:00:00Z')")
        report, _, _ = run(d1)
        rows = d1.query("SELECT title, relevant FROM stock_news_marks ORDER BY relevant DESC")
        self.assertEqual(rows, [{"title": "自選：TSMC ADR falls 4%", "relevant": 1}, {"title": "台積電盃馬拉松開跑", "relevant": 0}])
        self.assertEqual((report["owner_watch"], report["owner_watch_unrelated"]), (1, 1))
        self.assertEqual(report["diagnosis"]["calls"]["watch1"]["unrelated"], 1)
    def test_cna_rows_matched_only_by_their_lead_are_not_watch_news(self):
        d1 = D1()
        d1.db.execute("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
                      "('2330', 'https://www.cna.com.tw/k', 'cna-finance', '新竹市國際風箏節登場', '台積電贊助', '2026-11-19T03:00:00Z', '2026-11-19T06:00:00Z')")
        self.assertEqual([row["title"] for row in mn.pending_stock_rows(d1.query, {"2330": "台積電"}, NOW, limit=10)],
                         ["TSMC ADR falls 4%"])
    def test_friends_are_asked_in_small_batches_with_light_thinking(self):
        d1 = D1()
        for n in range(12):
            d1.db.execute("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
                          "('2454', ?, 'gnews-reuters', ?, '', '2026-11-19T02:00:00Z', '2026-11-19T06:00:00Z')",
                          (f"https://news.google.com/rss/articles/F{n}", f"MediaTek story {n}"))
        sizes = []

        def friend_ask(key_row, prompt, schema):
            payload = json.loads(prompt[prompt.index("INPUT:") + 6:])
            sizes.append(len(payload["watch"]))
            if len(sizes) == 1:
                return "gemini_unavailable", None, '{"error": "GeminiTimeout"}'
            return "ok", {"watch": [{"id": row["id"], "relevant": "yes", "kind": "other", "tone": "up", "impact": "1",
                                     "title": "朋友", "reason": "x"} for row in payload["watch"]]}, ""
        report, _, _ = run(d1, friend_keys=[{"email": FRIEND}], friend_ask=friend_ask)
        self.assertEqual(sizes, [mn.FRIEND_BATCH, 13 - mn.FRIEND_BATCH])
        self.assertIn("1/2", report["friends"][FRIEND])
        self.assertEqual(len(d1.query("SELECT 1 FROM stock_news_marks WHERE judged_by = 'friend'")), 13 - mn.FRIEND_BATCH)
        body = mn.friend_body("x", mn.WATCH_SCHEMA)
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], mn.FRIEND_NEWS_EFFORT)
        self.assertEqual(mn.FRIEND_NEWS_EFFORT, "low")
    def test_rules_use_taiwan_market_words_and_name_the_key_events(self):
        for rules in (mn.ITEM_RULES, mn.WATCH_RULES, mn.SUMMARY_RULES):
            for word in ("限空令", "融券", "法說會", "三大法人"):
                self.assertIn(word, rules)
        self.assertIn("點名", mn.SUMMARY_RULES)
        self.assertIn("同一件事", mn.ITEM_RULES)
        self.assertIn("中央通訊社", mn.ITEM_RULES)

    def test_groups_are_ordered_by_impact_then_kind(self):
        rows = [{"group": "tw", "impact": 2, "kind": "other", "published_at": "2026-11-19T05:00:00Z", "title": "a"},
                {"group": "tw", "impact": 2, "kind": "policy", "published_at": "2026-11-19T01:00:00Z", "title": "b"},
                {"group": "tw", "impact": 3, "kind": "other", "published_at": "2026-11-19T00:00:00Z", "title": "c"}]
        self.assertEqual([row["title"] for row in mn._keep_per_group(rows)], ["c", "b", "a"])

    def test_only_clearly_unrelated_news_is_skipped(self):
        self.assertIn("看不出影響", mn.ITEM_RULES)
        self.assertIn("flat", mn.ITEM_RULES.split("看不出影響")[1][:40], "看不出影響的標中性，不是丟掉")
        self.assertIn("不要使用任何工具", mn.ITEM_RULES)


class Round5Tests(unittest.TestCase):
    """2026-11-19 正式跑 run 44535530232 之後的三個小問題。"""

    def test_a_long_commentary_ends_at_a_full_stop(self):
        text = "台股今天下跌，主要是外資賣超。" * 30
        cut_text = mn.whole_sentences(text, mn.SUMMARY_MAX)
        self.assertLessEqual(len(cut_text), mn.SUMMARY_MAX)
        self.assertTrue(cut_text.endswith("。"))
        self.assertEqual(mn.whole_sentences("短短一句。", 300), "短短一句。")
        self.assertGreaterEqual(mn.SUMMARY_MAX, 300)

        class Long(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                if "market_commentary" in output:
                    output["market_commentary"] = "台股今天下跌，主要是外資賣超與輝達調" * 40
                return status, output, note
        d1 = D1()
        run(d1, ask=Long())
        self.assertTrue(d1.query("SELECT summary FROM market_news_runs")[0]["summary"].endswith("，主要是外資賣超與輝達調") is False)



class FriendCallTests(unittest.TestCase):
    """朋友的金鑰走 Gemini API。2026-11-19 正式跑：gemini-3.8-flash 一秒內回 503（帶不帶結構描述都一樣）；
    這把金鑰唯一成功過的呼叫是存金鑰時的試呼叫（gemini-flash-latest）。所以 5xx／逾時就退到那個模型再試。"""

    def setUp(self):
        import friend_ai_digest as fad
        self.fad = fad
        self.saved = fad.node_decrypt, mn._friend_post
        fad.node_decrypt = lambda row: "k"
        self.calls = []

    def tearDown(self):
        self.fad.node_decrypt, mn._friend_post = self.saved

    def reply(self, text):
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def ask(self):
        return mn._friend_asker("gemini-3.8-flash")({"email": FRIEND}, "x", mn.WATCH_SCHEMA)

    def test_the_fallback_model_is_the_one_the_key_was_tried_with(self):
        source = (ROOT / "cloud/lib/friend-ai.js").read_text(encoding="utf-8")
        self.assertIn(f"/models/{mn.FRIEND_FALLBACK_MODEL}:generateContent", source)

    def test_503_falls_back_to_the_trial_model_and_says_which(self):
        def post(url, body, key, timeout):
            self.calls.append((url, body))
            if "gemini-3.8-flash" in url:
                raise mn.FriendHttpError(503, "UNAVAILABLE", (), "The model is overloaded. Please try again later.")
            return self.reply('{"watch": []}')
        mn._friend_post = post
        status, output, note = self.ask()
        self.assertEqual((status, output), ("ok", {"watch": []}))
        self.assertEqual(len(self.calls), 2)
        self.assertIn(mn.FRIEND_FALLBACK_MODEL, self.calls[1][0])
        self.assertNotIn("thinkingConfig", self.calls[1][1]["generationConfig"], "備援模型不指定思考程度")
        self.assertIn("HTTP 503 UNAVAILABLE", note)
        self.assertIn("The model is overloaded", note)
        self.assertRegex(note, rf"{mn.FRIEND_FALLBACK_MODEL} schema \d+s ok")

    def test_everything_failing_reports_each_try_with_the_error_text(self):
        def post(url, body, key, timeout):
            self.calls.append(url)
            if len(self.calls) == 1:
                raise self.fad.GeminiTimeout("TimeoutError")
            raise mn.FriendHttpError(503, "UNAVAILABLE", (), "x" * 500)
        mn._friend_post = post
        status, output, note = self.ask()
        self.assertEqual((status, output, len(self.calls)), ("gemini_unavailable", None, 3))
        self.assertIn("timeout", note)
        self.assertIn("plain", note, "最後一次不帶結構描述")
        self.assertLess(len(note), 900)

    def test_a_key_problem_is_not_retried(self):
        def post(url, body, key, timeout):
            self.calls.append(url)
            raise mn.FriendHttpError(403, "PERMISSION_DENIED", (), "API key was reported as leaked")
        mn._friend_post = post
        status, _, note = self.ask()
        self.assertEqual((status, len(self.calls)), ("key_revoked", 1))
        self.assertIn("HTTP 403", note)

    def test_a_model_this_key_cannot_use_also_falls_back(self):
        def post(url, body, key, timeout):
            self.calls.append(url)
            if "gemini-3.8-flash" in url:
                raise mn.FriendHttpError(404, "NOT_FOUND", (), "models/gemini-3.8-flash is not found")
            return self.reply('```json\n{"watch": []}\n```')
        mn._friend_post = post
        status, _, note = self.ask()
        self.assertEqual((status, len(self.calls)), ("ok", 2))
        self.assertIn("HTTP 404", note)


class EnvelopeTests(unittest.TestCase):
    """agy 的 result 事件：Actions 的 1.5.3 回 SUCCESS 但 structured_output 是 None（2026-11-19 run 44527174969）。"""

    def test_structured_output_is_used_when_present(self):
        self.assertEqual(mn.interpret_envelope({"status": "SUCCESS", "structured_output": {"items": []}}), ("ok", {"items": []}, ""))

    def test_json_in_the_text_reply_is_used_when_structured_output_is_missing(self):
        envelope = {"status": "SUCCESS", "structured_output": None, "result": '好的：\n```json\n{"summary": "台股下跌"}\n```'}
        status, output, note = mn.interpret_envelope(envelope)
        self.assertEqual((status, output), ("ok", {"summary": "台股下跌"}))
        self.assertIn("解出", note)

    def test_nothing_usable_names_the_fields_and_the_start_of_the_text(self):
        status, output, note = mn.interpret_envelope({"status": "SUCCESS", "structured_output": None, "result": "抱歉，我無法"})
        self.assertEqual((status, output), ("no_structured_output", None))
        self.assertIn("result", note)
        self.assertIn("抱歉", note)

    def test_failure_is_not_ok(self):
        self.assertEqual(mn.interpret_envelope({"status": "ERROR", "error": "quota"})[0], "model_failed")
        self.assertEqual(mn.interpret_envelope(None)[0], "model_failed")


class WindowTests(unittest.TestCase):
    def test_monday_morning_reaches_back_to_the_last_run_but_not_forever(self):
        d1 = D1()
        d1.db.execute("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status) "
                      "VALUES ('2026-11-13-close', '2026-11-13', 'close', '2026-11-13T06:05:00Z', 'ok')")
        self.assertEqual(mn.window_start(d1.query, "2026-11-16T00:05:00Z"), "2026-11-13T06:05:00Z", "週一盤前：從週五收盤後那一輪起")
        self.assertEqual(mn.window_start(d1.query, "2026-11-13T07:00:00Z"), "2026-11-12T07:00:00Z", "同一天：至少一整天")
        self.assertEqual(mn.window_start(d1.query, "2026-11-30T00:05:00Z"), "2026-11-26T00:05:00Z", "太久沒跑：最多四天")

    def test_a_group_whose_sources_all_failed_is_marked_missed(self):
        sources = {feed["key"]: "ok" for feed in mn.FEEDS}
        for feed in mn.FEEDS:
            if feed["group"] == "us":
                sources[feed["key"]] = "HTTP 500"
        self.assertEqual(mn.missed_groups(sources), ["us"])
        d1 = D1()
        run(d1, overrides={key: Response(status=500) for key in ("wsj-markets", "bloomberg-markets", "ft-markets")})
        stored = json.loads(d1.query("SELECT sources FROM market_news_runs")[0]["sources"])
        self.assertEqual(stored["missed_groups"], ["us"])

    def test_trading_days_follow_the_official_holiday_calendar(self):
        holidays = {"2026-10-09"}
        self.assertTrue(mn.is_trading_day("2026-11-19", holidays))
        self.assertFalse(mn.is_trading_day("2026-10-09", holidays), "國慶日補假")
        self.assertFalse(mn.is_trading_day("2026-11-21", holidays), "週六")


class VersionTests(unittest.TestCase):
    def test_watch_judgments_change_version_but_the_market_forecast_does_not(self):
        import forecast_ledger
        import licensed_inputs
        import watch_ledger
        self.assertNotIn("stock_headlines", licensed_inputs.PARAMETERS, "大盤預測不讀個股新聞，版本不動")
        self.assertNotIn("stock_headlines", json.dumps(forecast_ledger.PARAMETERS, ensure_ascii=False, default=str))
        self.assertEqual(watch_ledger._LAYER_PARAMETERS["headlines"]["stock_headlines"], wn.STOCK_NEWS_PARAMETERS)
        for key, _label in wn.GNEWS_OUTLETS.values():
            self.assertIn(key, licensed_inputs.SOURCE_NAMES)


if __name__ == "__main__":
    unittest.main()


