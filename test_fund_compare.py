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


class CompareItemTest(unittest.TestCase):
    """基金當成一般個股 05 票：基金在比較資料包裡是和股票同形狀的一筆（benchmark_compare.Loader.item 的欄位），
    報酬是台幣、含息再投入；另帶基金專屬的匯率影響、原幣報酬與「成立以來」一列（本人 11-17 選的期間）。"""

    NAVS = [("2023-01-02", 10.0), ("2025-11-14", 15.0), ("2026-08-14", 16.0), ("2026-10-14", 20.0), ("2026-11-13", 18.0)]

    def build(self, **overrides):
        fx = [("2023-01-02", 30.0), ("2026-10-14", 32.0), ("2026-11-13", 33.0)]
        bench = [("2016-01-04", 1.0), ("2023-01-02", 2.0), ("2025-11-14", 3.0), ("2026-08-14", 3.2), ("2026-10-14", 3.5),
                 ("2026-11-13", 3.6), ("2026-11-14", 3.7)]
        args = {"navs": self.NAVS, "distributions": [], "fx": fx, "benchmark": bench, "issues": []}
        args.update(overrides)
        return fc.compare_item("BLKTENAU", **args)

    def test_same_fields_as_a_stock_item_plus_fund_kind(self) -> None:
        item = self.build()
        stock_fields = {"code", "name", "market", "status", "dividends_included", "dividend_source", "listed_date",
                        "history", "periods", "chart_from", "chart", "benchmark_chart"}
        self.assertLessEqual(stock_fields, set(item))
        self.assertEqual((item["code"], item["name"], item["market"], item["status"], item["kind"], item["currency"]),
                         ("BLKTENAU", "貝萊德世界科技 A10", "境外基金", "ok", "fund", "USD"))
        self.assertIs(item["dividends_included"], True)
        self.assertEqual(item["listed_date"], "2022-10-26")
        self.assertEqual(item["history"], {"from": "2023-01-02", "to": "2026-11-13", "points": 5})
        self.assertNotIn("schema", item)

    def test_periods_are_the_stock_five_then_since_launch(self) -> None:
        rows = self.build()["periods"]
        self.assertEqual([r["period"] for r in rows], ["1m", "3m", "1y", "3y", "5y", "since"])
        five = rows[4]   # 級別 2022-10-26 成立，近 5 年不夠長：和股票上市太晚同一個原因
        self.assertEqual(five, {"period": "5y", "label": "近 5 年", "status": "insufficient_history", "reason": "listed_recently"})

    def test_one_month_row_is_twd_with_dividends_and_fx_effect(self) -> None:
        one_month = {r["period"]: r for r in self.build()["periods"]}["1m"]
        # 資料日＝雙方都有的最後一天 11-13（0050 有 11-14 也截在 11-13）；錨點 10-13 → 期初取當天或之前最近一筆：08-14
        self.assertEqual((one_month["status"], one_month["from"], one_month["to"]), ("ok", "2026-08-14", "2026-11-13"))
        twd = round((18 * 33 / (16 * 30) - 1) * 100, 1)
        self.assertEqual(one_month["stock_return_pct"], twd)
        self.assertEqual(one_month["stock_native_return_pct"], 12.5)
        self.assertEqual(one_month["fx_effect_pct"], round((18 * 33 / (16 * 30) - 18 / 16) * 100, 1))
        self.assertEqual(one_month["benchmark_return_pct"], round((3.6 / 3.2 - 1) * 100, 1))
        self.assertEqual(one_month["excess_pct"], round(twd - one_month["benchmark_return_pct"], 1))
        self.assertEqual(one_month["stock_max_drawdown_pct"], round((18 * 33 / (20 * 32) - 1) * 100, 1))
        self.assertEqual(one_month["benchmark_max_drawdown_pct"], 0.0)

    def test_since_launch_starts_at_the_first_nav(self) -> None:
        since = {r["period"]: r for r in self.build()["periods"]}["since"]
        self.assertEqual((since["label"], since["status"], since["from"], since["to"]),
                         ("成立以來", "ok", "2023-01-02", "2026-11-13"))
        self.assertEqual(since["benchmark_return_pct"], 80.0)
        self.assertEqual(since["stock_native_return_pct"], 80.0)
        self.assertEqual(since["stock_return_pct"], round((18 * 33 / (10 * 30) - 1) * 100, 1))

    def test_three_years_needs_history(self) -> None:
        rows = {r["period"]: r for r in self.build(navs=[("2024-01-02", 10.0), ("2026-11-13", 12.0)])["periods"]}
        self.assertEqual(rows["3y"], {"period": "3y", "label": "近 3 年", "status": "insufficient_history",
                                      "reason": "history_not_loaded"})

    def test_distributions_are_reinvested_in_both_currencies(self) -> None:
        # 08-14 之後 10-14 除息 2.0（用當天淨值 20 買回 0.1 單位）：期末價值 18×1.1
        rows = {r["period"]: r for r in self.build(distributions=[{"ex_date": "2026-10-14", "amount": 2.0}])["periods"]}
        self.assertEqual(rows["1m"]["stock_native_return_pct"], round((18 * 1.1 / 16 - 1) * 100, 1))

    def test_chart_starts_at_one_for_both_and_ends_on_the_common_day(self) -> None:
        item = self.build()
        self.assertEqual((item["chart_from"], item["chart"][0][1], item["benchmark_chart"][0][1]), ("2023-01-02", 1.0, 1.0))
        self.assertEqual((item["chart"][-1][0], item["benchmark_chart"][-1][0]), ("2026-11-13", "2026-11-13"))

    def test_fx_effect_is_rounded_once(self) -> None:
        # 台幣 +10.04%、原幣 +9.96%：分別進位後相減是 0.0，實際差 0.08 → 0.1
        item = self.build(navs=[("2026-10-13", 10.0), ("2026-11-13", 10.996)], fx=[("2026-10-13", 30.0), ("2026-11-13", 30.0 * 11.004 / 10.996)],
                          benchmark=[("2026-10-13", 1.0), ("2026-11-13", 1.1)])
        one_month = item["periods"][0]
        self.assertEqual((one_month["stock_return_pct"], one_month["stock_native_return_pct"]), (10.0, 10.0))
        self.assertEqual(one_month["fx_effect_pct"], 0.1)

    def test_no_common_day_is_an_error_not_an_empty_item(self) -> None:
        # 匯率比所有淨值都晚才有、或 0050 全在基金淨值之前：沒有可比的一天，不出半套項目
        with self.assertRaisesRegex(ValueError, "雙方都有資料"):
            self.build(fx=[("2027-01-04", 30.0)])
        with self.assertRaisesRegex(ValueError, "雙方都有資料"):
            self.build(benchmark=[("2016-01-04", 1.0)])

    def test_fund_issues_pass_through(self) -> None:
        item = self.build(issues=[{"kind": "stale", "latest": "2026-11-13", "weekdays_behind": 9}])
        self.assertEqual(item["issues"], [{"kind": "stale", "latest": "2026-11-13", "weekdays_behind": 9}])
        self.assertEqual(item["nav_date"], "2026-11-13")

    def test_real_seed_native_return_is_the_official_total_return_series(self) -> None:
        # 原幣含息報酬直接用 total_return_series（上面 2023／2024／2025 對集保公布年報酬的回歸測試同一個算法）；
        # 匯率固定時台幣報酬＝原幣報酬、匯率影響是 0。
        import benchmark_compare as bc
        navs, dists = fc.load_seed("BLKTENAU")
        series = fc.total_return_series(navs, dists)
        bench = [(day, 1.0) for day, _ in navs]
        item = fc.compare_item("BLKTENAU", navs=navs, distributions=dists, fx=[("2022-11-01", 32.0)], benchmark=bench, issues=[])
        since = {r["period"]: r for r in item["periods"]}["since"]
        self.assertEqual(since["stock_native_return_pct"], bc._return_pct(series[0][1], series[-1][1]))
        self.assertEqual(since["stock_return_pct"], since["stock_native_return_pct"])
        self.assertEqual(since["fx_effect_pct"], 0.0)


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
    def __init__(self, series):
        self._series = series
        self.asked: list[tuple[str, str]] = []

    def series(self, code: str, end: str):
        self.asked.append((code, end))
        return [p for p in self._series if p[0] <= end], "元大台灣50", "上市"


class PrecomputeTest(unittest.TestCase):
    """build_compare_pack 用：每檔基金一份預算好的比較項目（JSON 字串，和股票項目同形狀）；資料不齊就不出那檔，不擋整個資料包。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        self.loader = _FakeLoader([("2022-11-30", 1.0), ("2026-11-09", 3.0), ("2026-11-10", 3.1)])
        # 這一組的預期值取自貝萊德的回補原件；名錄裡其他基金見 MoreFundsTest
        self.only = mock.patch.dict(fc.FUNDS, {"BLKTENAU": fc.FUNDS["BLKTENAU"]}, clear=True)
        self.only.start()

    def tearDown(self) -> None:
        self.only.stop()
        self.tmp.cleanup()

    def write_fx(self) -> None:
        import json
        (self.raw / "cbc-usdtwd-2026-11-16.json").write_text(json.dumps({"rows": [["2022-11-30", 31.0], ["2026-11-16", 32.8]]}),
                                                              encoding="utf-8")

    def test_one_compare_item_per_fund(self) -> None:
        import json
        self.write_fx()
        funds, problems = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual((list(funds), problems), (["BLKTENAU"], {}))
        item = json.loads(funds["BLKTENAU"])
        self.assertEqual((item["code"], item["kind"], item["status"], item["history"]["to"]), ("BLKTENAU", "fund", "ok", "2026-11-10"))
        self.assertEqual(self.loader.asked, [("0050", "2026-11-10")])

    def test_raw_dir_default_is_read_when_called(self) -> None:
        # 本機 live_server 不傳 raw_dir；測試換掉 RAW_DIR 時要讀換過的那個。
        import json
        from unittest import mock
        self.write_fx()
        with mock.patch.object(fc, "RAW_DIR", self.raw):
            funds, _ = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17")
        self.assertEqual(json.loads(funds["BLKTENAU"])["code"], "BLKTENAU")

    def test_codes_limits_which_funds(self) -> None:
        self.write_fx()
        self.assertEqual(fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw, codes=[]), ({}, {}))

    def test_taipei_today_is_a_date(self) -> None:
        self.assertRegex(fc.taipei_today(), r"^\d{4}-\d{2}-\d{2}$")

    def test_no_fx_means_no_fund_and_a_reason(self) -> None:
        funds, problems = fc.precompute(self.loader, "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual((funds, problems), ({}, {"BLKTENAU": "沒有匯率資料"}))

    def test_no_common_day_means_no_fund_and_a_reason(self) -> None:
        self.write_fx()
        funds, problems = fc.precompute(_FakeLoader([("2016-01-04", 1.0)]), "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual(funds, {})
        self.assertIn("雙方都有資料", problems["BLKTENAU"])

    def test_no_0050_means_no_fund(self) -> None:
        self.write_fx()
        funds, problems = fc.precompute(_FakeLoader([]), "2026-11-10", as_of="2026-11-17", raw_dir=self.raw)
        self.assertEqual((funds, problems), ({}, {"BLKTENAU": "沒有 0050 資料"}))


class DailyTest(unittest.TestCase):
    """每日排程：補齊 R2 已有的抽取檔 → 抓開放資料（一天每個來源最多一次）→ 只留這檔的列存檔 → 上傳新的。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock
        import state_sync
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = state_sync.DirectoryStore(self.root / "r2")
        self.raw = self.root / "raw"
        self.calls: list[str] = []
        # 這一組只看一檔（貝萊德）；名錄多檔的抓取見 MoreFundsDailyTest
        self.only = mock.patch.dict(fc.FUNDS, {"BLKTENAU": fc.FUNDS["BLKTENAU"]}, clear=True)
        self.only.start()

    def tearDown(self) -> None:
        self.only.stop()
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
        self.assertEqual(result["sources"]["nav-BLKTENAU"]["status"], "saved")
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
        self.assertEqual(result["sources"]["nav-BLKTENAU"]["status"], "already-fetched-today")

    def test_unchanged_file_next_day_still_counts_as_fetched_that_day(self) -> None:
        # 配息檔一個月才換一次、休市日匯率也不變：檔名相同不重寫，但當天抓過要留記號，同一天後面幾個時段不再抓。
        get = self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(("2026-10-30", "0.162", "N/A")),
                           _cbc(("2026-11-16", "32.8")))
        self.run_daily(get, now="2026-11-17T09:00:00+08:00")
        second = self.run_daily(get, now="2026-11-18T09:00:00+08:00")
        self.assertEqual(second["sources"]["dist-BLKTENAU"]["status"], "unchanged")
        self.calls.clear()
        third = self.run_daily(get, now="2026-11-18T15:00:00+08:00")
        self.assertEqual((self.calls, third["sources"]["dist-BLKTENAU"]["status"]), ([], "already-fetched-today"))
        self.assertIn("funds/raw/fetched-2026-11-18-dist-BLKTENAU.json", self.store.keys("funds/raw/"))

    def test_failed_fetch_leaves_no_mark_so_a_later_slot_retries(self) -> None:
        def broken(url: str) -> bytes:
            raise OSError("timeout")
        self.run_daily(broken)
        get = self.get_for(_opendata_nav(("2026-11-12", "18.02")), _opendata_dist(("2026-10-30", "0.162", "N/A")),
                           _cbc(("2026-11-16", "32.8")))
        self.assertEqual(self.run_daily(get, now="2026-11-17T15:00:00+08:00")["sources"]["nav-BLKTENAU"]["status"], "saved")

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
        self.assertEqual(result["sources"]["nav-BLKTENAU"], {"status": "failed", "error": "OSError: timeout"})
        self.assertEqual(result["sources"]["fx"]["status"], "saved")

    def test_missing_fund_rows_is_a_failure_not_an_empty_file(self) -> None:
        result = self.run_daily(self.get_for(_opendata_nav(), _opendata_dist(), _cbc(("2026-11-16", "32.8"))))
        self.assertEqual(result["sources"]["nav-BLKTENAU"]["status"], "failed")
        self.assertFalse(any(p.name.startswith("BLKTENAU-nav") for p in self.raw.iterdir()))


# ---- 名錄多檔基金（2026-11-24 加安聯收益成長 AM 穩定月收、摩根多重收益 A股穩定月配）----
# 站上代碼要符合 CODE_PATTERN（4～12 碼大寫英數），集保代碼可能帶連字號：AGIF-FVM → 站上 AGIFFVM，FUNDS 記 tdcc_code。

def _tdcc_nav(*rows: tuple[str, str, str]) -> bytes:
    """開放資料 3-4 的整份檔（全部基金）：(集保代碼, 日期, 淨值)，第一欄帶 BOM，外加一檔不相干的。"""
    import json
    return json.dumps([{"﻿基金代碼": c, "日期": d.replace("-", ""), "基金淨值(金額)": v, "計價幣別": "USD"} for c, d, v in rows] +
                      [{"﻿基金代碼": "OTHER", "日期": "20261110", "基金淨值(金額)": "1.0"}], ensure_ascii=False).encode()


def _tdcc_dist(*rows: tuple[str, str, str, str]) -> bytes:
    """開放資料 3-5 的整份檔：(集保代碼, 除息日, 發放日, 每單位配息)。"""
    import json
    return json.dumps([{"基金代碼": c, "﻿資料日期": "20261120", "配息基準日": ex.replace("-", ""), "除息日": ex.replace("-", ""),
                        "配息發放日": pay.replace("-", ""), "單位數": amount, "可分配淨利益/配息": "N/A", "本金/配息": "N/A"}
                       for c, ex, pay, amount in rows], ensure_ascii=False).encode()


def _write_seed(folder, code: str, nav_csv: bytes, dist_pages: list[bytes]) -> None:
    """和 seeds/funds/BLKTENAU 同格式的回補原件（清單逐檔記 SHA-256）。"""
    import hashlib
    import json
    folder.mkdir(parents=True)
    files = [{"file": "nav-backfill.csv", "kind": "nav", "sha256": hashlib.sha256(nav_csv).hexdigest()}]
    (folder / "nav-backfill.csv").write_bytes(nav_csv)
    for index, page in enumerate(dist_pages, 1):
        name = f"distributions-backfill-p{index}.json"
        (folder / name).write_bytes(page)
        files.append({"file": name, "kind": "distributions", "sha256": hashlib.sha256(page).hexdigest()})
    (folder / "backfill-manifest.json").write_text(json.dumps({"schema": "fund-backfill/v1", "fund_code": code,
                                                               "files": files}, ensure_ascii=False), encoding="utf-8")


class MoreFundsTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw, self.seeds = self.root / "raw", self.root / "seeds"
        self.raw.mkdir()
        self.seeds.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_site_codes_fit_the_site_code_rule_and_tdcc_codes_are_kept(self) -> None:
        import stock_codes
        self.assertEqual(sorted(fc.FUNDS), ["AGIFFVM", "BLKTENAU", "GLIACU"])
        for code, fund in fc.FUNDS.items():
            self.assertRegex(code, rf"^{stock_codes.CODE_PATTERN}$", code)
            self.assertEqual(fund["code"], code)
            self.assertRegex(fund["class_launch"], r"^\d{4}-\d{2}-\d{2}$", code)
            self.assertEqual(fund["currency"], "USD")
        self.assertEqual({code: fc.tdcc_code(code) for code in fc.FUNDS},
                         {"AGIFFVM": "AGIF-FVM", "BLKTENAU": "BLKTENAU", "GLIACU": "GLIACU"})
        self.assertEqual((fc.FUNDS["AGIFFVM"]["isin"], fc.FUNDS["GLIACU"]["isin"]), ("LU0820561818", "LU2347655073"))

    def test_open_data_rows_are_picked_by_the_tdcc_code_with_hyphen(self) -> None:
        import json
        rows = json.loads(_tdcc_nav(("AGIF-FVM", "2026-11-18", "6.39"), ("AGIFFVM", "2026-11-18", "9.99"),
                                    ("GLIACU", "2026-11-18", "75.20")).decode())
        self.assertEqual(fc.parse_opendata_nav(rows, "AGIF-FVM"), [("2026-11-18", 6.39)])
        self.assertEqual(fc.parse_opendata_nav(rows, "GLIACU"), [("2026-11-18", 75.2)])

    def test_pay_date_na_is_none_in_backfill_and_open_data(self) -> None:
        # 安聯回補原件前 7 筆（基準日 2021/11/12～2022/05/13）的發放日、頻率、比例都是 N/A，除息日與金額有
        import json
        page = ('{"total":1,"list":[{"asiBaseDate":"2021/11/12","asiEnterDate":"2021/11/15","asiDate":"N/A",'
                '"asiAmt":"0.035","asiFreq":"N/A","asiRatio1":"N/A","asiRatio2":"N/A"}],"pageNum":1}')
        self.assertEqual(fc.parse_backfill_distributions([page]), [
            {"ex_date": "2021-11-15", "record_date": "2021-11-12", "pay_date": None, "amount": 0.035,
             "income_pct": None, "capital_pct": None}])
        rows = json.loads(_tdcc_dist(("AGIF-FVM", "2026-11-14", "N/A", "0.035")).decode())
        self.assertEqual([(d["ex_date"], d["pay_date"]) for d in fc.parse_opendata_distributions(rows, "AGIF-FVM")],
                         [("2026-11-14", None)])
        with self.assertRaises(ValueError):   # 除息日是含息再投入的依據，沒有就不收
            fc.parse_backfill_distributions([page.replace('"asiEnterDate":"2021/11/15"', '"asiEnterDate":"N/A"')])

    def test_pay_date_none_flows_into_the_distribution_summary(self) -> None:
        import fund_distributions
        summary = fund_distributions.summary([{"ex_date": "2021-11-15", "record_date": "2021-11-12", "pay_date": None,
                                               "amount": 0.035, "income_pct": None, "capital_pct": None}],
                                             as_of="2026-11-20", currency="USD")
        self.assertEqual((summary["count"], summary["latest"]["pay_date"], summary["years"][0]["source"]), (1, None, "來源未公布"))

    def test_no_seed_folder_is_an_empty_seed_not_an_error(self) -> None:
        self.assertEqual(fc.load_seed("GLIACU", seed_dir=self.seeds), ([], []))

    def test_fund_without_seed_builds_from_daily_extracts_and_says_the_seed_is_missing(self) -> None:
        fc._extract_nav("AGIFFVM", _tdcc_nav(("AGIF-FVM", "2026-11-17", "6.38"), ("AGIF-FVM", "2026-11-18", "6.39"),
                                             ("BLKTENAU", "2026-11-18", "17.20")), "2026-11-20T09:00:00+08:00", self.raw)
        fc._extract_dist("AGIFFVM", _tdcc_dist(("AGIF-FVM", "2026-11-14", "N/A", "0.035")), "2026-11-20T09:00:00+08:00", self.raw)
        (self.raw / "cbc-usdtwd-2026-11-18.json").write_text('{"rows": [["2026-11-18", 32.5]]}', encoding="utf-8")
        h = fc.load_history("AGIFFVM", raw_dir=self.raw, seed_dir=self.seeds, as_of="2026-11-20")
        self.assertEqual(h["navs"], [("2026-11-17", 6.38), ("2026-11-18", 6.39)])
        self.assertEqual([(d["ex_date"], d["pay_date"], d["amount"]) for d in h["distributions"]], [("2026-11-14", None, 0.035)])
        self.assertEqual(h["issues"], [{"kind": "seed-missing", "code": "AGIFFVM"}])
        empty = fc.load_history("GLIACU", raw_dir=self.raw, seed_dir=self.seeds, as_of="2026-11-20")
        self.assertEqual((empty["navs"], empty["issues"]), ([], [{"kind": "seed-missing", "code": "GLIACU"}]))

    def test_seed_is_found_by_site_code_and_daily_rows_extend_it(self) -> None:
        page = ('{"total":1,"list":[{"asiBaseDate":"2026/10/30","asiEnterDate":"2026/11/02","asiDate":"2026/11/10",'
                '"asiAmt":"0.036","asiFreq":"每月","asiRatio1":"40.00%","asiRatio2":"60.00%"}],"pageNum":1}').encode()
        _write_seed(self.seeds / "AGIFFVM", "AGIFFVM", b"2026-11-13,6.30\n2026-11-16,6.35\n", [page])
        fc._extract_nav("AGIFFVM", _tdcc_nav(("AGIF-FVM", "2026-11-17", "6.38")), "2026-11-20T09:00:00+08:00", self.raw)
        (self.raw / "cbc-usdtwd-2026-11-18.json").write_text('{"rows": [["2026-11-18", 32.5]]}', encoding="utf-8")
        h = fc.load_history("AGIFFVM", raw_dir=self.raw, seed_dir=self.seeds, as_of="2026-11-20")
        self.assertEqual(h["navs"], [("2026-11-13", 6.3), ("2026-11-16", 6.35), ("2026-11-17", 6.38)])
        self.assertEqual((h["distributions"][0]["ex_date"], h["distributions"][0]["capital_pct"]), ("2026-11-02", 60.0))
        self.assertEqual(h["issues"], [])

    def test_precompute_reports_a_fund_without_any_nav_instead_of_crashing(self) -> None:
        import json
        fc._extract_nav("GLIACU", _tdcc_nav(*[("GLIACU", day, "75.2") for day in ("2026-11-09", "2026-11-10")]),
                        "2026-11-12T09:00:00+08:00", self.raw)
        (self.raw / "cbc-usdtwd-2026-11-16.json").write_text(json.dumps({"rows": [["2022-11-30", 31.0], ["2026-11-16", 32.8]]}),
                                                              encoding="utf-8")
        loader = _FakeLoader([("2022-11-30", 1.0), ("2026-11-09", 3.0), ("2026-11-10", 3.1)])
        funds, problems = fc.precompute(loader, "2026-11-10", as_of="2026-11-12", raw_dir=self.raw, seed_dir=self.seeds,
                                        codes=["AGIFFVM", "GLIACU"])
        self.assertEqual(list(funds), ["GLIACU"])
        item = json.loads(funds["GLIACU"])
        self.assertEqual((item["code"], item["name"], item["kind"], item["listed_date"], item["history"]["points"]),
                         ("GLIACU", "摩根多重收益 A股穩定月配", "fund", "2021-07-02", 2))
        self.assertEqual({r["period"]: r["status"] for r in item["periods"]}["1y"], "insufficient_history")
        self.assertIn({"kind": "seed-missing", "code": "GLIACU"}, item["issues"])
        self.assertIn("雙方都有資料", problems["AGIFFVM"])


class MoreFundsDailyTest(unittest.TestCase):
    """每日抓取：名錄每一檔各自存檔、各自一筆回報；整份 3-4、3-5 一次執行只下載一次。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        self.calls: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def get(self, url: str) -> bytes:
        self.calls.append(url)
        return {
            fc.NAV_URL: _tdcc_nav(("BLKTENAU", "2026-11-18", "17.20"), ("AGIF-FVM", "2026-11-17", "6.38"),
                                  ("AGIF-FVM", "2026-11-18", "6.39"), ("GLIACU", "2026-11-18", "75.20")),
            fc.DIST_URL: _tdcc_dist(("BLKTENAU", "2026-10-30", "2026-11-06", "0.162"), ("AGIF-FVM", "2026-11-14", "N/A", "0.035"),
                                    ("GLIACU", "2026-11-03", "2026-11-12", "0.40")),
            fc.CBC_URL: _cbc(("2026-11-18", "32.5")),
        }[url]

    def test_every_fund_is_saved_under_its_site_code_with_only_its_own_rows(self) -> None:
        import json
        result = fc.daily(store=None, get=self.get, now="2026-11-20T09:00:00+08:00", raw_dir=self.raw)
        self.assertEqual(sorted(self.calls), sorted([fc.NAV_URL, fc.DIST_URL, fc.CBC_URL]), "同一個網址只下載一次")
        self.assertEqual({key: value["status"] for key, value in result["sources"].items()},
                         {"nav-AGIFFVM": "saved", "dist-AGIFFVM": "saved", "nav-BLKTENAU": "saved", "dist-BLKTENAU": "saved",
                          "nav-GLIACU": "saved", "dist-GLIACU": "saved", "fx": "saved"})
        saved = sorted(p.name for p in self.raw.iterdir() if not p.name.startswith("fetched-"))
        self.assertEqual(saved, ["AGIFFVM-dist-2026-11-20.json", "AGIFFVM-nav-2026-11-18.json", "BLKTENAU-dist-2026-11-20.json",
                                 "BLKTENAU-nav-2026-11-18.json", "GLIACU-dist-2026-11-20.json", "GLIACU-nav-2026-11-18.json",
                                 "cbc-usdtwd-2026-11-18.json"])
        body = json.loads((self.raw / "AGIFFVM-nav-2026-11-18.json").read_text(encoding="utf-8"))
        self.assertEqual((body["code"], body["tdcc_code"], body["window"], len(body["rows"])),
                         ("AGIFFVM", "AGIF-FVM", ["2026-11-17", "2026-11-18"], 2))
        self.calls.clear()
        again = fc.daily(store=None, get=self.get, now="2026-11-20T15:00:00+08:00", raw_dir=self.raw)
        self.assertEqual((self.calls, again["sources"]["nav-GLIACU"]["status"]), ([], "already-fetched-today"))

    def test_one_fund_missing_from_open_data_fails_alone(self) -> None:
        def get(url: str) -> bytes:
            if url == fc.NAV_URL:
                return _tdcc_nav(("BLKTENAU", "2026-11-18", "17.20"))
            return self.get(url)
        result = fc.daily(store=None, get=get, now="2026-11-20T09:00:00+08:00", raw_dir=self.raw)
        self.assertEqual(result["sources"]["nav-BLKTENAU"]["status"], "saved")
        self.assertEqual(result["sources"]["nav-AGIFFVM"], {"status": "failed", "error": "ValueError: 開放資料 3-4 沒有 AGIF-FVM 的淨值"})
        self.assertEqual(result["sources"]["nav-GLIACU"]["status"], "failed")
        self.assertFalse((self.raw / "fetched-2026-11-20-nav-AGIFFVM.json").exists(), "失敗的不留記號，下一個時段再抓")

    def test_a_failed_download_is_tried_once_per_run_and_reported_for_every_fund(self) -> None:
        def get(url: str) -> bytes:
            self.calls.append(url)
            if url == fc.NAV_URL:
                raise OSError("timeout")
            return self.get(url)
        result = fc.daily(store=None, get=get, now="2026-11-20T09:00:00+08:00", raw_dir=self.raw)
        self.assertEqual(self.calls.count(fc.NAV_URL), 1)
        self.assertEqual({result["sources"][f"nav-{code}"]["error"] for code in fc.FUNDS}, {"OSError: timeout"})
        self.assertEqual(result["sources"]["dist-GLIACU"]["status"], "saved")


if __name__ == "__main__":
    unittest.main()
