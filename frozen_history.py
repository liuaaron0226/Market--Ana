"""測試用：把上市／上櫃行情庫凍結在指定資料日，讓寫死日期的契約測試不隨每日增量漂移。

只在測試的 setUpModule 使用：用 SQLite backup API 複製正式行情庫到暫存目錄，刪掉晚於 ``as_of`` 的列，
再把 live_server、雲端建置模組與 analyze_v2 的預設路徑指向這份副本。正式資料庫只讀、不改。
"""
from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
STOCK_HISTORY_DB = ROOT / "data/stock-history/stock-history.sqlite"
OTC_HISTORY_DB = ROOT / "data/otc-history/otc_history.sqlite"


def frozen_copy(source: Path, target: Path, as_of: str) -> Path:
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        dst.execute("DELETE FROM daily WHERE date > ?", (as_of,))
        dst.execute("DELETE FROM source_days WHERE date > ?", (as_of,))
        dst.commit()
    return target


def freeze_live_server(as_of: str) -> Callable[[], None]:
    """凍結 live_server 用到的兩個行情庫；回傳還原函式（同時清掉暫存副本）。"""
    import live_server
    from twon_analysis import analyze_v2 as analyze_v2_module

    tmp = tempfile.TemporaryDirectory()
    stock = frozen_copy(STOCK_HISTORY_DB, Path(tmp.name) / "stock-history.sqlite", as_of)
    otc = frozen_copy(OTC_HISTORY_DB, Path(tmp.name) / "otc_history.sqlite", as_of)
    build = live_server.cloud_build_module()
    saved: dict[str, Any] = {
        "ls_stock": live_server.STOCK_HISTORY_DB, "ls_otc": live_server.OTC_HISTORY_DB,
        "build_stock": build.HISTORY_DB, "build_otc": build.OTC_HISTORY_DB,
        "v2_defaults": analyze_v2_module.analyze_v2.__defaults__,
    }

    def reset_caches() -> None:
        live_server._LISTED_CATALOG = None
        live_server._OTC_CATALOG = None
        live_server._OTC_NAME_INDEX = None
        live_server._HISTORY_STATE = None
        live_server._UNIVERSE = None

    live_server.STOCK_HISTORY_DB, live_server.OTC_HISTORY_DB = stock, otc
    build.HISTORY_DB, build.OTC_HISTORY_DB = stock, otc
    analyze_v2_module.analyze_v2.__defaults__ = (stock, otc)
    reset_caches()

    def restore() -> None:
        live_server.STOCK_HISTORY_DB, live_server.OTC_HISTORY_DB = saved["ls_stock"], saved["ls_otc"]
        build.HISTORY_DB, build.OTC_HISTORY_DB = saved["build_stock"], saved["build_otc"]
        analyze_v2_module.analyze_v2.__defaults__ = saved["v2_defaults"]
        reset_caches()
        tmp.cleanup()

    return restore
