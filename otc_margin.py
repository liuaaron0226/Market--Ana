"""上櫃個股融資融券的每日原件保存區（自選股關注 I 票）。

專案原本只抓上市個股融資（證交所 MI_MARGN，見 ``margin_history.py``）；上櫃這一份從櫃買 OpenAPI
``/tpex_mainboard_margin_balance`` 抓。它只回最新一日（有日期欄），所以從開始抓那天起逐日累積：
``data/otc-margin/raw/margin-YYYYMMDD.json``，外包和上市那一份相同（抓取時間、來源網址、官方回應放在 data）。

存進來的日子不再改寫；回應不是預期的形狀（查無資料、混了好幾天、缺欄位）一律不收、不寫入。

條款：櫃買這一份的資料集授權頁還沒找到（2026-11-16 查 data.gov.tw 沒有結果），照規格「照樣抓、照樣給站主看、
不放進提示」——不放進提示由關注帳本把關（``watch_ledger.PROMPT_MARGIN_SOURCES``）。

``fetch_today`` 只抓一次、存一份；排進每日排程是 G 票的事，這裡不接排程。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
STORE_DIR = "data/otc-margin/raw"
DAILY_FILE = re.compile(r"margin-(\d{8})\.json", re.ASCII)
REQUIRED_FIELDS = ("Date", "SecuritiesCompanyCode", "MarginPurchaseBalance", "ShortSaleBalance")


def _roc_day(text: str) -> str:
    """民國年「1151113」→「20261113」。"""
    if not re.fullmatch(r"\d{7}", text):
        raise ValueError(f"日期不是民國年七碼：{text!r}")
    return f"{int(text[:3]) + 1911:04d}{text[3:]}"


def parse(content: bytes) -> tuple[str, list[dict[str, Any]]]:
    """（YYYYMMDD, 每一檔一列）。不是預期的形狀就丟 ValueError。"""
    try:
        rows = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"官方回應不是 JSON（{type(error).__name__}）") from error
    if not isinstance(rows, list) or not rows:
        raise ValueError("官方回應是查無資料")
    if any(not isinstance(row, dict) or any(field not in row for field in REQUIRED_FIELDS) for row in rows):
        raise ValueError("官方回應缺少必要欄位")
    days = {str(row["Date"]) for row in rows}
    if len(days) != 1:
        raise ValueError("官方回應混了不只一天")
    return _roc_day(days.pop()), rows


def stored_days(root: Path) -> dict[str, Path]:
    """保存區裡的每一天：{YYYYMMDD: 路徑}。"""
    folder = Path(root) / STORE_DIR
    if not folder.is_dir():
        return {}
    return {match.group(1): path for path in folder.iterdir() if (match := DAILY_FILE.fullmatch(path.name))}


def store(root: Path, content: bytes, *, fetched_at: str, source_url: str) -> dict[str, str]:
    """把一份官方回應存到它自己那一天；那一天已經有了就不動（``existing``）。"""
    day, rows = parse(content)
    target = Path(root) / STORE_DIR / f"margin-{day}.json"
    if target.exists():
        return {"day": day, "outcome": "existing"}
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"fetched_at_utc": fetched_at, "source_url": source_url, "data": rows}
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, target)
    return {"day": day, "outcome": "stored"}


def _default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_today(root: Path, *, fetch: Callable[[str], bytes] = _default_fetch,
                now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")) -> dict[str, str]:
    """抓一次櫃買的最新一日並存下。"""
    return store(root, fetch(URL), fetched_at=now(), source_url=URL)
