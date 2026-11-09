from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fundamentals_history import FundamentalsHistory, _monthly_available_on

import candidate_snapshots
import long_term_evaluation as lte
import long_term_screen as lts

ANCHOR = "2025-08-29"  # 週五；+6 個日曆月＝2026-02-28（週六）→ 實際 2026-03-02
EVAL_AS_OF = "2026-11-06"


def bdays(start: str, end: str) -> list[str]:
    cursor, stop, out = date.fromisoformat(start), date.fromisoformat(end), []
    while cursor <= stop:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


DAYS = bdays(ANCHOR, EVAL_AS_OF)


def series(knots: list[tuple[str, float]], days: list[str] = DAYS) -> list[dict]:
    """依 (日期, 收盤) 節點線性內插；漲跌欄位與前一日收盤一致（沒有公司行動）。"""
    index = {day: i for i, day in enumerate(days)}
    points = [(index[day], close) for day, close in knots]
    rows, prev = [], None
    for i, day in enumerate(days):
        for (a, ca), (b, cb) in zip(points, points[1:]):
            if a <= i <= b:
                close = round(ca + (cb - ca) * (i - a) / (b - a), 2) if b > a else ca
                break
        else:
            close = points[-1][1]
        change = None if prev is None else round(close - prev, 2)
        sign = "" if change in (None, 0) else ("+" if change > 0 else "-")
        rows.append({"date": day, "close": close, "change_sign": sign, "change": 0.0 if change is None else change, "suspended": False})
        prev = close
    return rows


def override(rows: list[dict], day: str, **fields) -> list[dict]:
    out = copy.deepcopy(rows)
    for row in out:
        if row["date"] == day:
            row.update(fields)
    return out


def rechain(rows: list[dict], start_day: str, closes: list[tuple[str, float]]) -> list[dict]:
    """把 start_day 之後的收盤改成新節點（漲跌以新序列自身計算）。"""
    tail_days = [row["date"] for row in rows if row["date"] > start_day]
    tail = series(closes, tail_days) if tail_days else []
    head = [row for row in rows if row["date"] <= start_day]
    if tail:
        tail[0]["change"] = round(tail[0]["close"] - head[-1]["close"], 2)
        tail[0]["change_sign"] = "+" if tail[0]["change"] > 0 else ("-" if tail[0]["change"] < 0 else "")
    return head + tail


class FakeProvider:
    def __init__(self, *, securities: dict, benchmarks: dict, events: dict | None = None,
                 days: dict | None = None) -> None:
        self.securities = securities
        self.benchmarks = benchmarks
        self.events = events or {}
        self.days = days or {"TWSE": DAYS, "TPEx": DAYS}

    def security_rows(self, code, market, start, end):
        return [row for row in self.securities.get((market, code), []) if start <= row["date"] <= end]

    def benchmark_rows(self, code, start, end):
        return [row for row in self.benchmarks.get(code, []) if start <= row["date"] <= end]

    def trading_days(self, market, start, end):
        return [day for day in self.days[market] if start <= day <= end]

    def last_date(self, market):
        return self.days[market][-1]

    def corporate_events(self, code, market):
        return self.events.get(code, {}) if market == "TWSE" else {}


def card(code: str, market: str = "TWSE", close: float = 100.0, defense: float = 90.0,
         industry: str = "水泥工業", data_date: str = ANCHOR) -> dict:
    return {
        "code": code, "name": f"測試{code}", "market": market, "industry": industry,
        "method_version": "long-v1.2", "data_date": data_date, "decision": "等待",
        "baseline": {"date": data_date, "close": close},
        "defense": {"level": defense, "basis": "MA120 下 8%"},
        "metrics": {"revenue_yoy_12m_pct": 20.0, "ytd_net_income_yoy_pct": 30.0},
    }


def snapshot(cards: list[dict], data_date: str = ANCHOR, status: str = "ok",
             bench_close: float = 50.0, tpex_close: float = 200.0) -> dict:
    result = {"status": status, "method_version": "long-v1.2", "data_date": data_date, "candidates": cards,
              "reconciliation": [], "counts": {}, "parameters": {}}
    published_at = f"{data_date}T08:00:00Z"
    planned = lts.attach_evaluation_plan(result, published_at=published_at,
                                         benchmark={"date": data_date, "close": bench_close},
                                         tpex_benchmark={"date": data_date, "close": tpex_close})
    return candidate_snapshots.build_snapshot("long", planned, input_fingerprints={"fixture": "f2"}, published_at=published_at)


def monthly_rows(code: str) -> list[dict]:
    rows = []
    year, month = 2023, 10
    while (year, month) <= (2026, 9):
        period = f"{year:04d}-{month:02d}"
        if year <= 2024:
            revenue = 100_000
        elif period in ("2025-10", "2025-11"):
            revenue = 90_000  # 年增 −10%，連兩個月為負 → 2025-11 可得日失效
        elif year == 2025:
            revenue = 120_000
        else:
            revenue = 130_000
        available_on, basis = _monthly_available_on(period)
        rows.append({"code": code, "name": code, "market": "listed", "period": period, "available_on": available_on,
                     "available_on_basis": basis, "revenue_thousand_twd": revenue, "unit": "TWD_thousand"})
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return rows


BASE_STOCK = series([(ANCHOR, 100.0), ("2025-10-15", 110.0), ("2025-12-01", 88.0), ("2026-03-02", 120.0),
                     ("2026-08-31", 130.0), (EVAL_AS_OF, 125.0)])
BENCH_0050 = series([(ANCHOR, 50.0), ("2026-03-02", 55.0), ("2026-08-31", 60.0), (EVAL_AS_OF, 60.0)])
BENCH_TPEX = [{"date": row["date"], "close": row["close"] * 4} for row in BENCH_0050]
FUNDAMENTALS = FundamentalsHistory(monthly=monthly_rows("1101"), quarterly=[])


def provider(**securities) -> FakeProvider:
    data = {("TWSE", "1101"): BASE_STOCK}
    data.update(securities.pop("extra", {}))
    return FakeProvider(securities=data, benchmarks={"0050": BENCH_0050, "TPEX_INDEX": BENCH_TPEX}, **securities)


def evaluate(cards: list[dict], prov: FakeProvider | None = None, eval_as_of: str = EVAL_AS_OF, **kwargs) -> dict:
    return lte.evaluate_snapshot(snapshot(cards, **kwargs), prov or provider(), FUNDAMENTALS, eval_as_of=eval_as_of)


def by_code(record: dict, code: str) -> dict:
    return next(item for item in record["candidates"] if item["code"] == code)


class CheckpointTests(unittest.TestCase):
    def test_pending_before_checkpoint_never_uses_today_price(self):
        rows = series([(ANCHOR, 100.0), (EVAL_AS_OF, 140.0)])
        record = evaluate([card("1101")], provider(extra={("TWSE", "1101"): rows}), eval_as_of="2026-01-15")
        for checkpoint in by_code(record, "1101")["checkpoints"]:
            self.assertEqual(checkpoint["status"], "pending")
            self.assertIsNone(checkpoint["return_pct"])
            self.assertIsNone(checkpoint["excess_return_pct"])

    def test_matured_checkpoint_uses_next_trading_day_and_same_comparison_date(self):
        item = by_code(evaluate([card("1101")]), "1101")
        six = item["checkpoints"][0]
        self.assertEqual((six["months"], six["calendar_date"], six["comparison_date"]), (6, "2026-02-28", "2026-03-02"))
        self.assertTrue(six["non_trading_calendar_date"])
        self.assertEqual(six["status"], "evaluated")
        self.assertAlmostEqual(six["return_pct"], 20.0, places=4)
        self.assertAlmostEqual(six["benchmark_return_pct"], 10.0, places=4)
        self.assertAlmostEqual(six["excess_return_pct"], 10.0, places=4)
        self.assertAlmostEqual(six["max_drawdown_pct"], -20.0, places=4)
        self.assertAlmostEqual(six["max_adverse_pct"], -12.0, places=4)
        self.assertEqual(six["benchmark"]["code"], "0050")
        self.assertEqual(item["checkpoints"][1]["comparison_date"], "2026-08-31")

    def test_tpex_candidate_uses_tpex_index_benchmark(self):
        rows = series([(ANCHOR, 100.0), ("2026-03-02", 110.0), (EVAL_AS_OF, 110.0)])
        prov = provider(extra={("TPEx", "6001"): rows})
        six = by_code(evaluate([card("6001", market="TPEx")], prov), "6001")["checkpoints"][0]
        self.assertEqual(six["benchmark"]["code"], "TPEX_INDEX")
        self.assertAlmostEqual(six["benchmark_return_pct"], 10.0, places=4)
        self.assertAlmostEqual(six["excess_return_pct"], 0.0, places=4)

    def test_suspended_on_checkpoint_uses_next_close_for_both_sides(self):
        rows = override(BASE_STOCK, "2026-03-02", close=None, suspended=True, change=None, change_sign="")
        six = by_code(evaluate([card("1101")], provider(extra={("TWSE", "1101"): rows})), "1101")["checkpoints"][0]
        self.assertEqual(six["comparison_date"], "2026-03-03")
        self.assertTrue(six["security_close_delayed"])
        bench_close = next(row["close"] for row in BENCH_0050 if row["date"] == "2026-03-03")
        self.assertAlmostEqual(six["benchmark_return_pct"], (bench_close / 50.0 - 1) * 100, places=4)

    def test_delisted_case_stays_with_reason(self):
        rows = [row for row in BASE_STOCK if row["date"] <= "2026-01-15"]
        item = by_code(evaluate([card("1101")], provider(extra={("TWSE", "1101"): rows})), "1101")
        self.assertEqual(item["checkpoints"][0]["status"], "not_evaluable")
        self.assertEqual(item["checkpoints"][0]["reason_code"], "no_security_close_after_checkpoint")

    def test_baseline_mismatch_is_not_evaluable(self):
        item = by_code(evaluate([card("1101", close=101.0)]), "1101")
        self.assertEqual({c["reason_code"] for c in item["checkpoints"]}, {"baseline_mismatch"})


class CorporateActionTests(unittest.TestCase):
    def test_detected_split_is_adjusted_with_caveat(self):
        rows = rechain(series([(ANCHOR, 100.0), ("2025-10-31", 120.0), (EVAL_AS_OF, 120.0)]), "2025-10-31",
                       [("2025-11-03", 30.5), ("2026-03-02", 31.0), (EVAL_AS_OF, 31.0)])
        rows = override(rows, "2025-11-03", change_sign="+", change=0.5)  # 參考價 30.0，前收 120 → 1 拆 4
        item = by_code(evaluate([card("2002")], provider(extra={("TWSE", "2002"): rows})), "2002")
        self.assertEqual(item["comparability"], "caveat")
        self.assertEqual(item["corporate_actions"][0]["kind"], "capital_change_detected")
        self.assertAlmostEqual(item["checkpoints"][0]["return_pct"], 24.0, places=4)

    def test_cash_dividend_not_adjusted(self):
        rows = rechain(series([(ANCHOR, 100.0), (EVAL_AS_OF, 100.0)]), "2025-10-31", [("2025-11-03", 98.5), (EVAL_AS_OF, 98.5)])
        rows = override(rows, "2025-11-03", change_sign="X", change=None)
        events = {"2003": {"2025-11-03": {"type": "除息", "reference": 98.0, "close_before": 100.0}}}
        item = by_code(evaluate([card("2003")], provider(extra={("TWSE", "2003"): rows}, events=events)), "2003")
        self.assertEqual(item["comparability"], "comparable")
        self.assertEqual(item["corporate_actions"][0]["kind"], "cash_dividend")
        self.assertAlmostEqual(item["checkpoints"][0]["return_pct"], -1.5, places=4)

    def test_unbridgeable_gap_kept_as_not_evaluable(self):
        rows = override(series([(ANCHOR, 100.0), (EVAL_AS_OF, 100.0)]), "2025-11-03", change_sign="X", change=None, close=60.0)
        item = by_code(evaluate([card("2004")], provider(extra={("TWSE", "2004"): rows})), "2004")
        self.assertEqual(item["comparability"], "not_comparable")
        self.assertEqual(item["checkpoints"][0]["reason_code"], "unbridgeable_corporate_action")

    def test_tpex_small_gap_is_unclassified_caveat(self):
        rows = rechain(series([(ANCHOR, 100.0), (EVAL_AS_OF, 100.0)]), "2025-10-31", [("2025-11-03", 96.0), (EVAL_AS_OF, 96.0)])
        rows = override(rows, "2025-11-03", change_sign="-", change=-1.0)  # 參考價 97 ≠ 前收 100
        item = by_code(evaluate([card("6002", market="TPEx")], provider(extra={("TPEx", "6002"): rows})), "6002")
        self.assertEqual(item["comparability"], "caveat")
        self.assertEqual(item["corporate_actions"][0]["kind"], "unclassified")
        self.assertEqual(item["checkpoints"][0]["status"], "evaluated")


class ThesisTests(unittest.TestCase):
    def test_revenue_invalidation_dated_at_availability_and_price_breach_first(self):
        item = by_code(evaluate([card("1101")]), "1101")
        thesis = item["thesis"]
        invalid = [e for e in thesis["events"] if e["status"] == "invalidated"]
        self.assertEqual(invalid[0]["period"], "2025-11")
        self.assertEqual(invalid[0]["date"], _monthly_available_on("2025-11")[0])
        self.assertEqual(thesis["first_fundamental_invalidation"]["period"], "2025-11")
        breach = thesis["price_breach"]["date"]
        self.assertEqual(breach, next(row["date"] for row in BASE_STOCK if row["close"] < 90.0))
        self.assertEqual(thesis["order"], "price_first")
        self.assertTrue(all(e["date"] > ANCHOR for e in thesis["events"]))
        six = item["checkpoints"][0]
        self.assertEqual(six["thesis_status"], "invalidated")
        self.assertTrue(all(day <= six["comparison_date"] for day in six["thesis_event_dates"]))

    def test_order_rules(self):
        fund = {"date": "2025-12-10", "period": "2025-11", "source": "monthly_revenue"}
        self.assertEqual(lte.invalidation_order(fund, "2025-11-27"), "price_first")
        self.assertEqual(lte.invalidation_order(fund, "2025-12-03"), "unknown_announcement_may_precede_price")
        self.assertEqual(lte.invalidation_order(fund, "2025-12-10"), "same_day_unknown")
        self.assertEqual(lte.invalidation_order(fund, "2025-12-15"), "fundamentals_first")
        self.assertEqual(lte.invalidation_order(None, "2025-12-15"), "price_only")


class RecordTests(unittest.TestCase):
    def test_record_is_separate_sealed_idempotent_and_conflicts(self):
        snap = snapshot([card("1101")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_snapshots.write_snapshot(root / "snapshots", snap)
            snap_path = root / "snapshots/long" / f"{ANCHOR}.json"
            before = hashlib.sha256(snap_path.read_bytes()).hexdigest()
            record = lte.evaluate_snapshot(candidate_snapshots.read_snapshot(root / "snapshots", "long", ANCHOR),
                                           provider(), FUNDAMENTALS, eval_as_of=EVAL_AS_OF)
            self.assertEqual(record["source_snapshot_sha256"], snap["snapshot_sha256"])
            self.assertEqual(lte.write_evaluation(root / "evals", record)["outcome"], "written")
            self.assertEqual(lte.write_evaluation(root / "evals", record)["outcome"], "unchanged")
            changed = lte.seal(dict(record, eval_version="tampered"))
            with self.assertRaises(lte.EvaluationConflict):
                lte.write_evaluation(root / "evals", changed)
            self.assertEqual(hashlib.sha256(snap_path.read_bytes()).hexdigest(), before)
            saved = root / "evals/long-eval" / ANCHOR / f"{EVAL_AS_OF}.json"
            data = json.loads(saved.read_text(encoding="utf-8"))
            data["candidates"][0]["code"] = "9999"
            saved.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(lte.EvaluationIntegrityError):
                lte.read_evaluation(root / "evals", ANCHOR, EVAL_AS_OF)
            self.assertNotIn("hit_rate", json.dumps(record))

    def test_empty_batch_keeps_status(self):
        record = evaluate([], status="no_candidates")
        self.assertEqual((record["status"], record["candidates"]), ("no_candidates", []))

    def test_overlapping_batches_are_not_independent(self):
        records = [
            {"batch_id": "long-2025-08-29", "candidates": [{"code": "1101", "baseline": {"date": "2025-08-29"},
                                                             "checkpoints": [{"months": 6, "calendar_date": "2026-02-28"}]}]},
            {"batch_id": "long-2025-09-30", "candidates": [{"code": "1101", "baseline": {"date": "2025-09-30"},
                                                             "checkpoints": [{"months": 6, "calendar_date": "2026-03-30"}]}]},
            {"batch_id": "long-2026-03-31", "candidates": [{"code": "1101", "baseline": {"date": "2026-03-31"},
                                                             "checkpoints": [{"months": 6, "calendar_date": "2026-09-30"}]}]},
        ]
        summary = lte.overlap_summary(records, months=6)
        self.assertEqual((summary["samples"], summary["independent_windows"]), (3, 2))


class DescriptionHelperTests(unittest.TestCase):
    def test_cyclical_classification_basis(self):
        self.assertEqual(lte.classify_cycle("2605", "航運業")["basis"], "official_industry")
        self.assertTrue(lte.classify_cycle("2605", "航運業")["cyclical"])
        self.assertEqual(lte.classify_cycle("2408", "半導體業")["basis"], "curated_code_list")
        self.assertEqual(lte.classify_cycle("2408", "半導體業")["group"], "記憶體")
        self.assertFalse(lte.classify_cycle("2330", "半導體業")["cyclical"])

    def test_operating_vs_net_income(self):
        quarterly = [
            {"period": "2025-Q2", "operating_income_thousand_twd": 1000, "net_income_thousand_twd": 900},
            {"period": "2026-Q2", "operating_income_thousand_twd": 1050, "net_income_thousand_twd": 1500},
        ]
        out = lte.operating_vs_net(quarterly, "2026-Q2")
        self.assertEqual((out["delta_net_income_thousand_twd"], out["delta_operating_income_thousand_twd"]), (600, 50))
        self.assertTrue(out["non_operating_dominant"])
        self.assertIsNone(lte.operating_vs_net(quarterly[:1], "2026-Q2")["non_operating_dominant"])


if __name__ == "__main__":
    unittest.main()
