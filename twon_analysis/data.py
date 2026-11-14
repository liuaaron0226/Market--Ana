"""唯讀行情 adapter：從票 01 的 SQLite 取某代號到 as_of 為止的有效日線（停牌、無收盤日不入列，不補 K 棒）。

這一層回傳的是實際成交價，不還原；除權息等「X 不比價」日的漲跌幅設 0（handoff §7）。
股本變動（分割、面額變更、減資、配股）的還原在 analyze_v2 以 price_adjustment 套用（本人 2026-11-14 定案），
原站對照測試仍直接用這裡的未還原價。
上櫃代號（票06）改讀 B 線的上櫃日 K 庫；同一代號只取一個市場庫，不混接轉市場前後的列。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .indicators import Bar

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data/stock-history/stock-history.sqlite'
DEFAULT_OTC_DB = ROOT / 'data/otc-history/otc_history.sqlite'

_SELECT = '''SELECT date, name, kind, open, high, low, close, change_sign, change, volume_shares, turnover
             FROM daily WHERE code = ? AND date <= ? AND close IS NOT NULL AND open IS NOT NULL
             ORDER BY date DESC LIMIT ?'''


def _read_only(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True)


def _otc_rows(code: str, as_of: str, otc_db_path: Path | None, limit: int) -> list[tuple] | None:
    """代號在上櫃庫有任何列即視為上櫃標的，回傳其列；否則回 None 交給上市庫。"""
    if otc_db_path is None or not otc_db_path.exists():
        return None
    connection = _read_only(otc_db_path)
    try:
        if connection.execute('SELECT 1 FROM daily WHERE code = ? LIMIT 1', (code,)).fetchone() is None:
            return None
        return connection.execute(_SELECT, (code, as_of, limit)).fetchall()
    finally:
        connection.close()


def load_bars(code: str, as_of: str, db_path: Path = DEFAULT_DB, limit: int = 300,
              otc_db_path: Path | None = DEFAULT_OTC_DB) -> tuple[list[Bar], dict]:
    """回傳 (bars 舊到新, 當日資訊)。當日資訊含 name、kind、chg（%）、change_sign、turnover。"""
    if not isinstance(code, str) or not code:
        raise ValueError('代號必須是字串（保留 0050 前導零）')
    rows = _otc_rows(code, as_of, otc_db_path, limit)
    market = '上櫃' if rows is not None else '上市'
    if rows is None:
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(_SELECT, (code, as_of, limit)).fetchall()
        finally:
            connection.close()
    rows.reverse()
    bars = [Bar(r[0], r[3], r[4], r[5], r[6], r[9]) for r in rows]
    if not rows:
        return [], {}
    last = rows[-1]
    reference = last[6] - last[8] if last[8] is not None else None
    chg = 0.0 if last[7] == 'X' or not reference else round(last[8] / reference * 100, 4)
    return bars, {'name': last[1], 'kind': last[2], 'chg': chg, 'change_sign': last[7], 'turnover': last[10], 'date': last[0],
                  'market': market}
