"""融資維持率（估）：五塊資料的其中一塊（本人 2026-11-16 共識；規格 .scratch/stock-signals/spec.md）。

交易所只公布每檔融資的張數，不公布金額，所以成本只能估。每天從起點把長期保存區（margin_history 管的
data/margin-history：個股融資原件加同一天的行情摘要）全部重播一次，每檔同時追兩種成本：
  高成本假設：賣出與現金償還時按比例減掉所有部位（成本維持平均）。
  低成本假設：賣出與現金償還時最近買的先減（短線的人先走，長抱的部位留下）。
新買進的張數用當天成交均價（成交金額 ÷ 成交股數）入帳；每天再以官方公布的當日餘額為準，把估計的部位等比例對齊。
維持率＝收盤 ÷（成本 × 融資成數六成）；兩種假設各算一個，小的是範圍下限、大的是上限。

每日對帳：用固定的混合權重把兩個成本合成一個，加總成估計的全市場融資金額，和官方當天公布的比；差距超過門檻、
原件有缺日、或原件落後行情太多的那一天，所有股票都不給這一塊（原因寫在 daily_check 的結果裡）。

起點日期、起點係數、混合權重是固定的（策略驗證線 2026-11-16 用 2025-11-03～2026-11-09 的原件求出），不每天重算；
重播不存中間狀態，同樣的原件重跑結果相同。要改這幾個數字或核對門檻先問本人。
沒處理的情況：個別股票被調降融資成數、股票股利、分割、減資（起點之後有股本變動的那一檔不給）；
現金股利不調成本（除息後借的錢沒變）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]   # 資料根目錄；長期保存區在它底下，檔名與位置由 margin_history 決定
START_DATE = '2025-11-03'              # 起點：手上最早那一天的個股融資原件
SEED_SCALE = 0.9369126317402282        # 起點係數：第一次出現的餘額沒有來歷，成本用當天均價乘這個數（讓起點的估計總額等於官方）
BLEND_WEIGHT = 0.5508671333927683      # 每日對帳用的混合權重：高成本假設佔的比重
LOAN_RATIO = 0.6                       # 融資成數六成
CHECK_THRESHOLD_PCT = 8.0              # 估計的全市場融資金額和官方差超過這麼多（%），當天所有股票都不給（本人 2026-11-16 定案 8%）
MAX_LAG_DAYS = 1                       # 融資原件最多可以比行情晚幾個交易日（官方晚上才公布，正常是晚一天）

# 重播結果的快取：{(資料根目錄, 用到的融資原件日子, 用到的行情摘要日子): (結果, 例外)}。建置時每一檔股票都會問一次，
# 資料日較舊的股票（停牌）用到的日子不同，所以留最近幾份。長期保存區的檔案存進去就不再改寫，用日子當鍵就夠。
_CACHE: dict[tuple, tuple] = {}
_CACHE_SIZE = 8


class Position(NamedTuple):
    """某一天某一檔的融資部位：官方今日餘額（張）、當天收盤、兩種假設下估計的每股成本。"""
    balance: float
    close: float
    high_cost: float
    low_cost: float


def _iso(compact: str) -> str:
    return f'{compact[:4]}-{compact[4:6]}-{compact[6:]}'


def _number(text) -> float:
    value = str(text).replace(',', '').strip()
    return float(value) if value not in ('', '--', '-') else 0.0


def kept_days(store: str, as_of: str) -> dict[str, Path]:
    """長期保存區裡起點到 as_of 之間的每日檔：{YYYY-MM-DD: 路徑}。store："raw" 是個股融資原件，"prices" 是行情摘要。"""
    import margin_history
    days = {_iso(day): path for day, path in margin_history.held_days(ROOT, store).items()}
    return {day: path for day, path in days.items() if START_DATE <= day <= as_of}


def _read_margin(path: Path) -> tuple[dict, float | None]:
    """一天的個股融資原件 → ({代號: (買進, 賣出, 現金償還, 今日餘額)}，官方公布的全市場融資金額（元）)。單位張。"""
    payload = json.loads(path.read_text(encoding='utf-8'))['data']
    trades, official = {}, None
    for table in payload.get('tables') or []:
        fields = [str(field).strip() for field in table.get('fields') or []]
        if '代號' in fields:
            # 個股表的欄位：代號 名稱｜融資 買進 賣出 現金償還 前日餘額 今日餘額 限額｜融券…（官方有兩組同名欄位，只能用位置取）
            for row in table.get('data') or []:
                trades[str(row[0]).strip()] = (_number(row[2]), _number(row[3]), _number(row[4]), _number(row[6]))
        elif '項目' in fields:
            # 信用交易統計的欄位：項目 買進 賣出 現金(券)償還 前日餘額 今日餘額；「融資金額(仟元)」那一列的今日餘額是官方總額
            for row in table.get('data') or []:
                if '融資金額' in str(row[0]):
                    official = _number(row[5]) * 1000
    return trades, official


def _read_prices(path: Path) -> dict:
    """一天的行情摘要 → {代號: (收盤, 成交均價)}。沒有成交量的用收盤當均價。"""
    quotes = json.loads(path.read_text(encoding='utf-8'))['prices']
    return {code: (close, turnover / volume if volume else close) for code, (close, volume, turnover) in quotes.items()}


def replay(raw: dict[str, Path], prices: dict[str, Path]):
    """把每一天依序重播（raw、prices 是 {YYYY-MM-DD: 路徑}）。每重播完一天交出
    （日期、那天每檔的 {代號: Position}、到那天為止曾在沒有成交均價的日子有融資進出的代號、那天官方公布的全市場融資金額）。
    那天沒有成交的、餘額為零的不在裡面。算法和策略驗證線的研究腳本逐步相同（對帳要逐檔一致）。"""
    state: dict[str, dict] = {}
    unpriced: set[str] = set()
    for day in sorted(raw):
        trades, official = _read_margin(raw[day])
        quotes = _read_prices(prices[day]) if day in prices else {}
        positions = {}
        for code, (buy, sell, repay, today) in trades.items():
            quote = quotes.get(code)
            if quote is None:
                # 當天沒有成交（拿不到成交均價）：略過這一天，之後以官方餘額對齊。那一天若有買進、賣出或現金償還，
                # 就沒有照上面兩種假設入帳，這一檔之後都不給；完全沒有進出的無成交日不影響估計。
                if buy > 0 or sell > 0 or repay > 0:
                    unpriced.add(code)
                continue
            close, average = quote
            if code not in state:
                # 第一次出現：買進以前就有的餘額沒有來歷，成本用當天均價乘起點係數。
                held = max(today - buy, 0.0)
                position = state[code] = {'cost': average * SEED_SCALE, 'quantity': held,
                                          'lots': [[average * SEED_SCALE, held]] if held > 0 else []}
            else:
                position, leaving = state[code], sell + repay
                position['quantity'] = max(position['quantity'] - leaving, 0.0)   # 高成本假設：按比例減，成本不變
                lots = position['lots']
                while leaving > 1e-9 and lots:                                    # 低成本假設：最近買的先減
                    take = min(lots[-1][1], leaving)
                    lots[-1][1] -= take
                    leaving -= take
                    if lots[-1][1] <= 1e-9:
                        lots.pop()
            if buy > 0:
                total = position['quantity'] + buy
                position['cost'] = (position['cost'] * position['quantity'] + average * buy) / total
                position['quantity'] = total
                position['lots'].append([average, buy])
            if today <= 0:
                position['quantity'], position['lots'] = 0.0, []
                continue
            held = sum(quantity for _, quantity in position['lots'])
            if held <= 0:
                # 帳對不上（沒有買進、官方卻有餘額）：低成本那一邊整筆用當天均價重建；高成本那一邊沿用原來的成本。
                position['lots'], held = [[average, today]], today
            scale = today / held   # 以官方今日餘額為準，把估計的部位等比例對齊
            for lot in position['lots']:
                lot[1] *= scale
            position['quantity'] = today
            positions[code] = Position(today, close, position['cost'],
                                       sum(cost * quantity for cost, quantity in position['lots']) / today)
        yield day, positions, unpriced, official


def market_total(positions: dict[str, Position]) -> float:
    """每日對帳用：兩個成本用固定權重合成一個，加總成估計的全市場融資金額（張 × 1,000 股 × 成本 × 融資成數）。"""
    return sum(p.balance * 1000 * (BLEND_WEIGHT * p.high_cost + (1 - BLEND_WEIGHT) * p.low_cost) * LOAN_RATIO
               for p in positions.values())


def ranges(positions: dict[str, Position], unpriced: set) -> dict:
    """{代號: (維持率下限 %, 上限 %)}：兩種成本各算一個維持率，小的是下限（下跌的股票兩者高低會反過來）。"""
    found = {}
    for code, p in positions.items():
        if code not in unpriced:
            low, high = sorted(p.close / (cost * LOAN_RATIO) * 100 for cost in (p.high_cost, p.low_cost))
            found[code] = (low, high)
    return found


def _check_result(status: str, data_date: str | None, reason: str | None = None, **more) -> dict:
    return {'status': status, 'data_date': data_date, 'gap_pct': None, 'threshold_pct': CHECK_THRESHOLD_PCT,
            'estimated': None, 'official': None, 'stocks': 0, 'reason': reason, **more}


def _estimate(as_of: str) -> tuple[dict, dict]:
    """到 as_of 為止的（每日對帳結果, {代號: (維持率下限 %, 上限 %)}）。同一批檔案只重播一次；重播出錯也記住，
    不讓每一檔股票都重播到壞掉的那一天（每一檔仍會收到同一個例外，建置報告才看得到這一塊算不出來）。"""
    raw, prices = kept_days('raw', as_of), kept_days('prices', as_of)
    key = (str(ROOT), tuple(sorted(raw)), tuple(sorted(prices)))
    if key not in _CACHE:
        while len(_CACHE) >= _CACHE_SIZE:
            del _CACHE[next(iter(_CACHE))]
        try:
            _CACHE[key] = (_estimate_from(raw, prices), None)
        except Exception as error:  # noqa: BLE001
            _CACHE[key] = (None, error)
    value, error = _CACHE[key]
    if error is not None:
        raise error
    return value


def _estimate_from(raw: dict[str, Path], prices: dict[str, Path]) -> tuple[dict, dict]:
    if START_DATE not in raw:
        return _check_result('no_start', None, f'沒有起點（{START_DATE}）的個股融資原件'), {}
    data_date = max(raw)
    # 行情摘要每個交易日一份，拿它當交易日曆：資料日以前有行情卻沒有融資原件的是缺日，資料日之後的是落後幾天。
    missing = sorted(day for day in prices if day < data_date and day not in raw)
    if missing:
        return _check_result('missing_days', data_date, f'個股融資原件缺 {len(missing)} 個交易日（{missing[0]}～{missing[-1]}）',
                             missing_days=missing), {}
    unpriced_days = sorted(day for day in raw if day not in prices)
    if unpriced_days:
        return _check_result('missing_prices', data_date, f'有 {len(unpriced_days)} 天的個股融資原件沒有行情摘要'
                             f'（{unpriced_days[0]}～{unpriced_days[-1]}）', missing_days=unpriced_days), {}
    behind = sum(1 for day in prices if day > data_date)
    if behind > MAX_LAG_DAYS:
        return _check_result('stale', data_date, f'個股融資原件停在 {data_date}，比行情晚 {behind} 個交易日'), {}
    for _, positions, unpriced, official in replay(raw, prices):
        pass   # 只要最後一天（資料日）的結果
    if not official:
        return _check_result('no_official', data_date, f'{data_date} 的原件沒有官方的全市場融資金額'), {}
    estimated = market_total(positions)
    gap_pct = (estimated / official - 1) * 100
    figures = {'gap_pct': round(gap_pct, 2) + 0.0, 'estimated': round(estimated), 'official': round(official)}
    if abs(gap_pct) > CHECK_THRESHOLD_PCT:
        reason = f'估計的全市場融資金額和官方差 {abs(gap_pct):.2f}%，超過 {CHECK_THRESHOLD_PCT:g}%'
        return _check_result('over_threshold', data_date, reason, **figures), {}
    found = ranges(positions, unpriced)
    return _check_result('ok', data_date, **figures, stocks=len(found)), found


def daily_check(as_of: str) -> dict:
    """每日對帳的結果（建置報告與站台狀態用）：status 是 ok 才有任何股票拿得到這一塊；其餘是當天全部不給的原因
    （over_threshold 差距超過門檻、missing_days 原件缺日、missing_prices 缺行情摘要、stale 原件落後太多、no_start 沒有起點、
    no_official 沒有官方總額）。"""
    return dict(_estimate(as_of)[0])


def estimated_range(*, code: str, market: str, kind: str, as_of: str, capital_events: list[dict]) -> tuple[float, float, str] | None:
    """這一檔到 as_of 為止最新一份融資原件那天的（維持率下限 %, 上限 %, 資料日）；不給就回 None。
    只給上市股票（上櫃等上櫃融資資料那一張票；ETF 第一版不顯示）。起點之後這一檔有股本變動（分割、減資、面額變更、
    配股；capital_events 是 price_adjustment.capital_events 的形狀，純現金股利不在裡面）就不給：估出來的成本沒有跟著調整。
    當天所有股票都不給的情況見 daily_check。"""
    if market != '上市' or kind == 'etf':
        return None
    check, found = _estimate(as_of)
    if code not in found or any(START_DATE < event['date'] <= check['data_date'] for event in capital_events):
        return None
    return (*found[code], check['data_date'])
