"""J 票：富果（分時、均價線、五檔、內外盤比、60 分 K）、新聞標題與摘要、國外同類股交給 Gemini。

本人 2026-11-19 00:1x 原話：「富果、新聞來源、國外同類股都交給 Gemini 做詳細分析」（推翻 11-15、11-16 的授權限制）。
預期值都是手算的固定數字，算式寫在旁邊。富果回應照官方文件的形狀（cloud/lib/live-quotes.js、hourly-candles.js 同一套）。
"""
import unittest

import licensed_inputs as li

BASE = "2026-11-06"


def minute(hhmm, o, h, l, c, v, avg, day=BASE):
    return {"date": f"{day}T{hhmm}:00.000+08:00", "open": o, "high": h, "low": l, "close": c, "volume": v, "average": avg}


def candles(rows, day=BASE):
    return {"date": day, "symbol": "2330", "timeframe": "1", "data": rows}


# 固定的 1 分 K（量的單位是張）：
#   09:00 開 100 高 101 低 99 收 100 量 10 均 100.0
#   09:05 開 100 高 104 低 100 收 103 量 30 均 102.0
#   09:12 開 103 高 103 低 98  收 99  量 20 均 101.0
#   10:30 開 99  高 100 低 97  收 98  量 40 均 100.0
#   13:30 開 98  高 102 低 98  收 102 量 50 均 100.5
ROWS = [minute("09:00", 100, 101, 99, 100, 10, 100.0), minute("09:05", 100, 104, 100, 103, 30, 102.0),
        minute("09:12", 103, 103, 98, 99, 20, 101.0), minute("10:30", 99, 100, 97, 98, 40, 100.0),
        minute("13:30", 98, 102, 98, 102, 50, 100.5)]


def quote(day=BASE, at_ask=600, at_bid=400):
    return {"date": day, "symbol": "2330",
            "bids": [{"price": 101.5, "size": 12}, {"price": 101, "size": 30}, {"price": 100.5, "size": 8},
                     {"price": 100, "size": 50}, {"price": 99.5, "size": 5}, {"price": 99, "size": 9}],
            "asks": [{"price": 102, "size": 7}, {"price": 102.5, "size": 0}, {"price": 103, "size": 20}],
            "total": {"tradeVolume": 150, "tradeVolumeAtAsk": at_ask, "tradeVolumeAtBid": at_bid}}


class NotesTests(unittest.TestCase):
    def test_notes_say_how_to_name_times_without_numbers(self):
        # 理由不得有數字：分時、60 分 K、新聞裡的時刻與數字都不能照抄，時段改用早盤、盤中、尾盤
        for name in ("stock_intraday", "index_intraday", "headlines"):
            self.assertIn("不得寫出", li.NOTES[name])
        self.assertIn("尾盤", li.NOTES["stock_intraday"])


class MinutesTests(unittest.TestCase):
    def test_summary_of_the_day_with_the_average_line(self):
        summary = li.intraday_summary(li.minute_rows(candles(ROWS), BASE))
        self.assertEqual(summary["open"], 100)
        self.assertEqual((summary["high"], summary["high_time"]), (104, "09:05"))
        self.assertEqual((summary["low"], summary["low_time"]), (97, "10:30"))
        self.assertEqual(summary["close"], 102)
        self.assertEqual(summary["volume_lots"], 150)            # 10 + 30 + 20 + 40 + 50
        self.assertEqual(summary["average"], 100.5)              # 最後一根的累計均價
        self.assertEqual(summary["close_vs_average_pct"], 1.49)  # 102 ÷ 100.5 − 1 ＝ 1.4925%

    def test_ten_minute_bars(self):
        bars = li.intraday_summary(li.minute_rows(candles(ROWS), BASE))["bars_10min"]
        # 09:00～09:09 兩根合一根；09:12 自成 09:10；10:30、13:30 各一根
        self.assertEqual(bars[0], {"time": "09:00", "open": 100, "high": 104, "low": 99, "close": 103,
                                   "volume_lots": 40, "average": 102.0})
        self.assertEqual([bar["time"] for bar in bars], ["09:00", "09:10", "10:30", "13:30"])

    def test_hourly_bars_from_minutes(self):
        hours = li.hourly_from_minutes(li.minute_rows(candles(ROWS), BASE))
        self.assertEqual(hours[0], {"date": BASE, "time": "09:00", "open": 100, "high": 104, "low": 98, "close": 99,
                                    "volume_lots": 60})
        self.assertEqual([h["time"] for h in hours], ["09:00", "10:00", "13:00"])

    def test_another_day_is_refused(self):
        # 晨間版 07:20 跑：富果還是基準日那一天；要是回的是別天（例如開盤後的新一天），整塊不給
        self.assertIsNone(li.minute_rows(candles(ROWS, day="2026-11-09"), BASE))


class BookTests(unittest.TestCase):
    def test_five_levels_and_the_outer_share(self):
        book = li.book_from_quote(quote(), BASE)
        self.assertEqual(book["bids"], [[101.5, 12], [101, 30], [100.5, 8], [100, 50], [99.5, 5]])   # 只取五檔
        self.assertEqual(book["asks"], [[102, 7], [103, 20]])                                     # 量是 0 的不收
        # 外盤（成交在賣價，主動買）600、內盤（成交在買價，主動賣）400 → 外盤比 600 ÷ 1000 ＝ 60%
        self.assertEqual(book["flow"], {"outer_lots": 600, "inner_lots": 400, "outer_pct": 60.0})

    def test_another_day_or_no_flow(self):
        self.assertIsNone(li.book_from_quote(quote(day="2026-11-09"), BASE))
        self.assertIsNone(li.book_from_quote({**quote(), "total": {}}, BASE)["flow"])


class StockLayerTests(unittest.TestCase):
    def test_layer_has_each_part_with_its_time_and_previous_hours(self):
        history = {"data": [{"date": "2026-11-05T13:00:00.000+08:00", "open": 95, "high": 96, "low": 94, "close": 96,
                             "volume": 70},
                            {"date": "2026-11-06T09:00:00.000+08:00", "open": 1, "high": 1, "low": 1, "close": 1,
                             "volume": 1}]}   # 基準日那幾根以 1 分 K 合成的為準，歷史裡同一天的不用
        layer = li.stock_intraday_layer(candles(ROWS), quote(), history, BASE)
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["data_date"], BASE)
        self.assertEqual(layer["missing"], [])
        self.assertEqual(layer["hourly"][0], {"date": "2026-11-05", "time": "13:00", "open": 95, "high": 96, "low": 94,
                                              "close": 96, "volume_lots": 70})
        self.assertEqual(layer["hourly"][1]["date"], BASE)
        self.assertEqual(layer["book"]["flow"]["outer_pct"], 60.0)
        self.assertIn("note", layer)

    def test_parts_that_are_missing_are_named_and_all_missing_is_missing(self):
        layer = li.stock_intraday_layer(candles(ROWS), None, None, BASE)
        self.assertEqual(layer["missing"], ["book", "hourly_history"])
        self.assertEqual(li.stock_intraday_layer(None, None, None, BASE)["status"], "missing")


class IndexLayerTests(unittest.TestCase):
    def test_index_has_price_path_and_hours_but_no_book(self):
        rows = [{**row, "volume": None, "average": None} for row in ROWS]
        layer = li.index_intraday_layer(candles(rows), BASE)
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["summary"]["close"], 102)
        self.assertNotIn("book", layer)
        self.assertIsNone(layer["summary"]["average"])
        self.assertEqual(len(layer["hourly"]), 3)

    def test_index_gets_earlier_hours_from_the_history_without_volume(self):
        # 富果文件（2026-11-19 讀）：歷史分 K 支援 IX0001；指數分 K 的量是成交金額，所以不給量
        rows = [{**row, "volume": None, "average": None} for row in ROWS]
        history = {"data": [{"date": "2026-11-05T13:00:00.000+08:00", "open": 95, "high": 96, "low": 94, "close": 96,
                             "volume": 123456789}]}
        layer = li.index_intraday_layer(candles(rows), BASE, history)
        self.assertEqual(layer["hourly"][0], {"date": "2026-11-05", "time": "13:00", "open": 95, "high": 96, "low": 94,
                                              "close": 96})
        self.assertEqual(len(layer["hourly"]), 4)


class HeadlineTests(unittest.TestCase):
    ROWS = [
        {"link": "https://tw.stock.yahoo.com/a", "source": "yahoo", "title": "台積電法說",
         "summary": "內容" * 150, "published_at": "2026-11-06T05:00:00Z", "fetched_at": "2026-11-06T09:00:00Z"},
        {"link": "https://www.cna.com.tw/b", "source": "cna-finance", "title": "外資賣超",
         "summary": "摘要", "published_at": "2026-11-06T08:00:00Z", "fetched_at": "2026-11-06T09:00:00Z"},
        {"link": "https://www.cna.com.tw/late", "source": "cna-finance", "title": "寫判斷之後才抓到",
         "summary": "", "published_at": "2026-11-06T08:30:00Z", "fetched_at": "2026-11-06T13:00:00Z"},
        {"link": "https://www.cna.com.tw/old", "source": "cna-intworld", "title": "太舊",
         "summary": "", "published_at": "2026-11-01T08:00:00Z", "fetched_at": "2026-11-01T09:00:00Z"},
    ]

    def test_only_what_existed_before_the_cutoff_newest_first_with_short_summaries(self):
        # 截止 2026-11-06 20:00（台北）＝ 12:00Z；往前 3 天
        items = li.headline_items(self.ROWS, cutoff="2026-11-06T20:00:00+08:00")
        self.assertEqual([item["title"] for item in items], ["外資賣超", "台積電法說"])
        self.assertEqual(items[0]["source"], "中央社")
        self.assertEqual(items[0]["published_at"], "2026-11-06T16:00:00+08:00")
        self.assertEqual(len(items[1]["summary"]), li.SUMMARY_MAX)   # 只給摘要，不給全文
        self.assertTrue(items[0]["id"].startswith("h_"))
        self.assertNotIn("link", items[0])

    def test_nothing_is_none(self):
        self.assertIsNone(li.headline_items([], cutoff="2026-11-06T20:00:00+08:00"))


class PeersTests(unittest.TestCase):
    QUOTES = [{"symbol": "NVDA", "date": d, "close": c, "fetched_at": "2026-11-06T00:00:00Z"} for d, c in
              (("2026-10-29", 100.0), ("2026-10-30", 101.0), ("2026-10-31", 102.0), ("2026-11-03", 103.0),
               ("2026-11-04", 104.0), ("2026-11-05", 110.0))] + [
              {"symbol": "NVDA", "date": "2026-11-06", "close": 999.0, "fetched_at": "2026-11-06T22:00:00Z"}]

    def test_last_us_close_before_the_cutoff_with_one_and_five_day_changes(self):
        peers = [{"symbol": "NVDA", "name": "NVIDIA", "market": "US"}]
        layer = li.peers_layer(peers, self.QUOTES, before="2026-11-06", cutoff="2026-11-06T20:00:00+08:00")
        # 晚間版只看基準日以前（美國日期 < 11-06）的收盤：11-05 收 110；1 日 110 ÷ 104 − 1 ＝ 5.77%；
        # 5 日 110 ÷ 100 − 1 ＝ 10%
        self.assertEqual(layer["peers"], [{"symbol": "NVDA", "name": "NVIDIA", "market": "US", "date": "2026-11-05",
                                           "close": 110.0, "change_1d_pct": 5.77, "change_5d_pct": 10.0}])

    def test_peers_without_quotes_are_listed_and_none_with_data_is_none(self):
        both = [{"symbol": "NVDA", "name": "NVIDIA", "market": "US"}, {"symbol": "SKH", "name": "SK", "market": "KR"}]
        layer = li.peers_layer(both, self.QUOTES, before="2026-11-06", cutoff="2026-11-06T20:00:00+08:00")
        self.assertEqual((layer["status"], [peer["symbol"] for peer in layer["peers"]], layer["no_data"]),
                         ("ok", ["NVDA"], ["SKH"]))
        # 每一檔都沒有資料、或根本沒有同類股：整層缺（呼叫端記進 missing_layers）
        self.assertIsNone(li.peers_layer(both[1:], self.QUOTES, before="2026-11-06", cutoff="2026-11-06T20:00:00+08:00"))
        self.assertIsNone(li.peers_layer([], self.QUOTES, before="2026-11-06", cutoff="2026-11-06T20:00:00+08:00"))

    def test_a_malformed_fugle_answer_is_missing_not_an_error(self):
        class Broken:
            def candles(self, *args):
                return {"date": BASE, "data": [{"date": BASE, "open": 1, "high": 1, "low": 1, "close": 1}, "壞的"]}
            quote = hourly = candles
        layer, errors = li.fetch_stock_intraday(Broken(), "2330", BASE)
        self.assertEqual(layer["status"], "missing")
        index, _ = li.fetch_index_intraday(Broken(), BASE)
        self.assertEqual(index["status"], "missing")


if __name__ == "__main__":
    unittest.main()


class FugleQuotaTests(unittest.TestCase):
    """富果的每分鐘額度可能和網站共用（11-19 owner 轉述）：呼叫之間隔一段時間；遇到 429 這一輪就不再打，也不重試。"""

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def test_calls_are_spaced_and_a_429_stops_the_rest_of_the_round(self):
        import io
        import urllib.error
        clock = {"now": 100.0}
        slept, asked = [], []

        def opener(request, timeout):
            asked.append(request.full_url)
            if len(asked) == 2:
                raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b""))
            return self.Response(b'{"date": "2026-11-06", "data": []}')

        fugle = li.Fugle("KEY", opener=opener, min_interval=2.0, clock=lambda: clock["now"],
                         sleep=lambda s: (slept.append(s), clock.__setitem__("now", clock["now"] + s)))
        fugle.candles("2330")
        with self.assertRaises(Exception):
            fugle.quote("2330")                       # 第二次回 429
        with self.assertRaises(li.FugleExhausted):
            fugle.hourly("2330", "2026-10-23", "2026-11-05")   # 之後同一輪不再打
        self.assertEqual(len(asked), 2)
        self.assertEqual(slept, [2.0])                # 第二次之前等了 2 秒（第一次不用等）

    def test_exhaustion_makes_every_later_stock_missing_without_calls(self):
        class Exhausted:
            def candles(self, *args):
                raise li.FugleExhausted("429")
            quote = hourly = candles
        layer, errors = li.fetch_stock_intraday(Exhausted(), "2330", BASE)
        self.assertEqual(layer["status"], "missing")
        self.assertEqual(set(errors.values()), {"FugleExhausted"})
