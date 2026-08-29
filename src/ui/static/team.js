"use strict";

/* ===== 组队页逻辑（/team）=====
 *
 * 功能：精灵搜索选将 → 槽位配置（技能池 / 血脉 / 个体值 / 性格）→ 校验 → 保存/加载。
 *
 * 数据源（服务端权威，见 ui/routes_team.py，前端不自行计算规则）：
 *   GET  /api/team/config    队伍规模范围 / 技能槽位数 / 个体值上限 / 性格 / 系别
 *   GET  /api/team/spirits   精灵目录（FULL 全量，裁剪字段，客户端过滤）
 *   GET  /api/team/skills    某精灵的可学技能池（含 battle_ready 未实装标记）
 *   POST /api/team/validate  整队校验（保存的闸门，一次报全部错误）
 *   POST /api/team/save      保存到路径（绝对路径或 teams/ 下相对路径）
 *   GET  /api/team/saved     列出已存队伍
 *   GET  /api/team/load      加载已存队伍
 *   POST /api/team/delete    删除已存队伍
 *
 * 与 environment/teambuilder.py 同源的规则：
 *   - 队伍规模 3–6；每只 1–4 技能；个体值 0–10 且最多 3 维；性格 31 种；
 *   - 未实装技能（battle_ready=false）置灰不可选 → 保存的队伍永远可开战；
 *   - 首领形态不可入队；同家族只能入队一只（前端拦截 + 服务端校验双保险）。
 * 属性预览：前端逐字节复刻 environment/statline.calc_combat_stats；
 *   保存时服务端 build_roster 返回权威属性，以服务端为准。
 */

(function () {
  'use strict';

  /* 六维表：UI 展示名 ↔ 字段名（渲染 IV 行、预览属性都按此顺序）。 */
  const STATS = [
    { cn: '生命', field: 'hp' },
    { cn: '物攻', field: 'atk' },
    { cn: '魔攻', field: 'sp_atk' },
    { cn: '物防', field: 'def' },
    { cn: '魔防', field: 'sp_def' },
    { cn: '速度', field: 'speed' },
  ];
  const STAT_FIELDS = STATS.map((s) => s.field);

  /* 模块级状态：本页唯一数据源（普通对象，每次变更后手动重绘）。 */
  const S = {
    config: null,      // /api/team/config
    spirits: [],       // 精灵目录（全量）
    spiritsByName: {},
    teamSize: 3,
    slots: [],         // 槽位数组（length === teamSize）
    selSlot: 0,        // 当前选中槽位下标
    pool: [],          // 当前选中精灵的技能池
    poolTotal: 0,      // 全部可学
    poolImpl: 0,       // 已实装（可配置）
  };

  const $ = (sel) => document.querySelector(sel);

  /* ---------- 工具 ---------- */

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let j = {};
    try { j = await res.json(); } catch (_) { /* 非 JSON 响应（如 5xx 页面） */ }
    return { ok: res.ok, status: res.status, j };
  }

  /* 页面底部浮条提示：ok / warn 两种样式，3s 自动消失。 */
  function flash(msg, kind) {
    const el = $('#notice');
    el.textContent = msg;
    el.className = 'notice ' + (kind || '');
    clearTimeout(flash._t);
    flash._t = setTimeout(() => { el.textContent = ''; el.className = 'notice'; }, 3200);
  }

  /* 复刻 environment/statline.calc_combat_stats（真实公式，逐字节一致）：
   *   hp:    floor(1.7 × (种族 + iv×3) + 70) × 性格修正 → floor(+100)
   *   其余： floor(1.1 × (种族 + iv×3) + 50) × 性格修正 → floor(+50)
   * 性格修正：提升项 ×1.2 / 降低项 ×0.9 / 其余 ×1.0。 */
  function statPreview(base, iv, natureName) {
    const mod = (S.config.natures || []).find((n) => n.name === natureName) || { plus: '', minus: '' };
    const out = {};
    for (const k of STAT_FIELDS) {
      const growth = (base[k] || 0) + (iv[k] || 0) * 3;
      let raw = k === 'hp' ? Math.floor(1.7 * growth + 70) : Math.floor(1.1 * growth + 50);
      if (mod.plus === k) raw = Math.floor(raw * 1.2);
      else if (mod.minus === k) raw = Math.floor(raw * 0.9);
      out[k] = Math.floor(raw + (k === 'hp' ? 100 : 50));
    }
    return out;
  }

  /* 加载队伍时把 IV 清洗成合法整数（六维、0–iv_max），丢弃坏键/坏值。
   * 防「存文件里塞 iv: {"hp": "0\"><img src=x onerror=…>"} 的存储型 XSS」——
   * 任何来自磁盘的字符串都不允许进 value 属性 / 参与计算。 */
  function sanitizeIv(iv) {
    const out = {};
    if (iv && typeof iv === 'object') {
      for (const k of Object.keys(iv)) {
        if (!STAT_FIELDS.includes(k)) continue;
        const n = Math.floor(Number(iv[k]));
        if (Number.isFinite(n) && n > 0) out[k] = Math.max(0, Math.min(S.config.iv_max, n));
      }
    }
    return out;
  }

  /* ---------- 槽位模型 ---------- */

  function emptySlot() {
    return { spirit: null, skills: [], bloodline: '', nature: '坦率', iv: {} };
  }

  function resizeTeam(n) {
    // 缩小/放大槽位数组：截断或补 null（缩小前调用方负责确认丢弃非空槽）
    const slots = [];
    for (let i = 0; i < n; i++) slots.push(S.slots[i] ? S.slots[i] : null);
    S.slots = slots;
    S.teamSize = n;
    if (S.selSlot >= n) S.selSlot = Math.max(0, n - 1);
    renderTeamSize();
  }

  function familyConflict(sp) {
    // FULL 规则：同一家族（family_key）只能入队一只；无家族 key 永不冲突
    if (!sp.family_key) return null;
    for (const slot of S.slots) {
      if (slot && slot.spirit && slot.spirit !== sp
          && slot.spirit.family_key && slot.spirit.family_key === sp.family_key) {
        return slot.spirit;
      }
    }
    return null;
  }

  /* ---------- 渲染：顶部 ---------- */

  function renderTeamSize() {
    const sel = $('#team-size-select');
    sel.innerHTML = '';
    const allowed = Array.isArray(S.config.team_size.allowed)
      ? S.config.team_size.allowed : [S.config.team_size.default];
    for (const n of allowed) {
      const opt = document.createElement('option');
      opt.value = n;
      opt.textContent = `${n}v${n}`;
      if (n === S.teamSize) opt.selected = true;
      sel.appendChild(opt);
    }
    const filled = S.slots.filter((s) => s && s.spirit).length;
    $('#team-count').textContent = `已选 ${filled}/${S.teamSize}`;
  }

  /* ---------- 渲染：精灵目录 ---------- */

  function spiritsFiltered() {
    const q = ($('#spirit-search').value || '').trim().toLowerCase();
    if (!q) return S.spirits;
    return S.spirits.filter((sp) =>
      sp.name.toLowerCase().includes(q)
      || sp.number.toLowerCase().includes(q)
      || (sp.types || []).some((t) => t.toLowerCase().includes(q)));
  }

  function renderSpiritList() {
    const list = $('#spirit-list');
    const items = spiritsFiltered();
    list.innerHTML = '';
    for (const sp of items) {
      const card = document.createElement('button');
      const boss = sp.is_boss;
      const conflict = familyConflict(sp);
      card.className = 'cat-card spirit-card' + (boss ? ' disabled' : '') + (conflict ? ' family-warn' : '');
      card.title = boss
        ? '首领形态不可入队'
        : (conflict ? `同家族「${conflict.name}」已在队中（每家族只能一只）` : (sp.trait_desc || ''));
      card.innerHTML = `
        <div class="cat-name">${escapeHtml(sp.name)} <span class="cat-num">#${escapeHtml(sp.number)}</span>${boss ? ' <b class="badge">BOSS</b>' : ''}</div>
        <div class="cat-meta">${escapeHtml((sp.types || []).join(' / ') || '未知')}${sp.family_key ? ' <span class="dim">家族#' + escapeHtml(sp.family_key) + '</span>' : ''}</div>
        <div class="cat-sub">${escapeHtml(sp.trait_name || '')}</div>`;
      card.addEventListener('click', () => (boss ? flash('首领形态不可入队。', 'warn') : addSpirit(sp)));
      list.appendChild(card);
    }
    if (!items.length) list.innerHTML = '<div class="empty">没有匹配的精灵</div>';
    $('#spirit-count').textContent = `${items.length}/${S.spirits.length}`;
  }

  function addSpirit(sp) {
    if (sp.is_boss) { flash('首领形态不可入队。', 'warn'); return; }
    const conflict = familyConflict(sp);
    if (conflict) { flash(`同家族「${conflict.name}」已在队中（每家族只能一只）。`, 'warn'); return; }
    let idx = S.slots.findIndex((s) => !s || !s.spirit);
    if (idx === -1) { flash('队伍已满，请先移除一只。', 'warn'); return; }
    const slot = emptySlot();
    slot.spirit = sp;
    slot.bloodline = sp.types[0] || '';   // 初始血脉 = 主系别
    S.slots[idx] = slot;
    S.selSlot = idx;
    renderAll();
    refreshPool();
  }

  /* ---------- 渲染：队伍槽位 ---------- */

  function selectedSlot() {
    const slot = S.slots[S.selSlot];
    return slot && slot.spirit ? slot : null;
  }

  function renderTeamSlots() {
    const wrap = $('#roster-slots');
    wrap.innerHTML = '';
    S.slots.forEach((slot, i) => {
      const btn = document.createElement('button');
      const selected = i === S.selSlot;
      btn.className = 'slot-card'
        + (selected ? ' selected' : '')
        + (!slot || !slot.spirit ? ' empty' : '');
      if (slot && slot.spirit) {
        const sp = slot.spirit;
        btn.innerHTML = `
          <div class="slot-name">${escapeHtml(sp.name)} <span class="cat-num">#${escapeHtml(sp.number)}</span></div>
          <div class="slot-meta">${escapeHtml((sp.types || []).join('/'))}</div>
          <div class="slot-sub">技能 ${slot.skills.length}/${S.config.skill_slots} · ${escapeHtml(slot.nature)}</div>`;
      } else {
        btn.innerHTML = '<div class="slot-name dim">+ 空槽</div>';
      }
      btn.addEventListener('click', () => {
        S.selSlot = i;
        renderAll();
        refreshPool();
      });
      wrap.appendChild(btn);
    });
  }

  /* 槽位编辑器（整块重建，事件须在渲染后重新绑定）。 */
  function renderSlotEditor() {
    const box = $('#slot-editor');
    const slot = selectedSlot();
    if (!slot) {
      box.innerHTML = '<div class="empty">从左侧选中一只精灵，开始配置队伍</div>';
      return;
    }
    const sp = slot.spirit;
    const ivDims = Object.keys(slot.iv).filter((k) => slot.iv[k] > 0).length;
    const preview = statPreview(sp.stats, slot.iv, slot.nature);

    const skillChips = slot.skills.map((name, i) =>
      `<button class="chosen-skill" data-rm="${i}" title="点击移除">${escapeHtml(name)}<br><small>点击移除</small></button>`
    ).join('') + (slot.skills.length < S.config.skill_slots
      ? '<div class="chosen-skill empty">+ 从右栏技能池添加</div>' : '');

    const ivRows = STATS.map((st) => {
      const v = slot.iv[st.field] || 0;
      return `<div class="ev-row"><label>${st.cn}</label>
        <input type="number" min="0" max="${S.config.iv_max}" value="${escapeHtml(v)}" data-field="${st.field}" class="iv-input">
        <span class="ev-max">/${S.config.iv_max}</span></div>`;
    }).join('');

    const bloodOpts = ['', ...(S.config.types || [])].map((t) =>
      `<option value="${escapeHtml(t)}" ${t === slot.bloodline ? 'selected' : ''}>${t ? escapeHtml(t) : '（无）'}</option>`
    ).join('');

    const natureOpts = (S.config.natures || []).map((n) =>
      `<option value="${escapeHtml(n.name)}" ${n.name === slot.nature ? 'selected' : ''}>${escapeHtml(natureLabel(n))}</option>`
    ).join('');

    box.innerHTML = `
      <h3>${escapeHtml(sp.name)} <small>#${escapeHtml(sp.number)} · ${escapeHtml(sp.trait_name || '无特性')}</small>
        <button class="remove-btn" id="btn-remove-slot" title="移除这只精灵">移除</button></h3>
      <div class="editor-section">
        <div class="editor-title">技能（右栏技能池点击添加，点击已选移除；1–${S.config.skill_slots} 个）</div>
        <div class="skill-slots">${skillChips}</div>
      </div>
      <div class="editor-section two-col">
        <div>
          <div class="editor-title">血脉系别（决定可携带的血脉技能）</div>
          <select id="blood-select">${bloodOpts}</select>
        </div>
        <div>
          <div class="editor-title">性格</div>
          <select id="nature-select">${natureOpts}</select>
        </div>
      </div>
      <div class="editor-section">
        <div class="editor-title">个体值 IV（0–${S.config.iv_max}，最多 ${S.config.iv_dims_max} 个维度有投入）</div>
        <div class="ev-grid">${ivRows}</div>
        <div class="ev-budget ${ivDims > S.config.iv_dims_max ? 'over' : ''}">投入维度 ${ivDims}/${S.config.iv_dims_max}</div>
      </div>
      <div class="editor-section">
        <div class="editor-title">计算后属性（预览复刻 calc_combat_stats；保存后以服务端为准）</div>
        <div class="prev-grid">${STATS.map((st) =>
          `<div class="prev-row"><span>${st.cn}</span><b>${preview[st.field] ?? '-'}</b></div>`).join('')}</div>
      </div>`;

    box.querySelectorAll('.chosen-skill[data-rm]').forEach((btn) => {
      btn.addEventListener('click', () => {
        slot.skills.splice(+btn.dataset.rm, 1);
        renderAll();
      });
    });
    $('#btn-remove-slot').addEventListener('click', () => {
      S.slots[S.selSlot] = null;
      renderAll();
      refreshPool();
    });
    $('#blood-select').addEventListener('change', (e) => {
      slot.bloodline = e.target.value;
      refreshPool();      // 血脉变化 → 可学池（血脉技能）变化
    });
    $('#nature-select').addEventListener('change', (e) => {
      slot.nature = e.target.value;
      renderAll();
    });
    box.querySelectorAll('.iv-input').forEach((input) => {
      input.addEventListener('change', () => {
        const field = input.dataset.field;
        const v = Math.max(0, Math.min(S.config.iv_max, parseInt(input.value || '0', 10) || 0));
        const prevDims = Object.keys(slot.iv).filter((k) => slot.iv[k] > 0).length;
        const willBeNewDim = v > 0 && !(slot.iv[field] > 0);
        if (willBeNewDim && prevDims >= S.config.iv_dims_max) {
          flash(`最多 ${S.config.iv_dims_max} 个维度可投入个体值。`, 'warn');
          input.value = slot.iv[field] || 0;
          return;
        }
        if (v > 0) slot.iv[field] = v; else delete slot.iv[field];
        renderAll();
      });
    });
  }

  function natureLabel(n) {
    const parts = [];
    for (const st of STATS) {
      if (n.plus === st.field) parts.push(`${st.cn}+20%`);
      if (n.minus === st.field) parts.push(`${st.cn}−10%`);
    }
    return parts.length ? `${n.name}（${parts.join(' ')}）` : `${n.name}（无修正）`;
  }

  /* ---------- 渲染：技能池 ---------- */

  // 请求序号：快速切换槽位/血脉时丢弃过期响应，避免旧技能池覆盖新槽位
  let _poolReq = 0;

  async function refreshPool() {
    const reqId = ++_poolReq;
    const slot = selectedSlot();
    $('#skill-panel').classList.toggle('no-sel', !slot);
    if (!slot) {
      S.pool = []; S.poolTotal = 0; S.poolImpl = 0;
      renderSkillList();
      return;
    }
    const r = await api('/api/team/skills?spirit=' + encodeURIComponent(slot.spirit.name)
      + '&bloodline=' + encodeURIComponent(slot.bloodline));
    if (reqId !== _poolReq) return;                 // 过期响应直接丢弃
    if (!r.ok) { flash('技能池加载失败。', 'warn'); return; }
    S.pool = r.j.skills || [];
    S.poolTotal = r.j.total;
    S.poolImpl = r.j.implemented;
    // 血脉/技能池变化后，剪掉已选技能里不再可携带的（如换血脉导致血脉技能失效），
    // 避免静默存下一支过不了校验的队伍。只在「当前选中槽 = 本次拉池的槽」时剪。
    const cur = selectedSlot();
    if (cur && cur.spirit === slot.spirit) {
      const names = new Set(S.pool.map((s) => s.name));
      const removed = cur.skills.filter((n) => !names.has(n));
      if (removed.length) {
        cur.skills = cur.skills.filter((n) => names.has(n));
        flash(`技能池变化，已移除不再可携带的技能：${removed.join('、')}`, 'warn');
        renderAll();
        return;   // renderAll 已重绘技能列表
      }
    }
    renderSkillList();
  }

  function poolFiltered() {
    const q = ($('#skill-search').value || '').trim().toLowerCase();
    if (!q) return S.pool;
    return S.pool.filter((s) =>
      s.name.toLowerCase().includes(q) || s.type.toLowerCase().includes(q));
  }

  function renderSkillList() {
    const list = $('#skill-list');
    const items = poolFiltered();
    list.innerHTML = '';
    const slot = selectedSlot();
    if (!slot) {
      list.innerHTML = '<div class="empty">先选择一只精灵，显示它的可学技能池</div>';
      $('#skill-count').textContent = '';
      return;
    }
    for (const sk of items) {
      const card = document.createElement('button');
      card.className = 'cat-card skill-card' + (sk.battle_ready ? '' : ' disabled');
      if (!sk.battle_ready) card.title = '效果未实装（P1∪P2 白名单外），暂不可携带';
      card.innerHTML = `
        <div class="cat-name">${escapeHtml(sk.name)} <span class="tag">${escapeHtml(sk.type)}</span>${sk.battle_ready ? '' : ' <span class="badge-warn">未实装</span>'}</div>
        <div class="cat-meta">${escapeHtml(sk.kind || '')} · 威力 ${sk.power ? escapeHtml(String(sk.power)) : '—'} · 能量 ${escapeHtml(String(sk.energy_cost))}</div>
        <div class="cat-sub">${escapeHtml(sk.desc || '')}</div>`;
      card.addEventListener('click', () => {
        if (!sk.battle_ready) { flash('该技能效果未实装，暂不可携带。', 'warn'); return; }
        addSkill(sk);
      });
      list.appendChild(card);
    }
    if (!items.length) list.innerHTML = '<div class="empty">没有匹配的技能</div>';
    $('#skill-count').textContent = `可配置 ${S.poolImpl}/${S.poolTotal}（已实装/全部可学）`;
  }

  function addSkill(sk) {
    const slot = selectedSlot();
    if (!slot) { flash('先选择一只精灵。', 'warn'); return; }
    if (slot.skills.includes(sk.name)) { flash('该技能已选择。', 'warn'); return; }
    if (slot.skills.length >= S.config.skill_slots) { flash(`最多携带 ${S.config.skill_slots} 个技能。`, 'warn'); return; }
    slot.skills.push(sk.name);
    renderAll();
  }

  /* ---------- 校验 / 保存 / 加载 ---------- */

  function picksPayload() {
    return S.slots.filter((s) => s && s.spirit).map((s) => ({
      spirit: s.spirit.name,
      skills: s.skills.slice(),
      bloodline: s.bloodline,
      nature: s.nature,
      iv: Object.assign({}, s.iv),
    }));
  }

  async function validate() {
    const body = { team: picksPayload(), team_size: S.teamSize };
    const r = await api('/api/team/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const box = $('#validate-result');
    if (r.ok && r.j.ok) {
      box.className = 'validate-box ok';
      box.innerHTML = `<b>✓ 队伍合法（${r.j.team_size}v${r.j.team_size}）</b><br>已选 ${r.j.roster.length} 只，属性以服务端计算为准。`;
    } else {
      box.className = 'validate-box err';
      const errors = (Array.isArray(r.j.errors) ? r.j.errors : [typeof r.j.detail === 'string' ? r.j.detail : ('HTTP ' + r.status)]).slice(0, 12);
      box.innerHTML = `<b>队伍不合法（${errors.length} 处）：</b><ul>${errors.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`;
    }
  }

  async function save() {
    const path = ($('#save-path').value || '').trim();
    const body = { team: picksPayload(), team_size: S.teamSize, path };
    const r = await api('/api/team/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (r.ok && r.j.ok) {
      flash(`已保存到 ${r.j.path}`, 'ok');
      $('#save-path').value = '';
      refreshSaved();
    } else {
      const detail = r.j.detail;
      const msg = typeof detail === 'object' && detail && Array.isArray(detail.errors)
        ? detail.errors.join('；')
        : (typeof detail === 'string' ? detail : ('HTTP ' + r.status));
      flash('保存失败：' + msg, 'warn');
    }
  }

  async function refreshSaved() {
    const r = await api('/api/team/saved');
    const sel = $('#saved-list');
    sel.innerHTML = '';
    if (!r.ok || !r.j.teams.length) {
      sel.innerHTML = '<option value="">（暂无已存队伍）</option>';
      return;
    }
    for (const t of r.j.teams) {
      const opt = document.createElement('option');
      opt.value = t.path;
      opt.textContent = `${t.name}（${t.team_size}只 · ${t.spirits.join('、') || '空'}）`;
      sel.appendChild(opt);
    }
  }

  async function loadSelected() {
    const path = $('#saved-list').value;
    if (!path) { flash('请先选择一个已存队伍。', 'warn'); return; }
    const r = await api('/api/team/load?path=' + encodeURIComponent(path));
    if (!r.ok) { flash('加载失败：' + (r.j.detail || ('HTTP ' + r.status)), 'warn'); return; }
    applyLoaded(r.j);
  }

  /* 加载后的统一落地：按 team_size 恢复槽位 + 自动校验（历史队伍可能已失效）。
     历史 4v4/5v5 队伍（不再支持）→ 就近吸附到 3 或 6 + 提示。 */
  function applyLoaded(data) {
    const team = Array.isArray(data.team) ? data.team : [];
    const allowed = Array.isArray(S.config.team_size.allowed)
      ? S.config.team_size.allowed : [S.config.team_size.default];
    let size = Number(data.team_size) || team.length || S.config.team_size.default;
    let snapped = false;
    if (!allowed.includes(size)) {
      // 就近吸附：4→3、5→6；其余默认 3
      const nearest = allowed.reduce((best, n) =>
        Math.abs(n - size) < Math.abs(best - size) ? n : best, allowed[0]);
      snapped = true;
      size = nearest;
    }
    S.selSlot = 0;
    const slots = [];
    for (let i = 0; i < size; i++) {
      const p = team[i];
      if (!p || typeof p !== 'object' || !S.spiritsByName[p.spirit]) { slots.push(null); continue; }
      // 技能归一化：v1 存字符串数组，v2 存 {name,type,desc} 富化对象 → 都转回名字数组
      const skillNames = (Array.isArray(p.skills) ? p.skills : []).map(
        (x) => (typeof x === 'string' ? x : (x && x.name) || '')).filter(Boolean);
      slots.push({
        spirit: S.spiritsByName[p.spirit],
        skills: skillNames,
        bloodline: typeof p.bloodline === 'string' ? p.bloodline : '',
        nature: typeof p.nature === 'string' ? p.nature : '坦率',
        iv: sanitizeIv(p.iv),
      });
    }
    S.slots = slots;
    S.teamSize = size;
    renderAll();
    refreshPool();
    validate();   // 加载即校验：未实装/家族冲突等会立即提示
    flash(snapped ? `历史队伍规模已吸附到 ${size}v${size}（原 ${data.team_size} 不再支持）。` : `已加载队伍（${size} 只）。`, snapped ? 'warn' : 'ok');
  }

  async function deleteSelected() {
    const path = $('#saved-list').value;
    if (!path) { flash('请先选择一个已存队伍。', 'warn'); return; }
    if (!window.confirm(`确定删除 ${path} ？`)) return;
    const r = await api('/api/team/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (r.ok) { flash('已删除。', 'ok'); refreshSaved(); }
    else { flash('删除失败：' + (r.j.detail || ('HTTP ' + r.status)), 'warn'); }
  }

  /* ---------- 渲染入口 ---------- */

  function renderAll() {
    renderTeamSize();
    renderSpiritList();
    renderTeamSlots();
    renderSlotEditor();
    renderSkillList();
  }

  /* ---------- 事件绑定 ---------- */

  function wire() {
    $('#team-size-select').addEventListener('change', (e) => {
      const n = +e.target.value;
      if (n < S.teamSize && S.slots.slice(n).some((s) => s && s.spirit)) {
        if (!window.confirm(`缩小队伍规模将移除第 ${n + 1} 只及以后的精灵，确定？`)) {
          $('#team-size-select').value = S.teamSize;
          return;
        }
      }
      resizeTeam(n);
      renderAll();      // 重绘槽位卡片/编辑器（缩小后 selSlot 已夹回合法范围）
      refreshPool();    // 技能池跟随新的选中槽位
    });
    $('#spirit-search').addEventListener('input', renderSpiritList);
    $('#skill-search').addEventListener('input', renderSkillList);
    $('#btn-validate').addEventListener('click', validate);
    $('#btn-save').addEventListener('click', save);
    $('#btn-load').addEventListener('click', loadSelected);
    $('#btn-delete').addEventListener('click', deleteSelected);
    $('#save-path').addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
  }

  async function init() {
    const cfgR = await api('/api/team/config');
    if (!cfgR.ok) { flash('组队配置加载失败。', 'warn'); return; }
    S.config = cfgR.j;
    const spR = await api('/api/team/spirits');
    if (spR.ok) {
      S.spirits = spR.j.spirits || [];
      S.spirits.forEach((sp) => { S.spiritsByName[sp.name] = sp; });
    }
    resizeTeam(S.config.team_size.default);
    wire();
    renderAll();
    refreshPool();
    refreshSaved();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
