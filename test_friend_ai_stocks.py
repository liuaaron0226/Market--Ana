"""開放給朋友 03 票：朋友的個股判斷用他自己的自選股與庫存。

鎖住四件事：整理輸入時可以指定任何一位使用者；送出的欄位和站主那條線逐欄一致；
A 的持股不會出現在 B 的提示或解讀結果裡；自選股和庫存都空的朋友不呼叫個股判斷，當天標「沒有持股」、不算失敗。
"""
import http.server
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from ai_digest_inputs import build_digest_input, build_friend_input, with_friend_portfolio
from friend_ai_digest import GeminiHttpError, friend_sql, main as friend_main, run_friends
from test_friend_ai_digest import (
    DATE, MASTER, MIGRATIONS, FakeGemini, encrypt_like_site, fake_decrypt, gemini_reply, write_market_fixture,
)

AMY, BOB, CAT, OWNER = "amy@example.com", "bob@example.com", "cat@example.com", "owner@example.com"
KEYS = [{"email": email, "ciphertext": "c", "iv": "i", "alg": "AES-256-GCM", "master_key_id": "x"} for email in (AMY, BOB, CAT)]

# 站主那條線送出的欄位（ai_digest_inputs._portfolio_payload＋_holding_metrics）。站主那邊改欄位時這裡要跟著紅燈。
WATCH_FIELDS = {"code", "name", "market", "horizon"}
HOLDING_FIELDS = {"code", "name", "market", "shares", "average_cost", "last_price", "price_as_of",
                  "latest_price", "market_value", "cost_basis", "unrealized_pnl", "return_pct"}


def write_packs(root):
    stock_pack = {"analysis_schema": "stock-analysis/v2", "as_of": DATE, "results": {
        code: {"schema": "stock-analysis/v2", "code": code, "name": name, "as_of": DATE,
               "quote": {"close": close, "chg_pct": 1.5}, "trend_classification": {"status": "up"},
               "levels": {"support": close - 20, "resistance": close + 20}, "defense": {"status": "ok"},
               "indicators": {"ma20": close - 50}, "qa": {"blocking": []}}
        for code, name, close in (("2330", "台積電", 1000), ("2317", "鴻海", 210), ("2454", "聯發科", 1300))
    }}
    compare_pack = {"schema": "compare-0050-pack/v1", "data_date": DATE,
                    "benchmark": json.dumps({"code": "0050", "periods": [{"period": "1m", "return_pct": 2.2}]}),
                    "items": {"2330": json.dumps({"code": "2330", "periods": [{"period": "1m", "return_pct": 4.4}]}),
                              "2317": json.dumps({"code": "2317", "periods": [{"period": "1m", "return_pct": -3.3}]})}}
    (root / "stock.json").write_text(json.dumps(stock_pack, ensure_ascii=False), encoding="utf-8")
    (root / "compare.json").write_text(json.dumps(compare_pack, ensure_ascii=False), encoding="utf-8")
    return stock_pack, compare_pack


def write_portfolios(root):
    """D1 匯出的形狀（wrangler d1 execute --json）：amy、bob 各有不同的持股，cat 什麼都沒有，另有站主的列。"""
    rows = [
        {"kind": "watchlist", "email": AMY, "code": "2330", "name": "台積電", "market": "上市", "horizon": "long"},
        {"kind": "holding", "email": AMY, "code": "2330", "name": "台積電", "market": "上市",
         "shares": 1000, "average_cost": 901.5, "last_price": 990, "price_as_of": DATE},
        {"kind": "watchlist", "email": BOB, "code": "2317", "name": "鴻海", "market": "上市", "horizon": "short"},
        {"kind": "holding", "email": BOB, "code": "2317", "name": "鴻海", "market": "上市",
         "shares": 7777, "average_cost": 188.25, "last_price": 205, "price_as_of": DATE},
        {"kind": "holding", "email": OWNER, "code": "2454", "name": "聯發科", "market": "上市",
         "shares": 333, "average_cost": 1111.5, "last_price": 1290, "price_as_of": DATE},
    ]
    path = root / "portfolios.json"
    path.write_text(json.dumps([{"results": rows, "success": True}], ensure_ascii=False), encoding="utf-8")
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = Path(self.tmp.name)
        self.market, self.screens = write_market_fixture(root)
        self.stock_pack, self.compare_pack = write_packs(root)
        self.portfolios = write_portfolios(root)
        self.base = build_friend_input(data_date=DATE, market_dir=self.market, screens_root=self.screens)

    def tearDown(self):
        self.tmp.cleanup()

    def friend(self, email):
        return with_friend_portfolio(self.base, email=email, portfolio_path=self.portfolios,
                                     stock_pack=self.stock_pack, compare_pack=self.compare_pack)


class FriendStockInputTests(Fixture):
    def test_any_user_can_be_named_and_only_their_own_rows_are_taken(self):
        amy = self.friend(AMY)
        self.assertEqual(amy["portfolio_codes"], ["2330"])
        self.assertEqual(list(amy["stocks"]), ["2330"])
        self.assertEqual(list(amy["compare_0050"]["stocks"]), ["2330"])
        self.assertTrue(amy["availability"]["stocks"])
        text = json.dumps(amy, ensure_ascii=False)
        for other in ("2317", "鴻海", "7777", "188.25", "2454", "聯發科", "1111.5", "@"):
            self.assertNotIn(other, text)
        self.assertEqual(self.friend(BOB)["portfolio_codes"], ["2317"])

    def test_fields_match_the_owner_line_exactly(self):
        amy = self.friend(AMY)
        self.assertEqual({key for row in amy["portfolio"]["watchlist"] for key in row}, WATCH_FIELDS)
        self.assertEqual({key for row in amy["portfolio"]["holdings"] for key in row}, HOLDING_FIELDS)
        holding = amy["portfolio"]["holdings"][0]
        self.assertEqual((holding["latest_price"], holding["market_value"], holding["cost_basis"]), (1000.0, 1000000.0, 901500.0))
        self.assertEqual((holding["unrealized_pnl"], holding["return_pct"]), (98500.0, 10.93))
        # 同一份資料、同一位使用者：站主那條線整理出來的持股段落與這裡逐欄相同。
        owner_line = build_digest_input(
            data_date=DATE, market_dir=self.market, screens_root=self.screens,
            stock_pack_path=self.root / "stock.json", compare_pack_path=self.root / "compare.json",
            portfolio_path=self.portfolios, owner_email=AMY)
        for section in ("portfolio_codes", "portfolio", "stocks", "compare_0050"):
            self.assertEqual(amy[section], owner_line[section], section)
        self.assertEqual(amy["allowed_codes"], owner_line["allowed_codes"])

    def test_market_part_stays_exchange_only(self):
        amy = self.friend(AMY)
        self.assertEqual(amy["market"], self.base["market"])
        self.assertEqual(amy["flows"], self.base["flows"])
        self.assertNotIn("futures", amy["market"])
        self.assertNotEqual(amy["input_sha256"], self.base["input_sha256"])
        self.assertRegex(amy["input_sha256"], r"^[0-9a-f]{64}$")

    def test_friend_with_nothing_gets_the_shared_market_input_unchanged(self):
        self.assertEqual(self.friend(CAT), self.base)

    def test_stock_pack_from_another_day_marks_stocks_not_ready(self):
        stale = {**self.stock_pack, "as_of": "2026-11-07"}
        amy = with_friend_portfolio(self.base, email=AMY, portfolio_path=self.portfolios, stock_pack=stale,
                                    compare_pack=self.compare_pack)
        self.assertFalse(amy["availability"]["stocks"])


def holder_structured(payload):
    """照這位朋友的輸入寫一份合格的回覆：每檔持股一筆。"""
    point = {"text": "加權指數收紅", "codes": [], "refs": ["/market/latest/close"]}
    signal = {"text": "外資買超集中", "codes": ["2330"], "refs": ["/flows/screens/0/items/0/metric_lots"]}
    held = [row["code"] for row in (payload.get("portfolio") or {}).get("holdings", [])]

    def reason_ref(code):
        return f"/portfolio/holdings/{held.index(code)}/average_cost" if code in held else f"/stocks/{code}/levels/support"
    stocks = [{"code": code, "name": payload["stocks"][code]["name"], "status": "站上支撐",
               "status_refs": [f"/stocks/{code}/quote/close"], "action": "續抱",
               "reason": "成本有緩衝", "reason_refs": [reason_ref(code)]}
              for code in payload["portfolio_codes"]]
    return {"data_date": DATE,
            "market": {"stance": "偏多", "conclusion": "指數站穩", "conclusion_refs": ["/market/latest/close"],
                       "points": [point, point, point]},
            "flows": {"signals": [signal, signal, signal]}, "stocks": stocks}


def market_only_structured():
    structured = holder_structured({"portfolio_codes": [], "stocks": {}})
    del structured["stocks"]
    return structured


class EchoGemini(FakeGemini):
    """回覆依提示內容決定：有持股就照他的持股寫，沒有就只寫大盤與籌碼。"""

    def __init__(self, payloads, override=None):
        super().__init__({})
        self.payloads = payloads
        self.override = override or {}

    def __call__(self, url, body, api_key):
        self.calls.append({"url": url, "body": body, "api_key": api_key})
        email = api_key.removeprefix("KEY-") + "@example.com"
        if email in self.override:
            reply = self.override[email]
            if isinstance(reply, Exception):
                raise reply
            return gemini_reply(reply)
        payload = self.payloads[email]
        return gemini_reply(holder_structured(payload) if payload.get("portfolio_codes") else market_only_structured())


def rows_by(result, email):
    return {row["digest_type"]: row for row in result["rows"] if row["email"] == email}


class FriendStockRunTests(Fixture):
    def run_all(self, override=None, keys=KEYS):
        payloads = {email: self.friend(email) for email in (AMY, BOB, CAT)}
        self.gemini = EchoGemini(payloads, override)
        return run_friends(self.base, keys, decrypt=fake_decrypt, post=self.gemini,
                           payload_for=lambda email: payloads[email], now=lambda: "2026-11-10T10:00:00+00:00")

    def prompt_of(self, email):
        key = "KEY-" + email.split("@")[0]
        return next(call["body"]["contents"][0]["parts"][0]["text"] for call in self.gemini.calls if call["api_key"] == key)

    def test_each_friend_gets_a_stock_judgment_of_his_own_holdings(self):
        result = self.run_all()
        amy = rows_by(result, AMY)
        self.assertEqual(set(amy), {"market", "flow", "stock"})
        self.assertEqual(amy["stock"]["status"], "generated")
        items = amy["stock"]["content"]["items"]
        self.assertEqual([item["code"] for item in items], ["2330"])
        self.assertEqual(items[0]["generation_status"], "generated")
        self.assertEqual(items[0]["action"], "續抱")
        self.assertIn("平均成本 901.5 元", items[0]["reason"])
        self.assertEqual(result["keys"][AMY], {"outcome": "success", "reason": None})
        schema = self.gemini.calls[0]["body"]["generationConfig"]["responseJsonSchema"]
        self.assertIn("stocks", schema["required"])

    def test_one_friends_holdings_never_reach_another_friends_prompt(self):
        self.run_all()
        amy, bob = self.prompt_of(AMY), self.prompt_of(BOB)
        self.assertIn("901.5", amy)
        self.assertIn("7777", bob)
        for leaked in ("2317", "鴻海", "7777", "188.25"):
            self.assertNotIn(leaked, amy)
        for leaked in ("901.5", "台積電\",\"shares", "2454", "1111.5"):
            self.assertNotIn(leaked, bob)
        for prompt in (amy, bob, self.prompt_of(CAT)):
            self.assertNotIn("@", prompt)
            self.assertNotIn("聯發科", prompt)  # 站主的持股不會進任何朋友的提示

    def test_a_reply_naming_another_friends_stock_is_rejected_not_shown(self):
        bob_payload = self.friend(BOB)
        sneaky = holder_structured(bob_payload)
        amy_item = {**sneaky["stocks"][0], "code": "2330", "name": "台積電", "status_refs": ["/stocks/2317/quote/close"],
                    "reason_refs": ["/portfolio/holdings/0/average_cost"]}
        sneaky["stocks"] = [amy_item, *sneaky["stocks"]]
        result = self.run_all(override={BOB: sneaky})
        stock = rows_by(result, BOB)["stock"]
        self.assertEqual([item["code"] for item in stock["content"]["items"]], ["2317"])
        self.assertNotIn("2330", json.dumps(stock["content"], ensure_ascii=False))
        self.assertNotIn("台積電", json.dumps([row["content"] for row in result["rows"] if row["email"] == BOB], ensure_ascii=False))

    def test_friend_with_no_watchlist_or_holdings_is_not_asked_and_is_not_a_failure(self):
        result = self.run_all()
        cat = rows_by(result, CAT)
        self.assertEqual((cat["stock"]["status"], cat["stock"]["content"], cat["stock"]["error_code"]), ("no_holdings", None, None))
        self.assertEqual({cat["market"]["status"], cat["flow"]["status"]}, {"generated"})
        self.assertEqual(result["keys"][CAT], {"outcome": "success", "reason": None})
        cat_calls = [call for call in self.gemini.calls if call["api_key"] == "KEY-cat"]
        self.assertEqual(len(cat_calls), 1)
        self.assertNotIn("stocks", cat_calls[0]["body"]["generationConfig"]["responseJsonSchema"]["properties"])

    def test_key_failure_marks_the_stock_judgment_unproduced_too(self):
        result = self.run_all(override={AMY: GeminiHttpError(429, "RESOURCE_EXHAUSTED")})
        amy = rows_by(result, AMY)
        self.assertEqual({(row["status"], row["error_code"]) for row in amy.values()}, {("unproduced", "quota")})
        self.assertIsNone(amy["stock"]["content"])
        self.assertEqual(rows_by(result, BOB)["stock"]["status"], "generated")

    def test_key_that_cannot_be_decrypted_keeps_no_holdings_for_empty_friend(self):
        payloads = {email: self.friend(email) for email in (AMY, CAT)}

        def broken(row):
            raise ValueError("bad tag")
        result = run_friends(self.base, [KEYS[0], KEYS[2]], decrypt=broken, post=EchoGemini(payloads),
                             payload_for=lambda email: payloads[email])
        self.assertEqual((rows_by(result, AMY)["stock"]["status"], rows_by(result, AMY)["stock"]["error_code"]),
                         ("unproduced", "key_decrypt_failed"))
        self.assertEqual(rows_by(result, CAT)["stock"]["status"], "no_holdings")

    def test_some_stocks_failing_validation_is_partial(self):
        two = with_friend_portfolio(self.base, email=AMY, portfolio_path=self._two_stock_portfolio(),
                                    stock_pack=self.stock_pack, compare_pack=self.compare_pack)
        self.assertEqual(two["portfolio_codes"], ["2317", "2330"])
        reply = holder_structured(two)
        reply["stocks"][0]["status"] = "跌了三成"  # 自由文字帶數字：這一檔不給
        gemini = EchoGemini({AMY: two}, override={AMY: reply})
        result = run_friends(self.base, KEYS[:1], decrypt=fake_decrypt, post=gemini, payload_for=lambda email: two)
        stock = rows_by(result, AMY)["stock"]
        self.assertEqual(stock["status"], "partial")
        self.assertEqual([(item["code"], item["generation_status"]) for item in stock["content"]["items"]],
                         [("2317", "unproduced"), ("2330", "generated")])
        self.assertEqual((stock["content"]["count"], stock["content"]["requested"]), (1, 2))
        self.assertEqual(result["keys"][AMY], {"outcome": "failure", "reason": "validation_failed"})

    def _two_stock_portfolio(self):
        path = self.root / "two.json"
        path.write_text(json.dumps({"watchlist": [
            {"email": AMY, "code": "2317", "name": "鴻海", "market": "上市", "horizon": "short"},
            {"email": AMY, "code": "2330", "name": "台積電", "market": "上市", "horizon": "long"}], "holdings": []},
            ensure_ascii=False), encoding="utf-8")
        return path


class FriendStockSqlTests(Fixture):
    def test_stock_judgments_land_in_their_own_table_per_friend(self):
        payloads = {email: self.friend(email) for email in (AMY, BOB, CAT)}
        result = run_friends(self.base, KEYS, decrypt=fake_decrypt, post=EchoGemini(payloads, {BOB: GeminiHttpError(403)}),
                             payload_for=lambda email: payloads[email], now=lambda: "2026-11-10T10:00:00+00:00")
        db = sqlite3.connect(":memory:")
        for name in ("0007_ai_daily.sql", "0014_friend_gemini_keys.sql", "0020_friend_ai_daily.sql", "0022_friend_ai_stock_daily.sql"):
            db.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
        scripts = friend_sql(result)
        self.assertEqual(len(scripts), 3)
        self.assertNotIn(BOB, scripts[0])
        self.assertNotIn("2317", scripts[0])  # amy 那份 SQL 沒有 bob 的持股
        for _ in range(2):  # 同一天重跑：覆蓋，不重複
            for script in scripts:
                db.executescript(script)
        self.assertEqual(db.execute(
            "SELECT email, status, error_code, content_json IS NOT NULL FROM friend_ai_stock_daily ORDER BY email").fetchall(), [
            (AMY, "generated", None, 1), (BOB, "unproduced", "key_revoked", 0), (CAT, "no_holdings", None, 0)])
        content = json.loads(db.execute("SELECT content_json FROM friend_ai_stock_daily WHERE email = ?", (AMY,)).fetchone()[0])
        self.assertEqual(content["items"][0]["code"], "2330")
        self.assertEqual((content["count"], content["requested"]), (1, 1))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM friend_ai_daily").fetchone()[0], 6)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_daily").fetchone()[0], 0)

    def test_table_refuses_content_on_unproduced_and_missing_content_on_generated(self):
        db = sqlite3.connect(":memory:")
        db.executescript((MIGRATIONS / "0022_friend_ai_stock_daily.sql").read_text(encoding="utf-8"))
        insert = ("INSERT INTO friend_ai_stock_daily (email,data_date,content_json,input_sha256,model,status,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?)")
        for content, status in (("{}", "unproduced"), ("{}", "no_holdings"), (None, "generated"), (None, "partial"), (None, "odd")):
            with self.subTest(status=status), self.assertRaises(sqlite3.IntegrityError):
                db.execute(insert, (AMY, DATE, content, "a" * 64, "m", status, "t", "t"))


class FriendStockWorkflowTests(unittest.TestCase):
    """friend-ai-digest.yml：真跑才讀朋友的持股；個股資料包、0050 對照包、持股匯出都交給排程程式；跑完清掉。"""

    def setUp(self):
        import yaml
        source = (Path(__file__).resolve().parent / ".github/workflows/friend-ai-digest.yml").read_text(encoding="utf-8")
        self.steps = yaml.safe_load(source)["jobs"]["digest"]["steps"]

    def running(self, marker):
        return [step for step in self.steps if marker in step.get("run", "")]

    def test_holdings_are_read_only_on_real_runs_and_handed_to_the_run_step(self):
        reads = self.running("portfolios-sql")
        self.assertEqual(len(reads), 1)
        self.assertIn("steps.mode.outputs.dry_run == 'false'", reads[0]["if"])
        self.assertIn("friend-portfolios.json", reads[0]["run"])
        self.assertNotIn("FRIEND_AI_MASTER_KEY", str(reads[0].get("env", {})))
        (real,) = self.running("--keys-json")
        for flag in ("--portfolios-json \"$RUNNER_TEMP/friend-portfolios.json\"", "--stock-pack cloud/.stock-pack.json",
                     "--compare-pack cloud/.compare-pack.json"):
            self.assertIn(flag, real["run"])
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index(self.running("build_compare_pack.py")[0]["name"]), names.index(real["name"]))
        (dry,) = self.running("--dry-run")
        self.assertIn("--stock-pack cloud/.stock-pack.json", dry["run"])
        self.assertNotIn("portfolios", dry["run"])

    def test_holdings_export_is_removed_afterwards(self):
        cleanup = self.steps[-1]
        self.assertEqual(cleanup["if"], "always()")
        self.assertIn("friend-portfolios.json", cleanup["run"])


class EchoGeminiServer(http.server.BaseHTTPRequestHandler):
    """假的 Gemini HTTP 上游：讀提示裡的 INPUT_JSON，照那位朋友自己的輸入寫一份合格的回覆。"""
    seen = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        prompt = body["contents"][0]["parts"][0]["text"]
        EchoGeminiServer.seen.append({"api_key": self.headers.get("x-goog-api-key"), "prompt": prompt, "body": body})
        lines = prompt.splitlines()
        payload = json.loads(lines[lines.index("INPUT_JSON:") + 1])
        structured = holder_structured(payload) if payload.get("portfolio_codes") else market_only_structured()
        data = json.dumps(gemini_reply(structured), ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FriendStockCliTests(Fixture):
    """排程這一步的完整流程：真的解密（Node）＋假的 Gemini 上游＋每位朋友讀自己的持股＋產生寫入 SQL。"""

    def setUp(self):
        super().setUp()
        EchoGeminiServer.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EchoGeminiServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1beta/models/{{model}}:generateContent"
        keys = [{"email": email, "alg": "AES-256-GCM", "master_key_id": "x", **encrypt_like_site(email, f"AIzaFAKE-{email[:3]}")}
                for email in (AMY, BOB, CAT)]
        self.keys_path = self.root / "keys.json"
        self.keys_path.write_text(json.dumps([{"results": keys, "success": True}]), encoding="utf-8")
        self.sql_dir, self.audit_path = self.root / "friend-sql", self.root / "audit.json"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def run_cli(self, *extra, with_portfolios=True):
        args = ["run", "--date", DATE, "--market-dir", str(self.market), "--screens-root", str(self.screens),
                "--stock-pack", str(self.root / "stock.json"), "--compare-pack", str(self.root / "compare.json"),
                "--sql-dir", str(self.sql_dir), "--audit", str(self.audit_path), "--gemini-url", self.url, *extra]
        if with_portfolios:
            args += ["--portfolios-json", str(self.portfolios)]
        previous = os.environ.get("FRIEND_AI_MASTER_KEY")
        os.environ["FRIEND_AI_MASTER_KEY"] = MASTER
        try:
            return friend_main(args)
        finally:
            if previous is None:
                os.environ.pop("FRIEND_AI_MASTER_KEY", None)
            else:
                os.environ["FRIEND_AI_MASTER_KEY"] = previous

    def test_full_run_writes_each_friends_own_stock_judgment(self):
        self.assertEqual(self.run_cli("--keys-json", str(self.keys_path)), 0)
        prompts = {call["api_key"]: call["prompt"] for call in EchoGeminiServer.seen}
        self.assertEqual(sorted(prompts), ["AIzaFAKE-amy", "AIzaFAKE-bob", "AIzaFAKE-cat"])
        self.assertIn("901.5", prompts["AIzaFAKE-amy"])
        self.assertNotIn("7777", prompts["AIzaFAKE-amy"])
        self.assertNotIn("901.5", prompts["AIzaFAKE-bob"])
        for absent in ('"holdings"', '"watchlist"', '"stocks"'):
            self.assertNotIn(absent, prompts["AIzaFAKE-cat"])
        db = sqlite3.connect(":memory:")
        for name in ("0014_friend_gemini_keys.sql", "0020_friend_ai_daily.sql", "0022_friend_ai_stock_daily.sql"):
            db.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
        for path in sorted(self.sql_dir.glob("*.sql")):
            db.executescript(path.read_text(encoding="utf-8"))
        stored = db.execute("SELECT email, status, content_json FROM friend_ai_stock_daily ORDER BY email").fetchall()
        self.assertEqual([(email, status) for email, status, _ in stored],
                         [(AMY, "generated"), (BOB, "generated"), (CAT, "no_holdings")])
        self.assertEqual([item["code"] for item in json.loads(stored[0][2])["items"]], ["2330"])
        self.assertEqual([item["code"] for item in json.loads(stored[1][2])["items"]], ["2317"])
        for path in (*self.sql_dir.glob("*.sql"), self.audit_path):
            self.assertNotIn("AIzaFAKE", path.read_text(encoding="utf-8"))

    def test_real_run_refuses_without_the_portfolio_export(self):
        # 少了持股匯出時不能把有持股的朋友誤標成「沒有持股」：直接拒跑。
        with self.assertRaises(SystemExit):
            self.run_cli("--keys-json", str(self.keys_path), with_portfolios=False)
        self.assertEqual(EchoGeminiServer.seen, [])

    def test_dry_run_checks_the_stock_packs_without_reading_anyones_holdings(self):
        self.assertEqual(self.run_cli("--dry-run", with_portfolios=False), 0)
        audit = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertTrue(audit["dry_run"])
        self.assertTrue(audit["stock_packs"]["stocks"])
        self.assertEqual(EchoGeminiServer.seen, [])


if __name__ == "__main__":
    unittest.main()
