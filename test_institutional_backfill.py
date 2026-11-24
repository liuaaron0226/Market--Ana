"""一次性回補上市／上櫃三大法人（institutional_backfill.py 與 institutional-backfill.yml）。

舊日期格式用 2026-11-24 實際向官方抓的三份原件（gzip 存在 test_fixtures/institutional_backfill/，位元組未改）。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml

import institutional_backfill as ib
import listed_institutional as li
import otc_market_data as omd
import state_sync
from test_listed_institutional import ROW_2330, t86_json
from test_otc_market_data import INST_6488, inst_csv

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "test_fixtures/institutional_backfill"
WORKFLOW = ROOT / ".github/workflows/institutional-backfill.yml"


def fixture(name: str) -> bytes:
    return gzip.decompress((FIXTURES / name).read_bytes())


class Response:
    def __init__(self, status=200, content=b"", content_type="application/json;charset=UTF-8"):
        self.status, self.content, self.content_type, self.url = status, content, content_type, "https://example/x"


def roc_title(compact: str) -> str:
    return f"{int(compact[:4]) - 1911}年{compact[4:6]}月{compact[6:]}日"


def listed_ok(compact):
    return Response(content=t86_json(day=compact, rows=[ROW_2330]))


def otc_ok(compact):
    return Response(content=inst_csv(day_title=roc_title(compact), rows=[INST_6488]),
                    content_type="application/csv;charset=MS950")


LISTED_EMPTY = Response(content=json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode("utf-8"))
OTC_EMPTY = Response(content=json.dumps({"stat": "無資料可供下載", "tables": [{"data": []}]}).encode("utf-8"))


class OldFormatTest(unittest.TestCase):
    """2021、2023 年的官方原件用現行端點、現行解析器就讀得懂（欄位與現在相同）。"""

    def test_t86_2021_original_parses_with_the_current_parser(self):
        rows = li.parse_t86_json(fixture("t86-20210104.json.gz"), "20210104")
        self.assertEqual(len(rows), 1079)
        hon_hai = next(r for r in rows if r["code"] == "2317")
        self.assertEqual((hon_hai["foreign_net"], hon_hai["trust_net"], hon_hai["dealer_net"], hon_hai["total_net"]),
                         (46_741_199, 1_070_960, 16_141_524, 63_953_683))
        for row in rows:
            if None not in (row["foreign_net"], row["trust_net"], row["dealer_net"]):
                self.assertEqual(row["foreign_net"] + row["trust_net"] + row["dealer_net"], row["total_net"])

    def test_tpex_2021_and_2023_originals_parse_with_the_current_parser(self):
        for day, count in (("20210104", 654), ("20230601", 762)):
            with self.subTest(day):
                rows = omd.parse_institutional_csv(fixture(f"tpex-{day}.csv.gz"), day)
                self.assertEqual(len(rows), count)
                self.assertEqual(rows[0]["code"], "00679B")
                for row in rows:
                    self.assertEqual(row["foreign_ex_dealer_net"] + row["foreign_dealer_net"], row["foreign_net"])

    def test_real_originals_classify_as_ok(self):
        self.assertEqual(ib.classify("listed", Response(content=fixture("t86-20210104.json.gz")))[0], "ok")
        self.assertEqual(ib.classify("otc", Response(content=fixture("tpex-20210104.csv.gz"),
                                                     content_type="application/csv;charset=MS950"))[0], "ok")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


class BackfillTest(unittest.TestCase):
    TRADING = ["20210104", "20210105", "20210106", "20210107", "20210108"]   # 01-08 起法人庫已有

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.store = state_sync.DirectoryStore(Path(self.tmp.name) / "r2")
        index = self.root / ib.INDEX_DIR
        index.mkdir(parents=True)
        rows = [[f"110/{d[4:6]}/{d[6:]}", "1", "1", "1", "1"] for d in self.TRADING]
        (index / "taiex-202101.json").write_text(json.dumps({"data": {"data": rows}}), encoding="utf-8")
        (index / "taiex-202012.json").write_text(json.dumps({"data": {"data": [["109/12/31", "1", "1", "1", "1"]]}}),
                                                 encoding="utf-8")
        listed = self.root / "data/listed-institutional"
        omd.write_raw(listed, "20210108.json", t86_json(day="20210108", rows=[ROW_2330]))
        li.build_db(listed, self.root / ib.MARKETS["listed"]["db"])
        otc = self.root / "data/otc-history/raw-institutional"
        omd.write_raw(otc, "20210108.csv", inst_csv(day_title=roc_title("20210108")))
        omd.build_market_db(otc.parent, self.root / ib.MARKETS["otc"]["db"])
        self.calls: list[tuple[str, str]] = []
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def web(self, overrides=None):
        overrides = dict(overrides or {})

        def get(session, url, params):
            market = "listed" if url == li.T86_URL else "otc"
            compact = params["date"].replace("/", "")
            self.calls.append((market, compact))
            queued = overrides.get((market, compact))
            if isinstance(queued, list):
                return queued.pop(0) if len(queued) > 1 else queued[0]
            if queued is not None:
                return queued
            return listed_ok(compact) if market == "listed" else otc_ok(compact)
        return get

    def run_fetch(self, overrides=None, **kwargs):
        kwargs.setdefault("start", "2021-01-01")
        return ib.fetch(self.root, self.store, get=self.web(overrides), sleep=self.clock.sleep, clock=self.clock, **kwargs)

    def test_range_is_start_to_the_day_before_coverage_from_the_trading_calendar(self):
        self.assertEqual(ib.target_days(self.root, "listed", "2021-01-01"), ["20210104", "20210105", "20210106", "20210107"])
        self.assertEqual(ib.target_days(self.root, "otc", "2021-01-05"), ["20210105", "20210106", "20210107"])
        self.assertEqual(ib.target_days(self.root, "listed", "2020-12-01")[0], "20201231")

    def test_runs_resume_newest_first_without_refetching_and_archive_originals_with_sha(self):
        first = self.run_fetch(days_per_run=2)
        self.assertEqual(first["saved"], {"listed": 2, "otc": 2})
        self.assertEqual(sorted({d for _, d in self.calls}), ["20210106", "20210107"])
        key = "institutional-backfill/twse-t86/20210107.json"
        body = self.store.get(key)
        self.assertEqual(self.store.get(key + ".sha256").decode("ascii").split(),
                         [hashlib.sha256(body).hexdigest(), "20210107.json"])
        self.assertEqual(ib.progress(self.root, self.store, days_per_run=2)["markets"]["listed"]["reached"], "2021-01-06")
        self.calls.clear()
        second = self.run_fetch(days_per_run=2)
        self.assertEqual(sorted({d for _, d in self.calls}), ["20210104", "20210105"])
        self.assertEqual(second["saved"], {"listed": 2, "otc": 2})
        self.calls.clear()
        self.assertEqual(self.run_fetch(days_per_run=2)["planned_days"], 0)
        self.assertEqual(self.calls, [])
        self.assertTrue(ib.progress(self.root, self.store)["finished"])

    def test_requests_to_the_same_site_are_spaced(self):
        self.run_fetch(days_per_run=2)
        self.assertEqual(self.calls, [("listed", "20210107"), ("otc", "20210107"), ("listed", "20210106"), ("otc", "20210106")])
        # 證交所、櫃買輪流打：輪到證交所時已過 0 秒→等滿 10 秒；接著櫃買離上次已 10 秒→不用再等
        self.assertEqual(self.clock.sleeps, [ib.HOST_INTERVAL_SECONDS])

    def test_no_data_day_is_recorded_and_never_retried(self):
        result = self.run_fetch(overrides={("listed", "20210105"): LISTED_EMPTY, ("otc", "20210105"): OTC_EMPTY})
        self.assertEqual(result["no_data"], {"listed": 1, "otc": 1})
        gaps = json.loads(self.store.get(ib.GAPS_KEY))
        self.assertIn("20210105", gaps["listed"])
        self.assertIn("20210105", gaps["otc"])
        self.calls.clear()
        self.assertEqual(self.run_fetch()["planned_days"], 0)
        report = ib.progress(self.root, self.store)
        self.assertTrue(report["finished"])
        self.assertEqual(report["markets"]["listed"]["no_data"], 1)
        self.assertIn("已補完", ib.progress_text(report))

    def test_throttle_backs_off_then_continues(self):
        result = self.run_fetch(days_per_run=1, overrides={("listed", "20210107"): [Response(status=429), listed_ok("20210107")]})
        self.assertIsNone(result["stopped"])
        self.assertEqual(result["saved"], {"listed": 1, "otc": 1})
        self.assertIn(ib.BACKOFF_SECONDS[0], self.clock.sleeps)

    def test_persistent_throttle_stops_cleanly_and_the_next_run_resumes(self):
        html = Response(content="<html>請求過於頻繁</html>".encode("utf-8"), content_type="text/html")
        result = self.run_fetch(overrides={("listed", "20210106"): html})
        self.assertEqual((result["stopped"]["kind"], result["stopped"]["date"]), ("throttled", "2021-01-06"))
        self.assertEqual(self.calls.count(("listed", "20210106")), len(ib.BACKOFF_SECONDS) + 1)
        self.assertEqual([b for b in self.clock.sleeps if b in ib.BACKOFF_SECONDS], list(ib.BACKOFF_SECONDS))
        self.assertIsNone(self.store.get("institutional-backfill/twse-t86/20210106.json"))
        self.assertIsNotNone(self.store.get("institutional-backfill/twse-t86/20210107.json"))
        self.calls.clear()
        self.run_fetch()
        self.assertNotIn(("listed", "20210107"), self.calls)
        self.assertTrue(ib.progress(self.root, self.store)["finished"])

    def test_format_change_stops_with_a_reason_for_an_engineer(self):
        bad = Response(content=t86_json(day="20210107", fields=["證券代號", "證券名稱"], rows=[["2330", "台積電"]]))
        result = self.run_fetch(overrides={("listed", "20210107"): bad})
        self.assertEqual(result["stopped"]["kind"], "format")
        self.assertEqual(self.store.keys(ib.ARCHIVE_PREFIX), [])

    def test_time_budget_stops_before_github_kills_the_job(self):
        result = self.run_fetch(max_seconds=ib.HOST_INTERVAL_SECONDS)
        self.assertEqual(result["stopped"]["kind"], "time")
        self.assertEqual(result["saved"], {"listed": 2, "otc": 2})

    def test_dry_run_fetches_a_sample_and_writes_nothing(self):
        result = self.run_fetch(dry_run=True)
        self.assertEqual([c["date"] for c in result["checked"]], ["2021-01-07", "2021-01-07", "2021-01-06", "2021-01-06"])
        self.assertEqual(self.store.keys(""), [])
        self.assertIn("取消", ib.progress_text(ib.progress(self.root, self.store), dry_run=True))

    def test_merge_appends_archived_days_like_the_daily_path_and_is_idempotent(self):
        self.run_fetch()
        merged = ib.merge(self.root, self.store)
        self.assertEqual(merged, {"listed": 4, "otc": 4})
        self.assertEqual(li.institutional_days(self.root / ib.MARKETS["listed"]["db"]),
                         ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08"])
        self.assertEqual(omd.institutional_days(self.root / ib.MARKETS["otc"]["db"])[0], "2021-01-04")
        got = li.institutional(self.root / ib.MARKETS["listed"]["db"], "2330", ["2021-01-05"])[0]
        self.assertEqual(got, {"foreign": 1_000, "trust": 500, "dealer": -200, "total": 1_300})
        self.assertEqual(ib.merge(self.root, self.store), {"listed": 0, "otc": 0})
        connection = sqlite3.connect(self.root / ib.MARKETS["listed"]["db"])
        sha = connection.execute("SELECT sha256 FROM source_files WHERE raw_file = 'raw/20210105.json'").fetchone()[0]
        connection.close()
        self.assertEqual(sha, hashlib.sha256(self.store.get("institutional-backfill/twse-t86/20210105.json")).hexdigest())

    def test_merge_rejects_an_archive_whose_sha_does_not_match(self):
        self.run_fetch(days_per_run=1)
        self.store.put("institutional-backfill/tpex-institutional/20210107.csv", inst_csv(day_title=roc_title("20210107"), rows=[]))
        with self.assertRaises(state_sync.StateIntegrityError):
            ib.merge(self.root, self.store)
        self.assertEqual(omd.institutional_days(self.root / ib.MARKETS["otc"]["db"]), ["2021-01-08"])

    def test_progress_text_says_how_far_and_how_many_more_presses(self):
        self.run_fetch(days_per_run=1)
        text = ib.progress_text(ib.progress(self.root, self.store, days_per_run=1))
        self.assertIn("上市：已補回到 2021-01-07（1／4 個交易日），還剩約 3 個交易日", text)
        self.assertIn("約再按 3 次", text)


class WorkflowTest(unittest.TestCase):
    def doc(self):
        return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def test_manual_only_and_dry_by_default(self):
        triggers = self.doc()[True]
        self.assertEqual(list(triggers), ["workflow_dispatch"])
        inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(list(inputs), ["dry_run", "days", "start"])
        self.assertEqual((inputs["dry_run"]["type"], inputs["dry_run"]["default"]), ("boolean", True))
        self.assertEqual((inputs["days"]["default"], inputs["start"]["default"]), (str(ib.DAYS_PER_RUN), ib.DEFAULT_START))

    def test_fetch_has_its_own_lock_and_merge_shares_the_working_set_lock(self):
        jobs = self.doc()["jobs"]
        self.assertEqual(jobs["fetch"]["concurrency"], {"group": "institutional-backfill", "cancel-in-progress": False})
        self.assertEqual(jobs["merge"]["concurrency"]["group"], "market-state-write")
        self.assertEqual(yaml.safe_load((ROOT / ".github/workflows/daily-update.yml").read_text(encoding="utf-8"))
                         ["concurrency"]["group"], "market-state-write")
        self.assertEqual(jobs["merge"]["needs"], "fetch")
        self.assertIn("inputs.dry_run == false", jobs["merge"]["if"])
        self.assertLess(ib.MAX_MINUTES, jobs["fetch"]["timeout-minutes"])
        self.assertLessEqual(jobs["fetch"]["timeout-minutes"], 360)

    def test_fetch_restores_only_what_it_reads_and_merge_uploads_on_the_same_data_date(self):
        jobs = self.doc()["jobs"]
        restore = next(s for s in jobs["fetch"]["steps"] if s.get("name", "").startswith("從 R2 還原兩個法人庫"))["run"]
        for path in ib.WORKING_SET_PATHS:
            self.assertIn(f"--only {path}", restore)
        fetch_step = next(s for s in jobs["fetch"]["steps"] if s.get("name") == "抓官方原件存進 R2")["run"]
        self.assertIn('if [ "$INPUT_DRY_RUN" != "false" ]; then dry="--dry-run"; fi', fetch_step)
        self.assertIn("state_sync.py upload", "\n".join(s.get("run", "") for s in jobs["merge"]["steps"]))
        upload = next(s for s in jobs["merge"]["steps"] if s.get("name", "").startswith("同步工作集回 R2"))["run"]
        self.assertIn('--data-date "${{ steps.restore.outputs.date }}" --cursor-domain market', upload)
        self.assertNotIn("upload", "\n".join(s.get("run", "") for s in jobs["fetch"]["steps"]))
        for job in jobs.values():
            for step in job["steps"]:
                if "| tee" in step.get("run", ""):
                    self.assertIn("set -o pipefail", step["run"])


if __name__ == "__main__":
    unittest.main()
