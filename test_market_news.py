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
SCHEMA = "".join((MIGRATIONS / name).read_text(encoding="utf-8")
                 for name in ("0005_user_portfolio.sql", "0011_watchlist_watch.sql", "0015_watch_news.sql", "0031_market_news.sql"))
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
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
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
                      "tone": tone, "impact": 3 if tone == "down" else 2, "title": "中文：" + row["title"][:20], "reason": "一句理由"})
    return {"summary": "台股今天下跌，主要是外資賣超。", "items": items,
            "watch": [{"id": row["id"], "tone": "down", "impact": 2, "title": "自選：" + row["title"], "reason": "跟著走弱"}
                      for row in watch]}


class FakeAsk:
    def __init__(self, status="ok"):
        self.status, self.prompts = status, []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        payload = json.loads(prompt[prompt.index("INPUT:") + len("INPUT:"):])
        if self.status != "ok":
            return self.status, None, "壞了"
        return "ok", model_reply(payload["items"], payload["watch"]), ""


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

    def test_a_busy_source_cannot_crowd_out_the_others(self):
        many = rss(*(item(f"Bloomberg story {n}", f"https://www.bloomberg.com/{n}", when=f"Thu, 19 Nov 2026 05:{n:02d}:00 GMT")
                     for n in range(40)))
        get, _ = web({key: Response(content=many) for key in ("bloomberg-markets",)})
        original = mn.MAX_ITEMS
        try:
            mn.MAX_ITEMS = 6
            items, _ = mn.collect(get=get, now=NOW, sleep=lambda s: None)
        finally:
            mn.MAX_ITEMS = original
        self.assertEqual(len(items), 6)
        self.assertIn("Korea exports rise 9 pct", [row["title"] for row in items])
        self.assertIn("外資賣超台股逾 600 億元", [row["title"] for row in items])

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
    def test_one_flash_call_writes_the_run_items_and_owner_watchlist(self):
        d1 = D1()
        report, _, ask = run(d1)
        self.assertEqual(len(ask.prompts), 1, "一輪只叫一次")
        self.assertEqual(report["status"], "ok")
        run_row = d1.query("SELECT * FROM market_news_runs")[0]
        self.assertEqual((run_row["run_id"], run_row["slot"], run_row["status"]), ("2026-11-19-close", "close", "ok"))
        self.assertEqual(run_row["summary"], "台股今天下跌，主要是外資賣超。")
        indices = json.loads(run_row["indices"])
        self.assertEqual(indices[0], {"name": "加權", "change_pct": -1.82, "date": "2026-11-19"})
        self.assertIn({"name": "費城半導體", "change_pct": -3.1, "date": "2026-11-18"}, indices)
        items = d1.query("SELECT grp, tone, impact, title, source, translated FROM market_news_items ORDER BY impact DESC, title")
        self.assertIn(("tw", "down", 3, "中文：外資賣超台股逾 600 億元", "中央通訊社", 0), [tuple(r.values()) for r in items])
        self.assertIn(("asia", "up", 2, "中文：Korea exports rise 9", "韓聯社", 1), [tuple(r.values()) for r in items])
        mine = d1.query("SELECT email, code, tone, title FROM market_news_user_items")
        self.assertEqual(mine, [{"email": OWNER, "code": "2330", "tone": "down", "title": "自選：TSMC ADR falls 4%"}])

    def test_prompt_has_items_indices_and_only_the_owners_watchlist(self):
        d1 = D1()
        _, _, ask = run(d1)
        payload = json.loads(ask.prompts[0][ask.prompts[0].index("INPUT:") + 6:])
        self.assertEqual(payload["slot"], "close")
        self.assertEqual([row["code"] for row in payload["watch"]], ["2330"])
        self.assertTrue(all("link" not in row for row in payload["items"]), "連結不給模型")

    def test_unknown_ids_bad_values_and_skipped_items_are_dropped(self):
        d1 = D1()

        class Bad(FakeAsk):
            def __call__(self, prompt, schema):
                status, output, note = super().__call__(prompt, schema)
                output["items"][0]["tone"] = "maybe"
                output["items"][1]["group"] = "skip"
                output["items"].append({"id": "n999", "group": "tw", "market": "", "tone": "up", "impact": 1,
                                        "title": "編的", "reason": "編的"})
                return status, output, note
        run(d1, ask=Bad())
        titles = [row["title"] for row in d1.query("SELECT title FROM market_news_items")]
        self.assertNotIn("編的", titles)
        self.assertEqual(len(titles), 2, "送出四則（台股兩、美股一、韓一；超過一天的不送），壞掉的兩則不存")

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
            return "ok", {"watch": [{"id": row["id"], "tone": "up", "impact": 1, "title": "朋友：" + row["title"], "reason": "x"}
                                    for row in payload["watch"]]}, ""
        run(d1, friend_keys=[{"email": FRIEND, "alg": "AES-256-GCM"}], friend_ask=friend_ask)
        self.assertEqual([email for email, _ in seen], [FRIEND])
        self.assertNotIn("TSMC", seen[0][1], "朋友的提示只有他自己的自選")
        rows = d1.query("SELECT email, code, tone FROM market_news_user_items ORDER BY email")
        self.assertEqual(rows, [{"email": FRIEND, "code": "2454", "tone": "up"}, {"email": OWNER, "code": "2330", "tone": "down"}])

    def test_a_friend_key_failure_only_skips_that_friend(self):
        d1 = D1()
        report, _, _ = run(d1, friend_keys=[{"email": FRIEND}],
                           friend_ask=lambda key_row, prompt, schema: ("quota", None, "429"))
        self.assertEqual(report["friends"], {FRIEND: "quota"})
        self.assertEqual(d1.query("SELECT email FROM market_news_user_items"), [{"email": OWNER}])

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
