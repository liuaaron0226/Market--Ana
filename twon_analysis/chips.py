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


FUND = ROOT / 'data/fundamentals'


@lru_cache(maxsize=64)
def _borrow(day: str) -> dict:
    path = FUND / 'twt93u' / f'twt93u-{day.replace("-", "")}.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    # 欄位：代號 名稱｜融券 6 欄｜借券賣出：前日餘額 當日賣出 當日還券 當日調整 當日餘額 可限額｜備註（單位：股）
    return {row[0].strip(): {'balance_prev': _int(row[8]), 'sold': _int(row[9]), 'balance': _int(row[12])} for row in data['data']}


def _valuation_openapi(day: str) -> dict:
    """OpenAPI BWIBBU_ALL 只有最新一天；檔名帶民國日期，只在日期相符時採用。"""
    y, m, d = day.split('-')
    path = FUND / 'openapi' / f'BWIBBU_ALL-{int(y) - 1911}{m}{d}.json'
    if not path.exists():
        return {}
    num = lambda v: None if str(v).strip() in ('', '-', '0.00') else float(str(v).replace(',', ''))
    return {row['Code'].strip(): {'per': num(row['PEratio']), 'pbr': num(row['PBratio']), 'yield': num(row['DividendYield'])}
            for row in json.loads(path.read_text(encoding='utf-8'))}


@lru_cache(maxsize=64)
def _valuation(day: str) -> dict:
    path = FUND / 'bwibbu' / f'bwibbu-{day.replace("-", "")}.json'
    if not path.exists():
        return _valuation_openapi(day)
    data = json.loads(path.read_text(encoding='utf-8'))
    out = {}
    for row in data['data']:
        cell = dict(zip(data['fields'], row))
        num = lambda key: None if str(cell[key]).strip() in ('', '-', '0.00') else float(str(cell[key]).replace(',', ''))
        out[cell['證券代號'].strip()] = {'per': num('本益比'), 'pbr': num('股價淨值比'), 'yield': num('殖利率(%)')}
    return out


def borrow(code: str, dates: list[str]) -> list[dict | None]:
    return [_borrow(d).get(code) for d in dates]


def valuation(code: str, day: str) -> dict | None:
    return _valuation(day).get(code)


@lru_cache(maxsize=4)
def _openapi(name: str) -> tuple[dict, str | None]:
    files = sorted((FUND / 'openapi').glob(f'{name}-*.json'))
    files = [f for f in files if not f.name.endswith('.meta.json')]
    if not files:
        return {}, None
    rows = json.loads(files[-1].read_text(encoding='utf-8'))
    return {row['公司代號'].strip(): row for row in rows}, files[-1].stem.split('-')[-1]


def revenue(code: str) -> dict | None:
    """最新一期月營收（OpenAPI 只有最新一期）。回傳 yoy、mom、cum_yoy（%）與資料年月。"""
    rows, _ = _openapi('t187ap05_L')
    row = rows.get(code)
    if not row:
        return None
    pct = lambda key: None if not row.get(key) or row[key] in ('-', '') else round(float(row[key]), 2)
    ym = row['資料年月']
    return {'yoy': pct('營業收入-去年同月增減(%)'), 'mom': pct('營業收入-上月比較增減(%)'),
            'cum_yoy': pct('累計營業收入-前期比較增減(%)'), 'period': f'{int(ym[:3]) + 1911}-{ym[3:]}'}


# 公開資訊觀測站「綜合損益表彙總」原件（data/fundamentals/mops/t163sb04-<民國年>-<季>.html），基本每股盈餘為當年累計。
# 各公司實際公告日未知：可用日期是「估計值」——Q2 用法定申報期限 8/14（假設），Q3 用本機取得日 11/07。
# 這不是公告日：例如台積電 Q3 已於 10/15 公告，只是本次取得的 MOPS 清單沒收錄，所以 11/06 仍用 Q2（偏保守）。
MOPS_USABLE_FROM = {('115', '02'): '2026-08-14', ('115', '03'): '2026-11-07'}


@lru_cache(maxsize=8)
def _mops_eps(year: str, season: str) -> dict:
    import html as htmllib
    import re
    path = FUND / 'mops' / f't163sb04-{year}-{season}.html'
    if not path.exists():
        return {}
    text = path.read_text(encoding='utf-8')
    out = {}
    for table in re.findall(r'<table class=.hasBorder.*?</table>', text, re.S):
        heads = [htmllib.unescape(re.sub('<[^>]+>', '', h)).strip() for h in re.findall(r'<th[^>]*>(.*?)</th>', table, re.S)]
        if '基本每股盈餘（元）' not in heads:
            continue
        col = heads.index('基本每股盈餘（元）')
        for row in re.findall(r'<tr class=.(?:even|odd).>(.*?)</tr>', table, re.S):
            cells = [re.sub('<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
            if len(cells) > col and cells[col] not in ('', '--'):
                out[cells[0]] = float(cells[col].replace(',', ''))
    return out


def eps(code: str, as_of: str = '9999-12-31') -> dict | None:
    for (year, season), usable_from in sorted(MOPS_USABLE_FROM.items(), reverse=True):
        if as_of < usable_from:
            continue
        value = _mops_eps(year, season).get(code)
        if value is not None:
            return {'eps': value, 'period': f'{int(year) + 1911}Q{int(season)}', 'basis': '當年累計（非單季）',
                    'estimated_available_at': usable_from, 'available_basis': '法定期限假設' if season == '02' else '本機取得日'}
    return None
