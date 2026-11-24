"""一次性的資金版圖樣品載入（chain_map_bootstrap.py，.scratch/ai-chain-map 票 01）：產生的 SQL 實際套進 0039 的表。

用法：python -m unittest test_chain_map_bootstrap
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import chain_map_bootstrap as cb

ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "cloud/migrations/0039_chain_map.sql"
MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02"]
PAYLOAD = {"months": MONTHS, "inst0": 2, "notes": ["一行"], "owner": {"TW 1111": "n1"},
           "tw": {"1111": {"t": [1, 2, 3, 4], "i": [0.5, -1]}}, "us": {"XX": {"t": [0.1, 0.2, 0.3, 0.4]}}}


def database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION.read_text(encoding="utf-8"))
    return db


class BootstrapTest(unittest.TestCase):
    def test_writes_one_row_without_owner(self) -> None:
        db = database()
        db.executescript(cb.bootstrap_sql(PAYLOAD, built_at="2026-11-24T10:00:00Z"))
        rows = db.execute("SELECT version, as_of, payload, built_at FROM chain_map_flows").fetchall()
        self.assertEqual(len(rows), 1)
        version, as_of, payload, built_at = rows[0]
        self.assertEqual((version, as_of, built_at), ("bootstrap-2026-02", "2026-02", "2026-11-24T10:00:00Z"))
        stored = json.loads(payload)
        self.assertEqual(list(stored), ["months", "inst0", "notes", "tw", "us"])   # 主要環節是定義，不寫進資金數字
        self.assertEqual(stored["tw"], PAYLOAD["tw"])

    def test_rerun_replaces_the_same_bootstrap_row(self) -> None:
        db = database()
        db.executescript(cb.bootstrap_sql(PAYLOAD, built_at="2026-11-24T10:00:00Z"))
        db.executescript(cb.bootstrap_sql({**PAYLOAD, "notes": ["改過"]}, built_at="2026-11-25T10:00:00Z"))
        self.assertEqual(db.execute("SELECT COUNT(*), MAX(built_at), json_extract(MAX(payload), '$.notes[0]') FROM chain_map_flows").fetchone(),
                         (1, "2026-11-25T10:00:00Z", "改過"))

    def test_never_overrides_a_scheduled_build(self) -> None:
        db = database()
        db.execute("INSERT INTO chain_map_flows VALUES ('2026-11-30', '2026-10', '{}', '2026-11-30T10:00:00Z')")
        db.executescript(cb.bootstrap_sql(PAYLOAD, built_at="2026-12-01T10:00:00Z"))
        self.assertEqual(db.execute("SELECT version FROM chain_map_flows").fetchall(), [("2026-11-30",)])

    def test_contract_errors_stop(self) -> None:
        bad = [
            {**PAYLOAD, "months": ["2025-11", "2026-01"]},          # 不連續
            {**PAYLOAD, "inst0": 9},
            {**PAYLOAD, "tw": {"1111": {"t": [1, 2], "i": []}}},
            {**PAYLOAD, "tw": {"1111": {"t": [1, 2, 3, 4], "i": [1]}}},
            {**PAYLOAD, "us": {"XX": {"t": [1, 2, 3, float("nan")]}}},
            {**PAYLOAD, "owner": {"1111": "n1"}},
            {k: v for k, v in PAYLOAD.items() if k != "us"},
        ]
        for payload in bad:
            with self.assertRaises(cb.ContractError, msg=str(payload)[:80]):
                cb.bootstrap_sql(payload, built_at="2026-11-24T10:00:00Z")

    def test_statement_too_big_for_d1_stops(self) -> None:
        big = {**PAYLOAD, "tw": {str(1000 + k): {"t": [123456.7] * 4, "i": [1.5, 2.5]} for k in range(3000)}}
        with self.assertRaises(cb.ContractError):
            cb.bootstrap_sql(big, built_at="2026-11-24T10:00:00Z")

    def test_cli_writes_file_and_prints_no_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "flows.json", Path(tmp) / "out.sql"
            src.write_text(json.dumps({**PAYLOAD, "us": {"XX": {"t": [987.6, 0.2, 0.3, 0.4]}}}), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cb.main(["--flows", str(src), "--sql-output", str(out), "--built-at", "2026-11-24T10:00:00Z"])
            self.assertEqual(code, 0)
            self.assertIn("INSERT INTO chain_map_flows", out.read_text(encoding="utf-8"))
            self.assertNotIn("987.6", buffer.getvalue())
            self.assertIn("2025-11", buffer.getvalue())

    def test_json_output_is_the_r2_object_the_site_reads(self) -> None:
        sample = {**PAYLOAD, "us": {"XX": {"t": [987.6, 0.2, 0.3, 0.4]}, "PSTG": {"t": [1, 2, 3, 4]}}}
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "flows.json", Path(tmp) / "flows-r2.json"
            src.write_text(json.dumps(sample), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cb.main(["--flows", str(src), "--json-output", str(out), "--built-at", "2026-11-24T15:00:00Z"])
            self.assertEqual(code, 0)
            obj = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual({k: obj[k] for k in ("schema", "version", "as_of", "built_at")},
                             {"schema": "chain-map-flows-r2/v1", "version": "bootstrap-2026-02", "as_of": "2026-02",
                              "built_at": "2026-11-24T15:00:00Z"})
            self.assertEqual(list(obj["payload"]), ["months", "inst0", "notes", "tw", "us"], "沒有 owner")
            self.assertEqual(obj["payload"]["us"], {"XX": {"t": [987.6, 0.2, 0.3, 0.4]}, "P": {"t": [1, 2, 3, 4]}}, "PSTG 改成 P")
            self.assertEqual(sample["us"].keys(), {"XX", "PSTG"}, "原檔的物件不被改動")
            self.assertNotIn("987.6", buffer.getvalue())
            self.assertIn("chain-map/current/flows.json", buffer.getvalue())
            # 每日建置讀得懂同一份（當成下一次的底）
            import chain_map_flows as cm
            store = type("S", (), {"get": lambda self, key: out.read_bytes()})()
            payload, built_at = cm.load_current(store)
            self.assertEqual((payload, built_at), (obj["payload"], "2026-11-24T15:00:00Z"))
            cm.validate_bootstrap(payload)
            # 一個輸出都沒給：停下
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cb.main(["--flows", str(src)])
            # 格式不對：不產生
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({**PAYLOAD, "inst0": 9}), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cb.main(["--flows", str(bad), "--json-output", str(Path(tmp) / "x.json")]), 1)
            self.assertFalse((Path(tmp) / "x.json").exists())


if __name__ == "__main__":
    unittest.main()
