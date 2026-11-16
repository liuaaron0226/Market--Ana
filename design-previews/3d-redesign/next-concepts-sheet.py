"""把 Blender 算出來的概念圖加上標題、一句說明與「哪些數字是示意」，各存一張成品。
第一批六張另外拼一張總覽（concepts-all.png）。
會動的樣品（加深版 deeper-*、第三輪 round3-*）存成最後一格加說明的圖與整串畫面接成的動圖（要有 ffmpeg）。
第三輪的畫面是透明背景、沒有底板，這裡疊到頁面卡片的底色上。
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
# 加深版（本人看過第一批之後：形狀與動態本身要帶出更多資訊）
DEEPER = [
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
# 第三輪（本人：①③ 整合成兩層、提兩個方向；⑤ 重做；不要底下那塊板子）
ROUND3 = [
    ("integ-a", "①＋③ 整合・方向甲「把那一層拿出來」", "自選與庫存頁",
     "外層是水槽：股票那一層是一檔一層疊起來的，厚度就是那一檔占總資金多少，最上面淺色的是現金。"
     "內層是把那幾層拿出來排成柱子：高度還是同一把尺（錢），每一根下面是投入的成本、上面變紅的是賺到的。同一個東西只是拿出來看仔細。",
     "數字全部是樣本資料；三檔都是賺的，賠錢的樣子這張沒有畫。"),
    ("integ-b", "①＋③ 整合・方向乙「水面就是成本」", "自選與庫存頁",
     "外層是同一個水槽。內層換成淺池：每檔持股一根柱子站在池裡，底面積是占比；水面代表剛好打平，"
     "露出水面的那一截是賺的，整根沉在水面下就是賠的。",
     "數字全部是樣本資料；三檔都是賺的。要留意：外層的水是「股票占總資金多少」，內層的水是「成本線」，同樣是水、意思不一樣。"),
    ("towers", "⑤ 短中長期走勢（重新設計）", "首頁",
     "每一期中間一條橫線：偏多的指標一格一格往上疊，偏空的往下疊，每一格寫著是哪個指標。旁邊的球停在「多的比空的多幾票」的高度——"
     "在線上面是偏多、線下面是偏空、停在線上是盤整。沒有底板，三期排成一列。",
     "票是示意：樣本資料裡走勢判讀還沒產生；票數照現行規則（短期 5 票、中期與長期各 4 票）。"),
]
# 第四輪（本人：物件不要侷限在現實中的物品、要大膽；①＋③ 要看得出哪一檔降多少；⑤ 方向對但物件要重做）
ROUND4 = [
    ("flow", "①＋③ 整合・「光帶分流」", "自選與庫存頁",
     "錢是一條會流動的光帶，粗細就是金額。左邊一整條是總資金，往右分成每一檔持股與現金；建議水位比現在低時，"
     "每一檔的光帶上緣剝出一條金色的細帶流向現金——那條細帶的粗細就是這一檔要降多少，右邊寫著金額。",
     "持股市值與現金是樣本資料。「建議 70%」是示意的情境（樣本那天其實不用動）。每一檔降多少是照占比平均分攤，這個規則還沒定。"),
    ("crystal", "①＋③ 整合・「晶柱與粒子」", "自選與庫存頁",
     "每一檔持股是一根懸浮的六角晶柱，高度是錢：藍色是投入的成本、紅色是賺到的。建議水位比現在低時，每一根頂端要降的那一截"
     "碎成金色的粒子飛向現金那一根——一顆是 2 萬，哪一檔飛出去多少顆就是要降多少；左邊的水位條跟著從 88% 降到 70%。",
     "成本、市值與現金是樣本資料。「建議 70%」是示意的情境。每一檔降多少是照占比平均分攤，這個規則還沒定。"),
    ("orbs", "⑤ 短中長期走勢・「浮上來的與沉下去的」", "首頁",
     "每一期中間一條線，每個指標是一顆繫在線上的球：偏多的往上浮、偏空的往下沉，球上寫著是哪個指標。"
     "旁邊的圓環停在「多的比空的多幾顆」的高度——環在線上面是偏多、線下面是偏空、套在線上是盤整。",
     "票是示意：樣本資料裡走勢判讀還沒產生；票數照現行規則（短期 5 票、中期與長期各 4 票）。"),
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


def flatten(path):
    """透明背景的畫面疊到頁面卡片的底色上。"""
    image = Image.open(path).convert("RGBA")
    backdrop = Image.new("RGBA", image.size, (*PAPER, 255))
    backdrop.alpha_composite(image)
    return backdrop.convert("RGB")


def card(render, title, page, how, source):
    width, pad = render.width, 36
    probe = ImageDraw.Draw(render)
    title_font, page_font, body_font = ImageFont.truetype(BOLD, 40), ImageFont.truetype(REGULAR, 26), ImageFont.truetype(REGULAR, 28)
    how_lines, source_lines = wrap(probe, how, body_font, width - pad * 2), wrap(probe, source, body_font, width - pad * 2)
    band = pad + 86 + 14 + 40 * (len(how_lines) + len(source_lines)) + 10 + pad
    sheet = Image.new("RGB", (width, render.height + band), PAPER)
    sheet.paste(render, (0, 0))
    draw = ImageDraw.Draw(sheet)
    draw.line([(pad, render.height), (width - pad, render.height)], fill=(222, 218, 208), width=2)
    y = render.height + pad
    draw.text((pad, y), title, fill=INK, font=title_font)
    draw.text((pad, y + 52), page, fill=DIM, font=page_font)
    y += 86 + 14
    for line in how_lines:
        draw.text((pad, y), line, fill=INK, font=body_font)
        y += 40
    y += 10
    for line in source_lines:
        draw.text((pad, y), line, fill=NOTE, font=body_font)
        y += 40
    return sheet


finished = []
for index, (name, title, page, how, source) in enumerate(CONCEPTS):
    still = folder / f"render-{name}.png"
    if not still.exists():
        continue
    sheet = card(flatten(still), title, page, how, source)
    sheet.save(folder / f"concept-{index + 1}-{name}.png")
    finished.append(sheet)
    print(f"concept-{index + 1}-{name}.png", sheet.size)

for prefix, entries in (("deeper", DEEPER), ("round3", ROUND3), ("round4", ROUND4)):
    for name, title, page, how, source in entries:
        still, frames = folder / f"render-{name}.png", folder / f"frames-{name}"
        if still.exists():
            sheet = card(flatten(still), title, page, how, source)
            sheet.save(folder / f"{prefix}-{name}.png")
            print(f"{prefix}-{name}.png", sheet.size)
        if frames.exists() and shutil.which("ffmpeg"):
            flat = folder / f"flat-{name}"
            flat.mkdir(exist_ok=True)
            for frame in sorted(frames.glob("f*.png")):
                flatten(frame).save(flat / frame.name)
            # 12 格一秒；開頭停 1 秒看清楚起點，最後一格停 2 秒看清楚結果，再重播
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "12", "-i", str(flat / "f%03d.png"), "-vf",
                            "tpad=start_mode=clone:start_duration=1:stop_mode=clone:stop_duration=2,split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                            "-loop", "0", str(folder / f"{prefix}-{name}.gif")], check=True)
            print(f"{prefix}-{name}.gif", (folder / f"{prefix}-{name}.gif").stat().st_size // 1024, "KB")

# 第一批的總覽：兩欄，每張縮到同樣的寬度
if len(finished) >= 2:
    cell, gap = 640, 20
    small = [image.resize((cell, round(image.height * cell / image.width))) for image in finished]
    row_heights = [max(image.height for image in small[i:i + 2]) for i in range(0, len(small), 2)]
    overview = Image.new("RGB", (cell * 2 + gap, sum(row_heights) + gap * (len(row_heights) - 1)), (110, 110, 110))
    y = 0
    for row, height in enumerate(row_heights):
        for col, image in enumerate(small[row * 2:row * 2 + 2]):
            overview.paste(image, (col * (cell + gap), y))
        y += height + gap
    overview.save(folder / "concepts-all.png")
    print("concepts-all.png", overview.size)
