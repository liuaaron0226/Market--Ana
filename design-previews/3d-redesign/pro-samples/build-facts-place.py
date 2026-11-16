# 樣品題：個股頁的事實區塊在立體版要放哪裡（owner 11-16 交代；本人兩個定案在立體版撞在一起，見 C 包票〈要本人決定的〉第 1 點）。
# 每個版本一頁、可以單獨打開、可以實際點：頁面是打包後的正式個股頁（cloud/dist），樣式與程式全部內嵌；
# 資料是預覽伺服器給台積電 2330 的那幾份回應（本機 11-09 資料，含事實區塊與五塊新功能），錄在 .tmp/3d-baseline/facts-place/api-2330.json，
# 頁面裡的 fetch 換成讀這份錄好的回應，所以不用開伺服器。只有「排法」各版不同。
# 用法：先跑 node cloud/build.mjs 與預覽伺服器錄好回應，再 python -X utf8 design-previews/3d-redesign/pro-samples/build-facts-place.py
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DIST = ROOT / "cloud" / "dist"
API = json.loads((ROOT / ".tmp/3d-baseline/facts-place/api-2330.json").read_text(encoding="utf-8"))
# 個股操作策略照正式站關著（預覽伺服器是開的）；本人看到的第一個畫面才和正式站一樣
API["/api/features"]["body"]["features"]["strategy_v1"] = False

VERSIONS = {
    "a": "排在三顆分類鍵之後（合併時的暫定）：一進去先看到 K 線與三顆鍵，事實區塊在最下面、重點開著。",
    "b": "緊接在 K 線下面（照事實區塊原本的定案）：三顆分類鍵往下移，電腦一進去看不到。",
    "c": "折衷：切換籤併進分類鍵那一排（重點、法人、融資券、基本面、技術、價位、籌碼、對照 0050），一次只開一個、一進去重點開著；"
         "電腦上當日三格縮成一行、畫線工具併到「日K／均線」那一列，電腦一進去整排看得到。（推薦）",
}

SHIM = """<script>
/* 樣品頁：fetch 改讀錄好的回應（預覽伺服器給 2330 的那幾份），不連任何伺服器 */
(() => {
  try { if (!location.search.includes('code=')) history.replaceState(null, '', '?code=2330'); } catch (_) {}
  const API = %s;
  const real = window.fetch;
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://sample.invalid');
    if (!url.pathname.startsWith('/api/')) return real(input, init);
    const method = (init.method || 'GET').toUpperCase();
    const hit = method === 'GET' ? API[url.pathname] : { status: 200, body: {} };
    const status = hit ? hit.status : 404, body = hit ? hit.body : { error: 'sample: no recorded response' };
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  };
  if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.reject(new Error('sample'));
})();
</script>"""

BADGE = """<div class="sample-badge" role="note">
  <b>樣品（不是正式站）</b><span>事實區塊放哪裡</span>%s
</div>
<style>
.sample-badge { position: fixed; left: 50%%; top: 14px; transform: translateX(-50%%); z-index: 50; display: flex; align-items: center; gap: 6px; padding: 6px 8px 6px 12px;
  border-radius: 10px; background: #1d1b20; color: #f2f0ec; font: 12px "Noto Sans TC", system-ui, sans-serif; box-shadow: 0 6px 18px rgb(0 0 0 / 30%%); }
.sample-badge span { color: #b0adb6; }
@media (max-width: 860px) { .sample-badge { position: static; transform: none; width: max-content; margin: 8px auto 24px; } }
.sample-badge a { min-width: 28px; height: 26px; display: inline-grid; place-items: center; border-radius: 6px; color: #f2f0ec; text-decoration: none; font-weight: 700; border: 1px solid #45434d; }
.sample-badge a[aria-current="page"] { background: #f2f0ec; color: #1d1b20; }
</style>"""

# 版本 c 的樣式：一排鍵（手機上排成兩列）、事實區塊的切換籤藏起來改由那一排帶、當日三格一行、電腦上畫線工具併到圖的標題列
C_STYLE = """<style>
/* ---- 版本 c ---- */
#stockFactsBlocks .sf-tabs { display: none; }
body.st #stockFactsBlocks { margin-top: 16px; padding-top: 0; border-top: 0; }
body.st #stockFactsBlocks.is-shut { display: none; }
@media (min-width: 861px) {
  /* 當日三格縮成一行（只在電腦上；手機照舊兩行）：名稱和數字排在同一行 */
  .bd .sf-cell { display: flex; align-items: baseline; gap: 8px; padding-top: 10px; padding-bottom: 2px; }
  .bd .sf-value { margin-top: 0; }
  /* 畫線工具併到「日K／均線」那一列（左邊是資料日，右邊是週期與均線） */
  body.st .bd-chart-bar { margin-bottom: 4px; }
  body.st .bd-chart-bar > div:first-child { display: flex; align-items: center; gap: 16px; }
  body.st .bd-draw-row { position: absolute; left: 230px; top: 20px; }
  body.st .bd-stage { position: relative; }
  body.st .bd-chart-bar + .bd-draw-row .bd-draw { margin: 0; }
  body.st #stockCats button { font-size: 14px; }
}
@media (max-width: 860px) {
  body.st #stockCats { grid-auto-flow: row; grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
</style>"""

# 版本 c 的程式：把事實區塊的切換籤搬成分類鍵那一排的鍵（放在前面），和三類一起「一次只開一個」
C_SCRIPT = """<script>
(() => {
  const bar = document.getElementById('stockCats'), host = document.getElementById('stockFactsBlocks');
  const ready = () => host.querySelectorAll('.sf-tab').length;
  const build = () => {
    const tabs = [...host.querySelectorAll('.sf-tab')];
    const keys = tabs.map(tab => {
      const key = document.createElement('button');
      key.type = 'button'; key.textContent = tab.textContent; key.dataset.factsKey = tab.dataset.block;
      key.setAttribute('aria-controls', 'stockFactsBlocks'); key.setAttribute('aria-expanded', 'false');
      return key;
    });
    bar.prepend(...keys);
    const open = key => {
      keys.forEach(other => other.setAttribute('aria-expanded', String(other === key)));
      host.classList.toggle('is-shut', !key);
      if (key) tabs.find(tab => tab.dataset.block === key.dataset.factsKey)?.click();
    };
    bar.addEventListener('click', event => {
      const key = event.target.closest('button');
      if (!key) return;
      if (key.dataset.factsKey) open(key.getAttribute('aria-expanded') === 'true' ? null : key);
      else open(null);   // 點了價位／籌碼／對照 0050：事實區塊收起來（三類的開合照舊由 home-fold.js 處理）
    });
    open(keys[0]);       // 一進去「重點」開著（事實區塊原本的定案：一次顯示一塊）
  };
  const wait = () => (ready() ? build() : setTimeout(wait, 100));
  wait();
})();
</script>"""

css_re = re.compile(r'<link rel="stylesheet" href="([^":]+?)(\?v=[^"]*)?">')
js_re = re.compile(r'<script src="([^":]+?)(\?v=[^"]*)?"( defer)?></script>')


def inline(html: str) -> str:
    html = css_re.sub(lambda m: f"<style>\n{(DIST / m.group(1)).read_text(encoding='utf-8')}\n</style>", html)
    return js_re.sub(lambda m: "<script>\n" + (DIST / m.group(1)).read_text(encoding="utf-8").replace("</script", "<\\/script") + "\n</script>", html)


def move_facts(html: str, before: str) -> str:
    block = re.search(r'      <!-- 事實區塊（本人 2026-11-15.*?<section class="wp" id="watchPeers" aria-label="國外同類股" hidden></section>\n', html, re.S).group(0)
    html = html.replace(block, "")
    assert html.count(before) == 1, before
    return html.replace(before, block + "\n" + before)


source = (DIST / "stock.html").read_text(encoding="utf-8")
for key, note in VERSIONS.items():
    html = source
    if key == "b":
        html = move_facts(html, "      <!-- 分類鍵：")
    if key == "c":
        html = move_facts(html, '      <section class="bd-cat" id="levelsPanel"')   # 那一排鍵的正下方、三類的區塊之前
        html = html.replace("</head>", C_STYLE + "\n</head>", 1)
    html = inline(html)
    # 正式頁在 file:// 打開時會叫人改開本機服務；樣品頁的資料是錄好的，這一道檢查在樣品裡拿掉
    html = html.replace("location.protocol === 'file:'", "false /* 樣品頁 */")
    html = html.replace("<head>", "<head>\n" + SHIM % json.dumps(API, ensure_ascii=False).replace("</", "<\\/"), 1)
    current = ' aria-current="page"'
    links = "".join(f'<a href="facts-place-{k}.html"{current if k == key else ""}>{k}</a>' for k in VERSIONS)
    tail = BADGE % links + (C_SCRIPT if key == "c" else "")
    html = html.replace("</body>", tail + "\n</body>", 1)
    html = html.replace("<title>個股分析</title>", f"<title>樣品 {key}｜事實區塊放哪裡</title>", 1)
    (OUT / f"facts-place-{key}.html").write_text(html, encoding="utf-8")
    print(f"facts-place-{key}.html", len(html) // 1024, "KB", note)
