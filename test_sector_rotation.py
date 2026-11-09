"""資金輪動引擎測試：公式逐條對照金融獵場 REA（FINDINGS.md、DATA-ORIGIN-DIGEST〈資金輪動圖〉）。"""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import sector_rotation as sr

YI = 100_000_000
REA_PREVIEW = Path(
    "C:/Users/User/Desktop/claude/.claude/worktrees/recovery-ready-20261029/"
    ".scratch/finhunt-sector-rotation-rea/raw/kash-sr-api-preview.json"
)


def _dates(n: int) -> list[str]:
    return [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}" for i in range(n)]


def _flat(amount_yi: float, dates: list[str]) -> dict[str, int]:
    return {d: int(amount_yi * YI) for d in dates}


class GroupFormulaTest(unittest.TestCase):
    def test_x_is_log2_of_group_20d_over_mean_rounded_2dp(self):
        self.assertEqual(sr.group_x(200.0, 100.0), 1.0)
        self.assertEqual(sr.group_x(100.0, 300.0), round(math.log2(1 / 3), 2))
        self.assertEqual(sr.group_x(100.0, 300.0), -1.58)

    def test_x_unknown_when_group_20d_is_zero(self):
        self.assertIsNone(sr.group_x(0.0, 100.0))

    def test_y_is_daily_average_difference_in_yi_one_decimal(self):
        # 近5日日均 50 億、近20日日均 40 億 → Y=+10.0 億
        sum5 = 5 * 50 * YI
        sum20 = 20 * 40 * YI
        self.assertEqual(sr.group_y(sum5, sum20), 10.0)

    def test_y_matches_rea_unit_correction_example(self):
        # FINDINGS 11-08 更正：AI 伺服器 10×(195.2/5 − 888.4/20) = −53.8（API 族群累計單位十億）
        sum5 = int(195.2 * 10 * YI)
        sum20 = int(888.4 * 10 * YI)
        self.assertEqual(sr.group_y(sum5, sum20), -53.8)

    def test_relative_pct_display(self):
        self.assertEqual(sr.relative_pct(1.0), 100)
        self.assertEqual(sr.relative_pct(-1.0), -50)
        self.assertEqual(sr.relative_pct(1.16), 123)


class QuadrantTest(unittest.TestCase):
    def test_boundaries_follow_front_end_code(self):
        self.assertEqual(sr.quadrant(0.0, 0.0), "leading")
        self.assertEqual(sr.quadrant(0.0, -0.1), "weakening")
        self.assertEqual(sr.quadrant(-0.01, 0.0), "improving")
        self.assertEqual(sr.quadrant(-0.01, -0.1), "lagging")

    def test_unknown_when_x_missing(self):
        self.assertIsNone(sr.quadrant(None, 1.0))


def _bubble(name, x, y, size=100.0, today=10.0):
    return {"name": name, "x": x, "y": y, "size": size, "amount_today": today,
            "quadrant": sr.quadrant(x, y)}


class RankingTest(unittest.TestCase):
    def test_efficiency_only_strictly_positive_x_and_uses_timeline_wide_m(self):
        bubbles = [
            _bubble("A", 0.5, 20.0, size=1000.0),
            _bubble("B", 0.0, 0.0, size=5000.0),   # X 剛好 0 不算
            _bubble("C", 1.2, -30.0, size=800.0),  # 負 Y → max(0,Y)=0
            _bubble("D", -0.3, 5.0, size=9000.0),  # X<0 不算
        ]
        rows = sr.efficiency_ranking(bubbles, max_abs_y=100.0)
        self.assertEqual([r["name"] for r in rows], ["A", "C"])
        self.assertAlmostEqual(rows[0]["score"], 1000.0 * (1 - 20.0 / 100.0))
        self.assertAlmostEqual(rows[1]["score"], 800.0)

    def test_efficiency_top_five_only(self):
        bubbles = [_bubble(str(i), 0.1, 0.0, size=float(i)) for i in range(1, 9)]
        self.assertEqual([r["name"] for r in sr.efficiency_ranking(bubbles, 1.0)], ["8", "7", "6", "5", "4"])

    def test_timeline_m_is_max_abs_y_over_all_days_min_one(self):
        snapshots = [{"bubbles": [_bubble("A", 0.1, 0.2)]}, {"bubbles": [_bubble("A", 0.1, -0.4)]}]
        self.assertEqual(sr.timeline_max_abs_y(snapshots), 1.0)
        snapshots.append({"bubbles": [_bubble("B", -1, -339.0)]})
        self.assertEqual(sr.timeline_max_abs_y(snapshots), 339.0)

    def test_recovery_signals_watch_quadrant_top_four_by_y(self):
        bubbles = [
            _bubble("a", -0.1, 16.5), _bubble("b", -0.2, 11.3), _bubble("c", -0.3, 9.8),
            _bubble("d", -0.4, 8.4), _bubble("e", -0.5, 1.0), _bubble("f", 0.5, 99.0),
            _bubble("g", -0.5, -3.0),
        ]
        self.assertEqual([r["name"] for r in sr.recovery_signals(bubbles)], ["a", "b", "c", "d"])


class SummaryTest(unittest.TestCase):
    def test_four_branches(self):
        # 右≥左，主力≥輪動
        s = sr.market_summary([_bubble("a", 1, 1, today=10), _bubble("b", -1, -1, today=5)])
        self.assertEqual((s["capital_side"], s["branch"]), ("in", "leading_dominant"))
        # 右≥左，主力<輪動
        s = sr.market_summary([_bubble("a", 1, -1, today=10), _bubble("b", -1, -1, today=5)])
        self.assertEqual((s["capital_side"], s["branch"]), ("in", "decelerating"))
        # 右<左，退潮≤觀望
        s = sr.market_summary([_bubble("a", 1, 1, today=1), _bubble("b", -1, 1, today=5)])
        self.assertEqual((s["capital_side"], s["branch"]), ("out", "selective_recovery"))
        # 右<左，退潮>觀望
        s = sr.market_summary([_bubble("a", 1, 1, today=1), _bubble("b", -1, -1, today=5)])
        self.assertEqual((s["capital_side"], s["branch"]), ("out", "broad_outflow"))

    def test_uses_absolute_today_amount(self):
        s = sr.market_summary([_bubble("a", 1, 1, today=-10), _bubble("b", -1, -1, today=5)])
        self.assertEqual((s["right_amount"], s["left_amount"]), (10.0, 5.0))

    def test_text_is_own_wording_not_original_copy(self):
        s = sr.market_summary([_bubble("a", 1, 1, today=10)])
        for original in ("強者恆強", "主力", "輪動", "觀望", "退潮", "大逃殺"):
            self.assertNotIn(original, s["text"])
        self.assertTrue(s["text"])


class StockDrilldownTest(unittest.TestCase):
    def test_stock_x_uses_nonzero_member_mean_and_zero_maps_to_zero(self):
        windows = {
            "1111": sr.Window(today=0, sum5=0, sum20=0),
            "2222": sr.Window(today=1 * YI, sum5=5 * YI, sum20=20 * YI),
            "3333": sr.Window(today=3 * YI, sum5=15 * YI, sum20=60 * YI),
        }
        points = {p["code"]: p for p in sr.stock_points(windows)}
        self.assertEqual(points["1111"]["x"], 0.0)
        # 非零平均＝(20+60)/2＝40 億
        self.assertEqual(points["2222"]["x"], round(math.log2(20 / 40), 2))
        self.assertEqual(points["3333"]["x"], round(math.log2(60 / 40), 2))
        self.assertEqual(points["2222"]["amount_20d"], 20.0)  # 個股單位：億

    def test_stock_y_is_scaled_by_ten(self):
        windows = {"2222": sr.Window(today=0, sum5=5 * 6 * YI, sum20=20 * 4 * YI)}
        point = sr.stock_points(windows)[0]
        self.assertEqual(point["y"], (6 - 4) * 10)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.dates = _dates(40)
        self.turnover = {
            "A1": _flat(100, self.dates), "A2": _flat(100, self.dates),
            "B1": _flat(50, self.dates),
            "C1": {**_flat(10, self.dates[:-1]), self.dates[-1]: None},  # 最後一天缺值
        }
        self.groups = {"甲": ["A1", "A2"], "乙": ["B1"], "丙": ["C1"]}

    def test_incomplete_member_excluded_and_counted_not_zero_filled(self):
        snap = sr.group_snapshot(self.groups, self.turnover, self.dates, len(self.dates) - 1)
        names = {b["name"] for b in snap["bubbles"]}
        self.assertEqual(names, {"甲", "乙"})
        self.assertEqual(snap["unavailable_groups"][0]["name"], "丙")
        self.assertEqual(snap["unavailable_groups"][0]["excluded_members"], ["C1"])

    def test_snapshot_values_and_units(self):
        snap = sr.group_snapshot(self.groups, self.turnover, self.dates, len(self.dates) - 1)
        a = next(b for b in snap["bubbles"] if b["name"] == "甲")
        # 甲 20 日＝200 億×20＝4000 億＝400.0 十億；平均＝(4000+1000)/2＝2500 億
        self.assertEqual(a["amount_20d"], 400.0)
        self.assertEqual(a["size"], 400.0)
        self.assertEqual(a["amount_5d"], 100.0)
        self.assertEqual(a["amount_today"], 200.0)
        self.assertEqual(a["x"], round(math.log2(4000 / 2500), 2))
        self.assertEqual(a["y"], 0.0)
        self.assertEqual(a["quadrant"], "leading")

    def test_timeline_has_twenty_days_oldest_first_and_needs_39_dates(self):
        timeline = sr.compute_timeline(self.groups, self.turnover, self.dates)
        self.assertEqual(len(timeline["snapshots"]), 20)
        self.assertEqual(timeline["snapshots"][-1]["date"], self.dates[-1])
        self.assertEqual(timeline["snapshots"][0]["date"], self.dates[-20])
        with self.assertRaises(ValueError):
            sr.compute_timeline(self.groups, self.turnover, self.dates[:38])

    def test_timeline_carries_group_definition_note_and_unknowns(self):
        timeline = sr.compute_timeline(self.groups, self.turnover, self.dates)
        self.assertIn("族群", timeline["group_definition"]["note"])
        self.assertTrue(timeline["unknowns"])

    def test_public_labels_do_not_reuse_original_site_names(self):
        timeline = sr.compute_timeline(self.groups, self.turnover, self.dates)
        self.assertEqual(timeline["title"], "產業資金流向")
        self.assertEqual(list(timeline["quadrants"]), ["leading", "weakening", "improving", "lagging"])
        labels = json.dumps({k: v for k, v in timeline.items() if k != "unknowns"}, ensure_ascii=False)
        for original in ("主力", "輪動", "觀望", "退潮", "CP值", "CP 值", "抄底", "資金輪動"):
            self.assertNotIn(original, labels)
        self.assertIn("efficiency_ranking", timeline["snapshots"][-1])
        self.assertIn("recovery_signals", timeline["snapshots"][-1])

    def test_deterministic(self):
        a = sr.compute_timeline(self.groups, self.turnover, self.dates)
        b = sr.compute_timeline(self.groups, self.turnover, self.dates)
        self.assertEqual(json.dumps(a, ensure_ascii=False, sort_keys=True),
                         json.dumps(b, ensure_ascii=False, sort_keys=True))

    def test_inflow_codes_are_members_of_groups_with_y_non_negative(self):
        snapshot = {"bubbles": [_bubble("甲", -1.0, 0.0), _bubble("乙", 2.0, -5.0)]}
        self.assertEqual(sr.rotation_inflow_codes(snapshot, self.groups), {"A1", "A2"})


class DisplayTest(unittest.TestCase):
    def test_axis_bounds(self):
        snapshots = [{"bubbles": [_bubble("a", 3.97, 100.0), _bubble("b", -8.55, -339.0)]}]
        bounds = sr.axis_bounds(snapshots)
        self.assertAlmostEqual(bounds["x_max"], 3.97 * 1.2)
        self.assertAlmostEqual(bounds["x_min"], -8.55 * 1.2)
        self.assertEqual(bounds["y_half_range"], 350)

    def test_axis_floor(self):
        bounds = sr.axis_bounds([{"bubbles": [_bubble("a", 0.01, 1.0)]}])
        self.assertAlmostEqual(bounds["x_max"], 0.12)
        self.assertAlmostEqual(bounds["x_min"], -0.12)
        self.assertEqual(bounds["y_half_range"], 50)

    def test_bubble_radius_linear_and_clamped(self):
        self.assertEqual(sr.bubble_radius(0, 100), 22)
        self.assertEqual(sr.bubble_radius(100, 100), 52)
        self.assertEqual(sr.bubble_radius(50, 100), 37)


class OfficialGroupsTest(unittest.TestCase):
    def test_groups_by_official_industry_across_markets(self):
        groups = sr.official_industry_groups([
            ("2330", "半導體業", "上市"), ("3105", "半導體業", "上櫃"), ("2603", "航運業", "上市"),
        ])
        self.assertEqual(groups, {"半導體業": ["2330", "3105"], "航運業": ["2603"]})


@unittest.skipUnless(REA_PREVIEW.exists(), "REA 訪客版原件不在本機")
class ReaPreviewReproductionTest(unittest.TestCase):
    """用原站訪客版公開回應驗算公式；只讀外部研究原件，不複製進產品。"""

    @classmethod
    def setUpClass(cls):
        raw = json.loads(REA_PREVIEW.read_text(encoding="utf-8"))
        cls.days = sorted(raw, key=lambda item: item["latest_date"])
        cls.latest = cls.days[-1]

    def _site_bubbles(self, day):
        return [_bubble(b["概念股名稱"], float(b["x"]), float(b["y"]),
                        size=float(b["size"]), today=float(b["當日成交額"])) for b in day["bubbles"]]

    def test_x_and_y_recomputed_from_published_amounts(self):
        worst_x = worst_y = 0.0
        for day in self.days:
            values = [float(b["近二十日累計"]) for b in day["bubbles"]]
            mean = sum(values) / len(values)
            for b in day["bubbles"]:
                x = sr.group_x(float(b["近二十日累計"]), mean)
                y = sr.group_y(int(round(float(b["近五日累計"]) * 10 * YI)),
                               int(round(float(b["近二十日累計"]) * 10 * YI)))
                worst_x = max(worst_x, abs(x - float(b["x"])))
                worst_y = max(worst_y, abs(y - float(b["y"])))
        # FINDINGS §3／§4：原站族群數字先取整到 0.1，X 取整差 ≤0.07、Y ≤0.2（加浮點容差）
        self.assertLessEqual(worst_x, 0.07 + 1e-9)
        self.assertLessEqual(worst_y, 0.2 + 1e-9)
        print(f"[REA 驗算] X 最大差 {worst_x:.3f}、Y 最大差 {worst_y:.2f}")

    def test_quadrant_counts_on_1105(self):
        counts = sr.quadrant_counts(self._site_bubbles(self.latest))
        self.assertEqual(counts, {"leading": 7, "weakening": 11, "improving": 17, "lagging": 14})

    def test_efficiency_ranking_on_1105_with_timeline_m(self):
        snapshots = [{"bubbles": self._site_bubbles(day)} for day in self.days]
        m = sr.timeline_max_abs_y(snapshots)
        self.assertEqual(m, 339.0)
        rows = sr.efficiency_ranking(snapshots[-1]["bubbles"], m)
        self.assertEqual([round(r["score"], 1) for r in rows], [1420.1, 1229.3, 1145.0, 989.1, 952.8])

    def test_recovery_signals_on_1105(self):
        rows = sr.recovery_signals(self._site_bubbles(self.latest))
        self.assertEqual([r["y"] for r in rows], [16.5, 11.3, 9.8, 8.4])

    def test_axis_top_matches_screenshot(self):
        snapshots = [{"bubbles": self._site_bubbles(day)} for day in self.days]
        self.assertEqual(sr.axis_bounds(snapshots)["y_half_range"], 350)


if __name__ == "__main__":
    unittest.main()
