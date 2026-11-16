import base64
import http.server
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from ai_digest_inputs import build_friend_input
from friend_ai_digest import (
    GeminiHttpError, build_friend_prompt, friend_sql, main as friend_main, run_friends, workflow_policy,
)

MIGRATIONS = Path(__file__).resolve().parent / "cloud" / "migrations"

DATE = "2026-11-10"


def friend_payload():
    return {
        "schema": "ai-digest-friend-input/v1", "data_date": DATE, "input_sha256": "b" * 64,
        "availability": {"market": True, "flow": True},
        "allowed_codes": ["2330"], "portfolio_codes": [],
        "market": {"latest": {"close": 23280, "change_percent": 0.78}, "institutional": [{"foreign": -120.5}]},
        "flows": {"screens": [{"code": "2330", "metric_lots": 12000}], "rotation": [{"score": 55.5}]},
    }


def valid_structured():
    point = {"text": "加權指數收紅", "codes": [], "refs": ["/market/latest/close"]}
    signal = {"text": "外資賣超但籌碼集中", "codes": ["2330"], "refs": ["/flows/screens/0/metric_lots"]}
    return {
        "data_date": DATE,
        "market": {"stance": "偏多", "conclusion": "指數站穩", "conclusion_refs": ["/market/latest/close"],
                   "points": [point, point, point]},
        "flows": {"signals": [signal, signal, signal]},
    }


def gemini_reply(structured):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(structured, ensure_ascii=False)}]}}],
            "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 120, "totalTokenCount": 1020}}


KEYS = [
    {"email": "amy@example.com", "ciphertext": "c1", "iv": "i1", "alg": "AES-256-GCM", "master_key_id": "abcd1234"},
    {"email": "bob@example.com", "ciphertext": "c2", "iv": "i2", "alg": "AES-256-GCM", "master_key_id": "abcd1234"},
]


class FakeGemini:
    """假的 Gemini 上游：依金鑰決定回什麼；記下每次送出的金鑰與提示。"""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, url, body, api_key):
        self.calls.append({"url": url, "body": body, "api_key": api_key})
        reply = self.replies[api_key]
        if isinstance(reply, Exception):
            raise reply
        return reply


def fake_decrypt(row):
    return "KEY-" + row["email"].split("@")[0]


def rows_by(result, email):
    return {row["digest_type"]: row for row in result["rows"] if row["email"] == email}


class FriendDigestTests(unittest.TestCase):
    def run_with(self, replies, keys=KEYS, decrypt=fake_decrypt):
        self.gemini = FakeGemini(replies)
        return run_friends(friend_payload(), keys, decrypt=decrypt, post=self.gemini,
                           now=lambda: "2026-11-10T10:00:00+00:00")

    def test_friend_gets_own_market_and_flow_digest_from_own_key(self):
        result = self.run_with({"KEY-amy": gemini_reply(valid_structured()), "KEY-bob": gemini_reply(valid_structured())})
        amy = rows_by(result, "amy@example.com")
        self.assertEqual(set(amy), {"market", "flow"})
        self.assertEqual(amy["market"]["status"], "generated")
        self.assertIn("收盤 23,280 點", amy["market"]["content"]["conclusion"])
        self.assertEqual(amy["flow"]["status"], "generated")
        self.assertEqual(amy["market"]["input_tokens"], 900)
        self.assertEqual(amy["market"]["total_tokens"], 1020)
        self.assertEqual([call["api_key"] for call in self.gemini.calls], ["KEY-amy", "KEY-bob"])
        self.assertEqual(result["keys"]["amy@example.com"], {"outcome": "success", "reason": None})

    def test_output_failing_validation_is_unproduced_not_guessed(self):
        bad = valid_structured()
        bad["market"]["conclusion"] = "指數漲了三百點"  # 自由文字帶數字
        bad["flows"]["signals"][0]["codes"] = ["9999"]  # 代號不在輸入名單
        result = self.run_with({"KEY-amy": gemini_reply(bad), "KEY-bob": gemini_reply(valid_structured())}, keys=KEYS[:1])
        amy = rows_by(result, "amy@example.com")
        self.assertEqual((amy["market"]["status"], amy["market"]["content"]), ("unproduced", None))
        self.assertEqual(amy["market"]["error_code"], "validation_failed")
        self.assertEqual((amy["flow"]["status"], amy["flow"]["content"]), ("unproduced", None))
        self.assertEqual(result["keys"]["amy@example.com"], {"outcome": "failure", "reason": "validation_failed"})

    def test_reply_with_owner_stock_section_is_rejected_whole(self):
        mixed = {**valid_structured(), "stocks": []}
        result = self.run_with({"KEY-amy": gemini_reply(mixed)}, keys=KEYS[:1])
        amy = rows_by(result, "amy@example.com")
        self.assertEqual({row["error_code"] for row in amy.values()}, {"structured_output_invalid"})

    def test_key_failures_are_classified_and_never_stop_the_next_friend(self):
        cases = [
            (GeminiHttpError(400, "INVALID_ARGUMENT", ("API_KEY_INVALID",)), "key_invalid"),
            (GeminiHttpError(401), "key_invalid"),
            (GeminiHttpError(403, "PERMISSION_DENIED"), "key_revoked"),
            (GeminiHttpError(429, "RESOURCE_EXHAUSTED"), "quota"),
            (GeminiHttpError(400, "FAILED_PRECONDITION"), "region"),
            (GeminiHttpError(503), "gemini_unavailable"),
            (OSError("connection reset"), "gemini_unavailable"),
        ]
        for failure, code in cases:
            with self.subTest(code=code, failure=failure):
                result = self.run_with({"KEY-amy": failure, "KEY-bob": gemini_reply(valid_structured())})
                amy = rows_by(result, "amy@example.com")
                self.assertEqual({(row["status"], row["error_code"]) for row in amy.values()}, {("unproduced", code)})
                self.assertTrue(all(row["content"] is None for row in amy.values()))
                self.assertEqual(result["keys"]["amy@example.com"], {"outcome": "failure", "reason": code})
                bob = rows_by(result, "bob@example.com")
                self.assertEqual({row["status"] for row in bob.values()}, {"generated"})

    def test_key_that_cannot_be_decrypted_is_unproduced_and_others_continue(self):
        def decrypt(row):
            if row["email"] == "amy@example.com":
                raise ValueError("bad tag")
            return fake_decrypt(row)
        result = self.run_with({"KEY-bob": gemini_reply(valid_structured())}, decrypt=decrypt)
        amy = rows_by(result, "amy@example.com")
        self.assertEqual({(row["status"], row["error_code"]) for row in amy.values()}, {("unproduced", "key_decrypt_failed")})
        self.assertEqual([call["api_key"] for call in self.gemini.calls], ["KEY-bob"])
        self.assertEqual({row["status"] for row in rows_by(result, "bob@example.com").values()}, {"generated"})

    def test_unexpected_crash_for_one_friend_does_not_stop_others(self):
        result = self.run_with({"KEY-amy": RuntimeError("boom"), "KEY-bob": gemini_reply(valid_structured())})
        self.assertEqual({row["error_code"] for row in rows_by(result, "amy@example.com").values()}, {"internal_error"})
        self.assertEqual({row["status"] for row in rows_by(result, "bob@example.com").values()}, {"generated"})

    def test_decrypted_key_never_reaches_rows_or_audit(self):
        result = self.run_with({"KEY-amy": GeminiHttpError(403, "PERMISSION_DENIED"), "KEY-bob": gemini_reply(valid_structured())})
        self.assertNotIn("KEY-", json.dumps(result, ensure_ascii=False))


class FriendSqlTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        for name in ("0007_ai_daily.sql", "0014_friend_gemini_keys.sql", "0020_friend_ai_daily.sql"):
            self.db.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
        for row in KEYS:
            self.db.execute(
                "INSERT INTO friend_gemini_keys (email,ciphertext,iv,alg,master_key_id,consent_at,consent_version,status,"
                "last_success_at,last_failure_reason,created_at,updated_at) VALUES (?,?,?,?,?,'t','v','active',NULL,'quota','t','t')",
                (row["email"], row["ciphertext"], row["iv"], row["alg"], row["master_key_id"]))

    def test_each_friend_rows_land_in_friend_table_and_owner_table_is_untouched(self):
        gemini = FakeGemini({"KEY-amy": gemini_reply(valid_structured()), "KEY-bob": GeminiHttpError(429, "RESOURCE_EXHAUSTED")})
        result = run_friends(friend_payload(), KEYS, decrypt=fake_decrypt, post=gemini, now=lambda: "2026-11-10T10:00:00+00:00")
        scripts = friend_sql(result)
        self.assertEqual(len(scripts), 2)  # 每位朋友一份、各自一個交易：一份寫失敗不擋其他人
        self.assertTrue(all(script.startswith("BEGIN;") and script.rstrip().endswith("COMMIT;") for script in scripts))
        self.assertNotIn("bob@example.com", scripts[0])
        self.assertNotIn("amy@example.com", scripts[1])
        for _ in range(2):  # 同一天重跑：覆蓋，不重複
            for script in scripts:
                self.db.executescript(script)
        stored = self.db.execute(
            "SELECT email, digest_type, status, error_code, content_json IS NOT NULL FROM friend_ai_daily ORDER BY email, digest_type").fetchall()
        self.assertEqual(stored, [
            ("amy@example.com", "flow", "generated", None, 1), ("amy@example.com", "market", "generated", None, 1),
            ("bob@example.com", "flow", "unproduced", "quota", 0), ("bob@example.com", "market", "unproduced", "quota", 0),
        ])
        content = json.loads(self.db.execute(
            "SELECT content_json FROM friend_ai_daily WHERE email='amy@example.com' AND digest_type='market'").fetchone()[0])
        self.assertEqual(content["stance"], "偏多")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ai_daily").fetchone()[0], 0)
        keys = self.db.execute("SELECT email, last_success_at, last_failure_reason FROM friend_gemini_keys ORDER BY email").fetchall()
        # 設定頁與後台會原樣顯示最近失敗原因：存中文說明，不存分類碼。
        self.assertEqual(keys, [("amy@example.com", "2026-11-10T10:00:00+00:00", None),
                                ("bob@example.com", None, "今天的免費額度用完了，明天會照常再試")])

    def test_nothing_to_write_when_no_friend_has_a_usable_key(self):
        self.assertEqual(friend_sql({"rows": [], "keys": {}, "audit": []}), [])


def write_js(path, name, value):
    path.write_text(f"window.{name} = {json.dumps(value, ensure_ascii=False)};\n", encoding="utf-8")


def write_market_fixture(root):
    """大盤資料包裡同時有新聞、國外行情與期交所欄位，用來確認朋友的提示一個都不帶。"""
    market = root / "market"
    market.mkdir()
    write_js(market / "analysis-data.js", "MARKET_ANALYSIS", {
        "meta": {"cutoff_date": DATE},
        "daily": [{"date": DATE, "close": 23280, "amount": 4300}],
        "institutional": [{"date": DATE, "foreign": -120.5, "trust": 35.2, "dealer": -4.1}],
        "futures": [{"date": DATE, "foreign_oi_net_contracts": -18888}],
        "sectors": [{"date": DATE, "electronic_change_pct": 1.2}],
        "indicators": {"daily": [{"date": DATE, "ma5": 23150}]},
    })
    write_js(market / "dashboard-data.js", "MARKET_DASHBOARD", {
        "market_date": DATE, "latest": {"close": 23280, "change_percent": 0.78, "nasdaq_close": 19876.5},
        "thresholds": {"upper": 23400, "lower": 23000, "sox_close": 5123.4},
        "news": [{"title": "美股那斯達克大漲", "source": "外電"}], "headline": "費半創高",
        "sources": [{"name": "Yahoo Finance"}],
    })
    write_js(market / "chips-data.js", "MARKET_CHIPS", {
        "margin": [{"date": DATE, "margin_balance_billion": 5340.5}],
        "foreign": [{"date": DATE, "net_billion": -120.5, "trust_billion": 35.2}],
        "futures": [{"date": DATE, "net_contracts": -18888}],
    })
    screens = root / "screens"
    screen_dir = screens / "screen-foreign_net_rank-1d-buy"
    screen_dir.mkdir(parents=True)
    (screen_dir / f"{DATE}.json").write_text(json.dumps({"result": {"display_name": "外資淨買超排行", "items": [
        {"code": "2330", "name": "台積電", "metric_lots": 12000}]}}, ensure_ascii=False), encoding="utf-8")
    (screens / "rotation").mkdir()
    (screens / "rotation" / f"{DATE}.json").write_text(json.dumps({"result": {"snapshots": [
        {"date": DATE, "summary": {"capital_side": "in"}, "efficiency_ranking": [{"name": "半導體", "score": 55.5}]}]}},
        ensure_ascii=False), encoding="utf-8")
    return market, screens


class FriendInputTests(unittest.TestCase):
    """送出的提示只有證交所、櫃買的官方開放資料：不含新聞、國外行情、期交所、任何人的持股。"""

    def test_prompt_only_carries_exchange_open_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            market, screens = write_market_fixture(Path(tmp))
            payload = build_friend_input(data_date=DATE, market_dir=market, screens_root=screens)

        self.assertEqual(set(payload), {"schema", "data_date", "availability", "source_dates", "allowed_codes",
                                        "portfolio_codes", "market", "flows", "input_sha256"})
        self.assertEqual(payload["availability"], {"market": True, "flow": True})
        self.assertEqual(payload["allowed_codes"], ["2330"])
        self.assertEqual(payload["portfolio_codes"], [])
        self.assertEqual(payload["market"]["latest"]["close"], 23280)
        self.assertNotIn("futures", payload["market"])
        self.assertNotIn("futures_chips", payload["market"])
        self.assertRegex(payload["input_sha256"], r"^[0-9a-f]{64}$")
        prompt = build_friend_prompt(payload)
        # 劇本檔 dashboard-data.js 以後多放國外指數也帶不進來：只放列出的欄位。
        self.assertEqual(payload["market"]["latest"], {"close": 23280, "change_percent": 0.78})
        self.assertEqual(payload["market"]["thresholds"], {"upper": 23400, "lower": 23000})
        for banned in ("那斯達克", "外電", "費半", "Yahoo", "foreign_oi", "net_contracts", "holdings", "watchlist", "nasdaq", "sox",
                       "average_cost", "shares", "@"):
            self.assertNotIn(banned, prompt)
        self.assertIn("/market/latest/close", prompt)
        self.assertIn("/flows/screens/0/items/0/metric_lots", prompt)


class WorkflowPolicyTests(unittest.TestCase):
    """朋友這條線自己的開關（GitHub variable MARKET_ANA_FRIEND_AI，只認 on）；手動觸發預設乾跑。"""

    def test_manual_run_defaults_to_dry_run_even_when_switch_is_on(self):
        for dry in (None, "", "true"):
            self.assertEqual(workflow_policy("workflow_dispatch", "on", dry), {"allowed": True, "dry_run": True})

    def test_real_run_needs_switch_exactly_on(self):
        self.assertEqual(workflow_policy("workflow_dispatch", "on", "false"), {"allowed": True, "dry_run": False})
        self.assertEqual(workflow_policy("workflow_run", "on", None), {"allowed": True, "dry_run": False})
        for switch in (None, "", "ON", "true", "1", "owner"):
            self.assertEqual(workflow_policy("workflow_run", switch, None), {"allowed": False, "dry_run": True})
            self.assertEqual(workflow_policy("workflow_dispatch", switch, "false"), {"allowed": False, "dry_run": True})
        self.assertEqual(workflow_policy("schedule", "on", None), {"allowed": False, "dry_run": True})


ROOT = Path(__file__).resolve().parent
MASTER = base64.b64encode(bytes([7]) * 32).decode()
# 和網站一樣的加密方式（AES-256-GCM、AAD＝AAD_PREFIX＋email），AAD_PREFIX 直接讀網站的常數。
ENCRYPT_JS = """
const [master, email, plain] = process.argv.slice(1);
const { AAD_PREFIX } = await import(process.env.FRIEND_AI_MODULE);
const iv = crypto.getRandomValues(new Uint8Array(12));
const key = await crypto.subtle.importKey("raw", Buffer.from(master, "base64"), "AES-GCM", false, ["encrypt"]);
const sealed = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(AAD_PREFIX + email), tagLength: 128 }, key, new TextEncoder().encode(plain));
process.stdout.write(JSON.stringify({ ciphertext: Buffer.from(sealed).toString("base64"), iv: Buffer.from(iv).toString("base64") }));
"""


def encrypt_like_site(email, plain):
    module = (ROOT / "cloud" / "lib" / "friend-ai.js").as_uri()
    out = subprocess.run(["node", "--input-type=module", "-e", ENCRYPT_JS, MASTER, email, plain], capture_output=True,
                         text=True, check=True, env={**os.environ, "FRIEND_AI_MODULE": module})
    return json.loads(out.stdout)


class FakeGeminiServer(http.server.BaseHTTPRequestHandler):
    """假的 Gemini HTTP 上游：x-goog-api-key 決定回成功或 403；記下收到的金鑰、網址與內容。"""
    seen = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        api_key = self.headers.get("x-goog-api-key")
        FakeGeminiServer.seen.append({"path": self.path, "api_key": api_key, "body": body})
        if api_key == "AIzaFAKE-amy":
            structured = valid_structured()
            # 真的輸入裡，籌碼名單的數字在 items 底下。
            for signal in structured["flows"]["signals"]:
                signal["refs"] = ["/flows/screens/0/items/0/metric_lots"]
            status, reply = 200, gemini_reply(structured)
        else:
            status, reply = 403, {"error": {"code": 403, "message": "Permission denied", "status": "PERMISSION_DENIED"}}
        data = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FriendCliTests(unittest.TestCase):
    """排程這一步的完整流程：真的解密（Node）＋假的 Gemini 上游＋產生寫入 SQL。"""

    def setUp(self):
        FakeGeminiServer.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGeminiServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1beta/models/{{model}}:generateContent"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.market, self.screens = write_market_fixture(self.root)
        keys = [{"email": email, "alg": "AES-256-GCM", "master_key_id": "x", **encrypt_like_site(email, plain)}
                for email, plain in (("amy@example.com", "AIzaFAKE-amy"), ("bob@example.com", "AIzaFAKE-bob"))]
        self.keys_path = self.root / "keys.json"
        self.keys_path.write_text(json.dumps([{"results": keys, "success": True}]), encoding="utf-8")
        self.sql_dir, self.audit_path = self.root / "friend-sql", self.root / "audit.json"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def run_cli(self, *extra, master=MASTER):
        args = ["run", "--date", DATE, "--market-dir", str(self.market), "--screens-root", str(self.screens),
                "--keys-json", str(self.keys_path), "--sql-dir", str(self.sql_dir), "--audit", str(self.audit_path),
                "--gemini-url", self.url, *extra]
        previous = os.environ.get("FRIEND_AI_MASTER_KEY")
        os.environ["FRIEND_AI_MASTER_KEY"] = master
        try:
            return friend_main(args)
        finally:
            if previous is None:
                os.environ.pop("FRIEND_AI_MASTER_KEY", None)
            else:
                os.environ["FRIEND_AI_MASTER_KEY"] = previous

    def test_full_run_with_fake_upstream_writes_each_friend_and_keeps_keys_out_of_files(self):
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual([call["api_key"] for call in FakeGeminiServer.seen], ["AIzaFAKE-amy", "AIzaFAKE-bob"])
        self.assertTrue(all("/models/gemini-3.8-flash:generateContent" in call["path"] for call in FakeGeminiServer.seen))
        self.assertTrue(all("AIzaFAKE" not in call["path"] for call in FakeGeminiServer.seen))
        sent = FakeGeminiServer.seen[0]["body"]
        self.assertEqual(sent["generationConfig"]["responseMimeType"], "application/json")
        self.assertNotIn("那斯達克", json.dumps(sent, ensure_ascii=False))
        db = sqlite3.connect(":memory:")
        for name in ("0014_friend_gemini_keys.sql", "0020_friend_ai_daily.sql"):
            db.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
        files = sorted(self.sql_dir.glob("*.sql"))
        self.assertEqual([path.name for path in files], ["friend-01.sql", "friend-02.sql"])  # 檔名不含 email
        for path in files:
            db.executescript(path.read_text(encoding="utf-8"))
        self.assertEqual(db.execute("SELECT email, digest_type, status, error_code FROM friend_ai_daily ORDER BY 1, 2").fetchall(), [
            ("amy@example.com", "flow", "generated", None), ("amy@example.com", "market", "generated", None),
            ("bob@example.com", "flow", "unproduced", "key_revoked"), ("bob@example.com", "market", "unproduced", "key_revoked"),
        ])
        for path in (*files, self.audit_path):
            self.assertNotIn("AIzaFAKE", path.read_text(encoding="utf-8"))

    def test_wrong_master_key_marks_everyone_unproduced_without_calling_gemini(self):
        self.assertEqual(self.run_cli(master=base64.b64encode(bytes([9]) * 32).decode()), 0)
        self.assertEqual(FakeGeminiServer.seen, [])
        written = "".join(path.read_text(encoding="utf-8") for path in self.sql_dir.glob("*.sql"))
        self.assertEqual(written.count("'key_decrypt_failed'"), 4)

    def test_dry_run_only_checks_input_and_never_calls_gemini_or_writes_sql(self):
        prompt_path = self.root / "prompt.txt"
        self.assertEqual(self.run_cli("--dry-run", "--prompt-output", str(prompt_path)), 0)
        self.assertEqual(FakeGeminiServer.seen, [])
        self.assertFalse(self.sql_dir.exists())
        self.assertIn("/market/latest/close", prompt_path.read_text(encoding="utf-8"))
        self.assertTrue(json.loads(self.audit_path.read_text(encoding="utf-8"))["dry_run"])


if __name__ == "__main__":
    unittest.main()
