"""R0 反馈函数 μ_f：把一回合分析确定性渲染成反思器可见的文本（§6.1）。

**绝不由 LLM 撰写或评分**——输入是引擎事件流 + 局面快照，输出是确定性文本。
本轮（R0）渲染「事件机制部分」：双方提交、事件（含克制/应对/减伤/能量机制数字）、
局面与价值落差。

**迷雾口径**（§7.3，不破坏）：
- 事件用过滤后的 `events_a/b`（玩家视角）；
- **对手行动按 `TurnAnalysis.foe_revealed`（按原始槽位对齐的已揭示技能名）渲染**——
  未揭示的技能只显示「技能（槽位 X）」不显示名字（从敌方全量视图取技能名会泄露
  玩家拿不到的技能名/能耗）；
- 「价值 V」是 full-state 口径的离线价值估计（§3.3 刻意设计，**绝不注入对战玩家**），
  供 R2 信度分配与 R3 反思使用。

`prediction`（校准偏差）与 `counterfactual`（反事实回放）属于 R2——`TurnAnalysis`
已含 `prediction` 字段（R0 恒 None），渲染从 R2 接上。
"""

from __future__ import annotations

from environment.models import SIDES

from roco_pvp_agent.battle.evolution.analysis import TurnAnalysis
from roco_pvp_agent.battle.prompts import _brief_event


def _my_action_line(decision: dict, item: str, view: dict) -> str:
    """我方行动一行：动作描述 + 技能能量预算（能量 前→后）+ 道具。

    `view` 是我方全量视图——自己的技能名/能耗完全可见，可放心渲染。
    """
    line = _brief_action(decision, view["me"])
    if decision.get("type") == "skill":
        idx = decision.get("value")
        me = view["me"]
        unit = me["units"][me["active"]]
        if isinstance(idx, int) and 0 <= idx < len(unit["skills"]):
            cost = unit["skills"][idx]["energy_cost"]
            line += f"（能量 {unit['energy']}→{max(0, unit['energy'] - cost)}）"
    if item:
        line += f" + 道具「{item}」"
    return line


def _foe_action_line(decision: dict, item: str, revealed_names: list[str | None],
                     view: dict) -> str:
    """对手行动一行（**迷雾口径**）：技能名只取我方已揭示的，否则只给槽位。

    - 技能：名字取 `revealed_names`（按原始槽位对齐，未揭示 → None）；能耗不可见，
      不给「能量 前→后」（那是全量信息）；
    - 换人：目标名从白名单（`view["opponent"]` 有敌方后备名字）；
    - 聚能 / 道具：字面。
    """
    atype = decision.get("type")
    if atype == "skill":
        idx = decision.get("value")
        name = None
        if isinstance(idx, int) and revealed_names and 0 <= idx < len(revealed_names):
            name = revealed_names[idx]
        line = f"技能「{name}」" if name else f"技能（槽位 {idx}）"
    elif atype == "switch":
        idx = decision.get("value")
        opp = view["opponent"]
        units = opp["units"]
        if isinstance(idx, int) and 0 <= idx < len(units) and not units[idx]["fainted"]:
            line = f"换人 → 「{units[idx]['name']}」"
        else:
            line = f"换人（槽位 {idx}）"
    elif atype == "recharge":
        line = "聚能（回复能量）"
    else:
        line = f"{atype or '未知'}（{decision.get('value')}）"
    if item:
        line += f" + 道具「{item}」"
    return line


def _brief_action(decision: dict, me_view: dict) -> str:
    """动作 → 一句中文（技能名查己方在场单位；换人查后备名）。"""
    atype = decision.get("type")
    idx = decision.get("value")
    units = me_view["units"]
    active = me_view["active"]
    if atype == "skill":
        unit = units[active]
        if isinstance(idx, int) and 0 <= idx < len(unit["skills"]):
            return f"技能「{unit['skills'][idx]['name']}」"
        return f"技能（槽位 {idx}）"
    if atype == "switch":
        if isinstance(idx, int) and 0 <= idx < len(units) and not units[idx]["fainted"]:
            return f"换人 → 「{units[idx]['name']}」"
        return f"换人（槽位 {idx}）"
    if atype == "recharge":
        return "聚能（回复能量）"
    return f"{atype or '未知'}（{idx}）"


def _mechanism_event(e: dict) -> str:
    """单条事件 → 一行；damage 补机制数字（克制/同系/应对/减伤），
    energy_gain/steal 单独渲染（避免裸 dict repr），reduce_arm 统一百分比。
    """
    t = e.get("type")
    s = e.get("side", "")
    if t == "damage":
        line = _brief_event(e)
        bits: list[str] = []
        eff, stab, mult, reduced = e.get("eff"), e.get("stab"), e.get("mult"), e.get("reduced")
        if eff is not None and eff != 1.0:
            bits.append(f"克制×{eff}")
        if stab is not None and stab != 1.0:
            bits.append(f"同系×{stab}")
        if mult is not None and mult != 1.0:
            bits.append(f"应对×{mult}")
        if reduced:
            bits.append(f"减伤{reduced * 100:.0f}%")
        if bits:
            line += f"（{' · '.join(bits)}）"
        return line
    if t == "energy_gain":
        return f"{s} {e.get('unit', '?')} 能量 +{e.get('gained', '?')}（来源「{e.get('source', '?')}」）"
    if t == "steal":
        return f"{s} {e.get('unit', '?')} 偷取 {e.get('foe', '?')} 能量 {e.get('gained', '?')}"
    line = _brief_event(e)
    if t == "reduce_arm":          # 与 damage 的「减伤70%」口径统一（_brief_event 输出小数 0.7）
        if e.get("armed"):
            pct = e.get("pct")
            if isinstance(pct, (int, float)):
                return f"{s} 武装防御（减伤 {pct * 100:.0f}%）"
        return f"{s} 武装防御（未触发）"
    return line


def render_feedback(ta: TurnAnalysis, *, side: str = "a", show_v: bool = True,
                    prediction: str | None = None, counterfactual: dict | None = None,
                    calibration: dict | None = None) -> str:
    """一回合 → 反思文本（side 视角：我方行动/对手行动/事件/局面/价值）。

    确定性：同样的 TurnAnalysis 必产出同样的文本。事件用过滤后的 `events_a/b`
    （玩家视角口径）；对手行动按 `foe_revealed` 迷雾渲染（§7.3）。
    `show_v=False` 时省略「价值 V」行——V 是 full-state 口径的离线价值估计，
    给**反思管线**（离线）可以看；给**对战玩家/记忆注入**（R3 起）不能带，
    否则把隐藏信息推导的标量喂进玩家视角（§7.3 不加信息面）。
    `prediction`（R2 校准原料）、`counterfactual`（R2 反事实结果）、
    `calibration`（R2 校准比对 `{prediction, actual, miss}`）非空时渲染对应行；
    「跨回合后效」由本方技能决策 + 我方视图确定性推导（能量付不起下回合）。
    """
    if side not in SIDES:
        raise ValueError(f"side 必须是 a 或 b，实际 {side!r}。")
    foe = "b" if side == "a" else "a"
    view = ta.views[side]
    me, opp = view["me"], view["opponent"]
    evs = ta.events_a if side == "a" else ta.events_b

    lines = [
        f"[T{ta.turn}] 我方行动: {_my_action_line(ta.decisions[side], ta.items[side], view)}",
        f"        对手行动: {_foe_action_line(ta.decisions[foe], ta.items[foe], ta.foe_revealed.get(side), view)}",
        "        【结算】",
    ]
    if prediction:
        lines.append(f"        预测: {prediction}")
    if evs:
        for e in evs:
            lines.append("        " + _mechanism_event(e))
    else:
        lines.append("        （本回合无事件）")

    v_before, v_after = ta.v_before[side], ta.v_after[side]
    my_active = me["units"][me["active"]]["name"]
    foe_active = opp["units"][opp["active"]]["name"]
    lines.append(f"        局面: 命数 {me['lives']}/{opp['lives']} · 在场 {my_active} vs {foe_active}")
    if calibration and calibration.get("miss"):
        pred = calibration.get("prediction", "")
        actual = calibration.get("actual", 0)
        pct = f"（偏差 {abs(actual - _first_int(pred)) / actual:.0%}）" if actual else ""
        lines.append(f"        校准: 预测「{pred}」vs 实际伤害 {actual} {pct}★认知错误")
    if counterfactual:
        better = counterfactual.get("counterfactual_better")
        if better is not None:
            desc = _brief_action(better, me)
            delta = counterfactual.get("delta_winrate", 0.0)
            n = counterfactual.get("n_replays", "?")
            lines.append(f"        反事实: 若改「{desc}」→ 代理尾胜率 {delta:+.2f}（M={n}）★")
    _append_aftereffect(lines, ta.decisions[side], me)
    if show_v:
        lines.append(f"        价值: V {v_before:+.2f} → {v_after:+.2f}（Δ{v_after - v_before:+.2f}）"
                     f"[full-state 离线口径]")
    return "\n".join(lines)


def _first_int(text: str) -> int | None:
    """预测文本里的第一个整数（校准偏差百分比用）；无 → None。"""
    import re
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _append_aftereffect(lines: list[str], decision: dict, me_view: dict) -> None:
    """跨回合后效（§6.1）：本方用了技能但下回合付不起 → 确定性推导、迷雾正确（我方视图）。

    只针对 skill 动作：`回合后能量 < 该技能能耗` → 下回合被迫聚能或换人。
    """
    if decision.get("type") != "skill":
        return
    idx = decision.get("value")
    units = me_view["units"]
    active = me_view["active"]
    if not (isinstance(idx, int) and 0 <= idx < len(units[active]["skills"])):
        return
    skill = units[active]["skills"][idx]
    cost = skill["energy_cost"]
    after = max(0, units[active]["energy"] - cost)
    if after < cost:
        lines.append(f"        跨回合后效: 我方能量 {after}，付不起「{skill['name']}」"
                     f"({cost})，下回合被迫聚能或换人")


def render_feedback_all(ta_list: list[TurnAnalysis], *, sides: tuple[str, str] = ("a", "b")) -> str:
    """整局 → 逐回合双方视角的反思文本拼接（`evolve reflect --dump` 的产物）。

    每回合渲染双方各一份（反思按 side 分桶，双方都要看自己的），末尾附终局摘要。
    空局 → 空串。
    """
    parts: list[str] = []
    for ta in ta_list:
        for s in sides:
            parts.append(render_feedback(ta, side=s))
    if ta_list:
        last = ta_list[-1]
        parts.append(f"[终局] 胜方: {last.winner or '平局'} · 回合数 {last.turn}")
    return "\n\n".join(parts)
