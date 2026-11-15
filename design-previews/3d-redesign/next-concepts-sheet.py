"""把 Blender 算出來的六張概念圖加上標題、一句說明與「哪些數字是示意」，各存一張成品，另外拼一張總覽。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/next-concepts-sheet.py <圖片資料夾>"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

folder = Path(sys.argv[1])
BOLD, REGULAR = "C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc"
INK, DIM, NOTE, PAPER = (28, 27, 34), (95, 93, 101), (154, 106, 28), (251, 250, 246)

# (場景, 標題, 在哪一頁, 怎麼讀, 數字的來源)
CONCEPTS = [
    ("tank", "① 今日股票水位", "自選與庫存頁", "水面是目前水位，槽前面金色的線是今日建議，右邊尺上金色那一段是容忍帶。", "數字是樣本資料。"),
    ("stairs", "② 價位階梯", "個股頁", "每一階的高度照它離現價幾個百分點：矮的那一階很近，高的那一階很遠。球停在現價。", "數字是樣本資料（2330）。"),
    ("blocks", "③ 庫存總覽", "自選與庫存頁", "每檔持股一塊積木：底面積是占比，往上長是賺、往下凹是賠。", "前三檔是樣本資料；樣本裡沒有賠錢的，「示意甲、乙」是為了示範往下凹加的。"),
    ("bins", "④ 隔日三區歷史分布", "首頁", "三個透明的槽，裝的量就是歷史上各占幾成。", "數字是樣本資料。"),
    ("ramps", "⑤ 短中長期走勢", "首頁", "往上斜是偏多、平的是盤整、往下斜是偏空。", "三個狀態是示意：樣本資料裡走勢判讀還沒產生。"),
    ("terraces", "⑥ 大戶持股", "個股頁", "每週一片，千張大戶比例高就高，連起來看得出一路往上還是往下；本週那一片上色。", "最後兩週是樣本資料，前十週是示意。"),
]


def wrap(draw, body, font, width):
    lines, line = [], ""
    for char in body:
        if draw.textlength(line + char, font=font) > width:
            lines.append(line)
            line = char
        else:
            line += char
    return lines + [line]


finished = []
for name, title, page, how, source in CONCEPTS:
    render = Image.open(folder / f"render-{name}.png").convert("RGB")
    width, pad = render.width, 36
    probe = ImageDraw.Draw(render)
    title_font, page_font, body_font = ImageFont.truetype(BOLD, 44), ImageFont.truetype(REGULAR, 26), ImageFont.truetype(REGULAR, 28)
    how_lines, source_lines = wrap(probe, how, body_font, width - pad * 2), wrap(probe, source, body_font, width - pad * 2)
    band = pad + 60 + 14 + 40 * (len(how_lines) + len(source_lines)) + 10 + pad
    card = Image.new("RGB", (width, render.height + band), PAPER)
    card.paste(render, (0, 0))
    draw = ImageDraw.Draw(card)
    y = render.height + pad
    draw.text((pad, y), title, fill=INK, font=title_font)
    draw.text((pad + draw.textlength(title, font=title_font) + 16, y + 16), page, fill=DIM, font=page_font)
    y += 60 + 14
    for line in how_lines:
        draw.text((pad, y), line, fill=INK, font=body_font)
        y += 40
    y += 10
    for line in source_lines:
        draw.text((pad, y), line, fill=NOTE, font=body_font)
        y += 40
    path = folder / f"concept-{CONCEPTS.index((name, title, page, how, source)) + 1}-{name}.png"
    card.save(path)
    finished.append(card)
    print(path.name, card.size)

# 總覽：兩欄三列，每張縮到同樣的寬度
cell = 640
small = [image.resize((cell, round(image.height * cell / image.width))) for image in finished]
row_heights = [max(small[i].height, small[i + 1].height) for i in range(0, len(small), 2)]
gap = 20
sheet = Image.new("RGB", (cell * 2 + gap, sum(row_heights) + gap * (len(row_heights) - 1)), (110, 110, 110))
y = 0
for row, height in enumerate(row_heights):
    for col in range(2):
        sheet.paste(small[row * 2 + col], (col * (cell + gap), y))
    y += height + gap
sheet.save(folder / "concepts-all.png")
print("concepts-all.png", sheet.size)
