"""基金當成一般個股 07 票（本人 2026-11-17／18 定案）：自選股與庫存收基金——資料端與本機 live_server。

- 每日建置：基金分析多帶最新匯率（fx），雲端庫存用「淨值×匯率」算每單位台幣價格（cloud/lib/user-portfolio.js）。
  匯率取資料日（含）以前最新的一筆央行美元兌台幣收盤，資料日之後的不用。
- 本機 live_server：自選與庫存和雲端同契約——基金是一般的一列；庫存以單位數＋台幣總成本新增與修改；
  回傳現值＝單位數×淨值×匯率、台幣賺賠、淨值日與匯率日。
- 本機自選／庫存資料庫（只套 0005、0006 建表）放寬成收 4～12 碼代碼與市場「境外基金」，既有資料保留。
期待值用固定的淨值與匯率手算，不用程式同一個算法重算。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import fund_compare  # noqa: E402
import fund_stock  # noqa: E402
import live_server  # noqa: E402
from cloud import build_stock_pack as build  # noqa: E402

EMPTY_RAW = Path(tempfile.mkdtemp(prefix="fund-raw-empty-"))
REAL = partial(fund_compare.load_history, raw_dir=EMPTY_RAW)
FX = [("2026-11-14", 32.1), ("2026-11-17", 32.4), ("2026-11-19", 33.0)]


def with_fx(fx):
    def loader(code: str, *, as_of: str) -> dict:
        return {**REAL(code, as_of=as_of), "fx": list(fx)}
    return loader


class FundAnalysisFxTests(unittest.TestCase):
    def test_analysis_carries_the_latest_fx_on_or_before_the_data_date(self) -> None:
        _, analyses, _ = fund_stock.fund_rows("2026-11-18", loader=with_fx(FX))
        payload = analyses[0]["payload"]
        self.assertEqual(payload["fx"], {"pair": "USD/TWD", "rate": 32.4, "date": "2026-11-17", "source": "中央銀行"})
        build._check_payload("BLKTENAU", payload)

    def test_no_fx_means_no_fx_and_no_made_up_number(self) -> None:
        _, analyses, _ = fund_stock.fund_rows("2026-11-18", loader=with_fx([]))
        self.assertIsNone(analyses[0]["payload"]["fx"])
        _, analyses, _ = fund_stock.fund_rows("2026-11-18", loader=with_fx([("2026-11-19", 33.0)]))
        self.assertIsNone(analyses[0]["payload"]["fx"], "資料日之後的匯率不用")

    def test_twd_quote_is_nav_times_fx(self) -> None:
        payload = {"currency": "USD", "quote": {"close": 21.12, "nav_date": "2026-11-12"},
                   "fx": {"rate": 32.5, "date": "2026-11-17"}}
        quote = fund_stock.twd_quote(payload)
        self.assertAlmostEqual(quote.pop("price"), 686.4, places=9)
        self.assertEqual(quote, {"as_of": "2026-11-12", "source": "本站淨值×匯率", "nav": 21.12, "currency": "USD",
                                 "nav_date": "2026-11-12", "fx_rate": 32.5, "fx_date": "2026-11-17"})
        self.assertIsNone(fund_stock.twd_quote({**payload, "fx": None}))
        self.assertIsNone(fund_stock.twd_quote(None))


FUND_QUOTE = {"price": 21.12 * 32.5, "as_of": "2026-11-12", "source": "本站淨值×匯率", "nav": 21.12, "currency": "USD",
              "nav_date": "2026-11-12", "fx_rate": 32.5, "fx_date": "2026-11-17"}


class LocalFundPortfolioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        self.server.portfolio_db = Path(self.temp.name) / "portfolio.sqlite"
        securities = {"2330": {"code": "2330", "name": "台積電", "market": "上市", "in_universe": True}}
        self.server.portfolio_resolver = lambda code: securities.get(code) or live_server._fund_security(code)
        self.prices = {"2330": {"price": 600.0, "as_of": "2026-11-17", "source": "測試盤後資料"}, "BLKTENAU": FUND_QUOTE}
        self.server.portfolio_prices = lambda codes: {code: self.prices[code] for code in codes if code in self.prices}
        self.server.strategies_enabled = True
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
        headers = {"Origin": self.base, **({"Content-Type": "application/json"} if data is not None else {})}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)

    def fund_item(self) -> dict | None:
        _, body = self.request("/api/holdings")
        return next((item for item in body["items"] if item["code"] == "BLKTENAU"), None)

    def test_fund_joins_the_watchlist_as_an_ordinary_row(self) -> None:
        status, body = self.request("/api/watchlist", "POST", {"code": "blktenau"})
        self.assertEqual(status, 201)
        self.assertEqual((body["code"], body["name"], body["market"], body["in_universe"]),
                         ("BLKTENAU", "貝萊德世界科技 A10", "境外基金", True))
        _, listed = self.request("/api/watchlist")
        self.assertEqual([(item["code"], item["market"]) for item in listed["items"]], [("BLKTENAU", "境外基金")])

    def test_fund_holding_with_units_and_twd_total_cost(self) -> None:
        status, body = self.request("/api/holdings", "POST",
                                    {"code": "BLKTENAU", "shares": 123.4567, "total_cost_twd": 80000, "note": "對帳單"})
        self.assertEqual(status, 201)
        self.assertEqual((body["shares"], body["total_cost_twd"], body["kind"]), (123.4567, 80000, "fund"))
        item = self.fund_item()
        # 21.12 × 32.5 ＝ 686.4 元／單位；123.4567 × 686.4 ＝ 84,740.68；成本 80,000 → 賺 4,740.68（5.93%）
        self.assertEqual((item["shares"], item["kind"], item["total_cost_twd"], item["cost_basis"]), (123.4567, "fund", 80000, 80000))
        self.assertEqual((item["latest_price"], item["market_value"], item["unrealized_pnl"], item["return_pct"]),
                         (686.4, 84740.68, 4740.68, 5.93))
        self.assertEqual((item["nav"], item["currency"], item["nav_date"], item["fx_rate"], item["fx_date"]),
                         (21.12, "USD", "2026-11-12", 32.5, "2026-11-17"))
        self.assertEqual((item["price_as_of"], item["price_source"], item["price_stale"]), ("2026-11-12", "本站淨值×匯率", False))

        status, _ = self.request("/api/holdings", "POST", {"code": "BLKTENAU", "shares": 200.5, "total_cost_twd": 130000.5})
        self.assertEqual(status, 200)
        item = self.fund_item()
        self.assertEqual((item["shares"], item["total_cost_twd"], item["market_value"], item["unrealized_pnl"], item["return_pct"]),
                         (200.5, 130000.5, 137623.2, 7622.7, 5.86))

    def test_fund_holding_checks_and_missing_fx(self) -> None:
        for payload, message in [
            ({"shares": 10, "average_cost": 600}, "基金請填單位數與台幣總成本"),
            ({"shares": 0, "total_cost_twd": 1000}, "單位數須大於 0 且不超過 1,000,000,000"),
            ({"shares": 10, "total_cost_twd": 0}, "台幣總成本須大於 0 且不超過 1,000,000,000,000"),
        ]:
            status, body = self.request("/api/holdings", "POST", {"code": "BLKTENAU", **payload})
            self.assertEqual((status, body.get("message") or body.get("error")), (400, message), payload)
        del self.prices["BLKTENAU"]
        self.request("/api/holdings", "POST", {"code": "BLKTENAU", "shares": 10, "total_cost_twd": 6000})
        item = self.fund_item()
        self.assertEqual((item["latest_price"], item["market_value"], item["total_cost_twd"]), (None, None, 6000))

    def test_stock_holdings_unchanged(self) -> None:
        status, _ = self.request("/api/holdings", "POST", {"code": "2330", "shares": 1.5, "average_cost": 500})
        self.assertEqual(status, 400)
        self.request("/api/holdings", "POST", {"code": "2330", "shares": 10, "average_cost": 500})
        _, body = self.request("/api/holdings")
        item = body["items"][0]
        self.assertEqual((item["shares"], item["market_value"], item["cost_basis"]), (10, 6000.0, 5000.0))
        self.assertNotIn("kind", item)


class LocalPortfolioSchemaWideningTest(unittest.TestCase):
    """舊的本機資料庫（0005 原樣：代號 4～6 碼、市場只收上市上櫃）打開後放寬，原本的資料還在。"""

    def test_old_local_database_is_widened_and_keeps_rows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portfolio.sqlite"
            with sqlite3.connect(path) as connection:
                for name in ("0005_user_portfolio.sql", "0006_cash_and_level.sql"):
                    connection.executescript((ROOT / "cloud/migrations" / name).read_text(encoding="utf-8"))
                connection.execute("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at) "
                                   "VALUES ('local', '2330', '台積電', '上市', 'long', 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')")
                connection.execute("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) "
                                   "VALUES ('local', '2330', '台積電', '上市', 1000, 500, 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')")
                connection.execute("INSERT INTO user_search_history (email, code, name, market, viewed_at) "
                                   "VALUES ('local', '2330', '台積電', '上市', '2026-11-01T00:00:00Z')")
            connection.close()
            live_server._PORTFOLIO_SCHEMA_READY.discard(str(path.resolve()))
            store = live_server.LocalPortfolioStore(path, resolver=live_server._fund_security,
                                                    prices=lambda codes: {"BLKTENAU": FUND_QUOTE})
            self.assertEqual([item["code"] for item in store.list_watchlist()["items"]], ["2330"])
            self.assertEqual(store.save_watchlist({"code": "BLKTENAU"})[0], 201)
            self.assertEqual(store.save_holding({"code": "BLKTENAU", "shares": 1.5, "total_cost_twd": 900})[0], 201)
            self.assertEqual(store.save_history({"code": "BLKTENAU"})[0], 201)
            self.assertEqual(sorted(item["code"] for item in store.list_watchlist()["items"]), ["2330", "BLKTENAU"])
            holdings = {item["code"]: item for item in store.list_holdings()["items"]}
            self.assertEqual((holdings["2330"]["shares"], holdings["2330"]["average_cost"]), (1000, 500.0))
            self.assertEqual(holdings["BLKTENAU"]["shares"], 1.5)
            self.assertEqual(sorted(item["code"] for item in store.list_history()["items"]), ["2330", "BLKTENAU"])
            # 上限觸發器與索引還在
            with sqlite3.connect(path) as connection:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger', 'index')")}
            connection.close()
            self.assertLessEqual({"user_watchlist_limit", "user_holdings_limit", "user_watchlist_recent",
                                  "user_holdings_recent", "user_search_history_recent"}, names)
            # 再打開一次不重做、不壞
            live_server._PORTFOLIO_SCHEMA_READY.discard(str(path.resolve()))
            again = live_server.LocalPortfolioStore(path, resolver=live_server._fund_security, prices=lambda codes: {})
            self.assertEqual(len(again.list_watchlist()["items"]), 2)


if __name__ == "__main__":
    unittest.main()
