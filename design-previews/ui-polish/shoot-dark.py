"""暗色桌面配色樣品的並排截圖（shoot-samples.py dark 會叫這一支的 run）。
每一種配色拍兩段：上半（標題與 K 線）、下半（量柱、分類鍵、清單、輸入框），各排成一張五格的對照圖，第一格是現在的墨黑。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

DESKS = [("", "墨黑（現在）"), ("slate", "深藍灰"), ("umber", "炭褐"), ("graphite", "石墨"), ("plum", "暮紫")]


def run(page, base: str, out: Path, sheet) -> None:
    tops, bottoms = [], []
    for key, _ in DESKS:
        page.goto(f"{base}/design-previews/ui-polish/dark.html?desk={key}")
        page.wait_for_selector("#chartHost canvas")
        page.wait_for_timeout(700)
        file = out / "_frame.png"
        page.evaluate("() => window.scrollTo(0, 150)")
        page.wait_for_timeout(150)
        page.screenshot(path=str(file))
        tops.append(Image.open(file).convert("RGB").copy())
        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(150)
        page.screenshot(path=str(file))
        bottoms.append(Image.open(file).convert("RGB").copy())
    labels = [name for _, name in DESKS]
    sheet(tops, labels, "暗色桌面配色：標題與 K 線（K 線顏色都一樣，只換底）", out / "dark-top.png")
    sheet(bottoms, labels, "暗色桌面配色：量柱、分類鍵、清單與輸入框", out / "dark-bottom.png")
