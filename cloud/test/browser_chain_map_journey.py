"""AI 產業鏈資金版圖的瀏覽器旅程（本人 2026-11-24 核准樣品 v4，.scratch/ai-chain-map 票 01）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），開關 MARKET_ANA_CHAIN_MAP=owner。
資金數字是這裡照入庫的定義 seeds/ai-chain-map.json 產生的假資料（格式和樣品的 stock_flows.json 一樣，數字是決定性的），
經 chain_map_bootstrap.py 轉成 SQL 當種子——順便驗那支一次性的載入。不連任何來源、不碰正式站。
先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/chain-map-journey/。

    python -X utf8 cloud/test/browser_chain_map_journey.py

和樣品並排比對（選用）：CHAIN_MAP_SAMPLE_DIR 指到樣品的資料夾（裡面有 資金版圖樣品v4.html 與 stock_flows.json）時，
另外用那一份真的資料起一次伺服器，和直接打開的樣品檔在同樣的尺寸、同樣的步驟下各截一張、比對每個元素的樣式，
放在 design-previews/chain-map/（不入庫）。真的資料只在這次執行時讀，不會寫進 repo。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/chain-map-journey"
PARITY = ROOT / "design-previews/chain-map"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
DEFINITION = json.loads((ROOT / "seeds/ai-chain-map.json").read_text(encoding="utf-8"))
PHONE = {"viewport": {"width": 390, "height": 720}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
DESKTOP = {"viewport": {"width": 1280, "height": 820}}
SNAP = "() => window.ChainMap && window.ChainMap.snapshot()"


def months(first: str, last: str) -> list[str]:
    out, (y, m) = [], map(int, first.split("-"))
    while True:
        out.append(f"{y}-{m:02d}")
        if out[-1] == last:
            return out
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fixture() -> tuple[dict, str]:
    """和 stock_flows.json 同結構的假資料。定義裡最後一檔美股故意不給（剛加進名單、還沒有資料的公司要畫成沒有資料）。"""
    span = months("2021-01", "2026-10")
    n, inst0 = len(span), 58
    tw_codes = sorted({row[0] for node in DEFINITION["nodes"] for row in node["tw"]})
    us_codes = sorted({row[0] for node in DEFINITION["nodes"] for row in node["us"]})
    skipped = us_codes[-1]
    tw = {code: {"t": [round(20 + (k * 37 + i * 11) % 400 + i * (k % 5), 1) for i in range(n)],
                 "i": [round(((k * 13 + i * 7) % 41 - 20) * 1.5, 1) for i in range(n - inst0)]} for k, code in enumerate(tw_codes)}
    us = {code: {"t": [round(0.2 + ((k * 29 + i * 5) % 90) / 10 + i * 0.03, 2) for i in range(n)]}
          for k, code in enumerate(us_codes) if code != skipped}
    return {"months": span, "inst0": inst0, "notes": ["假資料：旅程測試用"], "tw": tw, "us": us}, skipped


def seed_sql(flows_path: Path, extra_users: bool = True) -> str:
    out = OUT / f"{flows_path.stem}.sql"
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "chain_map_bootstrap.py"), "--flows", str(flows_path), "--sql-output", str(out),
                    "--built-at", "2026-11-24T10:00:00Z"], check=True, cwd=ROOT)
    users = f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', 'f', 'approved', '2026-11-10T00:00:00Z');\n" if extra_users else ""
    return users + out.read_text(encoding="utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(seed: str | None) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {**os.environ, "MARKET_ANA_CHAIN_MAP": "owner"}
    if seed:
        path = OUT / f"seed-{port}.sql"
        path.write_text(seed, encoding="utf-8")
        env["JOURNEY_SEED_SQL"] = str(path)
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    for _ in range(150):
        try:
            urlopen(f"http://127.0.0.1:{port}", timeout=1); break
        except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
    return server, f"http://127.0.0.1:{port}"


class Journey:
    def __init__(self) -> None:
        self.results, self.console = [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def context(self, browser, base: str, email: str, options: dict):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": base}])
        # 外部字型在這裡連不到，不擋的話截圖會卡住
        context.route(lambda url: not url.startswith(base), lambda route: route.abort())
        return context

    def page(self, context, base: str):
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text
                and "ERR_FAILED" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{base}/chain-map.html", wait_until="domcontentloaded")
        page.wait_for_function("() => window.ChainMap && window.ChainMap.snapshot()", timeout=30_000)
        page.wait_for_timeout(1500)   # 進場：一格一格浮上來
        return page


def cells(page, kind: str) -> list[dict]:
    return [c for c in page.evaluate(SNAP)["cells"] if c["type"] == kind]


def tap_at(page, x: float, y: float, touch: bool) -> None:
    box = page.locator("#cv").bounding_box()
    px, py = box["x"] + x, box["y"] + y
    page.touchscreen.tap(px, py) if touch else page.mouse.click(px, py)
    page.wait_for_timeout(900)


def tap_cell(page, cell: dict, touch: bool, head: float | None = None) -> None:
    tap_at(page, cell["x"] + cell["w"] / 2, cell["y"] + (head if head is not None else cell["h"] / 2), touch)


def biggest(items: list[dict], key: str) -> dict:
    return max(items, key=lambda c: (c["v"] is not None, abs(c["v"] or 0), c[key]))


def no_hscroll(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


def owner_journey(j: Journey, browser, base: str, prefix: str, options: dict, skipped_us: str) -> None:
    touch, wide = bool(options.get("has_touch")), not options.get("has_touch")
    context = j.context(browser, base, OWNER, options)
    page = j.page(context, base)
    snap = page.evaluate(SNAP)
    layers = cells(page, "L")
    j.check(f"{prefix}全景：每一層一格（照定義有幾層就幾格），月份停在資料的最後一個月", snap["level"] == 0 and len(layers) == len(DEFINITION["layers"])
            and snap["month"] == "2026-10" and page.locator("#month").inner_text() == "2026-10", [snap["level"], len(layers), snap["month"]])
    j.check(f"{prefix}全景：高度照占比（最大的一層最高）", max(layers, key=lambda c: c["h"])["id"] == biggest(layers, "h")["id"])
    j.check(f"{prefix}沒有橫向捲動（全景）", no_hscroll(page))
    page.screenshot(path=str(OUT / f"{prefix}1-overview.png"))

    layer = biggest(layers, "h")
    tap_cell(page, layer, touch)
    snap = page.evaluate(SNAP)
    nodes = cells(page, "N")
    expected_nodes = [n["id"] for n in DEFINITION["nodes"] if n["layer"] == layer["id"]]
    j.check(f"{prefix}點一層：切成這一層的環節", snap["level"] == 1 and snap["row"] >= 0 and [c["id"] for c in nodes] == expected_nodes,
            [snap["level"], [c["id"] for c in nodes], expected_nodes])
    page.screenshot(path=str(OUT / f"{prefix}2-layer.png"))

    node = biggest(nodes, "w")
    tap_cell(page, node, touch)
    snap = page.evaluate(SNAP)
    stocks = cells(page, "S")
    definition_node = next(n for n in DEFINITION["nodes"] if n["id"] == node["id"])
    j.check(f"{prefix}點一個環節：切成這個環節的台股公司", snap["level"] == 2 and snap["node"] == node["id"]
            and [c["id"] for c in stocks] == [row[0] for row in definition_node["tw"]], [snap["node"], [c["id"] for c in stocks]])
    page.screenshot(path=str(OUT / f"{prefix}3-segment.png"))

    stock = biggest([c for c in stocks if c["v"]], "h")
    tap_cell(page, stock, touch)
    snap = page.evaluate(SNAP)
    name = page.locator("#dName").inner_text()
    shown = page.locator("#sheet").get_attribute("data-state") in ("peek", "open") if touch else page.locator("#slot.on").count() == 1
    j.check(f"{prefix}點一家公司：{'說明板從月份帶上方浮起' if touch else '右欄出現說明'}，寫這家公司", snap["level"] == 3 and snap["stock"] == stock["id"]
            and name.startswith(stock["id"] + " ") and shown, [snap["level"], name, shown])
    page.screenshot(path=str(OUT / f"{prefix}4-company.png"))

    if wide:
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
        j.check(f"{prefix}Esc：收合一層（回到環節）", page.evaluate(SNAP)["level"] == 2)
    else:
        page.locator("#dClose").tap()
        page.wait_for_timeout(700)
        j.check(f"{prefix}說明板的關閉鍵：收合一層（回到環節）", page.evaluate(SNAP)["level"] == 2)
    open_node = next(c for c in cells(page, "N") if c["open"])
    tap_cell(page, open_node, touch, head=13)
    j.check(f"{prefix}點展開那一格（環節）的標題列：收合一層", page.evaluate(SNAP)["level"] == 1)
    open_layer = next(c for c in cells(page, "L") if c["open"])
    tap_cell(page, open_layer, touch, head=15)
    j.check(f"{prefix}點展開那一層的標題列：回到全景", page.evaluate(SNAP)["level"] == 0)

    # 三種指標
    for metric, label in (("inst", "台股法人"), ("us", "美股成交"), ("tw", "台股成交")):
        button = page.locator(f'#seg button[data-m="{metric}"]')
        button.tap() if touch else button.click()
        page.wait_for_timeout(700)
        snap = page.evaluate(SNAP)
        j.check(f"{prefix}切到「{label}」", snap["metric"] == metric and button.get_attribute("aria-selected") == "true"
                and len(cells(page, "L")) == len(DEFINITION["layers"]), snap["metric"])
        if metric == "inst":
            page.screenshot(path=str(OUT / f"{prefix}5-inst.png"))
        if metric == "us":
            # 定義裡有、資料裡沒有的美股（例如改了代號、還沒重建的那一檔）：畫成沒有資料，不會壞
            owner_node = next(n for n in DEFINITION["nodes"] if any(r[0] == skipped_us for r in n["us"]))
            j.check(f"{prefix}美股：資料裡沒有的 {skipped_us} 沒讓畫面壞掉（{owner_node['id']} 那一格照樣在）",
                    any(c["id"] == owner_node["layer"] for c in cells(page, "L")))

    # 月份帶：拖、播放
    track = page.locator("#track").bounding_box()
    y = track["y"] + 20
    before = page.evaluate(SNAP)["mi"]
    if touch:
        cdp = context.new_cdp_session(page)
        x0, x1 = track["x"] + track["width"] - 14, track["x"] + track["width"] * 0.3
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": round(x0), "y": round(y)}]})
        for step in range(1, 13):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": round(x0 + (x1 - x0) * step / 12), "y": round(y)}]})
            page.wait_for_timeout(16)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        cdp.detach()
    else:
        page.mouse.move(track["x"] + track["width"] - 14, y)
        page.mouse.down()
        for step in range(1, 13):
            page.mouse.move(track["x"] + track["width"] - 14 - (track["width"] * 0.7) * step / 12, y)
            page.wait_for_timeout(16)
        page.mouse.up()
    page.wait_for_timeout(700)
    dragged = page.evaluate(SNAP)
    j.check(f"{prefix}拖月份帶：回到比較早的月份，月份字跟著換", dragged["mi"] < before - 10 and page.locator("#month").inner_text() == dragged["month"]
            and not dragged["scrubbing"], [before, dragged["mi"], dragged["month"]])
    play = page.locator("#play")
    play.tap() if touch else play.click()
    page.wait_for_timeout(1300)
    playing = page.evaluate(SNAP)
    j.check(f"{prefix}播放：月份往前走", playing["playing"] and playing["mi"] > dragged["mi"], [dragged["mi"], playing["mi"]])
    play.tap() if touch else play.click()
    page.wait_for_timeout(400)
    j.check(f"{prefix}再按一次：停下", not page.evaluate(SNAP)["playing"])

    # 搜尋：名稱、代號
    target = next(row for row in definition_node["tw"])
    target_name = DEFINITION["tw"][target[0]]
    for query, label in ((target_name, "名稱"), (target[0], "代號")):
        if touch and not page.locator("#q").is_visible():
            page.locator("#searchKey").tap()
            page.wait_for_timeout(300)
        page.locator("#q").fill(query)
        page.wait_for_timeout(300)
        result = page.locator("#results button").filter(has_text=f"{target[0]} {target_name}").first
        result.tap() if touch else result.click()
        page.wait_for_timeout(1000)
        snap = page.evaluate(SNAP)
        j.check(f"{prefix}用{label}搜尋「{query}」：直接展開到那家公司", snap["level"] == 3 and snap["stock"] == target[0] and snap["metric"] == "tw", [snap["level"], snap["stock"]])
        page.keyboard.press("Escape") if wide else page.locator("#dClose").tap()
        page.wait_for_timeout(500)
    j.check(f"{prefix}整段操作之後沒有橫向捲動", no_hscroll(page))
    context.close()


def friend_and_gate(j: Journey, browser, base: str) -> None:
    context = j.context(browser, base, FRIEND, DESKTOP)
    page = context.new_page()
    response = page.goto(f"{base}/chain-map.html")
    j.check("朋友：那一頁是 404（和不存在一樣）", response.status == 404)
    for path in ("/chain-map.js", "/chain-map.css"):
        j.check(f"朋友：{path} 是 404", page.request.get(f"{base}{path}").status == 404)
    direct = page.evaluate("async () => { const r = await fetch('/api/chain-map'); return [r.status, await r.json()]; }")
    j.check("朋友直接問資料入口：拿到「沒有」", direct == [200, {"available": False}], direct)
    context.close()


def empty_journey(j: Journey, browser) -> None:
    server, base = start_server(None)
    try:
        for prefix, options in (("phone-", PHONE), ("desktop-", DESKTOP)):
            context = j.context(browser, base, OWNER, options)
            page = context.new_page()
            page.goto(f"{base}/chain-map.html")
            page.locator(".cm-empty").wait_for(timeout=20_000)
            j.check(f"{prefix}還沒有資料：畫面那一格寫「尚未就緒」，月份帶與指標都不出現",
                    page.locator(".cm-empty").inner_text() == "資金版圖尚未就緒：還沒有已建立的月資料。"
                    and not page.locator("#dock").is_visible() and not page.locator("#seg").is_visible() and no_hscroll(page))
            page.screenshot(path=str(OUT / f"{prefix}0-empty.png"))
            context.close()
    finally:
        server.terminate()


# ---------- 和樣品並排（選用） ----------
STYLE_DUMP = """() => {
  const props = ['color', 'background-color', 'background-image', 'font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'border-radius', 'box-shadow', 'display', 'position', 'width', 'height', 'overflow-x', 'min-height', 'min-width', 'opacity', 'transform', 'transition'];
  const out = {};
  const walk = (el, path) => {
    const cs = getComputedStyle(el);
    out[path] = Object.fromEntries(props.map(p => [p, cs.getPropertyValue(p)]));
    [...el.children].filter(c => !['SCRIPT', 'LINK', 'STYLE', 'META', 'TITLE', 'HEAD'].includes(c.tagName))
      .forEach((c, i) => walk(c, path + '>' + c.tagName.toLowerCase() + (c.id ? '#' + c.id : '') + ':' + i));
  };
  walk(document.documentElement, 'html');
  return out;
}"""


FONT_FALLBACK = {   # 樣品的值 → 本站的值
    '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif': '"Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif',
    '"JetBrains Mono", ui-monospace, Consolas, monospace': '"JetBrains Mono", Consolas, monospace',
}


def parity(j: Journey, browser, sample_dir: Path) -> None:
    PARITY.mkdir(parents=True, exist_ok=True)
    flows = OUT / "real-flows-copy-not-tracked.json"
    flows.write_text((sample_dir / "stock_flows.json").read_text(encoding="utf-8"), encoding="utf-8")   # .tmp 不入庫
    server, base = start_server(seed_sql(flows, extra_users=False))
    sample_url = (sample_dir / "資金版圖樣品v4.html").as_uri()
    try:
        for prefix, options in (("390x720-", PHONE), ("1280x820-", DESKTOP)):
            ours_ctx = j.context(browser, base, OWNER, options)
            ours = j.page(ours_ctx, base)
            sample_ctx = browser.new_context(**options)
            sample_ctx.route(lambda url: url.startswith("http"), lambda route: route.abort())
            sample = sample_ctx.new_page()
            sample.goto(sample_url)
            sample.wait_for_timeout(2000)
            ours.wait_for_timeout(500)
            for step in ("overview", "layer", "segment"):
                if step != "overview":
                    kind = "L" if step == "layer" else "N"
                    cell = biggest(cells(ours, kind), "h" if kind == "L" else "w")
                    for page in (ours, sample):
                        tap_cell(page, cell, bool(options.get("has_touch")))
                    ours.wait_for_timeout(800); sample.wait_for_timeout(800)
                a, b = PARITY / f"{prefix}{step}-site.png", PARITY / f"{prefix}{step}-sample.png"
                ours.screenshot(path=str(a)); sample.screenshot(path=str(b))
                left, right = ours.evaluate(STYLE_DUMP), sample.evaluate(STYLE_DUMP)
                diffs = [(path, p, left[path][p], right[path].get(p)) for path in right if path in left for p in right[path] if left[path][p] != right[path][p]]
                # 唯一已知、刻意留下的差別：--sans／--mono 改吃本站的值，備用字型清單少了樣品多寫的 PingFang TC、ui-monospace
                # （第一順位的 Noto Sans TC／JetBrains Mono 一樣，網路字型載得到時畫出來一樣）
                expected = [d for d in diffs if d[1] == "font-family" and FONT_FALLBACK.get(d[3]) == d[2]]
                diffs = [d for d in diffs if d not in expected]
                missing = [path for path in right if path not in left]
                (PARITY / f"{prefix}{step}-style-diff.json").write_text(json.dumps({"diffs": diffs, "missing": missing}, ensure_ascii=False, indent=1), encoding="utf-8")
                same = a.read_bytes() == b.read_bytes()
                try:
                    from PIL import Image, ImageChops
                    delta = ImageChops.difference(Image.open(a).convert("RGB"), Image.open(b).convert("RGB"))
                    box = delta.getbbox()
                    changed = sum(1 for px in getattr(delta, "get_flattened_data", delta.getdata)() if max(px) > 16)
                    pixel = f"差異範圍 {box}，差超過 16 的像素 {changed}"
                except ImportError:
                    pixel = "（沒有 PIL，未算像素差）"
                print(f"  parity {prefix}{step}: 截圖{'完全相同' if same else '不同'}；{pixel}；樣式不同 {len(diffs)} 項（另有備用字型清單 {len(expected)} 項）、少了 {len(missing)} 個元素")
                j.check(f"並排 {prefix}{step}：每個元素的樣式和樣品一樣", not diffs and not missing, diffs[:6] + missing[:3])
            ours_ctx.close(); sample_ctx.close()
    finally:
        server.terminate()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    flows, skipped = fixture()
    flows_path = OUT / "synthetic-flows.json"
    flows_path.write_text(json.dumps(flows), encoding="utf-8")
    server, base = start_server(seed_sql(flows_path))
    j = Journey()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for prefix, options in (("phone-", PHONE), ("desktop-", DESKTOP)):
                owner_journey(j, browser, base, prefix, options, skipped)
            friend_and_gate(j, browser, base)
            empty_journey(j, browser)
            if os.environ.get("CHAIN_MAP_SAMPLE_DIR"):
                parity(j, browser, Path(os.environ["CHAIN_MAP_SAMPLE_DIR"]))
            browser.close()
        errors = [text for text in j.console if "favicon" not in text]
        j.check("沒有程式錯誤", not errors, errors[:5])
    finally:
        server.terminate()
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
