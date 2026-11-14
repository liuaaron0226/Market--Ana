"""資金地形版面計算測試用的夾具：取自已發布的產業資金流向（34 個產業，含今日成交額與成員檔數）。

既有的選股夾具（fixtures/screens-pack.json）是合成資料，只有 2 個產業，而且沒有今日成交額與成員檔數，驗不了地形。
這份直接從本機的雲端選股資料包（cloud/.screens-pack.json，不入版控）抽出產業資金流向那一段的公開彙總數字：
各產業的成交額、成員檔數、資金狀態。不含任何使用者資料。
重跑：python cloud/test/make_rotation_fixture.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent / ".screens-pack.json"
OUT = HERE / "fixtures" / "rotation-terrain.json"


def main() -> None:
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    rotation = json.loads(pack["rotation"])
    fixture = {
        "source": {"data_date": rotation["data_date"], "snapshot_sha256": rotation["snapshot_sha256"]},
        "quadrants": {key: {"label": value["label"]} for key, value in rotation["quadrants"].items()},
        "bubbles": rotation["latest"]["bubbles"],
    }
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(fixture['bubbles'])} 個產業 → {OUT}")


if __name__ == "__main__":
    main()
