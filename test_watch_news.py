"""自選股關注 C 票：關注股的新聞標題（Yahoo股市個股新聞、中央通訊社產經證券／國際／科技）。

測試用假的網路回應，資料庫用本機 SQLite 套同一份變更檔，檢查存下了什麼。"""
import sqlite3
import unittest
from pathlib import Path

import watch_news as wn

ROOT = Path(__file__).resolve().parent
MIGRATION = (ROOT / "cloud/migrations/0014_watch_news.sql").read_text(encoding="utf-8")
NOW = "2026-11-16T09:00:00Z"


def item(title, link, when="Mon, 16 Nov 2026 07:44:00 GMT", summary="前言"):
    return (f"<item><title><![CDATA[{title}]]></title><link>{link}</link><pubDate>{when}</pubDate>"
            f"<description><![CDATA[{summary}]]></description></item>")


def rss(*items, title="Yahoo股市 - 最新新聞"):
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>{title}</title>'
            f'{"".join(items)}</channel></rss>').encode("utf-8")


def yahoo_link(n):
    return f"https://tw.stock.yahoo.com/news/a-{n}.html"


def cna_link(n):
    return f"https://www.cna.com.tw/news/afe/2026111601{n:02d}.aspx"


class Response:
    def __init__(self, status=200, content=b"", headers=None):
        self.status, self.content, self.headers = status, content, headers or {}


STOCKS = [{"code": "2330", "name": "台積電", "market": "上市"}, {"code": "6488", "name": "環球晶", "market": "上櫃"}]
YAHOO = {"2330": "https://tw.stock.yahoo.com/rss?s=2330.TW", "6488": "https://tw.stock.yahoo.com/rss?s=6488.TWO"}
CNA = {name: f"https://feeds.feedburner.com/rsscna/{name}" for name in ("finance", "intworld", "technology")}


def normal_web():
    return {
        YAHOO["2330"]: Response(content=rss(item("台積電法說會", yahoo_link(1)), item("外資賣超台積電", yahoo_link(2)))),
        YAHOO["6488"]: Response(content=rss(item("環球晶10月營收", yahoo_link(3)))),
        CNA["finance"]: Response(content=rss(
            item("台積電擴產", cna_link(1), "Mon, 16 Nov 2026 15:51:54 +0800", "（中央社記者張建中新竹16日電）台積電今天宣布擴產。"),
            item("三大法人賣超", cna_link(2), "Mon, 16 Nov 2026 15:13:30 +0800", "（中央社台北16日電）台股今天下跌。"),
            title="中央社即時新聞 財經新聞")),
        CNA["intworld"]: Response(content=rss(item("美股收黑", cna_link(3), "Mon, 16 Nov 2026 06:00:00 +0800"))),
        CNA["technology"]: Response(content=rss(item("6488 擴大矽晶圓產能", cna_link(4), "Mon, 16 Nov 2026 12:00:00 +0800"))),
    }


class FakeD1:
    """本機 SQLite 當成雲端資料庫：query 讀、execute 寫一整份指令檔，和 wrangler 的兩種用法一樣。"""

    def __init__(self, watched=STOCKS, owner="owner@example.com", migrated=True):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""CREATE TABLE user_watchlist (email TEXT, code TEXT, name TEXT, market TEXT,
                                 watching INTEGER NOT NULL DEFAULT 0, watch_since TEXT);""")
        if migrated:
            self.db.executescript(MIGRATION)
        for index, stock in enumerate(watched):
            self.db.execute("INSERT INTO user_watchlist VALUES (?, ?, ?, ?, 1, ?)",
                            (owner, stock["code"], stock["name"], stock["market"], f"2026-11-1{index}T00:00:00Z"))
        self.db.execute("INSERT INTO user_watchlist VALUES ('friend@example.com', '2317', '鴻海', '上市', 1, NULL)")
        self.db.execute("INSERT INTO user_watchlist VALUES (?, '2603', '長榮', '上市', 0, NULL)", (owner,))
        self.scripts = []

    def query(self, sql):
        return [dict(row) for row in self.db.execute(sql)]

    def execute(self, script):
        self.scripts.append(script)
        self.db.executescript(script)

    def news(self, code=None):
        sql = "SELECT code, source, title, summary, link, published_at, fetched_at FROM watch_news"
        rows = self.query(sql + " ORDER BY published_at DESC, link")
        return rows if code is None else [row for row in rows if row["code"] == code]

    def fetches(self):
        return {(row["source"], row["code"]): row for row in self.query("SELECT * FROM watch_news_fetches")}


def run(d1, web, now=NOW, owner="owner@example.com"):
    calls = []

    def get(url, headers=None):
        calls.append(url)
        response = web[url]
        if isinstance(response, Exception):
            raise response
        return response
    report = wn.daily(owner, query=d1.query, execute=d1.execute, get=get, now=now, sleep=lambda seconds: None)
    return report, calls


class ParseTests(unittest.TestCase):
    def test_keeps_only_title_lead_link_and_time(self):
        items = wn.parse(normal_web()[CNA["finance"]].content, source="cna-finance")
        self.assertEqual(items[0], {
            "title": "台積電擴產", "summary": "（中央社記者張建中新竹16日電）台積電今天宣布擴產。",
            "link": cna_link(1), "published_at": "2026-11-16T07:51:54Z"})

    def test_lead_is_plain_text_and_never_the_full_article(self):
        long = "<p>第一段<b>重點</b></p>" + "很長的內文" * 200
        lead = wn.parse(rss(item("標題", yahoo_link(9), summary=long)), source="yahoo")[0]["summary"]
        self.assertTrue(lead.startswith("第一段重點"))
        self.assertLessEqual(len(lead), wn.SUMMARY_MAX)

    def test_an_empty_feed_is_no_items_not_an_error(self):
        self.assertEqual(wn.parse(rss(), source="yahoo"), [])

    def test_a_changed_format_stops_instead_of_guessing(self):
        bad = [
            b"<html>maintenance</html>",
            rss('<item><link>https://tw.stock.yahoo.com/news/x.html</link></item>'),          # 沒有標題
            rss(item("標題", yahoo_link(1), when="昨天")),                                       # 時間看不懂
            rss(item("標題", "javascript:alert(1)")),                                           # 連結不是那一家的網址
            rss(item("標題", "https://evil.example/news.html")),
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss><channel></channel></rss>',
        ]
        for content in bad:
            with self.assertRaises(wn.FormatChanged, msg=content[:60]):
                wn.parse(content, source="yahoo")


class DailyTests(unittest.TestCase):
    def test_stores_each_watched_stocks_headlines_with_source_and_times(self):
        d1 = FakeD1()
        report, calls = run(d1, normal_web())
        self.assertEqual(sorted(calls), sorted([*YAHOO.values(), *CNA.values()]), "只抓站主關注中的股票，不抓別人的、也不抓沒關注的")
        tsmc = d1.news("2330")
        self.assertEqual([(row["source"], row["title"]) for row in tsmc],
                         [("cna-finance", "台積電擴產"), ("yahoo", "台積電法說會"), ("yahoo", "外資賣超台積電")])
        self.assertEqual(tsmc[1], {"code": "2330", "source": "yahoo", "title": "台積電法說會", "summary": "前言",
                                   "link": yahoo_link(1), "published_at": "2026-11-16T07:44:00Z", "fetched_at": NOW})
        self.assertEqual(report["stored"], 7)

    def test_cna_is_matched_by_name_or_code_and_the_rest_is_market_wide(self):
        d1 = FakeD1()
        run(d1, normal_web())
        self.assertEqual([row["title"] for row in d1.news("6488") if row["source"].startswith("cna")], ["6488 擴大矽晶圓產能"])
        self.assertEqual({row["title"] for row in d1.news("")}, {"三大法人賣超", "美股收黑"})

    def test_a_code_inside_a_longer_number_does_not_match(self):
        d1 = FakeD1()
        web = normal_web()
        web[CNA["technology"]] = Response(content=rss(item("營收 164880 億元", cna_link(5))))
        run(d1, web)
        self.assertIn("營收 164880 億元", [row["title"] for row in d1.news("")])
        self.assertNotIn("營收 164880 億元", [row["title"] for row in d1.news("6488")])

    def test_the_same_headline_is_not_stored_twice(self):
        d1 = FakeD1()
        run(d1, normal_web())
        before = d1.news()
        report, _ = run(d1, normal_web(), now="2026-11-16T21:00:00Z")
        self.assertEqual(d1.news(), before, "第二次抓到同一則：不重複存、取得時間留第一次的")
        self.assertEqual(report["stored"], 0)

    def test_the_same_cna_story_in_two_sections_is_one_headline(self):
        d1 = FakeD1()
        web = normal_web()
        web[CNA["technology"]] = Response(content=rss(item("台積電擴產", cna_link(1))))
        run(d1, web)
        self.assertEqual([row["title"] for row in d1.news("2330")].count("台積電擴產"), 1)

    def test_headlines_older_than_the_retention_are_removed(self):
        d1 = FakeD1()
        run(d1, normal_web(), now="2026-11-16T09:00:00Z")
        self.assertEqual(len(d1.news("6488")), 2)
        # 90 天後：11-16 07:44 發布的那幾則已經超過 90 天（2027-02-14 09:00 往前 90 天是 11-16 09:00）。
        web = {url: Response(content=rss()) for url in [*YAHOO.values(), *CNA.values()]}
        web[YAHOO["6488"]] = Response(content=rss(item("新的一則", yahoo_link(7), "Sun, 14 Feb 2027 01:00:00 GMT")))
        run(d1, web, now="2027-02-14T09:00:00Z")
        self.assertEqual([row["title"] for row in d1.news()], ["新的一則"])

    def test_each_source_fails_on_its_own(self):
        d1 = FakeD1()
        web = normal_web()
        web[YAHOO["2330"]] = Response(status=500)
        web[CNA["intworld"]] = ConnectionError("連不上")
        web[CNA["technology"]] = Response(content=b"<html>maintenance</html>")
        report, _ = run(d1, web)
        fetches = d1.fetches()
        self.assertIsNotNone(fetches[("yahoo", "2330")]["last_error"])
        self.assertIsNone(fetches[("yahoo", "2330")]["last_success_at"])
        self.assertIsNone(fetches[("yahoo", "6488")]["last_error"])
        self.assertEqual(fetches[("yahoo", "6488")]["last_success_at"], NOW)
        self.assertIsNone(fetches[("cna-finance", "")]["last_error"])
        self.assertIn("ConnectionError", fetches[("cna-intworld", "")]["last_error"])
        self.assertIn("格式", fetches[("cna-technology", "")]["last_error"])
        self.assertEqual([row["title"] for row in d1.news("6488")], ["環球晶10月營收"], "壞掉的那一份一則都不寫")
        self.assertEqual(report["sources"]["yahoo:2330"]["status"], "error")

    def test_a_failure_keeps_the_last_success_time(self):
        d1 = FakeD1()
        run(d1, normal_web())
        web = normal_web()
        web[YAHOO["2330"]] = Response(status=503)
        run(d1, web, now="2026-11-17T09:00:00Z")
        row = d1.fetches()[("yahoo", "2330")]
        self.assertEqual((row["last_attempt_at"], row["last_success_at"]), ("2026-11-17T09:00:00Z", NOW))
        self.assertEqual(row["last_error"], "HTTP 503")
        run(d1, normal_web(), now="2026-11-18T09:00:00Z")
        self.assertIsNone(d1.fetches()[("yahoo", "2330")]["last_error"])

    def test_when_yahoo_ignores_the_stock_filter_every_stock_is_marked_not_fetched(self):
        d1 = FakeD1()
        web = normal_web()
        same = rss(item("六都房價", yahoo_link(8)), item("國泰金獲利", yahoo_link(9)))
        web[YAHOO["2330"]] = Response(content=same)
        web[YAHOO["6488"]] = Response(content=same)
        run(d1, web)
        fetches = d1.fetches()
        for code in ("2330", "6488"):
            self.assertIn("篩選", fetches[("yahoo", code)]["last_error"])
        self.assertEqual([row["source"] for row in d1.news("2330")], ["cna-finance"], "大盤新聞不能被當成個股新聞存下")

    def test_when_yahoo_asks_to_slow_down_the_rest_are_not_requested(self):
        d1 = FakeD1()
        web = normal_web()
        web[YAHOO["2330"]] = Response(status=429, headers={"Retry-After": "120"})
        _, calls = run(d1, web)
        self.assertNotIn(YAHOO["6488"], calls, "來源要求放慢時，這一輪不再打它")
        self.assertEqual(d1.fetches()[("yahoo", "6488")]["last_error"], "來源要求放慢，這一輪沒抓")
        self.assertEqual(len(d1.news("")), 2, "中央社照常")

    def test_nothing_to_do_without_watched_stocks_but_market_wide_news_is_kept(self):
        d1 = FakeD1(watched=[])
        _, calls = run(d1, normal_web())
        self.assertEqual(sorted(calls), sorted(CNA.values()))
        self.assertEqual(len(d1.news("")), 4)

    def test_before_the_tables_exist_it_stops_without_fetching(self):
        d1 = FakeD1(migrated=False)
        report, calls = run(d1, normal_web())
        self.assertEqual((report["status"], calls, d1.scripts), ("not-migrated", [], []))

    def test_owner_email_is_checked_before_it_goes_into_a_query(self):
        with self.assertRaises(ValueError):
            run(FakeD1(), normal_web(), owner="x' OR '1'='1")

    def test_quotes_in_headlines_are_stored_as_written(self):
        d1 = FakeD1()
        web = normal_web()
        web[YAHOO["6488"]] = Response(content=rss(item("環球晶 'O'Neil'; DROP TABLE watch_news; --", yahoo_link(4))))
        run(d1, web)
        self.assertEqual(d1.news("6488")[0]["title"], "環球晶 'O'Neil'; DROP TABLE watch_news; --")


if __name__ == "__main__":
    unittest.main()
