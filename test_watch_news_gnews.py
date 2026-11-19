"""個股新聞改抓 Google News 大媒體（本人 2026-11-19「希望你的消息來源更具考靠性，且可以考慮過外的大型新聞」）。

規格：.scratch/market-news/consensus.md 第 9、10 條與技術選擇 e。只用假的網路回應。"""
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import watch_news as wn

ROOT = Path(__file__).resolve().parent
NOW = "2026-11-19T06:00:00Z"


def gitem(title, outlet_url, when="Thu, 19 Nov 2026 03:00:00 GMT", n=1):
    return (f"<item><title>{title}</title><link>https://news.google.com/rss/articles/CBM{n}?oc=5</link>"
            f"<pubDate>{when}</pubDate><description>&lt;a href=\"x\"&gt;{title}&lt;/a&gt;</description>"
            f"<source url=\"{outlet_url}\">x</source></item>")


def grss(*items):
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Google News</title>'
            f'{"".join(items)}</channel></rss>').encode("utf-8")


class Response:
    def __init__(self, status=200, content=b""):
        self.status, self.content = status, content


TSMC = {"code": "2330", "name": "台積電", "name_en": "TSMC", "market": "上市"}
MTK = {"code": "2454", "name": "聯發科", "name_en": "MediaTek", "market": "上市"}
GWC = {"code": "6488", "name": "環球晶", "name_en": "GWC", "market": "上櫃"}


def query_of(url):
    return parse_qs(urlsplit(url).query)["q"][0]


class QueryTest(unittest.TestCase):
    def test_batches_chinese_names_to_cna_and_english_names_to_big_outlets(self):
        queries = wn.gnews_queries([TSMC, MTK], batch=10)
        self.assertEqual([q["lang"] for q in queries], ["zh", "en"])
        zh, en = (query_of(q["url"]) for q in queries)
        self.assertIn("台積電 OR 聯發科", zh)
        self.assertIn("site:cna.com.tw", zh)
        self.assertIn('"TSMC" OR "MediaTek"', en)
        for site in ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "asia.nikkei.com"):
            self.assertIn(f"site:{site}", en)
        self.assertIn("hl=zh-TW", queries[0]["url"])
        self.assertIn("hl=en-US", queries[1]["url"])

    def test_batch_size_splits_long_lists_and_skips_missing_english_names(self):
        stocks = [{"code": str(1000 + n), "name": f"公司{n}", "name_en": None if n % 2 else f"Co{n}x"} for n in range(25)]
        queries = wn.gnews_queries(stocks, batch=10)
        self.assertEqual(sum(q["lang"] == "zh" for q in queries), 3)
        self.assertEqual(sum(q["lang"] == "en" for q in queries), 2)   # 13 檔有英文名 → 10＋3
        self.assertEqual(len(queries[0]["stocks"]), 10)


class ParseTest(unittest.TestCase):
    def test_keeps_only_whitelisted_outlets_and_strips_outlet_suffix(self):
        content = grss(gitem("TSMC raises capex - Reuters", "https://www.reuters.com", n=1),
                       gitem("TSMC stock forecast - MarketBeat", "https://www.marketbeat.com", n=2),
                       gitem("台積電擴產| 證券 - 中央社 CNA", "https://www.cna.com.tw", n=3))
        rows = wn.parse_gnews(content)
        self.assertEqual([(r["title"], r["source"]) for r in rows],
                         [("TSMC raises capex", "gnews-reuters"), ("台積電擴產", "gnews-cna")])
        self.assertEqual(rows[0]["summary"], "")
        self.assertTrue(rows[0]["link"].startswith("https://news.google.com/rss/articles/"))
        self.assertEqual(rows[0]["published_at"], "2026-11-19T03:00:00Z")

    def test_rejects_entity_declarations(self):
        with self.assertRaises(wn.FormatChanged):
            wn.parse_gnews(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>')


class MatchTest(unittest.TestCase):
    def test_title_must_name_the_company(self):
        rows = [{"title": "TSMC raises capex", "link": "a"}, {"title": "Nvidia earnings beat", "link": "b"},
                {"title": "聯發科、台積電同步走弱", "link": "c"}, {"title": "GWC expands wafer plant", "link": "d"},
                {"title": "A big gwc win", "link": "e"}]
        matched = wn.match_gnews(rows, [TSMC, MTK, GWC])
        self.assertEqual(sorted((r["link"], r["code"]) for r in matched),
                         [("a", "2330"), ("c", "2330"), ("c", "2454"), ("d", "6488")])

    def test_english_name_full_width_spaces_are_trimmed(self):
        stock = {"code": "3105", "name": "穩懋", "name_en": "WIN　　"}
        self.assertEqual([r["code"] for r in wn.match_gnews([{"title": "WIN Semi cuts outlook", "link": "x"}], [stock])],
                         ["3105"])


class GatherTest(unittest.TestCase):
    def web(self, routes):
        calls = []

        def get(url, params=None):
            calls.append(url)
            for key, response in routes.items():
                if key in url:
                    return response
            return Response(content=grss())
        return get, calls

    def test_old_items_are_dropped_and_rows_are_stored_per_stock(self):
        get, _ = self.web({"hl=en-US": Response(content=grss(
            gitem("TSMC raises capex - Reuters", "https://www.reuters.com", n=1),
            gitem("TSMC 2015 story - Bloomberg", "https://www.bloomberg.com", when="Mon, 02 Mar 2015 03:00:00 GMT", n=2)))})
        result = wn.gather([TSMC], get=get, now=NOW, sleep=lambda s: None)
        news = [n for n in result["news"] if n["source"].startswith("gnews")]
        self.assertEqual([(n["code"], n["title"]) for n in news], [("2330", "TSMC raises capex")])
        self.assertIn({"source": "gnews", "code": "2330", "error": None}, result["fetches"])

    def test_slow_down_stops_the_rest_of_this_round(self):
        many = [{"code": str(1000 + n), "name": f"公司{n}", "name_en": None} for n in range(25)]
        get, calls = self.web({"q=": Response(status=429)})
        result = wn.gather(many, get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual(sum("news.google.com" in url for url in calls), 1)
        errors = {f["code"]: f["error"] for f in result["fetches"] if f["source"] == "gnews"}
        self.assertEqual(len(errors), 25)
        self.assertTrue(all(errors.values()))

    def test_no_yahoo_requests(self):
        get, calls = self.web({})
        wn.gather([TSMC, GWC], get=get, now=NOW, sleep=lambda s: None)
        self.assertFalse(any("yahoo" in url for url in calls))


class SelectTest(unittest.TestCase):
    def test_priority_watchlist_then_pool_then_searches_and_cap(self):
        watch = [{"code": "2330", "name": "台積電", "market": "上市"}]
        pool = [{"code": "2454", "name": "聯發科", "market": "上市"}, {"code": "2330", "name": "台積電", "market": "上市"}]
        searches = [{"code": "6488", "name": "環球晶", "market": "上櫃"}, {"code": "3008", "name": "大立光", "market": "上市"}]
        names = {"2330": "TSMC", "6488": "GWC"}
        picked = wn.select_stocks(watch, pool, searches, names, cap=3)
        self.assertEqual([s["code"] for s in picked], ["2330", "2454", "6488"])
        self.assertEqual(picked[0]["name_en"], "TSMC")
        self.assertIsNone(picked[1]["name_en"])


class PoolTest(unittest.TestCase):
    def db(self, rows):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, turnover INT)")
        connection.executemany("INSERT INTO daily VALUES (?, ?, ?, ?, ?)", rows)
        return connection

    def test_top_by_latest_day_turnover_within_universe(self):
        listed = self.db([("2026-11-18", "2330", "台積電", "common", 900), ("2026-11-18", "2454", "聯發科", "common", 500),
                          ("2026-11-18", "1101", "台泥", "common", 990), ("2026-11-17", "2330", "台積電", "common", 1)])
        otc = self.db([("2026-11-18", "6488", "環球晶", "common", 700)])
        universe = {"members": [{"code": "2330", "market": "上市"}, {"code": "2454", "market": "上市"},
                                {"code": "6488", "market": "上櫃"}]}
        pool = wn.pool_top(listed, otc, universe, top=2)
        self.assertEqual([(p["code"], p["rank"]) for p in pool], [("2330", 1), ("6488", 2)])
        self.assertEqual(pool[0]["data_date"], "2026-11-18")
        self.assertEqual(pool[1]["market"], "上櫃")

    def test_english_names_from_both_exchanges(self):
        names = wn.english_names([{"公司代號": "2330", "英文簡稱": "TSMC"}],
                                 [{"SecuritiesCompanyCode": "3105", "Symbol": "WIN　　"}, {"SecuritiesCompanyCode": "9999", "Symbol": ""}])
        self.assertEqual(names, {"2330": "TSMC", "3105": "WIN"})


if __name__ == "__main__":
    unittest.main()
