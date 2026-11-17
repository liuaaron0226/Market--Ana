/* 基金對照 0050（本人 2026-11-17 定案）：把 /api/funds/<代碼>/vs-0050 的回應整理成畫面要的字。不碰畫面；
   畫面在 fund-entry.js（自選股頁那一列；基金對照頁 11-18 起轉址到個股頁）。

   定案：唯讀比較；台幣含匯率、另一行寫匯率影響；期間 1 月／3 月／1 年／3 年＋成立以來；
   除息日用淨值買回、不計費稅，0050 用本站既有算法。圖和個股頁「對照 0050」同一種。

   FundView.read(payload) → { title, sub, nav, navDate, conclusion, dateLine, periods, issues, capitalNote,
                              latestDistribution, notes, series }
   - periods：五顆期間鍵 { key, label, short, kind 'bull' 領先／'bear' 落後／'none', gap, facts 四個 [名稱, 數字], fx 匯率影響那一行 }
   - series：走勢圖兩條線（0050 虛線、基金實線），期初＝100
   FundView.entry(payload) → 自選股頁那一列 { title, kind, gap }（讀不到資料時都是空的） */
(function (global) {
  const SHORT = { '1m': '近1月', '3m': '近3月', '1y': '近1年', '3y': '近3年', since: '成立以來' };
  const CURRENCY = { USD: '美元' };
  const signed = value => `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;
  const drawdown = value => (value === 0 ? '0.0%' : `${Number(value).toFixed(1)}%`);
  const points = value => `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}`;
  const kindOf = row => (row.excess_pct > 0 ? 'bull' : row.excess_pct < 0 ? 'bear' : 'none');
  const yearRow = payload => (payload.periods || []).find(row => row.period === '1y');

  function conclusion(payload) {
    const row = yearRow(payload);
    if (!row || row.status !== 'ok') return '近 1 年資料不足，暫時無法和 0050 比較。';
    if (row.excess_pct === 0) return '近 1 年和 0050 持平（台幣、含息再投入）。';
    return `近 1 年${row.excess_pct > 0 ? '領先' : '落後'} 0050 ${Math.abs(row.excess_pct).toFixed(1)} 個百分點（台幣、含息再投入）。`;
  }

  function periods(payload) {
    return (payload.periods || []).map(row => {
      const base = { key: row.period, label: row.label, short: SHORT[row.period] || row.label };
      if (row.status !== 'ok') {
        return { ...base, kind: 'none', gap: '資料不足', fx: '',
          facts: [['基金報酬（台幣）', '—'], ['0050 報酬', '—'], ['基金最大回撤', '—'], ['0050 最大回撤', '—']] };
      }
      return {
        ...base, kind: kindOf(row), gap: signed(row.excess_pct),
        facts: [['基金報酬（台幣）', signed(row.fund_return_pct)], ['0050 報酬', signed(row.benchmark_return_pct)],
          ['基金最大回撤', drawdown(row.fund_max_drawdown_pct)], ['0050 最大回撤', drawdown(row.benchmark_max_drawdown_pct)]],
        fx: `其中匯率影響 ${points(row.fx_effect_pct)} 個百分點（只看美元是 ${signed(row.fund_usd_return_pct)}）`,
      };
    });
  }

  function issueText(issue) {
    if (issue.kind === 'gap') return `缺 ${issue.from}～${issue.to} 的淨值（${issue.weekdays} 個平日沒抓到），這段報酬可能不準。`;
    if (issue.kind === 'stale') return `基金淨值停在 ${issue.latest}，已經 ${issue.weekdays_behind} 個平日沒有更新。`;
    if (issue.kind === 'nav-conflict') return `${issue.date} 的淨值兩份資料不一致（${issue.kept}／${issue.other}），先用 ${issue.kept}。`;
    if (issue.kind === 'dist-conflict') return `${issue.ex_date} 的配息兩份資料不一致（${issue.kept}／${issue.other}），先用 ${issue.kept}。`;
    if (issue.kind === 'benchmark-dividends-incomplete') return '0050 最近的除息資料還沒抓齊，0050 的含息報酬可能少算。';
    return '';
  }

  function series(payload) {
    const rebased = list => (list || []).map(([day, value]) => [day, Math.round(Number(value) * 1000) / 10]);
    return [
      { key: '0050', label: '0050', dash: '8 5', points: rebased(payload.benchmark_chart) },
      { key: payload.fund.code, label: payload.fund.short_name, dash: '', points: rebased(payload.chart) },
    ];
  }

  // 兩邊比到雙方都有的同一天（data_date）；基金淨值通常晚幾天，哪一邊已經有更新的資料就在括號裡講
  function dateLine(payload) {
    const day = payload.data_date;
    if (payload.fund_nav_date > day) return `資料日 ${day}（基金淨值已到 ${payload.fund_nav_date}）`;
    if (payload.benchmark_date > day) return `資料日 ${day}（0050 已到 ${payload.benchmark_date}）`;
    return `資料日 ${day}`;
  }

  function read(payload) {
    const fund = payload.fund, latest = payload.latest || {};
    const currency = CURRENCY[fund.currency] || fund.currency;
    const dist = payload.distributions && payload.distributions.latest;
    return {
      title: fund.short_name,
      sub: `${fund.code} · ${currency}計價 · 月配息`,
      nav: Number.isFinite(latest.nav) ? latest.nav.toFixed(2) : '—',
      navDate: latest.nav_date ? `淨值日 ${latest.nav_date}` : '',
      conclusion: conclusion(payload),
      dateLine: dateLine(payload),
      periods: periods(payload),
      issues: (payload.issues || []).map(issueText).filter(Boolean),
      capitalNote: payload.capital_note || '',
      latestDistribution: dist ? `最近一次除息 ${dist.ex_date}，每單位配 ${dist.amount} ${currency}。` : '',
      notes: payload.notes || [],
      series: series(payload),
    };
  }

  function entry(payload) {
    if (!payload || !payload.fund) return { title: '', kind: 'none', gap: '' };
    const row = yearRow(payload);
    if (!row || row.status !== 'ok') return { title: payload.fund.short_name, kind: 'none', gap: '近1年 資料不足' };
    const word = row.excess_pct > 0 ? '領先' : row.excess_pct < 0 ? '落後' : '持平';
    return { title: payload.fund.short_name, kind: kindOf(row),
      gap: row.excess_pct === 0 ? '近1年 和 0050 持平' : `近1年 ${word} 0050 ${Math.abs(row.excess_pct).toFixed(1)}` };
  }

  global.FundView = { read, entry };
})(window);
