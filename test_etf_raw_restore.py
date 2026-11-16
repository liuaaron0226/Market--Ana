"""ETF 日線補抓（2026-11-16 本人同意「最近 130 個交易日」）：雲端工作集的每日行情原件只剩最近幾天，
ETF 回補沒有原件可讀。對行情庫 source_days 記著、但原件不在的最近 N 天，向官方重新抓同一份每日行情原件；
抓回來的位元組雜湊必須和 source_days 當初記的相同才放回原件夾（不同就丟掉，第一天就不同就整批停），
每次請求間隔 5 秒、不正常的回應立刻停；補完再重掃 ETF。不動行情庫裡任何既有的列。"""
from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import etf_history as eh
import otc_history as oh
import stock_history as sh
from test_daily_ingest import OTC_6488, Recorder
from test_etf_history import LISTED_DAY_ROWS, OTC_00888, _otc_raw_plain, codes_on, listed_raw
from test_otc_history import csv_bytes
from test_stock_history import official_payload

DAYS = ["20261103", "20261104", "20261105", "20261106"]


def listed_response(day: str, rows=None) -> sh.Response:
    return sh.Response(200, "application/json", official_payload(day, rows or LISTED_DAY_ROWS), "u")


class ListedRestoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.db = self.out / "stock-history.sqlite"
        for day in DAYS:
            listed_raw(self.out, day)
        sh.build_database(self.out, self.db)
        # 雲端工作集的樣子：行情庫記著四天，原件只剩最後一天
        for day in DAYS[:-1]:
            sh.raw_file(self.out, day).unlink()
        self.sleeps: list[float] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def restore(self, responses: dict, last_days: int = 3) -> tuple[dict, Recorder]:
        rec = Recorder({("MI_INDEX", day): response for day, response in responses.items()})
        report = eh.restore_raw("上市", self.out, self.db, last_days, get=rec.two_arg, sleep=self.sleeps.append)
        return report, rec

    def test_puts_back_raw_of_the_latest_days_when_bytes_match_what_the_database_recorded(self) -> None:
        report, rec = self.restore({day: listed_response(day) for day in DAYS})
        # 最近 3 天裡缺原件的只有 11-04、11-05（11-06 原件還在、11-03 超出範圍），由新到舊抓
        self.assertEqual(rec.calls, [("MI_INDEX", "20261105"), ("MI_INDEX", "20261104")])
        self.assertEqual(report["restored"], ["2026-11-04", "2026-11-05"])
        self.assertEqual((report["requests"], report["hash_mismatch"], report["stopped"]), (2, [], None))
        self.assertEqual(self.sleeps, [5.0])
        self.assertTrue(sh.raw_file(self.out, "20261104").exists())
        self.assertFalse(sh.raw_file(self.out, "20261103").exists())
        # 放回去的原件和每日更新存的一樣能被 ETF 回補讀到
        eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertIn("0056", codes_on(self.db, "2026-11-05"))
        self.assertNotIn("0056", codes_on(self.db, "2026-11-03"))

    def test_windows_style_paths_recorded_in_the_database_still_restore_to_the_normal_raw_folder(self) -> None:
        # 行情庫最早在 Windows 建，source_days.raw_file 記成 raw\2026\MI_INDEX-…；雲端（Linux）要放回正常的 raw/2026/ 底下
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE source_days SET raw_file = replace(raw_file, '/', '\')")
        connection.commit()
        connection.close()
        report, _ = self.restore({day: listed_response(day) for day in DAYS})
        self.assertEqual(report["restored"], ["2026-11-04", "2026-11-05"])
        self.assertTrue(sh.raw_file(self.out, "20261105").exists())
        self.assertEqual([p.name for p in self.out.iterdir() if "\\" in p.name], [])
        eh.backfill(self.db, self.out, "上市", {"0056"})
        self.assertIn("0056", codes_on(self.db, "2026-11-05"))
        self.assertIn("0056", codes_on(self.db, "2026-11-06"), "原本就在的那天照樣讀得到")

    def test_recorded_windows_separators_resolve_like_on_the_linux_runner(self) -> None:
        from pathlib import PurePosixPath

        out = PurePosixPath("/runner/data/stock-history")
        self.assertEqual(eh.raw_path(out, r"raw\2026\MI_INDEX-20261105.json.gz"),
                         PurePosixPath("/runner/data/stock-history/raw/2026/MI_INDEX-20261105.json.gz"))
        self.assertEqual(eh.raw_path(out, "raw/20261105.csv"), PurePosixPath("/runner/data/stock-history/raw/20261105.csv"))

    def test_first_day_whose_bytes_differ_stops_everything_after_one_request(self) -> None:
        changed = {day: listed_response(day, LISTED_DAY_ROWS[:-1]) for day in DAYS}   # 官方內容和當初不同
        report, rec = self.restore(changed)
        self.assertEqual(rec.calls, [("MI_INDEX", "20261105")])
        self.assertEqual(report["hash_mismatch"], ["2026-11-05"])
        self.assertEqual(report["stopped"]["date"], "2026-11-05")
        self.assertEqual(report["restored"], [])
        self.assertFalse(sh.raw_file(self.out, "20261105").exists(), "不同的位元組不放進原件夾")

    def test_a_later_day_whose_bytes_differ_is_dropped_and_the_rest_continue(self) -> None:
        responses = {day: listed_response(day) for day in DAYS}
        responses["20261104"] = listed_response("20261104", LISTED_DAY_ROWS[:-1])
        report, rec = self.restore(responses, last_days=4)
        self.assertEqual(len(rec.calls), 3)
        self.assertEqual((report["restored"], report["hash_mismatch"], report["stopped"]),
                         (["2026-11-03", "2026-11-05"], ["2026-11-04"], None))
        self.assertFalse(sh.raw_file(self.out, "20261104").exists())

    def test_non_200_stops_immediately(self) -> None:
        responses = {day: listed_response(day) for day in DAYS}
        responses["20261104"] = sh.Response(429, "text/html", b"too many", "u")
        report, rec = self.restore(responses, last_days=4)
        self.assertEqual(rec.calls, [("MI_INDEX", "20261105"), ("MI_INDEX", "20261104")])
        self.assertEqual(report["stopped"], {"date": "2026-11-04", "reason": "HTTP 429"})
        self.assertEqual(report["restored"], ["2026-11-05"])

    def test_nothing_to_do_makes_no_request(self) -> None:
        report, rec = self.restore({}, last_days=1)
        self.assertEqual((rec.calls, report["candidates"], report["requests"]), ([], 0, 0))


class OtcRestoreTest(unittest.TestCase):
    def test_otc_raw_and_its_hash_note_come_back_when_bytes_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            content = {day: csv_bytes(OTC_6488, OTC_00888, day=f"115/{day[4:6]}/{day[6:]}") for day in DAYS[2:]}
            for day, data in content.items():
                _otc_raw_plain(out, day, data)
            db = out / "otc_history.sqlite"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE source_days (date TEXT PRIMARY KEY, raw_file TEXT, sha256 TEXT, rows INTEGER)")
            for day, data in content.items():
                import hashlib
                connection.execute("INSERT INTO source_days VALUES (?, ?, ?, 2)",
                                   (f"{day[:4]}-{day[4:6]}-{day[6:]}", f"raw/{day}.csv", hashlib.sha256(data).hexdigest()))
            connection.commit()
            connection.close()
            (out / "raw" / "20261105.csv").unlink()
            (out / "raw" / "20261105.csv.sha256").unlink()
            rec = Recorder({("dailyQuotes", "2026/11/05"): oh.Response(200, "text/csv", content["20261105"], "u")})
            report = eh.restore_raw("上櫃", out, db, 5, get=rec.three_arg, sleep=lambda s: None)
            self.assertEqual((report["restored"], report["stopped"]), (["2026-11-05"], None))
            self.assertEqual((out / "raw" / "20261105.csv").read_bytes(), content["20261105"])
            self.assertTrue((out / "raw" / "20261105.csv.sha256").exists())


class RestoreAndRescanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.listed = self.root / "data/stock-history"
        for day in DAYS:
            listed_raw(self.listed, day)
        sh.build_database(self.listed, self.listed / "stock-history.sqlite")
        for day in DAYS[:-1]:
            sh.raw_file(self.listed, day).unlink()
        self.rescans: list[dict] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_restore(self, listed_responses: dict) -> dict:
        rec = Recorder({("MI_INDEX", day): response for day, response in listed_responses.items()})
        otc = Recorder({})
        rescan = lambda root, force: self.rescans.append({"root": root, "force": force}) or {"status": "ok"}
        return eh.restore_and_rescan(self.root, 4, get_listed=rec.two_arg, get_otc=otc.three_arg,
                                     sleep=lambda s: None, rescan=rescan)

    def test_restores_each_market_then_rescans_etfs_with_force(self) -> None:
        report = self.run_restore({day: listed_response(day) for day in DAYS})
        self.assertEqual(report["上市"]["restored"], ["2026-11-03", "2026-11-04", "2026-11-05"])
        self.assertEqual(report["上櫃"]["status"], "no-database")
        self.assertEqual(self.rescans, [{"root": self.root, "force": True}])
        self.assertEqual((report["rescan"], report["status"]), ({"status": "ok"}, "ok"))

    def test_one_market_restored_and_the_other_stopped_still_rescans_and_reports_both(self) -> None:
        otc_db = self.root / "data/otc-history/otc_history.sqlite"
        otc_db.parent.mkdir(parents=True)
        otc_db.touch()
        stop = {"market": "上櫃", "restored": [], "stopped": {"date": "2026-11-05", "reason": "HTTP 429"}}
        real = eh.restore_raw
        with unittest.mock.patch.object(eh, "restore_raw", side_effect=lambda market, *a, **k: stop if market == "上櫃" else real(market, *a, **k)):
            report = self.run_restore({day: listed_response(day) for day in DAYS})
        self.assertEqual((report["status"], report["上櫃"]["stopped"]["reason"]), ("partial", "HTTP 429"))
        self.assertEqual(len(report["上市"]["restored"]), 3)
        self.assertEqual(len(self.rescans), 1)

    def test_nothing_restored_because_the_first_day_differs_means_failed_and_no_rescan(self) -> None:
        report = self.run_restore({day: listed_response(day, LISTED_DAY_ROWS[:-1]) for day in DAYS})
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.rescans, [])


class CommandLineTest(unittest.TestCase):
    def test_restore_days_outside_1_to_300_is_refused_before_any_request(self) -> None:
        for value in ("301", "-1", "0"):
            with self.assertRaises(SystemExit):
                eh.main(["--restore-raw-days", value])

    def test_failed_restore_exits_non_zero_so_the_workflow_stops_before_publishing(self) -> None:
        with unittest.mock.patch.object(eh, "restore_and_rescan", return_value={"status": "failed"}):
            self.assertEqual(eh.main(["--restore-raw-days", "130"]), 1)
        with unittest.mock.patch.object(eh, "restore_and_rescan", return_value={"status": "ok"}):
            self.assertEqual(eh.main(["--restore-raw-days", "130"]), 0)


class DailyWorkflowTest(unittest.TestCase):
    """手動觸發每日更新時填 etf_raw_days 才補抓；補抓在還原雲端工作集之後、每日更新（重新發布）之前，
    補抓失敗整個 run 停下，不發布、也不把工作集同步回 R2。"""

    def test_manual_input_runs_the_restore_between_the_working_set_restore_and_the_daily_update(self) -> None:
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parent / ".github/workflows/daily-update.yml").read_text(encoding="utf-8"))
        option = doc[True]["workflow_dispatch"]["inputs"]["etf_raw_days"]
        self.assertEqual((option["type"], option["default"], option["required"]), ("string", "", False))
        steps = doc["jobs"]["update"]["steps"]
        names = [step.get("name", "") for step in steps]
        restore = next(i for i, step in enumerate(steps) if "--restore-raw-days" in step.get("run", ""))
        self.assertLess(names.index("從 R2 還原工作集"), restore)
        self.assertLess(restore, next(i for i, step in enumerate(steps) if step.get("id") == "run"))
        step = steps[restore]
        self.assertEqual(step["if"], "inputs.etf_raw_days != ''")
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("${{", step["run"], "輸入只經環境變數進命令，不直接拼進指令")
        self.assertIn("^[0-9]{1,3}$", step["run"])
        self.assertIn("INPUT_DRY_RUN", step["env"])
        self.assertIn('"$INPUT_DRY_RUN" = "true"', step["run"], "試跑時不准補抓：抓了也不會同步回 R2，白送幾百次請求")
