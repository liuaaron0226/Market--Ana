"""產生自選與庫存頁「不要那麼光滑」的三個質感樣品（本人 2026-11-16 看過做進去的液態玻璃＋金屬之後：
「我覺得太光滑了，可以在微調材質」）。

助手不確定「太光滑」指哪一塊、要往哪個方向，所以做三種不同的方向給本人挑。三頁都是「正式的頁面＋多一段樣式」
（資料與操作都是真的，要從預覽伺服器開）：
  portfolio-texture-a.html（/ta）霧面：玻璃列是霧面的（細細的霧）、損益條的反光收斂，像霧面玻璃與緞面金屬。
  portfolio-texture-b.html（/tb）噴砂金屬：只動金屬——成本那一段、選中的篩選鍵、「＋」有看得到的細顆粒；玻璃照舊。
  portfolio-texture-c.html（/tc）水紋玻璃：玻璃不是一整片平的——每一列與紅色那一段裡有很淡、不規則的水紋；金屬照舊。
顆粒與水紋都是不規則的雜訊，不是拉絲、不是一格一格（本人在個股頁那條線說過不要那兩種）。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-texture.py"""
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = (ROOT / "web" / "portfolio.html").read_text(encoding="utf-8")


def noise(frequency, octaves, slope, size=180):
    """一張灰階的雜訊圖（中間灰上下起伏），用「疊加」混上去：亮的地方更亮、暗的地方更暗，深淺兩種背景都看得到。
    frequency 大是細顆粒，小是大片的起伏；slope 是起伏的強弱。"""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}'><filter id='n' x='0' y='0' width='100%' height='100%'>"
           f"<feTurbulence type='fractalNoise' baseFrequency='{frequency}' numOctaves='{octaves}' seed='7' stitchTiles='stitch'/>"
           f"<feColorMatrix values='.33 .33 .33 0 0 .33 .33 .33 0 0 .33 .33 .33 0 0 0 0 0 0 1'/>"
           f"<feComponentTransfer><feFuncR type='linear' slope='{slope}' intercept='{(1 - slope) / 2:.3f}'/><feFuncG type='linear' slope='{slope}' intercept='{(1 - slope) / 2:.3f}'/><feFuncB type='linear' slope='{slope}' intercept='{(1 - slope) / 2:.3f}'/></feComponentTransfer>"
           f"</filter><rect width='100%' height='100%' filter='url(#n)'/></svg>")
    return f'url("data:image/svg+xml,{quote(svg)}")'


GRAIN_FINE = noise(".95", 2, 1.0)          # 細顆粒：霧面、噴砂
GRAIN_COARSE = noise(".8", 2, 1.5)        # 稍粗一點的顆粒：噴砂金屬
RIPPLE = noise(".011 .034", 2, 1.3, 420)   # 大片、橫向拉長的不規則起伏：水紋

# 每一列下面墊一層質感（在內容下面，字不受影響）
ROW_LAYER = """
  #holdingRows .hv-row[data-state]::before {
    content: ""; position: absolute; inset: 0; z-index: 0; border-radius: inherit; pointer-events: none;
    background-image: %(image)s; background-size: %(size)s; mix-blend-mode: soft-light; opacity: %(opacity)s;
  }
"""

A = f"""
  /* 霧面玻璃：每一列蓋一層很細的霧；上緣的亮邊收斂 */
  {ROW_LAYER % dict(image=GRAIN_FINE, size="180px 180px", opacity=".28")}
  body.pf {{ --glass-rise: inset 0 1px 0 rgb(255 255 255 / 14%), var(--glass-edge), 0 var(--key-thick) 0 var(--edge), 0 6px 16px rgb(var(--shade) / calc(.10 * var(--shade-k))); --glass-thin: inset 0 1px 0 rgb(255 255 255 / 9%), var(--glass-edge); }}
  /* 損益條：玻璃管與兩段的反光都收斂——緞面的金屬、霧面的紅色玻璃；高光還在漂，但是一團柔光不是一顆亮點 */
  .hv-bar::before {{ background-image: {GRAIN_FINE}, linear-gradient(180deg, rgb(255 255 255 / 8%) 0%, rgb(255 255 255 / 1%) 38%, transparent 62%, rgb(255 255 255 / 4%) 100%); background-blend-mode: soft-light, normal; box-shadow: inset 0 1px 0 rgb(255 255 255 / 14%), inset 0 0 0 1px color-mix(in srgb, var(--ink) 10%, transparent); }}
  .hv-bar .base {{
    background-image: {GRAIN_FINE},
      radial-gradient(60% 150% at var(--ex, 30%) -30%, rgb(255 255 255 / calc(var(--hi, .7) * .45)) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / 10%) 0%, transparent var(--hl, 18%), transparent var(--hz, 52%), rgb(0 0 0 / calc(var(--hs, .3) * .7)) calc(var(--hz, 52%) + 22%), rgb(0 0 0 / 6%) 100%),
      var(--base);
    background-blend-mode: soft-light, normal, normal, normal;
  }}
  .hv-bar :is(.gain, .lack) {{
    background-image: {GRAIN_FINE},
      radial-gradient(34% 90% at var(--ex, 30%) 20%, rgb(255 255 255 / 34%) 0%, transparent 74%),
      linear-gradient(180deg, rgb(255 255 255 / 18%) 0%, rgb(255 255 255 / 3%) 42%, transparent 64%, rgb(255 255 255 / 9%) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--a) 62%, transparent), color-mix(in srgb, var(--b) 82%, transparent));
    background-blend-mode: soft-light, normal, normal, normal;
    box-shadow: inset 0 0 0 .5px rgb(255 255 255 / 22%), inset 0 1px 0 rgb(255 255 255 / 26%);
    filter: drop-shadow(0 2px 4px color-mix(in srgb, var(--b) 34%, transparent));
  }}
  /* 金屬鍵：緞面（反光收斂、有很細的霧） */
  #listFilter button[aria-pressed="true"], #listFilter .slide-thumb, .pf-add:not([aria-expanded="true"]) {{
    background-image: {GRAIN_FINE},
      radial-gradient(60% 130% at var(--mx, 28%) -40%, rgb(255 255 255 / 11%) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / 5%) 0%, transparent 26%, transparent 56%, rgb(0 0 0 / 10%) 78%, rgb(0 0 0 / 4%) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--ink) 20%, var(--panel)), color-mix(in srgb, var(--ink) 9%, var(--panel)));
    background-blend-mode: soft-light, normal, normal, normal;
  }}
"""

B = f"""
  /* 噴砂金屬：只動金屬的三個地方——成本那一段、選中的篩選鍵、「＋」。表面有看得到的細顆粒，反光變成一片柔光 */
  .hv-bar .base {{
    background-image: {GRAIN_COARSE},
      radial-gradient(56% 150% at var(--ex, 30%) -30%, rgb(255 255 255 / calc(var(--hi, .7) * .6)) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / 14%) 0%, transparent var(--hl, 18%), transparent var(--hz, 52%), rgb(0 0 0 / var(--hs, .3)) calc(var(--hz, 52%) + 18%), rgb(0 0 0 / 8%) 100%),
      var(--base);
    background-size: 90px 90px, auto, auto, auto; background-blend-mode: soft-light, normal, normal, normal;
  }}
  #listFilter button[aria-pressed="true"], #listFilter .slide-thumb, .pf-add:not([aria-expanded="true"]) {{
    background-image: {GRAIN_COARSE},
      radial-gradient(56% 130% at var(--mx, 28%) -40%, rgb(255 255 255 / 16%) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / 7%) 0%, transparent 24%, transparent 54%, rgb(0 0 0 / 14%) 74%, rgb(0 0 0 / 5%) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--ink) 22%, var(--panel)), color-mix(in srgb, var(--ink) 10%, var(--panel)));
    background-size: 120px 120px, auto, auto, auto; background-blend-mode: soft-light, normal, normal, normal;
  }}
"""

C = f"""
  /* 水紋玻璃：玻璃不是一整片平的。每一列裡有很淡、大片、不規則的起伏（每一列截到的位置不同，不會一列一列長得一樣） */
  {ROW_LAYER % dict(image=RIPPLE, size="420px 420px", opacity=".3")}
  #holdingRows .hv-row:nth-child(4n + 1)::before {{ background-position: 0 0; }}
  #holdingRows .hv-row:nth-child(4n + 2)::before {{ background-position: 170px 96px; }}
  #holdingRows .hv-row:nth-child(4n + 3)::before {{ background-position: 60px 230px; }}
  #holdingRows .hv-row:nth-child(4n)::before {{ background-position: 290px 330px; }}
  /* 紅（綠）的那一段裡面也有水紋：像裡面真的有液體 */
  .hv-bar :is(.gain, .lack) {{
    background-image: {RIPPLE},
      radial-gradient(16% 62% at var(--ex, 30%) 24%, rgb(255 255 255 / 80%) 0%, transparent 72%),
      radial-gradient(38% 80% at calc(var(--ex, 30%) + 30%) 118%, rgb(255 255 255 / 34%) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / 36%) 0%, rgb(255 255 255 / 5%) 40%, transparent 62%, rgb(255 255 255 / 20%) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--a) 62%, transparent), color-mix(in srgb, var(--b) 82%, transparent));
    background-size: 150px 60px, auto, auto, auto, auto; background-blend-mode: soft-light, normal, normal, normal, normal;
  }}
  /* 玻璃管的管壁也不是平的 */
  .hv-bar::before {{ background-image: {RIPPLE}, linear-gradient(180deg, rgb(255 255 255 / 16%) 0%, rgb(255 255 255 / 2%) 38%, transparent 62%, rgb(255 255 255 / 9%) 100%); background-size: 220px 70px, auto; background-blend-mode: soft-light, normal; }}
"""

VARIANTS = {
    "a": ("霧面：霧面玻璃、緞面金屬，反光收斂", A),
    "b": ("噴砂金屬：只有金屬加細顆粒，玻璃照舊", B),
    "c": ("水紋玻璃：玻璃裡有很淡的不規則水紋，金屬照舊", C),
}

for key, (what, style) in VARIANTS.items():
    html = PAGE
    assert html.count("<head>") == 1 and html.count("</head>") == 1
    html = html.replace("<head>", f'<head>\n  <base href="/">\n  <!-- 可丟棄的樣品（由 build-portfolio-texture.py 產生，不要手改）：{what}。正式的頁面加一段樣式而已。 -->', 1)
    html = html.replace("</head>", f"  <style>{style}</style>\n</head>", 1)
    html = html.replace("<title>自選股與庫存｜台股決策鏡</title>", f"<title>樣品 {key}：{what}</title>", 1)
    (HERE / f"portfolio-texture-{key}.html").write_text(html, encoding="utf-8", newline="\n")
print("texture ok")
