"""G1：金融獵場「策略選股」10 分頁，依 REA 結果實作的計算引擎契約。

規格主來源：DATA-ORIGIN-DIGEST-20261108.md〈策略選股（26項）〉與 strategy-source-readable.txt。
REA 查不到的條件一律輸出 unknown／unavailable，不自創定義、不捏造數字。
"""
from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import finhunt_screens as fs


DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]


class FakeSource:
    """與 ``fs.MarketSource`` 同形的注入來源；未列出的代號代表當日原件沒有該檔。"""

    def __init__(self, market: str, inst: dict[str, dict[str, dict]], quotes: dict[str, dict] | None = None,
                 days: list[str] | None = None) -> None:
        self.market = market
        self._inst = inst
        self._quotes = quotes or {}
        self._days = days or DAYS

    def trading_days(self) -> list[str]:
        return list(self._days)

    def institutional(self, day: str) -> dict[str, dict]:
        return self._inst.get(day, {})

    def quotes(self, day: str) -> dict[str, dict]:
        return self._quotes if day == self._days[-1] else {}


def daily(values: dict[str, list[int | None]], key: str = "foreign") -> dict[str, dict[str, dict]]:
    """values[code] 依 DAYS 排序；None 代表該日欄位空白，缺鍵代表該日原件沒有該檔。"""
    out: dict[str, dict[str, dict]] = {day: {} for day in DAYS}
    for code, series in values.items():
        for day, value in zip(DAYS, series):
            if value == "absent":
                continue
            out[day][code] = {"foreign": None, "trust": None, key: value}
    return out


UNIVERSE = [
    fs.Security("1101", "台泥", "上市"),
    fs.Security("2330", "台積電", "上市"),
    fs.Security("0061", "測試零開頭", "上櫃"),
    fs.Security("6488", "環球晶", "上櫃"),
    fs.Security("8069", "元太", "上櫃"),
]


class TabCatalogueTest(unittest.TestCase):
    def test_exactly_the_ten_rea_tabs_in_original_order(self) -> None:
        self.assertEqual(
            [tab.key for tab in fs.TABS],
            ["curated_list", "rotation_relative_value", "rotation_etf_constituent", "foreign_net_rank", "trust_net_rank", "broker_top15_net",
             "broker_concentration", "foreign_streak", "trust_streak", "broker_streak"],
        )
        self.assertEqual(fs.TAB_BY_KEY["rotation_relative_value"].display_name, "資金流入族群・相對低位階")
        for tab in fs.TABS:
            for banned in ("Jimmy", "私藏", "甜價", "關注", "主力"):
                self.assertNotIn(banned, tab.display_name + "".join(tab.conditions), tab.key)
        self.assertEqual(fs.TAB_BY_KEY["broker_concentration"].day_options, (1, 5, 20))
        self.assertEqual(fs.TAB_BY_KEY["foreign_net_rank"].day_options, (1, 3, 5, 10, 20))

    def test_rotation_checkbox_only_on_the_seven_chip_tabs(self) -> None:
        shown = {tab.key for tab in fs.TABS if tab.rotation_checkbox}
        self.assertEqual(shown, {tab.key for tab in fs.TABS} - {"curated_list", "rotation_relative_value", "rotation_etf_constituent"})

    def test_display_names_are_professional_and_unique(self) -> None:
        names = [tab.display_name for tab in fs.TABS]
        self.assertEqual(len(set(names)), 10)
        self.assertEqual(fs.TAB_BY_KEY["foreign_net_rank"].display_name, "外資淨買超排行")
        self.assertEqual(fs.TAB_BY_KEY["broker_concentration"].display_name, "券商籌碼集中度")

    def test_every_tab_reports_evidence_level(self) -> None:
        for tab in fs.TABS:
            result = fs.run_screen(tab.key, [], UNIVERSE)
            self.assertIn(result["evidence"]["method"], {"前端原碼", "推論", "未知", "人工名單", "原站重現", "會員驗證"}, tab.key)
            self.assertEqual(result["conditions"], list(tab.conditions))


class RankingTest(unittest.TestCase):
    def setUp(self) -> None:
        inst = daily({
            "6488": [0, 0, 0, 0, 5_000],
            "8069": [0, 0, 0, 0, 9_000],
            "0061": [0, 0, 0, 0, -2_000],
            "9999": [0, 0, 0, 0, 99_999],  # 不在普通股母體（例如 ETF）
        })
        quotes = {"6488": {"close": 512.0, "change": -52.0, "suspended": False}}
        self.source = FakeSource("上櫃", inst, quotes)

    def test_buy_one_day_sorted_desc_and_universe_only(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, direction="buy", days=1)
        self.assertEqual([row["code"] for row in result["items"]], ["8069", "6488"])
        self.assertNotIn("9999", [row["code"] for row in result["items"]])
        self.assertEqual(result["items"][0]["metric_shares"], 9_000)
        self.assertEqual(result["items"][0]["metric_lots"], 9.0)
        self.assertEqual(result["evidence"]["method"], "會員驗證")

    def test_sell_sorted_most_negative_first_and_code_kept_as_string(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, direction="sell", days=1)
        self.assertEqual([row["code"] for row in result["items"]], ["0061"])
        self.assertEqual(result["items"][0]["metric_shares"], -2_000)

    def test_quote_columns_and_change_pct(self) -> None:
        row = next(r for r in fs.run_screen("foreign_net_rank", [self.source], UNIVERSE)["items"] if r["code"] == "6488")
        self.assertEqual(row["close"], 512.0)
        self.assertEqual(row["change"], -52.0)
        self.assertEqual(row["change_pct"], round(-52.0 / 564.0 * 100, 2))
        missing = next(r for r in fs.run_screen("foreign_net_rank", [self.source], UNIVERSE)["items"] if r["code"] == "8069")
        self.assertIsNone(missing["close"])
        self.assertIsNone(missing["change_pct"])

    def test_n_day_sum_and_missing_day_is_unknown_not_zero(self) -> None:
        inst = daily({
            "6488": [0, 0, 100, 200, 300],
            "8069": [0, 0, "absent", 900, 900],
            "0061": [0, 0, None, 50, 50],
        })
        result = fs.run_screen("foreign_net_rank", [FakeSource("上櫃", inst)], UNIVERSE, days=3)
        self.assertEqual([row["code"] for row in result["items"]], ["6488"])
        self.assertEqual(result["items"][0]["metric_shares"], 600)
        self.assertEqual(result["missing_count"], 2)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["window_days"], ["2026-11-04", "2026-11-05", "2026-11-06"])

    def test_trust_uses_trust_column(self) -> None:
        inst = daily({"6488": [0, 0, 0, 0, 7]}, key="trust")
        result = fs.run_screen("trust_net_rank", [FakeSource("上櫃", inst)], UNIVERSE)
        self.assertEqual([row["code"] for row in result["items"]], ["6488"])

    def test_days_must_be_a_rea_option(self) -> None:
        with self.assertRaises(ValueError):
            fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, days=2)
        with self.assertRaises(ValueError):
            fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, direction="hold")

    def test_not_enough_trading_days_is_missing(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, days=10)
        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["items"], [])

    def test_backend_cap_of_fifty(self) -> None:
        many = [fs.Security(f"{1000 + i}", f"股{i}", "上櫃") for i in range(80)]
        inst = {DAYS[-1]: {s.code: {"foreign": 1_000 + i, "trust": None} for i, s in enumerate(many)}}
        result = fs.run_screen("foreign_net_rank", [FakeSource("上櫃", inst)], many)
        self.assertEqual(result["total"], 50)
        self.assertEqual(result["items"][0]["code"], "1079")


class ConsecutiveTest(unittest.TestCase):
    def test_streak_counts_back_from_latest_and_zero_or_missing_breaks(self) -> None:
        inst = daily({
            "6488": [5, 5, 5, 5, 5],        # 全部可得天數都買超 → 5，且為下限
            "8069": [5, 0, 5, 5, 5],        # 0 中斷 → 3
            "0061": [5, 5, None, 5, 5],     # 空值中斷 → 2
            "1101": [5, 5, 5, 5, -1],       # 最新日賣超 → 不入買超榜
        })
        universe = UNIVERSE
        result = fs.run_screen("foreign_streak", [FakeSource("上櫃", inst)], universe, direction="buy")
        self.assertEqual([(r["code"], r["streak_days"]) for r in result["items"]], [("6488", 5), ("8069", 3), ("0061", 2)])
        self.assertTrue(result["items"][0]["streak_is_lower_bound"])
        self.assertFalse(result["items"][1]["streak_is_lower_bound"])
        self.assertEqual(result["evidence"]["method"], "會員驗證")
        self.assertTrue(any("中斷" in note for note in result["evidence"]["notes"]))

    def test_sell_streak_and_tie_by_code(self) -> None:
        inst = daily({"8069": [0, 0, 0, -1, -1], "6488": [0, 0, 0, -1, -1]})
        result = fs.run_screen("foreign_streak", [FakeSource("上櫃", inst)], UNIVERSE, direction="sell")
        self.assertEqual([r["code"] for r in result["items"]], ["6488", "8069"])

    def test_limit_fifty(self) -> None:
        many = [fs.Security(f"{2000 + i}", f"股{i}", "上櫃") for i in range(70)]
        inst = {day: {s.code: {"foreign": None, "trust": 3} for s in many} for day in DAYS}
        result = fs.run_screen("trust_streak", [FakeSource("上櫃", inst)], many)
        self.assertEqual(result["total"], 50)


class UnknownAndUnavailableTest(unittest.TestCase):
    def test_etf_constituent_tab_stays_unknown_without_invented_rule(self) -> None:
        rotation = [{"date": "2026-11-06", "groups": [{"name": "半導體", "y": 1.0, "codes": ["6488"]}]}]
        result = fs.run_screen("rotation_etf_constituent", [], UNIVERSE, rotation=rotation)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["items"], [])
        self.assertTrue(result["unknown"])
        self.assertEqual(result["rotation_member_count"], 1)

    def test_relative_value_without_inputs_returns_no_list_and_says_why(self) -> None:
        rotation = [{"date": "2026-11-06", "groups": [{"name": "半導體", "y": 1.0, "codes": ["6488"]}]}]
        result = fs.run_screen("rotation_relative_value", [], UNIVERSE, rotation=rotation)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["total"])
        self.assertIn("待發布流程接線", result["unknown"][0])

    def test_broker_branch_tabs_are_unavailable(self) -> None:
        for key in ("broker_top15_net", "broker_concentration", "broker_streak"):
            result = fs.run_screen(key, [], UNIVERSE)
            self.assertEqual(result["state"], "unavailable", key)
            self.assertEqual(result["items"], [])
            self.assertIsNone(result["total"])

    def test_curated_list_is_not_applicable(self) -> None:
        result = fs.run_screen("curated_list", [], UNIVERSE)
        self.assertEqual(result["state"], "not_applicable")
        self.assertEqual(result["evidence"]["method"], "人工名單")


class RotationFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        inst = daily({"6488": [0, 0, 0, 0, 5], "8069": [0, 0, 0, 0, 9], "0061": [0, 0, 0, 0, 1]})
        self.source = FakeSource("上櫃", inst)
        self.rotation = [
            {"date": "2026-11-04", "groups": [{"name": "舊", "y": 5.0, "codes": ["0061"]}]},
            {"date": "2026-11-05", "groups": [
                {"name": "觀望", "y": 0.0, "codes": ["6488"]},
                {"name": "退潮", "y": -0.1, "codes": ["8069"]},
                {"name": "壞值", "y": float("nan"), "codes": ["0061"]},
            ]},
        ]

    def test_star_uses_latest_snapshot_and_y_at_least_zero(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, rotation=self.rotation)
        stars = {row["code"]: row["rotation_star"] for row in result["items"]}
        self.assertEqual(stars, {"8069": False, "6488": True, "0061": False})
        self.assertEqual(result["rotation_date"], "2026-11-05")

    def test_only_rotation_filters_after_cap(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, rotation=self.rotation, only_rotation=True)
        self.assertEqual([row["code"] for row in result["items"]], ["6488"])
        self.assertEqual(result["total"], 1)

    def test_only_rotation_without_snapshot_is_missing_not_unfiltered(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], UNIVERSE, only_rotation=True)
        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["items"], [])

    def test_only_rotation_ignored_where_rea_hides_checkbox(self) -> None:
        result = fs.run_screen("rotation_etf_constituent", [], UNIVERSE, rotation=self.rotation, only_rotation=True)
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["only_rotation_applied"])


class RelativeValueTest(unittest.TestCase):
    """會員研究 127 檔全對：輪動族群成分股（不加象限條件），且個股 20 日乖離 < 所屬市場指數 20 日乖離。"""

    def setUp(self) -> None:
        self.groups = {"半導體業": ["2330", "6488"], "水泥工業": ["1101"]}
        self.bias = {
            "stocks": {"2330": -0.20, "6488": -0.10, "1101": -0.30, "8069": -0.50},
            "indexes": {"上市": -0.13943, "上櫃": -0.20947},
            "index_sources": {"上市": "加權指數", "上櫃": "櫃買指數"},
            "data_date": "2026-11-06",
        }

    def run_tab(self, **kwargs):
        return fs.run_screen("rotation_relative_value", [], UNIVERSE, groups=self.groups, bias=self.bias, **kwargs)

    def test_selects_members_with_bias_below_their_market_index(self) -> None:
        result = self.run_tab()
        self.assertEqual(result["state"], "available")
        # 2330 −20% < 上市 −13.9% ✓；1101 −30% < −13.9% ✓；6488 −10% 不低於上櫃 −20.9% ✗；8069 非成分股 ✗
        self.assertEqual([r["code"] for r in result["items"]], ["1101", "2330"])
        row = result["items"][1]
        self.assertEqual(row["bias20"], -0.20)
        self.assertEqual(row["index_bias20"], -0.13943)
        self.assertEqual(row["index_name"], "加權指數")
        self.assertEqual(row["group"], "半導體業")
        self.assertEqual(result["evidence"]["method"], "原站重現")

    def test_no_quadrant_filter_is_applied(self) -> None:
        rotation = [{"date": "2026-11-06", "groups": [{"name": "水泥工業", "y": -5.0, "codes": ["1101"]}]}]
        self.assertIn("1101", [r["code"] for r in self.run_tab(rotation=rotation)["items"]])

    def test_missing_bias_is_unknown_not_zero(self) -> None:
        self.bias["stocks"]["2330"] = None
        result = self.run_tab()
        self.assertNotIn("2330", [r["code"] for r in result["items"]])
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["state"], "partial")

    def test_group_definition_difference_is_disclosed(self) -> None:
        notes = " ".join(self.run_tab()["evidence"]["notes"])
        self.assertIn("官方產業別", notes)
        self.assertIn("127", notes)


class UsageNotesTest(unittest.TestCase):
    def test_operator_principles_are_labelled_not_signals(self) -> None:
        for key in ("foreign_net_rank", "trust_net_rank", "foreign_streak", "trust_streak", "rotation_relative_value"):
            notes = fs.TAB_USAGE_NOTES[key]
            self.assertTrue(notes, key)
            for note in notes:
                self.assertEqual(note["grade"], "B", key)
                self.assertIn("非網站計算訊號", note["label"])
                self.assertTrue(note["source"])
                for banned in ("Jimmy", "私藏", "甜價", "關注", "主力", "輪動", "觀望", "退潮", "CP值", "抄底", "金融獵場"):
                    self.assertNotIn(banned, note["text"] + note["label"], key)
        result = fs.run_screen("foreign_net_rank", [], UNIVERSE)
        self.assertEqual(result["usage_notes"], fs.TAB_USAGE_NOTES["foreign_net_rank"])

    def test_round2_evidence_is_cited_for_sum_and_streak(self) -> None:
        self.assertIn("26/26", " ".join(fs.RANK_NOTES))
        self.assertIn("180/180", " ".join(fs.STREAK_NOTES))


class MarketCoverageAndPagingTest(unittest.TestCase):
    def test_market_without_source_is_reported_partial(self) -> None:
        inst = daily({"6488": [0, 0, 0, 0, 5]})
        result = fs.run_screen("foreign_net_rank", [FakeSource("上櫃", inst)], UNIVERSE)
        self.assertEqual(result["uncovered_markets"], ["上市"])
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["data_dates"], {"上櫃": "2026-11-06"})

    def test_two_markets_merge_into_one_ranking(self) -> None:
        otc = FakeSource("上櫃", daily({"6488": [0, 0, 0, 0, 5]}))
        listed = FakeSource("上市", daily({"2330": [0, 0, 0, 0, 50]}))
        result = fs.run_screen("foreign_net_rank", [otc, listed], UNIVERSE)
        self.assertEqual([(r["code"], r["market"]) for r in result["items"]], [("2330", "上市"), ("6488", "上櫃")])
        self.assertEqual(result["uncovered_markets"], [])

    def test_page_size_fifty(self) -> None:
        many = [fs.Security(f"{3000 + i}", f"股{i}", "上櫃") for i in range(60)]
        inst = {day: {s.code: {"foreign": None, "trust": 2} for s in many} for day in DAYS}
        first = fs.run_screen("trust_streak", [FakeSource("上櫃", inst)], many, page=1)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["page_size"], 50)
        self.assertEqual(first["pages"], 1)


class LiquidUniverseTest(unittest.TestCase):
    """V2：排行母體＝大量股普通股＋同口徑前 300 名的 ETF（REA：原站排行含 ETF），每列標示類型。"""

    def setUp(self) -> None:
        inst = daily({"2330": [0, 0, 0, 0, 50], "1101": [0, 0, 0, 0, 40], "00878": [0, 0, 0, 0, 90],
                      "00940": [0, 0, 0, 0, 99]})
        self.source = FakeSource("上市", inst)
        self.universe = [fs.Security("2330", "台積電", "上市"), fs.Security("1101", "台泥", "上市"),
                         fs.Security("00878", "國泰永續高股息", "上市", "etf")]

    def test_etf_in_universe_is_ranked_and_labelled(self) -> None:
        result = fs.run_screen("foreign_net_rank", [self.source], self.universe)
        rows = [(r["code"], r["security_type"]) for r in result["items"]]
        self.assertEqual(rows, [("00878", "ETF"), ("2330", "普通股"), ("1101", "普通股")])
        self.assertNotIn("00940", [r["code"] for r in result["items"]])  # 不在大量股名單的 ETF 不進排行

    def test_universe_note_states_liquid_rule_and_etf_inclusion(self) -> None:
        notes = " ".join(fs.run_screen("foreign_net_rank", [self.source], self.universe)["evidence"]["notes"])
        self.assertIn("大量股", notes)
        self.assertIn("ETF", notes)
        self.assertNotIn("待決定", notes)

    def test_streak_also_labels_type(self) -> None:
        result = fs.run_screen("foreign_streak", [self.source], self.universe)
        self.assertEqual({r["code"]: r["security_type"] for r in result["items"]}["00878"], "ETF")

    def test_relative_value_ignores_etfs_even_if_in_universe(self) -> None:
        groups = {"半導體業": ["2330", "00878"]}
        bias = {"stocks": {"2330": -0.2, "00878": -0.5}, "indexes": {"上市": -0.1}, "data_date": "2026-11-06"}
        result = fs.run_screen("rotation_relative_value", [], self.universe, groups=groups, bias=bias)
        self.assertEqual([r["code"] for r in result["items"]], ["2330"])

    def test_universe_filter_helper_keeps_members_and_etfs(self) -> None:
        universe = {"members": [{"code": "2330", "market": "上市"}], "core_holdings": ["0050"],
                    "etf_members": [{"code": "00878", "name": "國泰永續高股息", "market": "上市"}]}
        commons = [fs.Security("2330", "台積電", "上市"), fs.Security("1101", "台泥", "上市")]
        picked = fs.liquid_universe_securities(universe, commons)
        self.assertEqual([(s.code, s.kind) for s in picked], [("2330", "common"), ("00878", "etf")])


class OtcAdapterTest(unittest.TestCase):
    def test_reads_institutional_and_quotes_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            market_db = Path(tmp) / "otc_market.sqlite"
            history_db = Path(tmp) / "otc_history.sqlite"
            with closing(sqlite3.connect(market_db)) as c, c:
                c.execute("CREATE TABLE institutional (date TEXT, code TEXT, name TEXT, foreign_net INTEGER, trust_net INTEGER)")
                c.executemany("INSERT INTO institutional VALUES (?,?,?,?,?)", [
                    ("2026-11-05", "6488", "環球晶", 10, None), ("2026-11-06", "6488", "環球晶", 20, -3)])
            with closing(sqlite3.connect(history_db)) as c, c:
                c.execute("CREATE TABLE daily (date TEXT, code TEXT, close REAL, change REAL, suspended INTEGER)")
                c.execute("INSERT INTO daily VALUES ('2026-11-06','6488',512.0,-52.0,0)")
            source = fs.OtcSqliteSource(market_db, history_db)
            self.assertEqual(source.trading_days(), ["2026-11-05", "2026-11-06"])
            self.assertEqual(source.institutional("2026-11-05")["6488"], {"foreign": 10, "trust": None})
            self.assertEqual(source.quotes("2026-11-06")["6488"]["close"], 512.0)
            before = market_db.read_bytes()
            fs.run_screen("foreign_net_rank", [source], [fs.Security("6488", "環球晶", "上櫃")], days=1)
            self.assertEqual(market_db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
