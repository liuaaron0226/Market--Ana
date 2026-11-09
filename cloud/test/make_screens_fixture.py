"""C1：產生雲端籌碼選股端點測試用的 fixture。

用 test_market_screens_publish.inputs() 的合成資料封存到暫存目錄，再：
1. 以 build_screens_pack.build_pack 產生資料包 → fixtures/screens-pack.json；
2. 啟動本機 live_server（指向同一個暫存目錄），把一組路徑的實際 HTTP 狀態與內容錄下 → fixtures/screens-local.json。
JS 測試（screens-api.test.js）拿同一份資料包餵給 gate，逐條比對雲端回應與本機錄到的回應。
重跑：python cloud/test/make_screens_fixture.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))

import build_screens_pack  # noqa: E402
import live_server  # noqa: E402
import market_screens_publish as msp  # noqa: E402
from test_market_screens_publish import inputs  # noqa: E402

# 本機與雲端都要一致的路徑：每個分頁的預設與變化、兩種 membership、參數錯誤。
LOCAL_PATHS = [
    "/api/screens",
    "/api/screens?key=foreign_net_rank",
    "/api/screens?key=foreign_net_rank&days=5&side=sell",
    "/api/screens?key=foreign_net_rank&days=20&side=buy&only_inflow=1",
    "/api/screens?key=trust_net_rank&days=3&side=buy&only_inflow=0",
    "/api/screens?key=foreign_streak&side=sell",
    "/api/screens?key=trust_streak&days=1&side=buy&only_inflow=1",
    "/api/screens?key=rotation_relative_value",
    "/api/screens?key=rotation_etf_constituent",
    "/api/screens?key=broker_top15_net&days=1",
    "/api/screens?key=broker_concentration",
    "/api/screens?key=broker_streak",
    "/api/rotation",
    "/api/core-holdings",
    "/api/screens/membership?code=6488",
    "/api/screens/membership?code=8069",
    "/api/screens/membership?code=1101",
    "/api/screens/membership?code=2330",
    # 參數錯誤：本機回 400，雲端必須同樣拒絕
    "/api/screens?key=curated_list",
    "/api/screens?key=nope",
    "/api/screens?key=foreign_net_rank&days=2",
    "/api/screens?key=foreign_streak&days=3",
    "/api/screens?key=rotation_relative_value&side=sell",
    "/api/screens?key=foreign_net_rank&side=hold",
    "/api/screens?key=foreign_net_rank&only_inflow=2",
    "/api/screens?key=foreign_net_rank&key=trust_net_rank",
    "/api/screens?days=1",
    "/api/screens?key=foreign_net_rank&days=x",
    "/api/screens/membership",
    "/api/screens/membership?code=23",
    "/api/screens/membership?code=abcd",
    "/api/screens/membership?code=2330&code=6488",
]


def fetch(base: str, path: str) -> tuple[int, str]:
    try:
        response = urllib.request.urlopen(base + path, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read().decode("utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        msp.publish(root, data_date="2026-11-06", published_at="2026-11-06T10:00:00Z", **inputs())
        pack = build_screens_pack.build_pack(root)
        server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        server.screen_root = root
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            local = {path: dict(zip(("status", "body"), fetch(base, path))) for path in LOCAL_PATHS}
        finally:
            server.shutdown()
            server.server_close()
    fixtures = HERE / "fixtures"
    (fixtures / "screens-pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    (fixtures / "screens-local.json").write_text(json.dumps(local, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"screens": len(pack["screens"]), "membership": len(pack["membership"]),
                      "local_paths": len(local)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
