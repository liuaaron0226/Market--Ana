/* 大戶持股的畫面（立體重設計，本人 2026-11-15 定案的 ⑥）：一句結論、這一週的數字、每週增減的小柱；點開看三個級距。
   資料由 holders-weekly.js 讀好（HoldersWeekly.read），這裡只畫，不另外算。樣式在 stock.css〈大戶持股〉，那一排鍵的樣式在 bold.css（.key-strip）。

   用法：HoldersCard.render(host, model)
   - host：個股頁的 #bigHolders。model：HoldersWeekly.read 的結果；null 表示沒有資料，整塊不顯示。
   同一份資料重複呼叫不會重畫；資料換了重畫時，開著的狀態與選中的級距照原樣留著。
   進場的動態只播一次，而且等捲到這一塊才播；只有一週（沒有小柱）或系統設定「減少動態」時沒有。
   那一排鍵「選一個」的滑動（選中的框跟著手指走）由 chart-menus.js 處理，這裡只管點了之後的事。 */
(function () {
  'use strict';
  const STILL = window.matchMedia('(prefers-reduced-motion: reduce)');
  // 進場的時間表（毫秒）：第一根什麼時候開始、一根比一根晚多少、整張定位要多久；每長一根整張被頂或壓一下
  const FIRST = 200, GAP = 110, SETTLE_MS = 700, BUMP_PX = 2;
  const STEADY_MS = 400;   // 進到畫面之後要停穩這麼久才播進場（載入時版面還在動）
  const TONES = { bull: 'up', bear: 'down' };   // 數字的顏色沿用全站的漲跌色
  const WORDS = { bull: '這一週增加', bear: '這一週減少', none: '這一週沒變' };
  const esc = value => String(value).replace(/[&<>"']/g, mark => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[mark]));
  // 鍵上的名稱：字之間的空白不換行
  const keyName = name => esc(name).replace(/ /g, '&nbsp;');

  const shown = new WeakMap();   // 每一塊現在畫的是哪一份資料：{ key, model, entered }

  const barsHtml = level => level.bars.map((bar, index) => `<span class="col ${bar.kind}" style="--size:${bar.size.toFixed(3)};--at:${FIRST + index * GAP}ms"><i></i></span>`).join('');

  // 上面的數字、小柱與下面那一行換成某一個級距
  function showLevel(host, index) {
    const level = shown.get(host).model.levels[index], card = host.querySelector('.hold-card');
    card.querySelector('.hold-label').textContent = level.label;
    card.querySelector('.hold-value').textContent = level.value;
    const change = card.querySelector('.hold-change');
    change.hidden = level.change === null;
    change.textContent = level.change || '';
    change.className = `hold-change ${TONES[level.kind] || ''}`.trim();
    // 小柱：根數一樣就原地換長短與方向（會滑過去），不一樣才重畫
    const bars = card.querySelector('.hold-bars'), cols = [...bars.children];
    if (cols.length === level.bars.length) {
      cols.forEach((col, order) => { col.className = `col ${level.bars[order].kind}`; col.style.setProperty('--size', level.bars[order].size.toFixed(3)); });
    } else bars.innerHTML = barsHtml(level);
    bars.hidden = !level.bars.length;
    card.querySelector('.hold-read').textContent = level.read;
    card.querySelectorAll('.cell').forEach(cell => cell.setAttribute('aria-pressed', String(Number(cell.dataset.level) === index)));
  }

  // 收起來的鍵不該被鍵盤走到
  function setOpen(host, open) {
    const card = host.querySelector('.hold-card');
    card.classList.toggle('is-open', open);
    card.querySelector('.hold-top').setAttribute('aria-expanded', String(open));
    card.querySelector('.hold-panel').inert = !open;
  }

  function onClick(host, event) {
    if (event.target.closest('.hold-top')) { setOpen(host, !host.querySelector('.hold-card').classList.contains('is-open')); return; }
    const cell = event.target.closest('.cell');
    if (cell) showLevel(host, Number(cell.dataset.level));   // 一定有一顆選著：再點同一顆不取消
  }

  // 進場：等捲到這一塊才播一次。還沒播之前小柱先藏著（.is-waiting），播的時候樣式照時間表帶（.is-entering）
  function enter(host) {
    const state = shown.get(host), card = host.querySelector('.hold-card'), bars = state.model.levels[0].bars;
    if (state.entered || STILL.matches || !bars.length || typeof IntersectionObserver !== 'function') { state.entered = true; return; }
    state.entered = true;
    const settleAt = FIRST + bars.length * GAP;
    card.style.setProperty('--settle-at', `${settleAt}ms`);
    card.style.setProperty('--hold-settle', `${SETTLE_MS}ms`);   // 整張定位要多久：樣式跟著這個值，不各寫一份
    card.classList.add('is-waiting');
    const play = () => {
      card.classList.replace('is-waiting', 'is-entering');
      bars.forEach((bar, index) => {
        if (bar.kind !== 'none') card.animate({ translate: ['0 0', `0 ${bar.kind === 'bull' ? -BUMP_PX : BUMP_PX}px`, '0 0'], offset: [0, .3, 1] }, { duration: GAP - 10, delay: FIRST + index * GAP + 120, easing: 'ease-out' });
      });
      setTimeout(() => card.classList.remove('is-entering'), settleAt + SETTLE_MS + 200);
    };
    // 個股頁剛載入時版面還在排：這一張會先短暫出現在畫面裡，隨即被上面的內容推到畫面下方。
    // 所以進到畫面之後先等一下，確認還在畫面裡才播；被推走了就繼續等下一次進來
    const stillThere = () => { const box = card.getBoundingClientRect(); return box.bottom > 0 && box.top < window.innerHeight * .9; };
    let pending = 0;
    const seen = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      clearTimeout(pending);
      pending = setTimeout(() => {
        if (!card.isConnected) { seen.disconnect(); return; }
        if (!stillThere()) return;
        seen.disconnect();
        play();
      }, STEADY_MS);
    }, { rootMargin: '0px 0px -10% 0px' });
    seen.observe(card);
  }

  function render(host, model) {
    const key = JSON.stringify(model), before = shown.get(host);
    if (before && before.key === key) return;
    // 重畫之前記下是不是開著、選的是哪一個級距，畫完照原樣放回去
    const old = host.querySelector('.hold-card');
    const keep = old && { open: old.classList.contains('is-open'), level: (old.querySelector('.cell[aria-pressed="true"]') || { dataset: {} }).dataset.level };
    shown.set(host, { key, model, entered: Boolean(before && before.entered) });
    if (!before) host.addEventListener('click', event => onClick(host, event));
    if (!model) { host.replaceChildren(); return; }
    const cells = model.levels.map((level, index) => {
      const kind = level.kind || 'plain';   // 只有一週時沒有方向可說，鍵上不放記號
      return `<button type="button" class="cell ${kind}" data-level="${index}" aria-pressed="false" style="--order:${index}" aria-label="${esc(level.name)}${WORDS[kind] ? `，${WORDS[kind]}` : ''}"><span class="cell-face"></span><i class="tag"></i><span class="cell-name">${keyName(level.name)}</span></button>`;
    }).join('');
    host.innerHTML = `<section class="sk-card sk-reason sk-membership" aria-labelledby="holdTitle">
      <div class="sk-reason-head"><h2 id="holdTitle">大戶持股</h2><span class="sk-reason-meta">資料日 ${esc(model.date)}</span></div>
      ${model.title ? `<p class="hold-align">${esc(model.title)} <small>${esc(model.note)}</small></p>` : ''}
      <div class="hold-card" data-state="${esc(model.state)}">
        <button type="button" class="hold-top" aria-expanded="false" aria-controls="holdPanel">
          <span class="hold-now"><span class="hold-label"></span><strong class="hold-value"></strong><em class="hold-change"></em></span>
          <span class="hold-bars" aria-hidden="true"></span>
          <span class="chev" aria-hidden="true"></span>
        </button>
        <div class="hold-panel" id="holdPanel" inert><div class="hold-clip"><div class="hold-inner">
          <div class="hold-strip key-strip" role="group" aria-label="三個級距">${cells}</div>
          <p class="hold-read"></p>
        </div></div></div>
      </div>
    </section>`;
    const level = keep && keep.level !== undefined && Number(keep.level) < model.levels.length ? Number(keep.level) : 0;
    showLevel(host, level);
    if (keep && keep.open) setOpen(host, true);
    enter(host);
  }

  window.HoldersCard = { render };
})();
