"""朋友 AI 解讀：每日更新發布之後，朋友解讀排程要接得上（2026-11-19 查到的三個斷點）。

2026-11-16 開啟到 11-19，friend_ai_daily 一列都沒有。Actions 紀錄：凡是通過核准的班次（11-17 兩次、11-18 兩次、11-19 一次）
都死在同一步——``build_compare_pack.py --universe data/liquid-universe/<當天>.json`` 找不到檔，還沒走到 Gemini。
當天的股票池快照只寫在 runner 的暫存目錄，沒有放進工作集（和 11-19 修的選股快照同一類）。
不跑 workflow、不連網。
"""
import json
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

import daily_update as du
import state_sync

ROOT = Path(__file__).resolve().parent
DAY = "2026-11-19"
FRIEND_WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/friend-ai-digest.yml").read_text(encoding="utf-8"))
OWNER_WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ai-digest.yml").read_text(encoding="utf-8"))


def universe_argument(workflow) -> str:
    """排程交給 build_compare_pack.py 的股票池快照路徑（含 $day）；有備援時是第一個。"""
    commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["digest"]["steps"])
    return re.search(r'"(data/[^"]*\$day\.json)"', commands).group(1)


class PublishedDayReachesTheDigestTests(unittest.TestCase):
    """發布那一班跑完，工作集裡就要有解讀排程開頭要讀的那個檔。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / ".tmp" / "daily-update" / DAY
        self.universe = {"data_date": DAY, "members": [{"code": "2330"}, {"code": "6669"}]}

        def fake_sh(args, cwd=du.ROOT):
            args = [str(arg) for arg in args]
            out = Path(args[args.index("--snapshot-root") + 1])
            if "liquid_universe.py" in args:
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{DAY}.json").write_text(json.dumps(self.universe), encoding="utf-8")
            elif "market_screens_publish.py" in args:
                (out / "universe").mkdir(parents=True, exist_ok=True)
                (out / "universe" / f"{DAY}.json").write_text(json.dumps({"result": self.universe}), encoding="utf-8")
            return {"cmd": " ".join(args[:3])}

        for name, value in (("_sh", fake_sh), ("SCREEN_SNAPSHOT_KEEP", self.root / "data/market-screen-snapshots"),
                            ("UNIVERSE_SNAPSHOT_KEEP", self.root / "data/liquid-universe")):
            patcher = unittest.mock.patch.object(du, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, dry_run: bool = False) -> dict:
        ctx = du.RunContext(DAY, dry_run, self.work)
        steps = {step.name: step for step in du.real_steps()}
        return {name: steps[name].fn(ctx) for name in ("build_universe", "publish_snapshots")}

    def test_the_universe_file_both_digest_schedules_open_first_is_in_the_working_set(self):
        self.publish()
        for workflow in (FRIEND_WORKFLOW, OWNER_WORKFLOW):
            path = universe_argument(workflow)
            # 排程每次都是新機器：只有工作集裡的資料夾會從 R2 還原回來。
            self.assertIn(Path(path).parent.as_posix(), {item.path for item in state_sync.WORKING_SET})
            restored = self.root / path.replace("$day", DAY)
            self.assertTrue(restored.is_file(), f"發布之後工作集裡沒有 {path}，解讀排程第一步就會 FileNotFoundError")
            self.assertEqual(json.loads(restored.read_text(encoding="utf-8")), self.universe)

    def test_a_dry_run_leaves_the_working_set_alone(self):
        self.publish(dry_run=True)
        self.assertFalse((self.root / "data/liquid-universe").exists())


# ---- Google 回 503 或逾時：退到備援模型，逐次留紀錄 ----

import friend_ai_digest as fad  # noqa: E402
import gemini_models  # noqa: E402
from ai_digest_trigger import authorize_publication, build_publication_evidence  # noqa: E402
from ai_digest_trigger import main as trigger_main  # noqa: E402
from test_friend_ai_digest import KEYS, FakeGemini, fake_decrypt, friend_payload, gemini_reply, rows_by, valid_structured  # noqa: E402

AMY = "amy@example.com"
BUSY = fad.GeminiHttpError(503, "UNAVAILABLE")


class BusyGoogleTests(unittest.TestCase):
    def run_with(self, replies):
        self.gemini = FakeGemini({"KEY-amy": replies})
        self.slept = []
        return fad.run_friends(friend_payload(), KEYS[:1], decrypt=fake_decrypt, post=self.gemini,
                               now=lambda: "2026-11-19T10:00:00+00:00", sleep=self.slept.append)

    def models_called(self):
        return [re.search(r"/models/([^:]+):", call["url"]).group(1) for call in self.gemini.calls]

    def test_main_model_busy_falls_back_to_the_latest_alias_and_the_digest_is_produced(self):
        result = self.run_with([BUSY] * fad.GEMINI_ATTEMPTS + [gemini_reply(valid_structured())])
        primary, fallback = gemini_models.FRIEND_DIGEST, gemini_models.FRIEND_DIGEST_FALLBACK
        self.assertEqual(fallback, "gemini-flash-latest")   # 存金鑰時試呼叫用的同一個，唯一確定成功過的
        self.assertEqual(self.models_called(), [primary] * fad.GEMINI_ATTEMPTS + [fallback])
        rows = rows_by(result, AMY)
        self.assertEqual({row["status"] for row in rows.values()}, {"generated"})
        self.assertEqual({row["model"] for row in rows.values()}, {fallback}, "記下實際產生的那個模型")
        self.assertEqual(result["keys"][AMY]["outcome"], "success")
        # 主要模型照本人定案思考「高」；備援是別名、不一定收這個設定，所以不帶。
        configs = [call["body"]["generationConfig"] for call in self.gemini.calls]
        self.assertEqual(configs[0]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertNotIn("thinkingConfig", configs[-1])
        self.assertEqual(configs[-1]["responseJsonSchema"], configs[0]["responseJsonSchema"])

    def test_every_try_is_recorded_with_model_seconds_and_http_status(self):
        result = self.run_with([BUSY] * fad.GEMINI_ATTEMPTS + [gemini_reply(valid_structured())])
        tries = result["audit"][0]["tries"]
        self.assertEqual([(item["model"], item["outcome"]) for item in tries],
                         [(gemini_models.FRIEND_DIGEST, "HTTP 503 UNAVAILABLE")] * fad.GEMINI_ATTEMPTS
                         + [(gemini_models.FRIEND_DIGEST_FALLBACK, "ok")])
        self.assertTrue(all(isinstance(item["seconds"], (int, float)) for item in tries))
        lines = fad.try_lines(result)
        self.assertEqual(len(lines), len(tries))
        self.assertRegex(lines[0], r"^friend-01 gemini-3\.8-flash [0-9.]+s HTTP 503 UNAVAILABLE$")
        self.assertNotIn("amy", "\n".join(lines))            # 執行紀錄不放 email
        self.assertNotIn("KEY-amy", json.dumps(result["audit"]))

    def test_a_timeout_goes_straight_to_the_fallback_without_waiting_again(self):
        result = self.run_with([fad.GeminiTimeout("TimeoutError"), gemini_reply(valid_structured())])
        self.assertEqual(self.models_called(), [gemini_models.FRIEND_DIGEST, gemini_models.FRIEND_DIGEST_FALLBACK])
        self.assertEqual(self.slept, [])
        self.assertEqual({row["status"] for row in rows_by(result, AMY).values()}, {"generated"})

    def test_both_models_busy_is_unproduced_as_temporary_so_a_later_slot_retries(self):
        result = self.run_with([BUSY] * 10)
        self.assertEqual(len(self.gemini.calls), fad.GEMINI_ATTEMPTS + fad.FALLBACK_ATTEMPTS)
        self.assertLessEqual(fad.FALLBACK_ATTEMPTS, 2, "有限次：十位朋友也要在排程時限內跑完")
        self.assertEqual({(row["status"], row["error_code"], row["model"]) for row in rows_by(result, AMY).values()},
                         {("unproduced", "gemini_unavailable", gemini_models.FRIEND_DIGEST)})
        self.assertIn("gemini_unavailable", fad.RETRY_LATER_CODES)
        self.assertIn("稍後的班次會再試", fad.FAILURE_TEXT["gemini_unavailable"])

    def test_quota_and_key_problems_never_try_the_fallback(self):
        for failure, code in ((fad.GeminiHttpError(429, "RESOURCE_EXHAUSTED"), "quota"),
                              (fad.GeminiHttpError(403, "PERMISSION_DENIED"), "key_revoked"),
                              (fad.GeminiHttpError(401), "key_invalid")):
            with self.subTest(code=code):
                result = self.run_with([failure, gemini_reply(valid_structured())])
                self.assertEqual(len(self.gemini.calls), 1)
                self.assertEqual(result["keys"][AMY]["reason"], code)

    def test_a_key_problem_found_by_the_fallback_wins_over_the_busy_main_model(self):
        result = self.run_with([BUSY] * fad.GEMINI_ATTEMPTS + [fad.GeminiHttpError(403, "PERMISSION_DENIED")])
        self.assertEqual(result["keys"][AMY]["reason"], "key_revoked")

    def test_fallback_refusing_the_request_keeps_the_main_models_temporary_reason(self):
        result = self.run_with([BUSY] * fad.GEMINI_ATTEMPTS + [fad.GeminiHttpError(400, "INVALID_ARGUMENT")])
        self.assertEqual(result["keys"][AMY]["reason"], "gemini_unavailable")


# ---- 當天稍後的班次補跑 ----

def evidence(result, dry_run=False):
    return build_publication_evidence(result, dry_run=dry_run, run_id="9", head_sha="a" * 40,
                                      repository="o/r", head_branch="main")


def decide(result, *, dry_run=False, **options):
    return authorize_publication(evidence(result, dry_run), expected_run_id="9", expected_head_sha="a" * 40,
                                 expected_repository="o/r", expected_branch="main", conclusion="success", **options)


PUBLISHED = {"data_date": DAY, "status": "success", "published": True, "sync_state": True}
ALREADY_DONE = {"data_date": DAY, "status": "already-done", "published": False, "sync_state": False}


class LaterSlotTests(unittest.TestCase):
    def test_the_publishing_run_is_allowed_as_before(self):
        self.assertEqual(decide(PUBLISHED)["reason"], "published")
        self.assertEqual(decide(PUBLISHED, allow_already_done=True)["reason"], "published")

    def test_a_later_slot_that_found_the_day_already_published_may_fill_in_for_friends_only(self):
        self.assertEqual(decide(ALREADY_DONE), {"allowed": False, "reason": "upstream-not-published", "data_date": "", "head_sha": ""})
        self.assertEqual(decide(ALREADY_DONE, allow_already_done=True),
                         {"allowed": True, "reason": "already-published", "data_date": DAY, "head_sha": "a" * 40})

    def test_code_only_redeploys_waiting_holidays_and_dry_runs_never_start_a_digest(self):
        redeploy = {"mode": "redeploy", "data_date": DAY, "status": "success", "published": False, "sync_state": False}
        for name, result, dry_run in (("redeploy", redeploy, False), ("waiting", {**ALREADY_DONE, "status": "waiting"}, False),
                                      ("skipped", {**ALREADY_DONE, "status": "skipped"}, False),
                                      ("dry-run", ALREADY_DONE, True), ("failed", {**ALREADY_DONE, "status": "failed"}, False)):
            with self.subTest(name):
                self.assertEqual(decide(result, dry_run=dry_run, allow_already_done=True)["reason"], "upstream-not-published")

    def test_the_command_line_passes_the_reason_on_to_the_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "evidence.json", Path(tmp) / "out.txt"
            source.write_text(json.dumps(evidence(ALREADY_DONE)), encoding="utf-8")
            common = ["authorize", "--evidence", str(source), "--expected-run-id", "9", "--expected-head-sha", "a" * 40,
                      "--expected-repository", "o/r", "--expected-branch", "main", "--conclusion", "success",
                      "--github-output", str(output)]
            self.assertEqual(trigger_main([*common, "--allow-already-done"]), 0)
            self.assertIn("allowed=true\nreason=already-published\n", output.read_text(encoding="utf-8"))


def step_named(workflow, job, fragment):
    [step] = [step for step in workflow["jobs"][job]["steps"] if fragment in step.get("name", "")]
    return step


class ScheduleContractTests(unittest.TestCase):
    def test_only_the_friend_schedule_fills_in_on_later_slots(self):
        self.assertIn("--allow-already-done", step_named(FRIEND_WORKFLOW, "authorize", "Fail-closed")["run"])
        self.assertNotIn("--allow-already-done", step_named(OWNER_WORKFLOW, "authorize", "Fail-closed")["run"])
        self.assertEqual(FRIEND_WORKFLOW["jobs"]["authorize"]["outputs"]["reason"], "${{ steps.auth.outputs.reason }}")

    def test_a_fill_in_only_picks_friends_still_missing_that_day(self):
        keys = step_named(FRIEND_WORKFLOW, "digest", "讀取可用的朋友金鑰")
        self.assertIn('[ "$AUTH_REASON" = "already-published" ]', keys["run"])
        self.assertIn('--pending "$INPUT_DATE"', keys["run"])
        self.assertEqual(keys["env"]["AUTH_REASON"], "${{ needs.authorize.outputs.reason }}")

    def test_nothing_heavy_runs_when_no_friend_needs_a_digest(self):
        steps = FRIEND_WORKFLOW["jobs"]["digest"]["steps"]
        names = [step.get("name", "") for step in steps]
        keys_at = next(index for index, name in enumerate(names) if "讀取可用的朋友金鑰" in name)
        restore_at = next(index for index, name in enumerate(names) if "從 R2 還原" in name)
        self.assertLess(keys_at, restore_at, "先看有沒有人要產生，再還原資料")
        for fragment in ("從 R2 還原", "重建 0050 對照資料包", "自選股與庫存", "逐位朋友呼叫 Gemini", "寫入 D1"):
            self.assertIn("steps.keys.outputs.count != '0'", step_named(FRIEND_WORKFLOW, "digest", fragment)["if"], fragment)

    def test_a_schedule_that_breaks_midway_leaves_the_friend_a_reason(self):
        step = step_named(FRIEND_WORKFLOW, "digest", "中途失敗")
        self.assertIn("failure()", step["if"])
        self.assertIn("steps.mode.outputs.dry_run == 'false'", step["if"])
        self.assertIs(step["continue-on-error"], True)
        self.assertIn("failed-run-sql", step["run"])

    def test_the_universe_falls_back_to_the_copy_archived_with_the_screens(self):
        commands = "\n".join(step.get("run", "") for step in FRIEND_WORKFLOW["jobs"]["digest"]["steps"])
        self.assertIn('[ -f "$universe" ] || universe="data/market-screen-snapshots/universe/$day.json"', commands)

    def test_the_tries_reach_the_run_summary(self):
        self.assertIn('"tries"', step_named(FRIEND_WORKFLOW, "digest", "執行摘要")["run"])

    def test_retry_codes_match_between_the_schedule_query_and_the_texts(self):
        source = (ROOT / "cloud/friend-ai-schedule.mjs").read_text(encoding="utf-8")
        [listed] = re.findall(r"RETRY_LATER_CODES = \[([^\]]*)\]", source)
        self.assertEqual(set(re.findall(r'"([a-z_]+)"', listed)), set(fad.RETRY_LATER_CODES))
        for code, text in fad.FAILURE_TEXT.items():
            self.assertEqual("稍後的班次會再試" in text, code in fad.RETRY_LATER_CODES, code)


if __name__ == "__main__":
    unittest.main()
