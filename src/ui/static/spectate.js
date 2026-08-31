/* 观战页（/spectate）：人类以**全局视角**观看两个 LLM 之间的对战过程（E7）。
 *
 * 帧协议（服务端 run_spectate 产出）：meta → state → turn* → done / error。
 * - meta：battle_id / seed / rules / 双方实际 kind / 双方精灵名
 * - state：turn 0 全局快照（a、b 双方都全量——绝对血量 / 全部技能 / 性格 / 血脉 / IV）
 * - turn：全局快照 + 双方 decisions + **全量事件**
 * - done：winner / 回合数；error：配置错误
 *
 * 关键：观战者上帝视角；两个 LLM 玩家仍按迷雾对战（引擎 drive_turn 只喂 view() + 过滤事件）——
 * 本页渲染的是全局快照，与玩家视角无关。
 *
 * 复用 app.js 的 `$` / `escapeHtml`；样式复用 battle 页（unit-card / hpbar / skill-chip /
 * status-bar / battle-log / log-turn 等）。
 */
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);

  // ── 技能悬停浮层（同 battle.js 的 TIP） ──────────────────────────────
  const TIP = (() => {
    const el = document.createElement('div');
    el.className = 'tip-float hidden';
    document.body.appendChild(el);
    return {
      show(sk, host) {
        el.innerHTML =
          `<b>${escapeHtml(sk.name)}</b> <span class="tag">${escapeHtml(sk.type)}系</span> ${escapeHtml(sk.kind)}` +
          `<br>威力 ${sk.power} · 能耗 ⚡${costText(sk)}` +
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

  // ── 状态栏（单位加成 * 层数，同 battle.js） ──────────────────────────
  const STAT_CN = { hp: '生命', atk: '物攻', sp_atk: '魔攻', def: '物防', sp_def: '魔防', speed: '速度' };

  function modText(m) {
    const prefix = m.trait ? '特性·' : '';
    if (m.stat === 'energy_cost') return prefix + `全技能能耗${m.layers > 0 ? '+' : ''}${m.layers}`;
    const name = STAT_CN[m.stat] || m.stat;
    if (m.mode === 'flat') return prefix + `${name}+10 * ${m.layers}`;
    if (m.mode === 'pct') return prefix + `${name}10% * ${m.layers}`;
    return prefix + `${name} * ${m.layers}`;   // dot/special：层数直显
  }

  function statusBar(u) {
    const parts = [];
    for (const m of (u.stat_mods || [])) parts.push(`<span class="stat-chip">${escapeHtml(modText(m))}</span>`);
    const gains = (u.trait && u.trait.gains) || [];
    for (const m of gains) parts.push(`<span class="stat-chip">${escapeHtml(modText(m))}</span>`);
    if (!parts.length) return '';
    return `<div class="status-bar">${parts.join('')}</div>`;
  }

  function costText(s) {
    if (s.current_cost == null) return String(s.energy_cost);
    return s.current_cost === s.energy_cost
      ? String(s.current_cost)
      : `${s.current_cost}（原${s.energy_cost}）`;
  }

  function chipFor(s, i) {
    return `<span class="skill-chip" data-tip-idx="${i}">${escapeHtml(s.name)} <small>⚡${costText(s)}</small></span>`;
  }

  function attachChips(card, skills) {
    card.querySelectorAll('[data-tip-idx]').forEach((node) => {
      const sk = skills[parseInt(node.dataset.tipIdx, 10)];
      if (!sk) return;
      node.addEventListener('mouseenter', () => TIP.show(sk, node));
      node.addEventListener('mouseleave', () => TIP.hide());
    });
  }

  // ── 全量单位卡（观战者上帝视角：绝对血量 / 六维 / 性格 / 血脉 / IV） ──
  function statsLine(u) {
    const s = u.stats || {};
    const parts = [`HP${s.hp ?? '?'}`, `攻${s.atk ?? '?'}`, `魔${s.sp_atk ?? '?'}`,
      `防${s.def ?? '?'}`, `魔防${s.sp_def ?? '?'}`, `速${s.speed ?? '?'}`];
    let line = parts.join(' ');
    if (u.nature) line += ` 性格 ${escapeHtml(u.nature)}`;
    if (u.bloodline) line += ` 血脉 ${escapeHtml(u.bloodline)}`;
    if (u.iv && Object.keys(u.iv).length) line += ` IV ${escapeHtml(JSON.stringify(u.iv))}`;
    return line;
  }

  function unitCard(u, active) {
    const hpPct = u.max_hp ? Math.max(0, Math.min(100, Math.floor(u.current_hp * 100 / u.max_hp))) : 0;
    const trait = u.trait && u.trait.name ? `特性：${escapeHtml(u.trait.name)}` : '';
    return `<div class="unit-card${active ? ' active' : ''}${u.fainted ? ' fainted' : ''}" title="${escapeHtml(statsLine(u))}">
      <div class="unit-head">
        <b>${escapeHtml(u.name)}</b> <span class="tag">${escapeHtml(u.types.join('/'))}</span>
        <span class="hpbar"><i style="width:${hpPct}%"></i></span>
        <span class="hp-num">${u.current_hp}/${u.max_hp}</span>
        <span class="energy" title="能量">⚡${u.energy}</span>
      </div>
      <div class="unit-meta">${statsLine(u)}${trait ? ' · ' + trait : ''}</div>
      ${statusBar(u)}
      <div class="unit-skills">${(u.skills || []).map(chipFor).join('')}</div>
    </div>`;
  }

  function renderSide(sideKey, view) {
    const unitsEl = $('#units-' + sideKey);
    unitsEl.innerHTML = view.units.map((u, i) => unitCard(u, i === view.active)).join('');
    unitsEl.querySelectorAll('.unit-card').forEach((card, i) => attachChips(card, view.units[i].skills || []));
    $('#side-info-' + sideKey).innerHTML = `剩余命数 <b>${view.lives}</b> · 在场 #${view.active + 1}`;
  }

  function applyState(st) {
    renderSide('a', st.a);
    renderSide('b', st.b);
    const w = st.weather;
    $('#spectate-weather').innerHTML = w
      ? `🌦 ${escapeHtml(w.kind)} · 剩 ${w.turns_left} 回合`
      : '';
  }

  // ── 全量事件行（观战者视角：绝对数值） ──────────────────────────────
  function formatEvent(e) {
    const side = e.side === 'a' ? 'a 方' : 'b 方';
    const t = e.type;
    if (t === 'damage') {
      let line = `${side} ${escapeHtml(e.attacker)} 用「${escapeHtml(e.skill)}」→ ${escapeHtml(e.target)} 伤害${e.damage}（剩${e.target_hp_left}）`;
      if (e.counter) line += ` 应对${escapeHtml(e.counter)}×${e.mult}`;
      if (e.reduced) line += ` 减伤${Math.round(e.reduced * 100)}%`;
      if (e.eff && e.eff !== 1) line += ` 系别×${e.eff}`;
      if (e.stab && e.stab !== 1) line += ` 本系×${e.stab}`;
      return line;
    }
    if (t === 'heal') return `${side} ${escapeHtml(e.unit)} 回复生命 +${e.applied}`;
    if (t === 'stat_change') return `${side} ${escapeHtml(e.unit)} 用「${escapeHtml(e.skill)}」${escapeHtml(e.stat)} ${e.mode} ${e.layers}层(共${e.total_layers})`;
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
    if (t === 'battle_end') return `🏁 战斗结束：${e.winner === 'a' ? 'a 方' : (e.winner === 'b' ? 'b 方' : '平局')} 胜 — ${escapeHtml(e.message || '')}`;
    if (t === 'error') return `⚠️ ${escapeHtml(e.message || '')}`;
    return escapeHtml(t);
  }

  // 把一方本回合提交的 action 解析成中文（用当前全局快照查技能名）
  function resolveAction(action, sideView) {
    if (!action) return '—';
    const t = action.type;
    const v = action.value;
    if (t === 'recharge') return '聚能';
    const unit = sideView && sideView.units[sideView.active];
    if (t === 'skill') {
      const sk = unit && unit.skills[v];
      return sk ? `技能「${escapeHtml(sk.name)}」` : `技能 槽位${v}`;
    }
    if (t === 'switch') {
      const target = sideView && sideView.units[v];
      return target ? `换人→${escapeHtml(target.name)}` : `换人 槽位${v}`;
    }
    return `${t}${v != null ? ' ' + v : ''}`;
  }

  let _logTurn = 0;
  function appendLog(frame) {
    const log = $('#spectate-log');
    if (frame.turn && frame.turn !== _logTurn) {
      const da = resolveAction(frame.decisions && frame.decisions.a, frame.state.a);
      const db = resolveAction(frame.decisions && frame.decisions.b, frame.state.b);
      log.insertAdjacentHTML('beforeend',
        `<div class="log-turn">── 第 ${frame.turn} 回合 ──<span class="log-sum">a：${da} · b：${db}</span></div>`);
      _logTurn = frame.turn;
    }
    const frag = document.createDocumentFragment();
    for (const e of frame.events) {
      const div = document.createElement('div');
      div.className = 'log-line';
      div.innerHTML = formatEvent(e);
      frag.appendChild(div);
    }
    log.appendChild(frag);
    log.scrollTop = log.scrollHeight;
  }

  // ── 帧分发 ──
  function onMeta(f) {
    $('#meta-a').textContent = `kind: ${f.players.a}`;
    $('#meta-b').textContent = `kind: ${f.players.b}`;
    $('#spectate-meta').textContent = `seed ${f.seed} · ${f.team_a.length}v${f.team_b.length} · 观战开始`;
  }

  function onFrame(f) {
    if (f.event === 'meta') return onMeta(f);
    if (f.event === 'state') {
      applyState(f.state);
      $('#spectate-meta').textContent = '第 1 回合 · 开局';
      return;
    }
    if (f.event === 'turn') {
      applyState(f.state);
      appendLog(f);
      $('#spectate-meta').textContent = `第 ${f.turn} 回合`;
      return;
    }
    if (f.event === 'done') {
      const banner = $('#spectate-banner');
      banner.classList.remove('hidden');
      banner.innerHTML = `🏁 对局结束：<b>${f.winner === 'a' ? 'a 方' : (f.winner === 'b' ? 'b 方' : '平局')} 胜</b>（共 ${f.turn} 回合）`;
      return;
    }
    if (f.event === 'error') {
      flash('观战失败：' + (f.message || ''), 'warn');
    }
  }

  // ── 观战流连接 ──
  let es = null;
  function flash(msg, kind) {
    const el = $('#notice');
    el.textContent = msg;
    el.className = 'notice ' + (kind || '');
    clearTimeout(flash._t);
    flash._t = setTimeout(() => { el.textContent = ''; el.className = 'notice'; }, 3200);
  }

  function connect() {
    if (es) { es.close(); es = null; }
    const q = new URLSearchParams({
      seed: $('#seed').value, a: $('#kind-a').value, b: $('#kind-b').value,
      team_size: $('#team-size').value, lives: $('#lives').value,
    });
    $('#spectate-log').innerHTML = '';
    $('#spectate-banner').classList.add('hidden');
    $('#spectate-banner').innerHTML = '';
    _logTurn = 0;
    $('#spectate-meta').textContent = '连接观战流…';
    es = new EventSource('/api/battle/stream?' + q.toString());
    es.onmessage = (ev) => {
      let f;
      try { f = JSON.parse(ev.data); } catch (err) { return; }
      onFrame(f);
      if (f.event === 'done' || f.event === 'error') { es.close(); es = null; }
    };
    es.onerror = () => { if (es) { es.close(); es = null; } flash('观战流连接中断', 'warn'); };
  }

  function init() {
    // 支持从 URL query 预填并自动开始（如 /spectate?seed=7&a=llm&b=fake_llm&team_size=3&lives=2）
    const q = new URLSearchParams(location.search);
    if (q.has('a')) $('#kind-a').value = q.get('a');
    if (q.has('b')) $('#kind-b').value = q.get('b');
    if (q.has('team_size')) $('#team-size').value = q.get('team_size');
    if (q.has('lives')) $('#lives').value = q.get('lives');
    if (q.has('seed')) $('#seed').value = q.get('seed');
    $('#start-btn').addEventListener('click', connect);
    if (q.has('seed') || q.has('a') || q.has('b')) connect();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
