"""J 票：更多新聞來源（本人 2026-11-19「還有很多新聞來源…專門在整理不同產業的消息，另外還要加入美國的前幾大報社」）。

寫預測、寫判斷之前直接抓官方 RSS（只拿標題、摘要與時間）；台灣產業新聞依公司名稱、代號與官方產業別對到關注股，
大盤只吃美國那幾家。預期值都是手算的固定例子。
"""
import io
import unittest
import urllib.error

import news_feeds as nf

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>Stocks slide as chip makers fall</title><link>https://www.cnbc.com/a</link>
<description><![CDATA[<p>The <b>S&amp;P 500</b> fell.</p>]]></description><pubDate>Wed, 18 Nov 2026 14:09:26 GMT</pubDate></item>
<item><title>Later story</title><link>https://www.cnbc.com/b</link><description>after</description>
<pubDate>Wed, 18 Nov 2026 13:30:00 GMT</pubDate></item>
</channel></rss>""".encode("utf-8")

CTEE = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>半導體設備交期拉長</title><link>https://www.ctee.com.tw/news/1</link><description>設備商表示…</description>
<pubDate>2026-11-18T22:11:16</pubDate></item>
<item><title>台玻擴產玻纖布</title><link>https://www.ctee.com.tw/news/2</link><description>1802 台玻宣布…</description>
<pubDate>2026-11-18T10:00:00</pubDate></item>
<item><title>航運運價回升</title><link>https://www.ctee.com.tw/news/3</link><description>貨櫃三雄…</description>
<pubDate>2026-11-18T09:00:00</pubDate></item>
</channel></rss>""".encode("utf-8")


class Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ParseTests(unittest.TestCase):
    def test_rss_items_with_plain_summaries_and_utc_times(self):
        rows = nf.parse_feed(RSS, "cnbc-markets")
        self.assertEqual(rows[0], {"source": "cnbc-markets", "link": "https://www.cnbc.com/a",
                                   "title": "Stocks slide as chip makers fall", "summary": "The S&P 500 fell.",
                                   "published_at": "2026-11-18T14:09:26Z"})

    def test_times_without_a_zone_are_taipei_time(self):
        rows = nf.parse_feed(CTEE, "ctee-industry")
        self.assertEqual(rows[0]["published_at"], "2026-11-18T14:11:16Z")   # 22:11:16 台北 ＝ 14:11:16Z


class FetchTests(unittest.TestCase):
    def test_each_feed_once_with_a_timeout_failures_are_named_and_not_retried(self):
        asked = []

        def opener(request, timeout):
            asked.append((request.full_url, timeout))
            if "nytimes" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b""))
            return Response(RSS)

        rows, errors = nf.fetch(("cnbc-markets", "nyt-business"), opener=opener, now="2026-11-18T20:00:00+08:00",
                                pause=lambda s: None)
        self.assertEqual([url for url, _ in asked], [nf.FEEDS["cnbc-markets"]["url"], nf.FEEDS["nyt-business"]["url"]])
        self.assertTrue(all(timeout == nf.TIMEOUT_SECONDS for _, timeout in asked))
        self.assertEqual(errors, {"nyt-business": "HTTPError"})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["fetched_at"] == "2026-11-18T12:00:00Z" for row in rows))


class MatchTests(unittest.TestCase):
    def test_company_name_or_code_first_then_the_official_industry(self):
        rows = nf.parse_feed(CTEE, "ctee-industry")
        company = nf.match_stock(rows, code="1802", name="台玻", industry="玻璃陶瓷")
        self.assertEqual([(row["title"], row["match"]) for row in company], [("台玻擴產玻纖布", "company")])
        industry = nf.match_stock(rows, code="2330", name="台積電", industry="半導體業")
        self.assertEqual([(row["title"], row["match"]) for row in industry], [("半導體設備交期拉長", "industry")])
        self.assertEqual(nf.match_stock(rows, code="2603", name="長榮", industry="航運業")[0]["match"], "industry")

    def test_industry_keywords_drop_the_generic_suffix(self):
        self.assertEqual(nf.industry_keywords("半導體業"), ["半導體"])
        self.assertEqual(nf.industry_keywords("電子零組件業"), ["電子零組件"])
        self.assertEqual(nf.industry_keywords("玻璃陶瓷"), ["玻璃陶瓷", "玻璃", "陶瓷"])
        self.assertEqual(nf.industry_keywords("其他業"), [])   # 太籠統，不拿來比對


class SourcesTests(unittest.TestCase):
    def test_market_uses_the_us_papers_and_watch_uses_the_taiwan_industry_feeds(self):
        self.assertEqual(set(nf.MARKET_FEEDS), {"cnbc-markets", "nyt-business", "wsj-markets"})
        self.assertEqual(set(nf.INDUSTRY_FEEDS), {"udn-industry", "ctee-industry", "cnyes-tw-stock"})
        for name in (*nf.MARKET_FEEDS, *nf.INDUSTRY_FEEDS):
            self.assertTrue(nf.FEEDS[name]["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
