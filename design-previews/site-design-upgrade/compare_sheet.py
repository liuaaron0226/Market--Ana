"""把兩段連續畫面上下排成一張對照圖：上面是樣品，下面是網站現在的同一類動作。給本人看差在哪裡用。

用法：python design-previews/site-design-upgrade/compare_sheet.py <輸出檔名> <上排標題> <上排互動名稱> <下排標題> <下排互動名稱> [要哪幾格，例如 0,1,2,3]
例：python design-previews/site-design-upgrade/compare_sheet.py cmp-open "樣品：點開一層" sample-open-layer-390 "首頁：換分類" home-cat-switch-390
輸出 .scratch/site-design-upgrade/compare/<輸出檔名>.png（不入版控）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".scratch" / "site-design-upgrade"
FONT = "C:/Windows/Fonts/msjhbd.ttc"
INK, PAPER, FAINT = "#1d1b20", "#f3f1ec", "#6d6874"


def frames_of(name: str, picks: list[int] | None) -> list[Path]:
    files = sorted(BASE.glob(f"frames/{name}-[0-9]*.png"), key=lambda f: int(re.search(r"-(\d+)\.png$", f.name).group(1)))
    files = [f for f in files if re.fullmatch(re.escape(name) + r"-\d+\.png", f.name)]
    return [files[i] for i in picks if i < len(files)] if picks else files


def strip(name: str, title: str, picks: list[int] | None, cell_w: int, labels: list[str]) -> Image.Image:
    files = frames_of(name, picks)
    tiles = []
    for file in files:
        image = Image.open(file).convert("RGB")
        tiles.append(image.resize((cell_w, round(image.height * cell_w / image.width)), Image.LANCZOS))
    height = max(tile.height for tile in tiles)
    gap, head = 14, 64
    sheet = Image.new("RGB", (cell_w * len(tiles) + gap * (len(tiles) - 1), height + head), PAPER)
    draw = ImageDraw.Draw(sheet)
    draw.text((0, 4), title, fill=INK, font=ImageFont.truetype(FONT, 28))
    small = ImageFont.truetype(FONT, 17)
    for index, tile in enumerate(tiles):
        x = index * (cell_w + gap)
        sheet.paste(tile, (x, head))
        draw.rectangle((x, head, x + cell_w - 1, head + height - 1), outline="#d6cfc0")
        if index < len(labels):
            draw.text((x + 2, 40), labels[index], fill=FAINT, font=small)
    return sheet


def main() -> None:
    out, top_title, top, bottom_title, bottom = sys.argv[1:6]
    picks = [int(n) for n in sys.argv[6].split(",")] if len(sys.argv) > 6 else None
    labels = sys.argv[7].split(",") if len(sys.argv) > 7 else []
    cell_w = 300 if top.endswith("-390") else 520
    upper, lower = strip(top, top_title, picks, cell_w, labels), strip(bottom, bottom_title, picks, cell_w, labels)
    pad = 28
    sheet = Image.new("RGB", (max(upper.width, lower.width) + pad * 2, upper.height + lower.height + pad * 3), PAPER)
    sheet.paste(upper, (pad, pad))
    sheet.paste(lower, (pad, upper.height + pad * 2))
    (BASE / "compare").mkdir(parents=True, exist_ok=True)
    sheet.save(BASE / "compare" / f"{out}.png")
    print("saved", BASE / "compare" / f"{out}.png", sheet.size)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
