"""盤勢解讀改版的瀏覽器旅程（本人 2026-11-23「共識達成」，選樣品 design-previews/plain-top3 的 ?l=b；.scratch/market-brief 票 02）。

看的是：
- 有最新一班（開關只開站主）：首頁那張卡只有一句結論；點開是一句結論＋一排類別鍵（籌碼面、技術面），停在這一班影響最大的那一類；
  按另一顆鍵換成那一類；籌碼面有外資每日買賣超與外資期貨淨空單兩張圖、技術面有加權指數與 20 日均線，圖上的數字和資料對得上，
  籌碼面的圖只畫到那一類的資料日；每一類下面是影響／觀察／因應；沒有買賣指令字眼、沒有多餘的小字；
  類別鍵高度至少 40、不折成兩排；籌碼卡點開的明細照舊；
- 同一個開關下朋友看到的和改版前一樣（不問那一支 API）；
- 開給全部、那一份四類都有、站主另有追蹤事項（票 03–05）：站主五顆鍵、朋友四顆；手機上那一排放不下就左右滑、不折行，
  按到的那一顆會捲進來；國際股市是各市場漲跌幅的橫條圖（由大到小、台股最後一列）；市場消息只有文字；
  追蹤事項一項一段（價位刻度＋因應），最多 5 項，朋友看不到那一顆、也看不到個股；
- 國際股市還沒更新、這一班沒有消息：那兩類只有一句；站主沒有追蹤事項就沒有那一顆；
- 沒有任何一班、或開關關著：卡片與面板和改版前一樣；
- 手機 390 與桌機 1440 都不橫向捲動、沒有程式錯誤。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），那一班的內容是這裡種的假資料，不叫模型、不碰正式站。
大盤資料讀 MARKET_DATA_DIR（沒設就找 web/*-data.js），這裡會先跑一次 cloud/build.mjs。截圖放 .tmp/market-brief-journey/。

    python -X utf8 cloud/test/browser_market_brief_journey.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/market-brief-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
ORDER_WORDS = ("買進", "賣出", "加碼", "減碼", "停損", "進場", "出場")
FILLER = ("更新於", "資料截至", "形成中", "依當天數字自動整理")
ONE_LINE = "指數反彈，但外資仍站在賣方、期貨空單還在高檔，這次反彈還沒有得到外資的確認。"
SAY = {"overseas": "美股費半反彈，亞股同步收紅", "news": "這一班沒有影響大盤的新消息", "chips": "指數漲，外資卻沒有跟著回補",
       "tech": "反彈了，但還在 20 日均線之下", "tracking": "追蹤的價位條件有一項成立"}
NAMES = {"overseas": "國際股市", "news": "市場消息", "chips": "籌碼面", "tech": "技術面", "tracking": "追蹤事項"}
SAY["news"] = "半導體報價消息帶動權值股"
QUIET_OVERSEAS, QUIET_NEWS = "國際股市資料尚未更新", "本時段無影響大盤的新消息"
MARKETS = [("dji", "道瓊", -0.4), ("ixic", "那斯達克", 1.4), ("spx", "標普 500", 0.8), ("sox", "費城半導體", 3.1), ("n225", "日經 225", 1.2), ("kospi", "韓國綜合", -1.9)]
OVERSEAS_TEXTS = ["各市場漲跌幅（%）", "費城半導體", "+3.1", "那斯達克", "+1.4", "日經 225", "+1.2", "標普 500", "+0.8", "道瓊", "-0.4", "韓國綜合", "-1.9", "加權指數", "+2.2"]


def data_source() -> Path:
    for folder in (os.environ.get("MARKET_DATA_DIR"), ROOT / "web", ROOT / "data/daily-update-state/last-market-pack"):
        if folder and (Path(folder) / "analysis-data.js").is_file() and (Path(folder) / "chips-data.js").is_file():
            return Path(folder)
    raise SystemExit("找不到大盤資料（analysis-data.js、chips-data.js）：用 MARKET_DATA_DIR 指到有資料的資料夾")


def read_assignment(path: Path):
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text[text.index("=") + 1:].strip().removesuffix(";").strip())


def num(value: float) -> str:
    return f"{int(value + 0.5):,}"   # 和畫面一樣四捨五入、加千分位（這裡只有正數）


def md(date: str) -> str:
    return f"{date[5:7]}/{date[8:10]}"


def expected(source: Path) -> dict:
    """圖上該出現的數字：照同一份資料在這裡另外算一次。籌碼面故意用前一個交易日當資料日（白天的班就是這樣）。"""
    chips, analysis = read_assignment(source / "chips-data.js"), read_assignment(source / "analysis-data.js")
    plain = read_assignment(source / "plain-data.js") if (source / "plain-data.js").is_file() else None
    chips_date = chips["foreign"][-2]["date"]
    foreign = [row for row in chips["foreign"] if row["date"] <= chips_date][-20:]
    futures = [row for row in chips["futures"] if row["date"] <= chips_date][-20:]
    last = foreign[-1]["net_billion"]
    streak = 0
    for row in reversed(foreign):
        if row["net_billion"] == 0 or (row["net_billion"] < 0) != (last < 0):
            break
        streak += 1
    side = "賣" if last < 0 else "買"
    short = futures[-1]["net_contracts"] <= 0
    sign = -1 if short else 1   # 畫的是淨空單（或淨多單）的口數
    size = sign * futures[-1]["net_contracts"]
    week = size - sign * futures[-6]["net_contracts"]   # 比一週（5 個交易日）前多了多少
    daily = analysis["daily"]
    tech_date = daily[-1]["date"]
    closes = [row["close"] for row in daily]
    ma = sum(closes[-20:]) / 20
    gap = (closes[-1] / ma - 1) * 100
    stale = bool(plain and plain.get("data_dates") and str(plain["data_dates"].get("price_and_foreign") or "") < analysis["meta"]["cutoff_date"])
    return {
        "chips_date": chips_date, "tech_date": tech_date, "plain": None if stale else plain, "stale": stale,
        "foreign_texts": [*([f"連 {streak} 天{side}"] if streak >= 2 else []), f"{side} {num(abs(last))}", md(foreign[0]["date"]), md(chips_date)],
        "foreign_aria": f"{md(chips_date)} {side}超 {num(abs(last))} 億",
        "futures_title": f"外資期貨淨{'空' if short else '多'}單（口）",
        "futures_texts": [num(size), "週持平" if week == 0 else f"週{'增' if week > 0 else '減'} {num(abs(week))}"],
        "index_texts": [f"均線 {num(ma)}", f"{'高於' if gap >= 0 else '低於'} {abs(gap):.1f}%", f"收 {num(closes[-1])}"],
        "index_aria": f"{md(tech_date)} 收 {num(closes[-1])}，20 日均線 {num(ma)}",
    }


def cat(key: str, date: str, quiet: bool = False) -> dict:
    if quiet:
        return {"key": key, "say": SAY[key], "data_date": date, "quiet": True}
    return {"key": key, "say": SAY[key], "effect": f"{NAMES[key]}：權值股上方仍有壓力，指數短線跟著外資的方向走。",
            "conditions": [f"{NAMES[key]}：外資有沒有由賣超轉為買超。"], "prepare": [f"如果{NAMES[key]}的方向反過來，就先檢查手上權值股的比重。"],
            "data_date": date, "quiet": False}


def brief(exp: dict, scenario: str) -> dict:
    cats = [cat("chips", exp["chips_date"]), cat("tech", exp["tech_date"]), {"key": "sectors", "say": "之後才有的類別", "data_date": exp["tech_date"]}]
    if scenario == "five":   # 那一份裡的四類都有內容；裡面叫 tracking 的不算（追蹤事項不經過那一份）
        overseas = {**cat("overseas", exp["tech_date"]), "home": {"name": "加權指數", "change_pct": 2.2, "date": exp["tech_date"]},
                    "markets": [{"key": key, "name": name, "change_pct": pct, "date": exp["chips_date"]} for key, name, pct in MARKETS]}
        cats = [cat("tracking", exp["tech_date"]), cat("tech", exp["tech_date"]), cat("news", exp["tech_date"]), cat("chips", exp["chips_date"]), overseas]
    if scenario == "quiet":   # 國際股市還沒更新、這一班沒有消息：那兩類只有一句
        cats = [{**cat("overseas", exp["tech_date"], quiet=True), "say": QUIET_OVERSEAS}, {**cat("news", exp["tech_date"], quiet=True), "say": QUIET_NEWS},
                cat("chips", exp["chips_date"]), cat("tech", exp["tech_date"])]
    return {"schema": "market-brief/v1", "trade_date": exp["tech_date"], "slot": "evening", "generated_at": f"{exp['tech_date']}T18:31:00+08:00",
            "source": "ai", "one_line": ONE_LINE, "lead": "tech", "cats": cats, "later_field": {"ignored": True}}


def q(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


# 站主的追蹤事項（票 05）：(id, 對象, 類型, 條件編號, 參數, 條件, 影響力, 狀態, 因應)。大盤的所有人可見，個股的只有站主。
WATCH_ITEMS = [
    ("s_resist", "2454", "numeric", "above_resist", {"name": "聯發科", "level": 3430, "as_of": "2026-11-18"}, "收盤站上壓力（3,430）", 2, "met", "如果跌回壓力之下，就把這一天當成單日事件。"),
    ("m_high", "market", "numeric", "above_high", {"level": 49967, "as_of": "2026-11-18"}, "加權指數收盤突破近 20 日高點（49,967）", 3, "watching", "如果突破，就先確認成交量有沒有跟上。"),
    ("m_low", "market", "numeric", "below_low", {"level": 44920, "as_of": "2026-11-18"}, "加權指數收盤跌破近 20 日低點（44,920）", 2, "watching", "如果跌破，就先檢查手上權值股的比重。"),
    ("m_news", "market", "news", "news:cbc", {"keywords": ["央行"]}, "央行公布理監事會議結果", 3, "watching", "如果升息，就先確認金融股的反應。"),
    ("m_flow", "market", "numeric", "foreign_turn_buy", {"flow_as_of": "2026-11-18"}, "外資由賣超轉為買超", 2, "watching", None),
    ("m_extra", "market", "numeric", "foreign_sell_streak", {"days": 9, "flow_as_of": "2026-11-18"}, "外資連續賣超達 9 天", 1, "watching", None),
]
# 畫面上該有的順序：有結果的在前，其餘照離價位的遠近、畫不出刻度的排後面；最多 5 項（第 6 項不出現）
TRACK_HEADS = ["聯發科 收盤站上壓力：成立", "加權指數收盤跌破近 20 日低點", "加權指數收盤突破近 20 日高點", "央行公布理監事會議結果", "外資由賣超轉為買超"]
TRACK_LEVELS = [["3,430", "收 3,685", "高於 7.4%"], ["44,920", "收 47,036", "高於 4.7%"], ["49,967", "收 47,036", "低於 5.9%"]]


def watch_sql(exp: dict) -> list[str]:
    out = []
    for subject, close in (("market", 47036), ("2454", 3685)):
        facts = {"date": exp["tech_date"], "close": close, "ma": {"20": close}}
        out.append("INSERT INTO impact_watch_facts (subject, data_date, payload, updated_at) VALUES ("
                   + ", ".join(q(value) for value in (subject, exp["tech_date"], json.dumps(facts), "2026-11-20T07:00:00Z")) + ");")
    for ident, subject, kind, key, params, label, impact, status, prepare in WATCH_ITEMS:
        met = status == "met"
        row = (ident, "market_news", "2026-11-18", subject, kind, key, json.dumps(params, ensure_ascii=False), label, impact,
               json.dumps([prepare], ensure_ascii=False) if prepare else None, "2026-11-18", "2099-01-01", None if met else 12, status,
               exp["tech_date"] if met else None, "成立：收 3,685，站上壓力 3,430。" if met else None, "2099-01-01" if met else None,
               "all" if subject == "market" else "owner", "2026-11-18T06:05:00Z")
        out.append("INSERT INTO impact_watch_items (id, source_block, source_date, subject, kind, condition_key, params, label, impact, prepare, created_date, "
                   "expires_on, days_left, status, result_date, result_note, show_until, visibility, created_at) VALUES ("
                   + ", ".join(str(value) if isinstance(value, int) else q(value) for value in row) + ");")
    return out


def seed_sql(exp: dict, scenario: str) -> str:
    out = [f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', '朋友', 'approved', '2026-11-09T00:00:00Z');"]
    if scenario == "none":
        return "\n".join(out)
    if scenario == "five":
        out += watch_sql(exp)
    older = {**brief(exp, scenario), "one_line": "前一班的結論，不該出現"}
    rows = [("2000-01-03", "evening", older), (exp["tech_date"], "morning", older), (exp["tech_date"], "evening", brief(exp, scenario))]
    out += ["INSERT INTO market_brief_runs (trade_date, slot, generated_at, source, model, payload) VALUES ("
            + ", ".join(q(value) for value in (date, slot, f"{date}T10:31:00Z", "ai", "journey", json.dumps(payload, ensure_ascii=False))) + ");"
            for date, slot, payload in rows]
    return "\n".join(out)


def build(source: Path) -> None:
    done = subprocess.run(["node", "build.mjs"], cwd=ROOT / "cloud", env={**os.environ, "MARKET_DATA_DIR": str(source)},
                          capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise SystemExit(f"cloud/build.mjs 失敗：\n{done.stdout[-600:]}\n{done.stderr[-600:]}")


def serve(exp: dict, scenario: str, mode: str | None, watch: str | None = None):
    """mode：盤勢解讀的開關；watch：追蹤事項（盯項）的開關。"""
    seed = OUT / f"seed-{scenario}.sql"
    seed.write_text(seed_sql(exp, scenario), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("MARKET_ANA_")}
    env["JOURNEY_SEED_SQL"] = str(seed)
    if mode:
        env["MARKET_ANA_MARKET_BRIEF"] = mode
    if watch:
        env["MARKET_ANA_IMPACT_WATCH"] = watch
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


HOME = """() => {
  const card = document.getElementById('readCard');
  return { line: document.getElementById('readLine').innerText.trim(), list: document.getElementById('readList').children.length,
    date: document.getElementById('readDate').textContent.trim(), hidden: card.hidden,
    noScroll: document.documentElement.scrollWidth <= innerWidth };
}"""

PANEL = """() => {
  const body = document.getElementById('panelBody');
  const text = node => node.innerText.replace(/\\s+/g, ' ').trim();
  const strip = body.querySelector('.plain-tabs');
  const buttons = strip ? [...strip.querySelectorAll('button[role="radio"]')] : [];
  const boxes = buttons.map(button => button.getBoundingClientRect());
  const tab = body.querySelector('#plainTab');
  const points = tab && tab.querySelector('.news-points');
  const bodyBox = body.getBoundingClientRect();
  return {
    title: document.getElementById('panelTitle').textContent.trim(),
    line: body.querySelector('.read-line.big') ? text(body.querySelector('.read-line.big')) : null,
    tabs: Boolean(strip), group: strip ? strip.querySelector('.chip-seg').getAttribute('role') : null,
    keys: buttons.map(button => button.textContent.trim()), checked: buttons.filter(button => button.getAttribute('aria-checked') === 'true').map(button => button.textContent.trim()),
    keyHeights: boxes.map(box => Math.round(box.height)), oneRow: boxes.every(box => Math.abs(box.top - boxes[0].top) < 1),
    stripScrolls: strip ? strip.scrollWidth > strip.clientWidth + 1 : false, stripOverflow: strip ? getComputedStyle(strip).overflowX : null,
    stripInside: strip ? strip.getBoundingClientRect().right <= innerWidth + 1 : null,
    checkedInView: boxes.filter((box, i) => buttons[i].getAttribute('aria-checked') === 'true')
      .every(box => box.left >= strip.getBoundingClientRect().left - 1 && box.right <= strip.getBoundingClientRect().right + 1),
    say: tab && tab.querySelector('.plain-tab-say') ? text(tab.querySelector('.plain-tab-say')) : null,
    heads: tab ? [...tab.querySelectorAll('.plain-tab-say')].map(text) : [], tracks: tab ? tab.querySelectorAll('.plain-track').length : 0,
    allLabels: tab ? [...tab.querySelectorAll('.news-points dt')].map(text) : [], allLines: tab ? [...tab.querySelectorAll('.news-points dd p')].map(text) : [],
    stripLeft: strip ? Math.round(strip.scrollLeft) : 0,
    charts: tab ? [...tab.querySelectorAll('svg.plain-chart')].map(svg => ({ name: svg.dataset.chart, role: svg.getAttribute('role'), label: svg.getAttribute('aria-label'),
      texts: [...svg.querySelectorAll('text')].map(node => node.textContent), marks: svg.querySelectorAll('rect, path, circle').length,
      // 圖上的字有沒有互相疊到、有沒有蓋到橫條（橫條圖與刻度圖才看）
      clash: (() => {
        const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
        const words = [...svg.querySelectorAll('text')].map(node => node.getBoundingClientRect());
        const blocks = [...svg.querySelectorAll('rect')].map(node => node.getBoundingClientRect());
        return words.some((box, i) => words.slice(i + 1).some(other => hit(box, other)) || blocks.some(block => hit(box, block)));
      })(),
      bars: [...svg.querySelectorAll('rect')].map(node => ({ fill: node.getAttribute('fill'), opacity: node.getAttribute('opacity') })),
      bold: [...svg.querySelectorAll('text')].filter(node => Number(getComputedStyle(node).fontWeight) >= 600).map(node => node.textContent),
      dots: [...svg.querySelectorAll('circle')].map(node => node.getAttribute('fill')),
      width: Math.round(svg.getBoundingClientRect().width), height: Math.round(svg.getBoundingClientRect().height),
      inside: svg.getBoundingClientRect().left >= bodyBox.left - 1 && svg.getBoundingClientRect().right <= bodyBox.right + 1,
      legend: svg.querySelectorAll('title, desc, .legend').length })) : [],
    labels: points ? [...points.querySelectorAll('dt')].map(text) : [], lines: points ? [...points.querySelectorAll('dd p')].map(text) : [],
    oldGroups: body.querySelectorAll('section.plain-group').length, small: body.querySelectorAll('small, .read-date, .plain-note, time').length,
    chipTop: Boolean(body.querySelector('.chip-top')), chipBig: Boolean(body.querySelector('.chip-big')), chipLines: [...body.querySelectorAll('.chip-points ul li')].map(text),
    text: text(body), noScroll: document.documentElement.scrollWidth <= innerWidth,
  };
}"""


def fonts_reachable() -> bool:
    """標題用的外部字型（Noto Serif TC）現在抓不抓得到。抓不到就整趟不去抓：不然截圖會一直等字型。"""
    try:
        urlopen("https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@500;600&display=swap", timeout=4).read()
        return True
    except Exception:  # noqa: BLE001 連不上、逾時都算抓不到
        return False


class Journey:
    def __init__(self) -> None:
        self.results, self.console = [], []
        self.fonts = fonts_reachable()

    def shot(self, target, name: str) -> None:
        """截圖只是給人看的：字型下載到一半卡住時不讓整趟旅程停在這裡。"""
        try:
            target.screenshot(path=str(OUT / name), timeout=15_000)
        except Exception as error:  # noqa: BLE001
            print(f"SKIP 截圖 {name}：{str(error).splitlines()[0]}")

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, base: str, email: str, options: dict, line: str | None):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": base}])
        if not self.fonts:
            context.route(lambda url: "fonts.googleapis.com" in url or "fonts.gstatic.com" in url, lambda route: route.abort())
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        asked = []
        page.on("request", lambda r: asked.append(r.url) if "/api/market-brief" in r.url else None)
        page.goto(f"{base}/minimal.html", wait_until="domcontentloaded")   # 不等外部字型（卡住時 load 不會來）
        page.locator("#currentPrice").wait_for(state="visible", timeout=60_000)
        if line:
            page.wait_for_function("line => document.getElementById('readLine').textContent.trim() === line", arg=line, timeout=60_000)
        page.wait_for_timeout(1200)
        return context, page, asked

    def open_card(self, page) -> dict:
        page.locator("#readCard").scroll_into_view_if_needed()
        page.locator("#readCard").click()
        page.locator("#panelBody .read-line.big").wait_for(state="visible", timeout=10_000)
        page.wait_for_timeout(500)
        return page.evaluate(PANEL)

    def pick(self, page, name: str) -> dict:
        page.locator("#panelBody .plain-tabs button", has_text=name).click()
        page.wait_for_timeout(350)
        return page.evaluate(PANEL)

    def close(self, page) -> None:
        page.locator("#panelClose").click()
        page.locator("#panelBody .chip-top, #panelBody .read-line.big").first.wait_for(state="hidden", timeout=10_000)
        page.wait_for_timeout(300)


def chart(panel: dict, name: str) -> dict | None:
    return next((item for item in panel["charts"] if item["name"] == name), None)


def clean(j: Journey, tag: str, panel: dict) -> None:
    j.check(f"{tag}沒有買賣指令字眼、沒有多餘的小字、不橫向捲動", panel["noScroll"] and panel["small"] == 0
            and not any(word in panel["text"] for word in (*ORDER_WORDS, *FILLER)), panel["text"][:200])


def with_brief(j: Journey, browser, base: str, exp: dict, prefix: str, options: dict, mobile: bool) -> None:
    tag = f"{prefix}有最新一班："
    context, page, asked = j.home(browser, base, OWNER, options, ONE_LINE)
    home = page.evaluate(HOME)
    j.check(f"{tag}首頁那張卡只有一句結論（最新一班的）", not home["hidden"] and home["line"] == ONE_LINE and home["list"] == 0 and home["date"] == ""
            and home["noScroll"] and len(asked) == 1, (home, asked))
    j.shot(page.locator("#readCard"), f"{prefix}1-card.png")

    panel = j.open_card(page)
    j.check(f"{tag}點開：一句結論＋一排類別鍵，停在這一班影響最大的那一類", panel["title"] == "盤勢解讀" and panel["line"] == ONE_LINE
            and panel["group"] == "radiogroup" and panel["keys"] == ["籌碼面", "技術面"] and panel["checked"] == ["技術面"] and panel["say"] == SAY["tech"], panel)
    j.check(f"{tag}類別鍵高度至少 40、同一排、不超出畫面", all(height >= 40 for height in panel["keyHeights"]) and panel["oneRow"] and panel["stripInside"], panel["keyHeights"])
    index = chart(panel, "index")
    j.check(f"{tag}技術面只有一張圖：加權指數與 20 日均線，尾端標均線、差距、收盤", len(panel["charts"]) == 1 and bool(index)
            and index["texts"] == ["加權指數與 20 日均線", *exp["index_texts"]], panel["charts"])
    j.check(f"{tag}技術面的圖有報讀文字、講得出數字，沒有圖例，不超出面板", bool(index) and index["role"] == "img" and exp["index_aria"] in index["label"]
            and index["legend"] == 0 and index["inside"] and index["marks"] >= 3 and index["height"] > 80, index)
    j.check(f"{tag}圖下面是影響／觀察／因應", panel["labels"] == ["影響", "觀察", "因應"] and panel["lines"][0].startswith("技術面：")
            and panel["lines"][-1].startswith("如果") and "就" in panel["lines"][-1], (panel["labels"], panel["lines"]))
    j.check(f"{tag}不是改版前的那一種面板", panel["oldGroups"] == 0)
    clean(j, tag + "技術面", panel)
    j.shot(page, f"{prefix}2-tech.png")

    panel = j.pick(page, "籌碼面")
    foreign, futures = chart(panel, "foreign"), chart(panel, "futures")
    j.check(f"{tag}按「籌碼面」：換成那一類（一次只看一類）", panel["checked"] == ["籌碼面"] and panel["say"] == SAY["chips"]
            and [item["name"] for item in panel["charts"]] == ["foreign", "futures"] and panel["lines"][0].startswith("籌碼面："), panel)
    j.check(f"{tag}外資每日買賣超：標連續天數與最後一天的金額，只畫到那一類的資料日", bool(foreign)
            and foreign["texts"] == ["外資每日買賣超（億）", *exp["foreign_texts"]] and exp["foreign_aria"] in foreign["label"] and foreign["marks"] >= 20, foreign)
    j.check(f"{tag}外資期貨淨空單：尾端標口數與一週的增減", bool(futures) and futures["texts"] == [exp["futures_title"], *exp["futures_texts"]]
            and exp["futures_texts"][0] in futures["label"], futures)
    j.check(f"{tag}籌碼面的圖有報讀文字、沒有圖例、不超出面板", all(item["role"] == "img" and item["label"] and item["legend"] == 0 and item["inside"] for item in panel["charts"]),
            panel["charts"])
    clean(j, tag + "籌碼面", panel)
    j.shot(page, f"{prefix}3-chips.png")

    panel = j.pick(page, "技術面")
    j.check(f"{tag}再按「技術面」：換回來", panel["checked"] == ["技術面"] and panel["say"] == SAY["tech"] and [item["name"] for item in panel["charts"]] == ["index"])
    j.close(page)
    panel = j.open_card(page)
    j.check(f"{tag}關掉再打開：回到影響最大的那一類", panel["checked"] == ["技術面"])
    j.close(page)

    # 籌碼卡點開的明細照舊（用資料包裡原本的解讀）
    section = page.locator('#homeCats [data-cat="chips"]')
    if section.get_attribute("aria-expanded") != "true":
        section.click()
        page.wait_for_timeout(400)
    page.locator('#chipGrid [data-chip="foreign"]').scroll_into_view_if_needed()
    page.locator('#chipGrid [data-chip="foreign"]').click()
    page.locator("#panelBody .chip-top").wait_for(state="visible", timeout=10_000)
    chip = page.evaluate(PANEL)
    lines = next((point["lines"] for point in (exp["plain"] or {}).get("points", []) if point["key"] == "foreign"), [])
    j.check(f"{tag}籌碼卡明細照舊：數字、圖與原本的說明都在，沒有類別鍵", chip["chipTop"] and chip["chipBig"] and not chip["tabs"] and chip["chipLines"] == lines
            and chip["noScroll"], chip["chipLines"])
    context.close()


def unchanged(j: Journey, browser, base: str, exp: dict, tag: str, email: str, options: dict, may_ask: bool) -> None:
    """和改版前一樣：卡片是資料包裡原本的解讀（過期就不顯示），點開是原本那一種面板。"""
    plain = exp["plain"]
    context, page, asked = j.home(browser, base, email, options, plain["one_line"] if plain else None)
    home = page.evaluate(HOME)
    j.check(f"{tag}{'問過' if may_ask else '不問'}最新一班", (len(asked) == 1) if may_ask else not asked, asked)
    if not plain:
        j.check(f"{tag}資料包裡原本的解讀過期：卡片照舊不顯示", home["hidden"] and home["noScroll"], home)
        context.close()
        return
    j.check(f"{tag}首頁那張卡照舊（原本的一句結論與四點）", not home["hidden"] and home["line"] == plain["one_line"] and home["list"] == min(4, len(plain["points"]))
            and home["noScroll"], home)
    panel = j.open_card(page)
    j.check(f"{tag}點開是原本的面板：沒有類別鍵與新的圖，各點照舊", panel["line"] == plain["one_line"] and not panel["tabs"] and not panel["charts"]
            and panel["oldGroups"] >= len(plain["points"]) and panel["noScroll"], panel["oldGroups"])
    context.close()


def five(j: Journey, browser, base: str, exp: dict, prefix: str, options: dict, mobile: bool) -> None:
    tag = f"{prefix}五類都有："
    context, page, _ = j.home(browser, base, OWNER, options, ONE_LINE)
    panel = j.open_card(page)
    j.check(f"{tag}類別鍵照固定的順序，選到的那一顆看得到", panel["keys"] == ["國際股市", "市場消息", "籌碼面", "技術面", "追蹤事項"] and panel["checked"] == ["技術面"]
            and panel["checkedInView"], panel["keys"])
    j.check(f"{tag}那一排不折行、整頁不橫向捲動{'；放不下就左右滑' if mobile else ''}", panel["oneRow"] and panel["noScroll"] and panel["stripOverflow"] == "auto"
            and panel["stripInside"] and (panel["stripScrolls"] or not mobile), panel)
    if mobile:
        j.shot(page, f"{prefix}4-five-keys.png")
    if not mobile:
        # 放得下時照舊是「按住滑到要的再放開」：從選到的那一顆按住，滑到「籌碼面」放開
        start, end = (page.locator("#panelBody .plain-tabs button", has_text=name).bounding_box() for name in ("技術面", "籌碼面"))
        page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
        page.mouse.down()
        page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=8)
        thumb = page.locator("#panelBody .plain-tabs .slide-thumb").count()
        page.mouse.up()
        page.wait_for_timeout(500)
        slid = page.evaluate(PANEL)
        j.check(f"{tag}按住滑到另一顆再放開：選中的框跟著走、放開就換成那一類", thumb == 1 and slid["checked"] == ["籌碼面"] and slid["say"] == SAY["chips"], (thumb, slid["checked"]))

    # 國際股市（票 03）
    panel = j.pick(page, "國際股市")
    bars = chart(panel, "overseas")
    j.check(f"{tag}國際股市：第一顆也按得到、看得到整顆；一句判斷＋一張橫條圖＋影響／觀察／因應", panel["checked"] == ["國際股市"] and panel["checkedInView"]
            and panel["say"] == SAY["overseas"] and len(panel["charts"]) == 1 and bool(bars) and panel["labels"] == ["影響", "觀察", "因應"], panel)
    j.check(f"{tag}橫條圖：各市場由大到小、台股在最後一列，數字帶正負號", bool(bars) and bars["texts"] == OVERSEAS_TEXTS, bars)
    j.check(f"{tag}橫條圖：紅漲綠跌，台股那一列實心、名稱粗體，其餘淡色", bool(bars)
            and [bar["fill"] for bar in bars["bars"]] == ["var(--up)"] * 4 + ["var(--down)"] * 2 + ["var(--up)"]
            and [bar["opacity"] for bar in bars["bars"]] == ["0.55"] * 6 + ["1"] and "加權指數" in bars["bold"] and "費城半導體" not in bars["bold"], bars)
    j.check(f"{tag}橫條圖：有報讀文字（列出每個市場的數字）、沒有圖例、字不疊在一起也不蓋到橫條、不超出面板", bool(bars) and bars["role"] == "img"
            and "費城半導體 +3.1%" in bars["label"] and bars["label"].endswith("加權指數 +2.2%") and bars["legend"] == 0 and not bars["clash"] and bars["inside"], bars)
    clean(j, tag + "國際股市", panel)
    if mobile:
        j.shot(page, f"{prefix}5-overseas.png")

    # 市場消息（票 04）
    panel = j.pick(page, "市場消息")
    j.check(f"{tag}市場消息：只有文字——一句判斷＋影響／觀察／因應，沒有圖", panel["checked"] == ["市場消息"] and panel["say"] == SAY["news"] and not panel["charts"]
            and panel["labels"] == ["影響", "觀察", "因應"] and len(panel["lines"]) == 3
            and panel["text"].replace(" ", "") == (ONE_LINE + "".join(panel["keys"]) + SAY["news"]
                                                   + "".join(label + line for label, line in zip(panel["labels"], panel["lines"]))).replace(" ", ""), panel["text"])
    clean(j, tag + "市場消息", panel)
    if mobile:
        j.shot(page, f"{prefix}6-news.png")

    # 追蹤事項（票 05）：不在那一份裡，用站主自己的追蹤事項畫
    panel = j.pick(page, "追蹤事項")
    levels = [item for item in panel["charts"] if item["name"] == "level"]
    j.check(f"{tag}追蹤事項：最後一顆也按得到、按了看得到整顆", panel["checked"] == ["追蹤事項"] and panel["checkedInView"] and panel["noScroll"]
            and (panel["stripLeft"] > 0 or not mobile), panel["stripLeft"])
    j.check(f"{tag}追蹤事項：一項一段，有結果的在前、其餘照離價位的遠近，最多 5 項", panel["heads"] == TRACK_HEADS and panel["tracks"] == 5
            and "外資連續賣超" not in panel["text"] and SAY["tracking"] not in panel["text"], panel["heads"])
    j.check(f"{tag}追蹤事項：價位型的畫刻度（價位、收盤、差幾 %），消息型與沒有價位的不畫", len(panel["charts"]) == 3
            and [item["texts"] for item in levels] == TRACK_LEVELS, [item["texts"] for item in panel["charts"]])
    j.check(f"{tag}刻度：條件達到的才上色；有報讀文字、沒有圖例、字不疊在一起、不超出面板", len(levels) == 3
            and [item["dots"] for item in levels] == [["var(--up)"], ["var(--ink)"], ["var(--ink)"]]
            and levels[0]["label"].endswith("已達條件") and levels[1]["label"].endswith("未達條件")
            and all(item["role"] == "img" and item["legend"] == 0 and not item["clash"] and item["inside"] for item in levels), levels)
    j.check(f"{tag}追蹤事項：每一項下面只有「因應」", panel["allLabels"] == ["因應"] * 4 and all(line.startswith("如果") and "就" in line for line in panel["allLines"]),
            (panel["allLabels"], panel["allLines"]))
    clean(j, tag + "追蹤事項", panel)
    if mobile:
        j.shot(page, f"{prefix}7-tracking.png")
    panel = j.pick(page, "國際股市")
    j.check(f"{tag}從最後一顆按回第一顆：一樣看得到整顆", panel["checked"] == ["國際股市"] and panel["checkedInView"])
    j.close(page)
    panel = j.open_card(page)
    j.check(f"{tag}關掉再打開：回到影響最大的那一類，不會停在追蹤事項", panel["checked"] == ["技術面"] and panel["checkedInView"] and len(panel["keys"]) == 5)
    context.close()

    context, page, _ = j.home(browser, base, FRIEND, options, ONE_LINE)
    panel = j.open_card(page)
    j.check(f"{tag}朋友（兩個開關都開給全部）：四顆鍵，沒有追蹤事項", panel["keys"] == ["國際股市", "市場消息", "籌碼面", "技術面"] and panel["oneRow"]
            and panel["noScroll"] and SAY["tracking"] not in panel["text"], panel["keys"])
    shown = "".join(j.pick(page, name)["text"] for name in panel["keys"])
    j.check(f"{tag}朋友每一類都看得到內容，但看不到站主的追蹤事項與個股", "聯發科" not in shown and "收盤站上壓力" not in shown and "近 20 日低點" not in shown
            and all(SAY[key] in shown for key in ("overseas", "news", "chips", "tech")), shown[:200])
    context.close()


def quiet(j: Journey, browser, base: str, exp: dict, prefix: str, options: dict, mobile: bool) -> None:
    """國際股市還沒更新、這一班沒有消息，站主也沒有追蹤事項：那兩類只有一句，沒有「追蹤事項」那一顆。"""
    tag = f"{prefix}沒有新東西的類別："
    context, page, _ = j.home(browser, base, OWNER, options, ONE_LINE)
    panel = j.open_card(page)
    j.check(f"{tag}四顆鍵都在（沒有追蹤事項就沒有那一顆）", panel["keys"] == ["國際股市", "市場消息", "籌碼面", "技術面"] and panel["checked"] == ["技術面"]
            and panel["oneRow"] and panel["noScroll"], panel["keys"])
    for name, say in (("國際股市", QUIET_OVERSEAS), ("市場消息", QUIET_NEWS)):
        panel = j.pick(page, name)
        j.check(f"{tag}{name}只有一句，沒有圖也沒有三段", panel["say"] == say and not panel["charts"] and not panel["labels"]
                and panel["text"].replace(" ", "") == f"{ONE_LINE}{''.join(panel['keys'])}{say}".replace(" ", "") and panel["noScroll"], panel["text"])
    if mobile:
        j.shot(page, f"{prefix}8-quiet.png")
    context.close()


def main() -> int:
    j = Journey()
    source = data_source()
    OUT.mkdir(parents=True, exist_ok=True)
    exp = expected(source)
    build(source)
    sizes = [("desktop-1440-", {"viewport": {"width": 1440, "height": 900}}, False),
             ("mobile-390-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}, True)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        server, base = serve(exp, "two", "owner")
        try:
            for prefix, options, mobile in sizes:
                with_brief(j, browser, base, exp, prefix, options, mobile)
                unchanged(j, browser, base, exp, f"{prefix}只開站主時的朋友：", FRIEND, options, may_ask=False)
        finally:
            server.terminate()
        server, base = serve(exp, "five", "all", watch="all")
        try:
            for prefix, options, mobile in sizes:
                five(j, browser, base, exp, prefix, options, mobile)
        finally:
            server.terminate()
        server, base = serve(exp, "quiet", "all", watch="all")
        try:
            for prefix, options, mobile in sizes:
                quiet(j, browser, base, exp, prefix, options, mobile)
        finally:
            server.terminate()
        server, base = serve(exp, "none", "owner")
        try:
            for prefix, options, mobile in sizes:
                unchanged(j, browser, base, exp, f"{prefix}還沒有任何一班：", OWNER, options, may_ask=True)
        finally:
            server.terminate()
        server, base = serve(exp, "two", None)
        try:
            for prefix, options, mobile in sizes:
                unchanged(j, browser, base, exp, f"{prefix}開關關著：", OWNER, options, may_ask=False)
        finally:
            server.terminate()
        browser.close()
    errors = [text for text in j.console if "favicon" not in text]
    j.check("沒有程式錯誤", not errors, errors[:5])
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
