// 雲端版限定：站主在大盤頁看到「帳號管理」入口與待核准人數；其他人什麼都不顯示。
(async () => {
  try {
    const me = await (await fetch("/api/me", { credentials: "same-origin" })).json();
    if (!me.owner) return;
    let pending = 0;
    try {
      const { users } = await (await fetch("/api/admin/users", { credentials: "same-origin" })).json();
      pending = users.filter((u) => u.status === "pending").length;
    } catch {}
    const style = document.createElement("style");
    style.textContent = `
      .owner-entry { position: fixed; right: 16px; bottom: 16px; z-index: 40; display: inline-flex; align-items: center; gap: 8px;
        min-height: 40px; padding: 0 14px; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--panel);
        color: var(--ink); font: 600 13px/1 var(--sans); text-decoration: none; box-shadow: 0 6px 18px rgb(0 0 0 / 12%); }
      .owner-entry:hover { border-color: var(--accent); }
      .owner-entry:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
      .owner-entry b { min-width: 20px; height: 20px; padding: 0 6px; border-radius: 999px; background: var(--accent); color: #fff;
        display: inline-grid; place-items: center; font: 700 12px/1 var(--mono); }
      body.panel-open .owner-entry { display: none; }
      /* 本人 11-07：手機不讓浮動按鈕蓋住主圖，改放在頁面最下方。 */
      @media (max-width: 760px) { .owner-entry { position: static; display: flex; justify-content: center; margin: 16px auto 24px; width: max-content; box-shadow: none; } }`;
    document.head.append(style);
    const a = document.createElement("a");
    a.className = "owner-entry";
    a.href = "/admin/";
    a.setAttribute("aria-label", pending ? `帳號管理，${pending} 人等待核准` : "帳號管理");
    a.innerHTML = `帳號管理${pending ? ` <b>${pending}</b>` : ""}`;
    document.body.append(a);
  } catch {}
})();
