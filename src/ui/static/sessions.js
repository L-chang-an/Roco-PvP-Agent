"use strict";
window.Sessions = (() => {
  let api, select, selected = null, cursor = null, generation = 0, channel;
  const el = RoundCard.el;
  const draftKey = sid => "roco_chat_draft_" + sid;
  function changed() {
    refresh();
    channel?.postMessage("refresh");
    localStorage.setItem("roco_chat_list_changed", String(Date.now()));
  }
  function setSelected(session) {
    selected = session;
    document.querySelector("#session-title").textContent = session?.title || "欢迎使用组队顾问";
    document.querySelectorAll(".session-link").forEach(n => n.setAttribute("aria-current", n.dataset.sid === session?.id ? "page" : "false"));
  }
  function saveDraft(sid, text) { if (sid) localStorage.setItem(draftKey(sid), text); }
  function draft(sid) { return localStorage.getItem(draftKey(sid)) || ""; }
  function groupName(stamp) {
    const date = new Date(stamp), today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
    return date.toDateString() === today.toDateString() ? "今天" : date.toDateString() === yesterday.toDateString() ? "昨天" : "更早";
  }
  async function refresh(more = false) {
    const gen = ++generation, list = document.querySelector("#session-list");
    if (!list) return;
    const q = document.querySelector("#session-search").value;
    const archived = document.querySelector("#show-archived").checked;
    try {
      const page = await api("/api/chat/sessions?" + new URLSearchParams({q, archived, ...(more && cursor ? {cursor} : {})}));
      if (gen !== generation) return;
      if (!more) list.replaceChildren();
      let previousGroup = more ? [...list.querySelectorAll('.session-date-group')].at(-1)?.textContent : null;
      for (const s of page.sessions) {
        if (selected?.id === s.id) setSelected(s);
        // De-duplicate if another tab updated ordering while paging.
        list.querySelectorAll(".session-entry").forEach(n => { if (n.dataset.sid === s.id) n.remove(); });
        const row = el("section", "session-entry"); row.dataset.sid = s.id;
        const group = groupName(s.updated_at);
        if (group !== previousGroup) { list.append(el('h3', 'session-date-group', group)); previousGroup = group; }
        const link = el("a", "session-link", s.title); link.href = "/chat/" + s.id; link.dataset.sid = s.id;
        link.setAttribute("aria-current", selected?.id === s.id ? "page" : "false");
        link.addEventListener("click", e => {
          if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
          e.preventDefault(); document.body.classList.remove("sessions-open"); select(s.id, true);
        });
        const hint = el("small", "", s.active_turn_id ? "运行中" : s.last_status === "interrupted" ? "已中断" : new Date(s.updated_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}));
        const preview = el("p", "session-preview", s.preview);
        const menu = el("details", "session-menu"), heading = el("summary", "", "管理"); menu.append(heading);
        const error = el("p", "action-error"); error.setAttribute("role", "status");
        const action = (label, fn) => {
          const b = el("button", "", label); b.type = "button";
          b.addEventListener("click", async () => {
            b.disabled = true; error.textContent = "";
            try { await fn(); changed(); } catch (e) {
              error.textContent = e.code === "SESSION_BUSY" ? "请先打开会话并停止运行中的请求。" : e.message;
              if (e.code === 'REVISION_CONFLICT' && e.data.current) { Object.assign(s, e.data.current); link.textContent = s.title; }
            }
            finally { b.disabled = false; }
          }); menu.append(b);
        };
        action("重命名", async () => {
          const title = prompt("会话新标题（最多 100 字符）", s.title);
          if (title === null) return;
          const updated = await api("/api/chat/sessions/" + s.id, {method: "PATCH", body: JSON.stringify({revision: s.revision, title})});
          if (selected?.id === s.id) setSelected(updated);
        });
        action(s.archived_at ? "恢复会话" : "归档", async () => {
          const updated = await api("/api/chat/sessions/" + s.id, {method: "PATCH", body: JSON.stringify({revision: s.revision, archived: !s.archived_at})});
          if (selected?.id === s.id) await select(updated.id);
        });
        action("删除", async () => {
          if (!confirm("删除会话「" + s.title + "」及其聊天记录？已保存的队伍文件会保留。")) return;
          await api("/api/chat/sessions/" + s.id + "?revision=" + s.revision, {method: "DELETE"});
          localStorage.removeItem(draftKey(s.id));
          if (localStorage.getItem("roco_chat_recent_session") === s.id) localStorage.removeItem("roco_chat_recent_session");
          if (selected?.id === s.id) window.ChatUI.welcome(true);
        });
        row.append(hint, link, preview, menu, error); list.append(row);
      }
      cursor = page.next_cursor; document.querySelector("#more-sessions").hidden = !cursor;
      document.querySelector("#sessions-error").textContent = "";
    } catch (e) { if (gen === generation) document.querySelector("#sessions-error").textContent = e.message; }
  }
  function init(apiFn, selectFn) {
    api = apiFn; select = selectFn;
    const header = document.querySelector('header');
    new ResizeObserver(() => document.body.style.setProperty('--chat-header-height', header.offsetHeight + 'px')).observe(header);
    document.querySelector("#session-search").addEventListener("input", () => refresh());
    document.querySelector("#show-archived").addEventListener("change", () => refresh());
    document.querySelector("#more-sessions").addEventListener("click", () => refresh(true));
    document.querySelector("#sessions-toggle").addEventListener("click", () => {
      const open = document.body.classList.toggle("sessions-open");
      document.querySelector("#sessions-toggle").setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") document.body.classList.remove("sessions-open"); });
    if (window.BroadcastChannel) { channel = new BroadcastChannel("roco-chat-sessions"); channel.onmessage = () => refresh(); }
    window.addEventListener("storage", e => { if (e.key === "roco_chat_list_changed") refresh(); });
    setInterval(() => { if (!document.hidden && !document.querySelector(".session-menu[open]") && !document.querySelector('#sessions-sidebar').contains(document.activeElement)) refresh(); }, 5000);
    refresh();
  }
  return {init, changed, refresh, setSelected, saveDraft, draft};
})();
