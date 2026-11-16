// 開放給朋友 01 票：Gemini 設定頁。同意 → 開 AI Studio 建金鑰 → 貼回 → 網站試呼叫 → 完成。
// 金鑰只送到本站的 /api/friend-ai/key，不存在頁面的任何地方；送出後立刻清掉輸入框。
(() => {
  const view = document.getElementById("view");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("zh-TW", { hour12: false }) : "");
  const back = '<div class="row"><a class="btn ghost" href="/">回到網站</a></div>';
  let state = null;

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "content-type": "application/json" }, ...opts });
    let body = {};
    try { body = await r.json(); } catch {}
    return { ok: r.ok, status: r.status, body };
  }

  const consentBoxes = () => state.consent.items
    .map((item) => `<label class="check"><input type="checkbox" data-consent="${esc(item.id)}"><span>${esc(item.text)}</span></label>`)
    .join("");
  const consentBody = () => {
    const body = { version: state.consent.version };
    for (const box of view.querySelectorAll("[data-consent]")) body[box.dataset.consent] = box.checked;
    return body;
  };
  const allChecked = () => [...view.querySelectorAll("[data-consent]")].every((box) => box.checked);
  const keyField = (label) => `
    <input type="password" id="key" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Gemini 金鑰" placeholder="AIza…">
    <div class="row"><button type="button" id="save">${label}</button></div>`;

  // 限速的回應 error 是英文代碼、說明在 message；其他錯誤的說明在 error。
  const errorText = (res, fallback) => res?.body?.message || res?.body?.error || fallback;

  function say(text) {
    const msg = view.querySelector(".msg");
    if (msg) msg.textContent = text || "";
  }

  async function submitKey(withConsent) {
    const input = view.querySelector("#key");
    const button = view.querySelector("#save");
    const key = input.value;
    if (!key.trim()) { say("請先貼上金鑰"); input.focus(); return; }
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "確認中…";
    say("");
    const body = withConsent ? { key, consent: consentBody() } : { key };
    const res = await api("/api/friend-ai/key", { method: "POST", body: JSON.stringify(body) }).catch(() => null);
    input.value = "";
    if (res?.ok) { state = res.body; renderDone(); return; }
    button.disabled = false;
    button.textContent = label;
    say(errorText(res, "操作失敗，請稍後再試"));
  }

  function renderSetup() {
    view.innerHTML = `
      <h1>Gemini 設定</h1>
      <ol class="steps">
        <li><h2>同意</h2>${consentBoxes()}</li>
        <li><h2>建立金鑰</h2>
          <div class="row"><button type="button" id="studio" disabled>開啟 AI Studio</button></div></li>
        <li><h2>貼上金鑰</h2><fieldset id="paste" disabled>${keyField("確認並保存")}</fieldset></li>
      </ol>
      <p class="msg" role="alert"></p>
      ${back}`;
    const sync = () => {
      const ok = allChecked();
      view.querySelector("#studio").disabled = !ok;
      view.querySelector("#paste").disabled = !ok;
    };
    view.querySelectorAll("[data-consent]").forEach((box) => box.addEventListener("change", sync));
    view.querySelector("#studio").addEventListener("click", () => window.open(state.studio_url, "_blank", "noopener,noreferrer"));
    view.querySelector("#save").addEventListener("click", () => submitKey(true));
  }

  function renderConfigured() {
    view.innerHTML = `
      <h1>Gemini 設定</h1>
      <p><span class="state approved">已設定</span>${state.last_success_at ? `<span class="note inline">最後成功 ${esc(fmt(state.last_success_at))}</span>` : ""}</p>
      ${state.last_failure_reason ? `<p class="msg">${esc(state.last_failure_reason)}</p>` : ""}
      <div id="replace" hidden>
        <div class="row"><button type="button" class="ghost" id="studio">開啟 AI Studio</button></div>
        ${keyField("確認並換上")}
      </div>
      <div class="row" id="actions">
        <button type="button" class="ghost" id="swap">換一把金鑰</button>
        <button type="button" class="warn" id="drop">刪除金鑰</button>
      </div>
      <p class="msg" role="alert"></p>
      ${back}`;
    view.querySelector("#swap").addEventListener("click", () => {
      view.querySelector("#replace").hidden = false;
      view.querySelector("#swap").hidden = true;
      view.querySelector("#key").focus();
    });
    view.querySelector("#studio").addEventListener("click", () => window.open(state.studio_url, "_blank", "noopener,noreferrer"));
    view.querySelector("#save").addEventListener("click", () => submitKey(false));
    view.querySelector("#drop").addEventListener("click", async (e) => {
      if (!confirm("刪除後網站不再用這把金鑰產生你的解讀。確定刪除？")) return;
      e.target.disabled = true;
      const res = await api("/api/friend-ai/key", { method: "DELETE" }).catch(() => null);
      if (res?.ok) return load();
      e.target.disabled = false;
      say(errorText(res, "刪除失敗，請稍後再試"));
    });
  }

  function renderReconsent() {
    view.innerHTML = `
      <h1>Gemini 設定</h1>
      <p class="lead">同意內容已更新，請重新確認。</p>
      ${consentBoxes()}
      <div class="row"><button type="button" id="agree" disabled>重新同意</button></div>
      <p class="msg" role="alert"></p>
      ${back}`;
    const agree = view.querySelector("#agree");
    view.querySelectorAll("[data-consent]").forEach((box) => box.addEventListener("change", () => { agree.disabled = !allChecked(); }));
    agree.addEventListener("click", async () => {
      agree.disabled = true;
      const res = await api("/api/friend-ai/consent", { method: "POST", body: JSON.stringify(consentBody()) }).catch(() => null);
      if (res?.ok) { state = res.body; return render(); }
      agree.disabled = false;
      say(errorText(res, "操作失敗，請稍後再試"));
    });
  }

  function renderDone() {
    view.innerHTML = `
      <h1>設定完成</h1>
      <p class="lead">下一個交易日收盤更新後，會看到第一份解讀。</p>
      ${back}`;
  }

  function render() {
    if (state.configured && state.consented) return renderConfigured();
    if (state.configured) return renderReconsent();
    if (state.full) {
      view.innerHTML = `<h1>Gemini 設定</h1><p class="lead">設定人數已滿（最多 ${esc(state.limit)} 人），目前無法新增。</p>${back}`;
      return;
    }
    renderSetup();
  }

  async function load() {
    const res = await api("/api/friend-ai").catch(() => null);
    if (!res?.ok || !res.body.available) {
      view.innerHTML = `<h1>Gemini 設定</h1><p class="lead">${esc(res?.ok ? "目前沒有開放" : errorText(res, "暫時無法讀取，請稍後再試"))}</p>${back}`;
      return;
    }
    state = res.body;
    render();
  }

  load();
})();
