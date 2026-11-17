"""基金對照 0050（2026-11-17 本人定案）：貝萊德世界科技 A10 美元，含息再投入、台幣計、和 0050 比。

定案：唯讀比較；台幣含匯率並另寫匯率影響；期間 1 月／3 月／1 年／3 年＋成立以來；
除息日用當天淨值買回、不計費稅，0050 照 benchmark_compare 既有含息算法。
"""
from __future__ import annotations

import unittest

import fund_compare as fc


class TotalReturnTest(unittest.TestCase):
    def test_without_distributions_value_follows_nav(self) -> None:
        navs = [("2026-01-02", 10.0), ("2026-01-05", 11.0), ("2026-01-06", 9.0)]
        self.assertEqual(fc.total_return_series(navs, []), [("2026-01-02", 10.0), ("2026-01-05", 11.0), ("2026-01-06", 9.0)])

    def test_distribution_is_reinvested_at_ex_date_nav(self) -> None:
        # 除息日淨值 9.5（已除息），配 0.5 → 單位數 ×(1+0.5/9.5)；之後淨值不動，價值仍含那 0.5。
        navs = [("2026-01-02", 10.0), ("2026-01-05", 9.5), ("2026-01-06", 9.5)]
        dists = [{"ex_date": "2026-01-05", "amount": 0.5}]
        series = fc.total_return_series(navs, dists)
        self.assertAlmostEqual(series[1][1], 10.0)
        self.assertAlmostEqual(series[2][1], 10.0)

    def test_ex_date_without_nav_reinvests_on_next_nav_day(self) -> None:
        navs = [("2026-01-02", 10.0), ("2026-01-06", 8.0)]
        dists = [{"ex_date": "2026-01-05", "amount": 2.0}]
        self.assertAlmostEqual(fc.total_return_series(navs, dists)[1][1], 10.0)

    def test_distributions_before_first_nav_or_after_last_nav_are_ignored(self) -> None:
        navs = [("2026-01-02", 10.0), ("2026-01-05", 10.0)]
        dists = [{"ex_date": "2025-12-30", "amount": 1.0}, {"ex_date": "2026-01-09", "amount": 1.0}]
        self.assertEqual([v for _, v in fc.total_return_series(navs, dists)], [10.0, 10.0])

    def test_real_seed_matches_official_calendar_year_total_returns(self) -> None:
        # 集保基金頁「近五年報酬率」（2026-11-17 讀）：2023 49.72%、2024 32.51%、2025 17.99%。
        # 含息再投入算對了，回補原件算出來的年度報酬要分毫不差。
        import benchmark_compare as bc
        navs, dists = fc.load_seed("BLKTENAU")
        series = fc.total_return_series(navs, dists)
        got = {}
        for year in (2023, 2024, 2025):
            start = bc._value_on_or_before(series, f"{year - 1}-12-31")[1]
            got[year] = round((bc._value_on_or_before(series, f"{year}-12-31")[1] / start - 1) * 100, 2)
        self.assertEqual(got, {2023: 49.72, 2024: 32.51, 2025: 17.99})

    def test_buying_on_the_ex_date_does_not_earn_that_distribution(self) -> None:
        navs = [("2026-01-05", 9.5), ("2026-01-06", 9.5)]
        dists = [{"ex_date": "2026-01-05", "amount": 0.5}]
        self.assertEqual([v for _, v in fc.total_return_series(navs, dists)], [9.5, 9.5])


class TwdTest(unittest.TestCase):
    FX = [("2026-01-02", 30.0), ("2026-01-05", 31.0)]

    def test_value_times_cbc_rate_on_or_before_the_day(self) -> None:
        # 01-06 央行沒有收盤匯率（台灣休市）→ 用 01-05 的 31。
        series = [("2026-01-02", 10.0), ("2026-01-05", 10.0), ("2026-01-06", 11.0)]
        self.assertEqual(fc.to_twd(series, self.FX), [("2026-01-02", 300.0), ("2026-01-05", 310.0), ("2026-01-06", 341.0)])

    def test_days_before_first_rate_are_dropped(self) -> None:
        self.assertEqual(fc.to_twd([("2025-12-31", 10.0), ("2026-01-02", 10.0)], self.FX), [("2026-01-02", 300.0)])


class ParseTest(unittest.TestCase):
    def test_backfill_nav_csv(self) -> None:
        self.assertEqual(fc.parse_nav_csv("2022-12-01,10.520000\n2022-12-02,10.310000\n"),
                         [("2022-12-01", 10.52), ("2022-12-02", 10.31)])

    def test_backfill_distribution_page(self) -> None:
        page = ('{"total":2,"list":[{"asiBaseDate":"2023/10/30","asiEnterDate":"2023/10/31","asiDate":"2023/11/08",'
                '"asiAmt":"0.098000000","asiFreq":"每月","asiRatio1":"21.44%","asiRatio2":"78.56%"},'
                '{"asiBaseDate":"2025/12/30","asiEnterDate":"2025/12/31","asiDate":"2026/01/08","asiAmt":"0.137000000",'
                '"asiFreq":"每月","asiRatio1":"N/A","asiRatio2":"N/A"}],"pageNum":1}')
        self.assertEqual(fc.parse_backfill_distributions([page]), [
            {"ex_date": "2023-10-31", "record_date": "2023-10-30", "pay_date": "2023-11-08", "amount": 0.098,
             "income_pct": 21.44, "capital_pct": 78.56},
            {"ex_date": "2025-12-31", "record_date": "2025-12-30", "pay_date": "2026-01-08", "amount": 0.137,
             "income_pct": None, "capital_pct": None},
        ])

    def test_opendata_nav_rows_only_this_fund(self) -> None:
        rows = [{"﻿基金代碼": "BLKTENAU", "日期": "20261110", "基金淨值(金額)": "17.850000", "計價幣別": "USD"},
                {"﻿基金代碼": "OTHER", "日期": "20261110", "基金淨值(金額)": "1.0", "計價幣別": "USD"},
                {"基金代碼": "BLKTENAU", "日期": "20261109", "基金淨值(金額)": "17.530000", "計價幣別": "USD"}]
        self.assertEqual(fc.parse_opendata_nav(rows, "BLKTENAU"), [("2026-11-09", 17.53), ("2026-11-10", 17.85)])

    def test_opendata_distribution_rows(self) -> None:
        rows = [{"基金代碼": "BLKTENAU", "﻿資料日期": "20261116", "配息基準日": "20261029", "除息日": "20261030",
                 "配息發放日": "20261106", "單位數": "0.162000000", "可分配淨利益/配息": "N/A", "本金/配息": "N/A"},
                {"基金代碼": "BLKTENAU", "﻿資料日期": "20261116", "配息基準日": "20260929", "除息日": "20260930",
                 "配息發放日": "20261007", "單位數": "0.153000000", "可分配淨利益/配息": "100.00", "本金/配息": "0.00"},
                {"基金代碼": "LU0497422583", "除息日": "20260915", "單位數": "1.07"}]
        self.assertEqual(fc.parse_opendata_distributions(rows, "BLKTENAU"), [
            {"ex_date": "2026-09-30", "record_date": "2026-09-29", "pay_date": "2026-10-07", "amount": 0.153,
             "income_pct": 100.0, "capital_pct": 0.0},
            {"ex_date": "2026-10-30", "record_date": "2026-10-29", "pay_date": "2026-11-06", "amount": 0.162,
             "income_pct": None, "capital_pct": None},
        ])

    def test_real_backfill_seed_is_complete(self) -> None:
        navs, dists = fc.load_seed("BLKTENAU")
        self.assertEqual((len(navs), navs[0], navs[-1]), (991, ("2022-12-01", 10.52), ("2026-11-16", 17.14)))
        self.assertEqual((len(dists), dists[0]["ex_date"], dists[-1]["ex_date"]), (47, "2022-12-30", "2026-10-30"))

    def test_seed_sha_mismatch_stops(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            src = fc.SEED_DIR / "BLKTENAU"
            dst = Path(tmp) / "BLKTENAU"
            dst.mkdir()
            for path in src.iterdir():
                (dst / path.name).write_bytes(path.read_bytes())
            (dst / "nav-backfill.csv").write_text("2022-12-01,10.520000\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                fc.load_seed("BLKTENAU", seed_dir=Path(tmp))


class SeedMergeTest(unittest.TestCase):
    def test_later_nav_backfill_files_are_merged_not_overwritten(self) -> None:
        # 之後若有缺口要再從集保網頁補一段，清單多一份 kind nav 的檔即可，不必整份重讀。
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "BLKTENAU"
            dst.mkdir()
            src = fc.SEED_DIR / "BLKTENAU"
            for path in src.iterdir():
                (dst / path.name).write_bytes(path.read_bytes())
            extra = b"2026-11-17,17.200000\n"
            (dst / "nav-backfill-2.csv").write_bytes(extra)
            manifest = json.loads((dst / "backfill-manifest.json").read_text(encoding="utf-8"))
            manifest["files"].append({"file": "nav-backfill-2.csv", "kind": "nav", "sha256": hashlib.sha256(extra).hexdigest()})
            (dst / "backfill-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            navs, _ = fc.load_seed("BLKTENAU", seed_dir=Path(tmp))
        self.assertEqual((len(navs), navs[0][0], navs[-1]), (992, "2022-12-01", ("2026-11-17", 17.2)))


class GapTest(unittest.TestCase):
    """缺日檢查：官方檔涵蓋的區間內沒有淨值＝基金休市（正常）；沒有任何一份官方檔涵蓋到的平日才算缺。"""

    def test_holiday_inside_a_covered_window_is_not_a_gap(self) -> None:
        # 11-11 那天官方檔有涵蓋（11-09～11-13 那份），卻沒有淨值 → 休市，不是缺。
        navs = ["2026-11-09", "2026-11-10", "2026-11-12", "2026-11-13"]
        self.assertEqual(fc.find_gaps(navs, windows=[("2026-11-09", "2026-11-13")]), [])

    def test_missed_runs_leave_uncovered_weekdays(self) -> None:
        # 回補到 11-16；之後排程漏了好幾天，下一份開放資料只涵蓋 11-19～11-24 → 11-17、11-18 沒人看過。
        navs = ["2026-11-13", "2026-11-16", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"]
        windows = [("2022-12-01", "2026-11-16"), ("2026-11-19", "2026-11-24")]
        self.assertEqual(fc.find_gaps(navs, windows=windows),
                         [{"kind": "gap", "from": "2026-11-17", "to": "2026-11-18", "weekdays": 2}])

    def test_one_missed_run_is_filled_by_the_next_window(self) -> None:
        # 開放資料一次給約 4 個交易日：漏抓一天，下一次的檔案仍涵蓋那天 → 自動補上，沒有缺。
        navs = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
        windows = [("2022-12-01", "2026-11-16"), ("2026-11-17", "2026-11-20")]
        self.assertEqual(fc.find_gaps(navs, windows=windows), [])

    def test_weekends_are_never_gaps(self) -> None:
        self.assertEqual(fc.find_gaps(["2026-11-13", "2026-11-16"], windows=[]), [])

    def test_stale_when_latest_nav_is_far_behind(self) -> None:
        # 開放資料本來就晚約 3 個營業日；超過 6 個平日沒有新淨值就提醒。
        self.assertEqual(fc.staleness("2026-11-06", as_of="2026-11-17"),
                         {"kind": "stale", "latest": "2026-11-06", "weekdays_behind": 7})
        self.assertIsNone(fc.staleness("2026-11-12", as_of="2026-11-17"))


class MergeTest(unittest.TestCase):
    def test_merges_by_date_and_reports_conflicts(self) -> None:
        merged, issues = fc.merge_navs([[("2026-11-12", 18.02), ("2026-11-13", 17.73)],
                                        [("2026-11-13", 17.80), ("2026-11-16", 17.14)]])
        self.assertEqual(merged, [("2026-11-12", 18.02), ("2026-11-13", 17.73), ("2026-11-16", 17.14)])
        self.assertEqual(issues, [{"kind": "nav-conflict", "date": "2026-11-13", "kept": 17.73, "other": 17.8}])

    def test_distribution_amount_conflict_has_its_own_kind(self) -> None:
        row = {"ex_date": "2026-10-30", "amount": 0.162, "capital_pct": 0.0}
        _, issues = fc.merge_distributions([[row], [{**row, "amount": 0.17}]])
        self.assertEqual(issues, [{"kind": "dist-conflict", "ex_date": "2026-10-30", "kept": 0.162, "other": 0.17}])

    def test_distribution_with_ratio_replaces_one_without(self) -> None:
        # 配息來源比例常常晚幾天才公布（先是 N/A）：同一個除息日，有比例的那份優先。
        early = {"ex_date": "2026-10-30", "record_date": "2026-10-29", "pay_date": "2026-11-06", "amount": 0.162,
                 "income_pct": None, "capital_pct": None}
        later = {**early, "income_pct": 100.0, "capital_pct": 0.0}
        merged, issues = fc.merge_distributions([[early], [later]])
        self.assertEqual((merged, issues), ([later], []))


def _opendata_nav(*rows: tuple[str, str], code: str = "BLKTENAU") -> bytes:
    import json
    return json.dumps([{"﻿基金代碼": code, "日期": d.replace("-", ""), "基金淨值(金額)": v, "計價幣別": "USD",
                        "ISINCODE": "LU2533723974"} for d, v in rows] +
                      [{"﻿基金代碼": "OTHER", "日期": "20261110", "基金淨值(金額)": "1.0"}], ensure_ascii=False).encode()


def _opendata_dist(*rows: tuple[str, str, str]) -> bytes:
    import json
    return json.dumps([{"基金代碼": "BLKTENAU", "﻿資料日期": "20261116", "配息基準日": ex.replace("-", ""),
                        "除息日": ex.replace("-", ""), "配息發放日": ex.replace("-", ""), "單位數": amount,
                        "可分配淨利益/配息": income, "本金/配息": "N/A"} for ex, amount, income in rows],
                      ensure_ascii=False).encode()


def _cbc(*rows: tuple[str, str]) -> bytes:
    import json
    return json.dumps([{"日期": d.replace("-", ""), "NTD_USD": v} for d, v in rows]).encode()


class HistoryTest(unittest.TestCase):
    """回補原件＋每日抽取檔 → 一條淨值、配息、匯率，加上缺日／衝突／過期提醒。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, payload: dict) -> None:
        import json
        (self.raw / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_seed_plus_extracts(self) -> None:
        self.write("BLKTENAU-nav-2026-11-19.json", {"window": ["2026-11-17", "2026-11-19"], "rows": [
            {"基金代碼": "BLKTENAU", "日期": "20261117", "基金淨值(金額)": "17.20"},
            {"基金代碼": "BLKTENAU", "日期": "20261119", "基金淨值(金額)": "17.40"}]})
        self.write("BLKTENAU-dist-2026-11-20.json", {"rows": [
            {"基金代碼": "BLKTENAU", "資料日期": "20261120", "配息基準日": "20261029", "除息日": "20261030",
             "配息發放日": "20261106", "單位數": "0.162", "可分配淨利益/配息": "100.00", "本金/配息": "0.00"}]})
        self.write("cbc-usdtwd-2026-11-18.json", {"rows": [["2026-11-17", 32.8], ["2026-11-18", 32.9]]})
        self.write("cbc-usdtwd-2026-11-10.json", {"rows": [["2026-11-10", 31.0]]})
        h = fc.load_history("BLKTENAU", raw_dir=self.raw, as_of="2026-11-20")
        self.assertEqual((len(h["navs"]), h["navs"][-1]), (993, ("2026-11-19", 17.4)))
        self.assertEqual(h["distributions"][-1]["capital_pct"], 0.0)   # 開放資料補上了原本 N/A 的比例
        self.assertEqual(h["fx"], [("2026-11-17", 32.8), ("2026-11-18", 32.9)])   # 用最新那份匯率
        self.assertEqual(h["issues"], [])   # 11-18 在 11-17～11-19 那份涵蓋範圍內＝休市

    def test_reports_gap_and_stale(self) -> None:
        self.write("BLKTENAU-nav-2026-11-24.json", {"window": ["2026-11-20", "2026-11-24"], "rows": [
            {"基金代碼": "BLKTENAU", "日期": "20261120", "基金淨值(金額)": "17.20"},
            {"基金代碼": "BLKTENAU", "日期": "20261124", "基金淨值(金額)": "17.40"}]})
        self.write("cbc-usdtwd-2026-11-24.json", {"rows": [["2026-11-24", 32.8]]})
        h = fc.load_history("BLKTENAU", raw_dir=self.raw, as_of="2026-12-07")
        self.assertEqual(h["issues"], [
            {"kind": "gap", "from": "2026-11-17", "to": "2026-11-19", "weekdays": 3},
            {"kind": "stale", "latest": "2026-11-24", "weekdays_behind": 9}])


def _days(start: str, values: list[float]) -> list[tuple[str, float]]:
    from datetime import date, timedelta
    day, out = date.fromisoformat(start), []
    for value in values:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append((day.isoformat(), value))
        day += timedelta(days=1)
    return out


class PayloadTest(unittest.TestCase):
    """對照內容：期間報酬（台幣）、美元報酬、匯率影響、0050、差距、最大回撤、對照圖、配息來源。"""

    def build(self, **overrides):
        navs = [("2023-01-02", 10.0), ("2025-11-14", 15.0), ("2026-08-14", 16.0), ("2026-10-14", 20.0), ("2026-11-13", 18.0)]
        fx = [("2023-01-02", 30.0), ("2026-10-14", 32.0), ("2026-11-13", 33.0)]
        bench = [("2016-01-04", 1.0), ("2023-01-02", 2.0), ("2025-11-14", 3.0), ("2026-08-14", 3.2), ("2026-10-14", 3.5),
                 ("2026-11-13", 3.6), ("2026-11-14", 3.7)]
        args = {"navs": navs, "distributions": [], "fx": fx, "benchmark": bench, "benchmark_date": "2026-11-14",
                "issues": []}
        args.update(overrides)
        return fc.fund_payload("BLKTENAU", **args)

    def test_end_is_the_latest_day_both_sides_have(self) -> None:
        p = self.build()
        self.assertEqual((p["data_date"], p["fund_nav_date"], p["benchmark_date"]), ("2026-11-13", "2026-11-13", "2026-11-14"))

    def test_period_rows(self) -> None:
        rows = {r["period"]: r for r in self.build()["periods"]}
        self.assertEqual([r["period"] for r in self.build()["periods"]], ["1m", "3m", "1y", "3y", "since"])
        one_month = rows["1m"]   # 錨點 10-13 → 期初取當天或之前最近一筆：08-14
        self.assertEqual(one_month["from"], "2026-08-14")
        # 台幣：16×30 → 18×33；美元：16 → 18；匯率影響＝台幣−美元。
        self.assertEqual(one_month["fund_return_pct"], round((18 * 33 / (16 * 30) - 1) * 100, 1))
        self.assertEqual(one_month["fund_usd_return_pct"], 12.5)
        self.assertEqual(one_month["fx_effect_pct"], round(one_month["fund_return_pct"] - 12.5, 1))
        self.assertEqual(one_month["benchmark_return_pct"], round((3.6 / 3.2 - 1) * 100, 1))   # 0050 截在 11-13
        self.assertEqual(one_month["excess_pct"], round(one_month["fund_return_pct"] - one_month["benchmark_return_pct"], 1))
        since = rows["since"]
        self.assertEqual((since["label"], since["from"]), ("成立以來", "2023-01-02"))
        self.assertEqual(since["benchmark_return_pct"], 80.0)

    def test_three_years_needs_history(self) -> None:
        rows = {r["period"]: r for r in self.build(navs=[("2024-01-02", 10.0), ("2026-11-13", 12.0)])["periods"]}
        self.assertEqual(rows["3y"], {"period": "3y", "label": "近 3 年", "status": "insufficient_history",
                                      "reason": "since_launch"})

    def test_chart_starts_at_one_for_both(self) -> None:
        p = self.build()
        self.assertEqual((p["chart_from"], p["chart"][0][1], p["benchmark_chart"][0][1]), ("2023-01-02", 1.0, 1.0))
        self.assertEqual(p["chart"][-1][0], "2026-11-13")
        self.assertEqual(p["benchmark_chart"][-1][0], "2026-11-13")

    def test_capital_source_summary(self) -> None:
        dists = [{"ex_date": "2023-01-31", "amount": 0.05, "capital_pct": 100.0, "income_pct": 0.0},
                 {"ex_date": "2023-10-31", "amount": 0.05, "capital_pct": 78.56, "income_pct": 21.44},
                 {"ex_date": "2024-01-31", "amount": 0.05, "capital_pct": 0.0, "income_pct": 100.0},
                 {"ex_date": "2026-10-30", "amount": 0.162, "capital_pct": None, "income_pct": None}]
        p = self.build(distributions=dists)
        self.assertEqual(p["distributions"]["by_year"], [
            {"year": "2023", "count": 2, "known": 2, "all_capital": 1, "some_capital": 2},
            {"year": "2024", "count": 1, "known": 1, "all_capital": 0, "some_capital": 0},
            {"year": "2026", "count": 1, "known": 0, "all_capital": 0, "some_capital": 0}])
        self.assertEqual(p["distributions"]["latest"], {"ex_date": "2026-10-30", "amount": 0.162})
        self.assertIn("配息可能來自本金", p["capital_note"])
        self.assertIn("2023 年 2 次配息中 1 次全部來自本金", p["capital_note"])

    def test_issues_and_notes_pass_through(self) -> None:
        p = self.build(issues=[{"kind": "stale", "latest": "2026-11-13", "weekdays_behind": 9}])
        self.assertEqual(p["issues"], [{"kind": "stale", "latest": "2026-11-13", "weekdays_behind": 9}])
        self.assertEqual(p["schema"], "fund-vs-0050/v1")
        self.assertTrue(any("不計申購手續費" in note for note in p["notes"]))


class WorkflowTest(unittest.TestCase):
    def test_daily_step_runs_before_build_and_never_blocks_publishing(self) -> None:
        from pathlib import Path
        import yaml
        doc = yaml.safe_load((Path(fc.__file__).parent / ".github/workflows/daily-update.yml").read_text(encoding="utf-8"))
        steps = next(iter(doc["jobs"].values()))["steps"]
        names = [s.get("name", "") for s in steps]
        fund = next(i for i, n in enumerate(names) if n.startswith("基金對照 0050"))
        self.assertLess(fund, names.index("每日更新"))
        self.assertGreater(fund, names.index("從 R2 還原工作集"))
        step = steps[fund]
        self.assertIs(step.get("continue-on-error"), True)
        self.assertIn("python fund_compare.py daily --r2 --no-upload", step["run"])   # 試跑不寫 R2
        self.assertIn("python fund_compare.py daily --r2 >", step["run"])


class _FakeLoader:
    def __init__(self, series, dividends_ok: bool = True):
        self._series = series
        self.dividends_ok = dividends_ok
        self.asked: list[tuple[str, str]] = []

    def dividends_included(self, market, series, code=None) -> bool:
        return self.dividends_ok

    def series(self, code: str, end: str):
        self.asked.append((code, end))
        return [p for p in self._series if p[0] <= end], "元大台灣50", "上市"


class PrecomputeTest(unittest.TestCase):
    """build_compare_pack 用：每檔基金一份預算好的 JSON 字串；資料不齊就不出那檔，不擋整個資料包。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        self.loader = _FakeLoader([("2022-11-30", 1.0), ("2026-11-09", 3.0), ("2026-11-10", 3.1)])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_one_json_text_per_fund(self) -> None:
        import json
        (self.raw / "cbc-usdtwd-2026-11-16.json").write_text(json.dumps({"rows": [["2022-11-30", 31.0], ["2026-11-16", 32.8]]}),
                                                              encoding="utf-8")
        funds, problems = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual((list(funds), problems), (["BLKTENAU"], {}))
        payload = json.loads(funds["BLKTENAU"])
        self.assertEqual((payload["schema"], payload["fund"]["code"], payload["data_date"]), ("fund-vs-0050/v1", "BLKTENAU", "2026-11-10"))
        self.assertEqual(self.loader.asked, [("0050", "2026-11-10")])

    def test_benchmark_dividends_not_complete_is_flagged(self) -> None:
        # 個股對照會檢查 0050 的除息表是否完整；基金這邊也一樣，不完整就在頁面上提醒（數字照出）。
        import json
        (self.raw / "cbc-usdtwd-2026-11-16.json").write_text(json.dumps({"rows": [["2022-11-30", 31.0]]}), encoding="utf-8")
        self.loader.dividends_ok = False
        funds, _ = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertIn({"kind": "benchmark-dividends-incomplete"}, json.loads(funds["BLKTENAU"])["issues"])

    def test_taipei_today_is_a_date(self) -> None:
        import re
        self.assertRegex(fc.taipei_today(), r"^\d{4}-\d{2}-\d{2}$")

    def test_no_fx_means_no_fund_and_a_reason(self) -> None:
        funds, problems = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual((funds, problems), ({}, {"BLKTENAU": "沒有匯率資料"}))


class DailyTest(unittest.TestCase):
    """每日排程：補齊 R2 已有的抽取檔 → 抓開放資料（一天每個來源最多一次）→ 只留這檔的列存檔 → 上傳新的。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        import state_sync
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = state_sync.DirectoryStore(self.root / "r2")
        self.raw = self.root / "raw"
        self.calls: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def get_for(self, nav: bytes, dist: bytes, cbc: bytes):
        def get(url: str) -> bytes:
            self.calls.append(url)
            return {fc.NAV_URL: nav, fc.DIST_URL: dist, fc.CBC_URL: cbc}[url]
        return get

    def run_daily(self, get, now: str = "2026-11-17T09:00:00+08:00") -> dict:
        return fc.daily(store=self.store, get=get, now=now, raw_dir=self.raw)

    def test_saves_only_this_fund_with_whole_file_sha_and_uploads(self) -> None:
        nav = _opendata_nav(("2026-11-09", "17.530000"), ("2026-11-12", "18.020000"))
        result = self.run_daily(self.get_for(nav, _opendata_dist(("2026-10-30", "0.162000000", "N/A")),
                                             _cbc(("2026-11-16", "32.8"), ("2022-10-31", "32.0"))))
        self.assertEqual(result["sources"]["nav"]["status"], "saved")
        saved = sorted(p.name for p in self.raw.iterdir() if not p.name.startswith("fetched-"))
        self.assertEqual(saved, ["BLKTENAU-dist-2026-11-16.json", "BLKTENAU-nav-2026-11-12.json", "cbc-usdtwd-2026-11-16.json"])
        import hashlib
        import json
        body = json.loads((self.raw / "BLKTENAU-nav-2026-11-12.json").read_text(encoding="utf-8"))
        self.assertEqual(body["file_sha256"], hashlib.sha256(nav).hexdigest())
        self.assertEqual(body["window"], ["2026-11-09", "2026-11-12"])
        self.assertEqual(len(body["rows"]), 2)
        uploaded = sorted(self.store.keys("funds/raw/"))
        self.assertEqual([k for k in uploaded if "/fetched-" not in k], ["funds/raw/" + name for name in saved])
        self.assertEqual(len(uploaded), len(saved) + 3)   # 三個來源各一個當天抓過的記號

    def test_second_run_same_day_does_not_fetch_again(self) -> None:
        get = self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(("2026-10-30", "0.162", "N/A")),
                           _cbc(("2026-11-16", "32.8")))
        self.run_daily(get)
        self.calls.clear()
        result = self.run_daily(get, now="2026-11-17T15:00:00+08:00")
        self.assertEqual(self.calls, [])
        self.assertEqual(result["sources"]["nav"]["status"], "already-fetched-today")

    def test_unchanged_file_next_day_still_counts_as_fetched_that_day(self) -> None:
        # 配息檔一個月才換一次、休市日匯率也不變：檔名相同不重寫，但當天抓過要留記號，同一天後面幾個時段不再抓。
        get = self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(("2026-10-30", "0.162", "N/A")),
                           _cbc(("2026-11-16", "32.8")))
        self.run_daily(get, now="2026-11-17T09:00:00+08:00")
        second = self.run_daily(get, now="2026-11-18T09:00:00+08:00")
        self.assertEqual(second["sources"]["dist"]["status"], "unchanged")
        self.calls.clear()
        third = self.run_daily(get, now="2026-11-18T15:00:00+08:00")
        self.assertEqual((self.calls, third["sources"]["dist"]["status"]), ([], "already-fetched-today"))
        self.assertIn("funds/raw/fetched-2026-11-18-dist-BLKTENAU.json", self.store.keys("funds/raw/"))

    def test_failed_fetch_leaves_no_mark_so_a_later_slot_retries(self) -> None:
        def broken(url: str) -> bytes:
            raise OSError("timeout")
        self.run_daily(broken)
        get = self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(("2026-10-30", "0.162", "N/A")),
                           _cbc(("2026-11-16", "32.8")))
        self.assertEqual(self.run_daily(get, now="2026-11-17T15:00:00+08:00")["sources"]["nav"]["status"], "saved")

    def test_restores_files_from_r2_before_fetching(self) -> None:
        self.store.put("funds/raw/BLKTENAU-nav-2026-11-05.json", b'{"old": true}')
        self.run_daily(self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(), _cbc(("2026-11-16", "32.8"))))
        self.assertTrue((self.raw / "BLKTENAU-nav-2026-11-05.json").exists())

    def test_one_failing_source_does_not_stop_the_others(self) -> None:
        def get(url: str) -> bytes:
            if url == fc.NAV_URL:
                raise OSError("timeout")
            return {fc.DIST_URL: _opendata_dist(), fc.CBC_URL: _cbc(("2026-11-16", "32.8"))}[url]
        result = self.run_daily(get)
        self.assertEqual(result["sources"]["nav"], {"status": "failed", "error": "OSError: timeout"})
        self.assertEqual(result["sources"]["fx"]["status"], "saved")

    def test_missing_fund_rows_is_a_failure_not_an_empty_file(self) -> None:
        result = self.run_daily(self.get_for(_opendata_nav(), _opendata_dist(), _cbc(("2026-11-16", "32.8"))))
        self.assertEqual(result["sources"]["nav"]["status"], "failed")
        self.assertFalse(any(p.name.startswith("BLKTENAU-nav") for p in self.raw.iterdir()))


if __name__ == "__main__":
    unittest.main()


class SearchFundsTest(unittest.TestCase):
    """本人 11-17：搜尋框打基金名稱也找得到這檔（本機 live_server 的 /api/stocks/search，和雲端 gate 同一組規則）。"""

    EXPECTED = [{"code": "BLKTENAU", "name": "貝萊德世界科技 A10", "full_name": "貝萊德世界科技基金 A10美元(總報酬穩定配息)"}]

    def test_name_part_class_or_code_ignoring_case_and_spaces(self) -> None:
        for query in ["貝萊德", "世界科技", "A10", "a10", "BLKTENAU", "blktenau", "世界科技A10", " 總報酬 "]:
            self.assertEqual(fc.search_funds(query), self.EXPECTED, query)

    def test_fund_without_seed_data_is_not_listed(self) -> None:
        # 和雲端只列有資料的基金一致：沒有回補原件的基金，基金頁算不出來，搜尋也不列
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(fc.search_funds("貝萊德", seed_dir=Path(empty)), [])

    def test_stock_names_and_blank_find_no_fund(self) -> None:
        for query in ["台積電", "2330", "", "   "]:
            self.assertEqual(fc.search_funds(query), [], query)
