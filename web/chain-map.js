// AI 產業鏈資金版圖（本人 2026-11-24 核准的樣品 v4，.scratch/ai-chain-map 票 01）。
// boot() 裡面是樣品 v4-app.js 原封不動（畫面、互動、動態、網頁端的加總都一樣）；只改了這幾處：
// - 資料改成向 /api/chain-map 要（樣品是內嵌），拿到才 boot；還沒有資料或讀不到就在畫面那一格寫一句話。
// - 同一家公司的主要環節從產業鏈定義的 primary 讀（樣品放在資金數字的 owner，內容相同）。
// - 層與環節的簡稱從定義的 short 讀（樣品寫死在程式裡，內容相同）：定義之後可以改，程式不認任何固定的 id。
// - 本頁固定淺色，不改其他頁已儲存的主題；測試用的 window.ChainMap.snapshot() 只讀狀態。
(function () {
'use strict';
function boot(CHAIN, FLOWS) {

/* ---------- 小工具 ---------- */
const $ = (s, r) => (r || document).querySelector(s);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const mix = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const rgb = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a == null ? 1 : a})`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const EASE = 'cubic-bezier(.16, 1, .3, 1)';
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const WIDE = matchMedia('(min-width: 960px)');
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/* ---------- 資料：個股 → 環節 → 層 → 全產業鏈 ---------- */
const MONTHS = FLOWS.months, N = MONTHS.length, I0 = FLOWS.inst0;
const LAYERS = CHAIN.layers, NODES = CHAIN.nodes, BY = {}, OTC = new Set(CHAIN.otc || []), ROWS = LAYERS.length;
const short = n => n.short || n.name;
NODES.forEach(n => { BY[n.id] = n; n.up = []; });
NODES.forEach(n => { n.down = (n.down || []).filter(d => BY[d]); n.down.forEach(d => BY[d].up.push(n.id)); });
LAYERS.forEach((L, k) => { L.k = k; L.nodes = NODES.filter(n => n.layer === L.id); L.nodes.forEach(n => { n.row = k; }); });
const STK = { tw: {}, us: {} };
NODES.forEach(n => ['tw', 'us'].forEach(m => (n[m] || []).forEach(r => {
  const s = STK[m][r[0]] || (STK[m][r[0]] = { code: r[0], market: m, name: (m === 'tw' ? CHAIN.tw : CHAIN.us)[r[0]] || r[0], nodes: [], roles: {} });
  s.nodes.push(n.id); s.roles[n.id] = r[1];
})));
const zeros = () => new Array(N).fill(0), nulls = () => new Array(N).fill(null);
const TOT = { tw: zeros(), us: zeros() };
['tw', 'us'].forEach(m => Object.values(STK[m]).forEach(s => {
  const f = (FLOWS[m] || {})[s.code];
  s.owner = s.nodes.length > 1 ? ((CHAIN.primary || {})[(m === 'tw' ? 'TW ' : 'US ') + s.code] || s.nodes[0]) : s.nodes[0];
  s.t = f && f.t ? f.t : null; s.i = null;
  if (m === 'tw') { s.i = nulls(); for (let j = I0; j < N; j++) s.i[j] = f && f.i ? (f.i[j - I0] || 0) : 0; }
  if (s.t) s.t.forEach((v, i) => { TOT[m][i] += v || 0; });
}));
const stocksOf = (n, m) => (n[m] || []).map(r => STK[m][r[0]]);
const SER = { tw: {}, inst: {}, us: {} }, RAW = { tw: {}, us: {} };
const addInto = (dst, src) => { for (let i = 0; i < N; i++) if (src[i] != null) dst[i] = (dst[i] == null ? 0 : dst[i]) + src[i]; };
['tw', 'us'].forEach(m => {
  Object.values(STK[m]).forEach(s => {
    SER[m]['S:' + s.code] = s.t ? s.t.map((v, i) => TOT[m][i] ? (v || 0) / TOT[m][i] * 100 : 0) : zeros();
    RAW[m]['S:' + s.code] = s.t || zeros();
    if (m === 'tw') SER.inst['S:' + s.code] = s.i;
  });
  NODES.forEach(n => {
    const sh = zeros(), raw = zeros(), ins = nulls();
    stocksOf(n, m).forEach(s => { if (s.owner !== n.id) return; addInto(sh, SER[m]['S:' + s.code]); addInto(raw, RAW[m]['S:' + s.code]); if (m === 'tw') addInto(ins, s.i); });
    SER[m]['N:' + n.id] = sh; RAW[m]['N:' + n.id] = raw;
    if (m === 'tw') { for (let i = I0; i < N; i++) if (ins[i] == null) ins[i] = 0; SER.inst['N:' + n.id] = ins; }
  });
  LAYERS.forEach(L => {
    const sh = zeros(), raw = zeros(), ins = nulls();
    L.nodes.forEach(n => { addInto(sh, SER[m]['N:' + n.id]); addInto(raw, RAW[m]['N:' + n.id]); if (m === 'tw') addInto(ins, SER.inst['N:' + n.id]); });
    SER[m]['L:' + L.id] = sh; RAW[m]['L:' + L.id] = raw; if (m === 'tw') SER.inst['L:' + L.id] = ins;
  });
  RAW[m].C = TOT[m]; SER[m].C = TOT[m];
});
SER.inst.C = (() => { const a = nulls(); LAYERS.forEach(L => addInto(a, SER.inst['L:' + L.id])); return a; })();
const METRICS = {
  tw:   { kind: 'share', market: 'tw', title: '資金最集中的環節' },
  inst: { kind: 'flow', market: 'tw', title: '法人買賣超最大的環節' },
  us:   { kind: 'share', market: 'us', title: '資金最集中的環節' }
};
const FM = [1, 1, 1];   // 法人金額在三個層級各自的最大值（決定顏色深淺）
Object.keys(SER.inst).forEach(k => { const lv = k[0] === 'L' ? 0 : k[0] === 'N' ? 1 : k[0] === 'S' ? 2 : -1; if (lv >= 0) SER.inst[k].forEach(v => { if (v != null && Math.abs(v) > FM[lv]) FM[lv] = Math.abs(v); }); });
function V(id, key, t) {
  const a = SER[id][key]; if (!a) return null;
  const r = a[Math.round(t)]; if (r == null) return null;
  const i = Math.floor(t), v0 = a[i], v1 = a[Math.min(N - 1, i + 1)];
  return v0 == null || v1 == null ? r : v0 + (v1 - v0) * (t - i);
}
const isZero = (m, v) => v == null || Math.abs(v) < (m.kind === 'share' ? .05 : .5);
const fmtVal = (m, v, bare) => v == null ? '—' : m.kind === 'share' ? v.toFixed(1) + '%' : (v > 0 ? '+' : v < 0 ? '−' : '') + nf0.format(Math.abs(Math.round(v))) + (bare ? '' : ' 億');
const fmtRaw = (m, v) => v == null ? '' : m.market === 'tw' ? `成交 ${nf0.format(Math.round(v))} 億` : v >= 1000 ? `成交約 ${(v / 1000).toFixed(2)} 兆美元` : `成交約 ${nf0.format(Math.round(v * 10))} 億美元`;

/* ---------- 狀態 ---------- */
// row：展開的那一層（-1＝全景）；node：展開的環節；stock：選到的個股
const S = { metric: 'tw', pos: N - 1, posT: N - 1, mi: -1, playing: false, scrubbing: false, row: -1, node: null, stock: null, hover: null, press: null };
const level = () => S.stock ? 3 : S.node ? 2 : S.row >= 0 ? 1 : 0;
const PLAY_MS = 420, PEEK = 64, TUCK = 18;
let vw = 0, vh = 0, DPR = 1, PAL = null, FONT_SANS = 'sans-serif', FONT_MONO = 'monospace', sheetState = 'hidden', bootAt = 0;

/* ---------- 顏色 ---------- */
function parseColor(str) {
  str = String(str).trim();
  if (str[0] === '#') { if (str.length === 4) str = '#' + str[1] + str[1] + str[2] + str[2] + str[3] + str[3]; const v = parseInt(str.slice(1, 7), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; }
  const m = str.match(/[\d.]+/g) || [0, 0, 0]; return [+m[0], +m[1], +m[2]];
}
function readTheme() {
  const cs = getComputedStyle(document.documentElement), g = n => parseColor(cs.getPropertyValue(n));
  const ink = g('--ink');
  PAL = { dark: ink[0] > 128, ink, dim: g('--dim'), faint: g('--faint'), slab: g('--panel'), well: g('--panel2'), edge: g('--edge'), accent: g('--accent'), amber: g('--amber'), us: g('--us'), up: g('--up'), down: g('--down'), stone: g('--stone'), shade: g('--shade') };
  FONT_SANS = cs.getPropertyValue('--sans').trim() || 'sans-serif'; FONT_MONO = cs.getPropertyValue('--mono').trim() || 'monospace';
}
const REF = [30, 14, 6];   // 占比到多少算「最深的顏色」：層、環節、個股各自一把尺
function colorFor(id, v, lv) {
  const P = PAL, m = METRICS[id], pale = mix(P.stone, P.slab, P.dark ? .1 : .45);
  if (v == null || Math.abs(v) < (m.kind === 'share' ? .05 : .5)) return pale;
  if (m.kind === 'share') {
    const u = clamp(v / REF[lv], 0, 1), hi = m.market === 'us' ? P.us : P.accent, mid = m.market === 'us' ? mix(P.stone, P.us, .55) : P.amber;
    const c = u < .42 ? mix(P.stone, mid, u / .42) : mix(mid, hi, (u - .42) / .58);
    return mix(c, P.slab, P.dark ? 0 : clamp(.5 - u * 1.9, 0, .5));
  }
  const u = clamp(Math.sqrt(Math.abs(v) / FM[lv]) * 1.25, 0, 1);
  return mix(pale, v > 0 ? P.up : P.down, .22 + .78 * u);
}
const onDark = c => c[0] * .3 + c[1] * .59 + c[2] * .11 < 158;

/* ---------- 版面：把一段長度照比例分給每一格，太小的給最小尺寸 ---------- */
function fitSizes(ws, total, min, zmin) {
  const n = ws.length, out = new Array(n).fill(0), fixed = new Array(n).fill(false);
  let rest = total, wrest = 0;
  ws.forEach((w, i) => { if (w > 0) wrest += w; else { out[i] = zmin; fixed[i] = true; rest -= zmin; } });
  if (wrest <= 0) return out.fill(total / n);
  for (let pass = 0; pass < n; pass++) {
    let changed = false;
    for (let i = 0; i < n; i++) if (!fixed[i] && ws[i] / wrest * rest < min) { out[i] = min; fixed[i] = true; rest -= min; wrest -= ws[i]; changed = true; }
    if (!changed) break;
  }
  for (let i = 0; i < n; i++) if (!fixed[i]) out[i] = wrest > 0 ? ws[i] / wrest * rest : 0;
  const sum = out.reduce((a, b) => a + b, 0); if (sum > total + .5) for (let i = 0; i < n; i++) out[i] *= total / sum;
  return out;
}
const GAP = 7, HEAD = 30, HEAD2 = 26, INSET = 4, CGAP = 4, CSTRIP = 26, MINCELL = 30, TGAP = 3, MINTILE = 20;
const view = $('#view'), cv = $('#cv'), ctx = cv.getContext('2d');
function mapArea() {
  const wide = WIDE.matches, padX = wide ? 6 : 12, w = Math.min(vw - padX * 2, 780);
  return { x: (vw - w) / 2, y: 6, w, h: vh - 6 - 16 - (!wide && sheetState !== 'hidden' ? PEEK : 0) };
}
const ents = new Map();
function ent(key, type, id, parent, dir, i, n) {
  let e = ents.get(key);
  if (!e) {
    e = { key, type, id, x: 0, y: 0, w: 0, h: 0, a: 0, c: null, open: 0, press: 0, t: {}, fresh: true };
    if (parent) {   // 新切出來的格子：從上一層那一格目前的位置、照順序切開的地方長出來
      if (dir === 'h') { e.x = parent.x + parent.w * i / n; e.w = parent.w / n; e.y = parent.y; e.h = parent.h; }
      else { e.y = parent.y + parent.h * i / n; e.h = parent.h / n; e.x = parent.x; e.w = parent.w; }
      e.c = parent.c ? parent.c.slice() : null; e.fresh = false;
    }
    ents.set(key, e);
  }
  e.live = true; return e;
}
function aim(e, x, y, w, h, c, open) { const t = e.t; t.x = x; t.y = y; t.w = w; t.h = h; t.a = 1; t.c = c; t.open = open; if (!e.c) e.c = c.slice(); }
function targets() {
  ents.forEach(e => { e.live = false; });
  const t = S.pos, id = S.metric, m = METRICS[id], A = mapArea(), lv = level(), wide = WIDE.matches;
  const strip = lv >= 2 ? (wide ? 12 : 9) : (wide ? 22 : 17);
  const vals = LAYERS.map(L => V(id, 'L:' + L.id, t));
  let hs;
  if (S.row < 0) hs = fitSizes(vals.map(v => isZero(m, v) ? 0 : Math.abs(v)), A.h - GAP * (ROWS - 1), wide ? 26 : 22, wide ? 20 : 16);
  else { hs = vals.map(() => strip); hs[S.row] = A.h - GAP * (ROWS - 1) - strip * (ROWS - 1); }
  let y = A.y;
  LAYERS.forEach((L, k) => { const e = ent('L:' + L.id, 'L', L.id); e.k = k; e.v = vals[k]; aim(e, A.x, y, A.w, hs[k], colorFor(id, vals[k], 0), S.row === k ? 1 : 0); y += hs[k] + GAP; });
  if (S.row < 0) return;
  const L = LAYERS[S.row], pe = ents.get('L:' + L.id), R = { x: pe.t.x + INSET, y: pe.t.y + HEAD, w: pe.t.w - INSET * 2, h: pe.t.h - HEAD - INSET };
  const nv = L.nodes.map(n => V(id, 'N:' + n.id, t)), cnt = L.nodes.length, ni = S.node ? L.nodes.findIndex(n => n.id === S.node) : -1;
  let ws;
  if (ni < 0) ws = fitSizes(nv.map(v => isZero(m, v) ? 0 : Math.abs(v)), R.w - CGAP * (cnt - 1), MINCELL, MINCELL);
  else { ws = nv.map(() => CSTRIP); ws[ni] = R.w - CGAP * (cnt - 1) - CSTRIP * (cnt - 1); }
  let x = R.x;
  L.nodes.forEach((n, i) => { const e = ent('N:' + n.id, 'N', n.id, pe, 'h', i, cnt); e.v = nv[i]; aim(e, x, R.y, ws[i], R.h, colorFor(id, nv[i], 1), ni === i ? 1 : 0); x += ws[i] + CGAP; });
  if (ni < 0) return;
  const nd = L.nodes[ni], ne = ents.get('N:' + nd.id), Q = { x: ne.t.x + INSET, y: ne.t.y + HEAD2, w: ne.t.w - INSET * 2, h: ne.t.h - HEAD2 - INSET };
  const list = stocksOf(nd, m.market), sv = list.map(s => V(id, 'S:' + s.code, t));
  const hh = fitSizes(list.map((s, i) => s.owner === nd.id ? Math.abs(sv[i] || 0) : 0), Q.h - TGAP * (list.length - 1), MINTILE, MINTILE);
  let yy = Q.y;
  list.forEach((s, i) => { const e = ent('S:' + nd.id + ':' + m.market + ':' + s.code, 'S', s.code, ne, 'v', i, list.length); e.v = sv[i]; e.own = s.owner === nd.id; e.stock = s; aim(e, Q.x, yy, Q.w, hh[i], colorFor(id, e.own ? sv[i] : null, 2), 0); yy += hh[i] + TGAP; });
}

/* ---------- 每一格的運算 ---------- */
let running = false, lastT = 0;
function kick() { if (!running) { running = true; lastT = performance.now(); requestAnimationFrame(frame); } }
function frame(now) {
  const dt = Math.min(48, now - lastT); lastT = now;
  const busy = step(dt, now); draw(); syncHead();
  if (busy) requestAnimationFrame(frame); else running = false;
}
function step(dt, now) {
  let busy = false;
  if (S.playing) { S.pos += dt / PLAY_MS; if (S.pos >= N - 1) { S.pos = N - 1; setPlaying(false); } S.posT = S.pos; busy = true; }
  else if (!S.scrubbing) { if (Math.abs(S.posT - S.pos) > .002) { S.pos += (S.posT - S.pos) * (1 - Math.exp(-dt / 90)); busy = true; } else S.pos = S.posT; }
  const mi = Math.round(S.pos); if (mi !== S.mi) { S.mi = mi; onMonth(); }
  targets();
  const fast = S.playing || S.scrubbing, kp = 1 - Math.exp(-dt / (fast ? 70 : 150)), ka = 1 - Math.exp(-dt / 110), kc = 1 - Math.exp(-dt / 170), kq = 1 - Math.exp(-dt / 55);
  ents.forEach((e, key) => {
    const t = e.t;
    if (!e.live) t.a = 0;
    if (e.fresh) {   // 第一次出現（進場）：由上游到下游一格一格浮上來
      const wait = REDUCED ? 0 : bootAt + (e.k || 0) * 38;
      e.x = t.x; e.w = t.w; e.h = t.h; e.open = t.open;
      if (now < wait) { e.y = t.y + 14; e.a = 0; busy = true; return; }
      e.y = t.y + (REDUCED ? 0 : 14); e.fresh = false;
    }
    for (const p of ['x', 'y', 'w', 'h']) { const d = t[p] - e[p]; if (Math.abs(d) > .08) { e[p] += d * kp; busy = true; } else e[p] = t[p]; }
    { const d = t.a - e.a; if (Math.abs(d) > .012) { e.a += d * ka; busy = true; } else e.a = t.a; }
    { const d = (t.open || 0) - e.open; if (Math.abs(d) > .012) { e.open += d * ka; busy = true; } else e.open = t.open || 0; }
    { const tp = S.press === key ? 1 : 0, d = tp - e.press; if (Math.abs(d) > .02) { e.press += d * kq; busy = true; } else e.press = tp; }
    if (t.c) for (let i = 0; i < 3; i++) { const d = t.c[i] - e.c[i]; if (Math.abs(d) > .6) { e.c[i] += d * kc; busy = true; } else e.c[i] = t.c[i]; }
    if (!e.live && e.a <= 0) ents.delete(key);
  });
  return busy;
}

/* ---------- 畫 ---------- */
function rr(x, y, w, h, r) { r = Math.max(0, Math.min(r, w / 2, h / 2)); ctx.beginPath(); ctx.roundRect(x, y, Math.max(0, w), Math.max(0, h), r); }
function label(str, x, y, font, color, align) { ctx.font = font; ctx.fillStyle = color; ctx.textAlign = align || 'left'; ctx.textBaseline = 'middle'; ctx.fillText(str, x, y); }
const fitCache = new Map();
function fitText(str, maxW, font) {   // 放不下就截短加「…」
  const key = str + '|' + (maxW | 0) + '|' + font; let r = fitCache.get(key); if (r != null) return r;
  ctx.font = font; r = str;
  if (ctx.measureText(str).width > maxW) { let s = str; while (s.length > 1 && ctx.measureText(s + '…').width > maxW) s = s.slice(0, -1); r = s.length > 1 ? s + '…' : ''; }
  if (fitCache.size > 4000) fitCache.clear(); fitCache.set(key, r); return r;
}
// 有厚度的一格：落影、下緣的厚度、上面、亮邊；按下去會沉
function block(e, r) {
  const P = PAL, th0 = e.h < 14 ? 2 : e.h < 30 ? 3.5 : 5, th = th0 * (1 - .8 * e.press), dy = th0 * .8 * e.press, hov = S.hover === e.key ? .07 : 0;
  const c = hov ? mix(e.c, [255, 255, 255], hov) : e.c, rad = Math.min(9, e.h / 2.4, e.w / 2.4);
  if (e.h > 12) { ctx.save(); ctx.shadowColor = rgb(P.shade, P.dark ? .5 : .2); ctx.shadowBlur = 12 * DPR; ctx.shadowOffsetY = 7 * DPR; rr(r.x, r.y + dy + th, r.w, r.h, rad); ctx.fillStyle = rgb(mix(c, P.dark ? [0, 0, 0] : P.shade, .3)); ctx.fill(); ctx.restore(); }
  else { rr(r.x, r.y + dy + th, r.w, r.h, rad); ctx.fillStyle = rgb(mix(c, P.dark ? [0, 0, 0] : P.shade, .3)); ctx.fill(); }
  rr(r.x, r.y + dy, r.w, r.h, rad); ctx.fillStyle = rgb(c); ctx.fill();
  if (e.h > 12) { rr(r.x + .5, r.y + dy + .5, r.w - 1, r.h - 1, rad); ctx.strokeStyle = P.dark ? 'rgba(255,255,255,.12)' : 'rgba(255,255,255,.42)'; ctx.lineWidth = 1; ctx.stroke(); }
  return dy;
}
function dashed(r) { rr(r.x + .5, r.y + .5, r.w - 1, r.h - 1, Math.min(7, r.h / 2.4)); ctx.setLineDash([4, 3]); ctx.strokeStyle = rgb(PAL.ink, .24); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]); }
function well(r, alpha) {   // 展開後的那一格：變成一個凹槽，裡面放切出來的小格
  const P = PAL; ctx.save(); ctx.globalAlpha *= alpha;
  rr(r.x, r.y, r.w, r.h, 11); ctx.fillStyle = rgb(P.well); ctx.fill();
  rr(r.x + .5, r.y + .5, r.w - 1, r.h - 1, 11); ctx.strokeStyle = rgb(P.shade, P.dark ? .6 : .13); ctx.lineWidth = 1; ctx.stroke(); ctx.restore();
}
function chevron(x, y, color) { ctx.beginPath(); ctx.moveTo(x - 5, y + 2.5); ctx.lineTo(x, y - 2.5); ctx.lineTo(x + 5, y + 2.5); ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.stroke(); }
function spark(key, x, y, w, h, color) {   // 大格子裡淡淡的 70 個月走勢，圓點是現在這個月
  const a = SER[S.metric][key]; if (!a) return; let mx = 0; for (const v of a) if (v != null && Math.abs(v) > mx) mx = Math.abs(v); if (!mx) return;
  ctx.beginPath(); let started = false;
  for (let i = 0; i < N; i++) { if (a[i] == null) continue; const px = x + i / (N - 1) * w, py = y + h - Math.abs(a[i]) / mx * h; if (started) ctx.lineTo(px, py); else { ctx.moveTo(px, py); started = true; } }
  ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.lineJoin = 'round'; ctx.stroke();
  const v = V(S.metric, key, S.pos); if (v != null) { ctx.beginPath(); ctx.arc(x + S.pos / (N - 1) * w, y + h - Math.abs(v) / mx * h, 2.6, 0, 6.3); ctx.fillStyle = color; ctx.fill(); }
}
function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, vw, vh);
  const P = PAL, id = S.metric, m = METRICS[id], lv = level();
  const all = [...ents.values()], by = t => all.filter(e => e.type === t);
  const sans = (w, px) => `${w} ${px}px ${FONT_SANS}`, mono = (w, px) => `${w} ${px}px ${FONT_MONO}`;
  const signCol = (v, dark) => m.kind === 'flow' && v ? (dark ? '#fff' : rgb(v > 0 ? P.up : P.down)) : null;

  // 第一層：12 層
  for (const e of by('L')) {
    if (e.a <= .01) continue; ctx.globalAlpha = e.a;
    const L = LAYERS[e.k], r = { x: e.x, y: e.y, w: e.w, h: e.h }, name = e.h > 44 ? L.name : (L.short || L.name), nodata = e.v == null, zero = !nodata && isZero(m, e.v);
    if (e.open > .02) well(r, e.open);
    if (e.open < .98) {
      ctx.save(); ctx.globalAlpha = e.a * (1 - e.open);
      if (nodata || zero) { dashed(r); if (e.h >= 13) label(name, r.x + 13, r.y + r.h / 2 + .5, sans(500, e.h < 20 ? 10.5 : 11.5), rgb(P.faint)); }
      else {
        const dy = block(e, r), dark = onDark(e.c), fg = dark ? '#fff' : rgb(P.ink), big = e.h > 44, small = e.h < 20, cy = r.y + dy + r.h / 2 + .5;
        if (e.h >= 13) {
          const ta = clamp((e.h - 12) / 4, 0, 1); ctx.globalAlpha = e.a * (1 - e.open) * ta;
          const val = fmtVal(m, e.v, true), vf = mono(600, big ? Math.min(26, 14 + e.h * .13) : small ? 10.5 : 12.5);
          ctx.font = vf; const vwid = ctx.measureText(val).width;
          if (big && r.w > 300 && e.h >= 54) spark('L:' + L.id, r.x + r.w * .42, r.y + dy + 12, r.w * .58 - vwid - 34, r.h - 24, dark ? 'rgba(255,255,255,.4)' : rgb(P.ink, .28));
          label(fitText(name, r.w - vwid - 38, sans(700, big ? 15 : small ? 11 : 12.5)), r.x + 14, cy, sans(700, big ? 15 : small ? 11 : 12.5), fg);
          label(val, r.x + r.w - 14, cy, vf, signCol(e.v, dark) || fg, 'right');
        }
      }
      ctx.restore();
    }
    if (e.open > .3) {   // 展開後的標題列：名稱、數字、收合的箭頭
      ctx.save(); ctx.globalAlpha = e.a * clamp((e.open - .3) / .5, 0, 1);
      const cy = r.y + HEAD / 2 + 1, val = fmtVal(m, e.v);
      label(L.name, r.x + 12, cy, sans(700, 14), rgb(P.ink)); chevron(r.x + r.w - 17, cy, rgb(P.dim));
      label(val, r.x + r.w - 34, cy, mono(600, 13.5), signCol(e.v, false) || rgb(P.ink), 'right');
      ctx.restore();
    }
  }
  // 第二層：環節
  for (const e of by('N')) {
    if (e.a <= .01) continue; ctx.globalAlpha = e.a;
    const n = BY[e.id], r = { x: e.x, y: e.y, w: e.w, h: e.h }, listed = (n[m.market] || []).length > 0, nodata = !listed || isZero(m, e.v);
    if (e.open > .02) well(r, e.open);
    if (e.open < .98) {
      ctx.save(); ctx.globalAlpha = e.a * (1 - e.open);
      let dy = 0, dark = false;
      if (nodata) dashed(r); else { dy = block(e, r); dark = onDark(e.c); }
      const fg = nodata ? rgb(P.faint) : dark ? '#fff' : rgb(P.ink), val = nodata ? '' : fmtVal(m, e.v, true);
      ctx.save(); rr(r.x, r.y + dy, r.w, r.h, 9); ctx.clip();
      if (r.w >= 62 && r.h >= 40) {
        const nf = sans(700, 12.5), big = r.w >= 96 && r.h >= 74;
        label(fitText(short(n), r.w - 20, nf), r.x + 11, r.y + dy + 18, nf, fg);
        if (val) label(val, r.x + 11, r.y + dy + (big ? 44 : 38), mono(600, big ? 20 : 13), signCol(e.v, dark) || fg);
        if (!nodata && big && r.h >= 120 && r.w >= 110) spark('N:' + n.id, r.x + 12, r.y + dy + r.h - 46, r.w - 24, 32, dark ? 'rgba(255,255,255,.42)' : rgb(P.ink, .28));
      } else if (r.h >= 34) {   // 很窄的一格：字轉 90 度直著寫
        ctx.translate(r.x + r.w / 2 + .5, r.y + dy + 10); ctx.rotate(Math.PI / 2);
        const f = sans(600, 11); label(fitText(short(n) + (val ? '  ' + val : ''), r.h - 20, f), 0, 0, f, fg);
      }
      ctx.restore(); ctx.restore();
    }
    if (e.open > .3) {
      ctx.save(); ctx.globalAlpha = e.a * clamp((e.open - .3) / .5, 0, 1);
      const cy = r.y + HEAD2 / 2 + 1, val = fmtVal(m, e.v), vf = mono(600, 12.5); ctx.font = vf; const vwid = ctx.measureText(val).width;
      label(fitText(n.name, r.w - vwid - 58, sans(700, 13)), r.x + 10, cy, sans(700, 13), rgb(P.ink)); chevron(r.x + r.w - 15, cy, rgb(P.dim));
      label(val, r.x + r.w - 30, cy, vf, signCol(e.v, false) || rgb(P.ink), 'right');
      ctx.restore();
    }
  }
  // 第三層：個股
  for (const e of by('S')) {
    if (e.a <= .01) continue; ctx.globalAlpha = e.a * (S.stock && S.stock !== e.id ? .62 : 1);
    const s = e.stock, r = { x: e.x, y: e.y, w: e.w, h: e.h }, ghost = !e.own || isZero(m, e.v);
    let dy = 0, dark = false;
    if (ghost) dashed(r); else { dy = block(e, r); dark = onDark(e.c); }
    const fg = ghost ? rgb(P.faint) : dark ? '#fff' : rgb(P.ink), cy = r.y + dy + r.h / 2 + .5, small = r.h < 24;
    if (r.h >= 13) {
      const val = e.v == null || !e.own ? '' : fmtVal(m, e.v, true), vf = mono(500, small ? 10.5 : 12); ctx.font = vf; const vwid = val ? ctx.measureText(val).width : 0;
      const cf = mono(600, small ? 10.5 : 11.5); ctx.font = cf; const cw = ctx.measureText(s.code).width;
      label(s.code, r.x + 10, cy, cf, fg);
      label(fitText(s.name, r.w - cw - vwid - 42, sans(700, small ? 11 : 12.5)), r.x + 10 + cw + 9, cy, sans(700, small ? 11 : 12.5), fg);
      if (val) label(val, r.x + r.w - 10, cy, vf, signCol(e.v, dark) || fg, 'right');
    }
    if (S.stock === e.id) { ctx.globalAlpha = e.a; rr(r.x - 2, r.y + dy - 2, r.w + 4, r.h + 4, 10); ctx.strokeStyle = rgb(P.ink); ctx.lineWidth = 1.8; ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
}

/* ---------- 點選 ---------- */
function hit(mx, my) {
  const inside = (e, pad) => e.a > .5 && mx >= e.x && mx <= e.x + e.w && my >= e.y - pad && my <= e.y + e.h + pad;
  const all = [...ents.values()].filter(e => e.live);
  for (const e of all) if (e.type === 'S' && inside(e, 1.5)) return { e, what: 'stock' };
  for (const e of all) if (e.type === 'N' && inside(e, 0)) return e.t.open ? (my < e.y + HEAD2 ? { e, what: 'nodeHead' } : null) : { e, what: 'node' };
  let best = null, bd = 1e9;
  for (const e of all) if (e.type === 'L' && inside(e, 2.6)) { const d = Math.abs(my - (e.y + e.h / 2)); if (d < bd) { bd = d; best = e; } }
  if (best) return best.t.open ? (my < best.y + HEAD ? { e: best, what: 'layerHead' } : null) : { e: best, what: 'layer' };
  return null;
}
let down = null;
const local = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
cv.addEventListener('pointerdown', e => { const [x, y] = local(e), h = hit(x, y); down = { id: e.pointerId, x: e.clientX, y: e.clientY, key: h ? h.e.key : null }; S.press = h && h.what !== 'layerHead' && h.what !== 'nodeHead' ? h.e.key : null; kick(); });
cv.addEventListener('pointermove', e => {
  if (down && down.id === e.pointerId && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 10) { S.press = null; down.moved = true; kick(); }
  if (e.pointerType === 'mouse') { const [x, y] = local(e), h = hit(x, y), key = h ? h.e.key : null; cv.classList.toggle('is-pointing', !!h); if (key !== S.hover) { S.hover = key; kick(); } }
});
function release(e, cancelled) {
  if (!down || down.id !== e.pointerId) return; const d = down; down = null; S.press = null; kick();
  if (cancelled || d.moved) return;
  const [x, y] = local(e), h = hit(x, y); if (!h) return;
  if (h.what === 'layer') openLayer(h.e.k); else if (h.what === 'layerHead') goHome();
  else if (h.what === 'node') openNode(h.e.id); else if (h.what === 'nodeHead') openLayer(S.row);
  else if (h.what === 'stock') pickStock(h.e.id);
}
cv.addEventListener('pointerup', e => release(e, false));
cv.addEventListener('pointercancel', e => release(e, true));
cv.addEventListener('pointerleave', () => { if (S.hover) { S.hover = null; cv.classList.remove('is-pointing'); kick(); } });
document.addEventListener('gesturestart', e => e.preventDefault(), { passive: false });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && level() && !e.target.closest('input')) up(); });

/* ---------- 一層一層展開、收合 ---------- */
function afterNav(fromList) {
  const lv = level(), wide = WIDE.matches;
  renderPanel();
  if (wide) slot.classList.toggle('on', lv >= 1);
  else { setSheet(lv >= 2 ? (sheetState === 'open' ? 'open' : 'peek') : 'hidden'); if (fromList) window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' }); }
  markSel(); updateWave(); kick();
}
function goHome() { S.row = -1; S.node = null; S.stock = null; afterNav(); }
function openLayer(k) { S.row = k; S.node = null; S.stock = null; afterNav(); }
function openNode(id, fromList) { const n = BY[id]; if (!n) return; S.row = n.row; S.node = id; S.stock = null; afterNav(fromList); }
function pickStock(code, fromList) { if (S.stock === code) { if (!WIDE.matches) setSheet(sheetState === 'open' ? 'peek' : 'open'); return; } S.stock = code; afterNav(fromList); }
function up() { if (S.stock) S.stock = null; else if (S.node) S.node = null; else S.row = -1; afterNav(); }
const scopeKey = () => S.stock ? 'S:' + S.stock : S.node ? 'N:' + S.node : S.row >= 0 ? 'L:' + LAYERS[S.row].id : 'C';

/* ---------- 月份帶 ---------- */
const track = $('#track'), trackIn = $('#trackIn'), head = $('#head'), monthOut = $('#month'), playBtn = $('#play');
let trackW = 0, trendBox = null, trendMark = null, markW = 0;
function measureTrack() { trackW = trackIn.getBoundingClientRect().width; markW = trendBox ? trendBox.getBoundingClientRect().width : 0; }
function syncHead() {
  head.style.transform = `translate3d(${(S.pos / (N - 1) * trackW).toFixed(1)}px,0,0)`;
  if (trendMark) trendMark.style.transform = `translate3d(${(S.pos / (N - 1) * markW).toFixed(1)}px,0,0)`;
}
function goTo(i) { setPlaying(false); S.posT = clamp(Math.round(i), 0, N - 1); kick(); }
function setPlaying(on) {
  if (on && S.pos >= N - 1 - .01) { S.pos = S.posT = 0; }
  S.playing = on; playBtn.classList.toggle('playing', on); playBtn.setAttribute('aria-label', on ? '暫停' : '播放');
  if (!on) S.posT = Math.round(S.pos);
  kick();
}
function sparkSVG(values, W, H, signed) {
  const n = values.length, x = i => i / (n - 1) * W;
  if (signed) {
    const mx = Math.max(1, ...values.map(v => Math.abs(v || 0))), mid = H / 2, bw = W / n * .7; let s = `<line class="axis" x1="0" x2="${W}" y1="${mid}" y2="${mid}"/>`;
    values.forEach((v, i) => { if (!v) return; const h = Math.max(1, Math.abs(v) / mx * (H / 2 - 1)); s += `<rect class="${v > 0 ? 'up' : 'down'}" x="${(x(i) - bw / 2).toFixed(1)}" y="${(v > 0 ? mid - h : mid).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`; });
    return s;
  }
  const mx = Math.max(1e-9, ...values.map(v => v || 0)); let d = '';
  values.forEach((v, i) => { d += (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + (H - 1 - (v || 0) / mx * (H - 4)).toFixed(1); });
  return `<path class="area" d="${d}L${W} ${H}L0 ${H}Z"/><path class="line" d="${d}"/>`;
}
function updateWave() {
  const flow = METRICS[S.metric].kind === 'flow';
  $('#wave').innerHTML = sparkSVG(SER[S.metric][scopeKey()] || [], 690, 34, flow);
  const v = $('#void'); v.hidden = !flow; if (flow) v.style.width = `calc(10px + ${((I0 - .5) / (N - 1) * 100).toFixed(2)}%)`;
}
function scrubber(node) {
  let on = false;
  node.addEventListener('pointerdown', e => { if (e.button) return; on = true; node.setPointerCapture(e.pointerId); setPlaying(false); S.scrubbing = true; node.classList.add('scrub'); S.pos = S.posT = node._pos(e.clientX); kick(); e.preventDefault(); });
  node.addEventListener('pointermove', e => { if (!on) return; S.pos = S.posT = node._pos(e.clientX); kick(); });
  const end = () => { if (!on) return; on = false; S.scrubbing = false; node.classList.remove('scrub'); S.posT = Math.round(S.pos); kick(); };
  node.addEventListener('pointerup', end); node.addEventListener('pointercancel', end);
}
track._pos = x => { const r = trackIn.getBoundingClientRect(); return clamp((x - r.left) / r.width, 0, 1) * (N - 1); }; scrubber(track);
track.addEventListener('keydown', e => {
  const k = e.key; if (k === 'ArrowLeft' || k === 'ArrowDown') goTo(S.posT - 1); else if (k === 'ArrowRight' || k === 'ArrowUp') goTo(S.posT + 1); else if (k === 'Home') goTo(0); else if (k === 'End') goTo(N - 1); else if (k === ' ') setPlaying(!S.playing); else return;
  e.preventDefault();
});
playBtn.addEventListener('click', () => setPlaying(!S.playing));
$('#prev').addEventListener('click', () => goTo(Math.round(S.posT) - 1));
$('#next').addEventListener('click', () => goTo(Math.round(S.posT) + 1));
(function years() { const box = $('#years'); MONTHS.forEach((m, i) => { if (m.slice(5) === '01') { const s = el('span', '', m.slice(0, 4)); s.style.left = (i / (N - 1) * 100) + '%'; box.append(s); } }); })();

/* ---------- 電腦右欄：全產業鏈前 6 個環節（點了直接展開到那個環節） ---------- */
const rankEls = new Map();
function rankNow() {
  const a = [], id = S.metric; for (const n of NODES) { const s = SER[id]['N:' + n.id], v = s ? s[S.mi] : null; if (v) a.push({ id: n.id, v }); }
  return a.sort((p, q) => Math.abs(q.v) - Math.abs(p.v));
}
function renderRank(animate) {
  const m = METRICS[S.metric], list = WIDE.matches ? rankNow().slice(0, 6) : [], ol = $('#rank'), top = Math.abs(list[0] ? list[0].v : 1);
  $('#rankCard').hidden = !list.length; $('#rankTitle').textContent = m.title; $('#rankMonth').textContent = MONTHS[S.mi];
  const first = new Map(); if (animate) rankEls.forEach((li, id) => first.set(id, li.getBoundingClientRect().top));
  const keep = new Set(list.map(r => r.id)); rankEls.forEach((li, id) => { if (!keep.has(id)) { li.remove(); rankEls.delete(id); } });
  list.forEach((r, i) => {
    let li = rankEls.get(r.id); const n = BY[r.id];
    if (!li) { li = el('li'); li.innerHTML = '<button class="row" type="button"><span class="rk"></span><span class="nm"></span><span class="ly"></span><span class="val"></span><span class="bar"><i></i></span></button>'; $('.nm', li).textContent = n.name; $('.ly', li).textContent = LAYERS[n.row].name; li.firstChild.addEventListener('click', () => openNode(n.id, true)); li._new = true; rankEls.set(r.id, li); }
    $('.rk', li).textContent = i + 1; const val = $('.val', li); val.textContent = fmtVal(m, r.v); if (m.kind === 'flow') val.dataset.sign = r.v >= 0 ? 'up' : 'down'; else delete val.dataset.sign;
    const bar = $('.bar i', li); bar.style.transform = `scaleX(${Math.max(.03, Math.abs(r.v) / top).toFixed(3)})`; bar.style.backgroundColor = rgb(colorFor(S.metric, r.v, 1));
    if (ol.children[i] !== li) ol.insertBefore(li, ol.children[i] || null);
  });
  rankEls.forEach((li, id) => {
    const fresh = li._new; li._new = false; if (!animate || REDUCED) return;
    if (fresh) { li.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, easing: EASE }); return; }
    const d = first.get(id) - li.getBoundingClientRect().top; if (Math.abs(d) > 1) li.animate([{ transform: `translateY(${d}px)` }, { transform: 'none' }], { duration: 320, easing: EASE });
  });
  markSel();
}
const markSel = () => rankEls.forEach((li, k) => li.classList.toggle('sel', k === S.node));

/* ---------- 說明板：手機從月份帶上方浮起，電腦在右欄最上面 ---------- */
const detail = el('article', 'detail');
detail.innerHTML = '<header class="d-head" id="dHead"><span class="grab" aria-hidden="true"></span><div class="d-id"><span class="d-layer" id="dLayer"></span><h2 id="dName"></h2></div><div class="d-num"><b id="dVal"></b><span id="dSub"></span></div><button class="key icon" id="dClose" type="button" aria-label="收合這一層"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg></button></header><div class="d-body" id="dBody"><p class="d-desc" id="dDesc"></p><div class="trend" id="trend"><svg viewBox="0 0 690 64" preserveAspectRatio="none" aria-hidden="true"></svg><div class="mark"></div><div class="ends"><span></span><span></span></div></div><div id="dMore"></div></div>';
const sheet = $('#sheet'), slot = $('#slot');
function mountDetail() {
  (WIDE.matches ? $('#slotIn') : $('#sheetIn')).append(detail); trendBox = $('#trend'); trendMark = $('.mark', trendBox);
  if (!trendBox._pos) { trendBox._pos = x => { const r = trendBox.getBoundingClientRect(); return clamp((x - r.left) / r.width, 0, 1) * (N - 1); }; scrubber(trendBox); }
}
function listHTML(title, rows) {   // 可以點的清單（這一層的環節、這個環節的個股）
  if (!rows.length) return '';
  return `<h3>${title}</h3><ol class="plist">` + rows.map(r => `<li><button class="row" type="button" data-kind="${r.kind}" data-id="${esc(r.id)}" data-key="${esc(r.key)}"${r.off ? ' data-off="1"' : ''}><span class="rk">${esc(r.rk)}</span><span class="nm">${esc(r.name)}${r.tag ? `<em>${esc(r.tag)}</em>` : ''}</span><span class="ly">${esc(r.sub)}</span><span class="val"></span><span class="bar"><i></i></span></button></li>`).join('') + '</ol>';
}
function plainList(title, rows, names, notes, otc) {
  if (!rows || !rows.length) return '';
  return `<h3>${title}</h3><ul class="stocks">` + rows.map(r => `<li><span class="cd">${esc(r[0])}</span><span class="nm">${esc(names[r[0]] || r[0])}${otc && OTC.has(r[0]) ? '<em>櫃</em>' : ''}${notes[r[0]] ? `<em>${esc(notes[r[0]])}</em>` : ''}</span><span class="rl">${esc(r[1])}</span></li>`).join('') + '</ul>';
}
const chips = (title, ids) => ids.length ? `<h3>${title}</h3><div class="chips">` + ids.map(id => `<button class="key" type="button" data-node="${id}">${esc(short(BY[id]))}</button>`).join('') + '</div>' : '';
function renderPanel() {
  const lv = level(); if (!lv) return;
  const m = METRICS[S.metric], mk = m.market; let eyebrow, title, desc = '', more = '';
  if (lv === 1) {
    const L = LAYERS[S.row]; eyebrow = `產業鏈第 ${S.row + 1} 層`; title = L.name;
    more = listHTML('環節', L.nodes.map((n, i) => ({ kind: 'node', id: n.id, key: 'N:' + n.id, rk: i + 1, name: n.name, sub: (n[mk] || []).length ? `${(n[mk] || []).length} 家公司` : (mk === 'tw' ? '沒有台股公司' : '沒有美股公司'), off: !(n[mk] || []).length })));
  } else if (lv === 2) {
    const n = BY[S.node]; eyebrow = LAYERS[n.row].name; title = n.name; desc = n.desc || '';
    more = listHTML(mk === 'tw' ? '台灣' : '美國', stocksOf(n, mk).map(s => ({ kind: 'stock', id: s.code, key: 'S:' + s.code, rk: s.code, name: s.name, tag: (mk === 'tw' && OTC.has(s.code) ? '櫃' : '') , sub: s.owner === n.id ? s.roles[n.id] : `${s.roles[n.id]}；金額算在「${short(BY[s.owner])}」`, off: s.owner !== n.id })))
      + (mk === 'tw' ? plainList('美國', n.us, CHAIN.us, CHAIN.usNote || {}, false) : plainList('台灣', n.tw, CHAIN.tw, CHAIN.twNote || {}, true))
      + (n.other && n.other.length ? '<h3>未上市與其他國家</h3><ul class="stocks other">' + n.other.slice(0, 4).map(r => `<li><span class="nm">${esc(r[0])}</span><span class="rl">${esc(r[1])}</span></li>`).join('') + '</ul>' : '')
      + chips('上游', n.up) + chips('下游', n.down);
  } else {
    const s = STK[mk][S.stock], n = BY[S.node]; eyebrow = n.name; title = s.code + ' ' + s.name; desc = s.roles[n.id] + (s.owner !== n.id ? `。這家公司的金額算在「${BY[s.owner].name}」。` : '。');
    more = chips('也出現在', s.nodes.filter(x => x !== n.id));
  }
  $('#dLayer').textContent = eyebrow; $('#dName').textContent = title; $('#dDesc').textContent = desc; $('#dDesc').hidden = !desc; $('#dMore').innerHTML = more;
  const ends = trendBox.querySelectorAll('.ends span'); ends[0].textContent = MONTHS[0]; ends[1].textContent = MONTHS[N - 1];
  $('svg', trendBox).innerHTML = sparkSVG(SER[S.metric][scopeKey()] || [], 690, 64, m.kind === 'flow');
  updatePanelNumbers(); measureTrack();
  if (!REDUCED) $('#dBody').animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 260, easing: EASE });
  $('#dBody').scrollTop = 0;
}
function updatePanelNumbers() {
  if (!level()) return;
  const id = S.metric, m = METRICS[id], key = scopeKey(), a = SER[id][key], v = a ? a[S.mi] : null, val = $('#dVal'), sub = $('#dSub');
  val.textContent = fmtVal(m, v); if (m.kind === 'flow' && v) val.dataset.sign = v > 0 ? 'up' : 'down'; else delete val.dataset.sign;
  if (m.kind === 'flow') sub.textContent = '外資＋投信';
  else { const raw = RAW[m.market][key], prev = a && S.mi > 0 ? a[S.mi - 1] : null, d = v != null && prev != null ? v - prev : null; sub.innerHTML = fmtRaw(m, raw ? raw[S.mi] : null) + (d != null && Math.abs(d) >= .05 ? `<i class="${d > 0 ? 'up' : 'down'}">${d > 0 ? '▲' : '▼'}${Math.abs(d).toFixed(1)}</i>` : ''); }
  const rows = [...detail.querySelectorAll('.plist .row')], vs = rows.map(b => { const s = SER[id][b.dataset.key]; return b.dataset.off ? null : (s ? s[S.mi] : null); }), top = Math.max(1e-9, ...vs.map(x => Math.abs(x || 0)));
  rows.forEach((b, i) => {
    const x = vs[i], vEl = $('.val', b), bar = $('.bar i', b); vEl.textContent = x == null ? '' : fmtVal(m, x, true);
    if (m.kind === 'flow' && x) vEl.dataset.sign = x > 0 ? 'up' : 'down'; else delete vEl.dataset.sign;
    bar.style.transform = `scaleX(${x ? Math.max(.03, Math.abs(x) / top).toFixed(3) : 0})`; bar.style.backgroundColor = rgb(colorFor(id, x, b.dataset.kind === 'node' ? 1 : 2));
    b.parentNode.classList.toggle('sel', b.dataset.kind === 'stock' && b.dataset.id === S.stock);
  });
}
detail.addEventListener('click', e => {
  const row = e.target.closest('.plist .row'); if (row) { if (row.dataset.kind === 'node') openNode(row.dataset.id); else pickStock(row.dataset.id); return; }
  const chip = e.target.closest('[data-node]'); if (chip) { openNode(chip.dataset.node); return; }
  if (e.target.closest('#dClose')) up();
});
function setSheet(state) { sheetState = state; sheet.dataset.state = state; sheet.style.transform = ''; kick(); }
(function sheetDrag() {
  const headEl = $('#dHead', detail); let d = null;
  const offsetOf = state => { const H = sheet.offsetHeight; return state === 'open' ? 0 : state === 'peek' ? H - PEEK - TUCK : H; };
  headEl.addEventListener('pointerdown', e => { if (WIDE.matches || e.target.closest('button')) return; d = { id: e.pointerId, y0: e.clientY, t0: offsetOf(sheetState), H: sheet.offsetHeight, ly: e.clientY, lt: e.timeStamp, v: 0, moved: false }; headEl.setPointerCapture(e.pointerId); });
  headEl.addEventListener('pointermove', e => {
    if (!d || d.id !== e.pointerId) return; const dy = e.clientY - d.y0;
    if (Math.abs(dy) > 6) d.moved = true; if (!d.moved) return;
    sheet.classList.add('dragging'); sheet.style.transform = `translateY(${clamp(d.t0 + dy, 0, d.H)}px)`;
    const dt = e.timeStamp - d.lt; if (dt > 0) d.v = lerp(d.v, (e.clientY - d.ly) / dt, .6); d.ly = e.clientY; d.lt = e.timeStamp;
  });
  const end = e => {
    if (!d || d.id !== e.pointerId) return; const k = d; d = null; sheet.classList.remove('dragging');
    if (!k.moved) { setSheet(sheetState === 'open' ? 'peek' : 'open'); return; }
    const y = clamp(k.t0 + (e.clientY - k.y0), 0, k.H), peekY = k.H - PEEK - TUCK;
    let next; if (k.v > .45) next = y < peekY - 20 ? 'peek' : 'hidden'; else if (k.v < -.45) next = 'open'; else next = y < peekY / 2 ? 'open' : y < peekY + 30 ? 'peek' : 'hidden';
    if (next === 'hidden') { S.stock = null; S.node = null; afterNav(); } else setSheet(next);
  };
  headEl.addEventListener('pointerup', end); headEl.addEventListener('pointercancel', end);
})();

/* ---------- 指標切換 ---------- */
const seg = $('#seg'), pill = $('#pill');
function setMetric(id) {
  if (id === S.metric) return; const was = METRICS[S.metric].market; S.metric = id;
  seg.style.setProperty('--i', ['tw', 'inst', 'us'].indexOf(id));
  seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.m === id)));
  if (METRICS[id].market !== was) S.stock = null;
  onMonth(); afterNav();
}
seg.addEventListener('click', e => { const b = e.target.closest('button[data-m]'); if (b) setMetric(b.dataset.m); });
pill.addEventListener('click', () => goTo(I0));
function onMonth() {
  const m = METRICS[S.metric]; monthOut.textContent = MONTHS[S.mi];
  track.setAttribute('aria-valuemin', 0); track.setAttribute('aria-valuemax', N - 1); track.setAttribute('aria-valuenow', S.mi); track.setAttribute('aria-valuetext', MONTHS[S.mi]);
  $('#legend').hidden = true;
  const none = m.kind === 'flow' && S.mi < I0; pill.hidden = !none; if (none) pill.textContent = `法人資料自 ${MONTHS[I0]} 起`;
  renderRank(!S.scrubbing); updatePanelNumbers();
}

/* ---------- 搜尋 ---------- */
(function search() {
  const top = $('#top'), q = $('#q'), box = $('#results'), key = $('#searchKey'), index = [];
  NODES.forEach(n => {
    index.push({ label: n.name, sub: LAYERS[n.row].name, node: n.id, text: n.name.toLowerCase() });
    ['tw', 'us'].forEach(mk => stocksOf(n, mk).forEach(s => index.push({ label: s.code + ' ' + s.name, sub: n.name, node: n.id, code: s.code, market: mk, text: (s.code + ' ' + s.name).toLowerCase() })));
    (n.other || []).forEach(r => index.push({ label: r[0], sub: n.name, node: n.id, text: r[0].toLowerCase() }));
  });
  const close = () => { box.hidden = true; if (!WIDE.matches) { top.classList.remove('searching'); q.value = ''; } };
  function go(f) {
    close(); q.blur();
    if (f.market && METRICS[S.metric].market !== f.market) setMetric(f.market === 'us' ? 'us' : 'tw');
    openNode(f.node, true); if (f.code) pickStock(f.code, true);
  }
  function run() {
    const s = q.value.trim().toLowerCase(); if (!s) { box.hidden = true; return; }
    const found = index.filter(x => x.text.includes(s)).slice(0, 12); box.innerHTML = '';
    if (!found.length) box.append(el('div', 'none', '找不到這家公司或這個環節'));
    found.forEach(f => { const b = el('button'); b.type = 'button'; b.append(el('b', '', f.label), el('span', '', f.sub)); b.addEventListener('click', () => go(f)); box.append(b); });
    box.hidden = false;
  }
  q.addEventListener('input', run); q.addEventListener('focus', run);
  q.addEventListener('keydown', e => { if (e.key === 'Escape') { close(); q.blur(); } else if (e.key === 'Enter') { const b = $('button', box); if (b) b.click(); } });
  key.addEventListener('click', () => { if (top.classList.toggle('searching')) q.focus(); else close(); });
  document.addEventListener('pointerdown', e => { if (!e.target.closest('#search')) close(); });
})();

/* ---------- 起動 ---------- */
let lastW = 0;
function layout() {
  const wide = WIDE.matches, dock = $('#dock');
  document.documentElement.style.setProperty('--dock-h', (wide ? 0 : dock.offsetHeight) + 'px');
  if (wide) view.style.height = ''; else view.style.height = clamp(window.innerHeight - (view.getBoundingClientRect().top + window.scrollY) - dock.offsetHeight - 2, 340, 720) + 'px';
  lastW = window.innerWidth;
}
function resize() {
  const r = view.getBoundingClientRect(); if (!r.width || !r.height) return;
  vw = r.width; vh = r.height; DPR = Math.min(window.devicePixelRatio || 1, 2.5);
  cv.width = Math.round(vw * DPR); cv.height = Math.round(vh * DPR); measureTrack(); kick();
}
(FLOWS.notes || []).forEach(t => $('#notes').append(el('li', '', t)));
readTheme(); mountDetail(); layout(); updateWave();
bootAt = performance.now() + 140;
new ResizeObserver(resize).observe(view);
window.addEventListener('resize', () => { if (Math.abs(window.innerWidth - lastW) > 2) layout(); });
WIDE.addEventListener('change', () => { mountDetail(); layout(); afterNav(); renderRank(false); resize(); });
const themeChanged = () => { readTheme(); renderRank(false); updatePanelNumbers(); kick(); };
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', themeChanged);
new MutationObserver(themeChanged).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
resize();
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { readTheme(); fitCache.clear(); layout(); resize(); });
// 測試用（比照首頁的 window.HomeView）：只讀狀態與每一格現在的目標位置（畫布座標），不改任何東西
window.ChainMap = {
  snapshot: () => ({
    ready: true, level: level(), metric: S.metric, row: S.row, node: S.node, stock: S.stock, mi: S.mi, month: MONTHS[S.mi], months: N, inst0: I0,
    pos: S.pos, playing: S.playing, scrubbing: S.scrubbing, sheet: sheetState, wide: WIDE.matches,
    cells: [...ents.values()].filter(e => e.live).map(e => ({ key: e.key, type: e.type, id: e.id, x: e.t.x, y: e.t.y, w: e.t.w, h: e.t.h, open: e.t.open || 0, v: e.v == null ? null : e.v,
      at: { x: e.x, y: e.y, w: e.w, h: e.h, a: e.a } })),
    series: (metric, key) => (SER[metric] && SER[metric][key] ? SER[metric][key].slice() : null)
  })
};
}

/* ---------- 資料：/api/chain-map（開關關著、不是站主時這一頁本身就是 404，走不到這裡） ---------- */
const EMPTY = { none: '資金版圖尚未就緒：還沒有已建立的月資料。', error: '資金版圖暫時讀不到，請稍後再試。' };
function showEmpty(kind) {
  ['#seg', '#search', '#dock', '.side'].forEach(s => { const n = document.querySelector(s); if (n) n.hidden = true; });
  const p = document.createElement('p'); p.className = 'cm-empty'; p.setAttribute('role', 'status'); p.textContent = EMPTY[kind];
  document.getElementById('view').append(p);
  window.ChainMap = { snapshot: () => ({ ready: false, empty: kind }) };
}
fetch('/api/chain-map', { credentials: 'same-origin', cache: 'no-store', headers: { accept: 'application/json' } })
  .then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
  .then(d => { if (d && d.available && d.chain && d.flows) boot(d.chain, d.flows); else showEmpty('none'); },
    error => { console.warn('chain-map:', error && error.message); showEmpty('error'); });
})();
