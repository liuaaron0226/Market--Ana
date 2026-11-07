"""虛擬成交與只能新增的 SQLite 帳本。

SQLite 能把一筆成交與帳本新增放在同一個本機交易內，且比 JSONL 更容易依帳戶、日期及
reference 查詢；本模組不提供修改或刪除 API，資料表另以 trigger 阻擋 UPDATE／DELETE。
帳戶的起始現金與持股（單位：張）只存一次，目前狀態每次由起點加上帳本增量重播推得；
append 會在同一個 ``BEGIN IMMEDIATE`` 交易內重驗不當沖、現金與持股後才新增。

助手假設、待本人確認：手續費先以「成交金額 × 0.1425% × 使用者折扣」計算，再無條件
捨去到元，每筆最低 20 元。這些規則集中在 FeePolicy，可由 execute_intent 的 fee_policy
參數替換。證交稅為賣出成交金額 0.3%（ETF 如 0050 為 0.1%，本人 11-07 定案），無條件捨去到元。

助手假設、待本人確認：可交易範圍保守限定四位數且首碼非 0 的上市普通股與 0050；
特別股及其他 ETF 均拒絕。
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Protocol


SHARES_PER_LOT = 1000
SELL_TAX_RATE = Decimal('0.003')
ETF_SELL_TAX_RATE = Decimal('0.001')


def sell_tax(code: str, gross: Decimal) -> int:
    """賣出證交稅，捨去到元。稅率依代號判定（00 開頭為 ETF），不信任外部傳入的 security_kind。"""
    rate = ETF_SELL_TAX_RATE if code.startswith('00') else SELL_TAX_RATE
    return int((gross * rate).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class FeePolicy:
    rate: Decimal = Decimal('0.001425')
    minimum: int = 20

    def calculate(self, gross_amount: Decimal, discount: Decimal) -> int:
        discounted = (gross_amount * self.rate * discount).to_integral_value(rounding=ROUND_FLOOR)
        return max(self.minimum, int(discounted))


DEFAULT_FEE_POLICY = FeePolicy()


@dataclass(frozen=True)
class TradeIntent:
    account_id: str
    code: str
    side: str
    lots: int
    decided_at: str
    strategy_version: str
    strategy_rule: str
    plain_reason: str
    gemini_input_summary: str | None = None
    gemini_response_ref: str | None = None


@dataclass(frozen=True)
class MarketQuote:
    open_price: Decimal | float | int | None
    suspended: bool
    limit_up: Decimal | float | int | None
    limit_down: Decimal | float | int | None
    kind: str = 'common'


class MarketData(Protocol):
    def next_trading_day(self, decision_date: str) -> str | None: ...

    def quote(self, date: str, code: str) -> MarketQuote | None: ...


def _tick_size(price: Decimal, kind: str) -> Decimal:
    if kind == 'etf':
        return Decimal('0.01') if price < 50 else Decimal('0.05')
    if price < 10:
        return Decimal('0.01')
    if price < 50:
        return Decimal('0.05')
    if price < 100:
        return Decimal('0.1')
    if price < 500:
        return Decimal('0.5')
    if price < 1000:
        return Decimal('1')
    return Decimal('5')


def _inward_limit(reference: Decimal, multiplier: Decimal, kind: str, *, upper: bool) -> Decimal:
    raw = reference * multiplier
    tick = _tick_size(raw, kind)
    rounding = ROUND_FLOOR if upper else ROUND_CEILING
    return (raw / tick).to_integral_value(rounding=rounding) * tick


class StockHistoryMarketData:
    """以 ``stock_history.py`` 建立的 SQLite 提供可替換市場資料介面。

    交易日只讀 ``source_days``。漲跌停價先用前一交易日收盤價 ±10%，再依台股
    升降單位向內取整。除權息日參考價不是前日收盤，此近似在 ``change_sign='X'``
    日可能不準，待後續票處理。
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def next_trading_day(self, decision_date: str) -> str | None:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                'SELECT date FROM source_days WHERE date > ? ORDER BY date LIMIT 1',
                (decision_date,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else row[0]

    def quote(self, date: str, code: str) -> MarketQuote | None:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                'SELECT open, suspended, kind, change_sign FROM daily WHERE date = ? AND code = ?',
                (date, code),
            ).fetchone()
            previous_day = connection.execute(
                'SELECT date FROM source_days WHERE date < ? ORDER BY date DESC LIMIT 1',
                (date,),
            ).fetchone()
            previous = None if previous_day is None else connection.execute(
                'SELECT close, kind FROM daily WHERE date = ? AND code = ?',
                (previous_day[0], code),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        open_price = None if row['open'] is None else _decimal(row['open'])
        if previous is None or previous['close'] is None:
            limit_up = limit_down = None
        else:
            reference = _decimal(previous['close'])
            kind = previous['kind']
            limit_up = _inward_limit(reference, Decimal('1.1'), kind, upper=True)
            limit_down = _inward_limit(reference, Decimal('0.9'), kind, upper=False)
        return MarketQuote(open_price, bool(row['suspended']), limit_up, limit_down, row['kind'])


def _decimal(value: Decimal | float | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money_text(value: Decimal) -> str:
    return format(value, 'f')


def _supported_code(code: str, kind: str | None = None) -> bool:
    if code == '0050':
        return True
    return re.fullmatch(r'[1-9]\d{3}', code) is not None and kind in (None, 'common')


class Ledger:
    """Append-only ledger. Public writes are ``create_account``, ``append`` and ``reverse`` only."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self, *, immediate: bool = False):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            if immediate:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    initial_cash TEXT NOT NULL,
                    initial_holdings TEXT NOT NULL,
                    fee_discount TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    scheduled_trade_date TEXT,
                    code TEXT NOT NULL,
                    security_kind TEXT,
                    side TEXT NOT NULL,
                    lots INTEGER NOT NULL,
                    fill_price TEXT,
                    failure_reason TEXT,
                    fee INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    strategy_version TEXT NOT NULL,
                    strategy_rule TEXT NOT NULL,
                    plain_reason TEXT NOT NULL,
                    gemini_input_summary TEXT,
                    gemini_response_ref TEXT,
                    reference_id INTEGER,
                    cash_delta TEXT NOT NULL,
                    lot_delta INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY (reference_id) REFERENCES ledger(id)
                );
                CREATE TRIGGER IF NOT EXISTS ledger_no_update
                    BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS ledger_no_delete
                    BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
            ''')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(ledger)')}
            if 'odd_share_delta' not in columns:
                connection.execute('ALTER TABLE ledger ADD COLUMN odd_share_delta INTEGER NOT NULL DEFAULT 0')

    def create_account(
        self,
        account_id: str,
        initial_cash: Decimal | float | int,
        initial_holdings: dict[str, int] | None = None,
        *,
        fee_discount: Decimal | float | int = Decimal('1'),
    ) -> None:
        holdings = initial_holdings or {}
        cash = _decimal(initial_cash)
        discount = _decimal(fee_discount)
        if cash < 0 or cash != cash.to_integral_value():
            raise ValueError('initial_cash must be a non-negative integer amount')
        if any(not isinstance(lots, int) or lots < 0 for lots in holdings.values()):
            raise ValueError('initial holdings must be non-negative lots')
        if not Decimal('0') <= discount <= Decimal('1'):
            raise ValueError('fee_discount must be between 0 and 1')
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO accounts VALUES (?, ?, ?, ?)',
                (account_id, _money_text(cash), json.dumps(holdings, sort_keys=True), _money_text(discount)),
            )

    def _account_state_in(self, connection: sqlite3.Connection, account_id: str) -> tuple[Decimal, dict[str, int]]:
        account = connection.execute('SELECT * FROM accounts WHERE account_id = ?', (account_id,)).fetchone()
        if account is None:
            raise KeyError(account_id)
        cash = Decimal(account['initial_cash'])
        holdings = {code: int(lots) for code, lots in json.loads(account['initial_holdings']).items()}
        cash, holdings, _ = self._full_state_in(connection, account_id)
        return cash, holdings

    def _full_state_in(self, connection: sqlite3.Connection, account_id: str) -> tuple[Decimal, dict[str, int], dict[str, int]]:
        account = connection.execute('SELECT * FROM accounts WHERE account_id = ?', (account_id,)).fetchone()
        cash = Decimal(account['initial_cash'])
        holdings = {code: int(lots) for code, lots in json.loads(account['initial_holdings']).items()}
        odd: dict[str, int] = {}
        for row in connection.execute(
            'SELECT code, cash_delta, lot_delta, odd_share_delta FROM ledger WHERE account_id = ? ORDER BY id',
            (account_id,),
        ):
            cash += Decimal(row['cash_delta'])
            if row['lot_delta']:
                holdings[row['code']] = holdings.get(row['code'], 0) + row['lot_delta']
                if holdings[row['code']] == 0:
                    del holdings[row['code']]
            if row['odd_share_delta']:
                odd[row['code']] = odd.get(row['code'], 0) + row['odd_share_delta']
                if odd[row['code']] == 0:
                    del odd[row['code']]
        return cash, holdings, odd

    def apply_corporate_action(
        self,
        account_id: str,
        code: str,
        ex_date: str,
        *,
        cash_per_share: Decimal | float | int = Decimal('0'),
        share_ratio: Decimal | float | int = Decimal('1'),
        plain_reason: str,
    ) -> dict:
        """除權息、分割、面額變更、減資：依「除權息前一刻」的持有股數，現金按每股入帳（捨去到元、
        不扣補充保費與所得稅），股數乘 share_ratio 後向下取整股，不足一張的部分記為零股。只能新增一筆。"""
        cash_rate = _decimal(cash_per_share)
        ratio = _decimal(share_ratio)
        if cash_rate < 0 or ratio <= 0:
            raise ValueError('cash_per_share 不得為負，share_ratio 必須大於 0')
        with self._connection(immediate=True) as connection:
            cash, holdings, odd = self._full_state_in(connection, account_id)
            shares = holdings.get(code, 0) * SHARES_PER_LOT + odd.get(code, 0)
            if shares <= 0:
                raise ValueError(f'{account_id} 未持有 {code}，不能套用公司行動')
            new_shares = int((shares * ratio).to_integral_value(rounding=ROUND_FLOOR))
            cash_delta = int((shares * cash_rate).to_integral_value(rounding=ROUND_FLOOR))
            lot_delta = new_shares // SHARES_PER_LOT - holdings.get(code, 0)
            odd_delta = new_shares % SHARES_PER_LOT - odd.get(code, 0)
            values = {
                'record_type': 'corporate_action', 'status': 'applied', 'account_id': account_id,
                'decided_at': ex_date, 'scheduled_trade_date': ex_date, 'code': code, 'security_kind': None,
                'side': 'none', 'lots': 0, 'fill_price': None, 'failure_reason': None, 'fee': 0, 'tax': 0,
                'strategy_version': '', 'strategy_rule': 'corporate_action', 'plain_reason': plain_reason,
                'gemini_input_summary': None, 'gemini_response_ref': None, 'reference_id': None,
                'cash_delta': str(cash_delta), 'lot_delta': lot_delta, 'odd_share_delta': odd_delta,
                'created_at': datetime.now(timezone.utc).isoformat(),
            }
            cursor = connection.execute(
                f'INSERT INTO ledger ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', list(values.values()),
            )
            row = connection.execute('SELECT * FROM ledger WHERE id = ?', (cursor.lastrowid,)).fetchone()
        return self._decode(row)

    @staticmethod
    def _as_unfilled(entry: dict, reason: str) -> dict:
        entry = dict(entry)
        entry.update(
            status='unfilled', fill_price=None, failure_reason=reason, fee=0, tax=0,
            cash_delta='0', lot_delta=0,
        )
        return entry

    def _prepare_entry(
        self,
        connection: sqlite3.Connection,
        entry: dict,
        fee_policy: FeePolicy,
    ) -> dict:
        cash, holdings = self._account_state_in(connection, entry['account_id'])
        record_type = entry['record_type']
        if record_type == 'reversal':
            original = connection.execute('SELECT * FROM ledger WHERE id = ?', (entry['reference_id'],)).fetchone()
            if original is None or original['record_type'] != 'trade' or original['status'] != 'filled':
                raise ValueError('只能沖銷已成交的交易紀錄')
            if connection.execute(
                'SELECT 1 FROM ledger WHERE record_type = ? AND reference_id = ?',
                ('reversal', entry['reference_id']),
            ).fetchone():
                raise ValueError(f'紀錄 {entry["reference_id"]} 已沖銷')
            same_identity = (
                entry['status'] == 'reversal'
                and entry['account_id'] == original['account_id']
                and entry['scheduled_trade_date'] == original['scheduled_trade_date']
                and entry['code'] == original['code']
                and entry['security_kind'] == original['security_kind']
                and entry['side'] == original['side']
                and entry['lots'] == original['lots']
            )
            exact_inverse = (
                _decimal(entry['cash_delta']) == -Decimal(original['cash_delta'])
                and entry['lot_delta'] == -original['lot_delta']
                and entry['fee'] == -original['fee']
                and entry['tax'] == -original['tax']
            )
            if not same_identity or not exact_inverse:
                raise ValueError('沖銷紀錄必須完整反轉原成交')
            if cash + _decimal(entry['cash_delta']) < 0 or holdings.get(entry['code'], 0) + entry['lot_delta'] < 0:
                raise ValueError('沖銷後帳戶會出現負值')
            return entry
        if record_type != 'trade':
            raise ValueError('record_type must be trade or reversal')
        if entry['reference_id'] is not None:
            raise ValueError('交易紀錄不得帶 reference_id')
        if entry['status'] == 'unfilled':
            if any((entry['fill_price'] is not None, entry['fee'] != 0, entry['tax'] != 0,
                    _decimal(entry['cash_delta']) != 0, entry['lot_delta'] != 0)):
                raise ValueError('未成交紀錄不得改變帳戶')
            return entry
        if entry['status'] != 'filled' or entry['fill_price'] is None:
            raise ValueError('交易紀錄狀態無效')
        if entry['side'] not in ('buy', 'sell') or entry['lots'] <= 0:
            raise ValueError('成交方向或張數無效')
        if not _supported_code(entry['code'], entry['security_kind']):
            raise ValueError('標的不在可交易範圍')
        if entry['fee'] < 0 or entry['tax'] < 0:
            raise ValueError('成交費稅不得為負')
        price = _decimal(entry['fill_price'])
        gross = price * entry['lots'] * SHARES_PER_LOT
        discount = Decimal(connection.execute(
            'SELECT fee_discount FROM accounts WHERE account_id = ?', (entry['account_id'],),
        ).fetchone()[0])
        required_fee = fee_policy.calculate(gross, discount)
        if entry['fee'] != required_fee:
            raise ValueError(f'手續費必須是 {required_fee} 元')
        required_tax = 0 if entry['side'] == 'buy' else sell_tax(entry['code'], gross)
        if entry['tax'] != required_tax:
            raise ValueError(f'證交稅必須是 {required_tax} 元')
        if entry['side'] == 'buy':
            expected_cash, expected_lots = -(gross + entry['fee']), entry['lots']
        else:
            expected_cash, expected_lots = gross - entry['fee'] - entry['tax'], -entry['lots']
        if _decimal(entry['cash_delta']) != expected_cash or entry['lot_delta'] != expected_lots:
            raise ValueError('帳本增量與成交欄位不一致')
        opposite = 'sell' if entry['side'] == 'buy' else 'buy'
        opposite_fill = connection.execute(
            '''SELECT 1 FROM ledger AS trade
               WHERE trade.account_id = ? AND trade.status = 'filled'
                 AND trade.scheduled_trade_date = ? AND trade.code = ? AND trade.side = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM ledger AS reversal
                     WHERE reversal.record_type = 'reversal' AND reversal.reference_id = trade.id
                 )
               LIMIT 1''',
            (entry['account_id'], entry['scheduled_trade_date'], entry['code'], opposite),
        ).fetchone()
        if opposite_fill:
            return self._as_unfilled(entry, '同日已有反向成交，不允許當沖')
        if cash + expected_cash < 0:
            return self._as_unfilled(entry, '現金不足')
        if holdings.get(entry['code'], 0) + expected_lots < 0:
            return self._as_unfilled(entry, '持股不足，不允許放空')
        return entry

    def append(
        self,
        record: dict,
        *,
        fee_policy: FeePolicy = DEFAULT_FEE_POLICY,
    ) -> dict:
        columns = (
            'record_type', 'status', 'account_id', 'decided_at', 'scheduled_trade_date', 'code', 'security_kind', 'side',
            'lots', 'fill_price', 'failure_reason', 'fee', 'tax', 'strategy_version', 'strategy_rule',
            'plain_reason', 'gemini_input_summary', 'gemini_response_ref', 'reference_id', 'cash_delta',
            'lot_delta', 'created_at',
        )
        entry = dict(record)
        entry.setdefault('created_at', datetime.now(timezone.utc).isoformat())
        with self._connection(immediate=True) as connection:
            entry = self._prepare_entry(connection, entry, fee_policy)
            cursor = connection.execute(
                f'INSERT INTO ledger ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                [entry[column] for column in columns],
            )
            row = connection.execute('SELECT * FROM ledger WHERE id = ?', (cursor.lastrowid,)).fetchone()
        return self._decode(row)

    def read(self, record_id: int) -> dict:
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM ledger WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return self._decode(row)

    def records(self, account_id: str | None = None) -> list[dict]:
        query = 'SELECT * FROM ledger'
        parameters: tuple[str, ...] = ()
        if account_id is not None:
            query += ' WHERE account_id = ?'
            parameters = (account_id,)
        query += ' ORDER BY id'
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]

    def reverse(self, record_id: int, *, decided_at: str, plain_reason: str) -> dict:
        """以一筆新紀錄沖銷原紀錄；原列不會被修改。"""
        original = self.read(record_id)
        if original['record_type'] != 'trade' or original['status'] != 'filled':
            raise ValueError('只能沖銷已成交的交易紀錄')
        if any(record['reference_id'] == record_id for record in self.records(original['account_id'])):
            raise ValueError(f'紀錄 {record_id} 已沖銷')
        record = {
            'record_type': 'reversal',
            'status': 'reversal',
            'account_id': original['account_id'],
            'decided_at': decided_at,
            'scheduled_trade_date': original['scheduled_trade_date'],
            'code': original['code'],
            'security_kind': original['security_kind'],
            'side': original['side'],
            'lots': original['lots'],
            'fill_price': None if original['fill_price'] is None else _money_text(_decimal(original['fill_price'])),
            'failure_reason': f'沖銷紀錄 {record_id}',
            'fee': -original['fee'],
            'tax': -original['tax'],
            'strategy_version': original['strategy_version'],
            'strategy_rule': original['strategy_rule'],
            'plain_reason': plain_reason,
            'gemini_input_summary': original['gemini_input_summary'],
            'gemini_response_ref': original['gemini_response_ref'],
            'reference_id': record_id,
            'cash_delta': _money_text(-_decimal(original['cash_delta'])),
            'lot_delta': -original['lot_delta'],
        }
        return self.append(record)

    def _decode(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        record['fill_price'] = None if record['fill_price'] is None else float(Decimal(record['fill_price']))
        record['cash_delta'] = int(Decimal(record['cash_delta']))
        return record

    def _account(self, account_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM accounts WHERE account_id = ?', (account_id,)).fetchone()
        if row is None:
            raise KeyError(account_id)
        return dict(row)

    def account_state(self, account_id: str) -> dict:
        with self._connection() as connection:
            self._account_state_in(connection, account_id)
            cash, holdings, odd = self._full_state_in(connection, account_id)
        state = {'cash': int(cash), 'holdings': holdings}
        if odd:
            state['odd_shares'] = odd
        return state

    def fee_discount(self, account_id: str) -> Decimal:
        return Decimal(self._account(account_id)['fee_discount'])


def _base_record(
    intent: TradeIntent,
    scheduled_trade_date: str | None,
    security_kind: str | None = None,
) -> dict:
    return {
        'record_type': 'trade',
        'account_id': intent.account_id,
        'decided_at': intent.decided_at,
        'scheduled_trade_date': scheduled_trade_date,
        'code': intent.code,
        'security_kind': security_kind,
        'side': intent.side,
        'lots': intent.lots,
        'strategy_version': intent.strategy_version,
        'strategy_rule': intent.strategy_rule,
        'plain_reason': intent.plain_reason,
        'gemini_input_summary': intent.gemini_input_summary,
        'gemini_response_ref': intent.gemini_response_ref,
        'reference_id': None,
    }


def _append_unfilled(
    ledger: Ledger,
    intent: TradeIntent,
    scheduled_trade_date: str | None,
    reason: str,
    *,
    security_kind: str | None = None,
) -> dict:
    record = _base_record(intent, scheduled_trade_date, security_kind)
    record.update(
        status='unfilled', fill_price=None, failure_reason=reason, fee=0, tax=0,
        cash_delta='0', lot_delta=0,
    )
    return ledger.append(record)


def execute_intent(
    ledger: Ledger,
    market: MarketData,
    intent: TradeIntent,
    *,
    fee_policy: FeePolicy = DEFAULT_FEE_POLICY,
) -> dict:
    """用決定日後第一個交易日的開盤價撮合一筆意圖，並必定 append 一筆結果。"""
    if intent.side not in ('buy', 'sell'):
        raise ValueError('side must be buy or sell')
    if intent.lots <= 0:
        raise ValueError('lots must be positive')
    decision_date = intent.decided_at[:10]
    scheduled = market.next_trading_day(decision_date)
    if scheduled is None:
        return _append_unfilled(ledger, intent, None, '下一交易日缺漏')
    if not _supported_code(intent.code):
        return _append_unfilled(ledger, intent, scheduled, '標的不在可交易範圍')
    quote = market.quote(scheduled, intent.code)
    if quote is None:
        return _append_unfilled(ledger, intent, scheduled, '市場資料缺漏')
    if not _supported_code(intent.code, quote.kind):
        return _append_unfilled(
            ledger, intent, scheduled, '標的不在可交易範圍', security_kind=quote.kind,
        )
    if quote.suspended:
        return _append_unfilled(ledger, intent, scheduled, '停牌', security_kind=quote.kind)
    if quote.open_price is None:
        return _append_unfilled(ledger, intent, scheduled, '開盤價缺漏')
    if intent.side == 'buy' and quote.limit_up is None:
        return _append_unfilled(ledger, intent, scheduled, '漲停價缺漏')
    if intent.side == 'sell' and quote.limit_down is None:
        return _append_unfilled(ledger, intent, scheduled, '跌停價缺漏')
    price = _decimal(quote.open_price)
    if intent.side == 'buy' and price >= _decimal(quote.limit_up):
        return _append_unfilled(ledger, intent, scheduled, '開盤即漲停，買單未成交')
    if intent.side == 'sell' and quote.limit_down is not None and price <= _decimal(quote.limit_down):
        return _append_unfilled(ledger, intent, scheduled, '開盤即跌停，賣單未成交')
    gross = price * intent.lots * SHARES_PER_LOT
    fee = fee_policy.calculate(gross, ledger.fee_discount(intent.account_id))
    record = _base_record(intent, scheduled, quote.kind)
    if intent.side == 'buy':
        tax = 0
        cash_delta = -(gross + fee)
        lot_delta = intent.lots
    else:
        tax = sell_tax(intent.code, gross)
        cash_delta = gross - fee - tax
        lot_delta = -intent.lots
    record.update(
        status='filled', fill_price=_money_text(price), failure_reason=None, fee=fee, tax=tax,
        cash_delta=_money_text(cash_delta), lot_delta=lot_delta,
    )
    return ledger.append(record, fee_policy=fee_policy)
