"""基金當成一般個股 05 票：比較資料包裡基金是 items 的一筆（和股票同形狀），原本的 funds 欄退場。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from cloud import build_compare_pack

FUND_ITEM = {"code": "BLKTENAU", "name": "貝萊德世界科技 A10", "market": "境外基金", "kind": "fund", "status": "ok", "periods": []}


def build(fund_result=None, fund_error=None):
    state = {"universe": {"data_date": "2026-11-10", "etf_members": []}, "members": {"2330": 1}, "core": {"0050"}}

    def fake_precompute(codes, end, listed_dates, loader):
        return {"data_date": end, "max_codes": 5, "notes": [], "benchmark": "{}",
                "items": {code: json.dumps({"code": code}) for code in codes}}

    fund = mock.patch.object(build_compare_pack.fund_compare, "precompute",
                             side_effect=fund_error, return_value=fund_result)
    with mock.patch.object(build_compare_pack.live_server, "universe_state", return_value=state), \
         mock.patch.object(build_compare_pack.live_server, "lookup_security", return_value=None), \
         mock.patch.object(build_compare_pack.live_server, "cloud_build_module") as cloud_module, \
         mock.patch.object(build_compare_pack.benchmark_compare, "Loader"), \
         mock.patch.object(build_compare_pack.benchmark_compare, "precompute", side_effect=fake_precompute), fund:
        cloud_module.return_value.catalog_securities.return_value = []
        return build_compare_pack.build_pack(Path("unused.json"))


class ComparePackFundTest(unittest.TestCase):
    def test_fund_is_an_ordinary_item(self) -> None:
        pack = build(fund_result=({"BLKTENAU": json.dumps(FUND_ITEM, ensure_ascii=False)}, {}))
        self.assertEqual(sorted(pack["items"]), ["0050", "2330", "BLKTENAU"])
        self.assertEqual(json.loads(pack["items"]["BLKTENAU"])["kind"], "fund")
        self.assertNotIn("funds", pack)

    def test_fund_data_problems_do_not_stop_the_pack(self) -> None:
        pack = build(fund_result=({}, {"BLKTENAU": "沒有匯率資料"}))
        self.assertEqual(sorted(pack["items"]), ["0050", "2330"])
        pack = build(fund_error=ValueError("壞掉"))
        self.assertEqual(sorted(pack["items"]), ["0050", "2330"])


if __name__ == "__main__":
    unittest.main()
