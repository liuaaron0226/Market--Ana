"""風格板：同一段內容用四種視覺畫出來，請本人指出哪一種最接近他要的（本人 2026-11-15：四輪樣品的物件與動畫都不喜歡，要天馬行空）。
內容固定是「短期走勢：1 個指標偏多（KD）、4 個偏空（MA5、MA10、MACD、RSI）」——偏多在線上面、偏空在線下面，這個讀法本人已經說方向對。
四種視覺：neon 霓虹光管、liquid 液態玻璃、swarm 粒子群、holo 全像線框。深色背景（本人的手機用深色主題）。
只產生圖片，不是網站執行時用的程式；發光的暈由 style-board-sheet.py 事後加上。
用法：blender -b -P style-board-blender.py -- <輸出資料夾（要給完整路徑）> <風格名稱>"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
out, style = Path(args[0]), args[1]
out.mkdir(parents=True, exist_ok=True)
UP_VOTES, DOWN_VOTES = ["KD"], ["MA5", "MA10", "MACD", "RSI"]
RED, GREEN, WHITE, NIGHT = "#ff4d5e", "#2fe0a2", "#f4f6fb", "#12131a"
RISE = 1.15   # 一票的間距


def rgb(code: str, alpha: float = 1.0):
    code = code.lstrip("#")
    srgb = [int(code[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return (*[c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb], alpha)


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def glow(code, strength=6.0):
    """自己會發光的材質。"""
    mat = bpy.data.materials.new(f"glow-{code}-{strength}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgb(code)
    bsdf.inputs["Emission Color"].default_value = rgb(code)
    bsdf.inputs["Emission Strength"].default_value = strength
    return mat


def glossy(code, alpha=1.0):
    """像糖果或玻璃一樣亮的材質。"""
    mat = bpy.data.materials.new(f"glossy-{code}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgb(code)
    bsdf.inputs["Roughness"].default_value = 0.06
    bsdf.inputs["Coat Weight"].default_value = 1.0
    bsdf.inputs["Emission Color"].default_value = rgb(code)
    bsdf.inputs["Emission Strength"].default_value = 0.35
    if alpha < 1:
        bsdf.inputs["Alpha"].default_value = alpha
        for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(mat, attr, value)
            except Exception:  # noqa: BLE001
                pass
    return mat


FONT = None
for path in ("C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc"):
    try:
        FONT = bpy.data.fonts.load(path)
        break
    except Exception:  # noqa: BLE001
        pass


def label(body, x, z, size=0.3, code=WHITE, align="CENTER", y=-0.75):
    bpy.ops.object.text_add(location=(x, y, z))
    obj = bpy.context.active_object
    obj.data.body, obj.data.size, obj.data.align_x = body, size, align
    if FONT:
        obj.data.font = FONT
    obj.rotation_euler = (math.radians(90), 0, 0)
    obj.data.materials.append(glow(code, 2.2))
    return obj


def bar(center, size, mat):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.scale = size
    obj.data.materials.append(mat)
    return obj


def levels():
    """每一票的位置與顏色：偏多在線上面、偏空在線下面。"""
    spots = [(name, (index + 0.8) * RISE, RED) for index, name in enumerate(UP_VOTES)]
    return spots + [(name, -(index + 0.8) * RISE, GREEN) for index, name in enumerate(DOWN_VOTES)]


NET = len(UP_VOTES) - len(DOWN_VOTES)


def neon():
    """霓虹光管：每一票是一圈發光的管子，中間一條光把它們串起來。"""
    bar((0.4, 0, 0), (4.4, 0.05, 0.05), glow(WHITE, 5))
    for name, z, code in levels():
        bpy.ops.mesh.primitive_torus_add(location=(0, 0, z), major_radius=0.47, minor_radius=0.05, rotation=(math.radians(90), 0, 0), major_segments=72)
        bpy.context.active_object.data.materials.append(glow(code, 4.2 if code == RED else 6))   # 紅色太強會過曝成粉白
        bpy.ops.object.shade_smooth()
        label(name, 0, z - 0.11, 0.25)
    bar((0, 0.05, (len(UP_VOTES) - len(DOWN_VOTES)) * RISE / 2), (0.03, 0.03, (len(UP_VOTES) + len(DOWN_VOTES)) * RISE), glow(WHITE, 1.2))
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.24, depth=0.36, location=(1.75, 0, NET * RISE), rotation=(math.radians(180), 0, 0))
    bpy.context.active_object.data.materials.append(glow(GREEN, 9))
    label(f"{NET:+d}", 2.2, NET * RISE - 0.13, 0.36, GREEN, "LEFT")


def liquid():
    """液態玻璃：每一票是一滴會融在一起的液體，偏多的往上冒、偏空的往下滴。"""
    bar((0.4, 0, 0), (4.4, 0.3, 0.07), glossy("#cfd8ea", 0.55))
    for base, votes, code, sign in (("Rise", UP_VOTES, RED, 1), ("Sink", DOWN_VOTES, GREEN, -1)):
        data = bpy.data.metaballs.new(base)
        data.resolution = data.render_resolution = 0.045
        obj = bpy.data.objects.new(base, data)
        scene.collection.objects.link(obj)
        obj.data.materials.append(glossy(code))
        root = data.elements.new()
        root.co, root.radius = (0, 0, sign * 0.12), 0.62
        for index, name in enumerate(votes):
            z = sign * (index + 0.8) * RISE
            drop = data.elements.new()
            drop.co, drop.radius = (0, 0, z), 0.86 - index * 0.03
            if index:
                neck = data.elements.new()
                neck.co, neck.radius = (0, 0, z - sign * RISE / 2), 0.5
            label(name, 0, z - 0.11, 0.25)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.2, location=(1.75, 0, NET * RISE))
    bpy.ops.object.shade_smooth()
    bpy.context.active_object.data.materials.append(glossy(GREEN))
    label(f"{NET:+d}", 2.2, NET * RISE - 0.13, 0.36, GREEN, "LEFT")


def swarm():
    """粒子群：每一票是一團發光的小點；偏空的那幾團往下拖著尾巴。"""
    bar((0.4, 0, 0), (4.4, 0.04, 0.04), glow(WHITE, 4))
    seeds = {}   # 一種顏色一份網格：材質是掛在網格上的，共用同一份的話顏色會互相蓋掉
    for code, strength in ((RED, 4.5), (GREEN, 7)):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1)
        seeds[code] = bpy.context.active_object
        seeds[code].hide_render = True
        seeds[code].data.materials.append(glow(code, strength))
    count = 0
    for name, z, code in levels():
        for index in range(150):
            a, b, c = math.sin(index * 12.9898 + z) * 43758.5453 % 1, math.sin(index * 78.233 + z * 3) * 12543.123 % 1, math.sin(index * 37.719 + z * 7) * 9321.77 % 1
            radius, theta, phi = 0.5 * a ** 0.6, b * math.tau, math.acos(2 * c - 1)
            tail = (a ** 3) * 0.9 * (1 if z > 0 else -1)   # 少數的點往外飄：偏多的往上、偏空的往下
            dot = bpy.data.objects.new(f"dot{count}", seeds[code].data)
            scene.collection.objects.link(dot)
            dot.location = (radius * math.sin(phi) * math.cos(theta), radius * math.sin(phi) * math.sin(theta) * 0.5, z + radius * math.cos(phi) + tail)
            size = 0.014 + 0.03 * (1 - a)
            dot.scale = (size, size, size)
            count += 1
        label(name, 0.78, z - 0.11, 0.25, WHITE, "LEFT")
    label(f"{NET:+d}", 2.2, NET * RISE - 0.13, 0.36, GREEN, "LEFT")
    bar((1.75, 0, NET * RISE / 2), (0.02, 0.02, abs(NET) * RISE), glow(GREEN, 4))


def holo():
    """全像線框：每一票是一顆只有線條的多面體，像投影出來的。"""
    for index in range(22):
        bar((-1.75 + index * 0.2, 0, 0), (0.11, 0.03, 0.03), glow(WHITE, 4))
    for name, z, code in levels():
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.5, location=(0, 0, z), rotation=(0.3, 0.2, z))
        shell = bpy.context.active_object
        wire = shell.modifiers.new("wire", "WIREFRAME")
        wire.thickness = 0.03
        shell.data.materials.append(glow(code, 4.5 if code == RED else 7))
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.47, location=(0, 0, z), rotation=(0.3, 0.2, z))
        bpy.context.active_object.data.materials.append(glossy(code, 0.16))
        label(name, 0.78, z - 0.11, 0.25, WHITE, "LEFT")
    for step in range(abs(NET)):
        bar((1.75, 0, (step + 0.5) * RISE * (1 if NET > 0 else -1)), (0.34, 0.03, 0.05), glow(GREEN, 7))
    label(f"{NET:+d}", 2.2, NET * RISE - 0.13, 0.36, GREEN, "LEFT")


{"neon": neon, "liquid": liquid, "swarm": swarm, "holo": holo}[style]()
label("短期", -1.9, 2.55, 0.34, "#9aa0ad", "LEFT")
label("偏空", -1.05, 2.48, 0.56, GREEN, "LEFT")

camera_data = bpy.data.cameras.new("camera")
camera_data.type = "ORTHO"
camera_data.ortho_scale = 9.6
camera = bpy.data.objects.new("camera", camera_data)
scene.collection.objects.link(camera)
camera.rotation_euler = (math.radians(84), 0, 0)
camera.location = Vector((0.45, 0, -1.2)) + camera.rotation_euler.to_matrix() @ Vector((0, 0, 40))
scene.camera = camera
for position, energy in (((-6, -9, 8), 2600), ((7, -6, -3), 700)):
    light_data = bpy.data.lights.new("light", "AREA")
    light_data.energy, light_data.size = energy, 9
    light = bpy.data.objects.new("light", light_data)
    scene.collection.objects.link(light)
    light.location = position
    light.rotation_euler = (Vector((0.4, 0, -1)) - Vector(position)).to_track_quat("-Z", "Y").to_euler()
world = bpy.data.worlds.new("world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = rgb(NIGHT)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.world = world
scene.render.resolution_x = scene.render.resolution_y = 800
scene.view_settings.view_transform = "Standard"
for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        scene.render.engine = name
        break
    except TypeError:
        continue
scene.render.filepath = str(out / f"style-{style}.png")
bpy.ops.render.render(write_still=True)
print("saved", scene.render.filepath)
