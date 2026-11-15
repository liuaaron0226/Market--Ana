/* 大戶持股的畫面（立體重設計，本人 2026-11-15 定案的 ⑥）：一句結論、這一週的數字、每週增減的小柱；點開看三個級距。
   資料由 holders-weekly.js 讀好（HoldersWeekly.read），這裡只畫，不另外算。樣式在 stock.css〈大戶持股〉；
   會浮起或凹下的那一張與展開收合在 bold.css（.depth-row），那一排鍵在 bold.css（.key-strip）。

   用法：HoldersCard.render(host, model)
   - host：個股頁的 #bigHolders。model：HoldersWeekly.read 的結果；null 表示沒有資料，整塊不顯示。
   同一份資料重複呼叫不會重畫；資料換了（例如在站內換一檔股票）重畫時，開著的狀態與選中的級距照原樣留著。
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

  // 每一塊的狀態，整頁只建一次：{ key 畫的是哪一份資料, model, played 進場播過了沒, observer 等這一塊捲進畫面, pending 停穩的計時 }
  const shown = new WeakMap();

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
    bars.classList.toggle('is-empty', !level.bars.length);   // 只有一週沒有小柱：位置留著（樣式裡只是看不見），右邊的箭頭才不會跑到數字旁邊
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

  // 播進場：小柱一週一週長出來，每長一根這一張被頂或壓一下，都到了才定在最後的高度
  function play(host, card) {
    const state = shown.get(host), bars = state.model.levels[0].bars;
    state.played = true;
    state.observer.disconnect();
    card.classList.replace('is-waiting', 'is-entering');
    bars.forEach((bar, index) => {
      if (bar.kind !== 'none') card.animate({ translate: ['0 0', `0 ${bar.kind === 'bull' ? -BUMP_PX : BUMP_PX}px`, '0 0'], offset: [0, .3, 1] }, { duration: GAP - 10, delay: FIRST + index * GAP + 120, easing: 'ease-out' });
    });
    setTimeout(() => card.classList.remove('is-entering'), FIRST + bars.length * GAP + SETTLE_MS + 200);
  }

  // 等這一塊捲進畫面才播進場。個股頁剛載入時版面還在排：這一張會先短暫出現在畫面裡，隨即被上面的內容推到畫面下方，
  // 所以進到畫面之後先等一下，確認還在畫面裡才播；被推走了就繼續等下一次進來。
  // 觀察的是外層（重畫不會換掉它），所以還沒播之前資料換了、這一張被重畫，照樣等得到。
  function arm(host) {
    const state = shown.get(host), card = host.querySelector('.hold-card');
    if (state.played || STILL.matches || typeof IntersectionObserver !== 'function') return;
    const bars = state.model.levels[0].bars;
    if (!bars.length) return;   // 只有一週：沒有小柱可播。之後資料換成有小柱的再等
    card.style.setProperty('--settle-at', `${FIRST + bars.length * GAP}ms`);
    card.style.setProperty('--hold-settle', `${SETTLE_MS}ms`);   // 整張定位要多久：樣式跟著這個值，不各寫一份
    card.classList.add('is-waiting');
    const steady = () => {
      clearTimeout(state.pending);
      state.pending = setTimeout(() => {
        const now = host.querySelector('.hold-card.is-waiting');
        if (!now || state.played) return;
        const box = now.getBoundingClientRect();
        if (box.bottom > 0 && box.top < window.innerHeight * .9) play(host, now);
      }, STEADY_MS);
    };
    if (!state.observer) {
      // 這一塊露出多少都通知（分五段）：載入時可能只露出標題的一點點，之後整張捲進來時還要再看一次
      state.observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) steady(); }, { rootMargin: '0px 0px -10% 0px', threshold: [0, .25, .5, .75, 1] });
      state.observer.observe(host);
    }
    steady();   // 這一塊已經在畫面裡的話觀察器不會再通知，自己看一次
  }

  function render(host, model) {
    const key = JSON.stringify(model);
    let state = shown.get(host);
    if (!state) {
      state = { key: null, model: null, played: false, observer: null, pending: 0 };
      shown.set(host, state);
      host.addEventListener('click', event => onClick(host, event));
    } else if (state.key === key) return;
    // 重畫之前記下是不是開著、選的是哪一個級距，畫完照原樣放回去
    const old = host.querySelector('.hold-card');
    const keep = old && { open: old.classList.contains('is-open'), level: (old.querySelector('.cell[aria-pressed="true"]') || { dataset: {} }).dataset.key };
    state.key = key;
    state.model = model;
    if (!model) { host.replaceChildren(); return; }
    const cells = model.levels.map((level, index) => {
      const kind = level.kind || 'plain';   // 只有一週時沒有方向可說，鍵上不放記號
      return `<button type="button" class="cell ${kind}" data-level="${index}" data-key="${esc(level.key)}" aria-pressed="false" style="--order:${index}" aria-label="${esc(level.name)}${WORDS[kind] ? `，${WORDS[kind]}` : ''}"><span class="cell-face"></span><i class="tag"></i><span class="cell-name">${keyName(level.name)}</span></button>`;
    }).join('');
    host.innerHTML = `<section class="sk-card sk-reason sk-membership" aria-labelledby="holdTitle">
      <div class="sk-reason-head"><h2 id="holdTitle">大戶持股</h2><span class="sk-reason-meta">資料日 ${esc(model.date)}</span></div>
      ${model.title ? `<p class="hold-align">${esc(model.title)} <small>${esc(model.note)}</small></p>` : ''}
      <div class="hold-card depth-row on-page" data-state="${esc(model.state)}">
        <button type="button" class="hold-top" aria-expanded="false" aria-controls="holdPanel">
          <span class="hold-now"><span class="hold-label"></span><strong class="hold-value"></strong><em class="hold-change"></em></span>
          <span class="hold-bars" aria-hidden="true"></span>
          <span class="chev" aria-hidden="true"></span>
        </button>
        <div class="hold-panel depth-panel" id="holdPanel" inert><div class="hold-clip"><div class="hold-inner">
          <div class="hold-strip key-strip" role="group" aria-label="三個級距">${cells}</div>
          <p class="hold-read"></p>
        </div></div></div>
      </div>
    </section>`;
    // 選中的級距照名稱找（不是照第幾個）：換了一檔股票，級距可能少一個
    const kept = keep && keep.level ? model.levels.findIndex(level => level.key === keep.level) : -1;
    showLevel(host, kept >= 0 ? kept : 0);
    if (keep && keep.open) setOpen(host, true);
    arm(host);
  }

  window.HoldersCard = { render };
})();
