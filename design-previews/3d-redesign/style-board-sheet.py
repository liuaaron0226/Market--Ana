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
