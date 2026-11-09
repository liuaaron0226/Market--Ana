import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
SNAPSHOT_ROOT = ROOT / "data/market-snapshots/analysis-20261106"
CUTOFF = date(2026, 11, 5)
SEED_ROOT: Path | None = None
DEFAULT_DELAY_SECONDS = 3.0

INDEX_URL = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
TURNOVER_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"
INTRADAY_INDEX_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_INDEX"
INSTITUTIONAL_URL = "https://www.twse.com.tw/fund/BFI82U"
SECTOR_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
FUTURES_DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"


class FetchStopped(RuntimeError):
    pass


@dataclass
class FetchRecord:
    target: str
    source_url: str
    status: str
    rows: int | None = None


class OfficialClient:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(delay_seconds, DEFAULT_DELAY_SECONDS)
        self.last_request_at: float | None = None
        self.session = requests.Session()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if self.last_request_at is not None:
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < self.delay_seconds:
                time.sleep(self.delay_seconds - elapsed)
        try:
            response = self.session.request(method, url, timeout=60, **kwargs)
        except requests.RequestException as error:
            raise FetchStopped(f"官方端點請求失敗，未重試：{url}: {error}") from error
        finally:
            self.last_request_at = time.monotonic()

        if response.status_code in {403, 429}:
            raise FetchStopped(f"官方端點回傳 HTTP {response.status_code}，依規則停止：{response.url}")
        body_prefix = response.content[:10000].decode("utf-8", errors="ignore").lower()
        verification_markers = ("captcha", "驗證碼", "人機驗證", "access denied", "robot check")
        if any(marker in body_prefix for marker in verification_markers):
            raise FetchStopped(f"官方端點出現驗證要求，依規則停止：{response.url}")
        if response.status_code >= 400:
            raise FetchStopped(f"官方端點回傳 HTTP {response.status_code}，未重試：{response.url}")
        return response


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def write_new_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_new_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def load_wrapper(path: Path) -> dict[str, Any]:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FetchStopped(f"既有原件無法解析，為避免覆寫而停止：{path}: {error}") from error
    if not isinstance(wrapper, dict) or "data" not in wrapper:
        raise FetchStopped(f"既有原件缺少 data，為避免覆寫而停止：{path}")
    return wrapper


def parse_json_response(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FetchStopped(f"{label} 回應不是可解析的 UTF-8 JSON：{response.url}") from error
    if not isinstance(payload, dict):
        raise FetchStopped(f"{label} 回應最外層不是物件：{response.url}")
    return payload


def fetch_json(
    client: OfficialClient,
    *,
    url: str,
    params: dict[str, str],
    target: Path,
    label: str,
    records: list[FetchRecord],
    seedable: bool = True,
) -> dict[str, Any]:
    if seedable and not target.exists() and SEED_ROOT is not None:
        seed = SEED_ROOT / target.relative_to(SNAPSHOT_ROOT)
        if seed.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, target)
    if target.exists():
        wrapper = load_wrapper(target)
        data = wrapper["data"]
        rows = len(data.get("data", [])) if isinstance(data, dict) else None
        records.append(FetchRecord(relative(target), str(wrapper.get("source_url", url)), "existing", rows))
        return data

    response = client.request("GET", url, params=params)
    payload = parse_json_response(response, label)
    stat = payload.get("stat")
    if stat is not None and stat != "OK":
        raise FetchStopped(f"{label} stat={stat!r}，停止：{response.url}")
    wrapper = {
        "fetched_at_utc": utc_now(),
        "source_url": response.url,
        "http_status": response.status_code,
        "data": payload,
    }
    write_new_json(target, wrapper)
    rows = len(payload.get("data", [])) if isinstance(payload.get("data"), list) else None
    records.append(FetchRecord(relative(target), response.url, "fetched", rows))
    print(json.dumps({"file": relative(target), "rows": rows}, ensure_ascii=False), flush=True)
    return payload


def month_keys(start_year: int, start_month: int, end_year: int, end_month: int) -> list[str]:
    values: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        values.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return values


def roc_to_iso(value: str) -> str:
    roc_year, month, day = value.strip().split("/")
    return f"{int(roc_year) + 1911:04d}-{int(month):02d}-{int(day):02d}"


def parse_number(value: str) -> float:
    return float(value.replace(",", "").strip())


def fetch_history(client: OfficialClient, records: list[FetchRecord]) -> None:
    for month in month_keys(2016, 1, CUTOFF.year, CUTOFF.month):
        request_date = f"{month}01"
        seedable = month != f"{CUTOFF.year:04d}{CUTOFF.month:02d}"
        index_target = SNAPSHOT_ROOT / "index-monthly" / f"taiex-{month}.json"
        turnover_target = SNAPSHOT_ROOT / "turnover-monthly" / f"fmtqik-{month}.json"
        fetch_json(
            client,
            url=INDEX_URL,
            params={"response": "json", "date": request_date},
            target=index_target,
            label=f"加權指數日K {month}",
            records=records,
            seedable=seedable,
        )
        fetch_json(
            client,
            url=TURNOVER_URL,
            params={"response": "json", "date": request_date},
            target=turnover_target,
            label=f"市場成交資訊 {month}",
            records=records,
            seedable=seedable,
        )


def load_trading_dates() -> list[str]:
    dates: list[str] = []
    for month in month_keys(2016, 1, CUTOFF.year, CUTOFF.month):
        index_path = SNAPSHOT_ROOT / "index-monthly" / f"taiex-{month}.json"
        turnover_path = SNAPSHOT_ROOT / "turnover-monthly" / f"fmtqik-{month}.json"
        if not index_path.exists() or not turnover_path.exists():
            raise FetchStopped(f"歷史月份原件不完整：{month}")
        index_data = load_wrapper(index_path)["data"]
        turnover_data = load_wrapper(turnover_path)["data"]
        if index_data.get("stat") != "OK" or turnover_data.get("stat") != "OK":
            raise FetchStopped(f"歷史月份 stat 非 OK：{month}")
        index_dates = [roc_to_iso(row[0]) for row in index_data.get("data", [])]
        turnover_dates = {roc_to_iso(row[0]) for row in turnover_data.get("data", [])}
        if not index_dates:
            raise FetchStopped(f"歷史月份沒有指數資料：{month}")
        missing_turnover = [value for value in index_dates if value not in turnover_dates]
        if missing_turnover:
            raise FetchStopped(f"歷史月份成交金額缺日期：{month} {missing_turnover[:3]}")
        dates.extend(value for value in index_dates if date.fromisoformat(value) <= CUTOFF)
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise FetchStopped("日K日期未嚴格遞增或有重複")
    if not dates or dates[-1] != CUTOFF.isoformat():
        raise FetchStopped(f"日K截止日不是 {CUTOFF.isoformat()}：{dates[-1] if dates else '無資料'}")
    return dates


def fetch_recent(client: OfficialClient, records: list[FetchRecord]) -> None:
    dates = load_trading_dates()
    intraday_dates = dates[-30:]
    institutional_dates = dates[-120:]
    sector_dates = dates[-60:]

    for value in intraday_dates:
        compact = value.replace("-", "")
        target = SNAPSHOT_ROOT / "intraday-5s" / f"taiex-{compact}.json"
        payload = fetch_json(
            client,
            url=INTRADAY_INDEX_URL,
            params={"date": compact, "response": "json"},
            target=target,
            label=f"每5秒指數 {value}",
            records=records,
        )
        if len(payload.get("data", [])) < 3000:
            raise FetchStopped(f"每5秒指數筆數異常：{value} rows={len(payload.get('data', []))}")

    for value in institutional_dates:
        compact = value.replace("-", "")
        target = SNAPSHOT_ROOT / "institutional" / f"bfi82u-{compact}.json"
        payload = fetch_json(
            client,
            url=INSTITUTIONAL_URL,
            params={"response": "json", "dayDate": compact, "type": "day"},
            target=target,
            label=f"三大法人 {value}",
            records=records,
        )
        if len(payload.get("data", [])) < 5:
            raise FetchStopped(f"三大法人欄位或筆數異常：{value}")

    for value in sector_dates:
        compact = value.replace("-", "")
        target = SNAPSHOT_ROOT / "sectors" / f"mi-index-ind-{compact}.json"
        payload = fetch_json(
            client,
            url=SECTOR_URL,
            params={"response": "json", "date": compact, "type": "IND"},
            target=target,
            label=f"類股指數 {value}",
            records=records,
        )
        if not payload.get("tables"):
            raise FetchStopped(f"類股指數缺少 tables：{value}")

    start_value = institutional_dates[0]
    end_value = institutional_dates[-1]
    csv_target = SNAPSHOT_ROOT / "futures" / f"taifex-txf-{start_value.replace('-', '')}-{end_value.replace('-', '')}.csv"
    meta_target = csv_target.with_suffix(".meta.json")
    if csv_target.exists() or meta_target.exists():
        if not csv_target.exists() or not meta_target.exists() or csv_target.stat().st_size < 500:
            raise FetchStopped(f"期交所既有原件不完整，為避免覆寫而停止：{csv_target}")
        meta = json.loads(meta_target.read_text(encoding="utf-8"))
        records.append(FetchRecord(relative(csv_target), str(meta.get("source_url", FUTURES_DOWNLOAD_URL)), "existing"))
    else:
        request_payload = {
            "queryStartDate": start_value.replace("-", "/"),
            "queryEndDate": end_value.replace("-", "/"),
            "commodityId": "TXF",
        }
        response = client.request("POST", FUTURES_DOWNLOAD_URL, data=request_payload)
        content_type = response.headers.get("content-type", "")
        if len(response.content) < 500 or "ms950" not in content_type.lower():
            raise FetchStopped(
                f"期交所下載內容不像 MS950 CSV：content-type={content_type!r}, bytes={len(response.content)}"
            )
        write_new_bytes(csv_target, response.content)
        write_new_json(
            meta_target,
            {
                "fetched_at_utc": utc_now(),
                "source_url": response.url,
                "http_status": response.status_code,
                "request_payload": request_payload,
                "content_type": content_type,
                "content_disposition": response.headers.get("content-disposition"),
                "bytes": len(response.content),
            },
        )
        records.append(FetchRecord(relative(csv_target), response.url, "fetched"))
        print(json.dumps({"file": relative(csv_target), "bytes": len(response.content)}, ensure_ascii=False), flush=True)


def write_fetch_report(records: list[FetchRecord], error: str | None) -> None:
    report_path = SNAPSHOT_ROOT / "fetch-report.json"
    payload = {
        "generated_at_utc": utc_now(),
        "cutoff_date": CUTOFF.isoformat(),
        "request_policy": {
            "single_threaded": True,
            "minimum_delay_seconds": DEFAULT_DELAY_SECONDS,
            "retries": 0,
            "stop_on_http": [403, 429],
            "stop_on_verification": True,
        },
        "records": [record.__dict__ for record in records],
        "error": error,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="單路下載台股多週期分析所需官方原件")
    parser.add_argument("--phase", choices=("all", "history", "recent"), default="all")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--cutoff", help="資料截止交易日 YYYY-MM-DD；預設沿用 2026-11-05")
    parser.add_argument("--snapshot", help="新快照資料夾（相對專案根目錄）；只寫入新檔，不覆寫既有原件")
    parser.add_argument("--seed-from", help="舊快照資料夾；截止月以外的原件複製過來，不重新下載")
    args = parser.parse_args()
    global SNAPSHOT_ROOT, CUTOFF, SEED_ROOT
    if args.cutoff:
        CUTOFF = date.fromisoformat(args.cutoff)
    if args.snapshot:
        SNAPSHOT_ROOT = ROOT / args.snapshot
    if args.seed_from:
        SEED_ROOT = ROOT / args.seed_from
        if SEED_ROOT.resolve() == SNAPSHOT_ROOT.resolve():
            raise SystemExit("--seed-from 不能與 --snapshot 相同")

    records: list[FetchRecord] = []
    client = OfficialClient(args.delay)
    try:
        if args.phase in {"all", "history"}:
            fetch_history(client, records)
        if args.phase in {"all", "recent"}:
            fetch_recent(client, records)
    except FetchStopped as error:
        write_fetch_report(records, str(error))
        print(str(error), file=sys.stderr, flush=True)
        return 2

    write_fetch_report(records, None)
    print(
        json.dumps(
            {
                "status": "ok",
                "phase": args.phase,
                "records": len(records),
                "fetched": sum(record.status == "fetched" for record in records),
                "existing": sum(record.status == "existing" for record in records),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
