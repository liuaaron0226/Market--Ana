"""個股 5 年歷史日線：算出每檔的還原日線，並產生寫進 D1 `stock_history` 的 SQL。

本人 2026-11-14 定案：範圍先做約 5 年（雲端日線庫保留的 1,270 個交易日）；價格口徑與個股分析相同；
放進資料庫，每檔一列、每天覆寫。個股分析 payload 仍只帶最近 120 根，這裡給圖往前拉用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twon_analysis.data import load_bars  # noqa: E402
from twon_analysis.indicators import Bar  # noqa: E402
from twon_analysis.price_adjustment import adjust_bars, default_capital_events  # noqa: E402

SCHEMA = "stock-history/v1"
HISTORY_BARS = 1270          # 與 state_sync.DAILY_KEEP_DAYS 相同：雲端日線庫只留這麼多
MAX_STATEMENT_BYTES = 90_000  # D1 單一語句上限 100KB；與 import_d1_seed 相同留 10KB 餘裕


def history_payload(code: str, bars: list[Bar]) -> dict:
    """欄位式（每個欄位一條陣列）比逐根物件小一半以上；日期存成 YYYYMMDD 整數。"""
    return {"schema": SCHEMA, "code": code, "as_of": bars[-1].date,
            "d": [int(bar.date.replace("-", "")) for bar in bars],
            "o": [bar.open for bar in bars], "h": [bar.high for bar in bars], "l": [bar.low for bar in bars],
            "c": [bar.close for bar in bars], "v": [bar.volume for bar in bars]}


def build_history(codes: list[str], as_of: str, *, loader: Callable = load_bars,
                  capital_actions: dict[str, list[dict]] | None = None) -> dict[str, dict]:
    """每檔最近 HISTORY_BARS 根的還原日線；查不到日線的代號略過。"""
    if capital_actions is None:
        capital_actions = default_capital_events()
    built = {}
    for code in codes:
        bars, _info = loader(code, as_of, limit=HISTORY_BARS)
        if not bars:
            continue
        adjusted, _unresolved = adjust_bars(bars, capital_actions.get(code, []))
        built[code] = history_payload(code, adjusted)
    return built


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def history_sql(payloads: dict[str, dict], data_date: str) -> tuple[list[str], list[str]]:
    """回傳（SQL 語句清單，因超過單句上限而略過的代號）。

    每檔一句 upsert；最後一句刪掉今天沒有更新到的列（離開母體的股票不留舊資料）。
    """
    statements, skipped = [], []
    for code, payload in payloads.items():
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        statement = ("INSERT INTO stock_history (code, data_date, payload) VALUES "
                     f"({_literal(code)}, {_literal(data_date)}, {_literal(body)}) "
                     "ON CONFLICT(code) DO UPDATE SET data_date = excluded.data_date, payload = excluded.payload;")
        if len(statement.encode("utf-8")) > MAX_STATEMENT_BYTES:
            skipped.append(code)  # 不截斷：這檔今天沒有歷史，圖維持最近 120 根
            continue
        statements.append(statement)
    if statements:
        statements.append(f"DELETE FROM stock_history WHERE data_date < {_literal(data_date)};")
    return statements, skipped
