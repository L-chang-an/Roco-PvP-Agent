"use strict";
/* A shared reducer for durable snapshots and ordered live events. */
window.ChatState = (() => {
  const active = new Set(["pending", "running", "cancelling"]);
  function create(sessionId) { return { sessionId, turns: new Map() }; }
  function snapshot(state, incoming) {
    if (incoming.session_id !== state.sessionId) return false;
    const old = state.turns.get(incoming.turn_id);
    if (old && old.last_seq > incoming.last_seq) return false;
    state.turns.set(incoming.turn_id, incoming);
    return true;
  }
  function event(state, e) {
    if (e.schema_version !== 2 || e.session_id !== state.sessionId) return false;
    const turn = state.turns.get(e.turn_id);
    if (!turn || e.seq <= turn.last_seq) return false;
    // A gap requires a snapshot, not guessing the missing updates.
    if (e.seq !== turn.last_seq + 1) return "gap";
    const p = e.payload;
    if (e.round_id) {
      const r = turn.rounds[e.round_id] ||= { round_id: e.round_id, round_index: e.round_index,
        status: "running", title: "正在分析请求…", tools: {}, summary: [],
        thought_summary: null, summary_status: "pending" };
      if (e.event === "round.summary") {
        r.thought_summary = p.text; r.summary_status = p.status;
      } else if (e.event === "round.progress") {
        r.phase = p.phase; r.progress = p.text;
        if (p.summary !== undefined) r.summary = p.summary;
        if (p.has_warnings !== undefined) r.has_warnings = p.has_warnings;
      } else if (e.event === "round.completed") {
        Object.assign(r, p);
        if (r.summary_status === "pending") r.summary_status = "missing";
      } else if (e.event.startsWith("tool.")) {
        r.tools[p.tool_execution_id] = { ...r.tools[p.tool_execution_id], ...p };
      }
    }
    if (e.event === "reply") { turn.result = p.result; turn.offline = !!p.offline; }
    if (e.event === "done" || e.event === "turn.cancelling") turn.status = p.status;
    turn.last_seq = e.seq;
    return true;
  }
  return { create, snapshot, event, isActive: status => active.has(status) };
})();
