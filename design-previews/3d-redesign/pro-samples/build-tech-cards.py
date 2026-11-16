# 樣品題（owner 11-16 轉本人「很亂」）：「技術」裡原本的均線距離表與 RSI／量比／KD，要不要也放進和三張新卡同一種卡片。
# 每個版本一頁、可以單獨打開、可以實際點：頁面是打包後的正式個股頁（cloud/dist），樣式與程式全部內嵌；
# 資料是預覽伺服器給台積電 2330 的那幾份回應（本機 11-09），錄在 .tmp/3d-baseline/facts-place/api-2330.json，頁面裡的讀取換成錄好的回應。
#   A：原本的內容照舊平放在桌面上，三張新卡接在下面（現在網站上的樣子，和本人指定的參考圖一樣）。
#   B：原本的內容也放進同一種卡片（每一個切換籤的內容各一張），三張新卡接在下面。
# 用法：先跑 node cloud/build.mjs，再 python -X utf8 design-previews/3d-redesign/pro-samples/build-tech-cards.py
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DIST = ROOT / "cloud" / "dist"
API = json.loads((ROOT / ".tmp/3d-baseline/facts-place/api-2330.json").read_text(encoding="utf-8"))
API["/api/features"]["body"]["features"]["strategy_v1"] = False   # 個股操作策略照正式站關著
# 這份錄好的分析是補產業別之前錄的；事實與五塊改用補好的那一份（有「消息」）
PREVIEW = json.loads((ROOT / ".tmp/3d-baseline/signals-preview.json").read_text(encoding="utf-8"))
API["/api/stocks/2330/analysis"]["body"]["facts"] = PREVIEW["2330"]["facts"]
API["/api/stocks/2330/analysis"]["body"]["signals"] = PREVIEW["2330"]["signals"]

VERSIONS = {
    "a": "原本的表平放在桌面上，三張新卡接在下面（現在的樣子）。（推薦：最精簡、和你指定的參考圖一樣）",
    "b": "原本的表與 RSI／量比／KD 也放進同一種卡片，和三張新卡分開。",
}

SHIM = """<script>
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

BADGE = """<div class="sample-badge" role="note"><b>樣品（不是正式站）</b><span>「技術」要不要也放進卡片</span>%s</div>
<style>
.sample-badge { position: fixed; left: 50%%; top: 14px; transform: translateX(-50%%); z-index: 50; display: flex; align-items: center; gap: 6px; padding: 6px 8px 6px 12px;
  border-radius: 10px; background: #1d1b20; color: #f2f0ec; font: 12px "Noto Sans TC", system-ui, sans-serif; box-shadow: 0 6px 18px rgb(0 0 0 / 30%%); }
.sample-badge span { color: #b0adb6; }
.sample-badge a { min-width: 28px; height: 26px; display: inline-grid; place-items: center; border-radius: 6px; color: #f2f0ec; text-decoration: none; font-weight: 700; border: 1px solid #45434d; }
.sample-badge a[aria-current="page"] { background: #f2f0ec; color: #1d1b20; }
@media (max-width: 860px) { .sample-badge { position: static; transform: none; width: max-content; margin: 8px auto 24px; } }
</style>"""

# 版本 b：每一塊切換籤的原本內容包進一張和新卡同一種的卡片（樣品用的一小段程式，選定後要照正式寫法做）
B_STYLE = """<style>
body.st .sf-card { margin-top: 2px; padding: 18px 22px 20px; border-radius: var(--r-slab); background: var(--slab); box-shadow: var(--slab-shadow); }
@media (max-width: 860px) { body.st .sf-card { padding: 14px 14px 16px; } }
</style>"""
B_SCRIPT = """<script>
(() => {
  const host = document.getElementById('stockFactsBlocks');
  const wrap = () => {
    const panel = host.querySelector('.sf-panel');
    if (!panel || panel.querySelector(':scope > .sf-card')) return;
    const own = [...panel.children].filter(node => !node.classList.contains('ss'));
    if (!own.length) return;
    const box = document.createElement('div');
    box.className = 'sf-card';
    panel.insertBefore(box, own[0]);
    box.append(...own);
  };
  new MutationObserver(wrap).observe(host, { childList: true, subtree: true });
  wrap();
})();
</script>"""

css_re = re.compile(r'<link rel="stylesheet" href="([^":]+?)(\?v=[^"]*)?">')
js_re = re.compile(r'<script src="([^":]+?)(\?v=[^"]*)?"( defer)?></script>')


def inline(html: str) -> str:
    html = css_re.sub(lambda m: f"<style>\n{(DIST / m.group(1)).read_text(encoding='utf-8')}\n</style>", html)
    return js_re.sub(lambda m: "<script>\n" + (DIST / m.group(1)).read_text(encoding="utf-8").replace("</script", "<\\/script") + "\n</script>", html)


source = (DIST / "stock.html").read_text(encoding="utf-8")
current = ' aria-current="page"'
for key, note in VERSIONS.items():
    html = source
    if key == "b":
        html = html.replace("</head>", B_STYLE + "\n</head>", 1)
    html = inline(html)
    html = html.replace("location.protocol === 'file:'", "false /* 樣品頁 */")
    html = html.replace("<head>", "<head>\n" + SHIM % json.dumps(API, ensure_ascii=False).replace("</", "<\\/"), 1)
    links = "".join(f'<a href="tech-cards-{k}.html"{current if k == key else ""}>{k}</a>' for k in VERSIONS)
    html = html.replace("</body>", BADGE % links + (B_SCRIPT if key == "b" else "") + "\n</body>", 1)
    html = html.replace("<title>個股分析</title>", f"<title>樣品 {key}｜技術要不要放進卡片</title>", 1)
    (OUT / f"tech-cards-{key}.html").write_text(html, encoding="utf-8")
    print(f"tech-cards-{key}.html", len(html) // 1024, "KB", note)
