"use strict";
let data,
  proposals = [],
  coverage,
  busy = false;
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );
const h = escapeHtml;
const actions = { add: "新增", remove: "移除", move: "調整環節" };
const markets = { tw: "台股", us: "美股" };
const threshold = 3;
let tab = "review",
  selected,
  query = "",
  period = "";
const nodeName = (id) => data.chain.nodes.find((n) => n.id === id)?.name || "";
const companyKey = (market, code) => market + ":" + code;
const decision = (p) => data.decisions[p.id] || "pending";
const statusText = (p) => ({ approved: "已同意", declined: "不採用", pending: "待審閱" })[decision(p)];
function notify(text) {
  $("notice").textContent = text;
}
function members(chain) {
  const result = new Map();
  for (const node of chain.nodes)
    for (const market of ["tw", "us"]) {
      for (const [code] of node[market]) {
        const id = companyKey(market, code);
        if (!result.has(id)) result.set(id, { id, market, code, name: chain[market][code], nodes: [] });
        result.get(id).nodes.push(node.name);
      }
    }
  return result;
}
const warning = (count) => '<span class="warn">缺資料 · ' + count + " 個交易日</span>";
function review() {
  if (!proposals.length)
    return (
      '<div class="panel empty">' + (data.ready ? "Gemini 此季沒有提名異動。" : "本季提名尚未產生。") + "</div>"
    );
  const p = proposals.find((p) => p.id === selected);
  const list = proposals
    .map(
      (item) =>
        `<button class="choice" data-select="${h(item.id)}" aria-pressed="${selected === item.id}"><span class="rowhead"><span class="type ${h(item.kind)}">${actions[item.kind]}</span><span class="meta">${statusText(item)}</span></span><strong>${h(item.name)}</strong><span class="meta">${markets[item.market]} · <span class="code">${h(item.code)}</span></span></button>`,
    )
    .join("");
  const current = decision(p);
  const footer =
    current === "pending"
      ? `<button class="light" data-decide="declined">不採用</button><button class="primary" data-decide="approved">同意${actions[p.kind]}</button>`
      : '<button class="light" data-page="members">查看生效名單</button>';
  return `<div class="split"><aside class="panel"><div class="listhead">季度提名 <span class="meta">／ ${proposals.length} 件</span></div>${list}</aside>
    <article class="panel" id="detail"><div class="cardhead"><strong>提名明細</strong><span class="state">${statusText(p)}</span></div><div class="detail"><h2>${h(p.name)}</h2><span class="code">${markets[p.market]} · ${h(p.code)}</span>
    <div class="route"><div class="pathrow"><span>調整前</span><strong>${h(p.kind === "remove" ? p.beforeNodes.map(nodeName).join("、") : p.from ? nodeName(p.from) : "未列入名單")}</strong></div><div class="pathrow"><span>提案</span><strong>${h(p.to ? nodeName(p.to) : "移出名單")}</strong></div></div>
    <h3>提名理由</h3><p class="reason">${h(p.reason)}</p><div class="actions">${footer}</div></div></article></div>`;
}
function memberRows(all) {
  const q = query.trim().toLocaleLowerCase();
  const matching = [...all.values()].filter((m) =>
    [m.name, m.code, ...m.nodes].join(" ").toLocaleLowerCase().includes(q),
  );
  matching.sort((a, b) => a.code.localeCompare(b.code));
  return (
    matching
      .map((m) => {
        const count = gapDays(m.id).length;
        return `<div class="record"><div><strong>${h(m.name)}</strong><small class="code">${markets[m.market]} · ${h(m.code)}</small></div><span>${h(m.nodes.join("、"))}</span><span>${count >= threshold ? warning(count) : ""}</span></div>`;
      })
      .join("") || '<div class="empty">查無符合的公司。</div>'
  );
}

function gapDays(id) {
  return coverage?.gaps?.[id]?.days || [];
}
function healthPanel(all) {
  const rows = [...all.values()]
    .filter((m) => gapDays(m.id).length >= threshold)
    .map((m) => {
      const gap = coverage.gaps[m.id];
      return `<article class="healthrow"><div class="rowhead"><h3>${h(m.name)} <small class="meta">${h(m.code)}</small></h3>${warning(gap.days.length)}</div>
      <p>${markets[m.market]} · ${h(m.nodes.join("、"))}</p><div class="meta">缺資料日期　${h(gap.days.join("、"))}<br>上次取得行情　${h(gap.lastGood || "尚無紀錄")}</div></article>`;
    })
    .join("");
  const sources = [
    ["上市", "listed"],
    ["上櫃", "otc"],
    ["美股", "us"],
  ];
  const dates = sources.map(([name, key]) => name + " " + (coverage?.through?.[key] || "尚無資料")).join(" ／ ");
  const empty =
    coverage && Object.values(coverage.through).some(Boolean)
      ? "在已取得的行情中，目前沒有達到門檻的公司。"
      : "目前尚無可核對的交易日資料。";
  return `<section class="panel"><div class="sectionline"><h2>公司行情缺口</h2><span class="meta">連續 ${threshold} 個交易日</span></div>
    ${rows || '<p class="empty">' + empty + "</p>"}<p class="hint">行情截至　${h(dates)}${coverage?.us_incomplete ? "（美股來源尚未補齊）" : ""}<br>各市場分別計算。收到行情後即解除標記。</p></section>`;
}
function render() {
  const all = members(data.chain);
  const groups = [
    ["review", "季度提名", proposals.filter((p) => decision(p) === "pending").length],
    ["members", "生效名單", all.size],
    [
      "health",
      "資料缺口",
      coverage ? [...all.keys()].filter((id) => gapDays(id).length >= threshold).length : "—",
    ],
  ];
  $("tabs").innerHTML = groups
    .map(
      ([key, label, count]) =>
        `<button data-page="${key}" ${key === tab ? 'aria-current="page"' : ""}>${label} <small>${count}</small></button>`,
    )
    .join("");
  if (tab === "review") $("content").innerHTML = review();
  else if (tab === "health") $("content").innerHTML = healthPanel(all);
  else {
    $("content").innerHTML =
      `<section class="panel"><div class="sectionline"><h2>目前生效的公司</h2><input id="query" type="search" aria-label="搜尋生效公司" placeholder="搜尋公司、代號或環節"></div><div id="records" class="records"></div></section>`;
    $("query").value = query;
    $("records").innerHTML = memberRows(all);
    $("query").oninput = (event) => {
      query = event.target.value;
      $("records").innerHTML = memberRows(all);
    };
  }
}

async function fetchJson(url, options) {
  const res = await fetch(url, { cache: "no-store", ...options });
  if (!res.ok)
    throw new Error(
      res.status === 409
        ? "名單或提名已變更，請核對最新狀態。"
        : "讀寫失敗（HTTP " + res.status + "），請重新整理再試。",
    );
  return res.json();
}
function displayQuarter(value) {
  const [year, q] = value.split("-Q");
  return year + " 年 · 第" + ["一", "二", "三", "四"][Number(q) - 1] + "季";
}
async function loadRoster() {
  data = await fetchJson("/api/chain-roster" + (period ? "?quarter=" + encodeURIComponent(period) : ""));
  proposals = data.proposals;
  if (!proposals.some((p) => p.id === selected)) selected = proposals[0]?.id;
  const heading = $("quarter");
  heading.textContent = displayQuarter(data.quarter);
  if (data.quarters.length > 1) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", "審閱季度");
    for (const q of data.quarters) select.add(new Option(displayQuarter(q), q, false, q === data.quarter));
    select.onchange = async () => {
      if (busy) return;
      busy = true;
      select.disabled = true;
      period = select.value;
      try {
        await loadRoster();
        notify("");
      } catch (error) {
        period = data.quarter;
        select.value = period;
        notify(error.message);
      } finally {
        busy = false;
        select.disabled = false;
      }
    };
    heading.replaceChildren(select);
  }
  render();
}
async function loadPage() {
  try {
    await loadRoster();
    const map = await fetchJson("/api/chain-map");
    coverage = map.flows?.roster_health;
    if (tab !== "members" || !query) render();
    else $("records").innerHTML = memberRows(members(data.chain));
  } catch (error) {
    notify(error.message);
  }
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button || busy) return;
  if (button.dataset.page) {
    tab = button.dataset.page;
    query = "";
    notify("");
    render();
  } else if (button.dataset.select) {
    selected = button.dataset.select;
    notify("");
    render();
    if (matchMedia("(max-width:680px)").matches) $("detail").scrollIntoView({ block: "start" });
    else document.querySelector('[data-select="' + CSS.escape(selected) + '"]').focus();
  } else if (button.dataset.decide) {
    const p = proposals.find((item) => item.id === selected);
    if (!p || decision(p) !== "pending") return;
    busy = true;
    document.querySelectorAll("[data-decide]").forEach((b) => {
      b.disabled = true;
    });
    let message;
    try {
      await fetchJson("/api/chain-roster", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          quarter: data.quarter,
          version: data.version,
          id: p.id,
          decision: button.dataset.decide,
        }),
      });
      message = p.name + (button.dataset.decide === "approved" ? "的提名已生效。" : "的提名已記為不採用。");
    } catch (error) {
      message = error.message;
    }
    try {
      await loadRoster();
    } catch {
      message += " 請重新整理以確認最新狀態。";
    } finally {
      busy = false;
      notify(message);
    }
  }
});
window.addEventListener("pageshow", loadPage);
