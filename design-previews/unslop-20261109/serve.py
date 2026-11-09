"""unslop 範例預覽伺服器：只服務 design-previews 複本，不碰 8897。

用法：python design-previews/unslop-20261109/serve.py [port] [web|web-bold]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa: E402
from shoot import make_handler  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8947
folder = sys.argv[2] if len(sys.argv) > 2 else "web"
server = live_server.MarketHTTPServer(("127.0.0.1", port), make_handler(HERE / folder))
print(f"unslop preview: http://127.0.0.1:{port}/minimal.html", flush=True)
server.serve_forever(poll_interval=0.25)
