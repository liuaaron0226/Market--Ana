"""Blender 概念樣品：首頁「籌碼與資金」六項近 20 天的大膽立體呈現。只產生圖片，不是網站執行時用的程式。
用法：blender -b -P blender_chips.py -- <chips.json> <輸出資料夾> <concept: relief|dials> [engine]"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
out = Path(args[1])
concept = args[2]
engine = args[3] if len(args) > 3 else "eevee"
out.mkdir(parents=True, exist_ok=True)


def rgb(code: str, alpha: float = 1.0):
    code = code.lstrip("#")
    srgb = [int(code[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (*linear, alpha)


DESK, SLAB, INK, DIM = "#e6e1d6", "#fbfaf6", "#1c1b22", "#77757d"
BOARD = "#f1ede4"
UP, DOWN, GOLD, ACCENT, BLUE, GREY = "#c8202a", "#0f7b5a", "#9a6a1c", "#c2410c", "#235b9f", "#b7b3a9"

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
materials = {}


def material(code: str, rough: float = 0.55):
    key = (code, rough)
    if key not in materials:
        mat = bpy.data.materials.new(code)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = rgb(code)
        bsdf.inputs["Roughness"].default_value = rough
        materials[key] = mat
    return materials[key]


def box(center, size, code, bevel=0.03, rough=0.55):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
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


def number(value, unit):
    if unit == "萬張":
        return f"{value:,.1f} {unit}"
    return f"{abs(value):,.0f} {unit}"


def relief():
    """資金浮雕：六條軌道，每條 20 天，最右邊是今天。
    外資現貨與投信：一條白色的台子，買超的日子在台子上長出紅色的柱，賣超的日子台子凹下去、露出綠色；凹得越深賣得越多。
    餘額與成交金額：站在桌面上的柱，高度是這 20 天裡的相對高低，今天那一根上色。"""
    lane_gap, bar_w, bar_gap, depth_y = 2.6, 0.38, 0.12, 0.7
    span = 20 * (bar_w + bar_gap) - bar_gap
    plinth = 0.62
    for row, item in enumerate(data):
        y = -row * lane_gap
        values = item["values"]
        today = values[-1]
        if item["signed"]:
            peak = max(abs(v) for v in values) or 1
            # 一條白色的槽：前後兩道槽壁到台面的高度；買超的日子槽填平再長出紅柱，賣超的日子槽底是綠的、賣得越多槽越深
            for side in (-1, 1):
                box((span / 2, y + side * (depth_y / 2 + 0.09), plinth / 2), (span + 0.24, 0.18, plinth), SLAB, bevel=0.02)
            for i, v in enumerate(values):
                x = i * (bar_w + bar_gap) + bar_w / 2
                size = abs(v) / peak
                if v >= 0:
                    box((x, y, plinth / 2), (bar_w + bar_gap, depth_y, plinth), SLAB, bevel=0)
                    h = max(0.05, size * 1.1)
                    box((x, y, plinth + h / 2), (bar_w, depth_y - 0.08, h), UP, bevel=0.02, rough=0.4)
                else:
                    h = plinth - max(0.08, size * 0.55)
                    box((x, y, h / 2), (bar_w + bar_gap, depth_y, h), DOWN, bevel=0, rough=0.5)
            label_color = UP if today >= 0 else DOWN
            label = f"{'買' if today >= 0 else '賣'} {number(today, item['unit'])}"
        else:
            lo, hi = min(values), max(values)
            for i, v in enumerate(values):
                h = 0.2 + (v - lo) / ((hi - lo) or 1) * 1.45
                x = i * (bar_w + bar_gap) + bar_w / 2
                last = i == len(values) - 1
                box((x, y, h / 2), (bar_w, depth_y, h), {"futures": DOWN, "margin": GOLD, "short": ACCENT, "volume": BLUE}[item["key"]] if last else "#a39e93",
                    bevel=0.02, rough=0.4 if last else 0.75)
            label_color = INK
            label = number(today, item["unit"])
        text(item["name"], (-0.5, y - 0.45, 0.62), 0.36, DIM, align="RIGHT", flat=False)
        text(label, (-0.5, y - 0.45, 0.02), 0.54, label_color, align="RIGHT", flat=False)
    depth = lane_gap * (len(data) - 1)
    box((span / 2 - 2.2, -depth / 2, -0.12), (span + 6.4, depth + 3.4, 0.24), BOARD, bevel=0.12, rough=0.6)
    return Vector((span / 2 - 2.0, -depth / 2, 0.5)), (52, 0, 10), 20.5


def dials():
    """資金儀表：六座嵌在桌面上的儀表。指針指出今天在這 20 天裡的位置；
    外資現貨與投信的錶面左半是賣超（綠）、右半是買超（紅），中間是零。"""
    cols, gap = 2, 5.0
    for index, item in enumerate(data):
        col, row = index % cols, index // cols
        cx, cy = col * gap, -row * gap
        values = item["values"]
        today = values[-1]
        bpy.ops.mesh.primitive_cylinder_add(radius=1.75, depth=0.3, location=(cx, cy, 0.15), vertices=96)
        rim = bpy.context.active_object
        rim.data.materials.append(material(SLAB, 0.5))
        bpy.ops.object.shade_smooth()
        bpy.ops.mesh.primitive_cylinder_add(radius=1.5, depth=0.06, location=(cx, cy, 0.31), vertices=96)
        face = bpy.context.active_object
        face.data.materials.append(material("#f1eee6", 0.7))
        if item["signed"]:
            peak = max(abs(v) for v in values) or 1
            ratio = max(-1, min(1, today / peak))          # −1（最大賣超）到 +1（最大買超）
        else:
            lo, hi = min(values), max(values)
            ratio = (today - lo) / ((hi - lo) or 1) * 2 - 1  # −1（20 天最低）到 +1（20 天最高）
        # 刻度弧：近 20 天每一天一格，顏色依那一天的方向或高低
        for i, v in enumerate(values):
            if item["signed"]:
                r = max(-1, min(1, v / (max(abs(x) for x in values) or 1)))
                code = UP if v >= 0 else DOWN
            else:
                lo, hi = min(values), max(values)
                r = (v - lo) / ((hi - lo) or 1) * 2 - 1
                code = GREY
            angle = math.radians(90 - (i / 19 * 2 - 1) * 120)
            length = 0.12 + abs(r) * 0.3 if item["signed"] else 0.12 + (r + 1) / 2 * 0.3
            radius = 1.32 - length / 2
            tick = box((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius, 0.36), (length, 0.07, 0.04), code, bevel=0)
            tick.rotation_euler = (0, 0, angle)
        angle = math.radians(90 - ratio * 120)
        needle = box((cx + math.cos(angle) * 0.5, cy + math.sin(angle) * 0.5, 0.42), (1.0, 0.07, 0.05),
                     (UP if today >= 0 else DOWN) if item["signed"] else INK, bevel=0.01, rough=0.35)
        needle.rotation_euler = (0, 0, angle)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.13, depth=0.12, location=(cx, cy, 0.42), vertices=48)
        bpy.context.active_object.data.materials.append(material(INK, 0.3))
        label = f"{'買' if today >= 0 else '賣'} {number(today, item['unit'])}" if item["signed"] else number(today, item["unit"])
        text(item["name"], (cx, cy - 2.25, 0.02), 0.3, DIM, align="CENTER")
        text(label, (cx, cy - 2.85, 0.02), 0.46, (UP if today >= 0 else DOWN) if item["signed"] else INK, align="CENTER")
    rows = math.ceil(len(data) / cols)
    box(((cols - 1) * gap / 2, -(rows - 1) * gap / 2 - 0.5, -0.12), (cols * gap + 0.4, rows * gap + 1.0, 0.24), BOARD, bevel=0.12, rough=0.6)
    return Vector(((cols - 1) * gap / 2, -(rows - 1) * gap / 2 - 0.5, 0.2)), (32, 0, 0), 16.5


target, angles, ortho = relief() if concept == "relief" else dials()

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

scene.render.resolution_x, scene.render.resolution_y = 1100, 1100
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
if engine == "cycles":
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
else:
    for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = name
            break
        except TypeError:
            continue
print("engine", scene.render.engine, "font", FONT.name if FONT else None)
scene.render.filepath = str(out / f"concept-{concept}.png")
bpy.ops.render.render(write_still=True)
print("saved", scene.render.filepath)
