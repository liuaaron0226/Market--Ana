"""個股新聞標題（本人 2026-11-19 起：Google News 只收大媒體＋中央社三個分類；Yahoo 逐檔停用）。

測試用假的網路回應，資料庫用本機 SQLite 套同一份變更檔，檢查存下了什麼。查詢與比對細節見 test_watch_news_gnews.py。"""
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import watch_news as wn

ROOT = Path(__file__).resolve().parent
MIGRATIONS = ROOT / "cloud/migrations"
WATCHLIST = "".join((MIGRATIONS / name).read_text(encoding="utf-8") for name in ("0005_user_portfolio.sql", "0011_watchlist_watch.sql"))
MIGRATION = (MIGRATIONS / "0015_watch_news.sql").read_text(encoding="utf-8")
NEWS_POOL = "".join((MIGRATIONS / name).read_text(encoding="utf-8") for name in ("0031_market_news.sql", "0032_stock_news_marks.sql"))
NOW = "2026-11-16T09:00:00Z"


def item(title, link, when="Mon, 16 Nov 2026 07:44:00 GMT", summary="前言"):
    return (f"<item><title><![CDATA[{title}]]></title><link>{link}</link><pubDate>{when}</pubDate>"
            f"<description><![CDATA[{summary}]]></description></item>")


def gitem(title, n, outlet="https://www.reuters.com", when="Mon, 16 Nov 2026 07:44:00 GMT"):
    return (f"<item><title>{title}</title><link>{gnews_link(n)}</link><pubDate>{when}</pubDate>"
            f"<source url=\"{outlet}\">x</source></item>")


def rss(*items, title="feed"):
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>{title}</title>'
            f'{"".join(items)}</channel></rss>').encode("utf-8")


def gnews_link(n):
    return f"https://news.google.com/rss/articles/CBM{n}?oc=5"


def cna_link(n):
    return f"https://www.cna.com.tw/news/afe/2026111601{n:02d}.aspx"


class Response:
    def __init__(self, status=200, content=b""):
        self.status, self.content = status, content


STOCKS = [{"code": "2330", "name": "台積電", "market": "上市"}, {"code": "6488", "name": "環球晶", "market": "上櫃"}]
CNA = {name: f"https://feeds.feedburner.com/rsscna/{name}" for name in ("finance", "intworld", "technology")}
CNA_ZH = "https://www.cna.com.tw"


def is_gnews(url):
    return url.startswith(wn.GNEWS_URL)


class Web(dict):
    """網址 → 回應。Google News 依查詢語言（zh／en）給同一份。"""

    def lookup(self, url):
        if is_gnews(url):
            return self.get(("gnews", "en" if "hl=en-US" in url else "zh"), Response(content=rss()))
        return self[url]


def normal_web():
    return Web({
        ("gnews", "zh"): Response(content=rss(gitem("台積電法說會 - 中央社 CNA", 1, CNA_ZH),
                                              gitem("環球晶10月營收 - 中央社 CNA", 3, CNA_ZH))),
        ("gnews", "en"): Response(content=rss(gitem("TSMC sales jump - Reuters", 2))),
        CNA["finance"]: Response(content=rss(
            item("台積電擴產", cna_link(1), "Mon, 16 Nov 2026 15:51:54 +0800", "（中央社記者張建中新竹16日電）台積電今天宣布擴產。"),
            item("三大法人賣超", cna_link(2), "Mon, 16 Nov 2026 15:13:30 +0800", "（中央社台北16日電）台股今天下跌。"),
            title="中央社即時新聞 財經新聞")),
        CNA["intworld"]: Response(content=rss(item("美股收黑", cna_link(3), "Mon, 16 Nov 2026 06:00:00 +0800"))),
        CNA["technology"]: Response(content=rss(item("6488 擴大矽晶圓產能", cna_link(4), "Mon, 16 Nov 2026 12:00:00 +0800"))),
    })


class FakeD1:
    """本機 SQLite 當成雲端資料庫：query 讀、execute 寫一整份指令檔，和 wrangler 的兩種用法一樣。"""

    def __init__(self, watched=STOCKS, owner="owner@example.com", migrated=True, pool=(), searches=()):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(WATCHLIST)
        if migrated:
            self.db.executescript(MIGRATION)
            self.db.executescript(NEWS_POOL)
            self.db.execute("INSERT INTO stock_news_names VALUES ('2330', 'TSMC', '2026-11-15T00:00:00Z')")
        # 站主的這兩檔關注開著（和 11-16 起一樣）：大環境消息只排除對到關注中的
        add = ("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at, watching) "
               "VALUES (?, ?, ?, ?, 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 1)")
        for stock in watched:
            self.db.execute(add, (owner, stock["code"], stock["name"], stock["market"]))
        if migrated:
            for rank, stock in enumerate(pool, 1):
                self.db.execute("INSERT INTO stock_news_pool VALUES (?, ?, ?, ?, '2026-11-13')",
                                (stock["code"], stock["name"], stock["market"], rank))
        for index, stock in enumerate(searches):
            self.db.execute("INSERT INTO user_search_history (email, code, name, market, viewed_at) VALUES (?, ?, ?, ?, ?)",
                            ("friend@example.com", stock["code"], stock["name"], stock["market"], f"2026-11-1{index}T00:00:00Z"))
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


def run(d1, web, now=NOW, owner="owner@example.com", budget=wn.MAX_QUERIES):
    calls = []

    def get(url, headers=None):
        calls.append(url)
        response = web.lookup(url)
        if isinstance(response, Exception):
            raise response
        return response
    report = wn.daily(owner, query=d1.query, execute=d1.execute, get=get, now=now, sleep=lambda seconds: None, budget=budget)
    return report, calls


def queried_names(calls):
    return " ".join(parse_qs(urlsplit(url).query)["q"][0] for url in calls if is_gnews(url))


class ParseTests(unittest.TestCase):
    def test_keeps_only_title_lead_link_and_time(self):
        items = wn.parse(normal_web()[CNA["finance"]].content, source="cna-finance")
        self.assertEqual(items[0], {
            "title": "台積電擴產", "summary": "（中央社記者張建中新竹16日電）台積電今天宣布擴產。",
            "link": cna_link(1), "published_at": "2026-11-16T07:51:54Z"})

    def test_lead_is_plain_text_and_never_the_full_article(self):
        long = "<p>第一段<b>重點</b></p>" + "很長的內文" * 200
        lead = wn.parse(rss(item("標題", cna_link(9), summary=long)), source="cna-finance")[0]["summary"]
        self.assertTrue(lead.startswith("第一段重點"))
        self.assertLessEqual(len(lead), 200)

    def test_an_empty_feed_is_no_items_not_an_error(self):
        self.assertEqual(wn.parse(rss(), source="cna-finance"), [])

    def test_a_changed_format_stops_instead_of_guessing(self):
        bad = [
            b"<html>maintenance</html>",
            rss(f"<item><link>{cna_link(1)}</link></item>"),                                         # 沒有標題
            rss(item("標題", cna_link(1), when="昨天")),                                              # 時間看不懂
            rss(item("標題", "javascript:alert(1)")),                                                # 連結不是那一家的網址
            rss(item("標題", "https://evil.example/news.html")),
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss><channel></channel></rss>',
        ]
        for content in bad:
            with self.assertRaises(wn.FormatChanged, msg=content[:60]):
                wn.parse(content, source="cna-finance")


class DailyTests(unittest.TestCase):
    def test_stores_big_outlet_headlines_per_stock_with_source_and_times(self):
        d1 = FakeD1()
        report, calls = run(d1, normal_web())
        self.assertFalse(any("yahoo" in url for url in calls), "Yahoo 逐檔停用")
        tsmc = d1.news("2330")
        self.assertEqual([(row["source"], row["title"]) for row in tsmc],
                         [("cna-finance", "台積電擴產"), ("gnews-cna", "台積電法說會"), ("gnews-reuters", "TSMC sales jump")])
        self.assertEqual(tsmc[1], {"code": "2330", "source": "gnews-cna", "title": "台積電法說會", "summary": "",
                                   "link": gnews_link(1), "published_at": "2026-11-16T07:44:00Z", "fetched_at": NOW})
        self.assertEqual(report["stored"], 7)

    def test_the_list_is_everyones_watchlist_plus_pool_plus_searches(self):
        d1 = FakeD1(pool=[{"code": "2454", "name": "聯發科", "market": "上市"}],
                    searches=[{"code": "3008", "name": "大立光", "market": "上市"}])
        d1.db.execute("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at, watching) "
                      "VALUES ('friend@example.com', '2317', '鴻海', '上市', 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)")
        report, calls = run(d1, normal_web())
        names = queried_names(calls)
        for name in ("台積電", "環球晶", "鴻海", "聯發科", "大立光", '"TSMC"'):
            self.assertIn(name, names)
        self.assertEqual(report["stocks"], 5)

    def test_market_wide_news_for_the_forecast_only_skips_the_owners_watched_stocks(self):
        # 大盤預測讀 code '' 的大環境消息：名單變大（前 50、每個人的自選）不能讓它變少——只有對到站主關注中的才不算大環境。
        d1 = FakeD1()
        d1.db.execute("UPDATE user_watchlist SET watching = 0 WHERE code = '2330'")
        run(d1, normal_web())
        wide = {row["title"] for row in d1.news("")}
        self.assertIn("台積電擴產", wide, "2330 只在自選、沒開關注：仍是大環境消息，也存給 2330")
        self.assertIn("台積電擴產", [row["title"] for row in d1.news("2330")])
        self.assertNotIn("6488 擴大矽晶圓產能", wide, "對到關注中的 6488：只存給 6488")

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
        web = Web({url: Response(content=rss()) for url in CNA.values()})
        web[("gnews", "zh")] = Response(content=rss(gitem("環球晶新的一則 - 中央社", 7, CNA_ZH, "Sun, 14 Feb 2027 01:00:00 GMT")))
        run(d1, web, now="2027-02-14T09:00:00Z")
        self.assertEqual([row["title"] for row in d1.news()], ["環球晶新的一則"])

    def test_each_source_fails_on_its_own(self):
        d1 = FakeD1()
        web = normal_web()
        web[("gnews", "en")] = Response(status=500)
        web[CNA["intworld"]] = ConnectionError("連不上")
        web[CNA["technology"]] = Response(content=b"<html>maintenance</html>")
        report, _ = run(d1, web)
        fetches = d1.fetches()
        self.assertEqual(fetches[("gnews", "2330")]["last_error"], "HTTP 500", "有英文名、英文那次失敗")
        self.assertIsNone(fetches[("gnews", "2330")]["last_success_at"])
        self.assertIsNone(fetches[("gnews", "6488")]["last_error"], "沒有英文名，只搜中文那次")
        self.assertEqual(fetches[("gnews", "6488")]["last_success_at"], NOW)
        self.assertIsNone(fetches[("cna-finance", "")]["last_error"])
        self.assertIn("ConnectionError", fetches[("cna-intworld", "")]["last_error"])
        self.assertIn("格式", fetches[("cna-technology", "")]["last_error"])
        self.assertEqual(report["sources"]["gnews:2330"]["status"], "error")

    def test_a_failure_keeps_the_last_success_time(self):
        d1 = FakeD1()
        run(d1, normal_web())
        web = normal_web()
        web[("gnews", "zh")] = Response(status=503)
        run(d1, web, now="2026-11-17T09:00:00Z")
        fetches = d1.fetches()
        failed = [code for code in ("2330", "6488") if fetches[("gnews", code)]["last_error"] == "HTTP 503"]
        self.assertEqual(len(failed), 1, "第一檔回 503，另一檔記這一輪沒抓")
        row = fetches[("gnews", failed[0])]
        self.assertEqual((row["last_attempt_at"], row["last_success_at"]), ("2026-11-17T09:00:00Z", NOW))
        run(d1, normal_web(), now="2026-11-18T09:00:00Z")
        self.assertIsNone(d1.fetches()[("gnews", failed[0])]["last_error"])

    def test_first_run_backfills_and_later_runs_only_look_back_two_days(self):
        d1 = FakeD1()
        _, first = run(d1, normal_web())
        self.assertIn("when:30d", queried_names(first))
        self.assertEqual(d1.fetches()[("gnews-backfill", "2330")]["last_success_at"], NOW)
        _, second = run(d1, normal_web(), now="2026-11-16T21:00:00Z")
        zh = " ".join(parse_qs(urlsplit(url).query)["q"][0] for url in second if is_gnews(url) and "hl=zh-TW" in url)
        self.assertNotIn("when:30d", zh)
        self.assertIn("when:2d", zh)

    def test_official_announcements_on_the_site_are_added_as_stock_news(self):
        import json
        d1 = FakeD1()
        d1.db.executescript("CREATE TABLE stock_data_state (key TEXT PRIMARY KEY, active_version TEXT);"
                            "CREATE TABLE stock_announcements (version TEXT, code TEXT, payload TEXT);"
                            "INSERT INTO stock_data_state VALUES ('current', 'v1');")
        d1.db.execute("INSERT INTO stock_announcements VALUES ('v1', '2330', ?)", (json.dumps({"items": [
            {"code": "2330", "date": "2026-11-16", "time": "15:30:00", "subject": "公告本公司董事會決議", "body": "說明"}]}),))
        run(d1, normal_web())
        self.assertIn(("mops", "公告本公司董事會決議"), [(row["source"], row["title"]) for row in d1.news("2330")])
        self.assertIsNone(d1.fetches()[("mops", "")]["last_error"])

    def test_when_google_asks_to_slow_down_the_rest_are_not_requested(self):
        d1 = FakeD1()
        web = normal_web()
        web[("gnews", "zh")] = Response(status=429)
        _, calls = run(d1, web)
        self.assertEqual(sum(is_gnews(url) for url in calls), 1, "來源要求放慢時，這一輪不再打它")
        self.assertEqual(d1.fetches()[("gnews", "2330")]["last_error"], "HTTP 429")
        self.assertEqual(len(d1.news("")), 2, "中央社照常")

    def test_the_night_budget_limits_google_searches_and_the_rest_wait_for_the_next_round(self):
        # 首頁消息加開的三班（本人 2026-11-24）：搜尋量壓低，沒輪到的那幾檔不記錯誤、下一輪接著抓；中央社照常
        d1 = FakeD1()
        report, calls = run(d1, normal_web(), budget=1)
        self.assertEqual(sum(is_gnews(url) for url in calls), 1)
        self.assertEqual(sorted(url for url in calls if not is_gnews(url)), sorted(CNA.values()))
        self.assertEqual(report["status"], "ok")
        tried = [code for code in ("2330", "6488") if ("gnews", code) in d1.fetches()]
        self.assertEqual(len(tried), 1, "沒輪到的那一檔這一輪不記")
        _, later = run(d1, normal_web(), now="2026-11-16T15:00:00Z", budget=1)
        other = next(code for code in ("2330", "6488") if code not in tried)
        self.assertIn(("gnews", other), d1.fetches(), "下一輪先抓上次沒輪到的")
        self.assertLessEqual(wn.NIGHT_MAX_QUERIES * 3, wn.MAX_QUERIES)

    def test_a_rate_limited_night_round_still_stores_the_other_sources(self):
        d1 = FakeD1()
        web = normal_web()
        web[("gnews", "zh")] = Response(status=429)
        report, calls = run(d1, web, budget=wn.NIGHT_MAX_QUERIES)
        self.assertEqual((report["status"], sum(is_gnews(url) for url in calls)), ("ok", 1))
        self.assertEqual(len(d1.news("")), 2, "中央社照常")

    def test_nothing_to_do_without_stocks_but_market_wide_news_is_kept(self):
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
        web[("gnews", "zh")] = Response(content=rss(gitem("環球晶 'O'Neil'; DROP TABLE watch_news; --", 4, CNA_ZH)))
        run(d1, web)
        self.assertEqual(d1.news("6488")[0]["title"], "環球晶 'O'Neil'; DROP TABLE watch_news; --")


class CommonWordNameTests(unittest.TestCase):
    """本人 2026-11-23：「新聞選了一個完全沒有跟創意這檔股票相關的新聞，只是他有用到創意這個字」。
    下面的標題都是當天正式站存進 watch_news（code 3443）的原文。"""
    STOCK = {"code": "3443", "name": "創意"}
    ABOUT = [
        "證交所：愛普、創意、景碩、金像電違約交割 總金額1.78億",
        "股后之爭再變天！創意一度跌停、川湖強彈逆襲 誰能坐穩？",
        "ASIC指標股不同調創意跌停失去股后寶座、世芯-KY、聯發科攜手漲停",
        "創意 今年業績將倍增(經濟日報，無內文)",
        "《半導體》ASIC遭血洗 創意連5日觸跌停、跌掉逾3000元",
        "創意今年業績將倍增 法人預估超過680億元",
    ]
    NOT_ABOUT = [
        "長庚大學校運會暨親子日熱鬧登場 千人路跑、19隊創意啦啦舞展現青春活力",
        "侯友宜新莊合體王珮宇車掃 盛讚年輕努力有創意僅缺知名度",
        "本校USR以賽代訓培育永續人才 青年學子發揮創意回應在地需求",
        "臺北客家義民嘉年華熱鬧登場客語展演美食創意展現客家魅力- 商情",
        "「美國月在楓康」登場！蘋果馬鈴薯限量搶購 五星主廚秀創意蔬果料理",
        "鼓勵孩子畫出無限夢想TOYOTA 2027全球夢想車創意繪畫大賽徵件開跑- 商情",
        "風城創意閃耀國際 2026新竹市國際風箏節奪美國繆思創意獎最高榮譽鉑金獎",
        "新北鐵道馬拉松接力賽開放報名追火車創意接棒",
    ]

    def test_common_word_name_needs_market_context(self):
        for title in self.ABOUT:
            self.assertTrue(wn._names_company(title, self.STOCK), title)
        for title in self.NOT_ABOUT:
            self.assertFalse(wn._names_company(title, self.STOCK), title)

    def test_code_in_title_is_enough(self):
        self.assertTrue(wn._names_company("創意（3443）發表新平台", self.STOCK))

    def test_distinctive_names_do_not_need_context(self):
        self.assertTrue(wn._names_company("台積電攜手夥伴發表新平台", {"code": "2330", "name": "台積電"}))
        self.assertTrue(wn._names_company("聯發科發表新平台", {"code": "2454", "name": "聯發科"}))


if __name__ == "__main__":
    unittest.main()
