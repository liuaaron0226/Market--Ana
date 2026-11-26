"""季度提名與連續缺行情的本機回歸。"""

import json
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from chain_roster_nominate import generate, main, proposals_from, quarter
from chain_roster_health import market_health

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)


class Database:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        for name in ("0039_chain_map.sql", "0042_chain_roster.sql"):
            self.db.executescript((ROOT / "cloud/migrations" / name).read_text(encoding="utf-8"))

    def query(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]


class Nomination(unittest.TestCase):
    def test_scheduled_entrypoint_with_local_cli_and_no_remote_services(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = Path(folder) / "fake_agy.py"
            cli.write_text(
                "import json, os, sys\n"
                "assert json.load(sys.stdin)['event'] == 'user'\n"
                "assert '--json-schema' in sys.argv and '--sandbox' in sys.argv\n"
                "assert 'MASSIVE_API_KEY' not in os.environ\n"
                "print(json.dumps({'event':'result','result':{'status':'SUCCESS',"
                "'structured_output':{'proposals':[]}}}))\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            settings = {
                "AI_WATCH_NOMINATE_MODEL": "fixture",
                "AI_WATCH_NOMINATE_EFFORT": "medium",
                "MASSIVE_API_KEY": "test-only",
            }
            with patch("chain_roster_nominate.D1Http.from_env", return_value=self.db), patch.dict(
                os.environ, settings
            ), redirect_stdout(output):
                for action in ["due", "run", "due"]:
                    with patch.object(
                        sys, "argv", ["nominate", action, "--agy-bin", str(cli), "--agy-home", folder]
                    ):
                        main()
            self.assertEqual(output.getvalue().splitlines(), ["true", "本季已產生，提名數 0", "false"])

    def setUp(self):
        self.db = Database()
        self.chain = {
            "nodes": [
                {"id": "a", "tw": [["1111", "舊角色"]], "us": [["XX", "角色"]]},
                {"id": "b", "tw": [], "us": []},
            ],
            "tw": {"1111": "甲"},
            "us": {"XX": "乙"},
        }
        self.db.query(
            "INSERT INTO chain_map_definitions VALUES ('v','baseline',?,'2026-09-01')",
            [json.dumps(self.chain)],
        )
        self.addCleanup(self.db.db.close)
        self.p = {
            "kind": "add",
            "market": "tw",
            "code": "9999",
            "name": "候選",
            "from": "",
            "to": "b",
            "role": "供應商",
            "reason": "測試用理由",
        }

    def test_once_per_taipei_quarter_and_no_definition_write(self):
        ask = Mock(return_value=("ok", {"proposals": [self.p]}, ""))
        verify = Mock(return_value=("核對名稱", True))
        self.assertEqual(quarter(NOW), "2026-Q4")
        generate(self.db, NOW, "gemini", ask, verify)
        generate(self.db, NOW, "gemini", ask, verify)
        ask.assert_called_once()
        verify.assert_called_once()
        (row,) = self.db.query("SELECT * FROM chain_roster_rounds")
        (p,) = json.loads(row["proposals"])
        self.assertEqual((p["name"], p["otc"], p["beforeNodes"]), ("核對名稱", True, []))
        self.assertEqual(len(self.db.query("SELECT * FROM chain_map_definitions")), 1)
        self.assertFalse(self.db.query("SELECT * FROM chain_roster_decisions"))

    def test_failure_can_retry_empty_is_final_and_stale_base_is_refused(self):
        def stale(*_):
            self.db.query("UPDATE chain_map_definitions SET version='new'")
            return "ok", {"proposals": []}, ""

        for ask in (lambda *_: ("timeout", None, ""), stale):
            with self.assertRaises(RuntimeError):
                generate(self.db, NOW, "gemini", ask, None)
            self.assertFalse(self.db.query("SELECT * FROM chain_roster_rounds"))
        generate(self.db, NOW, "gemini", lambda *_: ("ok", {"proposals": []}, ""), None)
        generate(self.db, NOW, "gemini", None, None)

    def test_validation_and_membership_preconditions(self):
        for rows in ([self.p, self.p], [dict(self.p, to="wrong")], [dict(self.p, code="1111")]):
            with self.assertRaises(ValueError):
                proposals_from({"proposals": rows}, self.chain, lambda *_: ("候選", False))
        with self.assertRaises(ValueError):
            proposals_from({"proposals": [self.p]}, self.chain, lambda *_: None)
        move = dict(self.p, kind="move", code="1111", **{"from": "a"})
        (p,) = proposals_from({"proposals": [move]}, self.chain, None)
        self.assertEqual((p["name"], p["beforeNodes"], p["beforePrimary"]), ("甲", ["a"], "a"))


class Health(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.dbs = ["listed.db", "otc.db"]
        self.days = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"]
        for file in self.dbs:
            with sqlite3.connect(self.root / file) as db:
                db.executescript(
                    "CREATE TABLE daily(date,code,turnover); CREATE TABLE source_days(date PRIMARY KEY,rows)"
                )
                db.executemany("INSERT INTO source_days VALUES (?,5)", [(d,) for d in self.days])
                db.execute("INSERT INTO daily VALUES (?, '1111', 0)", [self.days[0]])
            db.close()
        self.chain = {
            "nodes": [{"tw": [["1111", "角色"], ["2222", "角色"]], "us": [["XX", "角色"]]}],
            "otc": ["2222"],
            "roster_since": {"TW 2222": self.days[2]},
        }
        self.us = {day: {"holiday": False, "requested": ["XX"], "tickers": {}} for day in self.days}
        self.us[self.days[0]]["tickers"]["XX"] = {"turnover": 0}
        self.us[self.days[2]]["holiday"] = True

    def health(self):
        return market_health(self.chain, self.root, self.dbs, self.us, lambda code, day: code, set())

    def test_separate_market_calendars_zero_recovery_and_new_member(self):
        gaps = self.health()["gaps"]
        self.assertEqual(gaps["tw:1111"], {"days": self.days[1:], "lastGood": self.days[0]})
        self.assertEqual(gaps["us:XX"], {"days": [self.days[1], *self.days[3:]], "lastGood": self.days[0]})
        self.assertNotIn("tw:2222", gaps)
        self.us[self.days[-1]]["tickers"]["XX"] = {"turnover": 0}
        with sqlite3.connect(self.root / self.dbs[0]) as db:
            db.execute("INSERT INTO daily VALUES (?, '1111', 0)", [self.days[-1]])
        db.close()
        self.assertEqual(self.health()["gaps"], {})

    def test_unobserved_market_day_breaks_streak(self):
        del self.us[self.days[2]]
        with sqlite3.connect(self.root / self.dbs[0]) as db:
            db.execute("DELETE FROM source_days WHERE date=?", [self.days[2]])
        db.close()
        self.assertFalse(self.health()["gaps"])
        self.us[self.days[2]] = {"holiday": False, "tickers": {}}
        self.assertNotIn("us:XX", self.health()["gaps"])
