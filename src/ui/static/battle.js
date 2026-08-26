/* 对战页逻辑（/battle）：人类 vs LLM（假LLM）交互对战。
 *
 * 复用组队模块：我方/对手队伍从已存队伍（/api/team/saved + /load）选取；
 * 对局由 /api/battle/* 驱动。迷雾口径由服务端 view/filter 决定——前端只渲染收到的字段：
 * 己方全量；敌方只有 血量%、能量、系别、特性、已揭示技能（未揭示不出现 → 渲染 ？？？）。
 *
 * 状态全在 S；所有动态字符串过 escapeHtml（app.js 全局）；api() 统一封装 fetch。
 */

(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);

  const S = {
    teamConfig: null,          // /api/team/config（规模范围）
    savedTeams: [],            // 已存队伍列表
    teamA: null,               // 我方队伍（load 结果 {team, team_size,…}）
    teamB: null,               // 对手队伍（opp=saved 时）
    battle: null,              // 当前对局快照
    battles: [],               // 对战记录列表
    lock: false,               // act 防连点
    chosen: null,              // 行动面板当前选中的动作
  };

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let j = null;
    try { j = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    return { ok: res.ok, status: res.status, j };
  }

  function flash(msg, kind) {
    const el = $('#notice');
    el.textContent = msg;
    el.className = 'notice' + (kind ? ' ' + kind : '');
    clearTimeout(flash._t);
    flash._t = setTimeout(() => { el.className = 'notice'; el.textContent = ''; }, 3200);
  }

  function showScreen(name) {
    ['build', 'battle', 'records'].forEach((s) => {
      const el = $('#screen-' + s);
      if (el) el.classList.toggle('hidden', s !== name);
    });
  }

  // ── 技能悬停提示（单一浮层，跟随鼠标、钳制在视口内） ──────────────────────
  const TIP = (() => {
    const el = document.createElement('div');
    el.className = 'tip-float hidden';
    document.body.appendChild(el);
    return {
      show(sk, host) {
        el.innerHTML =
          `<b>${escapeHtml(sk.name)}</b> <span class="tag">${escapeHtml(sk.type)}系</span> ${escapeHtml(sk.kind)}` +
          `<br>威力 ${sk.power} · 能耗 ⚡${sk.energy_cost}` +
          (sk.priority ? ` · 先手 ${sk.priority}` : '') +
          `<br>${escapeHtml(sk.desc)}`;
        el.classList.remove('hidden');
        const r = host.getBoundingClientRect();
        const tw = el.offsetWidth, th = el.offsetHeight;
        let x = r.left + r.width / 2 - tw / 2;
        let y = r.top - th - 8;
        if (y < 8) y = r.bottom + 8;
        x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
        el.style.left = x + 'px';
        el.style.top = y + 'px';
      },
      hide() { el.classList.add('hidden'); },
    };
  })();

  // ── 状态栏（双方阵营可见）：单位加成 * 层数 ──────────────────────────────
  const STAT_CN = { hp: '生命', atk: '物攻', sp_atk: '魔攻', def: '物防', sp_def: '魔防', speed: '速度' };

  function modText(m) {
    // 显示规范：单位加成 * 层数（例：攻击 +100% = 10 层 × 10% → 物攻10% * 10）
    const unit = m.mode === 'flat' ? '+10' : '10%';
    return (m.trait ? '特性·' : '') + `${STAT_CN[m.stat] || m.stat}${unit} * ${m.layers}`;
  }

  function statusBar(u) {
    const parts = [];
    for (const m of (u.stat_mods || [])) parts.push(`<span class="stat-chip">${escapeHtml(modText(m))}</span>`);
    for (const m of (u.energy_cost_mods || [])) parts.push(`<span class="stat-chip">全技能能耗-1 * ${m.layers}</span>`);
    if (!parts.length) return '';
    return `<div class="status-bar" title="状态栏：常规增减益 / 特性层数（单位加成 × 层数）">${parts.join('')}</div>`;
  }

  function chipFor(s, i) {
    return `<span class="skill-chip" data-tip-idx="${i}">${escapeHtml(s.name)} <small>⚡${s.energy_cost}</small></span>`;
  }

  function attachChips(card, skills) {
    card.querySelectorAll('[data-tip-idx]').forEach((node) => {
      const sk = skills[parseInt(node.dataset.tipIdx, 10)];
      if (!sk) return;
      node.addEventListener('mouseenter', () => TIP.show(sk, node));
      node.addEventListener('mouseleave', () => TIP.hide());
    });
  }

  // ── 队伍选择（复用组队模块） ──────────────────────────────────────────────
  function fillTeamSelect(sel, teams) {
    sel.innerHTML = teams.map((t) =>
      `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}（${t.spirits.map(escapeHtml).join('、')}）</option>`
    ).join('');
  }

  async function refreshSavedTeams() {
    const r = await api('/api/team/saved');
    S.savedTeams = r.ok ? r.j.teams : [];
    fillTeamSelect($('#team-a-select'), S.savedTeams);
    fillTeamSelect($('#team-b-select'), S.savedTeams);
    if (S.savedTeams.length) await pickTeam('a', S.savedTeams[0].name);
    else $('#team-a-preview').innerHTML = '<div class="empty">暂无已存队伍，请先到组队页保存。</div>';
  }

  async function pickTeam(side, name) {
    const r = await api('/api/team/load?path=' + encodeURIComponent(name));
    if (!r.ok) { flash('队伍加载失败：' + (r.j && r.j.detail || ''), 'warn'); return; }
    if (side === 'a') {
      S.teamA = r.j;
      renderTeamPreview('#team-a-preview', r.j);
      const sizeSel = $('#team-size-select');
      if (sizeSel.value !== String(r.j.team_size)) {
        sizeSel.value = String(r.j.team_size);
        fillLives();
      }
    } else {
      S.teamB = r.j;
      renderTeamPreview('#team-b-preview', r.j);
    }
  }

  function renderTeamPreview(sel, data) {
    const names = (data.team || []).map((p) => p.spirit).filter(Boolean);
    const el = $(sel);
    if (!names.length) { el.innerHTML = '<div class="empty">（空队伍）</div>'; return; }
    el.innerHTML = `<div class="mini-tags">${names.map((n) => `<span class="tag">${escapeHtml(n)}</span>`).join('')}</div>`;
  }

  function fillTeamSize() {
    const cfg = S.teamConfig || { team_size: { min: 3, max: 6 } };
    const sel = $('#team-size-select');
    sel.innerHTML = '';
    for (let n = cfg.team_size.min; n <= cfg.team_size.max; n++) {
      const opt = document.createElement('option');
      opt.value = String(n); opt.textContent = `${n} 对 ${n}（${n} 只）`;
      sel.appendChild(opt);
    }
    sel.value = String(cfg.team_size.default || cfg.team_size.min);
    fillLives();
  }

  function fillLives() {
    const teamSize = parseInt($('#team-size-select').value, 10) || 3;
    const sel = $('#lives-select');
    sel.innerHTML = '';
    for (let n = 1; n < teamSize; n++) {
      const opt = document.createElement('option');
      opt.value = String(n); opt.textContent = `${n} 条命`;
      sel.appendChild(opt);
    }
    sel.value = '2';
    if (sel.value === '') sel.value = String(teamSize - 1);
  }

  // ── 开局 ─────────────────────────────────────────────────────────────────
  async function start() {
    $('#start-error').innerHTML = '';
    if (!S.teamA) { flash('请先选择我方队伍', 'warn'); return; }
    const opp = document.querySelector('input[name="opp"]:checked').value;
    let teamB = null;
    if (opp === 'saved') {
      if (!S.teamB) { flash('请选择对手队伍', 'warn'); return; }
      teamB = S.teamB.team;
    }
    const seedInput = $('#seed-input').value.trim();
    const body = {
      team_a: S.teamA.team,
      team_b: teamB,
      team_size: parseInt($('#team-size-select').value, 10),
      lives: parseInt($('#lives-select').value, 10),
      max_turns: parseInt($('#max-turns-input').value, 10) || 20,
      seed: seedInput ? parseInt(seedInput, 10) : null,
      opponent: 'fake_llm',
    };
    const r = await api('/api/battle/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = r.j && r.j.detail;
      const errs = (detail && detail.errors) || [typeof detail === 'string' ? detail : '开局失败'];
      $('#start-error').innerHTML = errs.map((e) => `<div class="err-line">${escapeHtml(e)}</div>`).join('');
      return;
    }
    S.battle = r.j;
    S.chosen = null;
    _logTurn = 0;                              // 新对局：日志回合头从第 1 回合重计
    $('#battle-log').innerHTML = '';
    showScreen('battle');
    renderBattle();
    refreshRecords();
  }

  // ── 战斗渲染 ─────────────────────────────────────────────────────────────
  function renderBattle() {
    const b = S.battle;
    $('#battle-meta').innerHTML =
      `第 <b>${b.turn}</b> 回合 · seed <code>${b.seed}</code> · ` +
      `<code class="muted">${escapeHtml(b.battle_id)}</code>`;
    $('#phase-badge').textContent =
      b.phase === 'replacement' ? '⏳ 等待补位' : (b.done ? '🏁 已结束' : '出招中');
    renderSideInfo('a', b.observation.me);
    renderSideInfo('b', b.observation.opponent);
    renderUnits('a', b.observation.me);
    renderUnits('b', b.observation.opponent);
    renderActionPanel();
    renderBanner();
    if (b.events && b.events.length) appendLog(b.events, b.llm_reply, b.events_turn);
  }

  function renderSideInfo(side, sideView) {
    $(`#side-info-${side}`).innerHTML =
      `<span class="badge">命数 ×${sideView.lives}</span>` +
      (side === 'a' ? ` <span class="badge">道具</span> ${Object.entries(sideView.item_uses || {})
        .map(([k, v]) => `${escapeHtml(k)} ×${v}`).join(' ')}` : '');
  }

  function renderUnits(side, sideView) {
    const active = sideView.active;
    const container = $(`#units-${side}`);
    container.innerHTML = sideView.units.map((u, i) =>
      side === 'a' ? renderOwnUnit(u, i === active) : renderFoeUnit(u, i === active)
    ).join('');
    // 渲染后为每个技能 chip 挂悬停提示（按 card 下标取技能数组）
    container.querySelectorAll('.unit-card').forEach((card, cardIdx) => {
      attachChips(card, sideView.units[cardIdx].skills || []);
    });
  }

  function statsLine(u) {
    const s = u.stats || {};
    return `六维 hp${s.hp} atk${s.atk} spa${s.sp_atk} def${s.def} spd${s.sp_def} spe${s.speed}`;
  }

  function renderOwnUnit(u, active) {
    const hpPct = u.max_hp ? Math.floor(u.current_hp * 100 / u.max_hp) : 0;
    return `<div class="unit-card ${u.fainted ? 'fainted' : ''} ${active ? 'active' : ''}" title="${escapeHtml(statsLine(u))}">
      <div class="unit-head">
        <b>${escapeHtml(u.name)}</b>
        <span class="tag">${(u.types || []).map(escapeHtml).join('/')}</span>
        <span class="hpbar"><i style="width:${hpPct}%"></i></span>
        <span class="hp-num">${u.current_hp}/${u.max_hp}</span>
        <span class="energy" title="能量">⚡${u.energy}</span>
      </div>
      <div class="unit-meta">${escapeHtml(statsLine(u))} · 性格 ${escapeHtml(u.nature)}</div>
      ${statusBar(u)}
      <div class="unit-skills">${(u.skills || []).map(chipFor).join('')}</div>
    </div>`;
  }

  function renderFoeUnit(u, active) {
    const skills = (u.skills || []).length
      ? (u.skills || []).map(chipFor).join('')
      : '<span class="skill-chip unknown">？？？（未揭示）</span>';
    return `<div class="unit-card foe ${u.fainted ? 'fainted' : ''} ${active ? 'active' : ''}">
      <div class="unit-head">
        <b>${escapeHtml(u.name)}</b>
        <span class="tag">${(u.types || []).map(escapeHtml).join('/')}</span>
        <span class="hpbar"><i style="width:${u.hp_pct}%"></i></span>
        <span class="hp-pct">${u.hp_pct}%</span>
        <span class="energy" title="能量">⚡${u.energy}</span>
      </div>
      <div class="unit-meta">特性：${escapeHtml((u.trait && u.trait.name) || '?')}</div>
      <div class="unit-meta dim">${escapeHtml((u.trait && u.trait.desc) || '')}</div>
      ${statusBar(u)}
      <div class="unit-skills">${skills}</div>
    </div>`;
  }

  // ── 行动面板 ─────────────────────────────────────────────────────────────
  function chooseAction(action, btn) {
    S.chosen = action;
    const el = $('#action-panel');
    el.querySelectorAll('.act-btn').forEach((x) => x.classList.remove('chosen'));
    if (btn) btn.classList.add('chosen');
  }

  function renderActionPanel() {
    const b = S.battle;
    const el = $('#action-panel');
    if (b.phase === 'replacement') { renderReplacePanel(); return; }
    if (b.done) { el.innerHTML = ''; return; }
    const me = b.observation.me;
    const act = me.units[me.active];
    const legalSkillIdx = new Set(b.legal.filter((a) => a.type === 'skill').map((a) => a.value));
    const switchActs = b.legal.filter((a) => a.type === 'switch');
    const canRecharge = b.legal.some((a) => a.type === 'recharge');

    let html = `<div class="action-bar"><h4>我方在场：${escapeHtml(act.name)} — 选择行动</h4><div class="skill-buttons">`;
    // 全部技能槽都显示；本回合付不起能量的**置灰禁用**（hover 仍可看描述）
    for (let i = 0; i < act.skills.length; i++) {
      const sk = act.skills[i];
      const enabled = legalSkillIdx.has(i);
      html += `<button class="act-btn skill" data-idx="${i}" ${enabled ? '' : 'disabled'} ` +
        `title="${enabled ? '' : '能量不足，本回合无法释放'}">${escapeHtml(sk.name)} <small>⚡${sk.energy_cost}</small></button>`;
    }
    // 换人：单个按钮 → 点击弹出可选名单
    html += `<span class="switch-wrap"><button id="switch-btn" class="act-btn switch" ${switchActs.length ? '' : 'disabled'}>⇄ 换人</button>` +
      `<span id="switch-menu" class="switch-menu hidden">` +
      switchActs.map((a) => {
        const bench = me.units[a.value];
        return `<button class="act-btn" data-act='${JSON.stringify(a)}'>⇄ ${escapeHtml(bench.name)}</button>`;
      }).join('') + `</span></span>`;
    if (canRecharge) html += `<button class="act-btn recharge" data-act='${JSON.stringify({ type: 'recharge' })}'>↻ 聚能（回复能量）</button>`;
    html += `</div><div class="item-row">`;
    for (const it of b.legal_items) {
      html += `<label class="item-cb-label"><input type="checkbox" class="item-cb" value="${escapeHtml(it)}"> 道具：${escapeHtml(it)}</label>`;
    }
    html += `<button id="act-submit" class="primary">出招</button></div></div>`;
    el.innerHTML = html;
    S.chosen = null;

    // 技能按钮：可用的可选（置灰的点击无效，但悬停仍显示描述）
    el.querySelectorAll('.act-btn.skill').forEach((btn) => {
      const sk = act.skills[parseInt(btn.dataset.idx, 10)];
      if (!sk) return;
      btn.addEventListener('mouseenter', () => TIP.show(sk, btn));
      btn.addEventListener('mouseleave', () => TIP.hide());
      if (!btn.disabled) {
        btn.addEventListener('click', () => chooseAction({ type: 'skill', value: parseInt(btn.dataset.idx, 10) }, btn));
      }
    });
    // 换人：单按钮弹名单
    const switchBtn = $('#switch-btn');
    const switchMenu = $('#switch-menu');
    if (switchBtn && !switchBtn.disabled) {
      switchBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        switchMenu.classList.toggle('hidden');
      });
      switchMenu.querySelectorAll('.act-btn').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          switchMenu.classList.add('hidden');
          chooseAction(JSON.parse(btn.dataset.act), btn);
        });
      });
    } else if (switchBtn) {
      switchBtn.title = '没有可换上的存活后备';
    }
    // 聚能
    el.querySelectorAll('.act-btn.recharge').forEach((btn) => {
      btn.addEventListener('click', () => chooseAction(JSON.parse(btn.dataset.act), btn));
    });

    $('#act-submit').addEventListener('click', async () => {
      if (!S.chosen) { flash('请先选择一个行动', 'warn'); return; }
      const itemCb = el.querySelector('.item-cb:checked');
      await doAct(S.chosen, itemCb ? itemCb.value : '');
    });
  }

  function renderReplacePanel() {
    const b = S.battle;
    const me = b.observation.me;
    const bench = me.units
      .map((u, i) => ({ u, i }))
      .filter((x) => x.i !== me.active && !x.u.fainted);
    const el = $('#action-panel');
    el.innerHTML = `<div class="action-bar"><h4>己方在场精灵倒下 — 选择补位</h4><div class="skill-buttons">` +
      bench.map(({ u, i }) => `<button class="act-btn" data-idx="${i}">⇄ ${escapeHtml(u.name)}</button>`).join('') +
      `</div></div>`;
    el.querySelectorAll('.act-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const r = await api(`/api/battle/${b.battle_id}/replace`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ bench_idx: parseInt(btn.dataset.idx, 10) }),
        });
        if (!r.ok || !r.j.ok) { flash((r.j && r.j.error) || '补位失败', 'warn'); return; }
        S.battle = r.j;
        renderBattle();
      });
    });
  }

  async function doAct(action, item) {
    if (S.lock) return;
    S.lock = true;
    const b = S.battle;
    const r = await api(`/api/battle/${b.battle_id}/act`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, item }),
    });
    S.lock = false;
    if (!r.ok || !r.j.ok) { flash((r.j && r.j.error) || '出招失败', 'warn'); return; }
    S.battle = r.j;
    renderBattle();
    if (r.j.phase === 'replacement') flash('己方精灵阵亡，请选择补位', 'ok');
  }

  function renderBanner() {
    const b = S.battle;
    const el = $('#battle-banner');
    if (!b.done) { el.classList.add('hidden'); return; }
    el.classList.remove('hidden');
    const who = b.winner === 'a' ? '我方' : (b.winner === 'b' ? '敌方（LLM）' : '平局');
    const lastEvt = (b.events || []).filter((e) => e.type === 'battle_end').pop();
    const msg = lastEvt && lastEvt.message ? ` — ${lastEvt.message}` : '';
    el.innerHTML = `<b>🏁 对战结束：${who} 获胜！</b><span class="muted">第 ${b.turn} 回合${escapeHtml(msg)}</span>`;
  }

  // ── 事件日志（展示口径已由服务端过滤） ────────────────────────────────────
  function formatEvent(e) {
    const side = e.side === 'a' ? '我方' : '敌方';
    const t = e.type;
    if (t === 'damage') {
      const hp = e.target_hp_pct != null ? `剩${e.target_hp_pct}%` : `剩${e.target_hp_left}`;
      let line = `${side} ${escapeHtml(e.attacker)} 用「${escapeHtml(e.skill)}」→ ${escapeHtml(e.target)} 伤害${e.damage}（${hp}）`;
      if (e.counter) line += ` 应对${escapeHtml(e.counter)}×${e.mult}`;
      if (e.reduced) line += ` 减伤${Math.round(e.reduced * 100)}%`;
      if (e.eff && e.eff !== 1) line += ` 系别×${e.eff}`;
      if (e.stab && e.stab !== 1) line += ` 本系×${e.stab}`;
      return line;
    }
    if (t === 'heal') {
      const num = e.applied != null ? ` +${e.applied}` : '';
      return `${side} ${escapeHtml(e.unit)} 回复生命${num}`;
    }
    if (t === 'stat_change') {
      if (e.stat == null) return `${side} ${escapeHtml(e.unit)} 用「${escapeHtml(e.skill)}」改变能力（详情迷雾）`;
      return `${side} ${escapeHtml(e.unit)} 用「${escapeHtml(e.skill)}」${escapeHtml(e.stat)} ${e.mode} ${e.layers}层(共${e.total_layers})`;
    }
    if (t === 'recharge') return `${side} ${escapeHtml(e.unit)} 聚能 ⚡${e.gained} → ${e.energy}`;
    if (t === 'energy_gain') return `${side} ${escapeHtml(e.unit)} 获得能量 +${e.gained}（⚡${e.energy}）`;
    if (t === 'steal') return `${side} ${escapeHtml(e.unit)} 偷取能量 +${e.gained}`;
    if (t === 'reduce_arm') return `${side} ${escapeHtml(e.unit)} 释放「${escapeHtml(e.skill)}」防御${Math.round(e.pct * 100)}%${e.armed ? '（已武装）' : '（未武装）'}`;
    if (t === 'switch') return `${side} 换人：${escapeHtml(e.out)} → ${escapeHtml(e.in)}`;
    if (t === 'replace') return `${side} 补位：${escapeHtml(e.out)} → ${escapeHtml(e.in)}`;
    if (t === 'faint') return `💀 ${side} ${escapeHtml(e.unit)} 倒下了！`;
    if (t === 'life_loss') return `${side} ${escapeHtml(e.unit)} 倒下，命数剩 ${e.lives_left}`;
    if (t === 'item_use') return `${side} 使用道具「${escapeHtml(e.item)}」`;
    if (t === 'skipped') return `${side} ${escapeHtml(e.unit)} 跳过（${escapeHtml(e.reason)}）`;
    if (t === 'battle_end') return `🏁 战斗结束：${e.winner === 'a' ? '我方' : (e.winner === 'b' ? '敌方' : '平局')} 胜 — ${escapeHtml(e.message || '')}`;
    if (t === 'error') return `⚠️ ${escapeHtml(e.message || '')}`;
    return escapeHtml(t);
  }

  // 从本回合事件反推一方主动作摘要（仿 CLI 的 _turn_header；事件已按人类视角过滤）
  function summarizeSide(events, side) {
    for (const e of events) {
      if (e.side !== side) continue;
      const t = e.type;
      if (t === 'damage' || t === 'stat_change' || t === 'reduce_arm') return escapeHtml(e.skill || '?');
      if (t === 'switch') return `换→${escapeHtml(e.in)}`;
      if (t === 'recharge') return '聚能';
      if (t === 'skipped') return '跳过';
    }
    return '—';
  }

  let _logTurn = 0;   // 日志里最后出现的回合号（补位续步同回合 → 不重复插头）

  function appendLog(events, llmReply, eventsTurn) {
    const log = $('#battle-log');
    if (eventsTurn && eventsTurn !== _logTurn) {
      const sum = `我方：${summarizeSide(events, 'a')} · 敌方：${summarizeSide(events, 'b')}`;
      log.insertAdjacentHTML('beforeend',
        `<div class="log-turn">── 第 ${eventsTurn} 回合 ──<span class="log-sum">${sum}</span></div>`);
      _logTurn = eventsTurn;
    }
    const frag = document.createDocumentFragment();
    for (const e of events) {
      const div = document.createElement('div');
      div.className = 'log-line';
      div.innerHTML = formatEvent(e);
      frag.appendChild(div);
    }
    if (llmReply) {
      const div = document.createElement('div');
      div.className = 'log-line llm';
      div.innerHTML = `<span class="llm-tag">🤖</span> ${escapeHtml(llmReply)}`;
      frag.appendChild(div);
    }
    log.appendChild(frag);
    log.scrollTop = log.scrollHeight;
  }

  // ── 对战记录：轨迹 + 重放 ────────────────────────────────────────────────
  async function refreshRecords() {
    const r = await api('/api/battle/saved');
    S.battles = r.ok ? r.j.battles : [];
    const ul = $('#record-list');
    if (!S.battles.length) {
      ul.innerHTML = '<li class="empty">暂无对战记录（打一局后自动保存到 battles/）。</li>';
      return;
    }
    ul.innerHTML = S.battles.map((b) =>
      `<li>
        <button class="ghost" data-load="${escapeHtml(b.name)}">${escapeHtml(b.name)}</button>
        <span class="muted">${b.winner === 'a' ? '我方胜' : (b.winner === 'b' ? '敌胜' : '?')} · ${b.turn_count}回合 · seed ${b.seed}</span>
      </li>`
    ).join('');
    ul.querySelectorAll('button[data-load]').forEach((btn) =>
      btn.addEventListener('click', () => loadRecord(btn.dataset.load)));
  }

  async function loadRecord(name) {
    const r = await api('/api/battle/load?path=' + encodeURIComponent(name));
    if (!r.ok) { flash('加载对局记录失败', 'warn'); return; }
    const b = r.j;
    let html = `<div class="record-detail"><h3>${escapeHtml(b.battle_id)}</h3>`;
    html += `<div class="muted">seed=${b.seed} · 对手=${escapeHtml(b.opponent || '?')} · winner=${escapeHtml(b.winner || '?')} · 我方 ${(b.team_a || []).map((p) => p.spirit).join('、')} vs 敌方 ${(b.team_b || []).map((p) => p.spirit).join('、')}</div>`;
    html += `<div class="record-turns">`;
    for (const t of b.turns || []) {
      html += `<div class="record-turn"><b>T${t.turn}</b> ` +
        `<span class="muted">a=${escapeHtml(JSON.stringify(t.decision_a && t.decision_a.action))} b=${escapeHtml(JSON.stringify(t.decision_b && t.decision_b.action))}</span> ` +
        `<code>${escapeHtml(t.state_hash.slice(0, 12))}…</code></div>`;
    }
    html += `</div>`;
    html += `<button id="replay-btn" class="primary">重放验证（逐回合 hash 比对）</button>`;
    html += `<div id="replay-result" class="validate-box"></div></div>`;
    $('#record-detail').innerHTML = html;
    $('#replay-btn').addEventListener('click', async () => {
      const rr = await api('/api/battle/replay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: b.path }),
      });
      const el = $('#replay-result');
      if (!rr.ok) { el.innerHTML = '重放失败：' + escapeHtml(rr.j && rr.j.detail || ''); return; }
      const bad = rr.j.turns.filter((t) => !t.match);
      el.innerHTML = rr.j.all_match
        ? `<span class="ok">✅ 重放 ${rr.j.turns.length} 回合 state_hash 全部一致（马尔可夫复现）</span>`
        : `❌ ${bad.length} 回合不一致（详见控制台）`;
    });
  }

  // ── 初始化 ───────────────────────────────────────────────────────────────
  async function init() {
    const cfgR = await api('/api/team/config');
    if (cfgR.ok) S.teamConfig = cfgR.j;
    fillTeamSize();
    const oppRadios = document.querySelectorAll('input[name="opp"]');
    oppRadios.forEach((rad) => rad.addEventListener('change', () => {
      const saved = rad.value === 'saved';
      $('#team-b-select').disabled = !saved;
      $('#preset-note').style.display = saved ? 'none' : '';
    }));
    $('#team-a-select').addEventListener('change', () => pickTeam('a', $('#team-a-select').value));
    $('#team-b-select').addEventListener('change', () => pickTeam('b', $('#team-b-select').value));
    $('#team-size-select').addEventListener('change', fillLives);
    $('#start-btn').addEventListener('click', start);
    $('#quit-btn').addEventListener('click', () => {
      showScreen('build');
      refreshRecords();
    });
    // 点击换人名单之外 → 收起名单（switch-btn 自身 stopPropagation，不受影响）
    document.addEventListener('click', () => {
      const menu = $('#switch-menu');
      if (menu && !menu.classList.contains('hidden')) menu.classList.add('hidden');
    });
    await refreshSavedTeams();
    refreshRecords();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
