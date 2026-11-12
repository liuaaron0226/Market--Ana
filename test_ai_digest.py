import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_digest import OUTPUT_SCHEMA, build_prompt, d1_sql, main as digest_main, run_digest


class AiDigestTests(unittest.TestCase):
    def setUp(self):
        self.input_payload = {
            "schema": "ai-digest-input/v1", "data_date": "2026-11-10", "input_sha256": "a" * 64,
            "availability": {"market": True, "flow": True, "stocks": True},
            "allowed_codes": ["2330"],
            "market": {"latest": {"close": 23280, "change_percent": 0.78}, "institutional": [{"foreign": -120.5}]},
            "flows": {"screens": [{"code": "2330", "metric_lots": 12000}], "rotation": [{"score": 55.5}]},
            "portfolio": {"watchlist": [{"code": "2330", "name": "台積電"}], "holdings": []},
            "stocks": {"2330": {"name": "台積電", "quote": {"close": 1000, "chg_pct": 1.5, "volume": 12000}, "levels": {"support": 980}}},
            "compare_0050": {"2330": {"return_pct": 4.4}},
        }

    def test_schema_is_closed_and_requires_three_outputs(self):
        self.assertFalse(OUTPUT_SCHEMA["additionalProperties"])
        self.assertEqual(set(OUTPUT_SCHEMA["required"]), {"data_date", "market", "flows", "stocks"})
        self.assertEqual(OUTPUT_SCHEMA["properties"]["market"]["properties"]["stance"]["enum"], ["偏多", "偏空", "觀望"])
        self.assertIn("conclusion_refs", OUTPUT_SCHEMA["properties"]["market"]["required"])
        self.assertIn("refs", OUTPUT_SCHEMA["$defs"]["evidence"]["required"])
        self.assertFalse(OUTPUT_SCHEMA["$defs"]["evidence"]["additionalProperties"])
        stock_schema = OUTPUT_SCHEMA["properties"]["stocks"]["items"]
        self.assertFalse(stock_schema["additionalProperties"])
        self.assertIn("status_refs", stock_schema["required"])
        self.assertIn("reason_refs", stock_schema["required"])

    def test_prompt_exposes_exact_typed_refs_and_forbids_numbers_in_free_prose(self):
        prompt = build_prompt(self.input_payload)
        self.assertIn("不得出現阿拉伯／全形／中文數字", prompt)
        self.assertIn("/stocks/2330/quote/close", prompt)
        self.assertIn('"label":"收盤"', prompt)
        self.assertIn('"unit":"元"', prompt)

    def test_validates_structured_output_and_records_usage(self):
        structured = self._valid_structured()
        raw = {"status": "SUCCESS", "structured_output": structured,
               "usage": {"input_tokens": 1234, "output_tokens": 321, "total_tokens": 1555}}
        with tempfile.TemporaryDirectory() as tmp:
            agy = self._fake_agy(Path(tmp), raw)
            result = run_digest(self.input_payload, agy_bin=agy, model="gemini-3.8-flash-medium")

        self.assertEqual(result["usage"], {"input_tokens": 1234, "output_tokens": 321, "total_tokens": 1555})
        rows = {(row["digest_type"], row["subject"]): row for row in result["rows"]}
        self.assertEqual(rows[("market", "market")]["status"], "generated")
        self.assertEqual(rows[("flow", "market")]["status"], "generated")
        self.assertEqual(rows[("stock", "2330")]["status"], "generated")
        self.assertIn("收盤 1,000 元", rows[("stock", "2330")]["content"]["status"])
        self.assertIn("漲跌幅 1.5%", rows[("stock", "2330")]["content"]["status"])
        self.assertEqual(rows[("stock", "2330")]["content"]["status_refs"], [
            "/stocks/2330/quote/close", "/stocks/2330/quote/chg_pct",
        ])
        self.assertEqual(rows[("stock", "*")]["content"]["count"], 1)
        self.assertEqual(result["audit"]["rejected"], [])

    def test_rejects_flow_volume_reused_as_stock_close(self):
        structured = self._valid_structured()
        structured["stocks"][0]["status"] = "收盤 12000。"
        structured["stocks"][0]["status_refs"] = ["/flows/screens/0/metric_lots"]
        raw = {"status": "SUCCESS", "structured_output": structured, "usage": {}}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_digest(self.input_payload, agy_bin=self._fake_agy(Path(tmp), raw), model="test-model")

        rows = {(row["digest_type"], row["subject"]): row for row in result["rows"]}
        self.assertEqual(rows[("stock", "2330")]["status"], "unproduced")
        self.assertTrue(any(item["section"] == "stocks" for item in result["audit"]["rejected"]))

    def test_rejects_cross_scope_code_field_date_unit_and_chinese_number_bypasses(self):
        cases = []

        cross_scope = self._valid_structured()
        cross_scope["stocks"][0]["status_refs"] = ["/flows/screens/0/metric_lots"]
        cases.append(("跨 scope", self.input_payload, cross_scope))

        cross_code_payload = json.loads(json.dumps(self.input_payload))
        cross_code_payload["allowed_codes"].append("2317")
        cross_code_payload["stocks"]["2317"] = {
            "name": "鴻海", "quote": {"close": 210, "volume": 88000}, "levels": {"support": 200},
        }
        cross_code = self._valid_structured()
        cross_code["stocks"][0]["status_refs"] = ["/stocks/2317/quote/close"]
        cases.append(("跨股票", cross_code_payload, cross_code))

        wrong_field = self._valid_structured()
        wrong_field["stocks"][0]["status"] = "收盤 12000。"
        wrong_field["stocks"][0]["status_refs"] = ["/stocks/2330/quote/volume"]
        cases.append(("同股不同 field", self.input_payload, wrong_field))

        wrong_date = self._valid_structured()
        wrong_date["stocks"][0]["status"] = "資料日 2026-11-09，股價偏強。"
        cases.append(("日期錯配", self.input_payload, wrong_date))

        chinese_quantity = self._valid_structured()
        chinese_quantity["stocks"][0]["status"] = "收盤一萬二千。"
        chinese_quantity["stocks"][0]["status_refs"] = ["/stocks/2330/quote/volume"]
        cases.append(("中文數量", self.input_payload, chinese_quantity))

        code_in_prose = self._valid_structured()
        code_in_prose["stocks"][0]["status"] = "2330 表現偏強。"
        cases.append(("代號冒價格", self.input_payload, code_in_prose))

        wrong_unit = self._valid_structured()
        wrong_unit["stocks"][0]["status_unit"] = "美元"
        cases.append(("單位錯配", self.input_payload, wrong_unit))

        for label, payload, structured in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                raw = {"status": "SUCCESS", "structured_output": structured, "usage": {}}
                result = run_digest(payload, agy_bin=self._fake_agy(Path(tmp), raw), model="test-model")
                rows = {(row["digest_type"], row["subject"]): row for row in result["rows"]}
                self.assertEqual(rows[("stock", "2330")]["status"], "unproduced")
                self.assertTrue(any(item["section"] == "stocks" for item in result["audit"]["rejected"]))

    def test_correct_volume_ref_is_rendered_with_its_own_label_and_unit(self):
        structured = self._valid_structured()
        structured["stocks"][0]["status"] = "量能值得留意。"
        structured["stocks"][0]["status_refs"] = ["/stocks/2330/quote/volume"]
        raw = {"status": "SUCCESS", "structured_output": structured, "usage": {}}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_digest(self.input_payload, agy_bin=self._fake_agy(Path(tmp), raw), model="test-model")
        row = next(row for row in result["rows"] if row["digest_type"] == "stock" and row["subject"] == "2330")
        self.assertEqual(row["status"], "generated")
        self.assertIn("成交量 12,000 股", row["content"]["status"])
        self.assertNotIn("收盤 12,000", row["content"]["status"])

    def test_discards_unsupported_numbers_and_unknown_codes_without_guessing(self):
        structured = self._valid_structured()
        structured["market"]["stance"] = "偏多"
        structured["market"]["conclusion"] = "偏多：預估上漲 9.9%。"
        structured["flows"]["signals"].append({
            "text": "排行出現未知股票。", "codes": ["9999"],
            "refs": ["/flows/screens/0/metric_lots"],
        })
        structured["stocks"][0]["status"] = "目標 1200。"
        raw = {"status": "SUCCESS", "structured_output": structured, "usage": {"input_tokens": 10, "output_tokens": 20}}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_digest(self.input_payload, agy_bin=self._fake_agy(Path(tmp), raw), model="test-model")

        rows = {(row["digest_type"], row["subject"]): row for row in result["rows"]}
        self.assertEqual(rows[("market", "market")]["status"], "unproduced")
        self.assertEqual(rows[("flow", "market")]["status"], "unproduced")
        self.assertIsNone(rows[("flow", "market")]["content"])
        self.assertEqual(rows[("stock", "2330")]["status"], "unproduced")
        reasons = [item["reason"] for item in result["audit"]["rejected"]]
        self.assertTrue(any("自由文字不得包含數字" in reason for reason in reasons))
        self.assertTrue(any("代號不在輸入名單" in reason for reason in reasons))

    def test_forbidden_generation_status_is_audited_and_response_cannot_override_it(self):
        structured = self._valid_structured()
        structured["generation_status"] = "generated"
        raw = {
            "status": "SUCCESS", "generation_status": "generated",
            "structured_output": structured,
            "response": json.dumps(self._valid_structured(), ensure_ascii=False),
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = run_digest(self.input_payload, agy_bin=self._fake_agy(Path(tmp), raw), model="test-model")
        self.assertTrue(all(row["status"] == "unproduced" for row in result["rows"]))
        self.assertTrue(all(row["error_code"] == "structured_output_invalid" for row in result["rows"]))
        self.assertEqual(result["audit"]["rejected"][0]["section"], "all")
        self.assertIn("允許欄位", result["audit"]["rejected"][0]["reason"])

    def test_cli_failure_marks_every_category_unproduced(self):
        raw = {"status": "ERROR", "error": "quota"}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_digest(self.input_payload, agy_bin=self._fake_agy(Path(tmp), raw, exit_code=1), model="test-model")
        self.assertTrue(all(row["status"] == "unproduced" for row in result["rows"]))
        self.assertTrue(all(row["data_date"] == "2026-11-10" and row["content"] is None for row in result["rows"]))
        self.assertEqual(result["audit"]["failure"], "agy_failed")

    def test_non_utf8_cli_output_fails_closed_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_agy.py"
            script.write_text(
                "import sys\nsys.stdout.buffer.write(bytes([0x7b, 0x22, 0xa6, 0xa8, 0x22, 0x7d]))\n",
                encoding="utf-8",
            )
            result = run_digest(self.input_payload, agy_bin=script, model="test-model")
        self.assertTrue(all(row["status"] == "unproduced" for row in result["rows"]))
        self.assertEqual(result["audit"]["failure"], "agy_failed")

    def test_dry_run_does_not_call_agy_or_write_d1_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "input.json"
            source.write_text(json.dumps(self.input_payload, ensure_ascii=False), encoding="utf-8")
            result_path, audit_path, sql_path = folder / "result.json", folder / "audit.json", folder / "rows.sql"
            self.assertEqual(digest_main([
                "--input", str(source), "--output", str(result_path), "--audit", str(audit_path),
                "--sql-output", str(sql_path), "--dry-run",
            ]), 0)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["rows"], [])
            self.assertFalse(sql_path.exists())
            self.assertTrue(json.loads(audit_path.read_text(encoding="utf-8"))["dry_run"])

    def test_setup_failure_cli_still_emits_unproduced_d1_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "input.json"
            source.write_text(json.dumps(self.input_payload, ensure_ascii=False), encoding="utf-8")
            result_path, audit_path, sql_path = folder / "result.json", folder / "audit.json", folder / "rows.sql"
            self.assertEqual(digest_main([
                "--input", str(source), "--output", str(result_path), "--audit", str(audit_path),
                "--sql-output", str(sql_path), "--mark-failure", "agy_setup_failed",
            ]), 0)
            rows = json.loads(result_path.read_text(encoding="utf-8"))["rows"]
            self.assertTrue(rows and all(row["status"] == "unproduced" for row in rows))
            self.assertEqual(json.loads(audit_path.read_text(encoding="utf-8"))["failure"], "agy_setup_failed")
            self.assertIn("INSERT INTO ai_daily", sql_path.read_text(encoding="utf-8"))

    def test_d1_upsert_sql_is_atomic_idempotent_and_preserves_json(self):
        row = {
            "data_date": "2026-11-10", "digest_type": "market", "subject": "market",
            "content": {"conclusion": "觀望：外資賣超 120.5 億元，台股仍在 O'Reilly 測試字串。"},
            "input_sha256": "a" * 64, "model": "test-model", "input_tokens": 10,
            "output_tokens": 2, "total_tokens": 12, "status": "generated", "error_code": None,
            "created_at": "2026-11-10T09:00:00+00:00",
        }
        connection = sqlite3.connect(":memory:")
        migration = (Path(__file__).parent / "cloud/migrations/0007_ai_daily.sql").read_text(encoding="utf-8")
        connection.executescript(migration)
        connection.executescript(d1_sql([row]))
        connection.executescript(d1_sql([row]))
        stored = connection.execute("SELECT content_json, input_sha256, total_tokens FROM ai_daily").fetchall()
        connection.close()
        self.assertEqual(len(stored), 1)
        self.assertEqual(json.loads(stored[0][0]), row["content"])
        self.assertEqual(stored[0][1:], ("a" * 64, 12))

    @staticmethod
    def _valid_structured():
        return {
            "data_date": "2026-11-10",
            "market": {
                "stance": "觀望",
                "conclusion": "指數與法人方向分歧，維持觀望。",
                "conclusion_refs": ["/market/latest/close", "/market/institutional/0/foreign"],
                "points": [
                    {"text": "指數維持上漲。", "codes": [], "refs": ["/market/latest/change_percent"]},
                    {"text": "外資偏賣。", "codes": [], "refs": ["/market/institutional/0/foreign"]},
                    {"text": "指數收盤維持高檔。", "codes": [], "refs": ["/market/latest/close"]},
                ],
            },
            "flows": {"signals": [
                {"text": "外資買超排行有訊號。", "codes": ["2330"], "refs": ["/flows/screens/0/metric_lots"]},
                {"text": "產業效率值得留意。", "codes": [], "refs": ["/flows/rotation/0/score"]},
                {"text": "籌碼排行出現個股。", "codes": ["2330"], "refs": ["/flows/screens/0/metric_lots"]},
            ]},
            "stocks": [{
                "code": "2330", "name": "台積電", "status": "股價維持強勢。",
                "status_refs": ["/stocks/2330/quote/close", "/stocks/2330/quote/chg_pct"],
                "action": "續抱", "reason": "股價仍在支撐之上。",
                "reason_refs": ["/stocks/2330/levels/support"],
            }],
        }

    @staticmethod
    def _fake_agy(folder: Path, payload, exit_code=0):
        script = folder / "fake_agy.py"
        script.write_text(
            "import json,sys\n"
            "args=sys.argv[1:]\n"
            "assert '--json-schema' in args and '--output-format' in args\n"
            f"sys.stdout.buffer.write(json.dumps({payload!r}, ensure_ascii=False).encode('utf-8'))\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        return script


if __name__ == "__main__":
    unittest.main()
