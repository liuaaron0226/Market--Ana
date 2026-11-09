"""B1：產生雲端「和 0050 比」端點測試用的 fixture。

用真資料 11-06 母體（只留 2330、6488，1102 因此是母體外）：
1. 以 build_compare_pack.build_pack 產生資料包 → fixtures/compare-pack.json；
2. 啟動本機 live_server（同一個母體快照），錄下一組路徑的實際 HTTP 狀態與內容 → fixtures/compare-local.json。
JS 測試（compare-api.test.js）拿同一份資料包餵給 gate，逐條比對雲端回應與本機錄到的回應。
重跑：python cloud/test/make_compare_fixture.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))

import build_compare_pack  # noqa: E402
import live_server  # noqa: E402
from test_benchmark_compare import crafted_universe  # noqa: E402

LOCAL_PATHS = [
    "/api/stocks/2330/vs-0050",
    "/api/stocks/6488/vs-0050",
    "/api/stocks/0050/vs-0050",
    "/api/stocks/1102/vs-0050",
    "/api/stocks/9999/vs-0050",
    "/api/compare-0050?codes=2330",
    "/api/compare-0050?codes=2330,6488",
    "/api/compare-0050?codes=6488,1102,9999,2330,2330",
    # 參數錯誤：本機回 400，雲端必須同樣拒絕
    "/api/stocks/23/vs-0050",
    "/api/compare-0050",
    "/api/compare-0050?codes=",
    "/api/compare-0050?codes=2330,",
    "/api/compare-0050?codes=2330,6488,0050,1102,2454,2317",
    "/api/compare-0050?codes=2330&codes=6488",
    "/api/compare-0050?codes=abcd",
]


def fetch(base: str, path: str) -> tuple[int, str]:
    try:
        response = urllib.request.urlopen(base + path, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read().decode("utf-8")


def main() -> int:
    universe = crafted_universe()
    pack = build_compare_pack.build_pack(universe)
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.universe_path = universe
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        local = {path: dict(zip(("status", "body"), fetch(base, path))) for path in LOCAL_PATHS}
    finally:
        server.shutdown()
        server.server_close()
    # fixture 只保留測試路徑會用到的代號，避免把 1,600 檔母體外名單塞進 repo。
    keep = {"2330", "6488", "0050", "1102"}
    slim = {**pack, "items": {k: v for k, v in pack["items"].items() if k in keep},
            "outside": {k: v for k, v in pack["outside"].items() if k in keep}}
    fixtures = HERE / "fixtures"
    (fixtures / "compare-pack.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    (fixtures / "compare-local.json").write_text(json.dumps(local, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"items": len(pack["items"]), "outside": len(pack["outside"]), "local_paths": len(local),
                      "full_pack_bytes": len(build_compare_pack._dump(pack).encode("utf-8"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
