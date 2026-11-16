"""換皮前｜現在 並排圖（D 包整合驗收的截圖巡檢）。
左邊是換皮前基準（3d-prototype 工作樹的 shots/baseline，只讀），右邊是這次的截圖，每頁的上半部（最多 2400px 高）並排存成一張。

用法：python design-previews/3d-redesign/compare-shots.py <基準資料夾> <這次的資料夾> <輸出資料夾> 名稱...
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

before_dir, after_dir, out_dir = (Path(arg) for arg in sys.argv[1:4])
out_dir.mkdir(parents=True, exist_ok=True)
GAP, LIMIT = 24, 2400
for name in sys.argv[4:]:
    left, right = Image.open(before_dir / f"{name}.png"), Image.open(after_dir / f"{name}.png")
    left, right = left.crop((0, 0, left.width, min(left.height, LIMIT))), right.crop((0, 0, right.width, min(right.height, LIMIT)))
    sheet = Image.new("RGB", (left.width + right.width + GAP, max(left.height, right.height)), (128, 128, 128))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width + GAP, 0))
    ImageDraw.Draw(sheet).rectangle((left.width, 0, left.width + GAP - 1, sheet.height), fill=(200, 60, 40))
    sheet.save(out_dir / f"{name}.png")
    print(name, left.size, right.size)
