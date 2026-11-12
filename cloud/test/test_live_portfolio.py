from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

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
        headers = {"Origin": self.base}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=15)
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

    def test_local_cash_and_level_match_cloud_contract(self):
        self.assertEqual(self.request("/api/holdings", "POST", {
            "code": "2330", "shares": 1000, "average_cost": 500,
        })[0], 201)
        self.assertEqual(self.request("/api/holdings/cash", "POST", {"amount_twd": 400000})[0], 201)
        store = live_server.LocalPortfolioStore(self.server.portfolio_db, self.server.portfolio_resolver,
                                                self.server.portfolio_prices)
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO level_daily (data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at, is_current) "
                "VALUES (?, 1, 'cash-level-v2.1', ?, ?, ?, ?, 1)",
                ("2026-11-10", "2026-11-10T08:30:00+08:00", "a" * 64,
                 json.dumps({"recommended_level": 80, "score": {"total": 4, "groups": {}},
                             "state": {"internal": "not-for-api"}, "source_manifest": {"files": []}}),
                 "2026-11-10T00:00:00Z"),
            )
        status, body = self.request("/api/holdings/level")
        self.assertEqual(status, 200)
        self.assertEqual(body["current"]["level"], 60)
        self.assertEqual(body["adjustment"]["amount_twd"], 200000)
        self.assertEqual(body["adjustment"]["estimated_commission_twd"], 285)
        self.assertNotIn("state", body["advice"])
        self.assertNotIn("source_manifest", body["advice"])

    def test_exactly_five_percentage_points_is_within_tolerance(self):
        self.server.portfolio_prices = lambda codes: {
            code: {"price": 750.0, "as_of": "2026-11-09", "source": "測試盤後資料"} for code in codes
        }
        store = live_server.LocalPortfolioStore(self.server.portfolio_db, self.server.portfolio_resolver,
                                                self.server.portfolio_prices)
        store.save_holding({"code": "2330", "shares": 1000, "average_cost": 500})
        store.save_cash({"amount_twd": 250000})
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO level_daily (data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at, is_current) "
                "VALUES (?, 1, 'cash-level-v2.1', ?, ?, ?, ?, 1)",
                ("2026-11-10", "2026-11-10T08:30:00+08:00", "a" * 64,
                 json.dumps({"recommended_level": 80}), "2026-11-10T00:00:00Z"),
            )
        body = store.current_level()
        self.assertEqual(body["current"]["level"], 75)
        self.assertTrue(body["adjustment"]["within_tolerance"])

    def test_zero_assets_do_not_create_zero_amount_adjustment(self):
        store = live_server.LocalPortfolioStore(self.server.portfolio_db, self.server.portfolio_resolver,
                                                self.server.portfolio_prices)
        store.save_cash({"amount_twd": 0})
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO level_daily (data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at, is_current) "
                "VALUES (?, 1, 'cash-level-v2.1', ?, ?, ?, ?, 1)",
                ("2026-11-10", "2026-11-10T08:30:00+08:00", "a" * 64,
                 json.dumps({"recommended_level": 80}), "2026-11-10T00:00:00Z"),
            )
        body = store.current_level()
        self.assertEqual(body["current"]["total_assets"], 0)
        self.assertIsNone(body["adjustment"])

    def test_existing_local_database_can_reopen_after_migrations(self):
        live_server.LocalPortfolioStore(self.server.portfolio_db, self.server.portfolio_resolver,
                                        self.server.portfolio_prices)
        live_server._PORTFOLIO_SCHEMA_READY.clear()
        reopened = live_server.LocalPortfolioStore(self.server.portfolio_db, self.server.portfolio_resolver,
                                                    self.server.portfolio_prices)
        self.assertEqual(reopened.list_cash()["amount_twd"], None)

    def _insert_ai_digest(self, *, status: str, content: dict | None = None) -> None:
        connection = sqlite3.connect(self.server.portfolio_db)
        try:
            connection.executescript(
                (ROOT / "cloud/migrations/0007_ai_daily.sql").read_text(encoding="utf-8")
            )
            connection.execute(
                "INSERT INTO ai_daily "
                "(data_date,digest_type,subject,content_json,input_sha256,model,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "2026-11-11",
                    "stock",
                    "2330",
                    None if content is None else json.dumps(content, ensure_ascii=False),
                    "a" * 64,
                    "test",
                    status,
                    "2026-11-11T09:00:00Z",
                    "2026-11-11T09:00:00Z",
                ),
            )
            connection.execute(
                "INSERT INTO ai_daily "
                "(data_date,digest_type,subject,content_json,input_sha256,model,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "2026-11-11",
                    "stock",
                    "*",
                    json.dumps({"count": 1}, ensure_ascii=False),
                    "b" * 64,
                    "test",
                    status,
                    "2026-11-11T09:00:00Z",
                    "2026-11-11T09:00:00Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_ai_digest_route_returns_generated_owner_payload_without_cache(self):
        self._insert_ai_digest(
            status="generated",
            content={"code": "2330", "status": "收盤 1000。", "action": "續抱", "reason": "量價仍穩定。"},
        )
        request = urllib.request.Request(
            self.base + "/api/ai-digest?scope=portfolio&code=2330",
            headers={"Origin": self.base},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(body["data_date"], "2026-11-11")
        self.assertEqual(body["stocks"]["status"], "generated")
        self.assertEqual(body["stocks"]["items"][0]["generation_status"], "generated")

    def test_ai_digest_route_returns_latest_unproduced_instead_of_404(self):
        self._insert_ai_digest(status="unproduced")
        status, body = self.request("/api/ai-digest?scope=portfolio&code=2330")
        self.assertEqual(status, 200)
        self.assertEqual(body["data_date"], "2026-11-11")
        self.assertEqual(body["stocks"]["status"], "unproduced")
        self.assertEqual(body["stocks"]["items"][0]["generation_status"], "unproduced")

    def test_ai_digest_route_rejects_non_owner_portfolio_scope(self):
        self.server.ai_digest_owner = False
        status, body = self.request("/api/ai-digest?scope=portfolio&code=2330")
        self.assertEqual(status, 403)
        self.assertEqual(body, {"error": "只有本人可查看個股判斷"})

    def test_core_release_defaults_off_before_level_or_ai_schema_queries(self):
        self.server.strategies_enabled = False
        status, features = self.request("/api/features?MARKET_ANA_STRATEGIES_ENABLED=1")
        self.assertEqual(status, 200)
        self.assertTrue(features["core_release"])
        self.assertTrue(features["features"]["holdings"])
        self.assertFalse(features["features"]["level_advice"])
        self.assertFalse(features["features"]["ai_stock_advice"])

        self.assertEqual(self.request("/api/holdings")[0], 200)
        status, level = self.request("/api/holdings/level")
        self.assertEqual(status, 403)
        self.assertEqual(level["error"], "feature-disabled")
        status, digest = self.request("/api/ai-digest?scope=portfolio")
        self.assertEqual(status, 403)
        self.assertEqual(digest["error"], "feature-disabled")

    def test_local_stock_route_strips_strategy_by_default_but_keeps_levels(self):
        fake_analyze = lambda code, as_of: {
            "schema": "stock-analysis/v2", "code": code, "name": "台積電", "as_of": "2026-11-10",
            "qa": {"blocking": False},
            "strategy": {"recommendation": "分批進場"},
            "levels": {"support": [{"tick": 980}], "resistance": [{"tick": 1050}]},
        }
        self.server.universe_enabled = False
        self.server.strategies_enabled = False
        with mock.patch("twon_analysis.analyze_v2.analyze_v2", side_effect=fake_analyze):
            status, body = self.request("/api/stocks/2330/analysis?MARKET_ANA_STRATEGIES_ENABLED=1")
        self.assertEqual(status, 200)
        self.assertNotIn("strategy", body)
        self.assertEqual(body["levels"]["support"][0]["tick"], 980)

        self.server.strategies_enabled = True
        with mock.patch("twon_analysis.analyze_v2.analyze_v2", side_effect=fake_analyze):
            status, body = self.request("/api/stocks/2330/analysis")
        self.assertEqual(status, 200)
        self.assertEqual(body["strategy"]["recommendation"], "分批進場")

    def test_local_live_route_passes_fake_upstream_payload_and_failure_status(self):
        class FakeIndexService:
            def __init__(self, payload):
                self.payload = payload
                self.calls = 0

            def snapshot(self):
                self.calls += 1
                return self.payload

        success = FakeIndexService({
            "ok": True, "source": "fake-index", "live": True, "market_open": True,
            "data_time": "2026-11-11T10:00:05+08:00",
            "fetched_at": "2026-11-11T10:00:10+08:00",
            "retryable": True, "retry_after_seconds": 0,
            "data": {"price": 28088.25, "turnover_billion": None},
            "series": [{"timestamp": "2026-11-11T10:00:00+08:00", "price": 28088.25}],
        })
        self.server.index_live_service = success
        status, body = self.request("/api/live")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["price"], 28088.25)
        self.assertIsNone(body["data"]["turnover_billion"])
        self.assertEqual(success.calls, 1)

        failure = FakeIndexService({
            "ok": False, "source": "fake-index", "live": False, "market_open": False,
            "retryable": True, "retry_after_seconds": 12, "data": None, "series": [],
            "error": "upstream unavailable",
        })
        self.server.index_live_service = failure
        status, body = self.request("/api/live")
        self.assertEqual(status, 503)
        self.assertTrue(body["retryable"])
        self.assertEqual(body["retry_after_seconds"], 12)

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
