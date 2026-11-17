/* 自選股頁的基金對照入口（本人 2026-11-17 定案）：清單最下面固定一列「貝萊德世界科技 A10」，點進基金對照頁。
   不是自選的一筆：不能滑開、不能刪、不算在「全部／自選」的檔數裡；按「庫存」時收起來（它不是庫存）。
   只給站主看（「我的股票池」）；本機版沒有帳號（/api/me 讀不到）就照常顯示。
   右邊寫近 1 年和 0050 差幾個百分點，讀不到就只留名稱。清單整份重畫時不會動到這一列（它在 <ol> 外面）。 */
(function () {
  const FUNDS = [{ code: 'BLKTENAU', title: '貝萊德世界科技 A10' }];
  const slot = document.getElementById('fundEntry');
  if (!slot) return;

  const element = (tag, className = '', text = '') => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };

  function row(fund) {
    const link = element('a', 'pf-fund depth-row on-page');
    link.href = `fund.html?code=${encodeURIComponent(fund.code)}`;
    link.dataset.state = 'none';
    link.dataset.code = fund.code;
    const name = element('span', 'hv-name');
    name.append(element('strong', '', fund.title), element('span', '', '基金 · 和 0050 比'));
    const gap = element('span', 'pf-fund-gap num');
    const chev = element('span', 'pf-fund-chev', '›');
    chev.setAttribute('aria-hidden', 'true');
    link.append(name, gap, chev);
    link.setAttribute('aria-label', `${fund.title}，和 0050 的含息報酬對照`);
    return link;
  }

  async function fill(link, fund) {
    try {
      const response = await fetch(`/api/funds/${encodeURIComponent(fund.code)}/vs-0050`, { cache: 'no-store' });
      if (!response.ok) return;
      const entry = window.FundView.entry(await response.json());
      const gap = link.querySelector('.pf-fund-gap');
      gap.textContent = entry.gap;
      gap.classList.toggle('up', entry.kind === 'bull');
      gap.classList.toggle('down', entry.kind === 'bear');
      link.setAttribute('aria-label', `${fund.title}，${entry.gap}（台幣、含息），點進去看對照`);
    } catch { /* 讀不到就只留名稱 */ }
  }

  // 按「庫存」時收起來；看 aria-pressed 的變化，不依賴清單程式的內部
  function followFilter() {
    const filter = document.getElementById('listFilter');
    if (!filter) return;
    const sync = () => { slot.hidden = filter.querySelector('[aria-pressed="true"]')?.dataset.filter === 'holding'; };
    new MutationObserver(sync).observe(filter, { subtree: true, attributes: true, attributeFilter: ['aria-pressed'] });
    sync();
  }

  async function isOwner() {
    try {
      const response = await fetch('/api/me', { cache: 'no-store' });
      if (!response.ok) return response.status === 404;   // 本機版沒有帳號
      return Boolean((await response.json()).owner);
    } catch {
      return true;
    }
  }

  isOwner().then(owner => {
    if (!owner) return;
    const links = FUNDS.map(row);
    slot.replaceChildren(...links);
    followFilter();
    links.forEach((link, index) => fill(link, FUNDS[index]));
  });
})();
