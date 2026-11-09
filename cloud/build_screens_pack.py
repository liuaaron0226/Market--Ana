"""C1（2026-11-09）：把籌碼選股／產業資金流向的發布封存與長期核心持有，預算成雲端 Functions 資料包。

雲端不能跑 Python，也不讀本機封存目錄；這裡在 build 前把本機 API（live_server）會回的每一種回應
全部算好，build.mjs 再包進 functions/_screens-pack.js，gate.js 只原樣回傳。用同一組函式產生，
雲端與本機的欄位自然一致；本機 API 的實作是唯一來源，這裡不另算一套。

快照目錄可設定（--snapshot-root，預設 data/market-screen-snapshots）；封存雜湊不符時整個 build 停止。
產物預設寫到 cloud/.screens-pack.json（不入庫）。不含本機絕對路徑。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import finhunt_screens  # noqa: E402
import live_server  # noqa: E402
import market_screens_publish  # noqa: E402

PACK_SCHEMA = "market-screens-pack/v1"
DEFAULT_OUT = Path(__file__).resolve().parent / ".screens-pack.json"


def _dump(payload: Any) -> str:
    # 與本機 _send_json 相同的序列化（ensure_ascii=False、緊湊分隔）。
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def allowed_variants(key: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """與 live_server._send_screens 的參數驗證相同：排行看全部天數，其他頁只有第一個天數；排行／連續有買賣。"""
    tab = finhunt_screens.TAB_BY_KEY[key]
    days = tuple(tab.day_options) if tab.kind == "rank" else (tab.day_options[0],)
    sides = ("buy", "sell") if tab.kind in ("rank", "streak") else ("buy",)
    return days, sides


def build_pack(snapshot_root: Path) -> dict[str, Any]:
    snapshot_root = Path(snapshot_root)
    # build 期間同一份封存會被讀很多次；只在這個行程內快取（讀取時仍驗雜湊）。
    original_latest = market_screens_publish.latest_batch
    cache: dict[tuple[str, str], Any] = {}

    def cached_latest(root: Path, kind: str) -> Any:
        key = (str(root), kind)
        if key not in cache:
            cache[key] = original_latest(root, kind)
        return cache[key]

    market_screens_publish.latest_batch = cached_latest
    try:
        catalog = live_server.screen_catalog(snapshot_root)
        tabs, screens = {}, {}
        for key in live_server.SCREEN_TABS:
            days_options, sides = allowed_variants(key)
            tabs[key] = {"days": list(days_options), "sides": list(sides)}
            for days in days_options:
                for side in sides:
                    for inflow in (False, True):
                        payload = live_server.screen_result(snapshot_root, key, days, side, inflow)
                        screens[f"{key}|{days}|{side}|{int(inflow)}"] = _dump(payload)
        rotation = live_server.rotation_latest(snapshot_root)

        # 名單裡出現過的代號各自預算完整 membership；其他代號只需要產業象限（memberships 為空）。
        listed_codes: set[str] = set()
        for variant in market_screens_publish.publish_plan():
            if variant["key"] not in live_server.SCREEN_TABS:
                continue
            batch = cached_latest(snapshot_root, variant["kind"])
            if batch:
                listed_codes.update(str(row.get("code")) for row in batch["result"].get("items") or [] if row.get("code"))
        membership = {code: _dump(live_server.screen_membership(snapshot_root, code)) for code in sorted(listed_codes)}

        industry_by_code: dict[str, Any] = {}
        rotation_batch = cached_latest(snapshot_root, market_screens_publish.ROTATION_KIND)
        if rotation_batch is not None:
            groups = rotation_batch["result"].get("groups") or {}
            for name, codes in groups.items():
                for code in codes:
                    # 本機以 dict 順序取第一個包含此代號的產業；這裡保留同樣的「先到先得」。
                    if code in industry_by_code or code in listed_codes:
                        continue
                    industry_by_code[code] = live_server.screen_membership(snapshot_root, code)["industry"]
        core = market_screens_publish.core_holdings()
    finally:
        market_screens_publish.latest_batch = original_latest

    pack = {
        "schema": PACK_SCHEMA,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_date": catalog.get("data_date"),
        "tabs": tabs,
        "catalog": _dump(catalog),
        "screens": screens,
        "rotation": _dump(rotation),
        "core_holdings": _dump(core),
        "membership": membership,
        "industry_by_code": {code: industry for code, industry in sorted(industry_by_code.items()) if industry is not None},
    }
    pack["pack_sha256"] = hashlib.sha256(_dump({k: v for k, v in pack.items() if k not in ("built_at",)}).encode("utf-8")).hexdigest()
    return pack


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=market_screens_publish.DEFAULT_SNAPSHOT_ROOT,
                        help="market_screens_publish 的封存根目錄；驗證時指向 .tmp")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    pack = build_pack(args.snapshot_root)
    text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    if "C:\\Users" in text or "C:/Users" in text:
        raise SystemExit("資料包含本機絕對路徑，停止")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"out": args.out.name, "bytes": len(text.encode("utf-8")), "data_date": pack["data_date"],
                      "screens": len(pack["screens"]), "membership": len(pack["membership"]),
                      "industry_codes": len(pack["industry_by_code"]), "pack_sha256": pack["pack_sha256"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
