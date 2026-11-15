/* 短中長期走勢的畫面（立體重設計，本人 2026-11-15 定案的 ⑤）：一列一期，橫條上一票一根小柱，點那一期才展開每個指標的鍵。
   資料由 trend-votes.js 讀好（TrendVotes.read），這裡只畫，不另外算。樣式在 home.css〈短中長期走勢〉，展開後那一排鍵的樣式在 bold.css（.key-strip）。

   用法：TrendMatrix.render(section, model)
   - section：首頁的 #marketTrend（裡面要有 .trend-align 與 .trend-rows）。
   - model：TrendVotes.read 的結果；null 表示今天沒有走勢判讀。
   同一份資料重複呼叫不會重畫（K 線重畫時首頁會一直叫）；資料換了才重畫，開著的那一期照樣開著。
   進場的動態只播一次，而且等捲到這一塊才播；系統設定「減少動態」時全部沒有。
   展開後那一排鍵「選一個」的滑動（選中的框跟著手指走）由 chart-menus.js 處理，這裡只管點了之後的事。 */
(function () {
  'use strict';
  const STILL = window.matchMedia('(prefers-reduced-motion: reduce)');
  const EASE = getComputedStyle(document.documentElement).getPropertyValue('--ease-depth').trim() || 'ease-out';   // 全站的緩動（bold.css）
  // 進場的時間表（毫秒）：一列比一列晚；每一票隔一拍落下；每落一票整列被壓或頂一下
  const FIRST_ROW = 100, ROW_GAP = 380, FIRST_VOTE = 260, VOTE_GAP = 240, SETTLE_MS = 700, BUMP_PX = 3;
  const KINDS = { '-1': 'bear', 0: 'none', 1: 'bull' };
  const WORDS = { bear: '偏空', none: '沒有方向', bull: '偏多' };
  const esc = value => String(value).replace(/[&<>"']/g, mark => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[mark]));

  const shown = new WeakMap();   // 每一塊現在畫的是哪一份資料：{ key, model, entered }

  // 進場的時間表：第幾列什麼時候開始、每一票什麼時候落下、整列什麼時候定位。畫的時候（寫進樣式）與播的時候（每一票那一下）都用這一份
  function timetable(period, row) {
    const start = FIRST_ROW + row * ROW_GAP, count = period.votes ? period.votes.length : 0;
    return { start, votes: Array.from({ length: count }, (_, index) => start + FIRST_VOTE + index * VOTE_GAP), settle: count ? start + FIRST_VOTE + count * VOTE_GAP : start };
  }
  // 鍵上的名稱：字之間的空白不換行；太窄時只在斜線後面折成兩行（MA120/240）
  const keyName = name => esc(name).replace(/ /g, '&nbsp;').replace('/', '/<wbr>');

  function rowHtml(period, row) {
    const times = timetable(period, row);
    const name = `<span class="trend-name"><span>${esc(period.label)}</span><strong>${esc(period.conclusion)}</strong></span>`;
    // 資料不足，或算出來的票和結論對不上：只寫原本那句說明，不能展開
    if (!period.votes) {
      return `<li class="trend-row" data-key="${esc(period.key)}" data-state="${esc(period.state)}" style="--settle-at:${times.settle}ms">
        <div class="trend-top is-plain">${name}<p class="trend-sentence">${esc(period.sentence)}</p></div></li>`;
    }
    const votes = period.votes.map((vote, index) => ({ ...vote, index, kind: KINDS[vote.side], at: times.votes[index] }));
    const of = kind => votes.filter(vote => vote.kind === kind);
    const block = vote => `<i class="blk ${vote.kind}" data-vote="${vote.index}" style="--at:${vote.at}ms"></i>`;
    const cell = vote => `<button type="button" class="cell ${vote.kind}" data-vote="${vote.index}" aria-pressed="false" aria-label="${esc(vote.name)} ${WORDS[vote.kind]}"><span class="cell-face"></span><i class="tag ${vote.kind}"></i><span class="cell-name">${keyName(vote.name)}</span></button>`;
    const none = of('none'), mid = '<span class="mid"></span>';
    const counts = `${of('bear').length} 個偏空、${of('bull').length} 個偏多${none.length ? `、${none.length} 個沒有方向` : ''}`;
    const panel = `trendPanel-${esc(period.key)}`;
    return `<li class="trend-row" data-key="${esc(period.key)}" data-state="${esc(period.state)}" style="--rail-at:${times.start}ms;--settle-at:${times.settle}ms">
      <button type="button" class="trend-top" aria-expanded="false" aria-controls="${panel}" aria-label="${esc(period.label)} ${esc(period.conclusion)}：${votes.length} 個指標，${counts}">
        ${name}
        <span class="trend-bar" aria-hidden="true"><span class="side bears">${of('bear').map(block).join('')}</span>${mid}<span class="side">${of('bull').map(block).join('')}</span></span>
        <span class="chev" aria-hidden="true"></span>
      </button>
      <div class="trend-panel" id="${panel}" inert><div class="trend-clip"><div class="trend-inner">
        <div class="trend-strip key-strip" role="group" aria-label="${esc(period.label)}的每個指標">${of('bear').reverse().map(cell).join('')}${mid}${none.length ? none.map(cell).join('') + mid : ''}${of('bull').map(cell).join('')}</div>
        <p class="trend-read"${period.extra ? '' : ' hidden'}>${esc(period.extra || '')}</p>
      </div></div></div>
    </li>`;
  }

  const rowsOf = section => [...section.querySelectorAll('.trend-row')];
  const periodOf = (section, row) => shown.get(section).model.periods.find(period => period.key === row.dataset.key);
  const cellsOf = row => [...row.querySelectorAll('.cell')].sort((a, b) => a.dataset.vote - b.dataset.vote);
  const partsOf = cell => ({ tag: cell.querySelector('.tag'), rest: [cell.querySelector('.cell-face'), cell.querySelector('.cell-name')] });

  // 收起來的鍵不該被鍵盤走到
  function setOpen(row, open) {
    row.classList.toggle('is-open', open);
    row.querySelector('.trend-top').setAttribute('aria-expanded', String(open));
    row.querySelector('.trend-panel').inert = !open;
  }
  // 下面那一行：選了一顆鍵就寫那個指標的數字；沒選的時候寫不算票的數字（短期的量比與 5 日法人），沒有就不佔位置
  function select(section, row, cell) {
    const period = periodOf(section, row), read = row.querySelector('.trend-read');
    row.querySelectorAll('.cell').forEach(other => other.setAttribute('aria-pressed', String(other === cell)));
    const text = cell ? period.votes[Number(cell.dataset.vote)].text : period.extra;
    read.hidden = !text;
    read.textContent = text || '';
  }

  // 鍵裡的小柱要從橫條上那一根的位置出發（或回去）時的位移；沒有方向的票橫條上沒有小柱
  function fromBlock(row, cell) {
    const block = row.querySelector(`.blk[data-vote="${cell.dataset.vote}"]`);
    if (!block) return null;
    const from = block.getBoundingClientRect(), to = cell.querySelector('.tag').getBoundingClientRect();
    return `translate(${from.left - to.left}px, ${from.top - to.top}px)`;
  }

  // 點開：橫條上的每一根小柱飛下來，落進自己那顆鍵裡；鍵在它落下時浮出來
  function open(row) {
    const starts = STILL.matches ? [] : cellsOf(row).map(cell => [cell, fromBlock(row, cell)]);
    setOpen(row, true);
    starts.forEach(([cell, transform], order) => {
      const timing = { duration: 560, delay: order * 80, easing: EASE, fill: 'backwards' }, { tag, rest } = partsOf(cell);
      if (transform) tag.animate([{ offset: 0, transform }], timing); else rest.push(tag);
      rest.forEach(part => part.animate({ opacity: [0, 0, 1], translate: ['0 6px', '0 6px', '0 0'], offset: [0, .35, 1] }, timing));
    });
  }
  // 收起來：小柱飛回橫條上
  function close(section, row) {
    if (row.classList.contains('is-closing')) return;
    if (STILL.matches) { select(section, row, null); setOpen(row, false); return; }
    // 展開的動畫還沒播完就收：先讓它落定，不然量到的是飛到一半的位置，飛回去的落點會偏
    row.querySelectorAll('.cell').forEach(cell => cell.getAnimations({ subtree: true }).forEach(run => run.finish()));
    const cells = cellsOf(row), targets = cells.map(cell => fromBlock(row, cell));
    row.classList.add('is-closing');
    row.querySelector('.trend-top').setAttribute('aria-expanded', 'false');
    const runs = cells.flatMap((cell, order) => {
      const timing = { duration: 360, delay: (cells.length - 1 - order) * 40, easing: 'cubic-bezier(.5, 0, .3, 1)', fill: 'forwards' }, { tag, rest } = partsOf(cell);
      if (!targets[order]) rest.push(tag);
      return [...(targets[order] ? [tag.animate([{ offset: 1, transform: targets[order] }], timing)] : []),
        ...rest.map(part => part.animate({ opacity: [1, 0, 0], offset: [0, .5, 1] }, timing))];
    });
    // 動畫不管是播完還是被取消，都要收乾淨，不然那一列會卡在「正在收」
    Promise.allSettled(runs.map(run => run.finished)).then(() => {
      if (!row.isConnected) return;   // 收到一半資料換了、整塊重畫了
      select(section, row, null);
      setOpen(row, false);
      row.classList.remove('is-closing');
      runs.forEach(run => run.cancel());
    });
  }

  function onClick(section, event) {
    const top = event.target.closest('button.trend-top'), cell = event.target.closest('.cell');
    if (top) {
      const row = top.closest('.trend-row');
      if (row.classList.contains('is-open')) { close(section, row); return; }
      rowsOf(section).forEach(other => { if (other !== row && other.classList.contains('is-open')) close(section, other); });
      open(row);
      return;
    }
    // 再點一次選中的那一顆＝取消，下面那一行回到沒選時的樣子（和本人定案的樣品一樣）
    if (cell) select(section, cell.closest('.trend-row'), cell.getAttribute('aria-pressed') === 'true' ? null : cell);
  }

  // 進場：等捲到這一塊才播一次。還沒播之前小柱先藏著（.is-waiting），播的時候樣式照時間表帶（.is-entering）
  function enter(section) {
    const state = shown.get(section);
    if (state.entered || STILL.matches || typeof IntersectionObserver !== 'function') { state.entered = true; return; }
    state.entered = true;
    section.classList.add('is-waiting');
    const seen = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      seen.disconnect();
      section.classList.replace('is-waiting', 'is-entering');
      let last = 0;
      rowsOf(section).forEach((row, index) => {
        const period = periodOf(section, row);
        if (!period) return;
        const times = timetable(period, index);
        last = Math.max(last, times.settle + SETTLE_MS);   // 沒有票的列也要等它定位完
        (period.votes || []).forEach((vote, order) => {
          if (vote.side) row.animate({ translate: ['0 0', `0 ${-vote.side * BUMP_PX}px`, '0 0'], offset: [0, .3, 1] }, { duration: VOTE_GAP - 10, delay: times.votes[order] + 150, easing: 'ease-out' });
        });
      });
      setTimeout(() => section.classList.remove('is-entering'), last + 200);
    }, { rootMargin: '0px 0px -10% 0px' });
    seen.observe(section.querySelector('.trend-rows'));
  }

  function render(section, model) {
    const align = section.querySelector('.trend-align'), list = section.querySelector('.trend-rows');
    if (!align || !list) return;
    const key = JSON.stringify(model), before = shown.get(section);
    if (before && before.key === key) return;
    // 重畫之前記下開著的那一期與選中的那顆鍵，畫完照原樣放回去（不播動畫）
    const wasOpen = section.querySelector('.trend-row.is-open:not(.is-closing)');
    const keep = wasOpen && { key: wasOpen.dataset.key, vote: (wasOpen.querySelector('.cell[aria-pressed="true"]') || { dataset: {} }).dataset.vote };
    shown.set(section, { key, model, entered: Boolean(before && before.entered) });
    if (!before) {
      list.addEventListener('click', event => onClick(section, event));
      section.style.setProperty('--trend-settle', `${SETTLE_MS}ms`);   // 整列定位要多久：樣式（home.css）跟著這個值，不各寫一份
    }
    if (!model) {
      align.hidden = true;
      list.innerHTML = '<li class="market-trend-error">走勢判讀尚未隨每日資料完成。</li>';
      return;
    }
    align.hidden = false;
    align.innerHTML = `${esc(model.title)}${model.note ? ` <small>${esc(model.note)}</small>` : ''}`;   // 中間留一個空白：朗讀與複製時兩句才不會黏在一起
    list.innerHTML = model.periods.map(rowHtml).join('');
    const again = keep && rowsOf(section).find(row => row.dataset.key === keep.key && row.querySelector('.trend-panel'));
    if (again) {
      setOpen(again, true);
      const cell = keep.vote !== undefined && again.querySelector(`.cell[data-vote="${keep.vote}"]`);
      if (cell) select(section, again, cell);
    }
    enter(section);
  }

  window.TrendMatrix = { render };
})();
