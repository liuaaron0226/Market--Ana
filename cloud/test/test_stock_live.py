from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402


QUOTE = {
    "rtcode": "0000", "userDelay": 5000,
    "msgArray": [{"c": "2330", "ex": "tse", "d": "20261110", "tlong": "1794277320000", "z": "-",
                  "y": "2235", "o": "2265", "h": "2280", "l": "2255", "v": "14037", "trade": {"z": "2265"}}],
}


class FakeService:
    def snapshot(self, code: str, market: str):
        return {"ok": True, "environment": "local", "code": code, "market": market, "market_open": True,
                "live": True, "quote": {"price": 2265}, "candles": {"1": [], "5": [], "15": []}}


class StockLiveTest(unittest.TestCase):
    def test_quote_parser_uses_last_trade_when_z_is_missing(self):
        quote = live_server.parse_stock_quote(QUOTE, "2330", "tse")
        self.assertEqual(quote["price"], 2265)
        self.assertEqual(quote["previous_close"], 2235)
        self.assertEqual(quote["volume_shares"], 14037000)
        self.assertEqual(quote["data_time"], "2026-11-10T10:22:00+08:00")
        self.assertEqual(quote["data_date"], "2026-11-10")
        json.dumps(quote)

    def test_portfolio_price_lookup_opens_one_connection_per_database(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "history.sqlite"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE daily (date TEXT, code TEXT, close REAL)")
                connection.executemany("INSERT INTO daily VALUES ('2026-11-09', ?, ?)", [("2330", 600), ("1102", 45)])
            original = sqlite3.connect
            opened = []

            def tracked(*args, **kwargs):
                connection = original(*args, **kwargs)
                opened.append(connection)
                return connection

            try:
                with patch.object(live_server, "STOCK_HISTORY_DB", database), \
                        patch.object(live_server, "lookup_security", return_value=SimpleNamespace(market="上市")), \
                        patch.object(live_server.sqlite3, "connect", side_effect=tracked):
                    result = live_server._default_portfolio_prices(["2330", "1102"])
                self.assertEqual(set(result), {"2330", "1102"})
                self.assertEqual(len(opened), 1)
            finally:
                for connection in opened:
                    connection.close()

    def test_local_public_endpoint_returns_same_contract(self):
        server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        server.stock_live_service = FakeService()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/stocks/2330/live", timeout=5)
            body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(body["environment"], "local")
            self.assertTrue(body["live"])
            self.assertEqual(body["quote"]["price"], 2265)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
