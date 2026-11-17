"""全站股票代號規則（基金當成一般個股 01 票，本人 2026-11-18）：本機 live_server、比較程式、AI 摘要都用這一份。"""
from __future__ import annotations

import unittest

import stock_codes as sc


class StockCodesTest(unittest.TestCase):
    def test_four_to_twelve_uppercase_alnum(self) -> None:
        for code in ("2330", "00981A", "BLKTENAU", "ABCDEFGHIJKL"):
            self.assertTrue(sc.is_code(code), code)
        for code in ("ABC", "ABCDEFGHIJKLM", "blktenau", "BLK-TENAU", "", "2330\n"):
            self.assertFalse(sc.is_code(code), code)

    def test_pattern_is_anchored_for_match_and_search(self) -> None:
        self.assertIsNone(sc.CODE_RE.match("BLKTENAU-X"))
        self.assertIsNone(sc.CODE_RE.search("x2330"))

    def test_exchange_codes_are_stock_shaped(self) -> None:
        # 證交所即時行情只認得上市櫃股票：4～6 碼、含數字；基金長代碼不拿去問
        for code in ("2330", "00981A", "6488"):
            self.assertTrue(sc.is_exchange_code(code), code)
        for code in ("BLKTENAU", "ABCD", "1234567"):
            self.assertFalse(sc.is_exchange_code(code), code)


if __name__ == "__main__":
    unittest.main()
