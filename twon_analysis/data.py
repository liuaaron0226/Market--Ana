"""唯讀行情 adapter：從票 01 的 SQLite 取某代號到 as_of 為止的有效日線（停牌、無收盤日不入列，不補 K 棒）。

價格不還原權值（與原站同口徑）；除權息等「X 不比價」日的漲跌幅設 0（handoff §7）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .indicators import Bar

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data/stock-history/stock-history.sqlite'


def load_bars(code: str, as_of: str, db_path: Path = DEFAULT_DB, limit: int = 300) -> tuple[list[Bar], dict]:
    """回傳 (bars 舊到新, 當日資訊)。當日資訊含 name、kind、chg（%）、change_sign、turnover。"""
    if not isinstance(code, str) or not code:
        raise ValueError('代號必須是字串（保留 0050 前導零）')
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            '''SELECT date, name, kind, open, high, low, close, change_sign, change, volume_shares, turnover
               FROM daily WHERE code = ? AND date <= ? AND close IS NOT NULL AND open IS NOT NULL
               ORDER BY date DESC LIMIT ?''', (code, as_of, limit)).fetchall()
    finally:
        connection.close()
    rows.reverse()
    bars = [Bar(r[0], r[3], r[4], r[5], r[6], r[9]) for r in rows]
    if not rows:
        return [], {}
    last = rows[-1]
    reference = last[6] - last[8] if last[8] is not None else None
    chg = 0.0 if last[7] == 'X' or not reference else round(last[8] / reference * 100, 4)
    return bars, {'name': last[1], 'kind': last[2], 'chg': chg, 'change_sign': last[7], 'turnover': last[10], 'date': last[0]}
