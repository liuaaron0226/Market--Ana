"""把四張風格圖加上發光的暈、標上代號與名稱，拼成一張風格板。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/style-board-sheet.py <圖片資料夾>"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

folder = Path(sys.argv[1])
BOLD, REGULAR = "C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc"
NIGHT, PALE, DIM = (18, 19, 26), (244, 246, 251), (154, 160, 173)
STYLES = [
    ("neon", "A", "霓虹光管", "每一票是一圈發光的管子，一條光把它們串起來"),
    ("liquid", "B", "液態玻璃", "每一票是一滴會融在一起的液體，往上冒、往下滴"),
    ("swarm", "C", "粒子群", "每一票是一團發光的小點，會飄散、拖著尾巴"),
    ("holo", "D", "全像線框", "每一票是一顆只有線條的多面體，像投影出來的"),
]


def bloom(image, radius, amount):
    """發光的暈：把畫面模糊之後疊回去（濾色），亮的地方會暈開。"""
    halo = image.filter(ImageFilter.GaussianBlur(radius)).point(lambda value: int(value * amount))
    return ImageChops.screen(image, halo)


cell, pad, band, head = 800, 24, 120, 150
board = Image.new("RGB", (cell * 2 + pad * 3, head + (cell + band) * 2 + pad * 3), NIGHT)
draw = ImageDraw.Draw(board)
draw.text((pad, 30), "風格板：同一段內容，四種視覺", fill=PALE, font=ImageFont.truetype(BOLD, 46))
draw.text((pad, 96), "內容都是「短期走勢：1 個指標偏多、4 個偏空」，偏多在線上面、偏空在線下面。請看物件的質感，不是看內容。", fill=DIM, font=ImageFont.truetype(REGULAR, 26))
for index, (name, letter, title, note) in enumerate(STYLES):
    path = folder / f"style-{name}.png"
    if not path.exists():
        continue
    image = Image.open(path).convert("RGB")
    image = bloom(bloom(image, 5, 0.55), 22, 0.5) if name != "liquid" else bloom(image, 16, 0.3)
    image.save(folder / f"style-{name}-glow.png")
    x, y = pad + (index % 2) * (cell + pad), head + pad + (index // 2) * (cell + band + pad)
    board.paste(image, (x, y))
    draw.text((x + 8, y + cell + 14), f"{letter}　{title}", fill=PALE, font=ImageFont.truetype(BOLD, 40))
    draw.text((x + 8, y + cell + 68), note, fill=DIM, font=ImageFont.truetype(REGULAR, 26))
board.save(folder / "style-board.png")
print("style-board.png", board.size)

# 第二張：表達的概念（本人看過風格板之後說不喜歡「垂直表達指標」）。質感都一樣，只比概念。
IDEAS = [
    ("tug", "甲", "拔河", "每一期一條橫的繩子：偏多的指標在右邊拉、偏空的在左邊拉，中間的結被拉向人多的那一邊"),
    ("rings", "乙", "年輪", "一個同心圓：裡圈短期、外圈長期；每一圈切成幾段，一段是一個指標，紅偏多、綠偏空"),
    ("path", "丙", "一條走勢帶", "一條從短期走到長期的光帶，三段的高低就是偏多還是偏空；帶子上每一顆珠子是一個指標"),
    ("weather", "丁", "天氣", "每一期一個天氣：偏多的指標是太陽的光芒、偏空的是雨滴；太陽大是偏多、下雨是偏空"),
]
# 第三張：本人對上面四種回答「其他的」，再四種有生命感的概念
IDEAS_MORE = [
    ("fish", "戊", "魚群", "每個指標是一條魚：偏多的往右上游、偏空的往右下游；整群往哪邊游，那一期就是偏多還是偏空"),
    ("plant", "己", "植物", "每一期一株：偏多的指標是往上挺的葉子、偏空的是垂下來的葉子；偏多的長得直、開花，偏空的整株彎下去"),
    ("wave", "庚", "海浪", "每一期一道浪，不把指標一個一個畫出來：往上湧是偏多、往下退是偏空；浪越碎表示指標彼此越不一致"),
    ("planet", "辛", "行星", "每一期一顆帶光環的行星：光環往右上翹是偏多、往右下垂是偏空；繞著它的小衛星是指標"),
]
for entries, heading, filename in ((IDEAS, "⑤ 短中長期走勢：四種表達的概念", "idea-board.png"), (IDEAS_MORE, "⑤ 短中長期走勢：再四種概念（有生命感的）", "idea-board-2.png")):
    if not all((folder / f"idea-{name}.png").exists() for name, *_ in entries):
        continue
    band = 150
    ideas = Image.new("RGB", (cell * 2 + pad * 3, head + (cell + band) * 2 + pad * 3), NIGHT)
    draw = ImageDraw.Draw(ideas)
    draw.text((pad, 30), heading, fill=PALE, font=ImageFont.truetype(BOLD, 46))
    draw.text((pad, 96), "同一組資料（短期偏空、中期盤整、長期偏多；票是示意）。這一張只比概念，質感先都用同一種。", fill=DIM, font=ImageFont.truetype(REGULAR, 26))
    for index, (name, letter, title, note) in enumerate(entries):
        image = bloom(Image.open(folder / f"idea-{name}.png").convert("RGB"), 14, 0.35)
        x, y = pad + (index % 2) * (cell + pad), head + pad + (index // 2) * (cell + band + pad)
        ideas.paste(image, (x, y))
        draw.text((x + 8, y + cell + 14), f"{letter}　{title}", fill=PALE, font=ImageFont.truetype(BOLD, 40))
        words, line, top = note, "", y + cell + 68
        for char in words:
            if draw.textlength(line + char, font=ImageFont.truetype(REGULAR, 25)) > cell - 16:
                draw.text((x + 8, top), line, fill=DIM, font=ImageFont.truetype(REGULAR, 25))
                line, top = char, top + 34
            else:
                line += char
        draw.text((x + 8, top), line, fill=DIM, font=ImageFont.truetype(REGULAR, 25))
    ideas.save(folder / filename)
    print(filename, ideas.size)
