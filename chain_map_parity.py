"""資金版圖台股加總的對帳（不是單元測試；要本機真實資料）：用 chain_map_flows.load_tw 算幾個月 × 幾檔，
和樣品 stock_flows.json 比，印最大差距。只印代號、月份與差距，不印資料本身以外的東西。

  python chain_map_parity.py --data <Market--Ana 的 data 資料夾> --sample <stock_flows.json> [--chain seeds/ai-chain-map.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import chain_map_flows as cm

MONTHS = ("2021-01", "2023-06", "2025-11", "2026-03", "2026-10")
CODES = ("2330", "2317", "2454", "3711", "6488", "3324", "8996", "6669")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--chain", type=Path, default=cm.CHAIN_PATH)
    args = parser.parse_args(argv)
    sample = json.loads(args.sample.read_text(encoding="utf-8"))
    tw_codes, _ = cm.definition(json.loads(args.chain.read_text(encoding="utf-8")))
    tw = cm.load_tw(args.data, tw_codes)
    months, i0 = sample["months"], sample["inst0"]
    worst = {"t": (0.0, None), "i": (0.0, None)}
    for code in [c for c in CODES if c in sample["tw"]]:
        for ym in MONTHS:
            k = months.index(ym)
            mine_t = cm.r(tw["t"].get(code, {}).get(ym, 0.0) / 1e8)
            diff = abs((mine_t or 0) - (sample["tw"][code]["t"][k] or 0))
            print(f"{code} {ym} 成交 樣品 {sample['tw'][code]['t'][k]} 重算 {mine_t} 差 {diff:g}")
            if diff > worst["t"][0]:
                worst["t"] = (diff, f"{code} {ym}")
            if k >= i0:
                mine_i = cm.r(tw["i"].get(code, {}).get(ym, 0.0) / 1e8)
                diff = abs((mine_i or 0) - (sample["tw"][code]["i"][k - i0] or 0))
                print(f"{code} {ym} 法人 樣品 {sample['tw'][code]['i'][k - i0]} 重算 {mine_i} 差 {diff:g}")
                if diff > worst["i"][0]:
                    worst["i"] = (diff, f"{code} {ym}")
    print(json.dumps({"max_diff_turnover_億": worst["t"], "max_diff_inst_億": worst["i"],
                      "tw_through": tw["through"], "inst_first": tw["inst_first"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
