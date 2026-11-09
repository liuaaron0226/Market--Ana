"""官方上櫃普通股名錄與既有價量資料資格。

名錄分類只採臺灣證券交易所 ISIN 名錄的市場別與 CFICode；不以代號長度、
前綴或名稱猜普通股。價量資格只讀既有 SQLite，不修改或補寫歷史資料。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


COMMON_STOCK_CFI_PREFIX = "ESV"


@dataclass(frozen=True)
class OtcSecurity:
    code: str
    name: str
    market: str
    security_type: str
    isin: str
    listed_date: str
    industry: str
    cfi_code: str
    source_url: str
    source_date: str
    available_bars: int
    latest_bar_date: str | None
    analysis_eligible: bool


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td" and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None


class OtcCatalog:
    def __init__(self, entries: dict[str, tuple[str, str, str, str, str, str]], source_url: str,
                 source_date: str, history_db: Path) -> None:
        self._entries = entries
        self._source_url = source_url
        self._source_date = source_date
        self._history_db = history_db

    def __len__(self) -> int:
        return len(self._entries)

    def lookup(self, code: str) -> OtcSecurity | None:
        entry = self._entries.get(code)
        if entry is None:
            return None
        name, isin, listed_date, market, industry, cfi_code = entry
        connection = sqlite3.connect(f"file:{self._history_db.as_posix()}?mode=ro", uri=True)
        try:
            available_bars, latest_bar_date = connection.execute(
                """SELECT COUNT(*), MAX(date) FROM daily
                   WHERE code = ? AND open IS NOT NULL AND close IS NOT NULL""",
                (code,),
            ).fetchone()
        finally:
            connection.close()
        return OtcSecurity(
            code=code,
            name=name,
            market=market,
            security_type="普通股",
            isin=isin,
            listed_date=listed_date,
            industry=industry,
            cfi_code=cfi_code,
            source_url=self._source_url,
            source_date=self._source_date,
            available_bars=available_bars,
            latest_bar_date=latest_bar_date,
            analysis_eligible=available_bars >= 20,
        )


def load_otc_catalog(raw_html: Path, metadata_json: Path, history_db: Path) -> OtcCatalog:
    metadata = json.loads(metadata_json.read_text(encoding="utf-8"))
    raw = raw_html.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
        raise ValueError("官方名錄原件 SHA-256 與來源中繼資料不符")
    parser = _TableRows()
    parser.feed(raw.decode("cp950"))
    entries: dict[str, tuple[str, str, str, str, str, str]] = {}
    for row in parser.rows:
        if len(row) != 7 or row[3] != "上櫃" or not row[5].startswith(COMMON_STOCK_CFI_PREFIX):
            continue
        code_and_name = row[0].split(maxsplit=1)
        if len(code_and_name) != 2:
            continue
        code, name = code_and_name
        entries[code] = (name, row[1], row[2], row[3], row[4], row[5])
    source_date = metadata.get("data_date", metadata["retrieved_at_utc"][:10])
    return OtcCatalog(entries, metadata["source_url"], source_date, history_db)
