"""歷史重播補抓的大盤法人與台指期外資部位（本人 2026-11-17 選 A2：重播從 04-01 起）。

網站的大盤資料包只留最近 120 個交易日的法人與期貨，04-01 到 05-25 的不在裡面。這裡向同一個官方來源補抓
（證交所三大法人買賣金額 BFI82U 一天一次、期交所三大法人期貨 futContractsDateDown 整段一次），
整理成和資料包一模一樣的欄位，重播時只補資料包沒有的日子（資料包有的照用資料包）。

原件存進重播帳本的 ``chips-backfill/``（跟著重播帳本存回 R2 的 forecast-replay/，只新增）；已經有的原件不再抓。
這些是官方公布的數字，重抓的和當天公布的相同；重播紀錄另外記下抓取時間。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TWSE_URL = "https://www.twse.com.tw/fund/BFI82U"
TAIFEX_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
# 走勢標籤的中期看近二十日法人合計、籌碼表給模型看近五日：起日之前多補二十五個交易日。
LEAD_DAYS = 25
# 證交所對連續請求很嚴，每次間隔三秒（每日更新抓官方資料也是三秒）。
PAUSE_SECONDS = 3.0
_ROWS = {"foreign": "外資及陸資(不含外資自營商)", "trust": "投信",
         "dealer_self": "自營商(自行買賣)", "dealer_hedge": "自營商(避險)"}


class BackfillStopped(RuntimeError):
    """官方回應不對（不是 OK、格式不符、HTTP 錯誤）：停下來，不存那一份。"""


def needed_dates(analysis: Mapping[str, Any], *, start: str) -> list[str]:
    """起日之前二十五個交易日起、到資料包法人與期貨都有的第一天之前，缺法人或期貨的交易日。"""
    dates = [row["date"] for row in analysis.get("daily") or []]
    have = ({row["date"] for row in analysis.get("institutional") or []}
            & {row["date"] for row in analysis.get("futures") or []})
    first = next((index for index, day in enumerate(dates) if day >= start), len(dates))
    lead = dates[max(0, first - LEAD_DAYS):]
    covered_from = min(have) if have else None
    return [day for day in lead if day not in have and (covered_from is None or day < covered_from)]


def _default_http(method: str, url: str, data: Mapping[str, str] | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"User-Agent": "Mozilla/5.0 (market-ana forecast replay)"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — 固定的官方網址
        return response.read()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_new(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_bytes(content)
    temporary.replace(path)


def fetch(out_dir: Path | str, dates: Sequence[str], *, http: Callable[..., bytes] | None = None,
          now: Callable[[], str] = _now, pause: float = PAUSE_SECONDS) -> dict[str, Any]:
    """補抓 ``dates`` 的原件到 ``out_dir``；已經存在的檔案不再抓、不覆寫。回傳並寫出 ``manifest.json``。"""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    http = http or _default_http
    entries: list[dict[str, Any]] = []
    requested = False

    def get(method: str, url: str, data: Mapping[str, str] | None = None) -> bytes:
        nonlocal requested
        if requested and pause:
            time.sleep(pause)
        requested = True
        try:
            return http(method, url, data)
        except Exception as error:  # noqa: BLE001 — 任何網路錯誤都停，不重試
            # 狀態碼要記下來（11-18 那次只留下 HTTPError，看不出是期交所擋、還是暫時故障）。
            code = getattr(error, "code", None)
            raise BackfillStopped(f"{url} 請求失敗：{type(error).__name__}"
                                  + (f"（HTTP {code}）" if code else "")) from error

    for day in dates:
        compact = day.replace("-", "")
        target = folder / f"bfi82u-{compact}.json"
        if target.exists():
            continue
        url = f"{TWSE_URL}?response=json&dayDate={compact}&type=day"
        content = get("GET", url)
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BackfillStopped(f"證交所 {day} 回的不是 JSON") from error
        names = {row[0] for row in payload.get("data") or [] if row}
        if payload.get("stat") != "OK" or not set(_ROWS.values()) <= names:
            raise BackfillStopped(f"證交所 {day} 沒有回三大法人資料（{payload.get('stat')}）")
        _write_new(target, content)
        entries.append({"file": target.name, "url": url, "sha256": hashlib.sha256(content).hexdigest(),
                        "fetched_at": now()})

    # 期貨只補已存原件還沒涵蓋到的日子（資料包每天往後推一天，需要的範圍尾巴就多一天；不整段重抓）。
    # 只抓已涵蓋的最後一天以後的日子：舊的缺口（期交所那天就是沒有外資那一列）不會把範圍一路拉回去整段重抓；
    # 那種日子在算天數時會列在「沒有當天的法人或期貨資料」，看得到。
    covered = {row["date"] for row in load(folder)["futures"]}
    last = max(covered) if covered else ""
    missing = [day for day in dates if day not in covered and day > last]
    if missing:
        start, end = missing[0].replace("-", ""), missing[-1].replace("-", "")
        target = folder / f"taifex-txf-{start}-{end}.csv"
        if not target.exists():
            data = {"queryStartDate": missing[0].replace("-", "/"), "queryEndDate": missing[-1].replace("-", "/"),
                    "commodityId": "TXF"}
            content = get("POST", TAIFEX_URL, data)
            try:
                text = content.decode("cp950")
            except UnicodeDecodeError as error:
                raise BackfillStopped("期交所回的不是 MS950 CSV") from error
            if "多空未平倉口數淨額" not in text.splitlines()[0] if text else True:
                raise BackfillStopped("期交所回的 CSV 欄位不對")
            _write_new(target, content)
            entries.append({"file": target.name, "url": TAIFEX_URL, "request": data,
                            "sha256": hashlib.sha256(content).hexdigest(), "fetched_at": now()})
        else:
            raise BackfillStopped(f"期交所 {missing[0]}～{missing[-1]} 已經抓過（{target.name}），"
                                  "原件裡還是沒有這幾天的外資部位；不重抓、不覆寫，請人工確認")

    manifest_path = folder / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": []}
    manifest = {"schema": "forecast-replay-chips/v1", "fetched_at": now() if entries else previous.get("fetched_at"),
                "files": [*previous.get("files", []), *entries]}
    if entries or not manifest_path.exists():
        # 每次多抓的原件換一個清單檔名，舊的清單不覆寫（帳本只能新增）；manifest.json 是最新的那一份。
        if manifest_path.exists():
            manifest_path.replace(folder / f"manifest-{len(previous.get('files', []))}.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _integer(text: str) -> int:
    return int(str(text).replace(",", "").strip())


def load(folder: Path | str) -> dict[str, list[dict[str, Any]]]:
    """把補抓的原件整理成資料包的 ``institutional`` 與 ``futures`` 欄位（同 logs/build_analysis_data.py）。"""
    root = Path(folder)
    institutional = []
    for path in sorted(root.glob("bfi82u-*.json")):
        compact = path.stem.split("-")[1]
        rows = {row[0]: row for row in json.loads(path.read_text(encoding="utf-8"))["data"]}

        def amounts(key: str) -> dict[str, int]:
            row = rows[_ROWS[key]]
            return {"buy": _integer(row[1]), "sell": _integer(row[2]), "net": _integer(row[3])}

        self_, hedge = amounts("dealer_self"), amounts("dealer_hedge")
        institutional.append({"date": f"{compact[:4]}-{compact[4:6]}-{compact[6:]}", "foreign": amounts("foreign"),
                              "trust": amounts("trust"),
                              "dealer": {key: self_[key] + hedge[key] for key in ("buy", "sell", "net")}})
    futures: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("taifex-txf-*.csv")):
        for row in csv.DictReader(io.StringIO(path.read_bytes().decode("cp950"))):
            if row.get("商品名稱") != "臺股期貨" or row.get("身份別") not in {"外資", "外資及陸資"}:
                continue
            day = row["日期"].replace("/", "-")
            futures[day] = {
                "date": day,
                "foreign_oi_long_contracts": _integer(row["多方未平倉口數"]),
                "foreign_oi_short_contracts": _integer(row["空方未平倉口數"]),
                "foreign_oi_net_contracts": _integer(row["多空未平倉口數淨額"]),
                "foreign_oi_long_amount_thousand_twd": _integer(row["多方未平倉契約金額(千元)"]),
                "foreign_oi_short_amount_thousand_twd": _integer(row["空方未平倉契約金額(千元)"]),
                "foreign_oi_net_amount_thousand_twd": _integer(row["多空未平倉契約金額淨額(千元)"]),
            }
    return {"institutional": institutional, "futures": [futures[day] for day in sorted(futures)]}


def merge(analysis: Mapping[str, Any], backfill: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """資料包有的日子照用資料包；補抓的只補資料包沒有的日子。"""
    merged = dict(analysis)
    for key in ("institutional", "futures"):
        published = list(analysis.get(key) or [])
        have = {row["date"] for row in published}
        extra = [dict(row) for row in backfill.get(key) or [] if row["date"] not in have]
        merged[key] = sorted(published + extra, key=lambda row: row["date"])
    return merged


def main(argv: list[str] | None = None) -> int:
    from forecast_run import load_analysis

    parser = argparse.ArgumentParser(description="補抓歷史重播要的大盤法人與台指期外資部位（官方公開資料）")
    parser.add_argument("command", choices=["fetch", "needed"])
    parser.add_argument("--analysis", type=Path, required=True, help="已發布的 analysis-data.js（交易日曆與已有的日子）")
    parser.add_argument("--start", required=True, help="重播起日")
    parser.add_argument("--out", type=Path, help="原件資料夾（重播帳本的 chips-backfill/）")
    args = parser.parse_args(argv)
    dates = needed_dates(load_analysis(args.analysis), start=args.start)
    summary: dict[str, Any] = {"needed": len(dates), "from": dates[0] if dates else None,
                               "to": dates[-1] if dates else None,
                               "requests": {"twse": len(dates), "taifex": 1 if dates else 0},
                               "estimated_seconds": round(len(dates) * PAUSE_SECONDS)}
    if args.command == "fetch":
        if not args.out:
            parser.error("fetch 要給 --out")
        try:
            manifest = fetch(args.out, dates)
        except BackfillStopped as error:
            print(f"::error::{error}")
            return 1
        summary["files"] = len(manifest["files"])
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
