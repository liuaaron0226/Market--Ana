"""還原價的補充資料：共同 events.json 沒有、但還原股本變動需要的官方數字。

- 證交所 TWTCAU：ETF 分割／反分割恢復買賣參考價（0050 2025-06-18 一拆四就在這張表）。
- 櫃買 revivt／pvChgRslt／etfSplitRslt：上櫃減資、面額變更、ETF 分割的恢復買賣參考價。
- 證交所 TWT49UDetail：上市「除權息」同日事件的每股現金股利（TWT49U 只給權值＋息值合計，拆不開）。
- 櫃買除權息的現金股利直接取自工作集裡的 exDailyQ 原件，不另外請求。

events.json 的形狀與既有使用者（回測、對照包、評估）都不動；本檔只給 twon_analysis.price_adjustment 用。

不留原件：每天只抓資料日前後的視窗，官方表隨時可重查；留原件會讓 R2 工作集每天多一批不會再讀的檔。
擷取規則照舊：同站固定間隔、遇非 200／非 JSON／欄位不符即停不重試、原子寫檔。
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Callable

from stock_history import http_get

ROOT = Path(__file__).resolve().parent
SCHEMA = 'capital-supplement/v1'
DEFAULT_PATH = ROOT / 'data/corporate-actions/capital-supplement.json'
SEED_PATH = ROOT / 'seeds/capital-supplement.json'
SAVE_EVERY = 20
DETAIL_URL = 'https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail'
# 同一官方站的請求間隔（秒）：證交所 3、櫃買 5，與既有擷取規則一致。
INTERVAL = {'TWSE': 3.0, 'TPEx': 5.0}

TPEX = 'https://www.tpex.org.tw/www/zh-tw/bulletin/'
SOURCES = {
    'TWSE-TWTCAU': {
        'url': 'https://www.twse.com.tw/rwd/zh/split/TWTCAU', 'type': '分割',
        'date': '恢復買賣日期', 'code': 'ETF代號', 'before': '停止買賣前收盤價格', 'reference': '恢復買賣參考價',
    },
    'TPEx-revivt': {
        'url': TPEX + 'revivt', 'type': '減資',
        'date': '恢復買賣日期', 'code': '股票代號', 'before': '最後交易日之收盤價格', 'reference': '減資恢復買賣開始日參考價格',
    },
    'TPEx-pvChgRslt': {
        'url': TPEX + 'pvChgRslt', 'type': '變更面額',
        'date': '恢復買賣日期', 'code': '證券代號', 'before': '最後交易日之收盤價格', 'reference': '恢復買賣開始參考價',
    },
    'TPEx-etfSplitRslt': {
        'url': TPEX + 'etfSplitRslt', 'type': '分割',
        'date': '恢復買賣日期', 'code': '證券代號', 'before': '最後交易日之收盤價格', 'reference': '恢復買賣開始參考價',
    },
}


def _roc_date(text: str) -> str:
    """'114/06/18'（證交所）或 '1140618'（櫃買）→ '2025-06-18'。"""
    text = text.strip()
    parts = text.split('/') if '/' in text else [text[:-4], text[-4:-2], text[-2:]]
    year, month, day = (int(part) for part in parts)
    return date(year + 1911, month, day).isoformat()


def _number(text: object) -> float:
    return float(str(text).replace(',', '').strip())


def _json(content: bytes) -> dict:
    try:
        payload = json.loads(content.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('官方回應不是 JSON（可能是驗證頁）') from error
    if not isinstance(payload, dict):
        raise ValueError('官方回應頂層不是物件')
    return payload


def parse_capital_events(source: str, content: bytes) -> list[dict]:
    """官方恢復買賣參考價表 → 事件（date＝恢復買賣日、close_before、reference）。區間內沒有事件回空清單。"""
    spec = SOURCES[source]
    payload = _json(content)
    if source.startswith('TPEx-'):
        tables = payload.get('tables')
        if not isinstance(tables, list) or not tables:
            raise ValueError(f'{source} 回應沒有資料表')
        table = tables[0]
    elif 'fields' not in payload:
        return []  # 證交所區間內沒有事件時只回 stat 訊息，沒有欄位
    else:
        table = payload
    fields = table.get('fields') or []
    missing = [spec[key] for key in ('date', 'code', 'before', 'reference') if spec[key] not in fields]
    if missing:
        raise ValueError(f'{source} 欄位不符，缺 {missing}')
    events = []
    for row in table.get('data') or []:
        cell = dict(zip(fields, row))
        try:
            events.append({'date': _roc_date(str(cell[spec['date']])), 'code': str(cell[spec['code']]).strip(),
                           'type': spec['type'], 'close_before': _number(cell[spec['before']]),
                           'reference': _number(cell[spec['reference']]), 'source': source})
        except ValueError as error:
            raise ValueError(f'{source} 資料列無法解析：{row[:6]}') from error
    return sorted(events, key=lambda event: (event['date'], event['code']))


CASH_FIELD = '(每股配發現金股利)除息'


def parse_detail_cash(content: bytes) -> float:
    """證交所 TWT49UDetail → 該次除權息的每股現金股利（元）。查無資料是錯誤，不當成 0。"""
    payload = _json(content)
    fields, rows = payload.get('fields') or [], payload.get('data') or []
    if CASH_FIELD not in fields or len(rows) != 1:
        raise ValueError(f"除權息明細無資料或欄位不符：{payload.get('stat')!r}")
    text = str(rows[0][fields.index(CASH_FIELD)])
    if not text.endswith('元／股'):
        raise ValueError(f'現金股利格式不符：{text!r}')
    return _number(text.removesuffix('元／股'))


def load(path: Path = DEFAULT_PATH, seed: Path = SEED_PATH) -> dict:
    """工作集裡的補充資料；還沒有就用隨程式入庫的種子，都沒有就是空的。"""
    for candidate in (path, seed):
        if candidate.exists():
            state = json.loads(candidate.read_text(encoding='utf-8'))
            if state.get('schema') != SCHEMA:
                raise ValueError(f'{candidate.name} 不是 {SCHEMA}')
            return state
    return {'schema': SCHEMA, 'events': [], 'cash_dividends': {}}


def _save(path: Path, state: dict) -> None:
    state['events'].sort(key=lambda event: (event['date'], event['code'], event['source']))
    state['cash_dividends'] = dict(sorted(state['cash_dividends'].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    partial.write_text(json.dumps(state, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    os.replace(partial, path)


class _Stop(Exception):
    """官方站拒絕或回應不可辨識：立刻停手，不重試、不往下打。"""


def _in_window(event: dict, start: str, end: str) -> bool:
    return start <= event['date'] <= end


def refresh(path: Path, listed_events: list[dict], start: str, end: str, *, seed: Path = SEED_PATH,
            get: Callable = http_get, sleep: Callable = time.sleep, otc_raw_dir: Path | None = None) -> dict:
    """抓 start..end（YYYY-MM-DD）的官方表，更新補充資料檔；遇拒絕即停，已取得的部分照存。

    順序：證交所（ETF 分割 → 上市除權息的現金股利明細）先做完，再打櫃買三張表；櫃買出狀況不拖住上市。
    otc_raw_dir：櫃買 exDailyQ 原件目錄；給了就把櫃買除權息的現金股利一併帶入（讀檔，不請求）。
    """
    state = load(path, seed)
    summary = {'requested': 0, 'stopped': None, 'missing_details': []}
    if otc_raw_dir is not None:
        import otc_corporate_actions
        state['cash_dividends'].update(otc_corporate_actions.cash_dividends(otc_raw_dir))

    def request(host: str, url: str, params: dict) -> bytes:
        if summary['requested']:
            sleep(INTERVAL[host])
        summary['requested'] += 1
        name = url.rsplit('/', 1)[-1]
        try:
            response = get(url, params)
        except Exception as error:  # noqa: BLE001 — 連線錯誤即停，不重試
            raise _Stop(f'{name} 連線錯誤：{type(error).__name__}') from error
        if response.status != 200:
            raise _Stop(f'{name} HTTP {response.status}')
        try:
            _json(response.content)
        except ValueError as error:
            raise _Stop(f'{name} {error}') from error
        return response.content

    def refresh_table(source: str) -> None:
        host = source.split('-')[0]
        separator = '' if host == 'TWSE' else '/'
        params = {'startDate': start.replace('-', separator), 'endDate': end.replace('-', separator), 'response': 'json'}
        try:
            fetched = parse_capital_events(source, request(host, SOURCES[source]['url'], params))
        except ValueError as error:
            raise _Stop(str(error)) from error
        had = [event for event in state['events'] if event['source'] == source and _in_window(event, start, end)]
        if had and not fetched:
            raise _Stop(f'{source} 回空，但同區間原本有 {len(had)} 筆事件；不採用')
        state['events'] = [event for event in state['events'] if event not in had
                           ] + [event for event in fetched if _in_window(event, start, end)]

    def refresh_listed_cash() -> None:
        for event in listed_events:
            key = f"{event['code']}|{event['date']}"
            if (event['type'] != '除權息' or str(event.get('source', '')).startswith('TPEx-')
                    or not _in_window(event, start, end) or key in state['cash_dividends']):
                continue
            content = request('TWSE', DETAIL_URL, {'STK_NO': event['code'], 'T1': event['date'].replace('-', ''),
                                                   'response': 'json'})
            try:
                state['cash_dividends'][key] = parse_detail_cash(content)
            except ValueError:
                summary['missing_details'].append(key)  # 這一筆維持不還原，下次再試；不擋其他事件
                continue
            if len(state['cash_dividends']) % SAVE_EVERY == 0:
                _save(path, state)  # 長回補中途被中斷也留得住進度

    try:
        refresh_table('TWSE-TWTCAU')
        refresh_listed_cash()
        for source in ('TPEx-revivt', 'TPEx-pvChgRslt', 'TPEx-etfSplitRslt'):
            refresh_table(source)
    except _Stop as stop:
        summary['stopped'] = str(stop)
    _save(path, state)
    return {**summary, 'events': len(state['events']), 'cash_dividends': len(state['cash_dividends'])}
