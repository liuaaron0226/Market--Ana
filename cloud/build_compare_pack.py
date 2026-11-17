"""B1（2026-11-09）：「和 0050 比」的雲端資料包。

雲端不能跑 Python：build 前用 ``benchmark_compare`` 把母體內每檔（＋0050）的對照算好，
母體外的官方普通股只存名次／原因，build.mjs 包進 functions/_compare-pack.js，gate 依請求組回
與本機 live_server 相同的 JSON（/api/stocks/{code}/vs-0050 與 /api/compare-0050?codes=）。

母體來自 liquid_universe 快照（--universe）；資料日＝快照資料日（與個股分析、選股同一天）。
產物預設寫到 cloud/.compare-pack.json（不入庫）。不含本機絕對路徑。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_compare  # noqa: E402
import fund_compare  # noqa: E402
import live_server  # noqa: E402

PACK_SCHEMA = "compare-0050-pack/v1"
DEFAULT_OUT = Path(__file__).resolve().parent / ".compare-pack.json"


def _taipei_today() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_pack(universe_path: Path) -> dict[str, Any]:
    state = live_server.universe_state(Path(universe_path))
    end = state["universe"]["data_date"]
    etf_codes = {str(item["code"]) for item in state["universe"].get("etf_members", ())}
    codes = sorted(set(state["members"]) | set(state["core"]) | etf_codes)
    listed_dates = {}
    for code in codes:
        security = live_server.lookup_security(code)
        listed_dates[code] = security.listed_date if security is not None else None
    loader = benchmark_compare.Loader()
    pre = benchmark_compare.precompute(codes, end, listed_dates, loader)

    # 母體外的官方普通股：與本機 outside_universe_payload 相同的欄位（不含 error／code），gate 原樣組回。
    outside: dict[str, str] = {}
    for security in live_server.cloud_build_module().catalog_securities():
        if security.code in pre["items"]:
            continue
        detail = live_server.outside_universe_payload(security.code, security, state)
        outside[security.code] = _dump({k: v for k, v in detail.items() if k not in ("error", "code")})

    # 基金對照 0050（2026-11-17 本人定案）：和個股對照同一個 Loader 的 0050 含息序列。資料不齊就不出那檔，不擋整個資料包。
    try:
        funds, problems = fund_compare.precompute(loader, end, as_of=_taipei_today())
    except Exception as error:  # noqa: BLE001 —— 基金資料壞掉不能讓個股對照跟著停
        funds, problems = {}, {"*": f"{type(error).__name__}: {error}"}
    for code, reason in problems.items():
        print(f"fund {code} 沒有放進資料包：{reason}", file=sys.stderr)

    pack = {
        "schema": PACK_SCHEMA,
        "data_date": end,
        "max_codes": pre["max_codes"],
        "notes": pre["notes"],
        "benchmark": pre["benchmark"],
        "items": pre["items"],
        "outside": outside,
        "funds": funds,
    }
    pack["pack_sha256"] = hashlib.sha256(_dump(pack).encode("utf-8")).hexdigest()
    return pack


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, required=True, help="liquid_universe 快照 JSON（發布批次或原生）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    pack = build_pack(args.universe)
    text = _dump(pack)
    if "C:\\Users" in text or "C:/Users" in text:
        raise SystemExit("資料包含本機絕對路徑，停止")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(_dump({"out": args.out.name, "bytes": len(text.encode("utf-8")), "data_date": pack["data_date"],
                 "items": len(pack["items"]), "outside": len(pack["outside"]), "funds": sorted(pack["funds"]),
                 "pack_sha256": pack["pack_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
