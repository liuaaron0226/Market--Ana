"""除錯：進場動畫是否有觸發。"""
import sys, threading, json
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa
from shoot import make_handler  # noqa

server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / "web-bold"))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    p = b.new_page(viewport={"width": 1280, "height": 800})
    p.add_init_script("""
      window.__log = [];
      const orig = Element.prototype.animate;
      Element.prototype.animate = function(...a){ window.__log.push([performance.now()|0, this.className || this.id]); return orig.apply(this, a); };
    """)
    msgs = []
    p.on("console", lambda m: msgs.append(m.text))
    p.on("pageerror", lambda e: msgs.append("ERR " + str(e)))
    p.goto(base + "/stock.html?code=6488")
    p.wait_for_timeout(2500)
    print(msgs)
    print(p.evaluate("fetch('/motion.js').then(r=>r.text()).then(t=>t.length+' '+t.slice(-80))"))
    print(p.evaluate("""(() => { try { const s=document.createElement('script'); s.textContent='window.__probe=1'; document.body.append(s); return window.__probe; } catch(e){ return String(e) } })()"""))
    print(p.evaluate("typeof Element.prototype.animate"), p.evaluate("document.querySelectorAll('.bd-switch-pill').length"))
    print(p.evaluate("""(() => {
      const sw = document.querySelector('.kc-study-switcher');
      const on = sw && sw.querySelector('.kc-study-choice[aria-pressed="true"]');
      return { sw: !!sw, on: !!on, html: sw ? sw.outerHTML.slice(0, 300) : null, content: document.getElementById('content').hidden };
    })()"""))
    print(json.dumps(p.evaluate("({log: window.__log, reduce: matchMedia('(prefers-reduced-motion: reduce)').matches, scripts: [...document.scripts].map(s=>s.src)})"), ensure_ascii=False))
    b.close()
server.shutdown()
