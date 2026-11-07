"""法人（T86）與融資融券（MI_MARGN）：從既有全市場原件逐日讀出單一個股（不從前 20 排行反推，不新增抓取）。

目前原件只有近 20 個交易日（2026-10-07～11-06，`data/market-snapshots/stock-detail/`），
超出範圍的日子回傳空，呼叫端要把該維標成「資料不足」而不是當成 0。借券（C04、RF4 的一部分）目前沒有資料。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / 'data/market-snapshots/stock-detail'


def _int(text) -> int:
    return int(str(text).replace(',', '').strip() or 0)


@lru_cache(maxsize=64)
def _t86(day: str) -> dict:
    path = DETAIL / 't86' / f't86-{day.replace("-", "")}.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))['data']
    fields = data['fields']
    out = {}
    for row in data.get('data') or []:
        cell = dict(zip(fields, row))
        out[cell['證券代號'].strip()] = {
            'foreign': _int(cell['外陸資買賣超股數(不含外資自營商)']) + _int(cell['外資自營商買賣超股數']),
            'trust': _int(cell['投信買賣超股數']), 'dealer': _int(cell['自營商買賣超股數']), 'total': _int(cell['三大法人買賣超股數']),
        }
    return out


@lru_cache(maxsize=64)
def _margin(day: str) -> dict:
    path = DETAIL / 'margin' / f'margin-{day.replace("-", "")}.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))['data']
    table = next(t for t in data['tables'] if t.get('fields') and t['fields'][0] == '代號')
    out = {}
    for row in table['data']:
        # 欄位：融資 買進 賣出 現金償還 前日餘額 今日餘額 限額｜融券 買進 賣出 現券償還 前日餘額 今日餘額 限額｜資券互抵 註記（單位：張）
        out[row[0].strip()] = {'margin_prev': _int(row[5]), 'margin': _int(row[6]), 'short_prev': _int(row[11]), 'short': _int(row[12])}
    return out


def available_days() -> list[str]:
    days = sorted(p.stem.split('-')[1] for p in (DETAIL / 't86').glob('t86-*.json'))
    return [f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in days]


def institutional(code: str, dates: list[str]) -> list[dict | None]:
    return [_t86(d).get(code) for d in dates]


def margin(code: str, dates: list[str]) -> list[dict | None]:
    return [_margin(d).get(code) for d in dates]
