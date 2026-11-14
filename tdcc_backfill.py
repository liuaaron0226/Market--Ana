"""集保查詢頁補抓過去一年的大戶持股（本人 2026-11-15「補，慢慢抓」）。

開放資料（tdcc_holders.py）只有最新一週。官方查詢頁保存一年、一次查一檔一週、沒有驗證碼；
頁面自己寫多檔查詢請用開放資料，但開放資料沒有歷史，所以這裡照本人的決定慢慢查：

- 每次查詢前固定等 PAUSE 秒；先把最近一週的所有股票查完，再往前一週（中途停下，手上是完整的近幾週）。
- 進度逐筆附加到 jsonl，重跑只查還沒有的；那一週沒有那一檔也記下來，不再問。
- HTTP 不是 200、頁面不是查詢頁、回的不是問的那一檔那一週、或連續一大串查無資料：立刻停，不重試硬闖。
- 連線錯誤只等 60 秒重來一次，再錯就停。

用法：
    python tdcc_backfill.py run --codes data/tdcc-holders/backfill/codes.json
    python tdcc_backfill.py export          # 進度檔 → seeds/tdcc-holders-history.json
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
DEFAULT_PROGRESS = ROOT / "data/tdcc-holders/backfill/progress.jsonl"
DEFAULT_SEED = ROOT / "seeds/tdcc-holders-history.json"
PAUSE = 3.0
RETRY_WAIT = 60.0
EMPTY_RUN_LIMIT = 15
REFRESH_EVERY = 100  # 同一個連線階段查太久官方會回 404（11-15 實跑 153 次後）；每查這麼多次就重新開一次表單頁
HISTORY_SCHEMA = "tdcc-holders-history/v1"
HISTORY_FIELDS = ("pct_1000", "holders_1000", "pct_400", "holders_400", "holders")

_ROW = re.compile(r'<tr>\s*<td align="center">([^<]*)</td>\s*<td align="center">([^<]*)</td>\s*<td align="right">([^<]*)</td>'
                  r'\s*<td align="right">([^<]*)</td>\s*<td align="right">([^<]*)</td>\s*</tr>')
_HIDDEN = re.compile(r'<input[^>]*name="(SYNCHRONIZER_TOKEN|SYNCHRONIZER_URI|method|firDate)"[^>]*value="([^"]*)"')


class Refused(Exception):
    """官方站的回應不是預期的樣子：停，不重試。"""


def parse_form(html: str) -> dict:
    """查詢表單的隱藏欄位（含一次性權杖）與可查的週。不是查詢頁就拒絕。"""
    fields = dict(_HIDDEN.findall(html))
    dates = re.findall(r'<option value="(\d{8})"', html)
    if not fields.get("SYNCHRONIZER_TOKEN") or not dates:
        raise ValueError("不是集保查詢頁（沒有表單權杖或日期選單）")
    return {"fields": fields, "dates": dates}


def _roc_date(text: str) -> str:
    match = re.fullmatch(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", text.strip())
    if not match:
        raise ValueError(f"資料日期看不懂：{text[:20]}")
    return date(int(match.group(1)) + 1911, int(match.group(2)), int(match.group(3))).isoformat()


def parse_result(html: str) -> dict | None:
    """查詢結果 → {code, data_date, holders}；那一週沒有這一檔回 None。欄位與 tdcc_holders.parse 相同。"""
    if "占集保庫存數比例" not in html or "SYNCHRONIZER_TOKEN" not in html:
        raise ValueError("不是集保查詢結果頁")
    rows = _ROW.findall(html)
    if not rows:
        return None
    code = re.search(r"證券代號：\s*([0-9A-Za-z]+)", html)
    day = re.search(r"資料日期：\s*([^<\s]+)", html)
    if not code or not day:
        raise ValueError("結果頁沒有證券代號或資料日期")
    levels: dict[int, tuple[int, float]] = {}
    total = None
    for number, label, count, _shares, pct in rows:
        if re.sub(r"\s|　", "", label) == "合計":
            total = int(count.replace(",", ""))
        elif number.strip().isdigit() and count.strip():
            levels[int(number)] = (int(count.replace(",", "")), float(pct))
    if total is None or any(level not in levels for level in range(1, 16)):
        raise ValueError("結果頁的持股分級不完整")
    big = [levels[level] for level in (12, 13, 14, 15)]
    return {"code": code.group(1), "data_date": _roc_date(day.group(1)),
            "holders": {"pct_1000": levels[15][1], "holders_1000": levels[15][0],
                        "pct_400": round(sum(pct for _count, pct in big), 2),
                        "holders_400": sum(count for count, _pct in big), "holders": total}}


def _load_progress(path: Path) -> dict[tuple[str, str], dict | None]:
    done: dict[tuple[str, str], dict | None] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[(row["code"], row["date"])] = row["holders"]
    return done


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def backfill(codes: list[str], progress: Path = DEFAULT_PROGRESS, *, fetch: Callable, sleep: Callable = time.sleep,
             log: Callable = print, skip_dates: set[str] | None = None, limit: int | None = None,
             refresh_every: int = REFRESH_EVERY) -> dict:
    """fetch(None) 讀表單、fetch(欄位) 送查詢，都回（HTTP 狀態，頁面文字）。回報做了多少、為什麼停。"""
    report = {"status": "done", "fetched": 0, "missing": 0, "requests": 0}

    def stopped(reason: str) -> dict:
        return {**report, "status": "stopped", "reason": reason}

    def open_form() -> dict:
        report["requests"] += 1
        status, html = fetch(None)
        if status != 200:
            raise Refused(f"HTTP {status}")
        return parse_form(html)

    try:
        form = open_form()
    except (Refused, ValueError) as error:
        return stopped(str(error))
    except Exception as error:  # noqa: BLE001 — 連線錯誤即停
        return stopped(f"連線錯誤：{type(error).__name__}")
    done = _load_progress(progress)
    skip = skip_dates or set()
    weeks = [(compact, f"{compact[:4]}-{compact[4:6]}-{compact[6:]}") for compact in sorted(form["dates"], reverse=True)]
    todo = [(code, compact, iso) for compact, iso in weeks if iso not in skip for code in codes if (code, iso) not in done]
    report["remaining"] = len(todo)
    empties: list[dict] = []  # 查無資料先放著：後面有查到東西才算數，一長串都空就當成被擋
    asked = 0  # 這個表單頁已經查了幾次
    for code, compact, iso in todo:
        if limit is not None and report["fetched"] + report["missing"] + len(empties) >= limit:
            report["status"] = "paused"
            break
        if asked >= refresh_every:
            sleep(PAUSE)
            try:
                form = open_form()
            except (Refused, ValueError) as error:
                return stopped(str(error))
            except Exception as error:  # noqa: BLE001
                return stopped(f"連線錯誤：{type(error).__name__}")
            asked = 0
        asked += 1
        retried = False
        while True:
            try:
                if not retried:
                    sleep(PAUSE)
                report["requests"] += 1
                status, html = fetch({**form["fields"], "scaDate": compact, "sqlMethod": "StockNo", "stockNo": code, "stockName": ""})
                break
            except Exception as error:  # noqa: BLE001
                if retried:
                    return stopped(f"連線錯誤：{type(error).__name__}")
                retried = True
                log(f"連線錯誤（{type(error).__name__}），等 {RETRY_WAIT:.0f} 秒後重來一次")
                sleep(RETRY_WAIT)
                try:
                    form = open_form()
                except Exception as again:  # noqa: BLE001
                    return stopped(f"連線錯誤：{type(again).__name__}" if not isinstance(again, (Refused, ValueError)) else str(again))
        if status != 200:
            return stopped(f"HTTP {status}")
        try:
            form = {**form, "fields": parse_form(html)["fields"]}
            result = parse_result(html)
        except ValueError as error:
            return stopped(str(error))
        if result is None:
            empties.append({"code": code, "date": iso, "holders": None})
            if len(empties) >= EMPTY_RUN_LIMIT:
                return stopped(f"連續 {len(empties)} 次查無資料，可能被擋或權杖失效；這一串沒有記成「沒有」")
            continue
        if (result["code"], result["data_date"]) != (code, iso):
            return stopped(f"問 {code} {iso}，回的是 {result['code']} {result['data_date']}")
        _append(progress, [*empties, {"code": code, "date": iso, "holders": result["holders"]}])
        report["missing"] += len(empties)
        report["fetched"] += 1
        empties = []
        if report["fetched"] % 50 == 0:
            log(f"{datetime.now().isoformat(timespec='seconds')} 已查到 {report['fetched']}／剩 {report['remaining'] - report['fetched'] - report['missing']}（目前 {iso}）")
    if empties:  # 收尾時剩下的零星查無資料（沒有達到整串的門檻）照實記下
        _append(progress, empties)
        report["missing"] += len(empties)
    report["remaining"] -= report["fetched"] + report["missing"]
    return report


def export(progress: Path = DEFAULT_PROGRESS) -> dict:
    """進度檔 → 依週分組的歷史（每檔一列數字，欄位順序見 HISTORY_FIELDS）；查無資料的不放。"""
    weeks: dict[str, dict[str, list]] = {}
    for (code, day), holders in sorted(_load_progress(progress).items()):
        if holders is not None:
            weeks.setdefault(day, {})[code] = [holders[field] for field in HISTORY_FIELDS]
    return {"schema": HISTORY_SCHEMA, "source": URL, "fields": list(HISTORY_FIELDS), "weeks": dict(sorted(weeks.items()))}


def site_fetch() -> Callable:
    """真的連集保：同一個連線階段保留 cookie；每次查詢都帶表單頁的網址當來源。"""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", "Mozilla/5.0"), ("Referer", URL)]

    def fetch(data: dict | None):
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        try:
            with opener.open(urllib.request.Request(URL, data=body), timeout=40) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, ""
    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="集保大戶持股：補抓過去一年")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--codes", type=Path, required=True, help="JSON 陣列：要補的證券代號")
    run.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    run.add_argument("--limit", type=int, help="這次最多查幾筆（試跑用）")
    run.add_argument("--skip-date", action="append", default=[], help="已經有的週（YYYY-MM-DD），可重複")
    out = sub.add_parser("export")
    out.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    out.add_argument("--out", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    if args.command == "run":
        codes = json.loads(args.codes.read_text(encoding="utf-8"))
        report = backfill(codes, args.progress, fetch=site_fetch(), skip_dates=set(args.skip_date), limit=args.limit,
                          log=lambda message: print(message, flush=True))
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report["status"] in ("done", "paused") else 1
    history = export(args.progress)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"weeks": len(history["weeks"]), "rows": sum(len(week) for week in history["weeks"].values()),
                      "bytes": args.out.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
