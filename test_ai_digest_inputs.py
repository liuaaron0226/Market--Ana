import json
import tempfile
import unittest
from pathlib import Path

from ai_digest_inputs import build_digest_input


class AiDigestInputTests(unittest.TestCase):
    def test_builds_compact_owner_only_input_and_stable_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            market = root / "market"
            market.mkdir()
            self._write_js(market / "analysis-data.js", "MARKET_ANALYSIS", {
                "meta": {"cutoff_date": "2026-11-10"},
                "daily": [
                    {"date": "2026-11-09", "close": 23100, "amount": 4100},
                    {"date": "2026-11-10", "open": 23120, "high": 23300, "low": 23050, "close": 23280, "amount": 4300},
                ],
                "institutional": [{"date": "2026-11-10", "foreign": -120.5, "trust": 35.2, "dealer": -4.1}],
                "futures": [{"date": "2026-11-10", "foreign_oi_net_contracts": -18888}],
                "sectors": [{"date": "2026-11-10", "electronic_change_pct": 1.2, "financial_change_pct": -0.3}],
                "indicators": {"daily": [{"date": "2026-11-10", "ma5": 23150, "ma20": 22900, "macd": 18.2}]},
            })
            self._write_js(market / "dashboard-data.js", "MARKET_DASHBOARD", {
                "market_date": "2026-11-10", "latest": {"close": 23280, "change_percent": 0.78}
            })
            self._write_js(market / "chips-data.js", "MARKET_CHIPS", {
                "analysis_cutoff": "2026-11-10",
                "margin": [{"date": "2026-11-10", "margin_balance_billion": 5340.5}],
                "foreign": [{"date": "2026-11-10", "net_billion": -120.5, "trust_billion": 35.2}],
                "futures": [{"date": "2026-11-10", "net_contracts": -18888}],
            })

            screens = root / "screens"
            screen_dir = screens / "screen-foreign_net_rank-1d-buy"
            screen_dir.mkdir(parents=True)
            (screen_dir / "2026-11-10.json").write_text(json.dumps({
                "data_date": "2026-11-10", "kind": "screen-foreign_net_rank-1d-buy",
                "result": {"display_name": "外資淨買超排行", "items": [
                    {"code": "2330", "name": "台積電", "metric_lots": 12000, "close": 1000},
                    {"code": "2317", "name": "鴻海", "metric_lots": 8000, "close": 210}
                ]}
            }, ensure_ascii=False), encoding="utf-8")
            rotation_dir = screens / "rotation"
            rotation_dir.mkdir()
            (rotation_dir / "2026-11-10.json").write_text(json.dumps({
                "data_date": "2026-11-10", "kind": "rotation", "result": {"snapshots": [{
                    "date": "2026-11-10", "summary": {"capital_side": "in"},
                    "efficiency_ranking": [{"name": "半導體", "score": 55.5}], "recovery_signals": []
                }]}
            }, ensure_ascii=False), encoding="utf-8")

            stock_pack = root / "stock.json"
            stock_pack.write_text(json.dumps({
                "analysis_schema": "stock-analysis/v2", "as_of": "2026-11-10", "results": {
                    "2330": {"schema": "stock-analysis/v2", "code": "2330", "name": "台積電", "as_of": "2026-11-10",
                             "quote": {"close": 1000, "chg_pct": 1.5}, "trend_classification": {"status": "up"},
                             "levels": {"support": 980, "resistance": 1020}, "conditions": {"confirmed": ["量價"]},
                             "defense": {"status": "ok"}, "indicators": {"ma20": 950}, "qa": {"blocking": []}}
                }
            }, ensure_ascii=False), encoding="utf-8")
            compare_pack = root / "compare.json"
            compare_pack.write_text(json.dumps({
                "schema": "compare-0050-pack/v1", "data_date": "2026-11-10",
                "benchmark": json.dumps({"code": "0050", "periods": [{"period": "1m", "return_pct": 2.2}]}),
                "items": {"2330": json.dumps({"code": "2330", "periods": [{"period": "1m", "return_pct": 4.4}]})}
            }, ensure_ascii=False), encoding="utf-8")
            portfolio = root / "portfolio.json"
            portfolio.write_text(json.dumps({"watchlist": [
                {"email": "owner@example.com", "code": "2330", "name": "台積電", "horizon": "long", "notes": "私人觀察筆記"},
                {"email": "other@example.com", "code": "2317", "name": "鴻海", "horizon": "short"},
            ], "holdings": [{"email": "owner@example.com", "code": "2330", "name": "台積電", "shares": 1000,
                               "average_cost": 900, "last_price": 990}]}, ensure_ascii=False), encoding="utf-8")

            first = build_digest_input(
                data_date="2026-11-10", market_dir=market, screens_root=screens,
                stock_pack_path=stock_pack, compare_pack_path=compare_pack,
                portfolio_path=portfolio, owner_email="owner@example.com",
            )
            second = build_digest_input(
                data_date="2026-11-10", market_dir=market, screens_root=screens,
                stock_pack_path=stock_pack, compare_pack_path=compare_pack,
                portfolio_path=portfolio, owner_email="owner@example.com",
            )

            self.assertEqual(first, second)
            self.assertRegex(first["input_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(first["data_date"], "2026-11-10")
            self.assertEqual(first["allowed_codes"], ["2317", "2330"])
            self.assertEqual(first["portfolio_codes"], ["2330"])
            self.assertEqual(first["portfolio"]["holdings"][0]["unrealized_pnl"], 100000)
            self.assertEqual(first["portfolio"]["holdings"][0]["return_pct"], 11.11)
            serialized = json.dumps(first, ensure_ascii=False)
            self.assertNotIn("other@example.com", serialized)
            self.assertNotIn("owner@example.com", serialized)
            self.assertNotIn("私人觀察筆記", serialized)
            self.assertLess(len(json.dumps(first, ensure_ascii=False)), 30_000)

    @staticmethod
    def _write_js(path, name, value):
        path.write_text(f"window.{name} = {json.dumps(value, ensure_ascii=False)};\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
