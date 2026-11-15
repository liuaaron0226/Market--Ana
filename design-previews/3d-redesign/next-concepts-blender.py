"""Blender 概念樣品（下一批立體呈現，本人 2026-11-15 要求先看樣品再決定）。只產生圖片，不是網站執行時用的程式。
六個場景：tank 今日股票水位、stairs 價位階梯、blocks 庫存總覽、bins 隔日三區歷史分布、ramps 短中長期走勢、terraces 大戶持股。
數字來自 next-concepts-data.py 取出的樣本資料；樣本裡不夠的（走勢判讀、大戶持股的逐週序列、賠錢的庫存）用示意數字，
哪些是示意由 next-concepts-sheet.py 寫在圖上。
加深版（本人看過第一批之後要求：形狀與動態本身要帶出更多資訊，足以取代原本的版面）：tank2、blocks2、jacks，
這三個會動，一次算出一串畫面（frames-<名稱>/f000.png…），由 next-concepts-sheet.py 接成動圖。
第三輪（本人：①③ 整合成兩層、提兩個方向；⑤ 重做，箭頭看不出上下、放不進版面；不要底下那塊板子）：
integ-a、integ-b、towers。這三個沒有底板、背景透明，由 next-concepts-sheet.py 疊到頁面的底色上。
用法：blender -b -P next-concepts-blender.py -- <輸出資料夾（要給完整路徑）> <場景名稱> [邊長，預設 1000] [final：會動的場景只算最後一格]"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
out = Path(args[0])
concept = args[1]
side_px = int(args[2]) if len(args) > 2 else 1000
out.mkdir(parents=True, exist_ok=True)


def rgb(code: str, alpha: float = 1.0):
    code = code.lstrip("#")
    srgb = [int(code[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (*linear, alpha)


DESK, SLAB, INK, DIM, BOARD = "#e6e1d6", "#fbfaf6", "#1c1b22", "#77757d", "#f1ede4"
UP, DOWN, GOLD, ACCENT, BLUE, GREY = "#c8202a", "#0f7b5a", "#9a6a1c", "#c2410c", "#235b9f", "#b7b3a9"

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
materials = {}


def material(code: str, rough: float = 0.55, alpha: float = 1.0):
    key = (code, rough, alpha)
    if key not in materials:
        mat = bpy.data.materials.new(f"{code}-{alpha}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = rgb(code)
        bsdf.inputs["Roughness"].default_value = rough
        if alpha < 1:
            bsdf.inputs["Alpha"].default_value = alpha
            for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
                try:
                    setattr(mat, attr, value)
                except Exception:  # noqa: BLE001  不同版本的 Blender 屬性名稱不同
                    pass
        materials[key] = mat
    return materials[key]


def box(center, size, code, bevel=0.03, rough=0.55, alpha=1.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    obj.data.materials.append(material(code, rough, alpha))
    return obj


def ball(center, radius, code, rough=0.3):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=center, segments=48, ring_count=24)
    obj = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(material(code, rough))
    return obj


def prism(points, depth, y, code, rough=0.5):
    """把一個側面的多邊形（x, z）沿 y 方向拉出厚度：用來做斜坡。"""
    verts = [(x, y - depth / 2, z) for x, z in points] + [(x, y + depth / 2, z) for x, z in points]
    n = len(points)
    faces = [list(range(n - 1, -1, -1)), list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    mesh = bpy.data.meshes.new("prism")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("prism", mesh)
    scene.collection.objects.link(obj)
    obj.data.materials.append(material(code, rough))
    return obj


FONT = None
for path in ("C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/kaiu.ttf"):
    try:
        FONT = bpy.data.fonts.load(path)
        break
    except Exception as error:  # noqa: BLE001
        print("font failed", path, error)


def text(body, location, size, code, align="LEFT", flat=True, rotation_z=0.0):
    """flat=True 是躺在桌面上的字；False 是立起來、面向前方的字。"""
    bpy.ops.object.text_add(location=location)
    obj = bpy.context.active_object
    obj.data.body = body
    obj.data.size = size
    obj.data.align_x = align
    obj.data.extrude = 0.004
    if FONT:
        obj.data.font = FONT
    obj.rotation_euler = (0 if flat else math.radians(90), 0, rotation_z)
    obj.data.materials.append(material(code, 0.8))
    return obj


def glass_tank(cx, width, depth, height, rim=INK):
    """一個透明的槽：四片玻璃、四根角柱、上緣一圈。"""
    for sx in (-1, 1):
        box((cx + sx * width / 2, 0, height / 2), (0.04, depth, height), "#dfe9f0", bevel=0, rough=0.05, alpha=0.05)
    for sy in (-1, 1):
        box((cx, sy * depth / 2, height / 2), (width, 0.04, height), "#dfe9f0", bevel=0, rough=0.05, alpha=0.05)
    for sx in (-1, 1):
        for sy in (-1, 1):
            box((cx + sx * width / 2, sy * depth / 2, height / 2), (0.07, 0.07, height), rim, bevel=0)
        box((cx + sx * width / 2, 0, height), (0.07, depth + 0.07, 0.07), rim, bevel=0)
    for sy in (-1, 1):
        box((cx, sy * depth / 2, height), (width + 0.07, 0.07, 0.07), rim, bevel=0)


def tank():
    """今日股票水位：一個透明的水槽。水面是目前水位，槽前面一道金色的線是今日建議，右邊的尺上金色那一段是容忍帶。
    樣本資料：目前 88.3%、建議 90%、容忍帶上下 5 個百分點。"""
    now, advice, band = 88.3, 90.0, 5.0
    width, depth, height = 3.6, 2.0, 6.0
    z = lambda pct: height * pct / 100
    box((0.9, 0, -0.15), (width + 4.6, depth + 1.6, 0.3), BOARD, bevel=0.1, rough=0.6)
    box((0, 0, z(now) / 2), (width - 0.12, depth - 0.12, z(now)), "#2c6cb4", bevel=0, rough=0.18)
    glass_tank(0, width, depth, height)
    # 今日建議：槽前面一道金色的線
    box((0, -depth / 2 - 0.04, z(advice)), (width + 0.2, 0.05, 0.07), GOLD, bevel=0, rough=0.4)
    # 右邊的尺：0 到 100，金色那一段是容忍帶
    ruler_x = width / 2 + 0.75
    box((ruler_x, 0, height / 2), (0.5, 0.1, height), SLAB, bevel=0.02)
    box((ruler_x, -0.06, z(advice)), (0.5, 0.06, z(band * 2)), GOLD, bevel=0, rough=0.5, alpha=0.75)
    for pct in (0, 25, 50, 75, 100):
        box((ruler_x, -0.07, z(pct)), (0.5, 0.05, 0.035), INK, bevel=0)
        text(f"{pct}", (ruler_x + 0.4, -0.08, z(pct) - 0.1 + (0.16 if pct == 0 else 0)), 0.26, DIM, flat=False)
    text("今日建議 90%", (ruler_x + 1.25, -0.08, z(advice) - 0.02), 0.32, GOLD, flat=False)
    text("金色那一段是容忍帶", (ruler_x + 1.25, -0.08, z(advice) - 0.55), 0.26, DIM, flat=False)
    text("目前水位", (-width / 2 - 0.35, -depth / 2, z(now) + 0.1), 0.32, DIM, align="RIGHT", flat=False)
    text("88.3%", (-width / 2 - 0.35, -depth / 2, z(now) - 0.75), 0.74, INK, align="RIGHT", flat=False)
    return Vector((1.15, 0, 3.05)), (74, 0, 20), 10.6


def stairs():
    """價位階梯：真的五階樓梯。每一階的高度照它離現價幾個百分點，所以一眼看得出哪一階很近、哪一階很遠；一顆球停在現價那一階。
    樣本資料（2330）：壓力二 2,345、壓力一 2,250、收盤 2,235、支撐一 2,230、支撐二 2,210。"""
    levels = [("支撐二", "2,210", -1.12, DOWN), ("支撐一", "2,230", -0.22, DOWN), ("收盤", "2,235", 0.0, INK),
              ("壓力一", "2,250", 0.67, UP), ("壓力二", "2,345", 4.87, UP)]
    per_pct, floor, step_w, depth = 0.78, 1.5, 2.2, 2.6
    for index, (name, price, pct, code) in enumerate(levels):
        top = floor + (pct - levels[0][2]) * per_pct
        x = index * step_w
        box((x, 0, top / 2), (step_w, depth, top), SLAB, bevel=0.03)
        box((x, 0, top + 0.04), (step_w - 0.06, depth - 0.06, 0.08), code, bevel=0.02, rough=0.45)
        face_y = -depth / 2 - 0.02
        text(name, (x, face_y, top - 0.5), 0.3, DIM, align="CENTER", flat=False)
        text(price, (x, face_y, top - 1.0), 0.44, INK, align="CENTER", flat=False)
        if pct:
            text(f"{pct:+.2f}%", (x, face_y, top - 1.38), 0.26, code, align="CENTER", flat=False)
        if not pct:
            ball((x, 0, top + 0.08 + 0.46), 0.46, ACCENT)
    span = step_w * (len(levels) - 1)
    box((span / 2, 0, -0.12), (span + step_w + 1.4, depth + 1.6, 0.24), BOARD, bevel=0.1, rough=0.6)
    return Vector((span / 2, 0, 2.7)), (68, 0, -24), 14.5


def blocks():
    """庫存總覽：每檔持股一塊積木。底面積是占比，往上長是賺、往下凹是賠（高度與深度照報酬率）。
    前三檔是樣本資料；樣本裡沒有賠錢的，後兩檔是為了示範往下凹的樣子加的示意數字。"""
    holdings = [("台積電", 2235000, 128.06), ("長榮", 491000, 14.19), ("0050", 292350, 66.87), ("示意甲", 260000, -18.0), ("示意乙", 150000, -6.5)]
    total = sum(value for _, value, _ in holdings)
    per_pct, gap, thick = 0.026, 0.4, 0.9
    cursor, placed = 0.0, []
    for name, value, pct in holdings:
        side = math.sqrt(value / total) * 6.2
        placed.append((name, value, pct, side, cursor + side / 2))
        cursor += side + gap
    span = cursor - gap
    deep = placed[0][3]
    board = box((span / 2, deep / 2 - 0.95, -thick / 2), (span + 1.6, deep + 3.5, thick), BOARD, bevel=0, rough=0.6)
    for name, value, pct, side, x in placed:
        y = side / 2   # 前緣對齊在 y = 0，往後長
        if pct >= 0:
            h = max(0.12, pct * per_pct)
            box((x, y, h / 2), (side, side, h), UP, bevel=0.04, rough=0.42)
        else:
            pit = max(0.16, -pct * per_pct)
            cutter = box((x, y, -pit / 2 + 0.01), (side, side, pit + 0.02), GREY, bevel=0)
            cut = board.modifiers.new("pit", "BOOLEAN")
            cut.operation = "DIFFERENCE"
            cut.object = cutter
            cutter.hide_render = True
            cutter.hide_viewport = True
            box((x, y, -pit + 0.02), (side - 0.02, side - 0.02, 0.04), DOWN, bevel=0, rough=0.5)
        code = UP if pct >= 0 else DOWN
        text(name, (x, -0.7, 0.01), 0.42, INK, align="CENTER")
        text(f"{pct:+.0f}%", (x, -1.4, 0.01), 0.56, code, align="CENTER")
        text(f"占 {value / total * 100:.0f}%", (x, -1.92, 0.01), 0.32, DIM, align="CENTER")
    return Vector((span / 2, deep / 2 - 0.9, 1.0)), (56, 0, 14), 15.5


def bins():
    """隔日三區歷史分布：三個透明的槽，裝的量就是歷史上各占幾成。樣本資料：站回 14.29%、兩線間 45.71%、跌破 40.00%。"""
    zones = [("站回", 14.29, UP), ("兩線間", 45.71, GOLD), ("跌破", 40.00, DOWN)]
    width, depth, height, gap = 2.4, 2.0, 5.0, 1.0
    for index, (name, pct, code) in enumerate(zones):
        x = index * (width + gap)
        fill = height * pct / 100
        box((x, 0, fill / 2), (width - 0.12, depth - 0.12, fill), code, bevel=0, rough=0.35)
        glass_tank(x, width, depth, height)
        # 字立在槽的前面、高過裡面那一塊的頂面（從斜上方看，頂面會露出來，字壓在上面會看不清）
        text(name, (x, -depth / 2 - 0.08, fill + 1.62), 0.34, DIM, align="CENTER", flat=False)
        text(f"{pct:.1f}%", (x, -depth / 2 - 0.08, fill + 0.86), 0.62, INK, align="CENTER", flat=False)
    span = (width + gap) * (len(zones) - 1)
    box((span / 2, 0, -0.12), (span + width + 1.4, depth + 1.6, 0.24), BOARD, bevel=0.1, rough=0.6)
    return Vector((span / 2, 0, 2.5)), (68, 0, 18), 11.8


def ramps():
    """短中長期走勢：三段斜坡。往上斜是偏多、平的是盤整、往下斜是偏空；一顆球停在坡上。
    樣本資料裡走勢判讀還沒產生，這張的三個狀態是示意。"""
    trends = [("短期", "偏空", -1, DOWN), ("中期", "盤整", 0, GOLD), ("長期", "偏多", 1, UP)]
    length, width, lane = 4.6, 1.5, 2.5
    for row, (label, state, direction, code) in enumerate(trends):
        y = -row * lane
        left, right = 1.25 - direction * 0.85, 1.25 + direction * 0.85
        prism([(0, 0), (length, 0), (length, right), (0, left)], width, y, SLAB)
        prism([(0, left), (length, right), (length, right + 0.09), (0, left + 0.09)], width - 0.06, y, code, rough=0.42)
        at = 0.62
        ball((length * at, y, left + (right - left) * at + 0.09 + 0.33), 0.33, INK)
        text(label, (-0.45, y - width / 2, 1.0), 0.34, DIM, align="RIGHT", flat=False)
        text(state, (-0.45, y - width / 2, 0.3), 0.56, code, align="RIGHT", flat=False)
    depth = lane * (len(trends) - 1)
    box((length / 2 - 1.1, -depth / 2, -0.12), (length + 4.2, depth + 3.0, 0.24), BOARD, bevel=0.1, rough=0.6)
    return Vector((length / 2 - 1.0, -depth / 2, 0.9)), (58, 0, 16), 12.5


def terraces():
    """大戶持股：每週一片薄板，千張大戶比例高就高、低就低，連起來像梯田，看得出一路往上還是往下；本週那一片上色。
    樣本資料只有最後兩週（84.04%、84.17%），前十週是示意。"""
    weeks = [83.31, 83.42, 83.38, 83.55, 83.61, 83.58, 83.74, 83.80, 83.92, 83.97, 84.04, 84.17]
    low, high, slab, depth = 83.2, 84.2, 0.72, 2.6
    for index, value in enumerate(weeks):
        h = 0.45 + (value - low) / (high - low) * 3.1
        last = index == len(weeks) - 1
        box((index * slab, 0, h / 2), (slab, depth, h), ACCENT if last else "#c9c4b8" if index % 2 else "#bdb8ac", bevel=0.015, rough=0.42 if last else 0.7)
    span = slab * (len(weeks) - 1)
    text("千張以上大戶", (span + 2.1, -depth / 2 - 0.5, 3.0), 0.3, DIM, flat=False)
    text("84.17%", (span + 2.1, -depth / 2 - 0.5, 2.15), 0.72, INK, flat=False)
    text("較前一週 +0.13", (span + 2.1, -depth / 2 - 0.5, 1.6), 0.28, UP, flat=False)
    text("12 週前", (0, -depth / 2 - 0.75, 0.01), 0.4, DIM, align="CENTER")
    text("本週", (span, -depth / 2 - 0.75, 0.01), 0.4, ACCENT, align="CENTER")
    box((span / 2 + 2.2, -0.3, -0.12), (span + 7.6, depth + 2.4, 0.24), BOARD, bevel=0.1, rough=0.6)
    return Vector((span / 2 + 2.1, -0.2, 1.7)), (64, 0, 18), 15.0


# ---------- 加深版：會動的三個場景。每個場景回傳 (鏡頭目標, 角度, 範圍, 動態函式)，動態函式吃 0 到 1 的進度 ----------
def ease(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def part(t, start, end):
    """整段動態裡的一小段：t 還沒到 start 是 0，過了 end 是 1。"""
    return ease((t - start) / (end - start))


def stretch(obj, base, height, share):
    """把一塊原本高 height 的方塊從底部長到 share（0 到 1）的高度。"""
    share = max(share, 0.001)
    obj.scale.z = share
    obj.location.z = base + height * share / 2


def plate(points, thickness, location, code, rough=0.45):
    """俯視的多邊形（x, y）加上厚度：用來做箭頭形的板子。原點在 location，方便繞著它轉。"""
    n = len(points)
    verts = [(x, y, -thickness / 2) for x, y in points] + [(x, y, thickness / 2) for x, y in points]
    faces = [list(range(n - 1, -1, -1)), list(range(n, 2 * n))] + [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    mesh = bpy.data.meshes.new("plate")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("plate", mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.data.materials.append(material(code, rough))
    return obj


def tank2():
    """今日股票水位（加深版）。整個槽是總資金：下面深色的是股票、上面淺色那一層是現金，兩層的交界就是目前水位。
    槽上方四格是四組訊號，每一分是一塊砝碼；砝碼一塊一塊落下，金色的建議線跟著被壓低（看得到建議是被哪幾組壓下來的）。
    半透明的那幾塊是還沒接上的訊號。建議線旁邊的圓點是連續確認的天數。半透明的金色帶是容忍帶。
    樣本資料：目前 88.3%、建議 90%、趨勢 2 分、預警 1 分、VIX 與費半未接、已連續確認 2 天（要 2 天）。
    砝碼壓低多少是示意方向，實際的換算照現行的分數表。"""
    now, advice, band = 88.3, 90.0, 5.0
    width, depth, height = 3.6, 2.0, 6.0
    z = lambda pct: height * pct / 100
    box((0.6, 0, -0.15), (width + 4.4, depth + 1.6, 0.3), BOARD, bevel=0.1, rough=0.6)
    box((0, 0, z(now) / 2), (width - 0.12, depth - 0.12, z(now)), "#2c6cb4", bevel=0, rough=0.18)
    box((0, 0, (z(now) + height) / 2), (width - 0.12, depth - 0.12, height - z(now)), "#dfe7ef", bevel=0, rough=0.3)
    glass_tank(0, width, depth, height)
    zone = box((0, -depth / 2 - 0.05, z(100)), (width + 0.16, 0.03, z(band * 2)), GOLD, bevel=0, rough=0.5, alpha=0.3)
    line = box((0, -depth / 2 - 0.08, z(100)), (width + 0.34, 0.05, 0.08), GOLD, bevel=0, rough=0.4)
    rack = height + 1.7
    box((0, 0, rack - 0.2), (width + 0.5, depth * 0.78, 0.4), SLAB, bevel=0.02)   # 架子厚一點，組名寫在前緣
    rod_len = 1.0
    rods = [box((sx * (width / 2 + 0.2), -depth / 2 - 0.08, rack - rod_len / 2), (0.035, 0.035, rod_len), GOLD, bevel=0) for sx in (-1, 1)]
    slot = (width + 0.5) / 4
    weights = []
    for index, (name, points, absent) in enumerate((("趨勢", 2, 0), ("波動", 0, 1), ("預警", 1, 1), ("資金面", 0, 0))):
        x = -(width + 0.5) / 2 + slot * (index + 0.5)
        text(name, (x, -depth * 0.39 - 0.02, rack - 0.3), 0.2, DIM, align="CENTER", flat=False)
        for level in range(points):
            rest = rack + 0.19 + level * 0.42
            weights.append((box((x, 0, rest + 4), (slot - 0.26, depth * 0.52, 0.38), INK, bevel=0.03, rough=0.35), rest))
        for level in range(points, points + absent):
            box((x, 0, rack + 0.19 + level * 0.42), (slot - 0.26, depth * 0.52, 0.38), GREY, bevel=0.03, rough=0.6, alpha=0.32)
    dots = [ball((width / 2 + 0.55 + index * 0.3, -depth / 2 - 0.08, z(100)), 0.1, GOLD) for index in range(2)]
    text("目前水位", (-width / 2 - 0.35, -depth / 2, z(now) + 0.1), 0.3, DIM, align="RIGHT", flat=False)
    text("88.3%", (-width / 2 - 0.35, -depth / 2, z(now) - 0.75), 0.74, INK, align="RIGHT", flat=False)
    text("現金", (-width / 2 - 0.35, -depth / 2, z(96) + 0.3), 0.24, DIM, align="RIGHT", flat=False)
    text("股票", (-width / 2 - 0.35, -depth / 2, z(40)), 0.24, DIM, align="RIGHT", flat=False)
    text("今日建議 90%", (width / 2 + 1.25, -depth / 2 - 0.08, z(advice) - 0.1), 0.3, GOLD, flat=False)
    text("已連續確認 2 天", (width / 2 + 1.25, -depth / 2 - 0.08, z(advice) - 0.55), 0.22, DIM, flat=False)

    def animate(t):
        landed = 0.0
        for index, (weight, rest) in enumerate(weights):
            fall = part(t, 0.06 + index * 0.2, 0.3 + index * 0.2)
            weight.location.z = rest + 4 * (1 - fall)
            landed += fall
        level = z(100 - (100 - advice) * landed / len(weights))
        line.location.z = zone.location.z = level
        for dot in dots:
            dot.location.z = level
        for rod in rods:
            stretch(rod, level, rod_len, (rack - level) / rod_len)
    return Vector((0.85, 0, 4.3)), (74, 0, 20), 12.6, animate


def blocks2():
    """庫存總覽（加深版）。每檔持股一根柱子，高度是錢：灰色那一段是投入的成本，上面紅色那一段是賺到的。
    賠錢的那一檔，柱子比成本矮，缺的那一塊用半透明的綠色標出來。後面的牆是金額的刻度。
    動態：柱子先長到成本的高度，接著賺的往上長、賠的往下縮——看得到每一檔是從成本變成現在的市值。
    前三檔是樣本資料（成本 98 萬→市值 223.5 萬、43 萬→49.1 萬、17.5 萬→29.2 萬）；「示意甲、乙」是為了示範賠錢加的。"""
    holdings = [("台積電", 980000, 2235000), ("長榮", 430000, 491000), ("0050", 175200, 292350), ("示意甲", 317000, 260000), ("示意乙", 160000, 150000)]
    unit, foot, pitch = 1 / 300000, 1.7, 2.3
    columns = []
    for index, (name, cost, value) in enumerate(holdings):
        x, cost_h, value_h = index * pitch, cost * unit, value * unit
        base = box((x, 0, cost_h / 2), (foot, foot, cost_h), "#bdb8ac", bevel=0.03, rough=0.7)
        if value >= cost:
            extra = box((x, 0, cost_h + (value_h - cost_h) / 2), (foot, foot, value_h - cost_h), UP, bevel=0.03, rough=0.42)
        else:
            extra = box((x, 0, value_h + (cost_h - value_h) / 2), (foot - 0.02, foot - 0.02, cost_h - value_h), DOWN, bevel=0, rough=0.5, alpha=0.4)
        columns.append((base, extra, cost_h, value_h))
        code = UP if value >= cost else DOWN
        text(name, (x, -foot / 2 - 0.6, 0.01), 0.38, INK, align="CENTER")
        text(f"{(value / cost - 1) * 100:+.0f}%", (x, -foot / 2 - 1.25, 0.01), 0.52, code, align="CENTER")
    span = pitch * (len(holdings) - 1)
    wall_y, wall_h = foot / 2 + 0.6, 8.2
    box((span / 2, wall_y, wall_h / 2), (span + foot + 1.0, 0.1, wall_h), SLAB, bevel=0.02)
    for amount in (500000, 1000000, 1500000, 2000000):
        box((span / 2, wall_y - 0.06, amount * unit), (span + foot + 1.0, 0.03, 0.03), GREY, bevel=0)
        text(f"{amount // 10000} 萬", (span / 2 + (span + foot + 1.0) / 2 + 0.15, wall_y - 0.06, amount * unit - 0.1), 0.26, DIM, flat=False)
    box((span / 2 + 0.3, -0.5, -0.12), (span + foot + 3.0, foot + 4.2, 0.24), BOARD, bevel=0.1, rough=0.6)

    def animate(t):
        for index, (base, extra, cost_h, value_h) in enumerate(columns):
            rise, change = part(t, 0.02 + index * 0.04, 0.34 + index * 0.04), part(t, 0.42 + index * 0.05, 0.84 + index * 0.03)
            if value_h >= cost_h:
                stretch(base, 0, cost_h, rise)
                stretch(extra, cost_h * rise, value_h - cost_h, change)
            else:
                top = cost_h * rise * (1 - (1 - value_h / cost_h) * change)   # 柱子現在的頂
                stretch(base, 0, cost_h, top / cost_h)
                stretch(extra, top, cost_h - value_h, (cost_h * rise - top) / (cost_h - value_h))   # 缺口從柱頂往上到成本的高度
    return Vector((span / 2 + 0.5, 0, 3.6)), (66, 0, 18), 15.5, animate


def jacks():
    """短中長期走勢（加深版）。每一個結論背後是幾個指標各投一票；這裡每一票是一根撐竿：
    偏空的指標（綠）撐在左邊、偏多的指標（紅）撐在右邊，哪一邊的撐竿多，箭頭就被撐得往另一邊低下去——
    看得到「偏空」是幾票對幾票，也看得到是哪幾個指標。箭頭往右上是偏多、往右下是偏空、平的是盤整。
    動態：撐竿一根一根升起來，箭頭跟著慢慢傾斜。
    樣本資料裡走勢判讀還沒產生，這張的票是示意（票數照現行規則：短期 5 票、中期與長期各 4 票）。"""
    periods = [("短期", 6.0, [("MA5", -1), ("MA10", -1), ("MACD", -1), ("RSI", -1), ("KD", 1)]),
               ("中期", 6.6, [("MA60", -1), ("20日", -1), ("MA20", 1), ("法人", 1)]),
               ("長期", 7.2, [("60日", -1), ("MA120", 1), ("MA60", 1), ("MA240", 1)])]
    lane, beam_w, pivot_z, thick = 4.0, 1.1, 1.15, 0.14
    movers = []
    for row, (label, length, votes) in enumerate(periods):
        y, cx = -row * lane, length / 2
        net = sum(side for _, side in votes)
        angle = math.radians(net / len(votes) * 16)
        code, state = (UP, "偏多") if net > 0 else (DOWN, "偏空") if net < 0 else (GOLD, "盤整")
        half, tip = length / 2, 0.62
        outline = [(-half, -beam_w / 2), (half - tip, -beam_w / 2), (half - tip, -beam_w / 2 - 0.26), (half, 0),
                   (half - tip, beam_w / 2 + 0.26), (half - tip, beam_w / 2), (-half, beam_w / 2)]
        beam = plate(outline, thick, (cx, y, 0.9), code)
        bears, bulls = [vote for vote in votes if vote[1] < 0], [vote for vote in votes if vote[1] > 0]
        poles = []
        for group, start, end in ((bears, 0.25, half - 0.35), (bulls, half + 0.35, length - tip - 0.25)):
            for index, (name, side) in enumerate(group):
                x = start + (end - start) * (index + 0.5) / len(group)
                final = pivot_z + (x - cx) * math.tan(angle) - thick / 2
                pole = box((x, y, final / 2), (0.34, 0.34, final), UP if side > 0 else DOWN, bevel=0.03, rough=0.45)
                poles.append((pole, final, len(poles)))
                text(name, (x, y - beam_w / 2 - 0.55, 0.01), 0.24, UP if side > 0 else DOWN, align="CENTER")
        text(label, (-0.6, y - beam_w / 2, 1.0), 0.36, DIM, align="RIGHT", flat=False)
        text(state, (-0.6, y - beam_w / 2, 0.25), 0.6, code, align="RIGHT", flat=False)
        movers.append((beam, angle, poles, row))
    depth = lane * (len(periods) - 1)
    box((2.7, -depth / 2 - 0.3, -0.12), (11.6, depth + 3.6, 0.24), BOARD, bevel=0.1, rough=0.6)

    def animate(t):
        for beam, angle, poles, row in movers:
            lifts = []
            for pole, final, order in poles:
                lift = part(t, 0.04 + row * 0.08 + order * 0.11, 0.3 + row * 0.08 + order * 0.11)
                stretch(pole, 0, final, lift)
                lifts.append(lift)
            done = sum(lifts) / len(lifts)
            beam.location.z = 0.9 + (pivot_z - 0.9) * done
            beam.rotation_euler = (0, -angle * done, 0)
    return Vector((2.7, -depth / 2 - 0.2, 0.8)), (54, 0, 10), 15.6, animate


# ---------- 第三輪：沒有底板、背景透明的三個場景 ----------
def lerp(a, b, share):
    return a + (b - a) * share


def pop(obj, share):
    """讓一行字（或一個小東西）出現或消失：整個縮放。"""
    share = max(share, 0.0001)
    obj.scale = (share, share, share)


def own_material(code, rough=0.4):
    """自己一份、之後可以改顏色的材質（共用的材質改了會連別的東西一起變）。"""
    mat = bpy.data.materials.new(f"own-{len(bpy.data.materials)}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgb(code)
    bsdf.inputs["Roughness"].default_value = rough
    return mat


def tint(mat, start, end, share):
    a, b = rgb(start), rgb(end)
    mat.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = tuple(lerp(x, y, share) for x, y in zip(a, b))


SAMPLE_HOLDINGS = [("台積電", 980000, 2235000), ("長榮", 430000, 491000), ("0050", 175200, 292350)]   # 名稱、投入成本、目前市值（樣本資料）
SAMPLE_CASH, SAMPLE_TOTAL = 400000, 3418350


def outer_tank(width, depth, height, with_strata):
    """外層：今日股票水位的水槽（和加深版同一套：建議線、容忍帶、砝碼、確認天數）。回傳畫股票那一層要用的東西。"""
    z = lambda pct: height * pct / 100
    now, advice, band = 88.3, 90.0, 5.0
    if not with_strata:
        box((0, 0, z(now) / 2), (width - 0.12, depth - 0.12, z(now)), "#2c6cb4", bevel=0, rough=0.18)
    box((0, 0, (z(now) + height) / 2), (width - 0.12, depth - 0.12, height - z(now)), "#dfe7ef", bevel=0, rough=0.3)
    glass_tank(0, width, depth, height)
    box((0, -depth / 2 - 0.05, z(advice)), (width + 0.16, 0.03, z(band * 2)), GOLD, bevel=0, rough=0.5, alpha=0.3)
    box((0, -depth / 2 - 0.08, z(advice)), (width + 0.34, 0.05, 0.08), GOLD, bevel=0, rough=0.4)
    rack = height + 1.7
    box((0, 0, rack - 0.2), (width + 0.5, depth * 0.78, 0.4), SLAB, bevel=0.02)
    for sx in (-1, 1):
        box((sx * (width / 2 + 0.2), -depth / 2 - 0.08, (rack + z(advice)) / 2), (0.035, 0.035, rack - z(advice)), GOLD, bevel=0)
    slot = (width + 0.5) / 4
    for index, (name, points, absent) in enumerate((("趨勢", 2, 0), ("波動", 0, 1), ("預警", 1, 1), ("資金面", 0, 0))):
        x = -(width + 0.5) / 2 + slot * (index + 0.5)
        text(name, (x, -depth * 0.39 - 0.02, rack - 0.3), 0.2, DIM, align="CENTER", flat=False)
        for level in range(points + absent):
            box((x, 0, rack + 0.19 + level * 0.42), (slot - 0.26, depth * 0.52, 0.38), INK if level < points else GREY, bevel=0.03,
                rough=0.35 if level < points else 0.6, alpha=1.0 if level < points else 0.32)
    for index in range(2):
        ball((width / 2 + 0.55 + index * 0.3, -depth / 2 - 0.08, z(advice)), 0.1, GOLD)
    text("目前水位", (-width / 2 - 0.35, -depth / 2, z(now) + 0.1), 0.3, DIM, align="RIGHT", flat=False)
    text("88.3%", (-width / 2 - 0.35, -depth / 2, z(now) - 0.75), 0.74, INK, align="RIGHT", flat=False)
    text("建議 90%", (-width / 2 - 0.35, -depth / 2, z(now) - 1.3), 0.28, GOLD, align="RIGHT", flat=False)
    return z


def integ_a():
    """①③ 整合・方向甲「把那一層拿出來」。
    外層：水槽裡股票那一層不是一整塊，而是一檔一層疊起來的，每一層的厚度就是那一檔占總資金多少；最上面淺色的是現金。
    內層：把股票那幾層從槽裡拿出來、排成一排柱子；柱子的高度還是同一把尺（錢），這時每一根分成兩段——
    下面是投入的成本、上面變成紅色的是賺到的。同一個東西從頭到尾沒有換，只是拿出來看仔細。
    數字全部是樣本資料（三檔都是賺的，賠錢的樣子這張沒有畫）。"""
    width, depth, height = 3.6, 2.0, 6.0
    z = outer_tank(width, depth, height, with_strata=True)
    blues = ["#245f9f", "#3f7fc2", "#6fa3d6"]
    foot_x, foot_y, column = width - 0.12, depth - 0.12, 1.5
    stacks, floor = [], 0.0
    for index, (name, cost, value) in enumerate(SAMPLE_HOLDINGS):
        cost_h, value_h = height * cost / SAMPLE_TOTAL, height * value / SAMPLE_TOTAL
        lower = box((0, 0, floor + cost_h / 2), (foot_x, foot_y, cost_h), blues[index], bevel=0, rough=0.25)
        upper = box((0, 0, floor + cost_h + (value_h - cost_h) / 2), (foot_x, foot_y, value_h - cost_h), blues[index], bevel=0, rough=0.25)
        gain = own_material(blues[index], 0.3)
        upper.data.materials.clear()
        upper.data.materials.append(gain)
        x = width / 2 + 2.6 + index * 2.3
        # 字要高過柱頂那一面（從斜上方看，頂面會露出來）
        name_tag = text(name, (x, -column / 2, value_h + 1.5), 0.36, INK, align="CENTER", flat=False)
        rate_tag = text(f"{(value / cost - 1) * 100:+.0f}%", (x, -column / 2, value_h + 0.85), 0.46, UP, align="CENTER", flat=False)
        share_tag = text(f"{name} {value / SAMPLE_TOTAL * 100:.0f}%", (width / 2 + 0.3, -depth / 2, floor + value_h / 2 - 0.1), 0.24, DIM, flat=False)
        stacks.append((lower, upper, gain, blues[index], floor, cost_h, value_h, x, name_tag, rate_tag, share_tag))
        floor += value_h
    cash_tag = text("現金 12%", (width / 2 + 0.3, -depth / 2, z(94) - 0.1), 0.24, DIM, flat=False)

    def animate(t):
        for index, (lower, upper, gain, blue, floor, cost_h, value_h, x, name_tag, rate_tag, share_tag) in enumerate(stacks):
            move = part(t, 0.22 + index * 0.07, 0.66 + index * 0.07)
            lift = math.sin(math.pi * move) * 0.9
            base = lerp(floor, 0, move) + lift
            for piece, offset in ((lower, cost_h / 2), (upper, cost_h + (value_h - cost_h) / 2)):
                piece.location = (lerp(0, x, move), 0, base + offset)
                piece.scale = (lerp(1, column / foot_x, move), lerp(1, column / foot_y, move), 1)
            tint(gain, blue, UP, part(t, 0.62 + index * 0.05, 0.86 + index * 0.04))
            pop(name_tag, part(t, 0.78, 0.9))
            pop(rate_tag, part(t, 0.84, 0.96))
            pop(share_tag, 1 - part(t, 0.16, 0.26))
        pop(cash_tag, 1 - part(t, 0.16, 0.26))
    return Vector((3.5, 0, 4.2)), (76, 0, 14), 16.0, animate


def integ_b():
    """①③ 整合・方向乙「水面就是成本」。
    外層：和加深版一樣的水槽（股票是一整層）。
    內層：換成一個淺池。每檔持股是一根柱子站在池裡，底面積是占比；池子的水面代表「剛好打平」——
    柱子露出水面的那一截是賺的（越高賺越多），整根沉在水面下就是賠的。一眼就看得出誰賺誰賠、賺多少。
    數字全部是樣本資料（三檔都是賺的，沉在水面下的樣子這張沒有畫）。
    要留意：外層的水是「股票占總資金多少」，內層的水是「成本線」，同樣是水、意思不一樣。"""
    width, depth, height = 3.6, 2.0, 6.0
    outer_tank(width, depth, height, with_strata=False)
    unit, pool_d, pool_x = 1.35, 3.4, width / 2 + 5.4     # unit：成本的高度（水面）
    held = sum(value for _, _, value in SAMPLE_HOLDINGS)
    sides = [math.sqrt(value / held) * 3.3 for _, _, value in SAMPLE_HOLDINGS]
    pool_w = sum(sides) + 0.55 * (len(sides) - 1) + 1.0
    box((pool_x, 0, unit / 2), (pool_w - 0.1, pool_d - 0.1, unit), "#3b7cc0", bevel=0, rough=0.1, alpha=0.5)
    glass_tank(pool_x, pool_w, pool_d, unit + 0.5)
    text("水面＝成本", (pool_x - pool_w / 2 - 0.2, -pool_d / 2 - 0.08, -0.75), 0.4, BLUE, flat=False)
    pillars, cursor = [], pool_x - pool_w / 2 + 0.5
    for index, (name, cost, value) in enumerate(SAMPLE_HOLDINGS):
        side, top = sides[index], unit * value / cost
        x = cursor + side / 2
        cursor += side + 0.55
        under = box((x, 0, unit / 2), (side, side, unit), "#93a9c4", bevel=0, rough=0.5)
        above = box((x, 0, unit + (top - unit) / 2), (side, side, top - unit), UP, bevel=0.03, rough=0.42)
        name_tag = text(name, (x, -side / 2, top + 1.85), 0.36, INK, align="CENTER", flat=False)
        rate_tag = text(f"{(value / cost - 1) * 100:+.0f}%", (x, -side / 2, top + 1.2), 0.46, UP, align="CENTER", flat=False)
        pillars.append((under, above, top, name_tag, rate_tag))

    def animate(t):
        for index, (under, above, top, name_tag, rate_tag) in enumerate(pillars):
            now = top * part(t, 0.2 + index * 0.1, 0.72 + index * 0.08)   # 柱子從池底長上來，先到水面、再露出水面
            stretch(under, 0, unit, min(now, unit) / unit)
            stretch(above, min(now, unit), top - unit, max(0.0, now - unit) / (top - unit))   # 還沒到水面時跟著柱頂走，不先浮在水面上
            pop(name_tag, part(t, 0.8, 0.92))
            pop(rate_tag, part(t, 0.84, 0.96))
    return Vector((4.3, 0, 4.2)), (74, 0, 14), 17.5, animate


def towers():
    """⑤ 短中長期走勢（重新設計）「上下兩邊的票」。
    每一期中間一條橫線。偏多的指標一格一格往上疊（紅），偏空的一格一格往下疊（綠），每一格上面寫是哪個指標；
    旁邊的圓球停在「多的比空的多幾票」的高度：球在線上面是偏多、在線下面是偏空、停在線上是盤整。
    上下就是畫面的上下，不用再看箭頭往哪邊斜。沒有底板，三期排成一列，可以直接放進首頁那一塊。
    樣本資料裡走勢判讀還沒產生，這張的票是示意（票數照現行規則：短期 5 票、中期與長期各 4 票）。"""
    periods = [("短期", [("MA5", -1), ("MA10", -1), ("MACD", -1), ("RSI", -1), ("KD", 1)]),
               ("中期", [("MA60", -1), ("20日", -1), ("MA20", 1), ("法人", 1)]),
               ("長期", [("60日", -1), ("MA120", 1), ("MA60", 1), ("MA240", 1)])]
    wide, deep, tall, gap, pitch = 2.0, 1.0, 0.62, 0.07, 4.3
    movers = []
    for column, (label, votes) in enumerate(periods):
        x = column * pitch
        box((x + 0.35, 0, 0), (wide + 1.5, deep + 0.06, 0.05), INK, bevel=0)
        net = sum(side for _, side in votes)
        code, state = (UP, "偏多") if net > 0 else (DOWN, "偏空") if net < 0 else (GOLD, "盤整")
        cells = []
        for side in (1, -1):
            for level, (name, _) in enumerate([vote for vote in votes if vote[1] == side]):
                near = side * (0.03 + level * (tall + gap))
                cell = box((x, 0, near + side * tall / 2), (wide, deep, tall), UP if side > 0 else DOWN, bevel=0.03, rough=0.42)
                tag = text(name, (x, -deep / 2 - 0.02, near + side * tall / 2 - 0.13), 0.3, SLAB, align="CENTER", flat=False)
                cells.append((cell, tag, near, side))
        stem = box((x + wide / 2 + 0.55, 0, 0), (0.05, 0.05, 1), code, bevel=0)
        dot = ball((x + wide / 2 + 0.55, 0, 0), 0.24, code)
        count = text(f"{net:+d}" if net else "0", (x + wide / 2 + 0.95, -0.2, net * (tall + gap) - 0.14), 0.38, code, flat=False)
        text(label, (x - wide / 2, -deep / 2, 3.5), 0.34, DIM, flat=False)
        text(state, (x - wide / 2 + 0.95, -deep / 2, 3.42), 0.56, code, flat=False)
        movers.append((cells, stem, dot, count, net, column))

    def animate(t):
        for cells, stem, dot, count, net, column in movers:
            total = 0.0
            for order, (cell, tag, near, side) in enumerate(cells):
                grow = part(t, 0.05 + column * 0.06 + order * 0.1, 0.27 + column * 0.06 + order * 0.1)
                cell.scale.z = max(grow, 0.001)
                cell.location.z = near + side * tall * grow / 2
                pop(tag, part(t, 0.2 + column * 0.06 + order * 0.1, 0.32 + column * 0.06 + order * 0.1))
                total += side * grow
            level = total * (tall + gap)
            dot.location.z = level
            stem.scale.z = max(abs(level), 0.001)
            stem.location.z = level / 2
            pop(count, part(t, 0.82, 0.94))
    return Vector((pitch + 0.35, 0, 0.45)), (76, 0, 0), 14.6, animate   # 正面看：三條橫線在同一個高度，上下才好比


CLEAR = {"integ-a", "integ-b", "towers"}   # 沒有底板、背景透明的場景

built = {"tank": tank, "stairs": stairs, "blocks": blocks, "bins": bins, "ramps": ramps, "terraces": terraces,
         "tank2": tank2, "blocks2": blocks2, "jacks": jacks, "integ-a": integ_a, "integ-b": integ_b, "towers": towers}[concept]()
target, angles, ortho = built[:3]
animate = built[3] if len(built) > 3 else None

# 鏡頭：正投影，從前上方看
camera_data = bpy.data.cameras.new("camera")
camera_data.type = "ORTHO"
camera_data.ortho_scale = ortho
camera = bpy.data.objects.new("camera", camera_data)
scene.collection.objects.link(camera)
camera.rotation_euler = tuple(math.radians(a) for a in angles)
camera.location = target + camera.rotation_euler.to_matrix() @ Vector((0, 0, 40))
scene.camera = camera

# 光：一盞大的柔光從左前上方，加一點環境光
light_data = bpy.data.lights.new("key", "AREA")
light_data.energy = 3600
light_data.size = 14
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
light.location = target + Vector((-9, -10, 16))
light.rotation_euler = (math.radians(38), 0, math.radians(-42))
world = bpy.data.worlds.new("world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = rgb(DESK)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.world = world

scene.render.resolution_x, scene.render.resolution_y = side_px, side_px
scene.render.film_transparent = concept in CLEAR
scene.view_settings.view_transform = "Standard"
for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        scene.render.engine = name
        break
    except TypeError:
        continue
print("engine", scene.render.engine, "font", FONT.name if FONT else None)
if animate and len(args) > 3 and args[3] == "final":   # 只算最後一格，調整構圖時用
    animate(1.0)
    scene.render.filepath = str(out / f"render-{concept}.png")
    bpy.ops.render.render(write_still=True)
    print("saved", scene.render.filepath)
elif animate:
    frames = 30
    (out / f"frames-{concept}").mkdir(exist_ok=True)
    for index in range(frames):
        animate(index / (frames - 1))
        scene.render.filepath = str(out / f"frames-{concept}" / f"f{index:03d}.png")
        bpy.ops.render.render(write_still=True)
    print("saved", frames, "frames", out / f"frames-{concept}")
else:
    scene.render.filepath = str(out / f"render-{concept}.png")
    bpy.ops.render.render(write_still=True)
    print("saved", scene.render.filepath)
