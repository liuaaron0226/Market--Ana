/* 基金個股頁的「配息」分類（基金當成一般個股 06 票，本人 2026-11-18；規格 .scratch/fund-as-stock/spec.md 第 28 條）。
   資料是分析契約 kind fund 的 distributions 欄（每日建置由集保配息原件算出：fund_distributions.py），股票沒有這欄。

   FundDistributions.read(dist) → { latest, years, note, hint, empty }：把契約整理成畫面上的字，不碰畫面（測試只驗這個）。
   FundDistributions.render(container, dist)：畫進個股頁的「配息」區塊；stock.js 換到基金時呼叫（股票不呼叫，區塊由它清空）。 */
(function (global) {
  'use strict';

  const CURRENCY = { USD: '美元', TWD: '台幣' };
  const EMPTY = '目前沒有這檔基金的配息紀錄。';
  const MISSING = '這檔基金的配息資料尚未提供。';   // 舊的分析資料沒有這一欄：和「真的沒配過息」分開說
  // 只解釋名詞，不說這檔現在還在配本金（現況看上面那一句與各年來源）
  const HINT = '「配息來自本金」是把你投入的錢拿一部分還給你，淨值會跟著變少；所以報酬要含息一起看（「比較」那一類就是含息再投入）。';
  const amountText = value => String(Math.round(Number(value) * 1e4) / 1e4);

  function latestSource(latest) {
    if (latest.capital_pct === null || latest.capital_pct === undefined) return '來源未公布';
    if (latest.capital_pct > 0) return `${latest.capital_pct}% 來自本金`;
    return `${latest.income_pct ?? 100}% 來自可分配淨利益`;
  }

  function read(dist) {
    const currency = CURRENCY[dist?.currency] || dist?.currency || '';
    const latest = dist?.latest || null;
    const years = (dist?.years || []).map(year => ({
      year: year.year,
      facts: `${year.count} 次 · 合計 ${amountText(year.total)} ${currency}`,
      source: year.source,
      capital: year.all_capital + year.part_capital > 0,
    }));
    return {
      latest: latest && {
        amount: amountText(latest.amount), unit: `${currency}／單位`,
        dates: `除息 ${latest.ex_date} · 發放 ${latest.pay_date}`, source: latestSource(latest),
      },
      years,
      note: dist?.capital_note || '',
      hint: dist?.capital_note ? HINT : '',
      empty: latest ? '' : dist ? EMPTY : MISSING,
    };
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render(container, dist) {
    if (!container) return;
    const view = read(dist);
    const box = el('section', 'fd');
    box.setAttribute('aria-labelledby', 'fdTitle');
    const title = el('h2', 'sr-only', '配息');
    title.id = 'fdTitle';
    box.append(title);
    if (view.empty) {
      box.append(el('p', 'fd-empty', view.empty));
      container.replaceChildren(box);
      return;
    }
    const latest = el('div', 'fd-latest');
    latest.append(el('span', 'fd-label', '最近一次配息'));
    const figure = el('p', 'fd-amount');
    figure.append(el('b', 'num', view.latest.amount), el('small', '', view.latest.unit));
    latest.append(figure, el('p', 'fd-meta num', view.latest.dates), el('p', 'fd-meta', view.latest.source));
    box.append(latest);
    if (view.note) box.append(el('p', 'fd-note', view.note), el('p', 'fd-hint', view.hint));
    const list = el('ol', 'fd-years');
    list.setAttribute('aria-label', '各年配息');
    for (const year of view.years) {
      const row = el('li', 'fd-year');
      if (year.capital) row.dataset.capital = '';
      row.append(el('b', 'num', year.year), el('span', 'num', year.facts), el('small', '', year.source));
      list.append(row);
    }
    box.append(list, el('p', 'fd-src', '資料：集保結算所基金資訊觀測站與開放資料（每單位配息、除息日、配息來源比例）'));
    container.replaceChildren(box);
  }

  global.FundDistributions = { read, render };
})(window);
