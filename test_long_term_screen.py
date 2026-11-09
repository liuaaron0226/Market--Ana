from __future__ import annotations

import copy
import random
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fundamentals_history import FundamentalsHistory, _monthly_available_on, _quarter_available_on

import long_term_screen as long_term
from long_term_screen import METHOD_VERSION, PARAMETERS, expected_latest_periods, screen_long_term

DATA_DATE = "2026-11-06"


def _trading_days(count: int, end: date = date(2026, 11, 6)) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _months(start: str, end: str) -> list[str]:
    year, month = map(int, start.split("-"))
    stop = tuple(map(int, end.split("-")))
    out: list[str] = []
    while (year, month) <= stop:
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


QUARTERS = ["2024-Q3", "2024-Q4", "2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]


def _monthly(code: str, *, growth: float = 1.15, last_two: tuple[float, float] | None = None) -> list[dict]:
    """前一年每月 100,000 仟元；最近 12 個月乘 growth。last_two 覆寫最後兩月倍率。"""
    periods = _months("2023-10", "2026-09")
    rows = []
    for index, period in enumerate(periods):
        base = 100_000
        multiplier = growth if index >= len(periods) - 12 else 1.0
        if last_two is not None and index >= len(periods) - 2:
            multiplier = last_two[index - (len(periods) - 2)]
        available_on, basis = _monthly_available_on(period)
        rows.append({
            "code": code, "name": code, "market": "listed", "period": period,
            "available_on": available_on, "available_on_basis": basis,
            "revenue_thousand_twd": round(base * multiplier), "unit": "TWD_thousand",
        })
    return rows


def _quarterly(code: str, *, single_ni: dict[int, int] | None = None, ocf_factor: float = 1.0,
               debt_ratio: float = 40.0, form: str = "general") -> list[dict]:
    """單季淨利：2024 年 1,000,000、2025 年 1,100,000、2026 年 1,250,000（仟元），轉成年初至今口徑。"""
    single_ni = single_ni or {2024: 1_000_000, 2025: 1_100_000, 2026: 1_250_000}
    rows = []
    for period in QUARTERS:
        year, quarter = int(period[:4]), int(period[-1])
        ytd_ni = single_ni[year] * quarter
        ytd_ocf = round(single_ni[year] * ocf_factor) * quarter
        available_on, basis = _quarter_available_on(period)
        general = form == "general"
        rows.append({
            "code": code, "name": code, "market": "listed", "period": period,
            "available_on": available_on, "available_on_basis": basis,
            "value_basis": "fiscal_year_to_date", "statement_form": form,
            "revenue_thousand_twd": ytd_ni * 10,
            "gross_profit_thousand_twd": ytd_ni * 3 if general else None,
            "operating_income_thousand_twd": ytd_ni * 2 if general else None,
            "net_income_thousand_twd": ytd_ni,
            "eps_twd": round(ytd_ni / 1_000_000, 4),
            "operating_cash_flow_thousand_twd": ytd_ocf,
            "total_assets_thousand_twd": 10_000_000,
            "total_liabilities_thousand_twd": round(10_000_000 * debt_ratio / 100) if general else None,
            "debt_ratio_pct": debt_ratio if general else None,
            "applicability": {
                "standard_income_statement": {"applicable": general, "reason": None if general else "sector_specific_statement_form"},
                "operating_cash_flow_quality": {"applicable": general, "reason": None if general else "sector_specific_statement_form"},
                "debt_ratio": {"applicable": general, "reason": None if general else "sector_specific_statement_form"},
            },
        })
    return rows


def _bars(*, start: float = 50.0, end: float = 58.0, count: int = 130, turnover: int = 30_000_000) -> list[dict]:
    days = _trading_days(count)
    step = (end - start) / (count - 1)
    rows = []
    for index, day in enumerate(days):
        close = round(start + step * index, 2)
        rows.append({
            "date": day, "open": close, "high": round(close + 0.5, 2), "low": round(close - 0.5, 2),
            "close": close, "volume": 500_000, "turnover": turnover, "suspended": False,
        })
    return rows


def _fixture(codes: tuple[str, ...] = ("1101",)) -> dict:
    monthly: list[dict] = []
    quarterly: list[dict] = []
    universe = []
    bars = {}
    for code in codes:
        monthly += _monthly(code)
        quarterly += _quarterly(code)
        universe.append({"code": code, "name": f"測試{code}", "market": "TWSE", "industry": "水泥工業"})
        bars[code] = _bars()
    return {
        "data_date": DATA_DATE,
        "universe": universe,
        "fundamentals": FundamentalsHistory(monthly=monthly, quarterly=quarterly),
        "bars_by_code": bars,
        "source_states": {
            "catalog": {"state": "ok"},
            "fundamentals": {"state": "ok", "latest_monthly_period": "2026-09", "latest_quarter_period": "2026-Q2"},
            "price": {"state": "ok", "as_of": DATA_DATE},
        },
    }


def _replace(fixture: dict, code: str, *, monthly: list[dict] | None = None, quarterly: list[dict] | None = None) -> dict:
    data = fixture["fundamentals"].to_dict()
    if monthly is not None:
        data["monthly"] = [row for row in data["monthly"] if row["code"] != code] + monthly
    if quarterly is not None:
        data["quarterly"] = [row for row in data["quarterly"] if row["code"] != code] + quarterly
    return {**fixture, "fundamentals": FundamentalsHistory(**data)}


def json_load(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


class FakeValuation:
    """同業估值供應者替身：percentiles[(code, metric)] = industry_percentile 形狀；markets 為已接入市場。"""

    def __init__(self, percentiles: dict | None = None, markets: tuple[str, ...] = ("TWSE",), day: str = DATA_DATE) -> None:
        self.percentiles = percentiles or {}
        self.markets = markets
        self.day = day
        self.calls: list[tuple[str, str, str]] = []

    def market_available(self, market: str, data_date: str) -> bool:
        return market in self.markets

    def percentile(self, code: str, market: str, data_date: str, metric: str) -> dict:
        self.calls.append((code, market, metric))
        found = self.percentiles.get((code, metric))
        if found is None:
            return {"reason": "no_data", "percentile": None, "value": None, "date": self.day, "peers_with_value": 0}
        return {"reason": "ok", "date": self.day, "peers_with_value": 20, **found}


def _reason_codes(result: dict, code: str) -> set[str]:
    row = next(item for item in result["reconciliation"] if item["code"] == code)
    return {reason["code"] for reason in row["reasons"]}


class LongTermScreenTest(unittest.TestCase):
    def test_qualifying_stock_gets_full_long_term_card(self) -> None:
        result = screen_long_term(**_fixture())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method_version"], METHOD_VERSION)
        card = result["candidates"][0]
        self.assertEqual(card["code"], "1101")
        self.assertEqual(card["horizon"], "6～12 個月")
        for key in ("main_judgment", "supporting_evidence", "opposing_evidence", "entry_trigger", "do_not_chase",
                    "target_observation_zone", "defense", "invalidation", "data_availability", "gaps"):
            self.assertIn(key, card)
        self.assertEqual(card["data_availability"]["monthly_revenue"]["period"], "2026-09")
        self.assertEqual(card["data_availability"]["monthly_revenue"]["available_on"], "2026-10-15")
        self.assertEqual(card["data_availability"]["quarterly"]["period"], "2026-Q2")
        self.assertTrue(all("available_on" in item for item in card["supporting_evidence"] if item["source"].startswith("fundamentals")))
        self.assertTrue(any("估值" in gap for gap in card["gaps"]))

    def test_ttm_and_ytd_values_from_year_to_date_quarters(self) -> None:
        card = screen_long_term(**_fixture())["candidates"][0]
        metrics = card["metrics"]
        # TTM = YTD(2026Q2) + FY2025 - YTD(2025Q2) = 2.5M + 4.4M - 2.2M
        self.assertEqual(metrics["ttm_net_income_thousand_twd"], 4_700_000)
        self.assertEqual(metrics["prev_ttm_net_income_thousand_twd"], 4_550_000)
        self.assertAlmostEqual(metrics["ttm_eps_twd"], 4.7)
        self.assertAlmostEqual(metrics["ytd_net_income_yoy_pct"], 13.6364, places=3)
        self.assertAlmostEqual(metrics["revenue_yoy_12m_pct"], 15.0)
        self.assertAlmostEqual(metrics["ocf_to_net_income_ttm"], 1.0)

    def test_revenue_yoy_12m_boundary(self) -> None:
        fixture = _fixture()
        at = screen_long_term(**_replace(fixture, "1101", monthly=_monthly("1101", growth=1.05)))
        below = screen_long_term(**_replace(fixture, "1101", monthly=_monthly("1101", growth=1.04999)))
        self.assertEqual(at["status"], "ok")
        self.assertIn("revenue_yoy_12m_low", _reason_codes(below, "1101"))

    def test_zero_base_month_inside_window_is_not_a_crash(self) -> None:
        rows = _monthly("1101")
        rows[len(rows) - 24 + 3]["revenue_thousand_twd"] = 0  # 去年同期某月營收為 0
        result = screen_long_term(**_replace(_fixture(), "1101", monthly=rows))
        self.assertIn("revenue_base_nonpositive", _reason_codes(result, "1101"))

    def test_two_consecutive_negative_monthly_yoy_excludes(self) -> None:
        fixture = _replace(_fixture(), "1101", monthly=_monthly("1101", last_two=(0.99, 0.98)))
        result = screen_long_term(**fixture)
        self.assertIn("revenue_yoy_two_month_negative", _reason_codes(result, "1101"))

    def test_ocf_quality_boundary(self) -> None:
        fixture = _fixture()
        at = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", ocf_factor=0.8)))
        below = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", ocf_factor=0.79)))
        self.assertEqual(at["status"], "ok")
        self.assertIn("cash_flow_quality_low", _reason_codes(below, "1101"))

    def test_debt_ratio_boundary(self) -> None:
        fixture = _fixture()
        at = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", debt_ratio=60.0)))
        above = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", debt_ratio=60.01)))
        self.assertEqual(at["status"], "ok")
        self.assertIn("debt_ratio_high", _reason_codes(above, "1101"))

    def test_profit_growth_and_eps_trend_gates(self) -> None:
        fixture = _fixture()
        slow = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", single_ni={2024: 1_000_000, 2025: 1_100_000, 2026: 1_200_000})))
        self.assertIn("net_income_growth_low", _reason_codes(slow, "1101"))
        loss = screen_long_term(**_replace(fixture, "1101", quarterly=_quarterly("1101", single_ni={2024: 1_000_000, 2025: -1_000_000, 2026: -500_000})))
        self.assertIn("ttm_eps_nonpositive", _reason_codes(loss, "1101"))

    def test_liquidity_boundary(self) -> None:
        fixture = _fixture()
        at = copy.deepcopy(fixture)
        at["bars_by_code"]["1101"] = _bars(turnover=PARAMETERS["min_avg_turnover_twd"])
        below = copy.deepcopy(fixture)
        below["bars_by_code"]["1101"] = _bars(turnover=PARAMETERS["min_avg_turnover_twd"] - 1)
        self.assertEqual(screen_long_term(**at)["status"], "ok")
        self.assertIn("liquidity_low", _reason_codes(screen_long_term(**below), "1101"))

    def test_as_of_hides_future_announcements(self) -> None:
        fixture = _fixture()
        data = fixture["fundamentals"].to_dict()
        crash_month = dict(data["monthly"][-1], period="2026-10", available_on="2026-11-16", revenue_thousand_twd=1)
        crash_quarter = dict(next(row for row in data["quarterly"] if row["period"] == "2026-Q2"),
                             period="2026-Q3", available_on="2026-11-16", net_income_thousand_twd=-99_999_999, eps_twd=-99.0)
        data["monthly"].append(crash_month)
        data["quarterly"].append(crash_quarter)
        result = screen_long_term(**{**fixture, "fundamentals": FundamentalsHistory(**data)})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["data_availability"]["monthly_revenue"]["period"], "2026-09")

    def test_engine_reads_fundamentals_only_through_as_of(self) -> None:
        fixture = _fixture()
        real = fixture["fundamentals"]
        calls: list[tuple[str, str]] = []

        class Spy:
            def as_of(self, code: str, as_of: str) -> dict:
                calls.append((code, as_of))
                return real.as_of(code, as_of)

        result = screen_long_term(**{**fixture, "fundamentals": Spy()})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls, [("1101", DATA_DATE)])

    def test_financial_industry_is_not_applicable(self) -> None:
        fixture = _fixture(("1101", "2881"))
        fixture["universe"][1]["industry"] = "金融保險業"
        result = screen_long_term(**fixture)
        row = next(item for item in result["reconciliation"] if item["code"] == "2881")
        self.assertEqual(row["outcome"], "excluded")
        self.assertEqual({reason["category"] for reason in row["reasons"]}, {"not_applicable"})
        self.assertEqual(result["counts"]["not_applicable"], 1)
        self.assertEqual([item["code"] for item in result["candidates"]], ["1101"])

    def test_sector_specific_statement_form_is_not_applicable(self) -> None:
        fixture = _replace(_fixture(), "1101", quarterly=_quarterly("1101", form="sector_specific"))
        result = screen_long_term(**fixture)
        self.assertIn("sector_not_applicable", _reason_codes(result, "1101"))

    def test_single_stock_missing_latest_revenue_is_missing_data(self) -> None:
        fixture = _fixture(("1101", "1102"))
        fixture = _replace(fixture, "1102", monthly=[row for row in _monthly("1102") if row["period"] != "2026-09"])
        result = screen_long_term(**fixture)
        row = next(item for item in result["reconciliation"] if item["code"] == "1102")
        self.assertEqual({reason["category"] for reason in row["reasons"]}, {"missing_data"})
        self.assertIn("revenue_latest_missing", _reason_codes(result, "1102"))
        self.assertEqual(result["status"], "ok")

    def test_empty_result_differs_from_source_failure_and_stale(self) -> None:
        fixture = _fixture()
        empty = screen_long_term(**_replace(fixture, "1101", monthly=_monthly("1101", growth=1.0)))
        self.assertEqual(empty["status"], "no_candidates")
        self.assertEqual(empty["errors"], [])
        self.assertEqual(empty["counts"]["evaluated"], 1)

        failed = copy.deepcopy(fixture)
        failed["source_states"]["fundamentals"] = {"state": "failed", "message": "MOPS 原件 SHA 不符"}
        failed_result = screen_long_term(**failed)
        self.assertEqual(failed_result["status"], "source_failure")
        self.assertEqual(failed_result["counts"]["not_evaluated"], 1)
        self.assertEqual(failed_result["reconciliation"][0]["outcome"], "not_evaluated")

        stale = copy.deepcopy(fixture)
        stale["source_states"]["fundamentals"]["latest_monthly_period"] = "2026-08"
        self.assertEqual(screen_long_term(**stale)["status"], "stale")

        price_stale = copy.deepcopy(fixture)
        price_stale["source_states"]["price"]["as_of"] = "2026-11-05"
        self.assertEqual(screen_long_term(**price_stale)["status"], "stale")

    def test_majority_missing_data_is_source_failure_not_empty(self) -> None:
        codes = ("1101", "1102", "1103")
        fixture = _fixture(codes)
        for code in ("1102", "1103"):
            fixture = _replace(fixture, code, monthly=[row for row in _monthly(code) if row["period"] != "2026-09"])
        result = screen_long_term(**fixture)
        self.assertEqual(result["status"], "source_failure")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["errors"][0]["source"], "fundamentals")

    def test_at_most_five_and_no_padding(self) -> None:
        codes = tuple(str(1101 + index) for index in range(7))
        result = screen_long_term(**_fixture(codes))
        self.assertEqual(len(result["candidates"]), PARAMETERS["max_candidates"])
        cutoffs = [item for item in result["reconciliation"] if any(r["code"] == "rank_cutoff" for r in item["reasons"])]
        self.assertEqual(len(cutoffs), 2)
        self.assertEqual(len({item["code"] for item in result["candidates"]}), 5)

    def test_low_base_profit_growth_does_not_dominate_ranking(self) -> None:
        fixture = _fixture(("1101", "1102"))
        # 1102：去年淨利極低，累計淨利年增數千%，但營收只成長 6%；1101 營收成長 30%、淨利成長 50%。
        fixture = _replace(fixture, "1102", monthly=_monthly("1102", growth=1.06),
                           quarterly=_quarterly("1102", single_ni={2024: 10_000, 2025: 10_000, 2026: 1_000_000}))
        fixture = _replace(fixture, "1101", monthly=_monthly("1101", growth=1.30),
                           quarterly=_quarterly("1101", single_ni={2024: 1_000_000, 2025: 1_000_000, 2026: 1_600_000}))
        result = screen_long_term(**fixture)
        self.assertEqual([card["code"] for card in result["candidates"]], ["1101", "1102"])
        low_base = result["candidates"][1]
        self.assertTrue(any(item["factor"] == "低基期" for item in low_base["opposing_evidence"]))

    def test_valuation_point_is_reserved_and_reports_not_connected(self) -> None:
        result = screen_long_term(**_fixture())
        valuation = result["candidates"][0]["valuation"]
        self.assertEqual(valuation["status"], "not_connected")
        self.assertIsNone(valuation["pe_percentile_in_industry"])
        self.assertIsNone(valuation["pb_percentile_in_industry"])
        self.assertIn("data/valuation-history/", valuation["message"])
        self.assertEqual(long_term.peer_valuation("1101", "水泥工業", DATA_DATE, None)["status"], "not_connected")
        self.assertFalse(any("不存在" in gap for gap in result["gaps"]))

    def test_deterministic_and_order_independent(self) -> None:
        codes = tuple(str(1101 + index) for index in range(6))
        first = screen_long_term(**_fixture(codes))
        shuffled = _fixture(codes)
        random.Random(7).shuffle(shuffled["universe"])
        second = screen_long_term(**shuffled)
        self.assertEqual(first, second)

    def test_timing_decision_is_wait_when_extended_or_below_quarter_line(self) -> None:
        fixture = _fixture()
        inside = screen_long_term(**fixture)["candidates"][0]
        self.assertEqual(inside["decision"], "符合進場條件")

        extended = copy.deepcopy(fixture)
        extended["bars_by_code"]["1101"][-1].update(close=90.0, high=90.5, open=90.0, low=89.5)
        card = screen_long_term(**extended)["candidates"][0]
        self.assertEqual(card["decision"], "等待")
        self.assertIn("不追價", card["main_judgment"])

        dip = copy.deepcopy(fixture)
        dip["bars_by_code"]["1101"][-1].update(close=55.5, high=56.0, open=55.5, low=55.0)
        card = screen_long_term(**dip)["candidates"][0]
        self.assertEqual(card["decision"], "等待")

    def test_price_below_defense_is_excluded(self) -> None:
        fixture = _fixture()
        fixture["bars_by_code"]["1101"][-1].update(close=40.0, high=40.5, open=40.0, low=39.5)
        self.assertIn("price_below_defense", _reason_codes(screen_long_term(**fixture), "1101"))

    def test_price_history_short_is_missing_data(self) -> None:
        fixture = _fixture()
        fixture["bars_by_code"]["1101"] = _bars(count=100)
        self.assertIn("price_history_short", _reason_codes(screen_long_term(**fixture), "1101"))

    def test_expected_latest_periods_follow_statutory_deadlines(self) -> None:
        self.assertEqual(expected_latest_periods("2026-11-06"), ("2026-09", "2026-Q2"))
        self.assertEqual(expected_latest_periods("2026-11-16"), ("2026-10", "2026-Q3"))

    def test_card_has_fixed_baseline_so_batch_can_be_published(self) -> None:
        import candidate_snapshots

        fixture = _fixture()
        result = screen_long_term(**fixture)
        card = result["candidates"][0]
        self.assertEqual(card["baseline"], {"date": DATA_DATE, "close": fixture["bars_by_code"]["1101"][-1]["close"]})
        snapshot = candidate_snapshots.build_snapshot("long", result, input_fingerprints={"x": "y"},
                                                      published_at="2026-11-06T08:00:00Z")
        self.assertEqual(snapshot["kind"], "long")
        self.assertEqual(snapshot["market"], "TWSE+TPEX")

    def test_reconciliation_rows_carry_market_for_two_market_reconciliation(self) -> None:
        fixture = _fixture(("1101", "6488"))
        fixture["universe"][1]["market"] = "TPEx"
        fixture = _replace(fixture, "6488", monthly=_monthly("6488", growth=1.0))
        result = screen_long_term(**fixture)
        markets = {row["code"]: row["market"] for row in result["reconciliation"]}
        self.assertEqual(markets, {"1101": "TWSE", "6488": "TPEx"})

    def test_code_absent_from_every_monthly_table_is_source_scope_gap(self) -> None:
        fixture = _fixture(("1101", "1256", "1102"))
        fixture["universe"][1]["name"] = "鮮活果汁-KY"
        fixture = _replace(fixture, "1256", monthly=[])
        fixture = _replace(fixture, "1102", monthly=[row for row in _monthly("1102") if row["period"] != "2026-09"])
        result = screen_long_term(**fixture)
        gap = next(item for item in result["reconciliation"] if item["code"] == "1256")["reasons"][0]
        self.assertEqual(gap["code"], "monthly_revenue_source_scope_gap")
        self.assertEqual(gap["category"], "missing_data")
        self.assertTrue(gap["details"]["is_ky"])
        # 只缺最新一期、但過去有營收的公司仍是單股缺最新營收，不混成來源範圍缺口。
        self.assertIn("revenue_latest_missing", _reason_codes(result, "1102"))
        self.assertEqual(result["counts"]["source_scope_gap"], 1)

    def test_nonpositive_ttm_net_income_is_excluded_not_crash(self) -> None:
        # 2025 下半年大虧使近四季淨利為負，但 EPS 欄位（母公司口徑）仍為正：
        # 現金流品質無從計算，不能靜默略過該門檻，也不能讓排序因 None 崩潰。
        rows = _quarterly("1101")
        ytd = {"2024-Q3": 3_000_000, "2024-Q4": 4_000_000, "2025-Q1": 1_000_000, "2025-Q2": 2_000_000,
               "2025-Q3": -20_000_000, "2025-Q4": -30_000_000, "2026-Q1": 1_200_000, "2026-Q2": 2_400_000}
        for row in rows:
            row["net_income_thousand_twd"] = ytd[row["period"]]
            row["operating_cash_flow_thousand_twd"] = abs(ytd[row["period"]])
            row["eps_twd"] = 1.0
        result = screen_long_term(**_replace(_fixture(), "1101", quarterly=rows))
        self.assertEqual(result["candidates"], [])
        self.assertIn("ttm_net_income_nonpositive", _reason_codes(result, "1101"))

    def test_method_version_is_v1_2_and_existing_thresholds_unchanged(self) -> None:
        self.assertEqual(METHOD_VERSION, "long-v1.2")
        for key, value in {"min_revenue_yoy_12m_pct": 5.0, "min_ytd_net_income_yoy_pct": 10.0, "min_ocf_to_net_income_ttm": 0.8,
                           "max_debt_ratio_pct": 60.0, "min_avg_turnover_twd": 20_000_000, "max_close_above_ma60_pct": 15.0,
                           "rank_profit_growth_cap_pct": 50.0}.items():
            self.assertEqual(PARAMETERS[key], value)

    def test_connected_valuation_reports_industry_percentiles(self) -> None:
        valuation = FakeValuation({("1101", "pe"): {"percentile": 0.42, "value": 15.3},
                                   ("1101", "pb"): {"percentile": 0.55, "value": 1.8}})
        result = screen_long_term(**_fixture(), valuation=valuation)
        card = result["candidates"][0]
        self.assertEqual(card["valuation"]["status"], "ok")
        self.assertEqual(card["valuation"]["pe_percentile_in_industry"], 0.42)
        self.assertEqual(card["valuation"]["pb_percentile_in_industry"], 0.55)
        self.assertEqual(card["valuation"]["valuation_date"], DATA_DATE)
        self.assertEqual(card["valuation"]["tier"], "normal")
        self.assertFalse(any(item["factor"] == "估值未接入" for item in card["opposing_evidence"]))

    def test_expensive_peer_valuation_demotes_rank_and_adds_opposition(self) -> None:
        fixture = _fixture(("1101", "1102"))
        plain = screen_long_term(**fixture)
        self.assertEqual([card["code"] for card in plain["candidates"]], ["1101", "1102"])
        valuation = FakeValuation({("1101", "pe"): {"percentile": 0.85, "value": 40.0}, ("1101", "pb"): {"percentile": 0.6, "value": 3.0},
                                   ("1102", "pe"): {"percentile": 0.30, "value": 12.0}, ("1102", "pb"): {"percentile": 0.4, "value": 1.2}})
        result = screen_long_term(**fixture, valuation=valuation)
        self.assertEqual([card["code"] for card in result["candidates"]], ["1102", "1101"])  # 降級，不排除
        expensive = next(card for card in result["candidates"] if card["code"] == "1101")
        self.assertEqual(expensive["valuation"]["tier"], "expensive")
        self.assertTrue(any(item["factor"] == "估值偏高" for item in expensive["opposing_evidence"]))
        self.assertEqual(result["counts"]["qualified"], 2)

    def test_loss_pe_is_not_ranked_and_pb_alone_can_flag_expensive(self) -> None:
        valuation = FakeValuation({("1101", "pb"): {"percentile": 0.95, "value": 6.0}})
        valuation.percentiles[("1101", "pe")] = {"percentile": None, "value": None, "reason": "value_missing"}
        result = screen_long_term(**_fixture(), valuation=valuation)
        card = result["candidates"][0]
        self.assertIsNone(card["valuation"]["pe_percentile_in_industry"])  # 不補 0
        self.assertEqual(card["valuation"]["pe_reason"], "value_missing")
        self.assertEqual(card["valuation"]["pb_percentile_in_industry"], 0.95)
        self.assertEqual(card["valuation"]["tier"], "expensive")

    def test_tpex_valuation_not_connected_is_not_zero_and_not_listed_substitute(self) -> None:
        fixture = _fixture(("1101", "6488"))
        fixture["universe"][1]["market"] = "TPEx"
        valuation = FakeValuation({("1101", "pe"): {"percentile": 0.5, "value": 15.0}, ("6488", "pe"): {"percentile": 0.1, "value": 9.0}},
                                  markets=("TWSE",))
        result = screen_long_term(**fixture, valuation=valuation)
        tpex = next(card for card in result["candidates"] if card["code"] == "6488")
        self.assertEqual(tpex["valuation"]["status"], "market_not_connected")
        self.assertIsNone(tpex["valuation"]["pe_percentile_in_industry"])
        self.assertIsNone(tpex["valuation"]["pb_percentile_in_industry"])
        self.assertEqual(tpex["valuation"]["tier"], "unknown")
        self.assertIn("上櫃估值尚未接入", tpex["valuation"]["message"])
        self.assertTrue(any("上櫃估值尚未接入" in item["value"] for item in tpex["opposing_evidence"]))
        self.assertFalse(any(call[0] == "6488" for call in valuation.calls))

    def test_code_absent_from_valuation_is_unavailable_not_zero(self) -> None:
        valuation = FakeValuation({}, markets=("TWSE", "TPEx"))  # 例：3718 在估值資料完全沒有出現
        card = screen_long_term(**_fixture(), valuation=valuation)["candidates"][0]
        self.assertEqual(card["valuation"]["status"], "unavailable")
        self.assertIsNone(card["valuation"]["pe_percentile_in_industry"])
        self.assertIsNone(card["valuation"]["pb_percentile_in_industry"])
        self.assertEqual(card["valuation"]["tier"], "unknown")
        self.assertIn("估值資料不可得", card["valuation"]["message"])
        self.assertTrue(any(item["factor"] == "估值資料不可得" for item in card["opposing_evidence"]))

    def test_stale_valuation_date_gives_no_percentile(self) -> None:
        valuation = FakeValuation({("1101", "pe"): {"percentile": 0.5, "value": 15.0}}, day="2026-10-20")
        card = screen_long_term(**_fixture(), valuation=valuation)["candidates"][0]
        self.assertEqual(card["valuation"]["status"], "stale")
        self.assertIsNone(card["valuation"]["pe_percentile_in_industry"])
        self.assertEqual(card["valuation"]["tier"], "unknown")

    def test_ky_foreign_table_revenue_carries_fx_note_without_threshold_change(self) -> None:
        fixture = _fixture()
        rows = [{**row, "source_scope": "foreign"} for row in _monthly("1101")]
        result = screen_long_term(**_replace(fixture, "1101", monthly=rows))
        card = result["candidates"][0]
        self.assertEqual(card["code"], "1101")
        fx = [item for item in card["opposing_evidence"] if item["factor"] == "匯率影響"]
        self.assertEqual(len(fx), 1)
        self.assertIn("新台幣列示", fx[0]["value"])
        plain = screen_long_term(**_fixture())["candidates"][0]
        self.assertFalse(any(item["factor"] == "匯率影響" for item in plain["opposing_evidence"]))

    def test_tpex_candidate_benchmark_uses_otc_index(self) -> None:
        fixture = _fixture(("1101", "6488"))
        fixture["universe"][1]["market"] = "TPEx"
        result = screen_long_term(**fixture)
        planned = long_term.attach_evaluation_plan(
            result, published_at="2026-11-06T08:00:00Z", benchmark={"code": "0050", "date": DATA_DATE, "close": 92.5},
            tpex_benchmark={"code": "TPEX_INDEX", "name": "櫃買指數", "date": DATA_DATE, "close": 304.02})
        plans = {card["code"]: card["evaluation_plan"]["benchmark"] for card in planned["candidates"]}
        self.assertEqual(plans["6488"]["code"], "TPEX_INDEX")
        self.assertEqual(plans["6488"]["name"], "櫃買指數")
        self.assertEqual(plans["6488"]["close"], 304.02)
        self.assertEqual(plans["6488"]["status"], "ok")
        self.assertIsNone(plans["6488"]["limitation"])
        self.assertEqual(plans["1101"]["code"], "0050")
        self.assertEqual(plans["1101"]["close"], 92.5)

    def test_valuation_index_uses_same_market_peers_and_reports_missing_market(self) -> None:
        import tempfile

        import valuation_history

        def record(code: str, pe: float | None, pb: float) -> dict:
            return {"date": DATA_DATE, "code": code, "name": code, "close": None, "dividend_yield_pct": None,
                    "dividend_year_roc": None, "pe": pe, "pb": pb, "fin_period_roc": None, "schema": "test"}

        universe = [{"code": str(1101 + i), "market": "TWSE", "industry": "水泥工業"} for i in range(6)]
        universe.append({"code": "9999", "market": "TPEx", "industry": "水泥工業"})
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "valuation.sqlite"
            rows = [record(str(1101 + i), 10.0 + i, 1.0 + i) for i in range(6)]
            valuation_history.replace_market(db, "上市", rows, [{"date": DATA_DATE, "raw_file": "x", "sha256": "0" * 64, "rows": 6, "schema": "test"}])
            index = long_term.ValuationIndex(db, universe)
            self.assertTrue(index.market_available("TWSE", DATA_DATE))
            self.assertFalse(index.market_available("TPEx", DATA_DATE))
            top = index.percentile("1106", "TWSE", DATA_DATE, "pe")
        self.assertEqual(top["reason"], "ok")
        self.assertAlmostEqual(top["percentile"], 5.5 / 6)
        self.assertEqual(top["date"], DATA_DATE)

    def test_evaluation_plan_uses_publish_date_calendar_months_and_market_benchmark(self) -> None:
        fixture = _fixture(("1101", "6488"))
        fixture["universe"][1]["market"] = "TPEx"
        result = screen_long_term(**fixture)
        before = copy.deepcopy(result)
        benchmark = {"code": "0050", "date": DATA_DATE, "close": 92.5}
        planned = long_term.attach_evaluation_plan(result, published_at="2026-08-31T08:00:00Z", benchmark=benchmark)
        self.assertEqual(result, before)  # 不改輸入
        plans = {card["code"]: card["evaluation_plan"] for card in planned["candidates"]}
        self.assertEqual(plans["1101"]["anchor_date"], "2026-08-31")
        self.assertEqual(plans["1101"]["checkpoints"], [
            {"months": 6, "calendar_date": "2027-02-28"}, {"months": 12, "calendar_date": "2027-08-31"}])
        self.assertIn("下一個有收盤資料的交易日", plans["1101"]["non_trading_day_rule"])
        self.assertEqual(plans["1101"]["benchmark"]["code"], "0050")
        self.assertEqual(plans["1101"]["benchmark"]["close"], 92.5)
        self.assertIsNone(plans["1101"]["benchmark"]["limitation"])
        self.assertIsNotNone(plans["6488"]["benchmark"]["limitation"])
        self.assertEqual(plans["1101"]["price_basis"], plans["1101"]["benchmark"]["price_basis"])

    def test_evaluation_plan_without_benchmark_close_is_marked_unavailable(self) -> None:
        planned = long_term.attach_evaluation_plan(screen_long_term(**_fixture()), published_at="2026-11-06T08:00:00Z", benchmark=None)
        bench = planned["candidates"][0]["evaluation_plan"]["benchmark"]
        self.assertEqual(bench["status"], "unavailable")
        self.assertIsNone(bench["close"])

    def test_publish_batch_dry_run_writes_nothing_then_write_is_idempotent(self) -> None:
        import tempfile

        result = screen_long_term(**_fixture())
        benchmark = {"code": "0050", "date": DATA_DATE, "close": 92.5}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dry = long_term.publish_batch(result, input_fingerprints={"a": "b"}, benchmark=benchmark,
                                          published_at="2026-11-06T08:00:00Z", snapshot_root=root, dry_run=True)
            self.assertEqual(dry["outcome"], "dry_run")
            self.assertEqual(list(root.rglob("*")), [])
            first = long_term.publish_batch(result, input_fingerprints={"a": "b"}, benchmark=benchmark,
                                            published_at="2026-11-06T08:00:00Z", snapshot_root=root, dry_run=False)
            again = long_term.publish_batch(result, input_fingerprints={"a": "b"}, benchmark=benchmark,
                                            published_at="2026-11-06T08:00:00Z", snapshot_root=root, dry_run=False)
            self.assertEqual((first["outcome"], again["outcome"]), ("written", "unchanged"))
            stored = json_load(root / "long" / "2026-11-06.json")
            self.assertEqual(stored["candidates"][0]["evaluation_plan"]["benchmark"]["close"], 92.5)

    def test_publish_batch_records_empty_and_failure_states(self) -> None:
        import tempfile

        empty = screen_long_term(**_replace(_fixture(), "1101", monthly=_monthly("1101", growth=1.0)))
        failed_fixture = _fixture()
        failed_fixture["source_states"]["fundamentals"] = {"state": "failed", "message": "SHA 不符"}
        failed = screen_long_term(**failed_fixture)
        with tempfile.TemporaryDirectory() as folder:
            for status_result, when in ((empty, "2026-11-06T08:00:00Z"), (failed, "2026-11-07T08:00:00Z")):
                out = long_term.publish_batch(status_result, input_fingerprints={"a": "b"}, benchmark=None,
                                              published_at=when, snapshot_root=Path(folder), dry_run=False)
                self.assertEqual(out["outcome"], "written")
            statuses = [json_load(path)["status"] for path in sorted((Path(folder) / "long").glob("*.json"))]
        self.assertEqual(statuses, ["no_candidates", "source_failure"])

    def test_real_loader_returns_fingerprints_and_benchmark(self) -> None:
        root = Path(__file__).resolve().parent
        try:
            loaded = long_term.load_real_input(root=root, expected_data_date=DATA_DATE, history_limit=130)
        except FileNotFoundError:
            self.skipTest("本機無真實資料")
        self.assertEqual(set(loaded["input_fingerprints"]) >= {"stock_history_sqlite", "otc_history_sqlite",
                                                               "listed_catalog_html", "otc_catalog_html", "fundamentals_history_meta"}, True)
        self.assertEqual(loaded["benchmark"]["code"], "0050")
        self.assertEqual(loaded["benchmark"]["date"], DATA_DATE)
        self.assertIsNotNone(loaded["benchmark"]["close"])
        self.assertNotIn("benchmark", long_term.screen_inputs(loaded))
        self.assertNotIn("tpex_benchmark", long_term.screen_inputs(loaded))
        self.assertEqual(loaded["tpex_benchmark"]["code"], "TPEX_INDEX")
        self.assertEqual(loaded["tpex_benchmark"]["date"], DATA_DATE)
        self.assertEqual(loaded["tpex_benchmark"]["close"], 304.02)
        self.assertIn("valuation", long_term.screen_inputs(loaded))
        codes = {item["code"] for item in loaded["universe"]}
        self.assertTrue({"9105", "912000"}.isdisjoint(codes))  # 外國表的 DR 不是普通股母體

    def test_real_loader_opens_sqlite_read_only(self) -> None:
        uris: list[str] = []
        original = sqlite3.connect

        def spy(target, *args, **kwargs):
            uris.append(str(target))
            return original(target, *args, **kwargs)

        root = Path(__file__).resolve().parent
        with patch.object(long_term.sqlite3, "connect", side_effect=spy):
            try:
                long_term.load_real_input(root=root, expected_data_date=DATA_DATE, history_limit=130)
            except FileNotFoundError:
                self.skipTest("本機無真實資料")
        self.assertTrue(uris)
        self.assertTrue(all("mode=ro" in uri for uri in uris))


if __name__ == "__main__":
    unittest.main()
