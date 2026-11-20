"""暗色桌面配色：K 線上每一種顏色在每一種底色上的對比度（WCAG 2 的算法）。

底色＝畫 K 線的那塊板（--panel）。半透明的東西（布林通道的線 55%、量柱 45%）先和底色混好再算。
門檻：線條與 K 棒 ≥ 3:1（WCAG 1.4.11 非文字），字 ≥ 4.5:1。布林通道現在的深色本來就刻意畫得淡，列出來對照。
量的是網站真正在用的（web/settings.css）；加 --samples 改量樣品檔 dark-desks.css。
用法：python design-previews/ui-polish/dark-contrast.py [--md] [--samples]   （有不過門檻的結束碼 1）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CURRENT = {"name": "墨黑（現在）", "panel": "#15151b", "bg": "#0b0b0e", "desk": "#070709"}
NAMES = {"slate": "深藍灰", "umber": "炭褐", "graphite": "石墨", "plum": "暮紫"}
# 深色主題的顏色（web/minimal.css :root、web/chart-studies.css）——配色不改這些
INK, DIM, FAINT = "#f2f0ec", "#b0adb6", "#8d8995"
MARKS = [
    ("紅 K（漲）", "#ff6868", 1, 3), ("綠 K（跌）", "#2fd09a", 1, 3),
    ("MA 橘（MA18）", "#e8622c", 1, 3), ("MA 金（MA45）", "#d9a94b", 1, 3), ("MA 藍", "#4f8bd4", 1, 3),
    ("MA 紫", "#a98be8", 1, 3), ("MA 青", "#3fbfb0", 1, 3), ("MA 褐", "#c07a42", 1, 3), ("MA 玫瑰", "#e07aa8", 1, 3),
    ("量柱 紅 45%", "#ff6868", .45, None), ("量柱 綠 45%", "#2fd09a", .45, None),
    ("布林通道線 55%", DIM, .55, None),
    ("價位數字（次要字）", DIM, 1, 4.5), ("日期與小字", FAINT, 1, 4.5), ("主要字", INK, 1, 4.5),
    ("利多記號（紅字）", "#ff6868", 1, 4.5), ("利空記號（綠字）", "#2fd09a", 1, 4.5),
]


def rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(color: tuple[float, float, float]) -> float:
    lin = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in color]
    return .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2]


def contrast(fg: str, bg: str, alpha: float = 1) -> float:
    f, b = rgb(fg), rgb(bg)
    mixed = tuple(f[i] * alpha + b[i] * (1 - alpha) for i in range(3))
    hi, lo = sorted((luminance(mixed), luminance(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def palettes() -> list[dict]:
    """量的是網站真正在用的那幾種（web/settings.css 的深色桌面背景）；加 --samples 改量樣品檔（dark-desks.css，含沒被選上的）。"""
    source = HERE / "dark-desks.css" if "--samples" in sys.argv else HERE.parents[1] / "web" / "settings.css"
    css = source.read_text(encoding="utf-8")
    variables = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", css))
    found = [CURRENT]
    for key, body in re.findall(r'\[data-theme="dark"\]\[data-desk="(\w+)"\]\s*\{([^}]*)\}', css):
        values = {}
        for name, value in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6}|var\(--[\w-]+\))", body):
            values[name] = variables[value[4:-1]] if value.startswith("var(") else value
        found.append({"name": NAMES.get(key, key), "panel": values["panel"], "bg": values["bg"], "desk": values["desk"]})
    if len(found) < 2:
        raise SystemExit(f"no dark desks found in {source}")
    return found


def main() -> int:
    rows, failed = [], []
    pals = palettes()
    for label, color, alpha, need in MARKS:
        cells = []
        for pal in pals:
            ratio = contrast(color, pal["panel"], alpha)
            if need and ratio < need:
                failed.append(f"{pal['name']} {label} {ratio:.2f} < {need}")
            cells.append(ratio)
        rows.append((label, need, cells))
    # 收盤價籤：K 棒顏色當底、--bg 當字
    for label, color in (("收盤價籤（紅底深字）", "#ff6868"), ("收盤價籤（綠底深字）", "#2fd09a")):
        cells = [contrast(pal["bg"], color) for pal in pals]
        for pal, ratio in zip(pals, cells):
            if ratio < 4.5:
                failed.append(f"{pal['name']} {label} {ratio:.2f} < 4.5")
        rows.append((label, 4.5, cells))
    rows.append(("板和桌面的明暗差", None, [contrast(pal["panel"], pal["desk"]) for pal in pals]))
    if "--md" in sys.argv:
        print("| K 線上的東西 | 門檻 | " + " | ".join(pal["name"] for pal in pals) + " |")
        print("|---|---|" + "---|" * len(pals))
        for label, need, cells in rows:
            print(f"| {label} | {'≥ ' + str(need) if need else '—'} | " + " | ".join(f"{c:.1f}" for c in cells) + " |")
    else:
        print(f"{'':24}" + "".join(f"{pal['name']:>12}" for pal in pals))
        for label, need, cells in rows:
            print(f"{label:24}" + "".join(f"{c:12.2f}" for c in cells) + (f"   need {need}" if need else ""))
    for line in failed:
        print("FAIL", line)
    print("ALL PASS" if not failed else f"{len(failed)} below threshold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
