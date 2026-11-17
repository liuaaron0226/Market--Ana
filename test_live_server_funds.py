"""基金當成一般個股 03 票：本機 live_server 對基金給和雲端一樣的契約（規格「本機版」；本機沒有開關，一律看得到）。

搜尋回一般的一筆（類別基金、市場境外基金）；個股入口回 kind fund 的分析；長日線回淨值序列。
股票名錄在 worktree 沒有（data/ 只在主檢出），這裡把股票那一半換成空的，只驗基金那一半。
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

import live_server


def get_json(url: str) -> tuple[int, dict]:
    try:
        response = urllib.request.urlopen(url, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


class LocalFundContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patch = mock.patch.object(live_server, "search_securities", return_value=([], None))
        cls.patch.start()
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.server.universe_enabled = False
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.patch.stop()

    def test_search_returns_the_fund_as_an_ordinary_item_without_funds_field(self) -> None:
        for query in ["貝萊德", "a10", "BLKTENAU"]:
            status, body = get_json(f"{self.base}/api/stocks/search?{urllib.parse.urlencode({'q': query})}")
            self.assertEqual(status, 200)
            self.assertEqual([(i["code"], i["security_type"], i["market"]) for i in body["items"]],
                             [("BLKTENAU", "基金", "境外基金")], query)
            self.assertNotIn("funds", body)

    def test_analysis_is_the_fund_contract(self) -> None:
        status, body = get_json(f"{self.base}/api/stocks/BLKTENAU/analysis")
        self.assertEqual(status, 200)
        self.assertEqual((body["schema"], body["kind"], body["market"], body["currency"]),
                         ("stock-analysis/v2", "fund", "境外基金", "USD"))
        self.assertEqual(body["quote"]["close"], body["bars"][-1]["close"])
        self.assertEqual(body["quote"]["nav_date"], body["bars"][-1]["date"])
        self.assertIn(body["analysis_status"], ("available", "stale"))

    def test_analysis_carries_the_same_distributions_field_as_the_cloud(self) -> None:
        # 06 票「配息」：本機和每日建置同一個 fund_stock.fund_rows，配息欄位同形狀（最近一次、各年、本金註記）
        _, body = get_json(f"{self.base}/api/stocks/BLKTENAU/analysis?as_of=2026-11-18")
        dist = body["distributions"]
        self.assertEqual((dist["currency"], dist["latest"]["ex_date"], dist["latest"]["amount"]), ("USD", "2026-10-30", 0.162))
        self.assertEqual([(y["year"], y["count"]) for y in dist["years"]][-2:], [("2023", 12), ("2022", 1)])
        self.assertIn("2023 年 12 次配息中 11 次全部來自本金", dist["capital_note"])

    def test_analysis_as_of_uses_only_navs_up_to_that_day(self) -> None:
        # 回補原件：2026-11-13 是 17.73（11-14、11-15 是週末，下一筆 11-16）
        status, body = get_json(f"{self.base}/api/stocks/BLKTENAU/analysis?as_of=2026-11-14")
        self.assertEqual(status, 200)
        self.assertEqual((body["quote"]["nav_date"], body["quote"]["close"]), ("2026-11-13", 17.73))

    def test_history_is_the_nav_series_and_stocks_have_none_locally(self) -> None:
        status, body = get_json(f"{self.base}/api/stocks/BLKTENAU/history")
        self.assertEqual(status, 200)
        self.assertEqual((body["schema"], body["d"][0], body["c"][0]), ("stock-history/v1", 20221201, 10.52))
        self.assertNotIn("o", body)
        _, analysis = get_json(f"{self.base}/api/stocks/BLKTENAU/analysis")
        self.assertEqual(str(body["d"][-1]), analysis["quote"]["nav_date"].replace("-", ""))
        status, body = get_json(f"{self.base}/api/stocks/2330/history")
        self.assertEqual((status, body["error"]), (404, "no-history"))


if __name__ == "__main__":
    unittest.main()
