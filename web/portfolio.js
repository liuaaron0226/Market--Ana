(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const money = new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', maximumFractionDigits: 0 });
  const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 });
  const state = { watchlist: [], holdings: [] };

  function setThemeLabel() {
    const dark = getComputedStyle(document.documentElement).colorScheme.includes('dark');
    $('portfolioTheme').setAttribute('aria-label', dark ? '改用淺色主題' : '改用深色主題');
  }
  $('portfolioTheme').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = getComputedStyle(root).colorScheme.includes('dark');
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('market-theme', root.dataset.theme); } catch (_) {}
    setThemeLabel();
  });
  try {
    const saved = localStorage.getItem('market-theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
  } catch (_) {}
  setThemeLabel();

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      headers: options.body ? { 'content-type': 'application/json' } : undefined,
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || '暫時無法處理');
    return payload;
  }

  function setStatus(node, message, kind = '') {
    node.textContent = message;
    if (kind) node.dataset.state = kind;
    else node.removeAttribute('data-state');
  }

  function stockIdentity(item) {
    const host = document.createElement('div');
    host.className = 'pf-stock';
    const link = document.createElement('a');
    link.href = item.in_universe ? `stock.html?code=${encodeURIComponent(item.code)}` : '#';
    link.textContent = `${item.code} ${item.name}`;
    if (!item.in_universe) {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
    }
    const meta = document.createElement('small');
    meta.textContent = item.market;
    const scope = document.createElement('span');
    scope.className = `pf-scope${item.in_universe ? '' : ' outside'}`;
    scope.textContent = item.in_universe ? '可開啟本站完整分析' : '不在本站分析範圍';
    host.append(link, meta, scope);
    return host;
  }

  function deleteButton(label, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pf-delete';
    button.textContent = '刪除';
    button.setAttribute('aria-label', label);
    button.addEventListener('click', action);
    return button;
  }

  function renderWatchlist() {
    const host = $('watchlistRows');
    host.replaceChildren();
    $('watchlistCount').textContent = `${state.watchlist.length} 檔`;
    if (!state.watchlist.length) {
      const empty = document.createElement('p');
      empty.className = 'pf-empty';
      empty.textContent = '還沒有自選股。輸入代號後，可標記為短期、長期或先不分類。';
      host.append(empty);
      return;
    }
    state.watchlist.forEach(item => {
      const row = document.createElement('article');
      row.className = 'pf-row pf-watch-row';
      const select = document.createElement('select');
      select.setAttribute('aria-label', `${item.code} 投資分類`);
      [['none', '未分類'], ['short', '短期'], ['long', '長期']].forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        option.selected = item.horizon === value;
        select.append(option);
      });
      select.addEventListener('change', async () => {
        select.disabled = true;
        try {
          await api('/api/watchlist', { method: 'POST', body: { code: item.code, horizon: select.value } });
          setStatus($('watchlistStatus'), `${item.code} 分類已更新。`, 'success');
          await loadWatchlist();
        } catch (error) {
          setStatus($('watchlistStatus'), error.message, 'error');
          select.value = item.horizon;
        } finally { select.disabled = false; }
      });
      row.append(stockIdentity(item), select, deleteButton(`從自選股刪除 ${item.code} ${item.name}`, async () => {
        if (!confirm(`要從自選股刪除 ${item.code} ${item.name} 嗎？`)) return;
        try {
          await api(`/api/watchlist/${encodeURIComponent(item.code)}`, { method: 'DELETE', body: {} });
          setStatus($('watchlistStatus'), `${item.code} 已從自選股刪除。`, 'success');
          await loadWatchlist();
        } catch (error) { setStatus($('watchlistStatus'), error.message, 'error'); }
      }));
      host.append(row);
    });
  }

  function valueCell(label, value, tone = '') {
    const cell = document.createElement('div');
    cell.className = 'pf-cell';
    const strong = document.createElement('strong');
    strong.textContent = value;
    if (tone) strong.className = tone;
    const small = document.createElement('small');
    small.textContent = label;
    cell.append(strong, small);
    return cell;
  }

  function renderSummary() {
    const priced = state.holdings.filter(item => Number.isFinite(item.market_value));
    const marketValue = priced.reduce((sum, item) => sum + item.market_value, 0);
    const cost = state.holdings.reduce((sum, item) => sum + item.cost_basis, 0);
    const pnl = priced.length === state.holdings.length ? marketValue - cost : null;
    const returnPct = pnl === null || !cost ? null : pnl / cost * 100;
    $('holdingCount').textContent = `${state.holdings.length} 檔庫存`;
    $('totalMarketValue').textContent = state.holdings.length && priced.length === state.holdings.length ? money.format(marketValue) : '—';
    $('totalCost').textContent = state.holdings.length ? money.format(cost) : '—';
    $('totalPnl').textContent = pnl === null ? '—' : `${pnl >= 0 ? '+' : '−'}${money.format(Math.abs(pnl))}`;
    $('totalReturn').textContent = returnPct === null ? '—' : `${returnPct >= 0 ? '+' : ''}${number.format(returnPct)}%`;
    for (const id of ['totalPnl', 'totalReturn']) {
      $(id).className = `num${pnl === null ? '' : pnl >= 0 ? ' up' : ' down'}`;
    }
  }

  function renderHoldings() {
    const host = $('holdingRows');
    host.replaceChildren();
    $('holdingsCount').textContent = `${state.holdings.length} 檔`;
    renderSummary();
    if (!state.holdings.length) {
      const empty = document.createElement('p');
      empty.className = 'pf-empty';
      empty.textContent = '還沒有庫存。先輸入代號、股數與平均成本，之後可用同一表單更新。';
      host.append(empty);
      return;
    }
    state.holdings.forEach(item => {
      const row = document.createElement('article');
      row.className = 'pf-row pf-holding-row';
      const price = item.latest_price === null ? '—' : number.format(item.latest_price);
      const marketValue = item.market_value === null ? '—' : money.format(item.market_value);
      const pnl = item.unrealized_pnl;
      const pnlTone = pnl === null ? '' : pnl >= 0 ? 'up' : 'down';
      const pnlText = pnl === null ? '—' : `${pnl >= 0 ? '+' : '−'}${money.format(Math.abs(pnl))}`;
      const returnText = item.return_pct === null ? '—' : `${item.return_pct >= 0 ? '+' : ''}${number.format(item.return_pct)}%`;
      const note = document.createElement('div');
      note.className = 'pf-cell pf-note';
      const strong = document.createElement('strong');
      strong.textContent = item.note || '無備註';
      const small = document.createElement('small');
      small.textContent = `${number.format(item.shares)} 股 · 均價 ${number.format(item.average_cost)}`;
      note.append(strong, small);
      row.append(
        stockIdentity(item),
        valueCell(item.price_stale ? '上次可用收盤' : '最新收盤', price),
        valueCell('目前市值', marketValue),
        valueCell('未實現損益', pnlText, pnlTone),
        valueCell('報酬率', returnText, pnlTone),
        note,
        deleteButton(`刪除庫存 ${item.code} ${item.name}`, async () => {
          if (!confirm(`要刪除 ${item.code} ${item.name} 的庫存資料嗎？`)) return;
          try {
            await api(`/api/holdings/${encodeURIComponent(item.code)}`, { method: 'DELETE', body: {} });
            setStatus($('holdingStatus'), `${item.code} 庫存已刪除。`, 'success');
            await loadHoldings();
          } catch (error) { setStatus($('holdingStatus'), error.message, 'error'); }
        }),
      );
      row.querySelectorAll('.pf-cell')[1]?.classList.add('pf-secondary');
      row.querySelectorAll('.pf-cell')[2]?.classList.add('pf-secondary');
      host.append(row);
    });
  }

  async function loadWatchlist() {
    const payload = await api('/api/watchlist');
    state.watchlist = payload.items || [];
    renderWatchlist();
  }
  async function loadHoldings() {
    const payload = await api('/api/holdings');
    state.holdings = payload.items || [];
    renderHoldings();
  }

  $('watchlistForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    setStatus($('watchlistStatus'), '正在儲存…');
    try {
      const code = $('watchCode').value.trim().toUpperCase();
      await api('/api/watchlist', { method: 'POST', body: { code, horizon: $('watchHorizon').value } });
      $('watchCode').value = '';
      setStatus($('watchlistStatus'), `${code} 已加入自選股。`, 'success');
      await loadWatchlist();
    } catch (error) { setStatus($('watchlistStatus'), error.message, 'error'); }
    finally { button.disabled = false; }
  });

  $('holdingForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    setStatus($('holdingStatus'), '正在儲存…');
    try {
      const code = $('holdingCode').value.trim().toUpperCase();
      await api('/api/holdings', { method: 'POST', body: {
        code,
        shares: Number($('holdingShares').value),
        average_cost: Number($('holdingCost').value),
        note: $('holdingNote').value.trim(),
      } });
      form.reset();
      setStatus($('holdingStatus'), `${code} 庫存已儲存。`, 'success');
      await loadHoldings();
    } catch (error) { setStatus($('holdingStatus'), error.message, 'error'); }
    finally { button.disabled = false; }
  });

  Promise.all([loadWatchlist(), loadHoldings()]).then(() => {
    const dates = state.holdings.map(item => item.price_as_of).filter(Boolean).sort();
    const stale = state.holdings.some(item => item.price_stale);
    $('portfolioFreshness').textContent = dates.length
      ? `庫存價格至 ${dates.at(-1)}${stale ? '（部分沿用上次資料）' : ''}`
      : '尚無庫存價格';
  }).catch(error => {
    $('portfolioFreshness').textContent = '資料讀取失敗';
    setStatus($('holdingStatus'), error.message, 'error');
  });
})();
