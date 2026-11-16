"""外資台指期部位的每日累積（預測帳本 G 票第 14 片）。

來源是期交所「三大法人－區分各期貨契約」下載（``futContractsDateDown``，MS950 CSV）。期交所只開放查近三年，
不每天存下來，歷史就拉不長，所以每日更新每天各抓一次大台（TXF）、小台（MXF）當天的檔，原件＋SHA-256 存在
``data/foreign-futures/raw/``（跟著工作集保存，不裁）。三年以前到開始累積那天的部分是起始資料
``seeds/foreign-futures-positions.json``，由 ``build_seed`` 從當時下載的整段原件解析而來（來源檔名與雜湊記在裡面）。

讀回用 ``positions``：每一天外資的大台、小台淨未平倉口數，和「大台＋小台÷4」的淨部位（小台 4 口折 1 口大台）。
只打 www.taifex.com.tw，一次一個請求、間隔 >= 5 秒；不是 CSV（查詢超出範圍時期交所回錯誤頁）就當沒公布，不寫檔。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from otc_market_data import Response, write_raw

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data/foreign-futures"
SEED = ROOT / "seeds/foreign-futures-positions.json"
URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
SEED_SCHEMA = "foreign-futures-seed/v1"
CONTRACTS = {"TXF": "臺股期貨", "MXF": "小型臺指期貨"}
MINI_PER_BIG = 4
_FOREIGN = ("外資及陸資", "外資")
MIN_INTERVAL_SECONDS = 5.0


class NotContractsCsv(ValueError):
    """回應不是期交所的期貨契約 CSV（例如查詢超出範圍時的錯誤頁）。"""


class PositionsConflict(ValueError):
    """原件雜湊不符，或同一天兩份來源的口數不一樣。"""


def _iso(text: str) -> str:
    return text.strip().replace("/", "-")


def parse_contracts_csv(content: bytes, contract: str) -> dict[str, int]:
    """回傳 {日期: 外資淨未平倉口數}，只取指定契約。"""
    try:
        text = content.decode("cp950")
    except UnicodeDecodeError as error:
        raise NotContractsCsv("不是 MS950 文字") from error
    text = text.lstrip("﻿")
    if not text.startswith("日期"):
        raise NotContractsCsv("第一行不是期交所 CSV 欄名")
    reader = csv.DictReader(io.StringIO(text))
    needed = {"日期", "商品名稱", "身份別", "多空未平倉口數淨額"}
    if not needed <= set(reader.fieldnames or []):
        raise NotContractsCsv(f"欄位不符：缺 {sorted(needed - set(reader.fieldnames or []))}")
    out: dict[str, int] = {}
    for row in reader:
        if (row.get("商品名稱") or "").strip() != CONTRACTS[contract] or (row.get("身份別") or "").strip() not in _FOREIGN:
            continue
        day = _iso(row["日期"])
        value = int(row["多空未平倉口數淨額"].replace(",", "").strip())
        if day in out and out[day] != value:
            raise ValueError(f"{contract} {day} 同一天出現兩個不同的口數")
        out[day] = value
    return out


def _raw_name(contract: str, day: str) -> str:
    return f"{contract}-{day.replace('-', '')}.csv"


def _verified_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    sidecar = path.with_name(path.name + ".sha256")
    parts = sidecar.read_text(encoding="ascii").split() if sidecar.exists() else []
    if parts[:2] != [hashlib.sha256(data).hexdigest(), path.name]:
        raise PositionsConflict(f"{path.name} 的 SHA-256 和 sidecar 不符")
    return data


def _default_post(url: str, data: dict[str, str]) -> Response:
    import requests
    response = requests.post(url, data=data, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    return Response(response.status_code, response.headers.get("content-type", ""), response.content, response.url)


def fetch_day(store: Path | str, day: str, *, post: Callable[[str, dict[str, str]], Response] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """抓指定交易日的大台、小台各一份。兩份都要有當天外資那一列才寫檔；已存在就驗雜湊後不再抓。"""
    store = Path(store)
    post = post or _default_post
    missing = [c for c in CONTRACTS if not (store / "raw" / _raw_name(c, day)).exists()]
    for contract in CONTRACTS:
        if contract not in missing:
            _verified_bytes(store / "raw" / _raw_name(contract, day))
    if not missing:
        return {"status": "already-present", "requests": 0}
    bodies: dict[str, bytes] = {}
    for n, contract in enumerate(missing):
        if n:
            sleep(MIN_INTERVAL_SECONDS)
        slash = day.replace("-", "/")
        response = post(URL, {"queryStartDate": slash, "queryEndDate": slash, "commodityId": contract})
        if response.status != 200:
            return {"status": "stopped", "reason": f"{contract} HTTP {response.status}", "requests": n + 1}
        try:
            parsed = parse_contracts_csv(response.content, contract)
        except NotContractsCsv:
            return {"status": "not_published", "contract": contract, "requests": n + 1}
        if day not in parsed:
            return {"status": "not_published", "contract": contract, "requests": n + 1}
        bodies[contract] = response.content
    for contract, content in bodies.items():
        write_raw(store, _raw_name(contract, day), content)
    return {"status": "appended", "requests": len(missing), "contracts": sorted(bodies)}


def _merge(target: dict[str, dict[str, int | None]], day: str, contract: str, value: int | None, origin: str) -> None:
    slot = target.setdefault(day, {"txf": None, "mxf": None})
    key = contract.lower()
    if value is None:
        return
    if slot[key] is not None and slot[key] != value:
        raise PositionsConflict(f"{day} {contract} 起始資料 {slot[key]} 和 {origin} {value} 不一樣")
    slot[key] = value


def positions(store: Path | str = STORE, seed: Path | str = SEED) -> list[dict[str, Any]]:
    """每一天外資的大台、小台淨未平倉與淨部位（大台＋小台÷4）；只列兩種契約都有的日子，日期遞增。"""
    by_day: dict[str, dict[str, int | None]] = {}
    seed = Path(seed)
    if seed.exists():
        payload = json.loads(seed.read_text(encoding="utf-8"))
        if payload.get("schema") != SEED_SCHEMA:
            raise PositionsConflict(f"起始資料格式不是 {SEED_SCHEMA}")
        for row in payload["rows"]:
            _merge(by_day, row["date"], "TXF", row.get("txf"), "起始資料")
            _merge(by_day, row["date"], "MXF", row.get("mxf"), "起始資料")
    raw = Path(store) / "raw"
    if raw.is_dir():
        for path in sorted(raw.glob("*.csv")):
            contract = path.name.split("-", 1)[0]
            if contract not in CONTRACTS:
                continue
            for day, value in parse_contracts_csv(_verified_bytes(path), contract).items():
                _merge(by_day, day, contract, value, path.name)
    return [{"date": day, "txf": slot["txf"], "mxf": slot["mxf"], "net": slot["txf"] + slot["mxf"] / MINI_PER_BIG}
            for day, slot in sorted(by_day.items()) if slot["txf"] is not None and slot["mxf"] is not None]


def build_seed(source_dir: Path | str, out: Path | str) -> dict[str, Any]:
    """把當時下載的整段原件（``TXF_起_迄.csv``、``MXF_起_迄.csv``）解析成起始資料；錯誤頁略過並列出。"""
    by_day: dict[str, dict[str, int | None]] = {}
    sources, skipped = [], []
    for path in sorted(Path(source_dir).glob("*.csv")):
        contract = path.name.split("_", 1)[0]
        if contract not in CONTRACTS:
            skipped.append(path.name)
            continue
        data = path.read_bytes()
        try:
            parsed = parse_contracts_csv(data, contract)
        except NotContractsCsv:
            skipped.append(path.name)
            continue
        for day, value in parsed.items():
            _merge(by_day, day, contract, value, path.name)
        sources.append({"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                        "days": len(parsed)})
    rows = [{"date": day, "txf": slot["txf"], "mxf": slot["mxf"]} for day, slot in sorted(by_day.items())]
    payload = {"schema": SEED_SCHEMA, "source_url": URL, "unit": "口（外資及陸資淨未平倉）",
               "built_at_utc": datetime.now(timezone.utc).isoformat(), "sources": sources, "rows": rows}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    incomplete = [row["date"] for row in rows if row["txf"] is None or row["mxf"] is None]
    return {"days": len(rows), "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
            "incomplete": incomplete, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="外資台指期部位：每日抓取或建起始資料")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--date", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--source-dir", type=Path, required=True)
    seed.add_argument("--out", type=Path, default=SEED)
    args = parser.parse_args(argv)
    if args.command == "fetch":
        print(json.dumps(fetch_day(STORE, args.date), ensure_ascii=False))
    else:
        print(json.dumps(build_seed(args.source_dir, args.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
