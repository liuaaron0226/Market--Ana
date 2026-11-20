(() => {
  const view = document.getElementById("view");
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const TEXT = {
    pending: { tag: "等待核准", title: "申請已送出，等待站主核准", lead: "站主核准後，這一頁會自己進站。也可以先關掉，之後直接打開網站，不用再登入。" },
    rejected: { tag: "未通過", title: "這次的申請沒有通過", lead: "如有疑問請直接聯絡站主。" },
    disabled: { tag: "已停用", title: "這個帳號已被停用", lead: "目前無法進站。如有疑問請直接聯絡站主。" },
  };
  // 等待核准時每隔這麼久自己查一次（分頁在背景時不查，回到前景馬上查一次）。
  const POLL_MS = 30_000;
  // 剛自動進站過又被送回這一頁（照理不會發生）：改成顯示按鈕，不再自己跳，避免來回轉。
  const ENTER_KEY = "apply-entered-at";
  const ENTER_GUARD_MS = 60_000;
  // 背景查詢連續失敗這麼多次（例如登入過期）就不再假裝還在等，改顯示「再試一次」。
  const POLL_FAIL_LIMIT = 3;
  let timer = 0;
  let waiting = false;
  let pollFails = 0;

  async function api(path, opts) {
    const r = await fetch(path, { credentials: "same-origin", cache: "no-store", headers: { "content-type": "application/json" }, ...opts });
    let body = {};
    try { body = await r.json(); } catch {}
    if (!r.ok) throw new Error(body.error || "無法連線，請稍後再試");
    return body;
  }

  // 登入錯信箱的人要有路可以換：登出 Access，回來重新輸入信箱（和主站設定裡的「登出（換帳號）」同一個網址）。
  const switchNote = (me) => (me.logout_url
    ? `<p class="note">不是這個信箱？<a href="${esc(me.logout_url)}">換一個信箱登入</a></p>` : "");

  function renderForm(me) {
    view.innerHTML = `
      <h1>申請使用</h1>
      <p class="lead">這個網站只開放給站主核准的人。填好稱呼送出，站主同意後就能進站。</p>
      <p class="who">登入身分：${esc(me.email)}</p>
      <form id="f" novalidate>
        <label for="nick">怎麼稱呼你（站主看得到，其他使用者看不到）</label>
        <input id="nick" type="text" maxlength="40" autocomplete="nickname" required>
        <div class="row"><button type="submit" id="send">送出申請</button></div>
        <p class="msg" id="msg" role="alert"></p>
      </form>
      <p class="note">核准後直接打開網站就能用，同一台裝置一個月內不用再收驗證碼。</p>
      ${switchNote(me)}`;
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

  // 自己進站：先記下時間再跳。記不下來（瀏覽器不給存）或剛跳過一次，就不自己跳，回 false 讓畫面給「進入網站」按鈕。
  function enterSite() {
    try {
      if (Date.now() - Number(sessionStorage.getItem(ENTER_KEY) || 0) < ENTER_GUARD_MS) return false;
      sessionStorage.setItem(ENTER_KEY, String(Date.now()));
    } catch {
      return false;
    }
    location.replace("/");
    return true;
  }

  function render(me) {
    clearTimeout(timer);
    waiting = me.status === "pending";
    if (me.status === "none") return renderForm(me);
    if (me.status === "approved") {
      // 已核准的朋友不用停在這一頁：直接進站。站主留在這裡（有管理申請的入口）。
      if (!me.owner && enterSite()) return;
      view.innerHTML = `
        <span class="state approved">已核准</span>
        <h1 style="margin-top:12px">${me.owner ? "你是站主" : "已經可以進站了"}</h1>
        <p class="lead">${me.owner ? "站主不用申請，可以直接進入網站。" : "申請已核准，可以直接進入網站。"}</p>
        <p class="who">${esc(me.email)}</p>
        <div class="row"><a class="btn" href="/">進入網站</a>${me.owner ? '<a class="btn ghost" href="/admin/">管理申請</a>' : ""}</div>
        ${switchNote(me)}`;
      return;
    }
    const t = TEXT[me.status] || TEXT.pending;
    view.innerHTML = `
      <span class="state ${esc(me.status)}">${t.tag}</span>
      <h1 style="margin-top:12px">${t.title}</h1>
      <p class="lead">${t.lead}</p>
      <p class="who">${esc(me.email)}</p>
      <div class="row"><button class="ghost" id="again" type="button">重新查看狀態</button></div>
      ${switchNote(me)}`;
    document.getElementById("again").addEventListener("click", load);
    if (waiting) timer = setTimeout(poll, POLL_MS);
  }

  function renderError(err) {
    clearTimeout(timer);
    waiting = false;
    view.innerHTML = `<h1>暫時無法讀取</h1><p class="lead">${esc(err.message)}</p>
      <div class="row"><button class="ghost" id="again" type="button">再試一次</button></div>`;
    document.getElementById("again").addEventListener("click", load);
  }

  // 背景查一次。還在等待中就不重畫（畫面是 aria-live，重畫會讓報讀軟體每 30 秒重唸一次、鍵盤焦點也會掉）。
  // 偶爾查不到（斷線）維持畫面、下一輪再查；連續查不到才換成「再試一次」。
  async function poll() {
    clearTimeout(timer);
    if (!waiting) return;
    if (document.hidden) return;                 // 回到前景時 visibilitychange 會再叫一次
    let me;
    try {
      me = await api("/apply/api/me");
    } catch (err) {
      pollFails += 1;
      if (pollFails >= POLL_FAIL_LIMIT) return renderError(err);
      timer = setTimeout(poll, POLL_MS);
      return;
    }
    pollFails = 0;
    if (me.status === "pending") timer = setTimeout(poll, POLL_MS);
    else render(me);
  }

  async function load() {
    pollFails = 0;
    try {
      render(await api("/apply/api/me"));
    } catch (err) {
      renderError(err);
    }
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  load();
})();
