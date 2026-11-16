"""風格板：同一段內容用四種視覺畫出來，請本人指出哪一種最接近他要的（本人 2026-11-15：四輪樣品的物件與動畫都不喜歡，要天馬行空）。
內容固定是「短期走勢：1 個指標偏多（KD）、4 個偏空（MA5、MA10、MACD、RSI）」——偏多在線上面、偏空在線下面，這個讀法本人已經說方向對。
四種視覺：neon 霓虹光管、liquid 液態玻璃、swarm 粒子群、holo 全像線框。深色背景（本人的手機用深色主題）。
只產生圖片，不是網站執行時用的程式；發光的暈由 style-board-sheet.py 事後加上。
本人看過風格板之後說不喜歡「垂直表達指標」這種表達方式，所以另外加了四種「表達的概念」（三期都畫，票是示意）：
tug 拔河、rings 年輪、path 一條走勢帶、weather 天氣。質感先用同一種（發光的線與球），這一輪只比概念。
這四種本人也都不要（回答「其他的」），再加四種有生命感的：fish 魚群、plant 植物、wave 海浪、planet 行星。
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


# ---------- 表達的概念：三期都畫。票是示意（樣本資料裡走勢判讀還沒產生），票數照現行規則 ----------
PERIODS = [("短期", ["KD"], ["MA5", "MA10", "MACD", "RSI"]), ("中期", ["MA20", "法人"], ["MA60", "20日"]), ("長期", ["MA120", "MA60", "MA240"], ["60日"])]
GOLD_LIGHT, GREY_LIGHT = "#f2c14e", "#9aa0ad"


def verdict(bulls, bears):
    net = len(bulls) - len(bears)
    return (net, "偏多", RED) if net > 0 else (net, "偏空", GREEN) if net < 0 else (net, "盤整", GOLD_LIGHT)


def orb(x, z, radius, mat, y=0.0):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=(x, y, z), segments=40, ring_count=20)
    bpy.ops.object.shade_smooth()
    bpy.context.active_object.data.materials.append(mat)
    return bpy.context.active_object


def tug():
    """拔河：每一期一條橫的繩子，偏多的指標在右邊拉、偏空的在左邊拉，中間的結被拉向人多的那一邊。左右，不是上下。"""
    for row, (name, bulls, bears) in enumerate(PERIODS):
        z = 2.6 - row * 2.6
        net, word, code = verdict(bulls, bears)
        bar((0, 0, z), (9.2, 0.03, 0.03), glow(WHITE, 2.5))
        bar((0, 0.02, z), (0.03, 0.03, 1.1), glow(GREY_LIGHT, 1.5))
        for index, vote in enumerate(bears):
            orb(-1.3 - index * 0.92, z, 0.36, glow(GREEN, 3.2))
            label(vote, -1.3 - index * 0.92, z - 0.75, 0.2)
        for index, vote in enumerate(bulls):
            orb(1.3 + index * 0.92, z, 0.36, glow(RED, 2.6))
            label(vote, 1.3 + index * 0.92, z - 0.75, 0.2)
        bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.3, radius2=0.3, depth=0.3, location=(net * 0.28, -0.1, z), rotation=(math.radians(90), 0, math.radians(45)))
        bpy.context.active_object.data.materials.append(glow(code, 6))
        label(name, -5.6, z + 0.25, 0.3, GREY_LIGHT, "LEFT")
        label(word, -5.6, z - 0.45, 0.5, code, "LEFT")
    label("空", -4.4, 3.75, 0.34, GREEN)
    label("多", 4.4, 3.75, 0.34, RED)
    return Vector((-0.5, 0, 0.1)), 12.6


def sector(inner, outer, start, end, mat, y=0.0, depth=0.12):
    """一段圓環（面向鏡頭）。角度從正上方起算、順時針。"""
    steps = max(4, int((end - start) / 4))
    verts = []
    for index in range(steps + 1):
        angle = math.radians(start + (end - start) * index / steps)
        for radius in (inner, outer):
            for side in (-depth / 2, depth / 2):
                verts.append((radius * math.sin(angle), y + side, radius * math.cos(angle)))
    faces = []
    for index in range(steps):
        a, b = index * 4, index * 4 + 4
        faces += [[a, a + 2, b + 2, b], [a + 1, b + 1, b + 3, a + 3], [a, b, b + 1, a + 1], [a + 2, a + 3, b + 3, b + 2]]
    faces += [[0, 1, 3, 2], [steps * 4, steps * 4 + 2, steps * 4 + 3, steps * 4 + 1]]
    mesh = bpy.data.meshes.new("sector")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("sector", mesh)
    scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def rings():
    """年輪：一個同心圓。最裡面一圈是短期、中間是中期、最外面是長期；每一圈切成幾段，一段是一個指標，紅的偏多、綠的偏空。
    一眼看得到三期各有幾段紅、幾段綠，也看得到是哪幾個指標。"""
    for ring, (name, bulls, bears) in enumerate(PERIODS):
        inner, outer = 1.0 + ring * 1.15, 1.9 + ring * 1.15
        votes = [(vote, RED) for vote in bulls] + [(vote, GREEN) for vote in bears]
        span = 360 / len(votes)
        for index, (vote, code) in enumerate(votes):
            sector(inner, outer, index * span + 3, (index + 1) * span - 3, glow(code, 0.55))   # 環暗一點，上面的白字才讀得到
            middle = math.radians((index + 0.5) * span)
            label(vote, (inner + outer) / 2 * math.sin(middle), (inner + outer) / 2 * math.cos(middle) - 0.1, 0.24, WHITE, y=-0.2)
        net, word, code = verdict(bulls, bears)
        label(f"{name} {word}", 5.2, 2.4 - ring * 1.1, 0.42, code, "LEFT")
    label("裡圈短期", 5.2, -1.6, 0.26, GREY_LIGHT, "LEFT")
    label("外圈長期", 5.2, -2.1, 0.26, GREY_LIGHT, "LEFT")
    return Vector((1.9, 0, 0.0)), 12.6


def path():
    """一條走勢帶：一條從左（短期）走到右（長期）的光帶，三段的高低就是那一期偏多還是偏空；
    帶子上的每一顆珠子是一個指標，紅的偏多、綠的偏空。看的是一條線怎麼走，不是一疊東西。"""
    spots = []
    for column, (name, bulls, bears) in enumerate(PERIODS):
        net, word, code = verdict(bulls, bears)
        spots.append((-4.2 + column * 4.2, net * 0.55, name, word, code, [(vote, RED) for vote in bulls] + [(vote, GREEN) for vote in bears]))
    points = [(-6.4, spots[0][1])] + [(x, z) for x, z, *_ in spots] + [(6.4, spots[-1][1])]
    steps = 80
    for index in range(steps):
        s0, s1 = index / steps, (index + 1) / steps
        def at(s):
            f = s * (len(points) - 1)
            i = min(int(f), len(points) - 2)
            t = f - i
            t = t * t * (3 - 2 * t)
            return points[i][0] + (points[i + 1][0] - points[i][0]) * (f - i), points[i][1] + (points[i + 1][1] - points[i][1]) * t
        (xa, za), (xb, zb) = at(s0), at(s1)
        nearest = min(spots, key=lambda spot: abs(spot[0] - (xa + xb) / 2))
        seg = bar(((xa + xb) / 2, 0.1, (za + zb) / 2), (math.hypot(xb - xa, zb - za) + 0.02, 0.05, 0.14), glow(nearest[4], 1.6))
        seg.rotation_euler = (0, -math.atan2(zb - za, xb - xa), 0)
    for x, z, name, word, code, votes in spots:
        for index, (vote, colour) in enumerate(votes):
            bx = x + (index - (len(votes) - 1) / 2) * 0.78
            orb(bx, z, 0.3, glow(colour, 2.6 if colour == RED else 3.2), y=-0.1)
            label(vote, bx, z - 0.72, 0.19)
        label(name, x, z + 1.35, 0.3, GREY_LIGHT)
        label(word, x, z + 0.7, 0.5, code)
    return Vector((0, 0, -0.1)), 14.0


def weather():
    """天氣：每一期一個天氣。偏多的指標是太陽的光芒（一道光一個指標）、偏空的指標是雲底下的雨滴（一滴一個指標）；
    太陽大雲小就是偏多、烏雲下雨就是偏空、半晴半雲是盤整。"""
    for column, (name, bulls, bears) in enumerate(PERIODS):
        x = -4.4 + column * 4.4
        net, word, code = verdict(bulls, bears)
        sun = 0.35 + 0.16 * len(bulls)
        orb(x + 0.5, 1.2, sun, glow(RED, 2.4), y=0.3)
        for index, vote in enumerate(bulls):
            angle = math.radians(35 + index * 42)
            ray = bar((x + 0.5 + math.cos(angle) * (sun + 0.5), 0.3, 1.2 + math.sin(angle) * (sun + 0.5)), (0.55, 0.05, 0.07), glow(RED, 3))
            ray.rotation_euler = (0, -angle, 0)
            label(vote, x + 0.5 + math.cos(angle) * (sun + 1.25), 1.2 + math.sin(angle) * (sun + 1.25) - 0.08, 0.18)
        puff = 0.3 + 0.1 * len(bears)
        for dx, dz, scale in ((-0.75, 0, 0.8), (-0.2, 0.22, 1.0), (0.35, 0.02, 0.85), (-0.2, -0.1, 0.9)):
            orb(x - 0.3 + dx * puff * 2.2, 0.2 + dz * puff * 2.2, puff * scale, glossy("#8f9bb0"), y=-0.2)
        for index, vote in enumerate(bears):
            dx = (index - (len(bears) - 1) / 2) * 0.62
            drop = orb(x - 0.45 + dx, -0.95 - (index % 2) * 0.35, 0.15, glow(GREEN, 4), y=-0.2)
            drop.scale = (1, 1, 1.5)
            label(vote, x - 0.45 + dx, -1.9 - (index % 2) * 0.3, 0.18)
        label(name, x - 0.6, 3.6, 0.3, GREY_LIGHT)
        label(word, x + 0.5, 3.5, 0.5, code)
    return Vector((0, 0, 0.7)), 14.0


IDEA_SCENES = {"tug": tug, "rings": rings, "path": path, "weather": weather}

def fish():
    """魚群：每個指標是一條魚。偏多的魚往右上游（紅）、偏空的往右下游（綠）；整群往哪邊游，那一期就是偏多還是偏空。"""
    for column, (name, bulls, bears) in enumerate(PERIODS):
        x = -4.4 + column * 4.4
        net, word, code = verdict(bulls, bears)
        school = [(vote, 1) for vote in bulls] + [(vote, -1) for vote in bears]
        for index, (vote, side) in enumerate(school):
            fx = x + (index % 3 - 1) * 1.05 + (0.3 if index // 3 % 2 else 0)
            fz = 0.9 - (index // 3) * 1.25 + side * 0.35
            angle = math.radians(30 * side)
            body = orb(fx, fz, 0.36, glow(RED if side > 0 else GREEN, 2.6 if side > 0 else 3.2))
            body.scale = (1.0, 0.5, 0.42)
            body.rotation_euler = (0, -angle, 0)
            bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.2, depth=0.3, location=(fx - math.cos(angle) * 0.45, 0, fz - math.sin(angle) * 0.45),
                                            rotation=(0, math.radians(90) - angle, 0))
            bpy.context.active_object.data.materials.append(glow(RED if side > 0 else GREEN, 2.2))
            label(vote, fx, fz - 0.55, 0.17)
        label(name, x - 0.6, 3.4, 0.3, GREY_LIGHT)
        label(word, x + 0.5, 3.3, 0.5, code)
    return Vector((0, 0, 0.5)), 14.0


def plant():
    """植物：每一期一株。偏多的指標是往上挺的葉子（紅）、偏空的是垂下來的葉子（綠）；
    偏多的那一株長得直、頂上開花，偏空的那一株整株彎下去。"""
    for column, (name, bulls, bears) in enumerate(PERIODS):
        x = -4.4 + column * 4.4
        net, word, code = verdict(bulls, bears)
        leaves = [(vote, 1) for vote in bulls] + [(vote, -1) for vote in bears]
        bend, px, pz, heading = -net * 0.085, x, -1.9, math.radians(90)
        for step in range(22):
            heading += bend * (step / 22)
            nx, nz = px + math.cos(heading) * 0.2, pz + math.sin(heading) * 0.2
            stalk = bar(((px + nx) / 2, 0, (pz + nz) / 2), (0.24, 0.06, 0.07), glow("#7fd8a8", 1.2))
            stalk.rotation_euler = (0, -heading, 0)
            if step % 4 == 2 and step // 4 < len(leaves):
                vote, side = leaves[step // 4]
                hand = 1 if (step // 4) % 2 else -1
                tilt = heading - math.radians(90) + hand * math.radians(55) + (math.radians(-40) * hand if side < 0 else 0)
                lx, lz = nx + math.cos(tilt + math.radians(90)) * 0.55, nz + math.sin(tilt + math.radians(90)) * 0.55 - (0.25 if side < 0 else 0)
                leaf = orb(lx, lz, 0.3, glow(RED if side > 0 else GREEN, 2.4 if side > 0 else 3.0))
                leaf.scale = (0.5, 0.3, 1.5)
                leaf.rotation_euler = (0, -(tilt), 0)
                label(vote, lx + hand * 0.75, lz - 0.08, 0.17)
            px, pz = nx, nz
        orb(px, pz, 0.24 + 0.08 * max(net, 0), glow(code, 3.2))
        label(name, x - 0.6, 3.6, 0.3, GREY_LIGHT)
        label(word, x + 0.5, 3.5, 0.5, code)
    return Vector((0, 0, 0.7)), 14.0


def wave():
    """海浪：每一期一道浪，不把指標一個一個畫出來。浪往右是往上湧還是往下退＝偏多還是偏空；
    浪越碎（細紋越多）＝指標彼此越不一致；浪底下一行小字寫幾個偏多、幾個偏空。"""
    for row, (name, bulls, bears) in enumerate(PERIODS):
        z = 2.4 - row * 2.6
        net, word, code = verdict(bulls, bears)
        mixed = min(len(bulls), len(bears))
        for layer in range(3):
            previous = None
            for step in range(121):
                s = step / 120
                x = -3.6 + s * 7.6
                height = z + net * 0.22 * (s - 0.5) * 2 + math.sin(s * math.tau * 1.5 + layer) * 0.22 + math.sin(s * math.tau * (7 + layer * 2)) * 0.07 * mixed - layer * 0.22
                if previous:
                    seg = bar(((previous[0] + x) / 2, layer * 0.2, (previous[1] + height) / 2), (math.hypot(x - previous[0], height - previous[1]) + 0.01, 0.04, 0.07 - layer * 0.015),
                              glow(code, 2.6 - layer * 0.8))
                    seg.rotation_euler = (0, -math.atan2(height - previous[1], x - previous[0]), 0)
                previous = (x, height)
        label(name, -5.9, z + 0.2, 0.3, GREY_LIGHT, "LEFT")
        label(word, -5.9, z - 0.5, 0.5, code, "LEFT")
        label(f"{len(bulls)} 個偏多、{len(bears)} 個偏空", 4.3, z - 0.1, 0.2, GREY_LIGHT, "LEFT")
    return Vector((0.2, 0, 0.0)), 14.0


def planet():
    """行星：每一期一顆行星，帶一圈光環。光環往右上翹是偏多、往右下垂是偏空、平的是盤整；
    繞著它的小衛星是指標，紅的偏多、綠的偏空。"""
    for column, (name, bulls, bears) in enumerate(PERIODS):
        x = -4.4 + column * 4.4
        net, word, code = verdict(bulls, bears)
        orb(x, 0.3, 0.8, glossy("#56627a"))
        lean, open_up = math.radians(net * 11), math.radians(14)   # 光環幾乎平躺（從正面看是一個扁的橢圓），再往右上或右下傾
        bpy.ops.mesh.primitive_torus_add(location=(x, 0, 0.3), major_radius=1.6, minor_radius=0.035, major_segments=96)
        ring = bpy.context.active_object
        ring.rotation_euler = (open_up, -lean, 0)
        ring.data.materials.append(glow(code, 3.5))
        moons = [(vote, RED) for vote in bulls] + [(vote, GREEN) for vote in bears]
        for index, (vote, colour) in enumerate(moons):
            angle = math.radians(205 + index * (130 / max(len(moons) - 1, 1)))   # 排在光環靠鏡頭的這半圈
            mx, mz = math.cos(angle) * 1.6, math.sin(angle) * 1.6 * math.sin(open_up)
            px, pz = x + mx * math.cos(lean) - mz * math.sin(lean), 0.3 + mx * math.sin(lean) + mz * math.cos(lean)
            orb(px, pz, 0.17, glow(colour, 3.0), y=-1.7)
            label(vote, px, pz - 0.45, 0.17, WHITE, "CENTER", y=-1.9)
        label(name, x - 0.6, 3.2, 0.3, GREY_LIGHT)
        label(word, x + 0.5, 3.1, 0.5, code)
    return Vector((0, 0, 0.5)), 14.0


IDEA_SCENES.update({"fish": fish, "plant": plant, "wave": wave, "planet": planet})

if style in IDEA_SCENES:
    look_at, width = IDEA_SCENES[style]()
    prefix = "idea"
else:
    {"neon": neon, "liquid": liquid, "swarm": swarm, "holo": holo}[style]()
    label("短期", -1.9, 2.55, 0.34, "#9aa0ad", "LEFT")
    label("偏空", -1.05, 2.48, 0.56, GREEN, "LEFT")
    look_at, width, prefix = Vector((0.45, 0, -1.2)), 9.6, "style"

camera_data = bpy.data.cameras.new("camera")
camera_data.type = "ORTHO"
camera_data.ortho_scale = width
camera = bpy.data.objects.new("camera", camera_data)
scene.collection.objects.link(camera)
camera.rotation_euler = (math.radians(84), 0, 0)
camera.location = look_at + camera.rotation_euler.to_matrix() @ Vector((0, 0, 40))
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
scene.render.filepath = str(out / f"{prefix}-{style}.png")
bpy.ops.render.render(write_still=True)
print("saved", scene.render.filepath)
