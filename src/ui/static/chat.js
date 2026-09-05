"use strict";

/** 聊天逻辑：session 管理、SSE 消费、事件渲染、POST 兜底。 */

const SESSION_KEY = "roco_pvp_session_id";

function getSessionId() {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    // 本地先生成一个兜底 id；SSE 的 meta 事件会回传服务端权威 id 并覆盖
    id = "s_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function setBusy(busy) {
  $("#send-btn").disabled = busy;
  $("#input").disabled = busy;
  $("#input").placeholder = busy ? "AI 思考中…" : "输入消息，Enter 发送";
}

/* ------------------------------------------------------------------ *
 * 折叠的"思考过程"卡片：一次回复的思考文本 + 工具调用收进一张卡，      *
 * 默认隐藏正文，点"显示思考过程 ▾"才展开。                            *
 * ------------------------------------------------------------------ */

let _turn = null; // { body, label, thinking: [], tools: [] }

function ensureTurn() {
  if (_turn) return _turn;
  const card = document.createElement("div");
  card.className = "bubble reason";

  const header = document.createElement("div");
  header.className = "reason-header";

  const label = document.createElement("span");
  label.className = "reason-label";
  label.textContent = "⏳ 正在连接顾问…";

  const btn = document.createElement("button");
  btn.className = "reason-toggle";
  btn.textContent = "查看工作过程 ▾";

  const body = document.createElement("div");
  body.className = "reason-body"; // 默认折叠：CSS display:none（不要用 hidden 属性，会被 .reason-body{display:flex} 覆盖）

  let open = false;
  btn.addEventListener("click", () => {
    open = !open;
    body.classList.toggle("open", open);
    btn.textContent = open ? "收起 ▲" : "查看工作过程 ▾";
    btn.setAttribute("aria-expanded", String(open));
  });

  header.appendChild(label);
  header.appendChild(btn);
  card.appendChild(header);
  card.appendChild(body);
  $("#chat").appendChild(card);
  scrollToBottom();

  _turn = { body, label, thinking: [], tools: [], progress: [], latestStatus: "正在连接顾问…" };
  return _turn;
}

function refreshLabel(t) {
  const names = t.tools.map((x) => x.name).join(", ");
  const namesText = names ? "（" + names + "）" : "";
  const counts = " · 🔧 工具 × " + t.tools.length + namesText;
  t.label.textContent = "⏳ " + t.latestStatus + counts;
}

function addThinking(text) {
  const t = ensureTurn();
  t.thinking.push(text);
  const el = document.createElement("div");
  el.className = "reason-thinking";
  el.textContent = "💭 " + text;
  t.body.appendChild(el);
  refreshLabel(t);
  scrollToBottom();
}

function addProgress(text) {
  // 轻量进度行：等待期反馈（第几轮/正在调工具），非原始思维链。
  const t = ensureTurn();
  t.progress.push(text);
  t.latestStatus = text;
  const el = document.createElement("div");
  el.className = "reason-progress";
  el.textContent = "⏳ " + text;
  t.body.appendChild(el);
  refreshLabel(t); // 卡片折叠时，标题也必须让用户看见当前进度
  scrollToBottom();
}

function addTool(name, args, result) {
  const t = ensureTurn();
  t.tools.push({ name });
  t.latestStatus = "已完成 " + name;
  const el = document.createElement("div");
  el.className = "reason-tool";
  el.innerHTML =
    '<span class="tool-name">🔧 ' + escapeHtml(name) + "</span> " +
    "<code>" + escapeHtml(JSON.stringify(args)) + "</code>" +
    '<div class="tool-result">→ ' + escapeHtml(result) + "</div>";
  t.body.appendChild(el);
  refreshLabel(t);
  scrollToBottom();
}

function endTurn() {
  if (_turn) {
    _turn.latestStatus = "工作过程已完成";
    refreshLabel(_turn);
  }
  _turn = null;
}

/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ *
 * 终稿回复气泡：默认 Markdown 渲染展示；可切"查看原文"，可复制原文。 *
 * 打字机先把原文逐字打出来（体现流式），打完后默认切到渲染视图。    *
 * ------------------------------------------------------------------ */

function renderReply(text, usage) {
  const bubble = createBubble("reply", "");

  // 渲染视图（默认显示）
  const md = document.createElement("div");
  md.className = "reply-md";
  md.hidden = true;

  // 原文视图
  const raw = document.createElement("pre");
  raw.className = "reply-raw";

  // 工具条：查看原文/渲染 + 复制
  const toolbar = document.createElement("div");
  toolbar.className = "reply-toolbar";
  const toggleBtn = document.createElement("button");
  toggleBtn.textContent = "查看原文";
  const copyBtn = document.createElement("button");
  copyBtn.textContent = "复制";
  toolbar.appendChild(toggleBtn);
  toolbar.appendChild(copyBtn);

  bubble.appendChild(md);
  bubble.appendChild(raw);
  bubble.appendChild(toolbar);

  let rawText = "";
  let finished = false;

  const showMd = () => {
    md.hidden = false;
    raw.hidden = true;
    toggleBtn.textContent = "查看原文";
  };
  const showRaw = () => {
    md.hidden = true;
    raw.hidden = false;
    toggleBtn.textContent = "查看渲染";
  };

  toggleBtn.addEventListener("click", () => {
    if (!finished) return;
    if (md.hidden) {
      showMd();
    } else {
      showRaw();
    }
  });

  copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(rawText).then(() => {
      copyBtn.textContent = "已复制";
      setTimeout(() => {
        copyBtn.textContent = "复制";
      }, 1500);
    });
  });

  // 打字机：原文逐字进 raw 视图
  let i = 0;
  // 固定逐字符会让 1 万字结构化建议额外播放 80 秒；按长度分块，保留可见流式效果，
  // 同时把前端完整呈现时间控制在约 2 秒（最多约 250 帧 × 8ms）。
  const charsPerTick = Math.max(1, Math.ceil(text.length / 250));
  function tick() {
    if (i < text.length) {
      i = Math.min(text.length, i + charsPerTick);
      raw.textContent = text.slice(0, i);
      scrollToBottom();
      setTimeout(tick, 8);
    } else {
      // 打完 → 渲染 markdown 并默认显示渲染视图
      rawText = text;
      md.innerHTML = renderMarkdown(text);
      finished = true;
      showMd();
      if (usage && usage.total_tokens) {
        const note = document.createElement("div");
        note.className = "usage";
        note.textContent =
          "⚡ tokens 输入 " + (usage.input_tokens ?? 0) +
          " · 输出 " + (usage.output_tokens ?? 0) +
          " · 总计 " + usage.total_tokens;
        bubble.appendChild(note);
      }
      scrollToBottom();
    }
  }
  tick();
}

function onEvent(data) {
  switch (data.event) {
    case "meta":
      if (data.session_id) sessionStorage.setItem(SESSION_KEY, data.session_id);
      break;
    case "thinking":
      addThinking(data.text);
      break;
    case "progress":
      addProgress(data.text);
      break;
    case "tool":
      addTool(data.name, data.args, data.result);
      break;
    case "reply":
      renderReply(data.text, data.usage);
      break;
    case "done":
      endTurn();
      setBusy(false);
      break;
  }
}

async function fallbackPost(message) {
  // SSE 失败（事件源中断）→ 同步 POST 兜底，至少拿到终稿
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: getSessionId() }),
    });
    if (!res.ok) {
      renderReply("（请求失败：HTTP " + res.status + "）");
      return;
    }
    const data = await res.json();
    renderReply(data.reply, data.usage);
    data.thinking.forEach(addThinking);
    data.tool_calls.forEach((tc) => addTool(tc.name, tc.args, tc.result));
  } catch (err) {
    renderReply("（请求失败：" + err + "）");
  } finally {
    endTurn();
    setBusy(false);
  }
}

function send(message) {
  setBusy(true);
  // EventSource 建连前就给首个可见反馈；即使 SSE 不可用转 POST，也不会白屏等待。
  addProgress("请求已提交，正在建立连接…");
  const url =
    "/api/chat/stream?message=" + encodeURIComponent(message) +
    "&session_id=" + encodeURIComponent(getSessionId());
  const es = new EventSource(url);
  es.onmessage = (e) => {
    let data;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    if (data.event === "done") {
      es.close();
    }
    onEvent(data);
  };
  es.onerror = () => {
    es.close();
    fallbackPost(message);
  };
}

async function reset() {
  await fetch("/api/chat/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: getSessionId() }),
  });
  $("#chat").innerHTML = "";
  endTurn();
  setBusy(false);
}

function init() {
  $("#send-btn").addEventListener("click", () => {
    const text = $("#input").value.trim();
    if (!text || $("#send-btn").disabled) return;
    $("#input").value = "";
    createBubble("user", text);
    send(text);
  });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      $("#send-btn").click();
    }
  });
  $("#reset-btn").addEventListener("click", reset);
}

document.addEventListener("DOMContentLoaded", init);
