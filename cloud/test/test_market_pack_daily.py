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
    def test_human_authored_files_are_carried_over_not_regenerated(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
