"""櫃買中心「除權除息計算結果表」原件與標準公司行動事件。

官方來源：``/www/zh-tw/bulletin/exDailyQ``。輸出事件沿用
``data/corporate-actions/events.json`` 的欄位與既有 ``除權``／``除息``／``除權息`` type。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple


EXDAILYQ_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
FIELDS = [
    "除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值",
    "權/息", "漲停價", "跌停價", "開始交易基準價", "減除股利參考價", "現金股利", "每仟股無償配股",
    "現金增資股數", "現金增資認購價", "公開承銷股數", "員工認購股數", "原股東認購股數", "按持股比例仟股認購",
]
VALUE_FIELD = {"除權": "權值", "除息": "息值", "除權息": "權值+息值"}
_MISSING = {"", "-", "--", "---", "N/A"}


def roc_date_to_iso(value: str) -> str:
    parts = value.strip().split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"民國日期格式不正確：{value!r}")
    year, month, day = int(parts[0]) + 1911, int(parts[1]), int(parts[2])
    return date(year, month, day).isoformat()


def _number(value: Any) -> float | None:
    text = str(value).replace(",", "").strip()
    return None if text in _MISSING else float(text)


def parse_exdailyq(content: bytes, *, source: str) -> list[dict[str, Any]]:
    """把櫃買 JSON 轉成既有 events.json 事件；ETF 與普通股都保留。"""
    payload = json.loads(content.decode("utf-8"))
    if str(payload.get("stat", "")).lower() != "ok":
        raise ValueError(f"櫃買回應狀態不是 ok：{payload.get('stat')!r}")
    table = next((table for table in payload.get("tables", []) if table.get("fields") == FIELDS), None)
    if table is None:
        raise ValueError("櫃買除權除息 JSON 欄位不符")
    rows = table.get("data") or []
    if table.get("totalCount") is not None and int(table["totalCount"]) != len(rows):
        raise ValueError(f"筆數不符：註記 {table['totalCount']}，原始列 {len(rows)}")

    events: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if len(raw) != len(FIELDS):
            raise ValueError(f"櫃買除權除息資料列欄位數 {len(raw)}，預期 {len(FIELDS)}")
        cell = dict(zip(FIELDS, raw))
        action_type = str(cell["權/息"]).strip()
        if action_type not in VALUE_FIELD:
            raise ValueError(f"未知權/息類型：{action_type!r}")
        event = {
            "date": roc_date_to_iso(str(cell["除權息日期"])),
            "code": str(cell["代號"]).strip(),
            "type": action_type,
            "close_before": _number(cell["除權息前收盤價"]),
            "reference": _number(cell["除權息參考價"]),
            "value": _number(cell[VALUE_FIELD[action_type]]),
            "limit_up": _number(cell["漲停價"]),
            "limit_down": _number(cell["跌停價"]),
            "source": source,
        }
        key = (event["code"], event["date"])
        previous = events.get(key)
        if previous is not None and previous != event:
            raise ValueError(f"同一代號／日期有衝突事件：{key[0]} {key[1]}")
        events[key] = event
    return sorted(events.values(), key=lambda event: (event["date"], event["code"]))


MIN_INTERVAL_SECONDS = 5.0
SOURCE_PREFIX = "TPEx-exDailyQ-"
ROOT = Path(__file__).resolve().parent
DEFAULT_RAW_DIR = ROOT / "data/corporate-actions/raw/TPEx-exDailyQ"
DEFAULT_EVENTS = ROOT / "data/corporate-actions/events.json"


class Response(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


def http_get(session: object, url: str, params: dict) -> Response:
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    return Response(response.status_code, response.headers.get("content-type", ""), response.content, response.url)


def _write_atomic(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(content)
    os.replace(partial, target)


def _write_meta(out: Path, files: dict[str, dict[str, Any]]) -> None:
    meta = {
        "dataset": "TPEx-exDailyQ",
        "source_url": EXDAILYQ_URL,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [files[key] for key in sorted(files)],
    }
    _write_atomic(out / "meta.json", (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _slash(day: str) -> str:
    return date.fromisoformat(day).strftime("%Y/%m/%d")


def fetch_periods(
    out: Path,
    periods: list[tuple[str, str, str]],
    *,
    get: Callable[[object, str, dict], Response] = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """逐期間抓官方 JSON；同 host 實際請求至少相隔五秒，失敗立即停止且不重試。"""
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"requested": 0, "fetched": 0, "existing": 0, "stopped": None}
    meta_path = out / "meta.json"
    raw_exists = any(out.glob(f"{SOURCE_PREFIX}*.json"))
    try:
        files = {entry["key"]: entry for entry in json.loads(meta_path.read_text(encoding="utf-8"))["files"]}
    except FileNotFoundError:
        if raw_exists:
            result["stopped"] = {"key": None, "reason": "已有原件但缺少 meta.json；停止以免遺失歷史索引"}
            return result
        files = {}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        result["stopped"] = {"key": None, "reason": "meta.json 格式損毀；停止以免覆寫歷史索引"}
        return result
    session = None
    if get is http_get:
        import requests
        session = requests.Session()
    first_request = True
    log_path = out / "fetch-log.jsonl"
    try:
        with log_path.open("a", encoding="utf-8") as log:
            for key, start, end in periods:
                date.fromisoformat(start)
                date.fromisoformat(end)
                if start > end:
                    raise ValueError(f"期間起日晚於迄日：{start} > {end}")
                filename = f"{SOURCE_PREFIX}{key}.json"
                target = out / filename
                sidecar = target.with_name(filename + ".sha256")
                if target.exists():
                    content = target.read_bytes()
                    digest = hashlib.sha256(content).hexdigest()
                    parts = sidecar.read_text(encoding="ascii").split() if sidecar.exists() else []
                    if parts[:2] != [digest, filename]:
                        result["stopped"] = {"key": key, "reason": "既有原件 SHA-256 sidecar 不符"}
                        break
                    recorded = files.get(key)
                    if recorded is not None and (recorded.get("sha256") != digest or
                                                 recorded.get("start_date") != start or
                                                 recorded.get("end_date") != end or
                                                 recorded.get("raw_file") != filename):
                        result["stopped"] = {"key": key, "reason": "既有原件與 meta.json 記錄不符"}
                        break
                    if recorded is None:
                        try:
                            events = parse_exdailyq(content, source=filename)
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
                            result["stopped"] = {"key": key, "reason": f"既有原件格式無效：{error}"}
                            break
                        files[key] = {
                            "key": key, "start_date": start, "end_date": end, "raw_file": filename,
                            "sha256": digest, "bytes": len(content), "rows": len(events),
                            "source_url": EXDAILYQ_URL, "fetched_at_utc": None, "recovered_existing": True,
                        }
                        _write_meta(out, files)
                    result["existing"] += 1
                    continue
                if not first_request:
                    sleep(MIN_INTERVAL_SECONDS)
                first_request = False
                result["requested"] += 1
                requested_at = datetime.now(timezone.utc).isoformat()
                reason = ""
                try:
                    response = get(session, EXDAILYQ_URL, {
                        "startDate": _slash(start), "endDate": _slash(end), "response": "json",
                    })
                except Exception as error:  # 連線錯誤即停，不重試
                    reason = f"連線錯誤：{type(error).__name__}: {error}"
                else:
                    if response.status != 200:
                        reason = f"HTTP {response.status}"
                    elif "json" not in response.content_type.lower():
                        reason = f"非預期 Content-Type：{response.content_type}"
                    else:
                        try:
                            events = parse_exdailyq(response.content, source=filename)
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
                            reason = f"非預期 JSON 格式：{error}"
                entry = {"key": key, "start_date": start, "end_date": end, "requested_at_utc": requested_at}
                if reason:
                    result["stopped"] = {"key": key, "reason": reason}
                    log.write(json.dumps({**entry, "outcome": "stop", "reason": reason}, ensure_ascii=False) + "\n")
                    log.flush()
                    break
                digest = hashlib.sha256(response.content).hexdigest()
                _write_atomic(target, response.content)
                _write_atomic(sidecar, f"{digest}  {filename}\n".encode("ascii"))
                files[key] = {
                    "key": key, "start_date": start, "end_date": end, "raw_file": filename,
                    "sha256": digest, "bytes": len(response.content), "rows": len(events),
                    "source_url": response.url, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                _write_meta(out, files)
                log.write(json.dumps({**entry, "outcome": "saved", "http_status": response.status,
                                      "source_url": response.url, "sha256": digest, "rows": len(events)},
                                     ensure_ascii=False) + "\n")
                log.flush()
                result["fetched"] += 1
    finally:
        if session is not None:
            session.close()
    _write_meta(out, files)
    return result


def _verified_entries(out: Path) -> list[dict[str, Any]]:
    try:
        entries = json.loads((out / "meta.json").read_text(encoding="utf-8"))["files"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"{out} 缺少有效 meta.json") from error
    for entry in entries:
        path = out / entry["raw_file"]
        sidecar = path.with_name(path.name + ".sha256")
        try:
            expected, filename = sidecar.read_text(encoding="ascii").split()
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(f"{path.name} 缺少有效 SHA-256 sidecar") from error
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if filename != path.name or expected != entry.get("sha256") or actual != expected:
            raise ValueError(f"{path.name} SHA-256 不符")
    return entries


def verified_coverage(out: Path) -> list[tuple[str, str]]:
    return sorted((entry["start_date"], entry["end_date"]) for entry in _verified_entries(out))


def coverage_covers(intervals: list[tuple[str, str]], start: str, end: str) -> bool:
    """區間聯集是否完整涵蓋 start..end（相鄰日可銜接）。"""
    if start > end:
        return False
    cursor = date.fromisoformat(start)
    target = date.fromisoformat(end)
    for left_text, right_text in sorted(intervals):
        left, right = date.fromisoformat(left_text), date.fromisoformat(right_text)
        if right < cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right + timedelta(days=1))
        if cursor > target:
            return True
    return cursor > target


def load_events(out: Path) -> list[dict[str, Any]]:
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in _verified_entries(out):
        path = out / entry["raw_file"]
        for event in parse_exdailyq(path.read_bytes(), source=path.name):
            key = (event["code"], event["date"])
            previous = events.get(key)
            if previous is not None:
                comparable_previous = {k: v for k, v in previous.items() if k != "source"}
                comparable_event = {k: v for k, v in event.items() if k != "source"}
                if comparable_previous != comparable_event:
                    raise ValueError(f"櫃買原件間有衝突事件：{key[0]} {key[1]}")
                continue
            events[key] = event
    return sorted(events.values(), key=lambda event: (event["date"], event["code"]))


def cash_dividends(out: Path) -> dict[str, float]:
    """「除權息」同日事件的每股現金股利（鍵：代號|日期），取自已驗 SHA 的原件。

    共同 events.json 只留權值＋息值合計；還原價只還原配股那部分，要用這個把現金拆出來。
    """
    cash: dict[str, float] = {}
    for entry in _verified_entries(out):
        payload = json.loads((out / entry["raw_file"]).read_bytes().decode("utf-8"))
        table = next((table for table in payload.get("tables", []) if table.get("fields") == FIELDS), None)
        if table is None:
            raise ValueError(f"{entry['raw_file']} 欄位不符")
        for raw in table.get("data") or []:
            cell = dict(zip(FIELDS, raw))
            if str(cell["權/息"]).strip() != "除權息":
                continue
            amount = _number(cell["現金股利"])
            if amount is not None:
                cash[f"{str(cell['代號']).strip()}|{roc_date_to_iso(str(cell['除權息日期']))}"] = amount
    return cash


def merge_events(existing: list[dict[str, Any]], otc_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """移除舊 TPEx 衍生事件後重建；上市官方事件原樣保留且遇鍵值衝突即停。"""
    merged = {(event["code"], event["date"]): event for event in existing
              if not str(event.get("source", "")).startswith(SOURCE_PREFIX)}
    for event in otc_events:
        key = (event["code"], event["date"])
        if key in merged:
            raise ValueError(f"櫃買事件與既有上市事件衝突：{key[0]} {key[1]}")
        merged[key] = event
    return sorted(merged.values(), key=lambda event: (event["date"], event["code"]))


def rebuild_events(events_path: Path, raw_dir: Path) -> dict[str, int]:
    """從已驗 SHA 的櫃買原件重建 TPEx 部分，再原子換上共同 events.json。"""
    try:
        existing = json.loads(events_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = []
    if not isinstance(existing, list):
        raise ValueError("events.json 頂層必須是事件清單")
    otc_events = load_events(raw_dir)
    merged = merge_events(existing, otc_events)
    _write_atomic(events_path, (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"listed_or_existing": len(merged) - len(otc_events), "otc": len(otc_events), "total": len(merged)}


def annual_periods(start: str, end: str) -> list[tuple[str, str, str]]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError(f"期間起日晚於迄日：{start} > {end}")
    periods = []
    for year in range(first.year, last.year + 1):
        left = max(first, date(year, 1, 1))
        right = min(last, date(year, 12, 31))
        full_year = left == date(year, 1, 1) and right == date(year, 12, 31)
        key = str(year) if full_year else f"{year}-{left:%m%d}-{right:%m%d}"
        periods.append((key, left.isoformat(), right.isoformat()))
    return periods


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓櫃買除權除息官方原件並合併 events.json")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    args = parser.parse_args(argv)
    summary = fetch_periods(args.raw_dir, annual_periods(args.start, args.end))
    if summary["stopped"]:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    summary["events"] = rebuild_events(args.events, args.raw_dir)
    summary["coverage"] = verified_coverage(args.raw_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
