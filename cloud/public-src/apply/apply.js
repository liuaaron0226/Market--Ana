(() => {
  const view = document.getElementById("view");
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const TEXT = {
    pending: { tag: "等待核准", title: "申請已送出，等待站主核准", lead: "站主核准後就能進站，這個頁面不用一直開著。之後再打開網站即可。" },
    rejected: { tag: "未通過", title: "這次的申請沒有通過", lead: "如有疑問請直接聯絡站主。" },
    disabled: { tag: "已停用", title: "這個帳號已被停用", lead: "目前無法進站。如有疑問請直接聯絡站主。" },
  };

  async function api(path, opts) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "content-type": "application/json" }, ...opts });
    let body = {};
    try { body = await r.json(); } catch {}
    if (!r.ok) throw new Error(body.error || "無法連線，請稍後再試");
    return body;
  }

  function renderForm(me) {
    view.innerHTML = `
      <h1>申請使用</h1>
      <p class="lead">這個網站只開放給站主核准的人。送出申請後，站主同意才能進站。</p>
      <p class="who">登入身分：${esc(me.email)}</p>
      <form id="f" novalidate>
        <label for="nick">怎麼稱呼你（站主看得到，其他使用者看不到）</label>
        <input id="nick" type="text" maxlength="40" autocomplete="nickname" required>
        <div class="row"><button type="submit" id="send">送出申請</button></div>
        <p class="msg" id="msg" role="alert"></p>
      </form>
      <p class="note">進站時需要兩步驟驗證：第一次登入時，Cloudflare 會請你用手機驗證器 App（例如 Google Authenticator）或裝置指紋完成設定。</p>`;
    const f = document.getElementById("f");
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nick = document.getElementById("nick").value.trim();
      const msg = document.getElementById("msg");
      if (!nick) { msg.textContent = "請填稱呼"; return; }
      const btn = document.getElementById("send");
      btn.disabled = true;
      msg.textContent = "";
      try {
        render(await api("/apply/api/apply", { method: "POST", body: JSON.stringify({ nickname: nick }) }));
      } catch (err) {
        msg.textContent = err.message;
        btn.disabled = false;
      }
    });
  }

  function render(me) {
    if (me.status === "none") return renderForm(me);
    if (me.status === "approved") {
      view.innerHTML = `
        <span class="state approved">已核准</span>
        <h1 style="margin-top:12px">${me.owner ? "你是站主" : "已經可以進站了"}</h1>
        <p class="lead">${me.owner ? "站主不用申請。" : "第一次進站會要求兩步驟驗證設定。"}</p>
        <p class="who">${esc(me.email)}</p>
        <div class="row"><a class="btn" href="/">進入網站</a>${me.owner ? '<a class="btn" style="background:transparent;color:var(--ink);border-color:var(--line-strong)" href="/admin/">管理申請</a>' : ""}</div>`;
      return;
    }
    const t = TEXT[me.status] || TEXT.pending;
    view.innerHTML = `
      <span class="state ${esc(me.status)}">${t.tag}</span>
      <h1 style="margin-top:12px">${t.title}</h1>
      <p class="lead">${t.lead}</p>
      <p class="who">${esc(me.email)}</p>
      <div class="row"><button class="ghost" id="again" type="button">重新查看狀態</button></div>`;
    document.getElementById("again").addEventListener("click", load);
  }

  async function load() {
    try {
      render(await api("/apply/api/me"));
    } catch (err) {
      view.innerHTML = `<h1>暫時無法讀取</h1><p class="lead">${esc(err.message)}</p>`;
    }
  }

  load();
})();
