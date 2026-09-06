"use strict";
window.TeamAdviceCard = (() => {
  const el = RoundCard.el;
  async function render(container, ref, api, isCurrent) {
    const root = el("section", "team-advice-card", "正在加载队伍…"); container.append(root);
    try {
      const a = await api("/api/chat/artifacts/" + ref.artifact_id);
      if (!isCurrent()) return;
      root.replaceChildren();
      const advice = a.advice, check = a.current_validation, doc = a.team_document;
      root.append(el("h2", "", doc.team_size + " 人队伍建议"));
      const validity = el("p", "team-validity", check.ok ? "当前配置校验通过" : "当前配置需修复：" + check.errors.join("；"));
      root.append(validity);
      if (a.version_changed) root.append(el("p", "", "生成于旧数据版本；当前配置已重新校验，历史建议与证据保留原样。"));
      root.append(el("p", "", "建议命数：" + advice.rules_used.lives + "（对战开局单独设置） · " + (doc.items.length ? "道具：" + doc.items.join("、") : "不携带道具")));
      const grid = el("div", "advice-members");
      a.display_snapshot.forEach((u, i) => {
        const card = el("article", "advice-member"), unit = advice.team[i];
        card.append(el("h3", "", (i + 1) + ". " + u.spirit), el("p", "", u.types.join(" / ") + " · " + u.trait.name));
        card.append(el("p", "", "分工：" + (unit.role || "未说明")));
        const skills = el("div", "advice-skills");
        u.skills.forEach(s => { const tag = el("span", "", s.name); tag.title = s.type + " · " + s.desc; skills.append(tag); });
        card.append(skills, el("p", "", "血脉：" + (u.bloodline || "无") + " · 性格：" + u.nature));
        card.append(el("p", "", "IV：" + (TeamComponents.statText(u.iv) || "无投入")));
        const stats = check.roster?.[i]?.stats;
        if (stats) card.append(el("p", "", "当前属性：" + TeamComponents.statText(stats)));
        const reasons = el("details", ""), heading = el("summary", "", "选择理由与证据");
        reasons.append(heading, el("p", "", unit.rationale || "未提供"), el("p", "", "引用：" + (unit.evidence_ids.join("、") || "无")));
        card.append(reasons); grid.append(card);
      });
      root.append(grid);
      for (const [label, value] of [["队伍协同", advice.synergy], ["优势", advice.strengths.join("；")], ["需注意的对局", advice.weak_matchups.join("；")]]) {
        root.append(el("h3", "", label), el("p", "", value || "未提供"));
      }
      const evidence = el("details", ""); evidence.append(el("summary", "", "证据、假设与不确定性"));
      for (const [key, label] of [["catalog", "图鉴"], ["human", "人机样本"], ["selfplay", "自博弈样本"], ["simulation", "模拟"]]) {
        evidence.append(el("h4", "", label));
        (a.evidence_status[key] || []).forEach(f => evidence.append(el('p', '', '本次工具记录：' + f.summary)));
        evidence.append(el('p', '', '建议中的说明（引用需单独核验）：'), el("pre", "", JSON.stringify(advice.evidence[key], null, 2)));
      }
      evidence.append(el("p", "", "未核验引用：" + (a.evidence_status.unverified_refs.join("、") || "无")),
        el("p", "", "假设：" + advice.assumptions.join("；")), el("p", "", advice.uncertainty || "缺少同配置实战证据时，按理论构筑理解。"));
      root.append(evidence);
      advice.alternatives.forEach((team, i) => {
        const row = el("div", "alternative"), b = el("button", "", "校验此备选"); b.type = "button";
        row.append(el("p", "", "备选 " + (i + 1) + "（未验证）：" + team.map(u => u.spirit).join("、")), b);
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            const derived = await api("/api/chat/artifacts/" + a.artifact_id + "/alternatives/" + i + "/validate", {method: "POST"});
            if (isCurrent()) { await render(row, derived, api, isCurrent); b.remove(); }
          } catch (e) { if (isCurrent()) row.append(el("p", "action-error", e.message)); }
          finally { b.disabled = false; }
        }); root.append(row);
      });
      const bar = el("div", "advice-actions"), save = el("button", "", "保存队伍"), edit = el("a", "", "在组队页编辑"), download = el("a", "", "下载队伍 JSON");
      save.type = "button"; save.disabled = !check.ok;
      edit.href = "/team?artifact_id=" + a.artifact_id;
      if (check.ok) { download.href = "/api/chat/artifacts/" + a.artifact_id + "/download"; download.setAttribute("download", ""); }
      else { download.setAttribute("aria-disabled", "true"); }
      const raw = el("details", ""); raw.append(el("summary", "", "查看结构化建议"), el("pre", "", JSON.stringify(advice, null, 2)));
      const status = el("p", "save-status"); status.setAttribute("role", "status");
      const saved = a.saved_copies.filter(s => s.status === "saved");
      if (saved.length) {
        status.append(el('span', '', '保存记录（文件可能已被移动或修改）：'));
        saved.forEach(s => {
          const link = el('a', '', s.path); link.href = '/team?path=' + encodeURIComponent(s.path) + '&session_id=' + a.session_id;
          status.append(link);
        });
      }
      const intentKey = 'roco_artifact_save_' + a.artifact_id;
      let intent = null;
      try { intent = JSON.parse(sessionStorage.getItem(intentKey) || 'null'); } catch { sessionStorage.removeItem(intentKey); }
      if (intent && saved.some(s => s.request_id === intent.request_id)) { intent = null; sessionStorage.removeItem(intentKey); }
      if (!intent) intent = a.saved_copies.filter(s => s.status === 'pending').at(-1)?.request_body || null;
      if (intent) { save.textContent = '重试保存'; status.textContent = '上次保存尚未确认，可以继续同一次保存。'; }
      save.addEventListener("click", async () => {
        if (!intent) {
          const path = prompt("保存路径：留空自动保存到队伍目录", ""); if (path === null) return;
          intent = {artifact_version: a.artifact_version, request_id: crypto.randomUUID(), path};
          sessionStorage.setItem(intentKey, JSON.stringify(intent));
        }
        save.disabled = true; status.textContent = "正在保存…";
        try {
          let saved;
          try { saved = await api("/api/chat/artifacts/" + a.artifact_id + "/save", {method: "POST", body: JSON.stringify(intent)}); }
          catch (e) {
            if (e.code !== "OVERWRITE_REQUIRED" || !isCurrent() || !confirm("覆盖现有文件「" + e.data.path + "」？")) throw e;
            intent.overwrite = true;
            sessionStorage.setItem(intentKey, JSON.stringify(intent));
            saved = await api("/api/chat/artifacts/" + a.artifact_id + "/save", {method: "POST", body: JSON.stringify(intent)});
          }
          if (!isCurrent()) return;
          status.replaceChildren(el("span", "", saved.file_matches ? "已保存：" + saved.path : "原保存文件已移动或修改，请重新保存。"));
          if (saved.file_matches) { const open = el("a", "", "在组队页打开"); open.href = "/team?path=" + encodeURIComponent(saved.path) + "&session_id=" + a.session_id; status.append(open); }
          intent = null; sessionStorage.removeItem(intentKey); save.textContent = '保存队伍';
        } catch (e) { if (isCurrent()) { status.textContent = e.message; if (e.status && e.status !== 503) { intent = null; sessionStorage.removeItem(intentKey); } } }
        finally { if (isCurrent()) save.disabled = !check.ok; }
      });
      bar.append(save, edit, download); root.append(bar, status, raw);
    } catch (e) { if (isCurrent()) root.textContent = "队伍加载失败：" + e.message; }
  }
  return {render};
})();
