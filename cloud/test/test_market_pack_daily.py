"""U2：大盤頁資料包每日重建入口的契約（只測入口本身：不寫 web/、人寫文字原樣沿用、根目錄寫法變了就停）。

用法：PYTHONPATH=. python cloud/test/test_market_pack_daily.py
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud import market_pack_daily as mpd


ROOT = Path(__file__).resolve().parents[2]


class ScriptLoadingTest(unittest.TestCase):
    def test_root_is_rewritten_only_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_builder.py"
            original = (
                'from pathlib import Path\n'
                f'{mpd.HARDCODED_ROOT}\n'
                'def main():\n'
                '    (ROOT / "out.txt").write_text(str(ROOT), encoding="utf-8")\n'
                '    return 0\n'
                'if __name__ == "__main__":\n'
                '    raise SystemExit(99)\n'
            )
            script.write_text(original, encoding="utf-8")
            target = Path(tmp) / "root"
            target.mkdir()
            self.assertEqual(mpd._call_main(script, target, []), 0)
            self.assertEqual((target / "out.txt").read_text(encoding="utf-8"), str(target))
            self.assertEqual(script.read_text(encoding="utf-8"), original, "腳本檔案本身不得被改")

    def test_scripts_that_define_dataclasses_can_be_loaded(self) -> None:
        """e2e 抓到：fetch_analysis_data.py 有 @dataclass，exec 時模組必須登記在 sys.modules。"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "with_dataclass.py"
            script.write_text(
                'from dataclasses import dataclass\nfrom pathlib import Path\n'
                f'{mpd.HARDCODED_ROOT}\n'
                '@dataclass\nclass Record:\n    target: str\n'
                'def main():\n    return 0 if Record("x").target == "x" else 1\n', encoding="utf-8")
            self.assertEqual(mpd._call_main(script, Path(tmp), []), 0)

    def test_changed_root_line_stops_instead_of_writing_to_the_real_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "moved.py"
            script.write_text('from pathlib import Path\nROOT = Path(__file__).parent\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "根目錄寫法變了"):
                mpd._call_main(script, Path(tmp), [])

    def test_the_four_real_builders_still_use_the_expected_root_line(self) -> None:
        for name in ("fetch_analysis_data.py", "build_analysis_data.py", "build_chips_data.py", "build_stock_detail.py"):
            with self.subTest(script=name):
                self.assertIn(mpd.HARDCODED_ROOT, (ROOT / "logs" / name).read_text(encoding="utf-8"))


class OutputContractTest(unittest.TestCase):
    def test_human_authored_files_are_carried_over_before_the_optional_rewrite(self) -> None:
        self.assertEqual(set(mpd.HUMAN_AUTHORED), {"dashboard-data.js", "plain-data.js"})
        self.assertEqual(set(mpd.FILES), {"analysis-data.js", "chips-data.js", "detail-data.js",
                                          "dashboard-data.js", "plain-data.js"})

    def test_stage_must_live_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(Exception, ""):
                # out 在 root 外沒關係；stage 一律在 root/.tmp 下（建置腳本的輸出路徑相對 ROOT）。
                mpd.build("2026-11-06", Path(tmp) / "out", root=Path(tmp) / "no-such-root", fetch=False)

    def test_computed_trend_is_added_to_carried_dashboard_without_changing_human_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "analysis-data.js"
            dashboard = root / "dashboard-data.js"
            trend = {"schema": "market-trend/v1", "data_date": "2026-11-10", "periods": []}
            analysis.write_text(
                "window.MARKET_ANALYSIS = " + json.dumps({"market_trend": trend}, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            original = {"headline": {"title": "人寫劇本"}, "actions": {"stand_back": "等待"}}
            dashboard.write_text(
                "window.MARKET_DASHBOARD = " + json.dumps(original, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )

            mpd._attach_market_trend(analysis, dashboard)

            payload = mpd._read_assignment(dashboard, "window.MARKET_DASHBOARD = ")
            self.assertEqual(payload["headline"], original["headline"])
            self.assertEqual(payload["actions"]["stand_back"], "等待")
            self.assertEqual(payload["actions"]["market_trend"], trend)

    def test_missing_or_invalid_trend_leaves_dashboard_untouched_and_does_not_raise(self) -> None:
        for label, trend in (("missing", "absent"), ("null", None), ("wrong-schema", {"schema": "other"})):
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                analysis = root / "analysis-data.js"
                dashboard = root / "dashboard-data.js"
                body = {"meta": {"cutoff_date": "2026-11-10"}}
                if trend != "absent":
                    body["market_trend"] = trend
                analysis.write_text("window.MARKET_ANALYSIS = " + json.dumps(body, ensure_ascii=False) + ";" + "\n", encoding="utf-8")
                original = "window.MARKET_DASHBOARD = " + json.dumps({"actions": {"stand_back": "等待"}}, ensure_ascii=False) + ";" + "\n"
                dashboard.write_text(original, encoding="utf-8")

                self.assertFalse(mpd._attach_market_trend(analysis, dashboard))

                self.assertEqual(dashboard.read_text(encoding="utf-8"), original)


def _source(root: Path, days: dict[str, list[str]]) -> Path:
    """近期明細原件：{資料集: [YYYYMMDD, ...]}，內容不重要，檢查只看有哪幾天。"""
    folder = root / "data/market-snapshots/stock-detail"
    for dataset, compacts in days.items():
        (folder / dataset).mkdir(parents=True, exist_ok=True)
        for compact in compacts:
            (folder / dataset / f"{dataset}-{compact}.json").write_text("{}", encoding="utf-8")
    return folder


def _row(code: str = "2330") -> dict:
    return {"code": code, "name": "台積電", "value": 1, "streak": 1, "capped": False}


FULL_DAY = {"foreign_buy": [_row()], "foreign_sell": [_row()], "trust_buy": [_row()], "trust_sell": [_row()],
            "turnover_top": [_row()], "margin_up": [_row()], "margin_down": [_row()], "short_up": [_row()],
            "short_down": [_row()]}


def _detail(by_date: dict, dates: dict, current: dict | None = None) -> dict:
    return {"days": sorted(by_date), "by_date": by_date,
            "current": {"as_of": max(by_date), "dates": dates, **(FULL_DAY if current is None else current)}}


class DetailCheckTest(unittest.TestCase):
    """11-17 本人回報：首頁籌碼卡點開，最近幾天都「這一天沒有資料」，而且沒有任何檢查擋下。
    每日建置完要對照原件：最新有原件的那天，六張卡的名單都要有人；沒有就在站台狀態標失敗。"""

    DATES = {"foreign": "2026-11-16", "trust": "2026-11-16", "margin": "2026-11-13", "short": "2026-11-13",
             "amount": "2026-11-16"}

    def test_all_six_cards_filled_on_their_latest_source_day_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), {"t86": ["20261113", "20261116"], "quotes": ["20261113", "20261116"],
                                         "margin": ["20261113"]})
            detail = _detail({"2026-11-13": FULL_DAY, "2026-11-16": {k: v for k, v in FULL_DAY.items()
                                                                     if not k.startswith(("margin", "short"))}},
                             self.DATES)
            self.assertEqual(mpd.check_detail(detail, source),
                             {"status": "ok", "data_date": "2026-11-16", "reason": None})

    def test_lists_empty_because_quotes_were_never_written_is_a_failure(self) -> None:
        # 正式站 11-16 的樣子：t86 有、行情沒有 → 外資、投信、成交金額的名單全空
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), {"t86": ["20261106", "20261116"], "quotes": ["20261106"],
                                         "margin": ["20261106", "20261116"]})
            credit_only = {k: v for k, v in FULL_DAY.items() if k.startswith(("margin", "short"))}
            detail = _detail({"2026-11-06": FULL_DAY, "2026-11-16": credit_only},
                             {**self.DATES, "foreign": "2026-11-06", "trust": "2026-11-06", "amount": "2026-11-06",
                              "margin": "2026-11-16", "short": "2026-11-16"})
            result = mpd.check_detail(detail, source)
            self.assertEqual((result["status"], result["data_date"]), ("failed", "2026-11-16"))
            for card in ("外資", "投信", "成交金額"):
                self.assertIn(card, result["reason"])
            self.assertIn("行情", result["reason"])     # 說得出缺的是哪一份原件
            self.assertNotIn("融資", result["reason"])

    def test_current_top_ten_empty_or_on_the_wrong_day_is_a_failure(self) -> None:
        # 「目前前 10」拿全體最新一天排、融資那天還沒公布 → 融資融券的目前前 10 是空的
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), {"t86": ["20261116"], "quotes": ["20261116"], "margin": ["20261113"]})
            detail = _detail({"2026-11-13": FULL_DAY, "2026-11-16": FULL_DAY},
                             {**self.DATES, "margin": "2026-11-16"},
                             current={**FULL_DAY, "short_up": [], "short_down": []})
            result = mpd.check_detail(detail, source)
            self.assertEqual(result["status"], "failed")
            self.assertIn("融資", result["reason"])
            self.assertIn("融券", result["reason"])
            self.assertNotIn("外資", result["reason"])

    def test_an_empty_day_in_the_middle_is_a_failure_too(self) -> None:
        # 審查指出：只看最新一天的話，中間幾天（例如補不回行情的 11-10）是空的也會靜靜過關
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), {"t86": ["20261109", "20261110", "20261116"],
                                         "quotes": ["20261109", "20261116"], "margin": ["20261116"]})
            detail = _detail({"2026-11-09": FULL_DAY, "2026-11-10": {}, "2026-11-16": FULL_DAY},
                             {**self.DATES, "margin": "2026-11-16", "short": "2026-11-16"})
            result = mpd.check_detail(detail, source)
            self.assertEqual(result["status"], "failed")
            self.assertIn("外資：2026-11-10 的名單是空的", result["reason"])
            self.assertIn("缺 2026-11-10 的每日收盤行情原件", result["reason"])
            self.assertNotIn("融資", result["reason"])

    def test_a_broken_check_is_reported_and_does_not_stop_the_market_pack(self) -> None:
        # 檢查不擋發布：detail-data.js 讀不起來時回報失敗，不能讓整包大盤資料改用上一版
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "detail-data.js"
            broken.write_text("not js", encoding="utf-8")
            result = mpd._safe_check_detail(broken, Path(tmp))
            self.assertEqual((result["status"], result["data_date"]), ("failed", None))
            self.assertIn("檢查本身出錯", result["reason"])

    def test_no_sources_at_all_is_a_failure_not_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = mpd.check_detail({"days": [], "by_date": {}, "current": {}}, Path(tmp) / "none")
            self.assertEqual(result["status"], "failed")


class PlainDailyTest(unittest.TestCase):
    """本人 11-17：首頁「籌碼與資金」白話分析改成每天依當天數字重寫；開關 MARKET_ANA_PLAIN_DAILY 預設關。"""

    def setUp(self) -> None:
        import sys
        sys.path.insert(0, str(ROOT))
        from test_plain_daily import good_ai, market
        self.good_ai = good_ai
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()
        self.audit_dir = Path(self.tmp.name) / "audit"
        analysis, chips = market()
        mpd._write_assignment(self.out / "analysis-data.js", "window.MARKET_ANALYSIS = ", analysis)
        mpd._write_assignment(self.out / "chips-data.js", "window.MARKET_CHIPS = ", chips)
        self.carried = "window.MARKET_PLAIN = " + json.dumps({"id": "人寫"}, ensure_ascii=False) + ";\n"
        (self.out / "plain-data.js").write_text(self.carried, encoding="utf-8")
        self.calls = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def caller(self, result):
        def call(prompt, schema):
            self.calls += 1
            return result
        return call

    def apply(self, mode, result=("ok", None, "")):
        result = ("ok", self.good_ai(), "") if result == ("ok", None, "") else result
        return mpd._apply_plain(self.out, "2026-11-06", mode=mode, call_model=self.caller(result), audit_dir=self.audit_dir)

    def plain(self) -> dict:
        return mpd._read_assignment(self.out / "plain-data.js", "window.MARKET_PLAIN = ")

    def test_off_keeps_the_carried_file_and_calls_nothing(self) -> None:
        report = self.apply("off")
        self.assertEqual((self.out / "plain-data.js").read_text(encoding="utf-8"), self.carried)
        self.assertEqual(self.calls, 0)
        self.assertEqual(report["mode"], "off")
        self.assertFalse(self.audit_dir.exists())

    def test_on_replaces_the_file_with_todays_checked_text(self) -> None:
        report = self.apply("on")
        plain = self.plain()
        self.assertEqual(plain["source"], "ai")
        self.assertEqual(plain["data_dates"]["price_and_foreign"], "2026-11-06")
        self.assertEqual(report["source"], "ai")
        audit = json.loads((self.audit_dir / "2026-11-06.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["source"], "ai")

    def test_on_with_a_failed_model_publishes_the_program_text(self) -> None:
        report = self.apply("on", ("model_timeout", None, "超過"))
        self.assertEqual(self.plain()["source"], "template")
        self.assertEqual(report["failure"], "model_timeout")

    def test_dry_run_keeps_the_page_but_records_what_would_be_published(self) -> None:
        report = self.apply("dry")
        self.assertEqual((self.out / "plain-data.js").read_text(encoding="utf-8"), self.carried)
        self.assertEqual(self.calls, 1)
        audit = json.loads((self.audit_dir / "2026-11-06.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["candidate"]["source"], "ai")
        self.assertNotIn("candidate", report, "印在最後一行的報告只放摘要")

    def test_broken_inputs_keep_the_carried_file_and_do_not_raise(self) -> None:
        (self.out / "chips-data.js").write_text("broken", encoding="utf-8")
        report = self.apply("on")
        self.assertEqual((self.out / "plain-data.js").read_text(encoding="utf-8"), self.carried)
        self.assertTrue(report["failure"])

    def test_settings_come_from_the_workflow_environment(self) -> None:
        mode, caller = mpd._plain_settings({"PLAIN_DAILY_MODE": "on", "PLAIN_AGY_BIN": "", "PLAIN_AGY_HOME": ""})
        self.assertEqual(mode, "on")
        self.assertEqual(caller("p", {})[0], "agy_unavailable")
        self.assertEqual(mpd._plain_settings({})[0], "off")
        self.assertEqual(mpd._plain_settings({"PLAIN_DAILY_MODE": "maybe"})[0], "off")

    def test_build_rewrites_only_after_the_cutoff_check(self) -> None:
        import inspect
        source = inspect.getsource(mpd.build)
        self.assertLess(source.index("analysis_cutoff"), source.index("_apply_plain("))


if __name__ == "__main__":
    unittest.main()
