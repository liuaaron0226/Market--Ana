"""首頁直白分析與籌碼卡明細的瀏覽器旅程（本人 2026-11-20「不要只說明事件，要講影響＋該做的準備」；.scratch/impact-watch 票 07）。

看的是：
- 首頁那張直白分析卡照舊：一句結論；手機上只有那一句，高度和舊版資料時一樣（沒有變長）；
- 點開的全文：結論下面是整體的「影響／要看／準備」，六點各自有發生什麼與影響、準備；排法和首頁消息同一種
  （.news-points：左邊小標、右邊內文）；沒有「要擔心／什麼時候比較安心」；沒有買賣指令字眼；
- 每一張籌碼卡點開的明細都有這張卡的影響與準備；跟外資有關的條件只放在外資的兩張卡；
- 模型沒寫成、改用程式套句的那一天：有影響與準備、沒有「要看」的空段落，小字說明是自動整理；
- 舊版資料（主資料夾現在那份人寫的，只有要擔心／什麼時候比較安心）：照舊顯示，不出現新的那一塊；
- 手機 390 與桌機 1440 都不橫向捲動、沒有程式錯誤。

三種資料各建一次站（cloud/build.mjs，MARKET_DATA_DIR 指到這裡組的資料夾），用真正的 gate.handle＋cloud/dist
（cloud/test/journey-server.mjs）。新寫法那兩份由 plain_daily.produce 產生：模型是這裡的假回答，不叫 Gemini、不碰正式站。
大盤資料讀主資料夾的 web/*-data.js（worktree 沒有時往上找；也可以用 PLAIN_JOURNEY_DATA 指定）。截圖放 .tmp/plain-journey/。

    python cloud/test/browser_plain_journey.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import impact_watch  # noqa: E402
import plain_daily  # noqa: E402

OUT = ROOT / ".tmp/plain-journey"
OWNER = "owner@example.com"
DATA_FILES = ("analysis-data.js", "chips-data.js", "detail-data.js", "dashboard-data.js", "plain-data.js")
ORDER_WORDS = ("買進", "賣出", "加碼", "減碼", "停損", "進場", "出場")
CHIPS = ("foreign", "futures", "margin", "short", "trust", "volume")


def data_source() -> Path:
    for folder in (os.environ.get("PLAIN_JOURNEY_DATA"), ROOT / "web", *(parent / "web" for parent in ROOT.parents[:4])):
        if folder and (Path(folder) / "analysis-data.js").is_file() and (Path(folder) / "chips-data.js").is_file():
            return Path(folder)
    raise SystemExit("找不到大盤資料（web/analysis-data.js）：在主資料夾跑，或用 PLAIN_JOURNEY_DATA 指到有資料的 web 資料夾")


def fake_answer(bundle, watchable) -> dict:
    """一份照規則寫的假回答：自由文字沒有數字與方向字眼（真資料哪一天都通過核對），數字都用一定存在的代號。"""
    ids = [row["id"] for row in watchable]
    picked = [*[key for key in ids if key.startswith("foreign_")][:1], *[key for key in ids if not key.startswith("foreign_")][:1]]
    return {
        "data_date": bundle["data_dates"]["price_and_foreign"],
        "one_line": "籌碼還沒有站到同一邊；{foreign_today}，{close_in_range}。",
        "points": [
            {"key": "foreign", "title": "現貨與期貨要一起看", "lines": ["{foreign_today}，{foreign_20d}。", "{futures_net}，{futures_week}。"],
             "effect": "外資的方向最先反映在權值股，指數跟著動。", "prepare": "如果外資現貨的方向反過來，就先確認期貨部位有沒有跟著轉向。"},
            {"key": "trust", "title": "力道比外資小", "lines": ["{trust_5d}，{trust_today}。"],
             "effect": "投信的金額比外資小，影響集中在中小型股。", "prepare": "如果投信的方向反過來，就先確認外資有沒有同步。"},
            {"key": "margin", "title": "看槓桿有沒有降", "lines": ["{margin_balance}，{margin_change}。"],
             "effect": "融資代表借錢的部位，行情轉弱時這些部位最先有壓力。", "prepare": "如果融資餘額明顯變化，就先檢查手上有沒有用融資的部位。"},
            {"key": "short", "title": "放空的部位", "lines": ["{short_units}，{short_change}。"],
             "effect": "融券之後都要買回，數量多的時候反彈會比較急。", "prepare": ""},
            {"key": "position", "title": "看均線的位置", "lines": ["今天{close}，{index_change}，{close_in_range}。"],
             "effect": "指數相對均線的位置，決定反彈時賣壓大不大。", "prepare": "如果收盤跌破{day_low}，就先檢查手上持股的支撐在哪裡。"},
            {"key": "volume", "title": "看量能有沒有跟上", "lines": ["{amount}，{amount_ratio}。"],
             "effect": "量能代表資金願不願意追，沒有量的行情走不遠。", "prepare": ""},
        ],
        "effect": "外資的方向還是關鍵，權值股與指數短線跟著外資的動作走。",
        "prepare": ["如果收盤跌破{day_low}，就先檢查手上持股的支撐在哪裡。", "如果外資現貨的方向反過來，就先確認期貨部位有沒有跟著轉向。"],
        "track": [{"id": key, "why": "這個條件出現，短線的方向就比較清楚。"} for key in picked],
    }


def make_plain(source: Path, scenario: str) -> dict:
    analysis = impact_watch.read_assignment(source / "analysis-data.js")
    chips = impact_watch.read_assignment(source / "chips-data.js")
    if scenario == "old":
        return impact_watch.read_assignment(source / "plain-data.js")

    def ask(prompt, schema):
        if scenario == "template":
            return "model_failed", None, "旅程：假裝模型沒有回來"
        return "ok", fake_answer(plain_daily.build_facts(analysis, chips), plain_daily.watchable_conditions(analysis)), ""

    plain, audit = plain_daily.produce(analysis, chips, mode="on", ask_model=ask)
    wanted = "template" if scenario == "template" else "ai"
    if plain is None or plain["source"] != wanted:
        raise SystemExit(f"旅程的假回答沒有產生 {wanted} 的白話分析：{json.dumps(audit, ensure_ascii=False)[:800]}")
    return plain


def build(source: Path, scenario: str) -> dict:
    folder = OUT / f"data-{scenario}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copyfile(source / name, folder / name)
    plain = make_plain(source, scenario)
    plain_daily.write_plain(folder / "plain-data.js", plain)
    done = subprocess.run(["node", "build.mjs"], cwd=ROOT / "cloud", env={**os.environ, "MARKET_DATA_DIR": str(folder)},
                          capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise SystemExit(f"cloud/build.mjs 失敗（{scenario}）：\n{done.stdout[-600:]}\n{done.stderr[-600:]}")
    return plain


def serve():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("MARKET_ANA_") and key != "JOURNEY_SEED_SQL"}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urlopen(base, timeout=1)
            break
        except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
    return server, base


PANEL = """() => {
  const body = document.getElementById('panelBody');
  const text = node => node.innerText.replace(/\\s+/g, ' ').trim();
  const block = dl => dl ? { labels: [...dl.querySelectorAll('dt')].map(text), lines: [...dl.querySelectorAll('dd')].map(dd => [...dd.querySelectorAll('p')].map(text)),
    display: getComputedStyle(dl).display, columns: getComputedStyle(dl).gridTemplateColumns.split(' ').length,
    labelSize: dl.querySelector('dt') ? getComputedStyle(dl.querySelector('dt')).fontSize : null,
    labelLeft: dl.querySelector('dt') && dl.querySelector('dd') ? dl.querySelector('dt').getBoundingClientRect().right <= dl.querySelector('dd').getBoundingClientRect().left + 1 : null,
    inside: dl.getBoundingClientRect().right <= innerWidth + 1 } : null;
  const top = body.querySelector(':scope > .news-points');
  return {
    line: body.querySelector('.read-line.big') ? text(body.querySelector('.read-line.big')) : null,
    top: block(top), topAfterLine: Boolean(top && top.previousElementSibling && top.previousElementSibling.classList.contains('read-line')),
    groups: [...body.querySelectorAll('.plain-group')].map(group => ({ title: text(group.querySelector('h4')),
      lines: [...group.querySelectorAll('ul li')].map(text), points: block(group.querySelector('.news-points')) })),
    chip: block(body.querySelector('.chip-points .news-points')), chipLines: [...body.querySelectorAll('.chip-points ul li')].map(text),
    chipWatch: body.querySelector('.chip-watch') ? text(body.querySelector('.chip-watch')) : null,
    note: body.querySelector('.plain-note') ? text(body.querySelector('.plain-note')) : null,
    text: text(body), noScroll: document.documentElement.scrollWidth <= innerWidth,
  };
}"""

HOME = """() => {
  const card = document.getElementById('readCard');
  const shown = node => Boolean(node && node.offsetParent !== null && getComputedStyle(node).display !== 'none');
  return { line: document.getElementById('readLine').innerText.trim(), list: shown(document.getElementById('readList')),
    date: shown(document.getElementById('readDate')) ? document.getElementById('readDate').innerText.trim() : '',
    height: Math.round(card.getBoundingClientRect().height), hidden: card.hidden,
    noScroll: document.documentElement.scrollWidth <= innerWidth };
}"""


class Journey:
    def __init__(self) -> None:
        self.results, self.console, self.heights = [], [], {}

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, base: str, options: dict):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": OWNER, "url": base}])
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{base}/minimal.html", wait_until="domcontentloaded")   # 不等外部字型（卡住時 load 不會來）
        page.locator("#currentPrice").wait_for(state="visible", timeout=60_000)
        page.wait_for_function("() => document.getElementById('readLine').textContent.trim() !== ''", timeout=60_000)
        page.wait_for_timeout(800)
        return context, page

    def open_plain(self, page) -> dict:
        page.locator("#readCard").scroll_into_view_if_needed()
        page.locator("#readCard").click()
        page.locator("#panelBody .read-line.big").wait_for(state="visible", timeout=10_000)
        page.wait_for_timeout(500)
        return page.evaluate(PANEL)

    def open_chip(self, page, key: str) -> dict:
        section = page.locator('#homeCats [data-cat="chips"]')   # 籌碼卡平常收著，先按「籌碼與資金」
        if section.get_attribute("aria-expanded") != "true":
            section.click()
            page.wait_for_timeout(400)
        page.locator(f'#chipGrid [data-chip="{key}"]').scroll_into_view_if_needed()
        page.locator(f'#chipGrid [data-chip="{key}"]').click()
        page.locator("#panelBody .chip-top").wait_for(state="visible", timeout=10_000)
        page.wait_for_timeout(500)
        return page.evaluate(PANEL)

    def close(self, page) -> None:
        page.locator("#panelClose").click()
        page.locator("#panelBody .chip-top, #panelBody .read-line.big").first.wait_for(state="hidden", timeout=10_000)
        page.wait_for_timeout(300)


def walk(j: Journey, browser, base: str, scenario: str, plain: dict, prefix: str, options: dict, mobile: bool) -> None:
    label = {"ai": "新寫法", "template": "程式套句", "old": "舊版資料"}[scenario]
    tag = f"{prefix}{label}："
    new = scenario != "old"
    context, page = j.home(browser, base, options)
    home = page.evaluate(HOME)
    j.heights[prefix + scenario] = home["height"]
    j.check(f"{tag}首頁那張卡是一句結論", not home["hidden"] and home["line"] == plain["one_line"] and home["noScroll"], home)
    if mobile:
        j.check(f"{tag}手機上只有那一句（四點與小字都不顯示）", not home["list"] and home["date"] == "", home)
    page.locator("#readCard").screenshot(path=str(OUT / f"{prefix}{scenario}-1-card.png"))

    panel = j.open_plain(page)
    page.screenshot(path=str(OUT / f"{prefix}{scenario}-2-plain.png"))
    j.check(f"{tag}點開的全文第一行是同一句結論、六點都在", panel["line"] == plain["one_line"]
            and [group["title"] for group in panel["groups"]][:6] == [point["title"] for point in plain["points"]], panel["groups"][:1])
    # 舊版那份是人寫的（講外資「賣出」是在講事情），買賣字眼只查新寫法
    j.check(f"{tag}全文不橫向捲動{'、沒有買賣指令字眼' if new else ''}", panel["noScroll"] and not (new and any(word in panel["text"] for word in ORDER_WORDS)))
    if new:
        top = panel["top"]
        labels = ["影響", "觀察", "因應"] if scenario == "ai" else ["影響", "因應"]
        j.check(f"{tag}結論下面就是整體的{'／'.join(labels)}", bool(top) and panel["topAfterLine"] and top["labels"] == labels, top)
        j.check(f"{tag}內容就是資料寫的那幾句", bool(top) and top["lines"] == [[plain["effect"]], *([plain["conditions"]] if scenario == "ai" else []), plain["prepare"]],
                top and top["lines"])
        j.check(f"{tag}排法和首頁消息同一種：左邊小標、右邊內文", bool(top) and top["display"] == "grid" and top["columns"] == 2
                and top["labelSize"] == "12px" and top["labelLeft"] and top["inside"], top)
        groups = panel["groups"][:6]
        j.check(f"{tag}六點各自有影響", all(group["points"] and group["points"]["labels"][0] == "影響" for group in groups),
                [group["points"] and group["points"]["labels"] for group in groups])
        j.check(f"{tag}六點的影響與準備和資料一致、沒寫的那一段不出現",
                [(group["points"]["lines"] if group["points"] else None) for group in groups]
                == [[[point["effect"]], *([point["prepare"]] if point.get("prepare") else [])] for point in plain["points"]])
        j.check(f"{tag}準備都是「如果…就…」", all(line.startswith("如果") and "就" in line for line in plain["prepare"]), plain["prepare"])
        j.check(f"{tag}不再有舊版的「風險／轉強條件」那兩段", "要擔心" not in panel["text"] and "比較安心" not in panel["text"]
                and "轉強條件" not in panel["text"] and len(panel["groups"]) == 6)
        if scenario == "ai":
            j.check(f"{tag}AI 正常寫成：沒有小字", panel["note"] is None or "自動整理" not in panel["note"], panel["note"])
        else:
            j.check(f"{tag}沒有「觀察」的空段落，小字說明是自動整理", "觀察" not in top["labels"] and "依當天數字自動整理" in (panel["note"] or ""), panel["note"])
    else:
        j.check(f"{tag}全文照舊：風險／轉強條件（舊資料的 worry／relief）都在，沒有新的那一塊", panel["top"] is None and all(group["points"] is None for group in panel["groups"])
                and [group["title"] for group in panel["groups"]][6:] == ["風險", "轉強條件"]
                and panel["groups"][6]["lines"] == plain["worry"] and panel["groups"][7]["lines"] == plain["relief"], panel["groups"][6:])
    j.close(page)

    for key in CHIPS:
        chip = j.open_chip(page, key)
        point = next(row for row in plain["points"] if row["key"] == ("foreign" if key == "futures" else key))
        about_foreign = key in ("foreign", "futures")
        if key in ("foreign", "margin"):
            page.screenshot(path=str(OUT / f"{prefix}{scenario}-3-chip-{key}.png"))
        j.check(f"{tag}籌碼卡 {key}：發生什麼的句子照舊在", chip["chipLines"] == point["lines"] and chip["noScroll"], chip["chipLines"])
        if new:
            conditions = [text for text in plain.get("conditions") or [] if about_foreign and "外資" in text]
            expected_labels = ["影響", *(["觀察"] if conditions else []), *(["因應"] if point.get("prepare") else [])]
            expected_lines = [[point["effect"]], *([conditions] if conditions else []), *([point["prepare"]] if point.get("prepare") else [])]
            block = chip["chip"]
            j.check(f"{tag}籌碼卡 {key}：這張卡的{'／'.join(expected_labels)}", bool(block) and block["labels"] == expected_labels
                    and block["lines"] == expected_lines, block)
            j.check(f"{tag}籌碼卡 {key}：同一種排法、不超出畫面、沒有舊的「注意」", bool(block) and block["display"] == "grid" and block["labelLeft"]
                    and block["inside"] and chip["chipWatch"] is None and not any(word in chip["text"] for word in ORDER_WORDS), block)
        else:
            watch = [text for text in plain["worry"] if about_foreign and "外資" in text]
            j.check(f"{tag}籌碼卡 {key}：照舊（外資的卡有「注意」，沒有新的那一塊）", chip["chip"] is None
                    and (chip["chipWatch"] == "注意 " + " ".join(watch) if watch else chip["chipWatch"] is None), chip["chipWatch"])
        j.close(page)
    if scenario == "ai":
        page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
        j.open_plain(page)
        page.screenshot(path=str(OUT / f"{prefix}{scenario}-4-plain-dark.png"))
    context.close()


def main() -> int:
    j = Journey()
    source = data_source()
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [("desktop-1440-", {"viewport": {"width": 1440, "height": 900}}, False),
             ("mobile-390-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}, True)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scenario in ("ai", "template", "old"):
            plain = build(source, scenario)
            if scenario == "ai":
                j.check("假模型挑的條件進了要看的條件（含一個外資的）", len(plain.get("track") or []) == 2 and any("外資" in text for text in plain["conditions"]),
                        plain.get("conditions"))
            if scenario == "template":
                j.check("程式套句不產生盯項、仍有影響與準備", "track" not in plain and "conditions" not in plain and plain["effect"] and plain["prepare"])
            server, base = serve()
            try:
                for prefix, options, mobile in sizes:
                    walk(j, browser, base, scenario, plain, prefix, options, mobile)
            finally:
                server.terminate()
        browser.close()
    for prefix, _, mobile in sizes:
        if mobile:
            heights = {name: j.heights.get(prefix + name) for name in ("ai", "template", "old")}
            # 一句結論的字數不同會差一行；新寫法不能讓卡片多出別的東西
            j.check(f"{prefix}首頁那張卡沒有變長（三種資料只差結論那一句的行數）", None not in heights.values()
                    and max(heights.values()) - min(heights.values()) <= 70, heights)
    errors = [text for text in j.console if "favicon" not in text]
    j.check("沒有程式錯誤", not errors, errors[:5])
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
