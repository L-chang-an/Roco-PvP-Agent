"""LLM 对战玩家的提示词与观测渲染（E6.5）。

渲染函数只消费**已迷雾的观测**（`session.view(side)` 的白名单 dict）——提示词工程在
白名单之上做，敌方绝对血量/六维/性格/血脉/IV/未揭示技能在渲染层**结构上不可能出现**
（白名单里根本没有这些键）。这是「信息隔离」的第二道闸：第一道在 `run_match` 只喂 `view()`，
第二道在这里只读白名单字段。
"""

from __future__ import annotations

STAT_CN = {"atk": "物攻", "sp_atk": "魔攻", "def": "物防", "sp_def": "魔防",
           "speed": "速度", "hp": "生命"}

BATTLE_PLAYER_SYSTEM_PROMPT = """你是《洛克王国》式回合制精灵对战的一位玩家（我方）。
每回合你会收到【当前战况】（我方全貌 + 敌方已揭示信息）与【本回合合法动作】列表。

战斗规则：
- 技能分三类：攻击（造成伤害）/ 防御（本回合减伤）/ 状态（属性增减益）。
- 应对三角：攻击 克制 状态（伤害 ×1.5）；状态 克制 防御（增益层数更多）；防御 克制 攻击（减伤）。
- 能量是释放技能的资源；付不起能耗的行动不会出现在合法动作里。

行动协议（每回合必须严格遵守）：
- 每回合**恰好调用一次** battle_act 提交你的行动，不要输出任何对话文本。
- battle_act(action_type, target, item)：
  · action_type ∈ {skill, switch, recharge}；
  · skill / switch 的 target 是该槽位下标（从 0 开始）；
  · recharge 不需要 target；
  · item 是道具名（不用则留空）。
- 只能提交【本回合合法动作】里的行动。若 battle_act 返回「行动非法」，按提示修正后重试。
- 目标是打赢对手：消耗对方命数（精灵倒下扣 1 命），同时保存自己的命数。"""


def _stat_cn(stat: str) -> str:
    """六维英文 key → 中文名（未知键原样返回）。"""
    return STAT_CN.get(stat, stat)


def _stat_line(stat: str, mode: str, layers: int) -> str:
    """一条增减益的中文描述：`物攻+100%`（pct）/ `速度-20`（flat）。"""
    sign = "+" if layers >= 0 else ""
    if mode == "flat":
        return f"{_stat_cn(stat)}{sign}{layers * 10}"
    return f"{_stat_cn(stat)}{sign}{layers * 10}%"


def _mod_line(m: dict) -> str:
    """stat_mods 单条（{stat, mode, layers, …}）→ `物攻+100%`。"""
    return _stat_line(m.get("stat", ""), m.get("mode", ""), m.get("layers", 0))


def _mods_text(mods: list[dict]) -> str:
    """增减益列表 → 逗号分隔；空 → 空串。"""
    if not mods:
        return ""
    return "，".join(_mod_line(m) for m in mods)


def render_observation(observation: dict, legal: list[dict], items: list[str]) -> str:
    """把迷雾观测渲染成 LLM 的中文战场文本。

    消费白名单字段：我方全量、敌方 name/types/hp_pct/energy/trait{name,desc}/已揭示技能/
    stat_mods/energy_cost_mods。敌方绝对血量等键不在白名单里，渲染层碰不到。
    """
    lines: list[str] = []
    side = observation.get("side", "?")
    turn = observation.get("turn", "?")
    me = observation["me"]
    foe = observation["opponent"]
    lines.append(f"你是我方（{side} 方）。第 {turn} 回合。"
                 f"我方剩余命数 {me['lives']}，敌方剩余命数 {foe['lives']}。")

    lines.append("【我方】")
    for i, u in enumerate(me["units"]):
        if u["fainted"]:
            continue
        active = i == me["active"]
        star = "（*在场）" if active else ""
        hp = f"{u['current_hp']}/{u['max_hp']}"
        mods = _mods_text(u.get("stat_mods") or [])
        ecm = u.get("energy_cost_mods") or []
        lines.append(f"  #{i}「{u['name']}」{'/'.join(u['types'])} 血量 {hp} 能量 {u['energy']}{star}")
        skills = " · ".join(
            f"[{j}] {s['name']}（{s.get('type', '?')}系 能耗{s['energy_cost']}）：{s.get('desc', '')}"
            for j, s in enumerate(u["skills"])
        )
        lines.append(f"    技能 {skills}")
        if mods:
            lines.append(f"    增减益 {mods}")
        if ecm:
            lines.append(f"    能耗减益 {len(ecm)} 条")

    lines.append("【敌方】")
    for i, u in enumerate(foe["units"]):
        if u["fainted"]:
            continue
        active = i == foe["active"]
        star = "（*在场）" if active else ""
        trait = u.get("trait") or {}
        trait_s = f"{trait.get('name', '')}" + (f"：{trait.get('desc', '')[:60]}" if trait.get("desc") else "")
        lines.append(f"  #{i}「{u['name']}」{'/'.join(u['types'])} 血量 {u['hp_pct']}% "
                     f"能量 {u['energy']} 特性 {trait_s or '?'}{star}")
        if u.get("skills"):
            revealed = " · ".join(
                f"[{j}] {s['name']}（{s.get('type', '?')}系 能耗{s['energy_cost']}）：{s.get('desc', '')}"
                for j, s in enumerate(u["skills"])
            )
            lines.append(f"    已见技能 {revealed}")
        else:
            lines.append("    已见技能 （尚未揭示）")
        mods = _mods_text(u.get("stat_mods") or [])
        if mods:
            lines.append(f"    增减益 {mods}")

    lines.append("【本回合合法动作】")
    if legal:
        for k, a in enumerate(legal):
            lines.append(f"  {k}. {_describe_action(a, observation)}")
    else:
        lines.append("  （无）")
    lines.append(f"【可用道具】{('、'.join(items)) if items else '无'}")
    lines.append("请调用 battle_act 提交行动。")
    return "\n".join(lines)


def _describe_action(action: dict, observation: dict) -> str:
    """合法动作 dict → 中文描述（技能名查己方在场单位）。"""
    atype = action.get("type")
    idx = action.get("value")
    me = observation["me"]
    if atype == "skill":
        unit = me["units"][me["active"]]
        if isinstance(idx, int) and 0 <= idx < len(unit["skills"]):
            s = unit["skills"][idx]
            return f"技能「{s['name']}」"
        return f"技能（槽位 {idx}）"
    if atype == "switch":
        units = me["units"]
        if isinstance(idx, int) and 0 <= idx < len(units) and not units[idx]["fainted"]:
            return f"换人 → 「{units[idx]['name']}」"
        return f"换人（槽位 {idx}）"
    if atype == "recharge":
        return "聚能（回复能量）"
    return f"{atype}（{idx}）"


def render_replacement(observation: dict, bench: list[int]) -> str:
    """补位提示：我方在场阵亡，请从存活后备里选一个。"""
    me = observation["me"]
    lines = ["我方在场精灵已倒下，请选择补位（存活后备）。"]
    for i in bench:
        u = me["units"][i]
        mods = _mods_text(u.get("stat_mods") or [])
        lines.append(f"  槽位 {i}：「{u['name']}」 血量 {u['current_hp']}/{u['max_hp']} "
                     f"能量 {u['energy']}" + (f" 增减益 {mods}" if mods else ""))
    lines.append("请调用 battle_act(action_type='replace', target=<槽位下标>)。")
    return "\n".join(lines)


def render_events(events: list[dict]) -> str:
    """回合事件 → 中文摘要（LLM 学习本回合发生了什么）。消费**已过滤**事件（run_match 给 view 口径）。"""
    if not events:
        return "（本回合无事件）"
    return "\n".join(_brief_event(e) for e in events)


def _brief_event(e: dict) -> str:
    """单条事件 → 一行中文。字段缺失时尽量容错（事件形状见 environment/events.py）。"""
    t = e.get("type", "?")
    s = e.get("side", "")
    if t == "damage":
        if "target_hp_pct" in e:
            hp = f"剩{e['target_hp_pct']}%"
        else:
            hp = f"剩{e.get('target_hp_left', '?')}"
        return f"{s} {e.get('attacker', '?')} 用「{e.get('skill', '?')}」→ {e.get('target', '?')} 伤害 {e.get('damage', '?')}（{hp}）"
    if t == "heal":
        return f"{s} {e.get('unit', '?')} 回复 {e.get('applied', '?')}"
    if t == "stat_change":
        return f"{s} {e.get('unit', '?')} {_stat_line(e.get('stat', ''), e.get('mode', ''), e.get('layers', 0))}"
    if t == "recharge":
        return f"{s} {e.get('unit', '?')} 聚能 +{e.get('gained', '?')}"
    if t == "switch":
        return f"{s} 换人 {e.get('out', '?')} → {e.get('in', '?')}"
    if t == "replace":
        return f"{s} 补位 {e.get('out', '?')} → {e.get('in', '?')}"
    if t == "faint":
        return f"{s} {e.get('unit', '?')} 倒下"
    if t == "life_loss":
        return f"{s} {e.get('unit', '?')} 命数-1（剩 {e.get('lives_left', '?')}）"
    if t == "item_use":
        return f"{s} 使用道具「{e.get('item', '?')}」"
    if t == "reduce_arm":
        return f"{s} 武装防御（减伤 {e.get('pct', '?')}）"
    if t == "battle_end":
        return f"对局结束：{e.get('message', e.get('winner', '?'))}"
    if t == "skipped":
        return f"{s} {e.get('unit', '?')} 跳过（{e.get('reason', '')}）"
    if t == "error":
        return f"错误：{e.get('message', '')}"
    return f"{s} {t} {e}"
