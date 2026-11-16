"""預測帳本的鎖定原件存回雲端儲存（R2）／從雲端還原（C 票第 6 片）。

帳本是唯一的事實來源，只能新增；所以這裡也只新增：
- push：本機帳本每個檔案上傳到 ``forecast-ledger/<相對路徑>``；雲端已有同內容就略過，內容不同就丟
  ``LedgerStoreConflict``，絕不覆寫。
- pull：把雲端 ``forecast-ledger/`` 底下的物件還原到本機帳本；本機已有不同內容就丟錯，不覆蓋。
  海外原件（``sources/``，以內容雜湊命名）留在雲端，不拉回本機。

刻意不放進 state_sync 的工作集（本人 2026-11-15 同意的技術選擇）：工作集是整包換 manifest、會清理沒被引用的
``state/`` 物件，由三支既有排程共用 ``market-state-write`` 鎖。帳本用自己的前綴，不受清理影響、也不用搶那把鎖。
store 是 state_sync 的 R2Store 或 DirectoryStore（有 put／get／keys）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

PREFIX = "forecast-ledger/"
# 海外原件（D 票）：檔名是內容的雜湊，同名必定同內容，只會越來越多；還原時不拉回本機（寫預測用不到舊原件）。
ARCHIVE_DIR = "sources/"


class LedgerStoreConflict(Exception):
    """雲端或本機已有同一路徑、但內容不同的帳本檔案；只能新增的帳本不覆寫。"""


def _local_files(ledger: Path) -> list[Path]:
    if not ledger.is_dir():
        return []
    # 寫入中的暫存檔（.<name>.<pid>.partial）不是帳本紀錄。
    return sorted(path for path in ledger.rglob("*") if path.is_file() and not path.name.endswith(".partial"))


def push(ledger: Path | str, store, prefix: str = PREFIX) -> dict[str, int]:
    # 先讀再寫（check-then-act）：晚間與晨間兩支排程各有自己的鎖，但寫的是不同的檔案（各自的預測與漏記、
    # 自己的 runs/；對帳只有晚間寫；sources/ 以內容雜湊命名），所以同一路徑不會有兩個寫入者。
    ledger = Path(ledger)
    uploaded = unchanged = 0
    for path in _local_files(ledger):
        key = prefix + path.relative_to(ledger).as_posix()
        data = path.read_bytes()
        remote = store.get(key)
        if remote is None:
            store.put(key, data)
            uploaded += 1
        elif remote == data:
            unchanged += 1
        else:
            raise LedgerStoreConflict(f"雲端 {key} 已有不同內容；帳本只能新增，不覆寫")
    return {"uploaded": uploaded, "unchanged": unchanged}


def _relative(key: str, prefix: str) -> PurePosixPath:
    relative = PurePosixPath(key[len(prefix):])
    # 反斜線與磁碟代號在 Windows 上會被當成路徑分隔或根目錄，一律拒絕，避免寫到帳本資料夾外面。
    if (not relative.parts or relative.is_absolute() or ".." in relative.parts
            or any("\\" in part or ":" in part for part in relative.parts)):
        raise ValueError(f"雲端物件路徑不合法：{key}")
    return relative


def pull(store, ledger: Path | str, prefix: str = PREFIX) -> dict[str, int]:
    ledger = Path(ledger)
    downloaded = unchanged = 0
    for key in store.keys(prefix):
        if not key.startswith(prefix) or key.endswith("/") or key[len(prefix):].startswith(ARCHIVE_DIR):
            continue
        target = ledger.joinpath(*_relative(key, prefix).parts)
        data = store.get(key)
        if data is None:
            continue
        if target.exists():
            if target.read_bytes() != data:
                raise LedgerStoreConflict(f"本機 {target} 與雲端 {key} 內容不同；不覆蓋")
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        downloaded += 1
    return {"downloaded": downloaded, "unchanged": unchanged}


def main(argv: list[str] | None = None) -> int:
    from state_sync import DirectoryStore, R2Store

    parser = argparse.ArgumentParser(description="預測帳本存回 R2／從 R2 還原（只新增、不覆寫）")
    parser.add_argument("command", choices=["push", "pull"])
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--store-dir", type=Path, help="以本機目錄模擬 R2（不給就用環境變數連 R2）")
    args = parser.parse_args(argv)
    store = DirectoryStore(args.store_dir) if args.store_dir else R2Store.from_env()
    try:
        result = push(args.ledger, store) if args.command == "push" else pull(store, args.ledger)
    except (LedgerStoreConflict, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
