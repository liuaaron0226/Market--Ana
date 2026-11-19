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


class PlanTest(unittest.TestCase):
    """2026-11-20：一檔一次、公司名加引號再加代號或「股、營收、法說、財報」，來源含台灣財經媒體四家。"""

    def test_one_query_per_stock_with_code_or_finance_words_and_taiwan_finance_sites(self):
        glass = {"code": "1802", "name": "台玻", "tier": "watch"}
        plan = wn.gnews_plan([glass])
        self.assertEqual(len(plan), 1)
        query = query_of(plan[0]["url"])
        self.assertIn('"台玻" (1802 OR 股 OR 營收 OR 法說 OR 財報)', query)
        for site in ("cna.com.tw", "cnyes.com", "money.udn.com", "ctee.com.tw", "moneydj.com"):
            self.assertIn(f"site:{site}", query)
        self.assertIn("hl=zh-TW", plan[0]["url"])

    def test_a_stock_new_to_the_list_is_backfilled_thirty_days_first(self):
        new = {"code": "8358", "name": "金居", "tier": "watch"}
        old = {"code": "1802", "name": "台玻", "tier": "watch"}
        state = {("gnews-backfill", "1802"): {"last_success_at": "2026-11-19T00:00:00Z"},
                 ("gnews", "1802"): {"last_attempt_at": "2026-11-19T06:00:00Z"}}
        plan = wn.gnews_plan([old, new], state)
        self.assertEqual([q["stocks"][0]["code"] for q in plan], ["8358", "1802"])
        self.assertIn("when:30d", query_of(plan[0]["url"]))
        self.assertEqual((plan[0]["backfill"], plan[0]["keep_days"]), (True, 30))
        self.assertIn("when:2d", query_of(plan[1]["url"]))
        self.assertEqual((plan[1]["backfill"], plan[1]["keep_days"]), (False, 3))

    def test_the_budget_goes_to_whoever_was_fetched_longest_ago(self):
        stocks = [{"code": str(2000 + n), "name": f"公司{n}", "tier": "search"} for n in range(5)]
        state = {("gnews-backfill", s["code"]): {"last_success_at": "x"} for s in stocks}
        state.update({("gnews", s["code"]): {"last_attempt_at": f"2026-11-1{9 - n}T00:00:00Z"} for n, s in enumerate(stocks)})
        plan = wn.gnews_plan(stocks, state, budget=2)
        self.assertEqual([q["stocks"][0]["code"] for q in plan], ["2004", "2003"])

    def test_pool_stocks_are_batched_and_english_names_go_to_big_outlets(self):
        pool = [{"code": str(3000 + n), "name": f"池{n}號", "tier": "pool"} for n in range(7)]
        plan = wn.gnews_plan([*pool, {**TSMC, "tier": "watch"}], {("gnews-backfill", "2330"): {"last_success_at": "x"}})
        sizes = [len(q["stocks"]) for q in plan]
        self.assertEqual(sizes, [1, 5, 2, 1])
        english = query_of(plan[-1]["url"])
        self.assertIn('"TSMC"', english)
        self.assertIn("site:reuters.com", english)
        self.assertNotIn("cnyes", english)

    def test_ky_suffix_is_dropped_for_search_and_matching(self):
        chip = {"code": "3661", "name": "世芯-KY", "tier": "watch"}
        self.assertIn('"世芯" (3661', query_of(wn.gnews_plan([chip])[0]["url"]))
        self.assertEqual([r["code"] for r in wn.match_gnews([{"title": "大摩看好世芯拿下第三家客戶", "link": "x"}], [chip])], ["3661"])

    def test_auto_generated_price_alerts_are_noise(self):
        for title in ("盤中速報 - 台玻(1802)股價殺至跌停，跌停價58.7元，成交70,877張", "鉅亨速報- Factset 最新調查：世芯-KY(3661-TW)EPS預估下修",
                      "世芯-KY(3661)股價拉至漲停，漲停價3950.0元"):
            self.assertTrue(wn.is_noise(title), title)
        for title in ("台玻9月獲利11.7億年增28750%", "創意爆9,453萬元違約交割 股價跌停深鎖", "金居11/16~17參加J.P. Morgan舉辦之海外法說會"):
            self.assertFalse(wn.is_noise(title), title)


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

    def test_taiwan_finance_outlets_and_their_subdomains(self):
        content = grss(gitem("台玻：展望穩定訂單看好到2027 - 鉅亨網", "https://news.cnyes.com", n=1),
                       gitem("玻纖布夯 台玻9月獲利爆衝 - 工商時報", "https://www.ctee.com.tw", n=2),
                       gitem("台玻兩岸擴產 - 經濟日報", "https://money.udn.com", n=3),
                       gitem("台玻9月獲利佔Q3逾四成 - MoneyDJ", "https://www.moneydj.com", n=4),
                       gitem("台玻怎麼了 - 聯合新聞網", "https://udn.com", n=5))
        self.assertEqual([r["source"] for r in wn.parse_gnews(content)], ["gnews-cnyes", "gnews-ctee", "gnews-udn", "gnews-moneydj"])

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

    def test_noise_is_not_stored_and_a_backfill_is_recorded(self):
        get, calls = self.web({"hl=zh-TW": Response(content=grss(
            gitem("盤中速報 - 台積電(2330)股價殺至跌停，跌停價900元 - 鉅亨網", "https://news.cnyes.com", n=1),
            gitem("台積電10月營收創高 - 鉅亨網", "https://news.cnyes.com", when="Sun, 01 Nov 2026 03:00:00 GMT", n=2)))})
        result = wn.gather([{**TSMC, "name_en": None, "tier": "watch"}], get=get, now=NOW, sleep=lambda s: None)
        self.assertEqual([(n["source"], n["title"]) for n in result["news"] if n["code"] == "2330"],
                         [("gnews-cnyes", "台積電10月營收創高")], "回補：18 天前的也收；自動稿不收")
        self.assertIn({"source": "gnews-backfill", "code": "2330", "error": None}, result["fetches"])

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
        self.assertEqual([(s["code"], s["tier"]) for s in picked], [("2330", "watch"), ("6488", "search"), ("3008", "search")])
        self.assertEqual(picked[0]["name_en"], "TSMC")
        self.assertIsNone(picked[2]["name_en"])
        self.assertEqual(wn.select_stocks(watch, pool, searches, names)[-1]["tier"], "pool")


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


class AnnouncementTest(unittest.TestCase):
    def test_official_announcements_become_stock_news_rows(self):
        import json
        payload = json.dumps({"items": [{"code": "3443", "date": "2026-11-16", "time": "15:59:09", "subject": "公告本公司董事會決議  股利分派",
                                         "body": "1.事實發生日:115/11/16\n2.說明" + "很長" * 300}, {"code": "3443", "subject": "缺日期"}]})
        rows = wn.announcement_rows([{"code": "3443", "payload": payload}, {"code": "x", "payload": "{壞掉"}])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["code"], row["source"], row["title"]), ("3443", "mops", "公告本公司董事會決議 股利分派"))
        self.assertEqual(row["published_at"], "2026-11-16T07:59:09Z")
        self.assertTrue(row["link"].startswith("https://mops.twse.com.tw/"))
        self.assertIn("3443", row["link"])
        self.assertLessEqual(len(row["summary"]), 200)


if __name__ == "__main__":
    unittest.main()
