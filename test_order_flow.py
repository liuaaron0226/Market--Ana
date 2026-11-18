"""J 票：大盤委買委賣（本人 2026-11-19 題 1「要找」，同意新增這一個對外抓取）。

證交所每 5 秒委託成交統計（MI_5MINS）：累積委買、委賣的筆數與張數，累積成交筆數、張數與金額。預期值都是手算的。
累積委託張數在盤中會變少（撤單、改單扣掉），所以三段用「每段結束那一刻」的比值與它比前一段的變化，不相減；
成交張數只增不減，各段用相減。
"""
import io
import json
import unittest
import urllib.error

import order_flow as of

FIELDS = ["時間", "累積委託買進筆數", "累積委託買進數量", "累積委託賣出筆數", "累積委託賣出數量", "累積成交筆數",
          "累積成交數量", "累積成交金額"]


def payload(day, rows):
    """rows：[(時間, 委買筆, 委買張, 委賣筆, 委賣張, 成交筆, 成交張, 成交金額)]，照官方寫成帶千分位的字串。"""
    return {"stat": "OK", "date": day.replace("-", ""), "fields": FIELDS,
            "data": [[time, *[f"{value:,}" for value in values]] for time, *values in rows]}


BASE = "2026-11-18"
# 09:00 開盤前；10:00 早盤結束；12:00 盤中結束；13:30 收盤
DAY = payload(BASE, [
    ("09:00:00", 1000, 10000, 500, 4000, 0, 0, 0),
    ("10:00:00", 3000, 30000, 2000, 20000, 400, 6000, 300),
    ("12:00:00", 5000, 36000, 4000, 30000, 900, 11000, 600),
    ("13:30:00", 6000, 40000, 5000, 32000, 1200, 15000, 900),
])


class SummaryTests(unittest.TestCase):
    def test_day_ratios_and_the_three_segments(self):
        summary = of.day_summary(of.parse(DAY, BASE))
        # 全天（收盤那一刻）：委買／委賣張數 40000 ÷ 32000 ＝ 1.25；筆數 6000 ÷ 5000 ＝ 1.2
        self.assertEqual((summary["qty_ratio"], summary["count_ratio"]), (1.25, 1.2))
        self.assertEqual(summary["traded_lots"], 15000)
        # 三段結束那一刻的張數比：10:00 30000÷20000＝1.5；12:00 36000÷30000＝1.2；13:30 1.25
        # 變化：早盤比開盤前（10000÷4000＝2.5）−1.0；盤中 −0.3；尾盤 +0.05。成交張數各段 6000、5000、4000
        self.assertEqual(summary["segments"], [
            {"segment": "早盤", "until": "10:00", "qty_ratio": 1.5, "qty_ratio_change": -1.0, "count_ratio": 1.5,
             "traded_lots": 6000},
            {"segment": "盤中", "until": "12:00", "qty_ratio": 1.2, "qty_ratio_change": -0.3, "count_ratio": 1.25,
             "traded_lots": 5000},
            {"segment": "尾盤", "until": "13:30", "qty_ratio": 1.25, "qty_ratio_change": 0.05, "count_ratio": 1.2,
             "traded_lots": 4000},
        ])

    def test_another_day_or_no_rows_is_none(self):
        self.assertIsNone(of.parse(payload("2026-11-19", [("13:30:00", 1, 1, 1, 1, 1, 1, 1)]), BASE))
        self.assertIsNone(of.parse({"stat": "很抱歉，沒有符合條件的資料!"}, BASE))


class LayerTests(unittest.TestCase):
    def test_layer_compares_with_earlier_days(self):
        earlier = [(day, of.parse(payload(day, [("13:30:00", 100, 1000, 100, 1000, 10, 100, 10)]), day))
                   for day in ("2026-11-13", "2026-11-14", "2026-11-17")]   # 每天張數比 1.0、筆數比 1.0
        layer = of.order_flow_layer(of.parse(DAY, BASE), earlier, BASE)
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["data_date"], BASE)
        self.assertEqual(layer["earlier"], [{"date": d, "qty_ratio": 1.0, "count_ratio": 1.0}
                                            for d in ("2026-11-13", "2026-11-14", "2026-11-17")])
        # 和前三天平均比：張數比 1.25 − 1.0 ＝ +0.25；筆數比 +0.2
        self.assertEqual((layer["vs_earlier"]["qty_ratio_change"], layer["vs_earlier"]["count_ratio_change"]), (0.25, 0.2))
        self.assertIn("不能單獨決定方向", layer["note"])

    def test_no_base_day_is_missing_and_missing_earlier_days_are_named(self):
        self.assertEqual(of.order_flow_layer(None, [], BASE)["status"], "missing")
        layer = of.order_flow_layer(of.parse(DAY, BASE), [("2026-11-17", None)], BASE)
        self.assertEqual((layer["earlier"], layer["earlier_missing"]), ([], ["2026-11-17"]))
        self.assertIsNone(layer["vs_earlier"])


class Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FetchTests(unittest.TestCase):
    def test_one_request_per_day_spaced_with_a_timeout_no_retry_and_cached(self):
        asked, slept = [], []

        def opener(request, timeout):
            asked.append((request.full_url, timeout))
            if "20261117" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "x", {}, io.BytesIO(b""))
            day = request.full_url.split("date=")[1][:8]
            return Response(json.dumps(payload(f"{day[:4]}-{day[4:6]}-{day[6:]}",
                                               [("13:30:00", 1, 1, 1, 1, 1, 1, 1)])).encode("utf-8"))

        stats = of.TwseOrderStats(opener=opener, pause=slept.append)
        layer, errors = stats.layer(BASE, ["2026-11-14", "2026-11-17"])
        self.assertEqual([url.split("date=")[1][:8] for url, _ in asked], ["20261118", "20261114", "20261117"])
        self.assertTrue(all(timeout == of.TIMEOUT_SECONDS for _, timeout in asked))
        self.assertEqual(slept, [of.PAUSE_SECONDS, of.PAUSE_SECONDS])
        self.assertEqual(errors, {"2026-11-17": "HTTPError"})
        self.assertEqual(layer["earlier_missing"], ["2026-11-17"])
        stats.layer(BASE, ["2026-11-14"])   # 同一天不再打（重播早晚兩版、隔天又當前幾天）
        self.assertEqual(len(asked), 3)


if __name__ == "__main__":
    unittest.main()
