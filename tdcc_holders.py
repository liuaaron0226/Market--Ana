"""集保戶股權分散表 → 大戶持股（本人 2026-11-14「可以，用集保」）。

主力買賣超要用券商分點資料，官方查詢有驗證碼且條款禁止自動下載，付費來源又與「不付費」衝突；
改用集保每週公布的股權分散表（政府資料開放授權、免驗證碼）看大戶持股變化。
開放資料檔只有最新一週，所以每次抓到新的一週就存成一個小檔，歷史自己累積。

持股分級（集保固定 17 級）：第 12～14 級是 400～1,000 張，第 15 級是 1,000 張以上，
第 16 級是差異數調整，第 17 級是合計。
"""
from __future__ import annotations

import csv
import functools
import io
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Callable

from stock_history import http_get

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "data/tdcc-holders"
URL = "https://opendata.tdcc.com.tw/getOD.ashx"
PARAMS = {"id": "1-5"}
FIELDS = ["資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%"]
# 只留股票與 ETF（4 碼，或 00 開頭的 ETF）；權證、特別股代號等不是個股頁的對象。
STOCK_CODE = re.compile(r"^(?:[1-9][0-9]{3}|00[0-9]{2,4}[A-Z]?)$")
SCHEMA = "tdcc-holders/v1"
# 過去一年由官方查詢頁補抓（tdcc_backfill.py）後入庫的歷史；每週新抓到的仍存 weekly/，同一週以 weekly 為準。
DEFAULT_HISTORY = ROOT / "seeds/tdcc-holders-history.json"
HISTORY_SCHEMA = "tdcc-holders-history/v1"


def parse(content: bytes) -> tuple[str, dict[str, dict]]:
    """回傳（資料日 YYYY-MM-DD，每檔的大戶持股）。欄位不符或混了不同日期就拒絕。"""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("集保檔案不是 UTF-8") from error
    reader = csv.reader(io.StringIO(text))
    if next(reader, None) != FIELDS:
        raise ValueError("集保股權分散表欄位不符")
    days: set[str] = set()
    levels: dict[str, dict[int, tuple[int, float]]] = {}
    for row in reader:
        if not row:
            continue
        if len(row) != len(FIELDS):
            raise ValueError(f"集保資料列欄位數不符：{row[:3]}")
        days.add(row[0].strip())
        code = row[1].strip()
        if STOCK_CODE.fullmatch(code):
            levels.setdefault(code, {})[int(row[2])] = (int(row[3]), float(row[5]))
    if len(days) != 1:
        raise ValueError(f"集保檔案應只有一個資料日，實際 {sorted(days)[:3]}")
    compact = days.pop()
    day = date(int(compact[:4]), int(compact[4:6]), int(compact[6:8])).isoformat()
    holders = {}
    for code, by_level in levels.items():
        if 15 not in by_level or 17 not in by_level:
            continue  # 沒有完整級距的不算，寧可缺也不拿殘缺資料
        big = [by_level.get(level, (0, 0.0)) for level in (12, 13, 14, 15)]
        holders[code] = {"pct_1000": by_level[15][1], "holders_1000": by_level[15][0],
                         "pct_400": round(sum(pct for _count, pct in big), 2),
                         "holders_400": sum(count for count, _pct in big), "holders": by_level[17][0]}
    return day, holders


def refresh(out: Path = DEFAULT_OUT, *, get: Callable = http_get) -> dict:
    """抓最新一週；是新的一週就存成 weekly/<資料日>.json，已經有的那一週不覆寫。"""
    try:
        response = get(URL, PARAMS)
    except Exception as error:  # noqa: BLE001 — 連線錯誤即停，不重試
        return {"status": "stopped", "reason": f"連線錯誤：{type(error).__name__}", "requests": 1}
    if response.status != 200:
        return {"status": "stopped", "reason": f"HTTP {response.status}", "requests": 1}
    try:
        day, holders = parse(response.content)
    except ValueError as error:
        return {"status": "stopped", "reason": str(error), "requests": 1}
    target = out / "weekly" / f"{day}.json"
    if target.exists():
        stored = json.loads(target.read_text(encoding="utf-8"))
        return {"status": "already-present", "requests": 1, "data_date": day, "stocks": len(stored["holders"])}
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps({"schema": SCHEMA, "data_date": day, "holders": holders}, ensure_ascii=False,
                                  separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(partial, target)
    return {"status": "appended", "requests": 1, "data_date": day, "stocks": len(holders)}


@functools.lru_cache(maxsize=4)
def _weeks(folder: Path, stamps: tuple) -> list[tuple[str, dict]]:
    return [(path.stem, json.loads(path.read_text(encoding="utf-8"))["holders"]) for path in sorted(folder.glob("*.json"))]


@functools.lru_cache(maxsize=2)
def _history(path: Path, stamp: int) -> dict[str, dict[str, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != HISTORY_SCHEMA or not isinstance(payload.get("weeks"), dict):
        raise ValueError(f"大戶持股歷史檔格式不符：{path.name}")
    fields = payload["fields"]
    return {day: {code: dict(zip(fields, values)) for code, values in week.items()} for day, week in payload["weeks"].items()}


def series(out: Path, code: str, as_of: str, history: Path | None = None) -> list[dict]:
    """某一檔逐週的大戶持股（舊到新，只到 as_of 為止）；那一週沒有它就略過。

    history 是補抓的歷史檔；同一週兩邊都有時用每週存下的那一份。
    """
    weeks: dict[str, dict] = {}
    if history is not None and history.is_file():
        weeks.update({day: week[code] for day, week in _history(history, history.stat().st_mtime_ns).items() if code in week})
    folder = out / "weekly"
    if folder.is_dir():
        stamps = tuple((path.name, path.stat().st_mtime_ns) for path in sorted(folder.glob("*.json")))
        weeks.update({day: holders[code] for day, holders in _weeks(folder, stamps) if code in holders})
    return [{"date": day, **weeks[day]} for day in sorted(weeks) if day <= as_of]
