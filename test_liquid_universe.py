"""liquid_universe：大量股母體（本人 2026-11-09「整個網站都只做大量股」）。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import liquid_universe as lu

DAYS = [f"2026-10-{d:02d}" for d in range(1, 26)]


def make_db(path: Path, rows: list[tuple[str, str, int | None, int]]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE daily (date TEXT, code TEXT, kind TEXT, turnover INTEGER, suspended INTEGER)"
    )
    connection.executemany(
        "INSERT INTO daily VALUES (?, ?, 'common', ?, ?)", [(d, c, t, s) for d, c, t, s in rows]
    )
    connection.commit()
    connection.close()


def flat(code: str, turnover: int, days=DAYS) -> list[tuple[str, str, int, int]]:
    return [(d, code, turnover, 0) for d in days]


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.listed = root / "listed.sqlite"
        self.otc = root / "otc.sqlite"
        self.snapshots = root / "snaps"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sources(self, listed_codes, otc_codes) -> lu.Sources:
        return lu.Sources(self.listed, self.otc, frozenset(listed_codes), frozenset(otc_codes))


class RankingTest(Fixture):
    def test_ranks_both_markets_together_by_20day_average_turnover(self) -> None:
        make_db(self.listed, flat("1101", 500) + flat("2330", 900))
        make_db(self.otc, flat("6488", 700))
        result = lu.build_universe("2026-10-25", self.sources({"1101", "2330"}, {"6488"}), enter_rank=2, exit_rank=2)
        self.assertEqual([m["code"] for m in result["members"]], ["2330", "6488"])
        self.assertEqual(result["members"][1]["market"], "上櫃")
        self.assertEqual(result["members"][0]["avg_turnover_20d"], 900)
        self.assertEqual(result["members"][0]["rank"], 1)
        self.assertEqual(result["data_date"], "2026-10-25")

    def test_window_uses_only_last_20_trading_days_up_to_as_of(self) -> None:
        rows = [(d, "2330", 100 if d < "2026-10-06" else 10, 0) for d in DAYS]
        make_db(self.listed, rows)
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual(result["members"][0]["avg_turnover_20d"], 10)
        self.assertEqual(result["window"], {"start": "2026-10-06", "end": "2026-10-25", "days": 20})

    def test_missing_days_are_excluded_with_reason_not_zero_filled(self) -> None:
        make_db(self.listed, flat("2330", 900) + flat("1101", 800, DAYS[:-1]))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330", "1101"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual([m["code"] for m in result["members"]], ["2330"])
        self.assertEqual(result["excluded"]["1101"], "insufficient_window")

    def test_null_turnover_counts_as_missing(self) -> None:
        rows = flat("2330", 900)
        rows[-3] = (rows[-3][0], "2330", None, 0)  # 落在最近 20 日窗口內
        make_db(self.listed, rows)
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual(result["members"], [])
        self.assertEqual(result["excluded"]["2330"], "insufficient_window")

    def test_suspended_on_data_date_is_excluded(self) -> None:
        rows = flat("2330", 900)
        rows[-1] = (rows[-1][0], "2330", 0, 1)
        make_db(self.listed, rows)
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual(result["excluded"]["2330"], "suspended_on_data_date")

    def test_codes_outside_catalog_are_ignored(self) -> None:
        make_db(self.listed, flat("2330", 900) + flat("0050", 9999))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual([m["code"] for m in result["members"]], ["2330"])

    def test_core_holdings_always_present_and_outside_ranking(self) -> None:
        make_db(self.listed, flat("2330", 900))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertEqual(result["core_holdings"], ["0050"])
        self.assertNotIn("0050", [m["code"] for m in result["members"]])

    def test_ties_break_by_code_for_reproducibility(self) -> None:
        make_db(self.listed, flat("2330", 500) + flat("1101", 500))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330", "1101"}, set()), enter_rank=1, exit_rank=1)
        self.assertEqual([m["code"] for m in result["members"]], ["1101"])


class HysteresisTest(Fixture):
    def setUp(self) -> None:
        super().setUp()
        make_db(self.listed, flat("A", 900) + flat("B", 800) + flat("C", 700) + flat("D", 600))
        make_db(self.otc, [])
        self.src = self.sources({"A", "B", "C", "D"}, set())

    def test_previous_member_stays_until_it_falls_beyond_exit_rank(self) -> None:
        result = lu.build_universe("2026-10-25", self.src, enter_rank=2, exit_rank=3, previous={"C"})
        codes = [m["code"] for m in result["members"]]
        self.assertEqual(codes, ["A", "B", "C"])
        self.assertTrue(result["members"][2]["retained_by_hysteresis"])

    def test_previous_member_beyond_exit_rank_is_dropped(self) -> None:
        result = lu.build_universe("2026-10-25", self.src, enter_rank=2, exit_rank=3, previous={"D"})
        self.assertEqual([m["code"] for m in result["members"]], ["A", "B"])
        self.assertIn("D", result["dropped"])

    def test_without_previous_list_only_enter_rank_applies(self) -> None:
        result = lu.build_universe("2026-10-25", self.src, enter_rank=2, exit_rank=3)
        self.assertEqual([m["code"] for m in result["members"]], ["A", "B"])
        self.assertIsNone(result["previous_data_date"])

    def test_rolling_universe_is_reproducible_from_fixed_warmup(self) -> None:
        first = lu.rolling_universe("2026-10-25", self.src, warmup=3, enter_rank=2, exit_rank=3)
        second = lu.rolling_universe("2026-10-25", self.src, warmup=3, enter_rank=2, exit_rank=3)
        self.assertEqual(first, second)
        self.assertEqual(first["warmup"]["days"], 3)


class SnapshotTest(Fixture):
    def test_snapshot_is_written_atomically_and_reloads_identically(self) -> None:
        make_db(self.listed, flat("2330", 900))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        path = lu.save_snapshot(result, self.snapshots)
        self.assertEqual(path.name, "2026-10-25.json")
        self.assertEqual(lu.load_snapshot(self.snapshots, "2026-10-25"), result)
        self.assertFalse(list(self.snapshots.glob("*.partial")))

    def test_snapshot_rewrite_with_different_content_is_refused(self) -> None:
        make_db(self.listed, flat("2330", 900) + flat("1101", 100))
        make_db(self.otc, [])
        src = self.sources({"2330", "1101"}, set())
        lu.save_snapshot(lu.build_universe("2026-10-25", src, enter_rank=1, exit_rank=1), self.snapshots)
        self.assertEqual(lu.save_snapshot(lu.build_universe("2026-10-25", src, enter_rank=1, exit_rank=1), self.snapshots).name, "2026-10-25.json")
        with self.assertRaises(lu.SnapshotConflict):
            lu.save_snapshot(lu.build_universe("2026-10-25", src, enter_rank=2, exit_rank=2), self.snapshots)

    def test_snapshot_tampering_is_detected(self) -> None:
        make_db(self.listed, flat("2330", 900))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        path = lu.save_snapshot(result, self.snapshots)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["members"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(lu.SnapshotConflict):
            lu.load_snapshot(self.snapshots, "2026-10-25")


class ContainsTest(Fixture):
    def test_is_member_answers_for_core_and_ranked(self) -> None:
        make_db(self.listed, flat("2330", 900))
        make_db(self.otc, [])
        result = lu.build_universe("2026-10-25", self.sources({"2330"}, set()), enter_rank=5, exit_rank=5)
        self.assertTrue(lu.is_member(result, "2330"))
        self.assertTrue(lu.is_member(result, "0050"))
        self.assertFalse(lu.is_member(result, "9999"))


class FakeEtfs:
    """與 ``lu.RawEtfSource`` 同形：{代號: {"market", "name", "rows": {日: (成交金額, 停牌)}}}。"""

    def __init__(self, data: dict[str, dict]) -> None:
        self.data = data

    def series(self, days: list[str]) -> dict[str, dict]:
        return {code: {**item, "rows": {d: v for d, v in item["rows"].items() if d in days}}
                for code, item in self.data.items()}


def etf(market: str, turnover: int | None, days=DAYS, suspended_last: bool = False) -> dict:
    rows = {d: (turnover, 0) for d in days}
    if suspended_last:
        rows[days[-1]] = (0, 1)
    return {"market": market, "name": "測試ETF", "rows": rows}


class EtfRuleTest(Fixture):
    """V2：籌碼排行納入 ETF，但只收「若和普通股一起排，名次也在前 300」的 ETF（遲滯同樣適用）。"""

    def setUp(self) -> None:
        super().setUp()
        make_db(self.listed, flat("A", 900) + flat("B", 800) + flat("C", 700))
        make_db(self.otc, [])

    def src(self, etfs: dict[str, dict]) -> lu.Sources:
        return lu.Sources(self.listed, self.otc, frozenset({"A", "B", "C"}), frozenset(), FakeEtfs(etfs))

    def test_etf_enters_only_if_its_equivalent_rank_is_within_enter_rank(self) -> None:
        result = lu.build_universe("2026-10-25", self.src({"0050": etf("上市", 850), "00878": etf("上市", 750),
                                                             "006201": etf("上櫃", 100)}), enter_rank=2, exit_rank=3)
        etfs = {m["code"]: m for m in result["etf_members"]}
        self.assertEqual(sorted(etfs), ["0050"])
        self.assertEqual(etfs["0050"]["equivalent_rank"], 2)
        self.assertEqual(etfs["0050"]["avg_turnover_20d"], 850)
        # ETF 不擠掉普通股：普通股名單與沒有 ETF 時相同
        self.assertEqual([m["code"] for m in result["members"]], ["A", "B"])

    def test_etf_hysteresis_uses_exit_rank(self) -> None:
        etfs = {"00878": etf("上市", 750)}
        self.assertEqual(lu.build_universe("2026-10-25", self.src(etfs), enter_rank=2, exit_rank=3)["etf_members"], [])
        kept = lu.build_universe("2026-10-25", self.src(etfs), enter_rank=2, exit_rank=3, previous={"00878"})
        self.assertEqual([(m["code"], m["retained_by_hysteresis"]) for m in kept["etf_members"]], [("00878", True)])

    def test_etf_with_missing_day_or_suspended_is_excluded_not_zero_filled(self) -> None:
        holed = etf("上市", 9999)
        del holed["rows"][DAYS[-2]]
        result = lu.build_universe("2026-10-25", self.src({"00631L": holed, "00632R": etf("上市", 9999, suspended_last=True)}),
                                   enter_rank=2, exit_rank=3)
        self.assertEqual(result["etf_members"], [])
        self.assertEqual(result["etf_excluded"], {"00631L": "insufficient_window", "00632R": "suspended_on_data_date"})

    def test_without_etf_source_output_has_empty_etf_list(self) -> None:
        result = lu.build_universe("2026-10-25", self.sources({"A"}, set()), enter_rank=2, exit_rank=3)
        self.assertEqual(result["etf_members"], [])
        self.assertFalse(lu.is_etf_member(result, "0050"))

    def test_rolling_carries_etf_membership_between_days(self) -> None:
        first = lu.rolling_universe("2026-10-25", self.src({"0050": etf("上市", 850)}), warmup=3, enter_rank=2, exit_rank=3)
        self.assertTrue(lu.is_etf_member(first, "0050"))
        self.assertFalse(lu.is_member(first, "00878"))

    def test_ranking_exposes_ranks_of_non_members(self) -> None:
        ranked, excluded = lu.ranking("2026-10-25", self.src({}))
        self.assertEqual([(r["code"], r["rank"]) for r in ranked], [("A", 1), ("B", 2), ("C", 3)])
        self.assertEqual(excluded, {})


class EtfCodeTest(unittest.TestCase):
    def test_etf_codes(self) -> None:
        for code in ("0050", "006201", "00878", "00679B", "00631L", "00400A"):
            self.assertTrue(lu.is_etf_code(code), code)
        for code in ("2330", "020000", "1101B", "9103"):
            self.assertFalse(lu.is_etf_code(code), code)


class DefaultsTest(unittest.TestCase):
    def test_default_rule_is_enter_top300_exit_beyond400(self) -> None:
        self.assertEqual((lu.ENTER_RANK, lu.EXIT_RANK, lu.WINDOW_DAYS), (300, 400, 20))
        self.assertEqual(lu.RULE_VERSION, "liquid-v1")


if __name__ == "__main__":
    unittest.main()
