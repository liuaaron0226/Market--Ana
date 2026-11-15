"""Blender 概念樣品（下一批立體呈現，本人 2026-11-15 要求先看樣品再決定）。只產生圖片，不是網站執行時用的程式。
六個場景：tank 今日股票水位、stairs 價位階梯、blocks 庫存總覽、bins 隔日三區歷史分布、ramps 短中長期走勢、terraces 大戶持股。
數字來自 next-concepts-data.py 取出的樣本資料；樣本裡不夠的（走勢判讀、大戶持股的逐週序列、賠錢的庫存）用示意數字，
哪些是示意由 next-concepts-sheet.py 寫在圖上。
用法：blender -b -P next-concepts-blender.py -- <輸出資料夾> <場景名稱>"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
out = Path(args[0])
concept = args[1]
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


target, angles, ortho = {"tank": tank, "stairs": stairs, "blocks": blocks, "bins": bins, "ramps": ramps, "terraces": terraces}[concept]()

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

scene.render.resolution_x, scene.render.resolution_y = 1000, 1000
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        scene.render.engine = name
        break
    except TypeError:
        continue
print("engine", scene.render.engine, "font", FONT.name if FONT else None)
scene.render.filepath = str(out / f"render-{concept}.png")
bpy.ops.render.render(write_still=True)
print("saved", scene.render.filepath)
