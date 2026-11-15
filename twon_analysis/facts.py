"""個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）。

只整理官方資料與由它算出的數字，固定句型、不加判讀詞、沒有評分。天生沒有或當天沒有的資料就少那一格，
不放說明文字；頁面只負責顯示這裡算好的內容。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _lots(shares: float) -> int:
    """股 → 張，四捨五入（不是銀行家捨入：2.5 張要顯示 3 張）。"""
    return int(Decimal(str(shares / 1000)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _multiple_text(value: float) -> str:
    """倍數去掉尾端的零：2.16 → 2.16、1.10 → 1.1、2.00 → 2（和樣品、網站其他數字的寫法一致）。"""
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def _cell(key: str, label: str, tone: str | None, text: str) -> dict:
    return {'key': key, 'label': label, 'tone': tone, 'text': text}


def _close_cell(chg_pct: float) -> dict:
    tone, word = ('up', '收紅') if chg_pct > 0 else ('down', '收黑') if chg_pct < 0 else (None, '平盤')
    return _cell('close', '收盤', tone, f'{word} {chg_pct:+.2f}%' if chg_pct else f'{word} 0.00%')


def _foreign_cell(shares: float) -> dict:
    lots = _lots(shares)
    if lots > 0:
        return _cell('foreign', '外資', 'up', f'買超 {lots:,} 張')
    if lots < 0:
        return _cell('foreign', '外資', 'down', f'賣超 {-lots:,} 張')
    return _cell('foreign', '外資', None, '買賣超 0 張')


def build_facts(*, data_date: str, chg_pct: float | None, indicators: dict, institutional: list[dict]) -> dict:
    """data_date：資料日；chg_pct：漲跌幅（%），不比價或沒有參考價時是 None；
    institutional：由舊到新的三大法人買賣超（股，已依還原換算）。"""
    today = []
    if chg_pct is not None:
        today.append(_close_cell(chg_pct))
    ratio = indicators.get('relative_volume')
    if ratio is not None:
        today.append(_cell('volume', '成交量', None, f'均量的 {_multiple_text(ratio)} 倍'))
    latest = institutional[-1] if institutional else None
    if latest and latest.get('date') == data_date and latest.get('foreign') is not None:
        today.append(_foreign_cell(latest['foreign']))
    return {'data_date': data_date, 'today': today, 'blocks': []}
