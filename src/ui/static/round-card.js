"use strict";
window.RoundCard = (() => {
  const labels = { running: "进行中", completed: "已完成", failed: "失败", skipped: "已跳过",
    timed_out: "已超时", cancelled: "已停止", interrupted: "已中断", cancelling: "正在停止" };
  const warningDomains = new Set(["invalid", "error", "not_found", "disabled", "truncated", "empty"]);
  function el(tag, className, text) {
    const node = document.createElement(tag); node.className = className || "";
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function placeChild(parent, node, index) {
    // Moving an already positioned node drops keyboard focus in Chromium.
    if (parent.children[index] !== node) parent.insertBefore(node, parent.children[index] || null);
  }
  function create(loadDetails) {
    const root = el("details", "round-card");
    const heading = el("summary", "round-heading");
    const title = el("span", "round-title"), meta = el("span", "round-meta");
    heading.append(title, meta);
    const body = el("div", "round-body");
    const thought = el("p", "round-thought"), execution = el("div", "round-execution");
    const tools = el("div", "round-tools");
    body.append(el("h3", "", "思考摘要 · 模型说明"), thought,
      el("h3", "", "执行结果摘要"), execution, el("h3", "", "工具列表"), tools);
    root.append(heading, body);
    const rows = new Map();
    function toolRow(t) {
      let view = rows.get(t.tool_execution_id);
      if (!view) {
        const row = el("article", "round-tool");
        const name = el("div", "tool-heading"), args = el("p", "tool-args"), result = el("p", "tool-brief");
        const highlights = el("ul", "tool-highlights"), details = el("details", "tool-details");
        const summary = el("summary", "", "查看结果详情"), content = el("pre", "", "尚未加载");
        const retry = el("button", "", "重新读取详情"); retry.type = "button"; retry.hidden = true;
        details.append(summary, content, retry); row.append(name, args, result, highlights, details);
        view = { row, name, args, result, highlights, details, content, retry, loading: false, loaded: false, data: t };
        const load = async () => {
          if (view.loaded || view.loading || !view.data.details_available) return;
          view.loading = true; retry.hidden = true; content.textContent = "正在读取…";
          try {
            const data = await loadDetails(view.data.tool_execution_id);
            content.textContent = JSON.stringify(data, null, 2); view.loaded = true;
          } catch (e) {
            content.textContent = "详情读取失败：" + e.message; retry.hidden = false;
          } finally { view.loading = false; }
        };
        details.addEventListener("toggle", () => { if (details.open) load(); });
        retry.addEventListener("click", load);
        rows.set(t.tool_execution_id, view);
      }
      view.data = t;
      const warning = warningDomains.has(t.domain_status);
      view.row.dataset.status = warning ? "warning" : t.status;
      const duration = t.duration_ms == null ? "" : " · " + Math.round(t.duration_ms) + " ms";
      view.name.textContent = (t.display_name || t.name) + " · " + t.name + " · " +
        (labels[t.status] || t.status) + (warning ? " · 需注意" : "") + duration;
      view.args.textContent = "本次检查：" + (t.args_summary || "—");
      view.result.textContent = "结果：" + (t.result_summary || "正在等待…");
      view.highlights.replaceChildren(...(t.highlights || []).map(h => el("li", "", h)));
      view.details.hidden = !t.details_available;
      return view.row;
    }
    function update(r) {
      root.dataset.roundId = r.round_id; root.dataset.status = r.status;
      title.textContent = r.status === "running" ? (r.progress || r.title) : r.title;
      const count = Object.keys(r.tools).length;
      meta.textContent = "第 " + r.round_index + " 轮 · " + count + " 项工具调用 · " +
        (labels[r.status] || r.status) + (r.has_warnings ? " · 需注意" : "") +
        (r.duration_ms == null ? "" : " · " + (r.duration_ms / 1000).toFixed(1) + " 秒");
      thought.textContent = r.thought_summary || (r.summary_status === "pending" ?
        "模型正在分析，等待本轮摘要…" : "本轮未提供思考摘要");
      execution.replaceChildren(...(r.summary.length ? r.summary : [r.status === "running" ?
        "工具结果将在执行后显示。" : "本轮没有工具执行结果。"]).map(s => el("p", "", s)));
      Object.values(r.tools).sort((a, b) => a.call_index - b.call_index)
        .forEach((t, i) => placeChild(tools, toolRow(t), i));
    }
    return { root, update };
  }
  return { create, el, placeChild };
})();
