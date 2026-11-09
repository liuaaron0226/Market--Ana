"""B1（2026-11-09）：個股與 0050 含息報酬對照。

本人 11-09 定案：個股頁加上和 0050 的對照（近 1 個月、3 個月、1 年、3 年、5 年含息報酬、最大回撤、對照圖）；
追加：只能從本站涵蓋的大量股中選，可同時選多檔（/api/compare-0050?codes=...）。
含息報酬沿用 market_screens_publish 的核心持有算法（同一個 total_return_series），不另寫一套。
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import benchmark_compare as bc
import market_screens_publish as msp

ROOT = Path(__file__).resolve().parent


class ShiftMonthsTest(unittest.TestCase):
    def test_month_end_is_clamped(self) -> None:
        self.assertEqual(bc.shift_months("2026-03-31", 1), "2026-02-28")

    def test_whole_years_match_core_holdings_anchor(self) -> None:
        self.assertEqual(bc.shift_months("2026-11-09", 12), "2025-11-09")
        self.assertEqual(bc.shift_months("2026-11-09", 60), "2021-11-09")

    def test_crosses_year(self) -> None:
        self.assertEqual(bc.shift_months("2026-01-15", 3), "2025-10-15")


def _series(start_value: float, points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    return [(d, v / start_value) for d, v in points]


# 期初點與核心持有相同：取「錨點當天或之前最近一筆」（1 個月錨點 2026-10-06 → 用 10-05）。
STOCK = _series(100, [("2020-01-02", 100), ("2025-11-03", 120), ("2026-08-07", 90), ("2026-10-05", 150), ("2026-11-06", 180)])
BENCH = _series(50, [("2016-01-04", 50), ("2020-01-02", 60), ("2025-11-03", 80), ("2026-08-07", 70), ("2026-10-05", 90),
                     ("2026-11-06", 100)])


class PeriodStatsTest(unittest.TestCase):
    def test_returns_excess_and_drawdown_per_period(self) -> None:
        periods = {p["period"]: p for p in bc.period_rows(STOCK, BENCH, "2026-11-06", listed_date="2019-01-01")}
        one_month = periods["1m"]
        self.assertEqual(one_month["status"], "ok")
        self.assertEqual(one_month["from"], "2026-10-05")
        self.assertAlmostEqual(one_month["stock_return_pct"], 20.0)        # 150 → 180
        self.assertAlmostEqual(one_month["benchmark_return_pct"], 11.1)    # 90 → 100
        self.assertAlmostEqual(one_month["excess_pct"], 8.9)
        one_year = periods["1y"]
        self.assertEqual(one_year["from"], "2025-11-03")
        self.assertAlmostEqual(one_year["stock_return_pct"], 50.0)         # 120 → 180
        self.assertAlmostEqual(one_year["stock_max_drawdown_pct"], -25.0)  # 120 → 90
        self.assertAlmostEqual(one_year["benchmark_max_drawdown_pct"], -12.5)

    def test_insufficient_history_is_not_filled(self) -> None:
        periods = {p["period"]: p for p in bc.period_rows(STOCK, BENCH, "2026-11-06", listed_date="2019-01-01")}
        self.assertEqual(periods["5y"]["status"], "ok")
        late = _series(100, [("2024-06-03", 100), ("2026-11-06", 130)])
        rows = {p["period"]: p for p in bc.period_rows(late, BENCH, "2026-11-06", listed_date="2024/06/03")}
        self.assertEqual(rows["3y"]["status"], "insufficient_history")
        self.assertEqual(rows["3y"]["reason"], "listed_recently")
        self.assertNotIn("stock_return_pct", rows["3y"])
        not_loaded = {p["period"]: p for p in bc.period_rows(late, BENCH, "2026-11-06", listed_date="2001/01/01")}
        self.assertEqual(not_loaded["3y"]["reason"], "history_not_loaded")
        self.assertEqual(not_loaded["1y"]["status"], "ok")

    def test_uses_core_holdings_total_return(self) -> None:
        self.assertIs(bc.total_return_series, msp.total_return_series)


class ChartTest(unittest.TestCase):
    def test_chart_starts_at_one_and_is_downsampled(self) -> None:
        daily = [(f"2021-{m:02d}-{d:02d}", 1.0 + 0.001 * i) for i, (m, d) in
                 enumerate((m, d) for m in range(1, 13) for d in range(1, 28))]
        start, points = bc.chart_points(daily, daily[0][0])
        self.assertEqual(start, daily[0][0])
        self.assertEqual(points[0][1], 1.0)
        self.assertLessEqual(len(points), bc.CHART_POINTS)


class ParseCodesTest(unittest.TestCase):
    def test_valid_and_deduplicated(self) -> None:
        self.assertEqual(bc.parse_codes(["2330,6488,2330"]), ["2330", "6488"])

    def test_rejects_bad_input(self) -> None:
        for raw in ([], [""], ["2330,"], ["23"], ["2330", "6488"], ["2330, 6488"],
                    [",".join(["2330", "6488", "0050", "1102", "2454", "2317"])]):
            with self.subTest(raw=raw):
                self.assertIsNone(bc.parse_codes(raw))

    def test_max_codes_is_five(self) -> None:
        self.assertEqual(bc.MAX_CODES, 5)
        self.assertEqual(len(bc.parse_codes(["2330,6488,0050,1102,2454"])), 5)


class RealDataTest(unittest.TestCase):
    """驗算：1Y 報酬與核心持有（同一算法、同一資料日）逐位相同。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.core = msp.core_holdings()
        cls.end = cls.core["holdings"][0]["stats"]["end"]
        cls.loader = bc.Loader()

    def test_2330_and_0050_match_core_holdings(self) -> None:
        bench = self.loader.benchmark_block(self.end)
        item = self.loader.item("2330", self.end, listed_date="1994/09/05")
        core = {h["code"]: h["stats"]["trailing_pct"] for h in self.core["holdings"]}
        periods = {p["period"]: p for p in item["periods"]}
        bench_periods = {p["period"]: p for p in bench["periods"]}
        for key in ("1y", "3y", "5y"):
            self.assertEqual(periods[key]["stock_return_pct"], core["2330"][key], key)
            self.assertEqual(bench_periods[key]["return_pct"], core["0050"][key], key)

    def test_6488_and_1102_match_shared_algorithm(self) -> None:
        for code, listed in (("6488", "2015/09/25"), ("1102", "1962/06/08")):
            with self.subTest(code=code):
                item = self.loader.item(code, self.end, listed_date=listed)
                rows, _, _ = self.loader.rows(code, self.end)
                expected = msp.series_stats(msp.total_return_series(code, rows, self.loader.events))["trailing_pct"]["1y"]
                one_year = next(p for p in item["periods"] if p["period"] == "1y")
                self.assertEqual(one_year["stock_return_pct"], expected)

    def test_otc_flags_dividends_not_included(self) -> None:
        item = self.loader.item("6488", self.end, listed_date="2015/09/25")
        self.assertEqual(item["market"], "上櫃")
        self.assertFalse(item["dividends_included"])
        listed = self.loader.item("1102", self.end, listed_date="1962/06/08")
        self.assertTrue(listed["dividends_included"])


UNIVERSE_FILE = ROOT / ".tmp/lane-B1/universe-fixture.json"


def crafted_universe() -> Path:
    """真資料 11-06 母體只留 2330（第 1）與 6488（第 20），1102 因此成為母體外（名次照實）。"""
    source = json.loads((ROOT / "data/liquid-universe/2026-11-06.json").read_text(encoding="utf-8"))
    source = source.get("result", source)
    source = {k: v for k, v in source.items() if k != "sha256"}
    source["members"] = [m for m in source["members"] if m["code"] in ("2330", "6488")]
    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_FILE.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return UNIVERSE_FILE


def get(base: str, path: str) -> tuple[int, dict]:
    try:
        response = urllib.request.urlopen(base + path, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


@unittest.skipUnless((ROOT / "data/liquid-universe/2026-11-06.json").exists(), "母體快照不在本機")
class LocalEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import live_server

        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.server.universe_path = crafted_universe()
        live_server.universe_state(cls.server.universe_path)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_single_member(self) -> None:
        status, body = get(self.base, "/api/stocks/2330/vs-0050")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "vs-0050/v1")
        self.assertEqual(body["data_date"], "2026-11-06")
        self.assertEqual(body["benchmark"]["code"], "0050")
        self.assertEqual(body["item"]["code"], "2330")
        self.assertEqual([p["period"] for p in body["item"]["periods"]], ["1m", "3m", "1y", "3y", "5y"])

    def test_single_outside_and_not_found(self) -> None:
        status, body = get(self.base, "/api/stocks/1102/vs-0050")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "outside-universe")
        self.assertEqual(get(self.base, "/api/stocks/9999/vs-0050")[1]["error"], "not-found")
        self.assertEqual(get(self.base, "/api/stocks/23/vs-0050")[0], 400)

    def test_multi_mixed_items_and_one_benchmark(self) -> None:
        status, body = get(self.base, "/api/compare-0050?codes=2330,1102,9999,6488")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "compare-0050/v1")
        self.assertEqual(body["max_codes"], 5)
        self.assertEqual([i["code"] for i in body["items"]], ["2330", "1102", "9999", "6488"])
        self.assertEqual([i["status"] for i in body["items"]], ["ok", "outside-universe", "not-found", "ok"])
        self.assertIn("universe_rank", body["items"][1])
        self.assertEqual(body["benchmark"]["code"], "0050")

    def test_multi_rejects_too_many_or_bad(self) -> None:
        for path in ("/api/compare-0050", "/api/compare-0050?codes=",
                     "/api/compare-0050?codes=2330,6488,0050,1102,2454,2317",
                     "/api/compare-0050?codes=2330&codes=6488"):
            with self.subTest(path=path):
                self.assertEqual(get(self.base, path)[0], 400)


if __name__ == "__main__":
    unittest.main()
