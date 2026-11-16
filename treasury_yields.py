"""美國公債 2 年、10 年殖利率的原件保存區（自選股關注 I 票；大盤預測 D 票第 9 片共用同一份，不各抓各的）。

來源是美國財政部每日 par yield curve 的 XML，一個月一份：
``https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM``
存成 ``data/treasury-yields/raw/ust-YYYYMM.xml``，旁邊一份 ``.meta.json``（抓取時間、來源網址、雜湊）。
當月的那一份每天會多幾筆，所以同一個月重抓時整份換新，但筆數不能比已存的少（官方回應短少時不收）。

格式（2026-11 實際抓到的）：Atom feed，每筆 ``entry/content/m:properties`` 底下 ``d:NEW_DATE``、``d:BC_2YEAR``、
``d:BC_10YEAR``；官方留空的值整個元素不出現（財政部 XML 改版公告 ``/developer-notice-xml-changes``，2026-11-16 讀過：
只有新增欄位與網址異動，沒有停用這份 XML）。

條款：catalog.data.gov 上財政部每日殖利率標 CC0（公有領域；那筆目錄是 2014 年的），財政部網站本身沒寫重用條款。
【助手判斷】可以放進提示。不可用 FRED（條款禁止存檔）。

``fetch_month`` 只抓一個月、存一份；排進每日排程是自選股關注 G 票（與大盤預測那條線）的事，這裡不接排程。
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from level_signals import HostPacer, atomic_write

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
       "?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}")
STORE_DIR = "data/treasury-yields/raw"
MONTH_FILE = re.compile(r"ust-(\d{6})\.xml", re.ASCII)
_ATOM = "{http://www.w3.org/2005/Atom}"
_D = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
_M = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"


def _number(element: ET.Element | None) -> float | None:
    if element is None or not (element.text or "").strip():
        return None
    return float(element.text)


def parse(content: bytes) -> list[dict[str, Any]]:
    """每一個交易日的 2 年、10 年殖利率（百分比；官方留空是 None），依日期排序。不是預期的格式丟 ValueError。"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("官方回應不是 XML") from error
    if root.tag != f"{_ATOM}feed":
        raise ValueError("官方回應不是殖利率的 Atom feed")
    rows = []
    for properties in root.iter(f"{_M}properties"):
        day = properties.find(f"{_D}NEW_DATE")
        if day is None or not re.match(r"\d{4}-\d{2}-\d{2}", day.text or ""):
            raise ValueError("有一筆缺日期")
        rows.append({"date": day.text[:10], "yield_2y": _number(properties.find(f"{_D}BC_2YEAR")),
                     "yield_10y": _number(properties.find(f"{_D}BC_10YEAR"))})
    dates = [row["date"] for row in rows]
    if len(set(dates)) != len(dates):
        raise ValueError("同一天出現兩筆")
    return sorted(rows, key=lambda row: row["date"])


def month_url(month: str) -> str:
    return URL.format(month=month)


def store_month(root: Path, month: str, content: bytes, *, fetched_at: str, source_url: str) -> str:
    """存一個月份的原件；回傳 ``stored``（新存或換成筆數更多的一份）或 ``unchanged``（和已存的一樣）。"""
    if not re.fullmatch(r"\d{6}", month):
        raise ValueError(f"月份要是 YYYYMM：{month!r}")
    rows = parse(content)
    if not rows:
        raise ValueError("官方回應沒有任何一筆")
    if any(row["date"][:7].replace("-", "") != month for row in rows):
        raise ValueError("官方回應的日期不在這個月")
    folder = Path(root) / STORE_DIR
    target = folder / f"ust-{month}.xml"
    if target.exists():
        held = target.read_bytes()
        if held == content:
            return "unchanged"
        try:
            held_rows = len(parse(held))
        except ValueError:     # 已存的那一份壞了（不是這裡寫壞的）：讓新的一份換上，不要整個月卡住
            held_rows = 0
        if len(rows) < held_rows:
            raise ValueError("官方回應的筆數比已存的少，不收")
    # 原件先換、說明檔後換：讀的時候只看原件，說明檔只記抓取時間、來源與雜湊。
    atomic_write(target, content)
    atomic_write(folder / f"ust-{month}.meta.json", json.dumps({
        "fetched_at_utc": fetched_at, "source_url": source_url, "sha256": hashlib.sha256(content).hexdigest(),
        "rows": len(rows)}, ensure_ascii=False).encode("utf-8"))
    return "stored"


def load(root: Path) -> list[dict[str, Any]]:
    """保存區裡全部月份的每一筆，依日期排序。"""
    folder = Path(root) / STORE_DIR
    rows: list[dict[str, Any]] = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if MONTH_FILE.fullmatch(path.name):
                rows.extend(parse(path.read_bytes()))
    return sorted(rows, key=lambda row: row["date"])


def _default_fetch(url: str) -> bytes:
    return HostPacer().fetch(url, timeout=60, accept="application/xml")


def fetch_month(root: Path, month: str, *, fetch: Callable[[str], bytes] = _default_fetch,
                now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")) -> str:
    """抓一個月份並存下。"""
    url = month_url(month)
    return store_month(root, month, fetch(url), fetched_at=now(), source_url=url)
