from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402


class LivePortfolioApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        self.server.portfolio_db = Path(self.temp.name) / "portfolio.sqlite"
        securities = {
            "2330": {"code": "2330", "name": "台積電", "market": "上市", "in_universe": True},
            "1101": {"code": "1101", "name": "台泥", "market": "上市", "in_universe": False},
        }
        self.server.portfolio_resolver = lambda code: securities.get(code)
        self.server.portfolio_prices = lambda codes: {
            code: {"price": 600.0 if code == "2330" else 40.0, "as_of": "2026-11-09", "source": "測試盤後資料"}
            for code in codes
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path: str, method: str = "GET", payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Origin": self.base}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        body = json.loads(raw) if raw else None
        return response.status, body

    def test_local_api_persists_holdings_and_calculates_profit(self):
        status, _ = self.request("/api/holdings", "POST", {
            "code": "2330", "shares": 1000, "average_cost": 500, "note": "核心部位",
        })
        self.assertEqual(status, 201)
        status, body = self.request("/api/holdings")
        self.assertEqual(status, 200)
        item = body["items"][0]
        self.assertEqual(item["market_value"], 600000)
        self.assertEqual(item["unrealized_pnl"], 100000)
        self.assertEqual(item["return_pct"], 20)
        self.assertEqual(item["price_as_of"], "2026-11-09")
        self.assertFalse(item["price_stale"])

    def test_spoofed_host_cannot_bypass_local_same_origin_check(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("GET", "/api/holdings", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.endheaders()
        read_response = connection.getresponse()
        read_response.read()
        connection.close()
        self.assertEqual(read_response.status, 403)

        body = json.dumps({"code": "2330", "horizon": "short"}).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("POST", "/api/watchlist", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.putheader("Origin", "http://evil.example")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
