from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import benchmark_compare as bc
from cloud import build_compare_pack


class FakeEtfHistory:
    def rows(self, code: str, end: str):
        if code != "00919":
            return [], None, None
        return [
            {"date": "2026-07-01", "close": 20.0, "change": 0.1, "change_sign": ""},
            {"date": "2026-10-01", "close": 21.0, "change": 0.2, "change_sign": ""},
            {"date": "2026-11-10", "close": 22.0, "change": 0.3, "change_sign": ""},
        ], "群益台灣精選高息", "上市"


def db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE daily (date TEXT, code TEXT, name TEXT, close REAL, change_sign TEXT, change REAL)")
        connection.executemany(
            "INSERT INTO daily VALUES (?, '0050', '元大台灣50', ?, '', 0)",
            [("2026-07-01", 100.0), ("2026-10-01", 105.0), ("2026-11-10", 110.0)],
        )
        connection.commit()
    finally:
        connection.close()


class EtfLoaderTest(unittest.TestCase):
    def test_etf_uses_the_same_total_return_interface_as_stocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            listed, otc = root / "listed.sqlite", root / "otc.sqlite"
            db(listed)
            db(otc)
            events = root / "events.json"
            events.write_text(json.dumps([
                {"code": "00919", "date": "2026-10-01", "type": "除息", "value": 1.0,
                 "close_before": 22.0, "reference": 21.0, "source": "TWT49U-202610"},
            ]), encoding="utf-8")
            loader = bc.Loader(listed, otc, events, otc_actions_dir=root / "missing",
                               etf_history=FakeEtfHistory())

            item = loader.item("00919", "2026-11-10", listed_date=None)

            self.assertEqual(item["status"], "ok")
            self.assertEqual(item["market"], "上市")
            self.assertEqual(item["name"], "群益台灣精選高息")
            self.assertEqual(item["dividend_source"], "TWSE TWT49U")
            one_month = next(row for row in item["periods"] if row["period"] == "1m")
            self.assertEqual(one_month["status"], "ok")
            self.assertGreater(one_month["stock_return_pct"], 4.7, "10/01 配息再投入後須高於純價格報酬")


class ComparePackEtfTest(unittest.TestCase):
    def test_etf_members_are_precomputed_with_large_common_stocks(self) -> None:
        state = {
            "universe": {"data_date": "2026-11-10", "etf_members": [
                {"code": "00919", "name": "群益台灣精選高息", "market": "上市"},
            ]},
            "members": {"2330": 1},
            "core": {"0050"},
        }
        captured = {}

        def fake_precompute(codes, end, listed_dates, loader):
            captured["codes"] = list(codes)
            return {"data_date": end, "max_codes": 5, "notes": [], "benchmark": "{}",
                    "items": {code: json.dumps({"code": code}) for code in codes}}

        with mock.patch.object(build_compare_pack.live_server, "universe_state", return_value=state), \
             mock.patch.object(build_compare_pack.live_server, "lookup_security", return_value=None), \
             mock.patch.object(build_compare_pack.live_server, "cloud_build_module") as cloud_module, \
             mock.patch.object(build_compare_pack.benchmark_compare, "Loader"), \
             mock.patch.object(build_compare_pack.benchmark_compare, "precompute", side_effect=fake_precompute):
            cloud_module.return_value.catalog_securities.return_value = []
            pack = build_compare_pack.build_pack(Path("unused.json"))

        self.assertEqual(captured["codes"], ["0050", "00919", "2330"])
        self.assertIn("00919", pack["items"])


if __name__ == "__main__":
    unittest.main()
