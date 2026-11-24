"""數一數全站樣式與程式裡，動態用了幾種時間、幾種曲線（盤點表「全站共通」那一節的數字來源）。

用法：python design-previews/site-design-upgrade/motion_census.py
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKENS = {"--t-lift": "350ms", "--t-press": "80ms"}
EASE = re.compile(r"var\(--ease[a-z-]*|cubic-bezier\([^)]*\)|(?<![-\w])(?:linear|ease-out|ease-in-out|ease-in|ease)(?![-\w])")


def main() -> None:
    durations: collections.Counter = collections.Counter()
    tokens: collections.Counter = collections.Counter()
    easings: collections.Counter = collections.Counter()
    styles = sorted((ROOT / "web").glob("*.css")) + [ROOT / "cloud" / "public-src" / "account.css"]
    for file in styles:
        text = file.read_text(encoding="utf-8")
        for found in re.finditer(r"(transition|animation)\s*:\s*([^;}]+)", text):
            declaration = found.group(2)
            for number, unit in re.findall(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b", declaration):
                value = float(number) * (1 if unit == "ms" else 1000)
                if value > 1:
                    durations[int(value)] += 1
            for name in re.findall(r"var\((--t-[a-z]+)", declaration):
                tokens[name] += 1
            for curve in EASE.findall(declaration):
                easings[curve.replace("var(", "")] += 1
    script_durations: collections.Counter = collections.Counter()
    script_curves: collections.Counter = collections.Counter()
    for file in sorted((ROOT / "web").glob("*.js")):
        text = file.read_text(encoding="utf-8")
        for found in re.finditer(r"duration:\s*([^,}\n]+)", text):
            script_durations[found.group(1).strip()[:28]] += 1
        for found in re.finditer(r"cubic-bezier\([^)]*\)", text):
            script_curves[found.group(0)] += 1
    print("樣式表裡寫死的時間（毫秒：次數）：", sorted(durations.items()))
    print("幾種：", len(durations), "；用變數的：", dict(tokens))
    print("樣式表裡的曲線：", easings.most_common())
    print("程式（.animate）裡的時間：", script_durations.most_common(30))
    print("程式裡的曲線：", script_curves.most_common())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
