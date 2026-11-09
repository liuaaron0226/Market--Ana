"""G3：籌碼選股與產業資金流向的發布封存（演算法依金融獵場 REA，對外名稱本站自訂）。

發布只呼叫 ``finhunt_screens.run_screen`` 與 ``sector_rotation.compute_timeline``，再以和
``candidate_snapshots`` 相同的正規化 SHA-256 封存；unknown／unavailable／partial 照實留存，
同一批重跑不動，內容不同不覆寫。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import finhunt_screens as fs
import market_screens_publish as msp

DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
BANNED = ("Jimmy", "私藏", "甜價", "甜甜價", "關注", "主力", "輪動", "觀望", "退潮", "CP值", "CP 值", "抄底", "金融獵場", "漲停寶寶")


class FakeSource:
    def __init__(self, market: str, values: dict[str, list[int | None]]) -> None:
        self.market = market
        self._values = values

    def trading_days(self) -> list[str]:
        return list(DAYS)

    def institutional(self, day: str) -> dict[str, dict]:
        index = DAYS.index(day)
        return {code: {"foreign": series[index], "trust": series[index]} for code, series in self._values.items()}

    def quotes(self, day: str) -> dict[str, dict]:
        return {code: {"close": 100.0, "change": 1.0, "suspended": False} for code in self._values}


UNIVERSE = [fs.Security("1101", "台泥", "上市"), fs.Security("6488", "環球晶", "上櫃"), fs.Security("8069", "元太", "上櫃")]
ROTATION = {
    "title": "產業資金流向",
    "quadrants": {"leading": {"label": "資金領先"}},
    "group_definition": {"kind": "official_industry", "note": "族群＝官方產業別"},
    "unknowns": ["原站當日成交額定義未知"],
    "snapshots": [{"date": "2026-11-06", "bubbles": [
        {"name": "半導體業", "x": 1.2, "y": 3.0, "quadrant": "leading"},
        {"name": "水泥工業", "x": -0.5, "y": -2.0, "quadrant": "lagging"},
    ], "efficiency_ranking": [], "recovery_signals": [], "summary": {"text": "測試摘要"}}],
}
GROUPS = {"半導體業": ["6488"], "水泥工業": ["1101"], "光電業": ["8069"]}


def inputs(only_otc: bool = False) -> dict:
    sources = [FakeSource("上櫃", {"6488": [10, 20, 30, 40, 50], "8069": [-5, -5, -5, -5, -5]})]
    if not only_otc:
        sources.append(FakeSource("上市", {"1101": [1, 1, 1, 1, 1]}))
    return {"sources": sources, "universe": UNIVERSE, "rotation": ROTATION, "groups": GROUPS,
            "input_fingerprints": {"otc_market": "a" * 8}}


class PlanTest(unittest.TestCase):
    def test_variants_cover_every_tab_and_every_rea_option(self) -> None:
        plan = msp.publish_plan()
        keys = {variant["key"] for variant in plan}
        self.assertEqual(keys, {tab.key for tab in fs.TABS})
        foreign = [(v["days"], v["side"]) for v in plan if v["key"] == "foreign_net_rank"]
        self.assertEqual(sorted(foreign), sorted((d, s) for d in (1, 3, 5, 10, 20) for s in ("buy", "sell")))
        streak = [(v["days"], v["side"]) for v in plan if v["key"] == "trust_streak"]
        self.assertEqual(sorted(streak), [(1, "buy"), (1, "sell")])
        self.assertEqual([v for v in plan if v["key"] == "broker_concentration"][0]["days"], 1)

    def test_kind_names_are_neutral(self) -> None:
        self.assertEqual(msp.screen_kind("foreign_net_rank", 5, "sell"), "screen-foreign_net_rank-5d-sell")
        self.assertEqual(msp.ROTATION_KIND, "rotation")


class PublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def publish(self, **overrides) -> dict:
        args = inputs()
        args.update(overrides)
        return msp.publish(self.root, data_date="2026-11-06", published_at="2026-11-06T10:00:00Z", **args)

    def test_writes_rank_streak_unknown_unavailable_and_rotation(self) -> None:
        report = self.publish()
        batch = msp.read_batch(self.root, "screen-foreign_net_rank-1d-buy", "2026-11-06")
        self.assertEqual(batch["result"]["state"], "available")
        self.assertEqual([row["code"] for row in batch["result"]["items"]], ["6488", "1101"])
        self.assertEqual(batch["schema_version"], msp.SCHEMA_VERSION)
        self.assertEqual(batch["engine_version"], fs.ENGINE_VERSION)
        self.assertEqual(batch["data_date"], "2026-11-06")
        self.assertEqual(batch["input_fingerprints"], {"otc_market": "a" * 8})
        unknown = msp.read_batch(self.root, "screen-rotation_relative_value-1d-buy", "2026-11-06")
        self.assertEqual(unknown["result"]["state"], "unknown")
        self.assertEqual(unknown["result"]["items"], [])
        unavailable = msp.read_batch(self.root, "screen-broker_top15_net-1d-buy", "2026-11-06")
        self.assertEqual(unavailable["result"]["state"], "unavailable")
        rotation = msp.read_batch(self.root, "rotation", "2026-11-06")
        self.assertEqual(rotation["result"]["title"], "產業資金流向")
        self.assertEqual(rotation["result"]["groups"], GROUPS)
        self.assertEqual(report["counts"]["written"], len(msp.publish_plan()) + 1)

    def test_rotation_star_comes_from_rotation_snapshot(self) -> None:
        self.publish()
        batch = msp.read_batch(self.root, "screen-foreign_net_rank-1d-buy", "2026-11-06")
        stars = {row["code"]: row["rotation_star"] for row in batch["result"]["items"]}
        self.assertEqual(stars, {"6488": True, "1101": False})
        self.assertEqual(batch["result"]["rotation_date"], "2026-11-06")

    def test_missing_market_is_partial_and_recorded(self) -> None:
        self.publish(**inputs(only_otc=True))
        batch = msp.read_batch(self.root, "screen-foreign_net_rank-1d-buy", "2026-11-06")
        self.assertEqual(batch["result"]["state"], "partial")
        self.assertEqual(batch["result"]["uncovered_markets"], ["上市"])

    def test_rerun_same_content_is_unchanged(self) -> None:
        self.publish()
        report = self.publish()
        self.assertEqual(report["counts"]["written"], 0)
        self.assertEqual(report["counts"]["unchanged"], len(msp.publish_plan()) + 1)

    def test_different_content_same_date_is_refused(self) -> None:
        self.publish()
        changed = inputs()
        changed["sources"][0] = FakeSource("上櫃", {"6488": [1, 1, 1, 1, 1], "8069": [-5, -5, -5, -5, -5]})
        with self.assertRaises(msp.ScreenSnapshotConflict):
            msp.publish(self.root, data_date="2026-11-06", published_at="2026-11-06T10:00:00Z", **changed)

    def test_publish_before_close_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            msp.publish(self.root, data_date="2026-11-06", published_at="2026-11-06T04:00:00Z", **inputs())

    def test_tampered_batch_is_refused(self) -> None:
        self.publish()
        path = self.root / "screen-foreign_net_rank-1d-buy" / "2026-11-06.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["result"]["items"][0]["code"] = "9999"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(msp.ScreenSnapshotIntegrityError):
            msp.read_batch(self.root, "screen-foreign_net_rank-1d-buy", "2026-11-06")

    def test_latest_batch_ignores_partial_files(self) -> None:
        self.publish()
        (self.root / "rotation" / ".2026-11-07.json.1.partial").write_text("{", encoding="utf-8")
        self.assertEqual(msp.latest_batch(self.root, "rotation")["data_date"], "2026-11-06")
        self.assertIsNone(msp.latest_batch(self.root, "screen-nope-1d-buy"))

    def test_published_text_never_reuses_original_site_names(self) -> None:
        self.publish()
        for folder in self.root.iterdir():
            for path in folder.glob("*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                result = dict(data["result"])
                # unknown／evidence 是出處說明；conditions 是引擎原文（稽核用），畫面改用 display_conditions。
                for audit_only in ("unknowns", "unknown", "evidence", "conditions"):
                    result.pop(audit_only, None)
                text = json.dumps(result, ensure_ascii=False)
                for banned in BANNED:
                    self.assertNotIn(banned, text, f"{path.name}: {banned}")


LIQUID = {"rule_version": "liquid-v1", "data_date": "2026-11-06", "core_holdings": ["0050"],
          "members": [{"code": "6488", "market": "上櫃", "rank": 1}, {"code": "1101", "market": "上市", "rank": 2}],
          "etf_members": [{"code": "00878", "name": "國泰永續高股息", "market": "上市", "equivalent_rank": 2}]}


class LiquidPublishTest(unittest.TestCase):
    """V2：發布第一步先封存當日大量股母體；排行只取母體（含大量股 ETF）；相對低位階在發布時就有名單。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def publish(self, **overrides) -> dict:
        args = inputs()
        args["sources"] = [FakeSource("上櫃", {"6488": [10, 20, 30, 40, 50], "8069": [-5, -5, -5, -5, -5]}),
                           FakeSource("上市", {"1101": [1, 1, 1, 1, 1], "00878": [9, 9, 9, 9, 99]})]
        args.update(liquid_universe=LIQUID,
                    bias={"stocks": {"6488": -0.3, "1101": -0.05, "8069": -0.9}, "indexes": {"上市": -0.1, "上櫃": -0.2},
                          "index_sources": {"上市": "加權指數", "上櫃": "櫃買指數"}, "data_date": "2026-11-06"})
        args.update(overrides)
        return msp.publish(self.root, data_date="2026-11-06", published_at="2026-11-06T10:00:00Z", **args)

    def test_universe_snapshot_is_sealed_first(self) -> None:
        report = self.publish()
        batch = msp.read_batch(self.root, msp.UNIVERSE_KIND, "2026-11-06")
        self.assertEqual(batch["result"]["rule_version"], "liquid-v1")
        self.assertEqual(next(iter(report["outcomes"])), msp.UNIVERSE_KIND)

    def test_rankings_only_contain_universe_members_including_liquid_etfs(self) -> None:
        self.publish()
        items = msp.read_batch(self.root, "screen-foreign_net_rank-1d-buy", "2026-11-06")["result"]["items"]
        self.assertEqual([(r["code"], r["security_type"]) for r in items], [("00878", "ETF"), ("6488", "普通股"), ("1101", "普通股")])
        self.assertNotIn("8069", [r["code"] for r in items])  # 不在大量股名單

    def test_relative_value_has_a_list_at_publish_time(self) -> None:
        self.publish()
        result = msp.read_batch(self.root, "screen-rotation_relative_value-1d-buy", "2026-11-06")["result"]
        self.assertEqual(result["state"], "available")
        self.assertEqual([r["code"] for r in result["items"]], ["6488"])  # 1101 乖離高於加權；8069 不在母體

    def test_rotation_group_amounts_stay_whole_market(self) -> None:
        self.publish()
        rotation = msp.read_batch(self.root, "rotation", "2026-11-06")["result"]
        self.assertEqual(rotation["groups"], GROUPS)  # 族群成分不因母體縮小而刪減（族群成交額＝全市場）

    def test_publish_without_universe_keeps_old_behaviour(self) -> None:
        report = PublishTest.publish.__get__(self)()  # noqa: 舊呼叫端（無母體）仍可用
        self.assertNotIn(msp.UNIVERSE_KIND, report["outcomes"])


class CoreHoldingsTest(unittest.TestCase):
    def test_total_return_reinvests_cash_dividend_and_handles_split(self) -> None:
        rows = [
            {"date": "2016-01-04", "close": 100.0, "change_sign": "+", "change": 0.0},
            {"date": "2016-07-01", "close": 98.0, "change_sign": "-", "change": -1.0},   # 除息 2 元：參考價 98
            {"date": "2017-01-03", "close": 49.0, "change_sign": "+", "change": 0.0},    # 1 拆 2：參考價 49
            {"date": "2018-01-02", "close": 60.0, "change_sign": "+", "change": 11.0},
        ]
        events = {("TEST", "2016-07-01"): {"type": "除息", "value": 2.0, "close_before": 100.0, "reference": 98.0}}
        series = msp.total_return_series("TEST", rows, events)
        self.assertAlmostEqual(series[1][1], 1.0, places=6)          # 除息當天市值不變（息再投入）
        self.assertAlmostEqual(series[2][1], 1.0, places=6)          # 分割當天市值不變
        self.assertAlmostEqual(series[-1][1], 60 / 49, places=6)

    def test_stats_from_series(self) -> None:
        series = [("2016-01-04", 1.0), ("2017-01-04", 2.0), ("2018-01-04", 1.0), ("2026-11-06", 4.0)]
        stats = msp.series_stats(series)
        self.assertEqual(stats["start"], "2016-01-04")
        self.assertEqual(stats["end"], "2026-11-06")
        self.assertAlmostEqual(stats["total_multiple"], 4.0)
        self.assertAlmostEqual(stats["max_drawdown_pct"], -50.0)
        self.assertEqual(set(stats["trailing_pct"]), {"1y", "3y", "5y"})
        self.assertAlmostEqual(stats["trailing_pct"]["1y"], 300.0)   # 2025-11-06 前最近一筆是 1.0
        short = msp.series_stats([("2026-01-02", 1.0), ("2026-11-06", 1.1)])
        self.assertIsNone(short["trailing_pct"]["1y"])                # 起點晚於一年前：不給近一年報酬

    def test_real_core_holdings_match_research_numbers(self) -> None:
        # 研究數字以 2026-11-06 為準；固定資料日，行情庫每日增量後不會漂移。
        report = msp.core_holdings(as_of="2026-11-06")
        self.assertEqual(report["holdings"][0]["stats"]["end"], "2026-11-06")
        self.assertEqual([item["code"] for item in report["holdings"]], ["0050", "2330"])
        by_code = {item["code"]: item for item in report["holdings"]}
        self.assertEqual(by_code["0050"]["stats"]["start"], "2016-01-04")
        self.assertAlmostEqual(by_code["0050"]["stats"]["total_multiple"], 8.58, delta=0.02)
        self.assertAlmostEqual(by_code["2330"]["stats"]["total_multiple"], 20.31, delta=0.05)
        self.assertAlmostEqual(by_code["0050"]["stats"]["cagr_pct"], 21.9, delta=0.2)
        self.assertAlmostEqual(by_code["2330"]["stats"]["max_drawdown_pct"], -44.8, delta=0.2)
        self.assertLessEqual(len(by_code["0050"]["chart"]), 200)
        self.assertIn("未扣交易成本", "".join(report["notes"]))
        self.assertEqual(report["role"]["0050"], "核心持有")

    def test_core_holdings_as_of_excludes_later_rows_and_default_reaches_latest(self) -> None:
        pinned = msp.core_holdings(as_of="2026-11-05")
        self.assertTrue(all(h["stats"]["end"] <= "2026-11-05" for h in pinned["holdings"]))
        latest = msp.core_holdings()
        self.assertGreaterEqual(latest["holdings"][0]["stats"]["end"], "2026-11-06")
        self.assertNotEqual(pinned["holdings"][0]["stats"]["end"], latest["holdings"][0]["stats"]["end"])


if __name__ == "__main__":
    unittest.main()
