"""雲端個股分析資料包（票 06 延伸，本人 11-07 選「現在就把個股頁上雲」）。

雲端 Functions 不能跑 Python，所以在本機用 twon_analysis.analyze 先算好：
籌碼卡明細近 20 天出現過的所有股票 × 三種分析方案，日期取最新交易日。
輸出 cloud/.stock-pack.json（不入庫），由 build.mjs 打包成 functions/_stock-pack.js。
只讀 twon_analysis 與 web/detail-data.js，不修改它們。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from twon_analysis.analyze import analyze  # noqa: E402
from twon_analysis.profiles import DEFAULT, available_presets  # noqa: E402

OUT = Path(__file__).resolve().parent / ".stock-pack.json"


def chip_codes() -> list[str]:
    text = (ROOT / "web" / "detail-data.js").read_text(encoding="utf-8").strip()
    data = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))
    codes = {row["code"] for day in data["by_date"].values() for rows in day.values() for row in rows}
    return sorted(codes)


def main() -> None:
    presets = [p["id"] for p in available_presets()]
    codes = chip_codes()
    started = time.time()
    results: dict[str, dict] = {}
    as_of_seen: set[str] = set()
    failures: list[str] = []
    for i, code in enumerate(codes, 1):
        for preset in presets:
            try:
                r = analyze(code, "9999-12-31", preset_id=preset)
            except Exception as error:  # 單檔失敗不中止整批，記下來報告
                failures.append(f"{code}/{preset}: {type(error).__name__}")
                continue
            # 查無代號的結果 as_of 會是請求的 9999-12-31，不算進資料日；停牌股可能停在較早日期，照實保留。
            if r.get("as_of") and r["as_of"] != "9999-12-31":
                as_of_seen.add(r["as_of"])
            results.setdefault(code, {})[preset] = r
        if i % 25 == 0:
            print(f"{i}/{len(codes)} 檔，{time.time() - started:.0f} 秒", flush=True)
    if len(as_of_seen) > 1:
        print(f"注意：有股票的最新資料日較早（例如停牌）：{sorted(as_of_seen)}；雲端以最新的 {max(as_of_seen)} 為資料日", flush=True)
    pack = {
        "as_of": max(as_of_seen) if as_of_seen else None,
        "presets": presets,
        "default_preset": DEFAULT,
        "results": results,
    }
    OUT.write_text(json.dumps(pack, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"codes": len(codes), "with_result": len(results), "failures": failures[:20], "failure_count": len(failures),
                      "as_of": pack["as_of"], "seconds": round(time.time() - started), "bytes": OUT.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
