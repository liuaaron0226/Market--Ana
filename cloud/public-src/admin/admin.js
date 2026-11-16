(() => {
  const list = document.getElementById("list");
  const msg = document.getElementById("msg");
  const tabs = [...document.querySelectorAll("[data-f]")];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const LABEL = { pending: "等待核准", approved: "已核准", rejected: "已拒絕", disabled: "已停用" };
  const ACTIONS = {
    pending: [["approve", "核准", ""], ["reject", "拒絕", "warn"]],
    approved: [["disable", "停用", "warn"]],
    rejected: [["approve", "改為核准", "ghost"]],
    disabled: [["approve", "恢復", "ghost"]],
  };
  let filter = "pending";
  let users = [];

  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("zh-TW", { hour12: false }) : "");
  // 朋友 AI 開關打開時才有 u.gemini：只有狀態與數字（已設定、暫停、最後成功、最近失敗、最新一天是否產生、
  // 自選股與庫存各幾檔），沒有解讀內容、持有哪些股票、股數或成本（DECISIONS 第 11 題）。
  const geminiChip = (g) => ` <span class="state gemini${g.paused ? " paused" : g.configured ? " on" : ""}">Gemini ${
    g.paused ? "已暫停" : g.configured ? "已設定" : "未設定"}</span>`;
  const geminiNote = (g) => `${g.configured
    ? `<div class="note" style="margin-top:4px">${g.digest ? `解讀（資料日 ${esc(g.digest.data_date)}）：${g.digest.produced ? "已產生" : "未產生"}　` : ""}Gemini 最後成功：${
      g.last_success_at ? fmt(g.last_success_at) : "尚未產生"}${g.last_failure_reason ? `　最近失敗：${esc(g.last_failure_reason)}` : ""}</div>` : ""}
    <div class="note" style="margin-top:4px">自選股 ${Number(g.watchlist_count) || 0} 檔・庫存 ${Number(g.holdings_count) || 0} 檔</div>`;

  async function api(path, opts) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "content-type": "application/json" }, ...opts });
    let body = {};
    try { body = await r.json(); } catch {}
    if (!r.ok) throw new Error(body.error || "操作失敗，請稍後再試");
    return body;
  }

  function render() {
    const shown = users.filter((u) => (filter === "closed" ? u.status === "rejected" || u.status === "disabled" : u.status === filter));
    tabs.forEach((t) => t.setAttribute("aria-pressed", String(t.dataset.f === filter)));
    if (!shown.length) {
      list.innerHTML = `<p class="empty">${filter === "pending" ? "目前沒有人在等待核准。" : "沒有資料。"}</p>`;
      return;
    }
    list.innerHTML = shown
      .map(
        (u) => `
        <article class="item" data-email="${esc(u.email)}">
          <div class="item-head">
            <span class="item-name">${esc(u.nickname)}</span>
            <span class="chips"><span class="state ${esc(u.status)}">${LABEL[u.status] || esc(u.status)}</span>${u.gemini ? geminiChip(u.gemini) : ""}</span>
          </div>
          <div class="who">${esc(u.email)}</div>
          <div class="note" style="margin-top:4px">申請：${fmt(u.applied_at)}${u.decided_at ? `　處理：${fmt(u.decided_at)}` : ""}</div>
          ${u.gemini ? geminiNote(u.gemini) : ""}
          <div class="row">${(ACTIONS[u.status] || [])
            .map(([a, text, cls]) => `<button type="button" class="${cls}" data-act="${a}">${text}</button>`)
            .join("")}</div>
        </article>`,
      )
      .join("");
  }

  async function load() {
    try {
      users = (await api("/api/admin/users")).users;
      msg.textContent = "";
      render();
    } catch (err) {
      msg.textContent = err.message;
    }
  }

  list.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const email = btn.closest("[data-email]").dataset.email;
    if (btn.dataset.act === "disable" && !confirm(`確定停用 ${email}？對方會立刻無法進站。`)) return;
    btn.disabled = true;
    try {
      await api(`/api/admin/users/${encodeURIComponent(email)}/${btn.dataset.act}`, { method: "POST", body: "{}" });
      await load();
    } catch (err) {
      msg.textContent = err.message;
      btn.disabled = false;
    }
  });

  tabs.forEach((t) => t.addEventListener("click", () => { filter = t.dataset.f; render(); }));
  load();
})();
