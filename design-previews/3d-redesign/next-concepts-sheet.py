"""把 Blender 算出來的六張概念圖加上標題、一句說明與「哪些數字是示意」，各存一張成品，另外拼一張總覽。
加深版的三個（會動的）另外存成 deeper-<名稱>.png（最後一格加說明）與 deeper-<名稱>.gif（整串畫面接成動圖，要有 ffmpeg）。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/next-concepts-sheet.py <圖片資料夾>"""
import shutil
import subprocess
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

# ---------- 加深版（會動的三個）：最後一格加上說明存成圖，整串畫面接成動圖 ----------
MOTION = [
    ("tank2", "① 今日股票水位（加深版）", "自選與庫存頁",
     "整個槽是總資金：深色是股票、上面淺色那一層是現金，交界就是目前水位。上方每一塊砝碼是一分訊號，砝碼落下，金色的建議線就被壓低——"
     "看得到建議是被哪幾組壓下來的；半透明的是還沒接上的訊號。金色的帶子是容忍帶，旁邊的圓點是連續確認的天數。",
     "數字是樣本資料；砝碼壓低多少只示意方向，實際換算照現行的分數表。"),
    ("blocks2", "③ 庫存總覽（加深版）", "自選與庫存頁",
     "每一檔的高度就是錢：灰色是投入的成本，紅色是賺到的；賠錢的那一檔比成本矮，缺的那一塊用半透明的綠色標出來。後面的牆是金額刻度。"
     "動起來時柱子先長到成本，再長（或縮）到現在的市值。",
     "前三檔是樣本資料；「示意甲、乙」是為了示範賠錢加的。"),
    ("jacks", "⑤ 短中長期走勢（加深版）", "首頁",
     "每個結論背後是幾個指標各投一票，每一票是一根撐竿：偏空的（綠）撐左邊、偏多的（紅）撐右邊，哪邊多，箭頭就往另一邊低下去。"
     "看得到是幾票對幾票、是哪幾個指標。",
     "票是示意：樣本資料裡走勢判讀還沒產生；票數照現行規則（短期 5 票、中期與長期各 4 票）。"),
]
for name, title, page, how, source in MOTION:
    still = folder / f"render-{name}.png"
    if not still.exists():
        continue
    render = Image.open(still).convert("RGB")
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
    card.save(folder / f"deeper-{name}.png")
    print(f"deeper-{name}.png", card.size)
    frames = folder / f"frames-{name}"
    if frames.exists() and shutil.which("ffmpeg"):
        # 12 格一秒；最後一格多停 1.8 秒再重播，看得清楚結果
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "12", "-i", str(frames / "f%03d.png"),
                        "-vf", "tpad=stop_mode=clone:stop_duration=1.8,split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                        "-loop", "0", str(folder / f"deeper-{name}.gif")], check=True)
        print(f"deeper-{name}.gif", (folder / f"deeper-{name}.gif").stat().st_size // 1024, "KB")

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
