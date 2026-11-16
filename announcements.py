"""公司重大訊息：抓官方當日快照、原件留存、整理成近 90 天（個股頁事實區塊 D 票；規格 .scratch/stock-facts/spec.md）。

來源是政府資料開放平台登記的公開資訊觀測站 CSV（上市 dataset 18415、上櫃 18418，政府資料開放授權條款第 1 版）。
2026-11-16 實測：每份快照只有「前一天」的公告，隔天就被換掉，所以每天（含週末）抓到就整份存下、不刪；
網站要的「近 90 天 × 分析範圍」每次都從原件重算，同一則在不同天的快照重複出現只算一則。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data/announcements/raw"
SOURCES = {
    "上市": "https://mopsfin.twse.com.tw/opendata/t187ap04_L.csv",
    "上櫃": "https://mopsfin.twse.com.tw/opendata/t187ap04_O.csv",
}
FILE_KEY = {"上市": "listed", "上櫃": "otc"}
FIELDS = ["出表日期", "發言日期", "發言時間", "公司代號", "公司名稱", "主旨", "符合條款", "事實發生日", "說明"]
KEEP_DAYS = 90


class FormatChanged(ValueError):
    """官方檔案的欄位跟預期不同：停下來，不猜、不寫入。"""


def _western(roc: str) -> str | None:
    """民國年月日（1151115）→ 2026-11-15；空白或格式不對回 None。"""
    text = (roc or "").strip()
    if len(text) < 6 or not text.isdigit():
        return None
    return date(int(text[:-4]) + 1911, int(text[-4:-2]), int(text[-2:])).isoformat()


def _clock(text: str) -> str:
    """發言時間（70004、174706）→ 07:00:04、17:47:06。"""
    digits = (text or "").strip().zfill(6)
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:6]}"


def _clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def parse(content: bytes, *, market: str) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FormatChanged("不是 UTF-8") from error
    reader = csv.reader(io.StringIO(text))
    header = [cell.strip() for cell in next(reader, [])]
    if header != FIELDS:
        raise FormatChanged(f"欄位不同：{header[:9]}")
    rows = []
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(FIELDS):
            raise FormatChanged(f"欄數不同：{len(cells)}")
        row = dict(zip(FIELDS, cells))
        spoken = _western(row["發言日期"])
        if spoken is None:
            raise FormatChanged(f"發言日期看不懂：{row['發言日期']!r}")
        rows.append({
            "market": market, "code": row["公司代號"].strip(), "name": row["公司名稱"].strip(),
            "date": spoken, "time": _clock(row["發言時間"]),
            # 官方主旨是固定寬度硬斷行（例如「更名」後換行接「為」），接回一行；說明全文保留原本的換行。
            "subject": _clean(row["主旨"]).replace("\n", ""),
            "clause": row["符合條款"].strip(), "fact_date": _western(row["事實發生日"]), "body": _clean(row["說明"]),
        })
    return rows


def _http_get(url: str, params=None):
    import requests
    response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)

    class Result:
        status, content = response.status_code, response.content
    return Result


def fetch(raw_dir: Path = RAW_DIR, *, get: Callable | None = None, now: str | None = None) -> dict:
    """兩個市場各抓一次當日快照。內容和已存的某一份完全相同就不再存；格式不對或連不上就停下、不寫入。"""
    get = get or _http_get
    fetched_at = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp = fetched_at[:10].replace("-", "")
    known = {meta.get("sha256") for meta in (json.loads(p.read_text(encoding="utf-8")) for p in raw_dir.glob("*.json"))} \
        if raw_dir.exists() else set()
    result = {}
    for market, url in SOURCES.items():
        try:
            response = get(url, None)
            if response.status != 200:
                result[market] = {"status": "stopped", "reason": f"HTTP {response.status}"}
                continue
            rows = parse(response.content, market=market)
        except FormatChanged as error:
            result[market] = {"status": "stopped", "reason": str(error)}
            continue
        except Exception as error:  # noqa: BLE001 — 一個市場失敗只記錄，不影響另一個
            result[market] = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
            continue
        sha = hashlib.sha256(response.content).hexdigest()
        if sha in known:
            result[market] = {"status": "already-present", "rows": len(rows)}
            continue
        raw_dir.mkdir(parents=True, exist_ok=True)
        base = raw_dir / f"{FILE_KEY[market]}-{stamp}-{sha[:12]}"
        partial = base.with_suffix(".csv.partial")
        partial.write_bytes(response.content)
        os.replace(partial, base.with_suffix(".csv"))
        base.with_suffix(".json").write_text(json.dumps(
            {"market": market, "source_url": url, "fetched_at_utc": fetched_at, "sha256": sha, "rows": len(rows)},
            ensure_ascii=False), encoding="utf-8")
        known.add(sha)
        result[market] = {"status": "saved", "rows": len(rows)}
    return result


def collect(raw_dir: Path = RAW_DIR, *, as_of: str, codes: set[str], days: int = KEEP_DAYS) -> list[dict]:
    """原件裡發言日期在 as_of 往前 days 天內（含）、代號在 codes 裡的公告；新到舊、同一則只算一則。

    一則的識別：市場＋代號＋發言日期＋發言時間＋主旨（更正公告主旨不同，算另一則）。"""
    start = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    seen: dict[tuple, dict] = {}
    for meta_path in sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        csv_path = meta_path.with_suffix(".csv")
        for row in parse(csv_path.read_bytes(), market=meta["market"]):
            if row["code"] not in codes or not start <= row["date"] <= as_of:
                continue
            key = (row["market"], row["code"], row["date"], row["time"], row["subject"])
            seen.setdefault(key, {**row, "source": csv_path.name})
    return sorted(seen.values(), key=lambda r: (r["date"], r["time"]), reverse=True)


if __name__ == "__main__":
    print(json.dumps(fetch(), ensure_ascii=False))
