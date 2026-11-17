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


class _FakeLoader:
    """worktree 沒有行情庫：0050 含息序列用三筆假的，其他股票沒有資料。"""

    def series(self, code, end):
        rows = [("2022-11-30", 1.0), ("2026-11-09", 3.0), ("2026-11-10", 3.1)] if code == "0050" else []
        return [p for p in rows if p[0] <= end], "元大台灣50", "上市"

    def benchmark_block(self, end):
        return {"code": "0050", "name": "元大台灣50", "dividends_included": True, "periods": [], "chart": []}


class LocalFundCompareTest(unittest.TestCase):
    """05 票：本機的比較 API 也收基金，回和雲端資料包同一個比較項目（fund_compare.compare_item）；舊路由退場。"""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        import benchmark_compare
        import fund_compare

        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name) / "cbc-usdtwd-2026-11-16.json").write_text(
            json.dumps({"rows": [["2022-11-30", 31.0], ["2026-11-16", 32.8]]}), encoding="utf-8")
        cls.patches = [mock.patch.object(live_server, "history_state", return_value=("2026-11-10", {})),
                       mock.patch.object(live_server, "lookup_security", return_value=None),
                       mock.patch.object(benchmark_compare, "Loader", _FakeLoader),
                       mock.patch.object(fund_compare, "RAW_DIR", Path(cls.tmp.name))]
        for patch in cls.patches:
            patch.start()
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
        for patch in cls.patches:
            patch.stop()
        cls.tmp.cleanup()

    def test_multi_compare_returns_the_fund_item(self) -> None:
        status, body = get_json(f"{self.base}/api/compare-0050?codes=BLKTENAU")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "compare-0050/v1")
        [item] = body["items"]
        self.assertEqual((item["code"], item["status"], item["kind"], item["market"]), ("BLKTENAU", "ok", "fund", "境外基金"))
        self.assertEqual([p["period"] for p in item["periods"]], ["1m", "3m", "1y", "3y", "5y", "since"])
        self.assertEqual(item["history"]["to"], "2026-11-10")   # 雙方都有的最後一天（假 0050 到 11-10）

    def test_single_compare_for_the_fund_page(self) -> None:
        status, body = get_json(f"{self.base}/api/stocks/BLKTENAU/vs-0050")
        self.assertEqual(status, 200)
        self.assertEqual((body["schema"], body["item"]["code"], body["item"]["kind"], body["benchmark"]["code"]),
                         ("vs-0050/v1", "BLKTENAU", "fund", "0050"))

    def test_old_fund_route_is_retired(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.base}/api/funds/BLKTENAU/vs-0050", timeout=30)
        self.assertEqual(caught.exception.code, 404)
        self.assertNotIn("貝萊德", caught.exception.read().decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
