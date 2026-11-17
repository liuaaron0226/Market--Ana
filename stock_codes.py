"""全站股票代號規則（基金當成一般個股 01 票，本人 2026-11-18）：4～12 碼大寫英數。

4～6 碼是上市櫃股票與 ETF；更長的是名錄裡的基金（例如 BLKTENAU）。和雲端 cloud/lib/codes.js 的 CODE_PATTERN 是同一個，
要調整得兩邊一起改；比較程式、本機 live_server 都用這一份，不各寫一條正則。
"""
from __future__ import annotations

import re

CODE_PATTERN = r"[0-9A-Z]{4,12}"
CODE_RE = re.compile(CODE_PATTERN)
_EXCHANGE_RE = re.compile(r"[0-9A-Z]{4,6}")


def is_code(text: str) -> bool:
    return CODE_RE.fullmatch(text or "") is not None


def is_exchange_code(text: str) -> bool:
    """證交所即時行情只認得上市櫃股票的代號形狀：4～6 碼、含數字。"""
    return _EXCHANGE_RE.fullmatch(text or "") is not None and any(ch.isdigit() for ch in text)
