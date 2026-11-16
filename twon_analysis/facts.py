"""個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）。

只整理官方資料與由它算出的數字，固定句型、不加判讀詞、沒有評分。天生沒有或當天沒有的資料就少那一格，
不放說明文字；頁面只負責顯示這裡算好的內容。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _lots(shares: float) -> int:
    """股 → 張，四捨五入（不是銀行家捨入：2.5 張要顯示 3 張）。"""
    return int(Decimal(str(shares / 1000)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _round(value: float, places: int) -> float:
    """四捨五入（不是銀行家捨入）；和張數的捨入一致。"""
    # 加 0.0 把 -0.0 變成 0.0：很小的負數捨入後要是平常的 0，頁面才不會印出「-0」。
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)) + 0.0


def _num(value: float) -> str:
    """數字最多兩位小數、去掉尾端的零、加千分位：2.16、1.1、2、2,383.25（和樣品、網站其他數字的寫法一致）。"""
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def _side(close: float, line: float) -> str:
    return '之上' if close >= line else '之下'


def _position_sentence(close: float, ma20: float | None, ma240: float | None) -> str | None:
    if ma20 is None:
        return None
    parts = [f'月線 {_num(ma20)} {_side(close, ma20)}']
    if ma240 is not None:
        parts.append(f'年線 {_num(ma240)} {_side(close, ma240)}')
    return f'收盤 {_num(close)}，在{"、".join(parts)}。'


def _volume_sentence(ratio: float | None, rsi: float | None) -> str | None:
    parts = []
    if ratio is not None:
        parts.append(f'成交量是均量的 {_num(ratio)} 倍')
    if rsi is not None:
        parts.append(f'RSI {_num(rsi)}')
    return f'{"，".join(parts)}。' if parts else None


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


INSTITUTIONS = (('foreign', '外資'), ('trust', '投信'), ('dealer', '自營商'))


def _streak(values: list[float | None]) -> tuple[str | None, int | None, bool]:
    """從最新一天往回數同方向的天數；當天是 0 或缺值不寫連續。
    遇到方向相反或 0 才算確定停下；遇到缺資料的交易日或數到最早一天，只能說「以上」（at_least=True）。"""
    if not values or not values[-1]:
        return None, None, False
    buying = values[-1] > 0
    days = 0
    for value in reversed(values):
        if value is None:
            return ('buy' if buying else 'sell'), days, True
        if value == 0 or (value > 0) != buying:
            return ('buy' if buying else 'sell'), days, False
        days += 1
    return ('buy' if buying else 'sell'), days, True


def _sum_lots(values: list[float | None], n: int) -> int | None:
    window = values[-n:]
    if len(window) < n or any(value is None for value in window):
        return None
    return _lots(sum(window))


def _institutional_block(institutional: list[dict], trading_days: list[str]) -> dict | None:
    """對齊交易日（K 線的日期）：法人序列缺日是省略、不補零，所以缺的那天在這裡是 None，
    連續天數在那裡停下（只能說「以上」），時間窗跨過它的累計不給。"""
    if not institutional:
        return None
    latest = institutional[-1]['date']
    window = ([day for day in trading_days if day < latest] + [latest]) if trading_days else [row['date'] for row in institutional]
    by_day = {row['date']: row for row in institutional}
    rows = []
    for key, name in INSTITUTIONS:
        values = [by_day[day].get(key) if day in by_day else None for day in window]
        side, days, at_least = _streak(values)
        rows.append({'name': name, 'side': side, 'days': days, 'at_least': at_least,
                     'sum5': _sum_lots(values, 5), 'sum10': _sum_lots(values, 10), 'sum20': _sum_lots(values, 20)})
    if not any(row['side'] or row['sum5'] is not None for row in rows):
        return None
    return {'data_date': latest, 'rows': rows}


def _aligned(series: list[dict], key: str, trading_days: list[str]) -> tuple[str, list[str], list]:
    """把一串（日期, 值）對齊到交易日：回傳 (最新資料日, 到最新資料日為止的交易日, 對應的值；缺的那天是 None)。"""
    latest = series[-1]['date']
    by_day = {row['date']: row.get(key) for row in series}
    # 最新資料日一定排在最後，就算 K 線沒有那一天（例如當天停牌、原件仍有餘額）。
    days = ([day for day in trading_days if day < latest] + [latest]) if trading_days else [row['date'] for row in series]
    return latest, days, [by_day.get(day) for day in days]


def _credit_row(name: str, series: list[dict], key: str, trading_days: list[str], to_lots: bool) -> dict | None:
    """一列融資券：最新餘額、相對 5 個交易日前的增減（那天缺資料就不給）、近 20 個交易日走勢（從第一個有資料的日子起）。"""
    if not series or series[-1].get(key) is None:
        return None
    latest, _, values = _aligned(series, key, trading_days)
    lots = (lambda v: None if v is None else _lots(v)) if to_lots else (lambda v: v)
    # 增減用「顯示出來的兩個餘額」相減，畫面上的數字才對得起來。
    change = lots(values[-1]) - lots(values[-6]) if len(values) >= 6 and values[-6] is not None else None
    window = values[-20:]
    first = next((i for i, v in enumerate(window) if v is not None), len(window))
    return {'name': name, 'balance': lots(values[-1]), 'change5': change,
            'series': [lots(v) for v in window[first:]], 'data_date': latest}


def _credit_block(credit: dict, trading_days: list[str]) -> dict | None:
    margin, borrow = credit.get('margin') or [], credit.get('borrow') or []
    rows = [row for row in (
        _credit_row('融資', margin, 'margin', trading_days, to_lots=False),   # 融資融券原件單位就是張
        _credit_row('融券', margin, 'short', trading_days, to_lots=False),
        _credit_row('借券', borrow, 'balance', trading_days, to_lots=True),   # 借券原件是股
    ) if row]
    return {'rows': rows} if rows else None


def _previous_month(period: str, months: int = 1) -> str:
    year, month = map(int, period.split('-'))
    index = year * 12 + (month - 1) - months
    return f'{index // 12}-{index % 12 + 1:02d}'


def _growth(now: float | None, then: float | None) -> float | None:
    return _round((now / then - 1) * 100, 1) if now is not None and then else None


def _fundamentals_block(valuation: dict | None, revenue: list[dict]) -> dict | None:
    valuation_part = None
    if valuation and any(valuation.get(k) is not None for k in ('pe', 'pb', 'dividend_yield_pct')):
        valuation_part = {'pe': valuation.get('pe'), 'pb': valuation.get('pb'),
                          'yield': valuation.get('dividend_yield_pct'), 'data_date': valuation.get('date')}
    revenue_part, yoy_series = None, []
    if revenue:
        by_period = {row['period']: row['revenue'] for row in revenue}
        latest = max(by_period)
        revenue_part = {'period': latest, 'mom': _growth(by_period[latest], by_period.get(_previous_month(latest))),
                        'yoy': _growth(by_period[latest], by_period.get(_previous_month(latest, 12)))}
        periods = [_previous_month(latest, n) for n in range(12, -1, -1)]   # 近 13 個月，由舊到新
        yoy_series = [{'period': period, 'yoy': _growth(by_period.get(period), by_period.get(_previous_month(period, 12)))}
                      for period in periods]
    if valuation_part is None and revenue_part is None:
        return None
    return {'valuation': valuation_part, 'revenue': revenue_part, 'revenue_yoy': yoy_series}


def _earnings_sentence(fundamentals: dict | None) -> str | None:
    if not fundamentals:
        return None
    parts = []
    revenue = fundamentals.get('revenue')
    if revenue and revenue.get('yoy') is not None:
        yoy = revenue['yoy']
        parts.append(f'{int(revenue["period"][5:7])} 月營收年增 {yoy:+.1f}%' if yoy else f'{int(revenue["period"][5:7])} 月營收年增 0.0%')
    valuation = fundamentals.get('valuation')
    if valuation and valuation.get('pe') is not None:
        parts.append(f'本益比 {_num(valuation["pe"])}')
    return f'{"，".join(parts)}。' if parts else None


PRICE_ROWS = (('ma5', '5 日線'), ('ma10', '10 日線'), ('ma20', '月線'), ('ma60', '季線'), ('ma120', '半年線'),
              ('ma240', '年線'), ('high20_close', '20 日高'), ('low20_close', '20 日低'))


def _technical_block(close: float, indicators: dict) -> dict | None:
    """數值直接用個股分析結果的指標（已還原、和圖上的線同一套），不重算。"""
    rows = [{'name': name, 'value': indicators[key], 'distance_pct': _round((close / indicators[key] - 1) * 100, 1)}
            for key, name in PRICE_ROWS if indicators.get(key)]
    if not rows:
        return None
    return {'rows': rows, 'rsi': indicators.get('rsi14'), 'volume_ratio': indicators.get('relative_volume'),
            'kd_k': indicators.get('k9'), 'kd_d': indicators.get('d9')}


def with_news(facts: dict | None, *, industry: str | None) -> dict | None:
    """建置版本時加上產業別與「消息」按鈕（產業別來自名錄，分析時不知道）。公告清單另由讀取入口提供。

    「消息」排在「技術」前面（本人選的擺法 A 的順序）；沒有產業別（例如不在名錄的 0050）就不加。"""
    if not facts or not industry:
        return facts
    blocks = [name for name in facts.get("blocks", []) if name != "news"]
    at = blocks.index("technical") if "technical" in blocks else len(blocks)
    return {**facts, "industry": industry, "blocks": blocks[:at] + ["news"] + blocks[at:]}


def build_facts(*, data_date: str, close: float, chg_pct: float | None, indicators: dict, institutional: list[dict],
                trading_days: list[str] | None = None, credit: dict | None = None, valuation: dict | None = None,
                revenue: list[dict] | None = None) -> dict:
    """data_date：資料日；close：資料日收盤；chg_pct：漲跌幅（%），不比價或沒有參考價時是 None；
    institutional：由舊到新的三大法人買賣超（股，已依還原換算）；trading_days：由舊到新的交易日（K 線日期）；
    credit：{'margin': [{date, margin, short}]（張）, 'borrow': [{date, balance}]（股）}；valuation：估值最近一筆；
    revenue：[{period, revenue}] 由舊到新。"""
    today = []
    if chg_pct is not None:
        today.append(_close_cell(chg_pct))
    ratio = indicators.get('relative_volume')
    if ratio is not None:
        today.append(_cell('volume', '成交量', None, f'均量的 {_num(ratio)} 倍'))
    latest = institutional[-1] if institutional else None
    if latest and latest.get('date') == data_date and latest.get('foreign') is not None:
        today.append(_foreign_cell(latest['foreign']))
    fundamentals_block = _fundamentals_block(valuation, revenue or [])
    summary = [sentence for sentence in (
        _position_sentence(close, indicators.get('ma20'), indicators.get('ma240')),
        _volume_sentence(ratio, indicators.get('rsi14')),
        _earnings_sentence(fundamentals_block),
    ) if sentence]
    institutional_block = _institutional_block(institutional, trading_days or [])
    credit_block = _credit_block(credit or {}, trading_days or [])
    technical_block = _technical_block(close, indicators)
    # 切換籤的順序（本人選的擺法 A）：重點｜法人｜融資券｜基本面｜消息｜技術；沒有內容的那一塊不出現。
    blocks = [name for name, present in (('summary', bool(summary)), ('institutional', institutional_block is not None),
                                         ('credit', credit_block is not None), ('fundamentals', fundamentals_block is not None),
                                         ('technical', technical_block is not None)) if present]
    return {'data_date': data_date, 'today': today, 'summary': summary, 'institutional': institutional_block,
            'credit': credit_block, 'fundamentals': fundamentals_block, 'technical': technical_block, 'blocks': blocks}
