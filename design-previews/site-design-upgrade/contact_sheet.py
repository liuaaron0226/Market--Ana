"""把 audit_capture.py 拍的連續畫面排成一張橫條圖（每個互動一張），方便一眼看過場的中間格。

用法：python design-previews/site-design-upgrade/contact_sheet.py [名稱裡要有的字，例如 home-cat]
輸出 .scratch/site-design-upgrade/sheets/<互動名稱>.png（不入版控）。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
FRAMES = ROOT / ".scratch" / "site-design-upgrade" / "frames"
SHEETS = ROOT / ".scratch" / "site-design-upgrade" / "sheets"


def main() -> None:
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    SHEETS.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[Path]] = defaultdict(list)
    for file in sorted(FRAMES.glob("*.png")):
        found = re.match(r"(.+)-(\d+)\.png$", file.name)
        if found and want in found.group(1):
            groups[found.group(1)].append(file)
    for name, files in groups.items():
        files.sort(key=lambda f: int(re.search(r"-(\d+)\.png$", f.name).group(1)))
        cell_w = 250 if name.endswith("-390") else 420
        tiles = []
        for file in files:
            image = Image.open(file).convert("RGB")
            tiles.append(image.resize((cell_w, round(image.height * cell_w / image.width)), Image.LANCZOS))
        height = max(tile.height for tile in tiles)
        sheet = Image.new("RGB", (cell_w * len(tiles) + 6 * (len(tiles) - 1), height), "#888")
        for index, tile in enumerate(tiles):
            sheet.paste(tile, (index * (cell_w + 6), 0))
            ImageDraw.Draw(sheet).rectangle((index * (cell_w + 6), 0, index * (cell_w + 6) + 22, 16), fill="#000")
            ImageDraw.Draw(sheet).text((index * (cell_w + 6) + 4, 2), str(index), fill="#fff")
        sheet.save(SHEETS / f"{name}.png")
    print(len(groups), "張，存到", SHEETS)


if __name__ == "__main__":
    main()
