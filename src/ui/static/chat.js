"use strict";
/* Page orchestration: create once, subscribe/read the same durable turn. */
(() => {
  const RECENT = "roco_chat_recent_session", PENDING = "roco_chat_pending_";
  const el = RoundCard.el;
  let state, generation = 0, source = null, pollTimer = null, activeTurn = null;
  let views = new Map(), nextBefore = null, pending = null, selecting = false;
  const chat = () => document.querySelector("#chat");
  const scroller = () => document.scrollingElement;
  const input = () => document.querySelector("#input");
  function status(text, error = false) {
    const node = document.querySelector("#chat-status");
    node.textContent = text; node.dataset.error = String(error);
  }
  async function api(url, options = {}) {
    const res = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
    const data = await res.json();
    if (!res.ok) {
      const error = new Error(data.message || data.error || (typeof data.detail === "string" ? data.detail : "请求失败（HTTP " + res.status + "）"));
      error.code = data.error; error.data = data; error.status = res.status; throw error;
    }
    return data;
  }
  function nearBottom() { const c = scroller(); return c.scrollHeight - c.scrollTop - c.clientHeight < 90; }
  function scrollToLatest() { scroller().scrollTop = scroller().scrollHeight; }
  function follow(wasNear) {
    if (wasNear) scrollToLatest();
    else document.querySelector("#new-messages").hidden = false;
  }
  function controls() {
    const busy = selecting || !!pending || !!activeTurn;
    document.querySelector("#send-btn").disabled = busy;
    const stop = document.querySelector("#stop-btn");
    stop.hidden = !activeTurn;
    stop.disabled = !!activeTurn && state.turns.get(activeTurn)?.status === "cancelling";
    document.querySelector("#retry-send").hidden = !pending;
  }
  function stopListening() {
    source?.close(); source = null;
    clearTimeout(pollTimer); pollTimer = null;
  }
  function updateElapsed() {
    const turn = state?.turns.get(activeTurn), view = views.get(activeTurn);
    if (!turn || !view || !ChatState.isActive(turn.status)) return;
    const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(turn.created_at)) / 1000));
    view.badge.textContent = (turn.status === "cancelling" ? "正在停止…" : "执行中") +
      " · " + Object.keys(turn.rounds).length + " 轮 · 已用 " + seconds + " 秒";
  }
  function renderResult(container, result) {
    if (container.dataset.message === result.message) return;
    container.dataset.message = result.message; container.replaceChildren();
    const md = el("div", "reply-md"); md.innerHTML = renderMarkdown(result.message);
    const raw = el("pre", "reply-raw", result.message); raw.hidden = true;
    const bar = el("div", "reply-toolbar"), toggle = el("button", "", "查看原文"), copy = el("button", "", "复制");
    toggle.type = copy.type = "button";
    toggle.addEventListener("click", () => {
      raw.hidden = !raw.hidden; md.hidden = !raw.hidden;
      toggle.textContent = raw.hidden ? "查看原文" : "查看渲染";
    });
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(result.message); copy.textContent = "已复制"; }
      catch { copy.textContent = "复制失败，请切换原文复制"; }
    });
    bar.append(toggle, copy); container.append(md, raw, bar);
    if (result.usage?.total_tokens) container.append(el("div", "usage",
      "tokens 输入 " + (result.usage.input_tokens || 0) + " · 输出 " + (result.usage.output_tokens || 0) + " · 总计 " + result.usage.total_tokens));
  }
  function renderTurn(t, { prepend = false } = {}) {
    const wasNear = nearBottom();
    let view = views.get(t.turn_id);
    if (!view) {
      const root = el("section", "chat-turn"); root.dataset.turnId = t.turn_id;
      const user = el("div", "bubble user", t.message), rounds = el("div", "turn-rounds");
      const result = el("div", "bubble reply"), badge = el("div", "turn-status"); result.hidden = true;
      root.append(user, rounds, result, badge);
      view = { root, rounds, result, badge, cards: new Map() }; views.set(t.turn_id, view);
      if (prepend) chat().insertBefore(root, document.querySelector("#older-messages").nextSibling);
      else chat().append(root);
    }
    Object.values(t.rounds).sort((a, b) => a.round_index - b.round_index).forEach((r, i) => {
      let card = view.cards.get(r.round_id);
      if (!card) {
        card = RoundCard.create(xid => api("/api/chat/turns/" + t.turn_id + "/tools/" + xid));
        view.cards.set(r.round_id, card);
      }
      card.update(r); RoundCard.placeChild(view.rounds, card.root, i);
    });
    if (t.result) { view.result.hidden = false; renderResult(view.result, t.result); }
    const labels = { running: "执行中", cancelling: "正在停止…", completed: "已完成", degraded: "已返回阶段结果",
      failed: "执行失败", timed_out: "已超时", cancelled: "已停止", interrupted: "已中断" };
    view.badge.textContent = labels[t.status] || t.status;
    if (ChatState.isActive(t.status)) {
      activeTurn = t.turn_id;
      const budget = t.budget || {};
      status("执行中 · 最多 " + budget.max_llm_rounds + " 轮 · 总预算 " + budget.max_total_seconds + " 秒");
    } else if (activeTurn === t.turn_id) {
      activeTurn = null; status(labels[t.status] || "已结束");
    }
    updateElapsed(); controls(); if (!prepend) follow(wasNear);
  }
  function accept(snap, gen) {
    if (gen !== generation || !ChatState.snapshot(state, snap)) return;
    renderTurn(snap);
  }
  async function poll(tid, gen) {
    if (gen !== generation) return;
    try {
      const snap = await api("/api/chat/turns/" + tid);
      if (gen !== generation) return;
      accept(snap, gen);
      if (ChatState.isActive(snap.status)) pollTimer = setTimeout(() => poll(tid, gen), 1000);
    } catch (e) {
      if (gen !== generation) return;
      status(e.message + "；正在尝试恢复连接", true);
      if (e.code === "CHAT_STORAGE_UNAVAILABLE") { activeTurn = null; controls(); return; }
      pollTimer = setTimeout(() => poll(tid, gen), 2000);
    }
  }
  function subscribe(tid, gen) {
    stopListening();
    const turn = state.turns.get(tid);
    if (!turn || !ChatState.isActive(turn.status)) return;
    if (!window.EventSource) { poll(tid, gen); return; }
    const es = new EventSource("/api/chat/turns/" + tid + "/events?after_seq=" + turn.last_seq);
    source = es;
    es.onmessage = e => {
      if (gen !== generation) { es.close(); return; }
      let data; try { data = JSON.parse(e.data); } catch { return; }
      if (data.event === "stream.error") { es.close(); poll(tid, gen); return; }
      const applied = ChatState.event(state, data);
      if (applied === "gap") { es.close(); poll(tid, gen); return; }
      if (applied) renderTurn(state.turns.get(tid));
      if (data.event === "done") es.close();
    };
    es.onerror = () => { es.close(); if (gen === generation) poll(tid, gen); };
  }
  function clearPending(sid) {
    sessionStorage.removeItem(PENDING + sid); pending = null;
    document.querySelector("#pending-message")?.remove(); controls();
  }
  function showPending() {
    if (!pending) return;
    let node = document.querySelector("#pending-message");
    if (!node) { node = el("div", "bubble user"); node.id = "pending-message"; chat().append(node); }
    node.textContent = pending.message + "（正在确认提交）";
  }
  async function submitPending() {
    if (!pending || selecting) return;
    const gen = generation, sid = state.sessionId, request = { ...pending };
    document.querySelector("#retry-send").disabled = true;
    showPending(); status("请求已提交，正在确认…"); controls();
    try {
      const snap = await api("/api/chat/sessions/" + sid + "/turns", { method: "POST", body: JSON.stringify(request) });
      if (gen !== generation) return;
      clearPending(sid); accept(snap, gen); subscribe(snap.turn_id, gen);
    } catch (e) {
      if (gen !== generation) return;
      if ([409, 422, 429, 404].includes(e.status)) {
        input().value = request.message; clearPending(sid);
        status(e.code === "SESSION_BUSY" ? "当前会话已有任务运行，草稿已保留。" : e.message, true);
        if (e.data?.active_turn_id) poll(e.data.active_turn_id, gen);
      } else {
        status("提交结果尚未确认。重试将使用同一请求编号：" + e.message, true);
      }
    } finally {
      if (gen === generation) { document.querySelector("#retry-send").disabled = false; controls(); }
    }
  }
  async function selectSession(sid, push = false) {
    const gen = ++generation; stopListening(); selecting = true; activeTurn = null; pending = null;
    state = ChatState.create(sid); views = new Map(); controls();
    chat().querySelectorAll(".chat-turn, #pending-message, .welcome").forEach(n => n.remove());
    document.querySelector("#new-messages").hidden = true;
    status("正在恢复会话…");
    try {
      const [session, history] = await Promise.all([api("/api/chat/sessions/" + sid), api("/api/chat/sessions/" + sid + "/messages")]);
      if (gen !== generation) return;
      if (push) window.history.pushState({}, "", "/chat/" + sid);
      else window.history.replaceState({}, "", "/chat/" + sid);
      localStorage.setItem(RECENT, sid);
      history.turns.forEach(t => accept(t, gen));
      nextBefore = history.next_before; document.querySelector("#older-messages").hidden = !nextBefore;
      if (!history.turns.length) status("新会话已就绪");
      else if (!session.active_turn_id) status("会话已恢复");
      selecting = false;
      const stored = sessionStorage.getItem(PENDING + sid);
      try { pending = stored ? JSON.parse(stored) : null; } catch { sessionStorage.removeItem(PENDING + sid); }
      if (pending && history.turns.some(t => t.request_id === pending.request_id)) clearPending(sid);
      if (session.active_turn_id) {
        const snap = await api("/api/chat/turns/" + session.active_turn_id);
        if (gen !== generation) return;
        accept(snap, gen); subscribe(snap.turn_id, gen);
      }
      if (pending) await submitPending();
      if (gen !== generation) return;
      scrollToLatest();
    } catch (e) {
      if (gen !== generation) return;
      status("无法恢复会话：" + e.message + "。可点击新建会话。", true);
      selecting = true;
    } finally { if (gen === generation) controls(); }
  }
  async function newSession() {
    const button = document.querySelector("#new-session-btn"); button.disabled = true;
    const gen = generation;
    try {
      const session = await api("/api/chat/sessions", { method: "POST" });
      if (gen !== generation) return;
      input().value = ""; await selectSession(session.id, true);
    } catch (e) { if (gen === generation) status("新建失败：" + e.message, true); }
    finally { button.disabled = false; }
  }
  function send() {
    const message = input().value.trim();
    if (!message || selecting || pending || activeTurn) return;
    pending = { request_id: crypto.randomUUID(), message };
    sessionStorage.setItem(PENDING + state.sessionId, JSON.stringify(pending));
    input().value = ""; submitPending();
  }
  document.addEventListener("DOMContentLoaded", async () => {
    // Reads the selected state each tick, so a previous session cannot update this page.
    setInterval(updateElapsed, 1000);
    document.querySelector("#send-btn").addEventListener("click", send);
    input().addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); send(); }
    });
    document.querySelector("#new-session-btn").addEventListener("click", newSession);
    document.querySelector("#retry-send").addEventListener("click", submitPending);
    document.querySelector("#stop-btn").addEventListener("click", async () => {
      const tid = activeTurn, gen = generation;
      if (!tid) return;
      try { accept(await api("/api/chat/turns/" + tid + "/cancel", { method: "POST" }), gen); }
      catch (e) { if (gen === generation) status("停止请求失败：" + e.message, true); }
    });
    document.querySelector("#new-messages").addEventListener("click", () => {
      scrollToLatest(); document.querySelector("#new-messages").hidden = true;
    });
    document.addEventListener("scroll", () => { if (nearBottom()) document.querySelector("#new-messages").hidden = true; }, { passive: true });
    document.querySelector("#older-messages").addEventListener("click", async () => {
      if (!nextBefore) return;
      const gen = generation, previousHeight = scroller().scrollHeight, previousTop = scroller().scrollTop;
      try {
        const page = await api("/api/chat/sessions/" + state.sessionId + "/messages?before=" + nextBefore);
        if (gen !== generation) return;
        [...page.turns].reverse().forEach(t => { if (ChatState.snapshot(state, t)) renderTurn(t, { prepend: true }); });
        nextBefore = page.next_before; document.querySelector("#older-messages").hidden = !nextBefore;
        scroller().scrollTop = previousTop + scroller().scrollHeight - previousHeight;
      } catch (e) { if (gen === generation) status(e.message, true); }
    });
    window.addEventListener("popstate", () => {
      const sid = location.pathname.match(/^\/chat\/([^/]+)$/)?.[1];
      if (sid) selectSession(sid); else newSession();
    });
    const sid = location.pathname.match(/^\/chat\/([^/]+)$/)?.[1] || localStorage.getItem(RECENT);
    if (sid) {
      await selectSession(sid);
      // An explicit stale address remains visible; a stale recent preference opens a new session.
      if (selecting && location.pathname === "/") await newSession();
    } else await newSession();
  });
})();
