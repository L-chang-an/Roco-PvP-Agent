"""R0 可观测度量：确定性局面特征（纯函数，零第三方依赖）。

三个纯函数，供 analysis / valuefn / smc 复用：
- `situation_key(view)`：迷雾 view() → 定长局面键（§6.3，记忆检索的硬过滤依据）。
  可读字符串而非 hash——记忆条目里直接落这个键（§3.2 的 situation_key 就是它），
  Phase A 硬过滤用「精确相等」，Phase B 的相似度交给 R1 的 Embedder（Keyword/TF-IDF）。
- `v_heuristic(state, side)`：`0.6·命数差 + 0.3·血量比差 + 0.1·能量差`（确定性启发式价值，
  §3.3 档位 A-0）。命数差是原始整数（主导信号）；血量比差 / 能量差归一化到 [−1, 1]。
- `ko_thresholds(rules)`：每攻击技能对每防御精灵的击杀线——1=一击必杀，2=两回合击杀，
  超过 2 击（或打不死）不建键。纯引擎穷举（compute_damage + 克制/STAB），供 R6 构筑 L3 数值
  剪枝与信度分析的伤害线参考。

本模块是 environment 包的一部分：**永不 import roco_pvp_agent**，保持引擎零第三方依赖、
确定性不变式不受影响。全部输入输出都是 JSON 原生类型，可单测。
"""

from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace

from .damage import compute_damage
from .dataset import DataSource, load_skills, load_spirits
from .models import Unit, build_unit
from .presets import valid_spirit_candidates
from .rules import DEFAULT_RULES, BattleRules
from .skillbook import battle_ready
from .statline import calc_combat_stats
from .types import stab_multiplier, type_effectiveness

# situation_key 的档位/阶段枚举（确定性、可审计）。
ENERGY_BANDS: tuple[str, str, str] = ("low", "mid", "high")
PHASES: tuple[str, str, str] = ("early", "mid", "endgame")

# ko_thresholds：只记 1 击 / 2 击击杀线（R6 L3 剪枝用「一击必杀/两回合击杀」阈值）。
KO_HITS_CAP = 2


# ---------------------------------------------------------------------------
# situation_key / situation_features（§6.3，迷雾 view() 提取，确定性）
# ---------------------------------------------------------------------------


def _energy_band(energy: int, energy_max: int) -> str:
    """能量档：三等分。`energy_max=10`（third=3）→ low:0–2、mid:3–5、high:6+。

    阈值按 `max(1, energy_max // 3)` 与 `2*third` 划分，任何 energy_max 都确定、不越界。
    """
    third = max(1, energy_max // 3)
    if energy < third:
        return "low"
    if energy < 2 * third:
        return "mid"
    return "high"


def _phase(my_lives: int, foe_lives: int, lives: int) -> str:
    """阶段：任一方剩 1 命 → endgame；否则按「已消耗命数占总命数比例」分 early/mid。

    §6.3 用阶段区分残局与非残局（M4 endgame 只在该阶段激活）。确定性、可单测。
    """
    if my_lives == 1 or foe_lives == 1:
        return "endgame"
    total = max(1, lives * 2)
    spent = (total - my_lives - foe_lives) / total
    return "early" if spent < 0.25 else "mid"


def _bench_count(side_view: dict) -> int:
    """存活后备数：非在场、未阵亡的单位数。"""
    return sum(1 for i, u in enumerate(side_view["units"])
               if i != side_view["active"] and not u["fainted"])


def situation_features(view: dict) -> dict:
    """迷雾 view() → 局面特征 dict（§6.3 的十个分量，确定性、只读白名单字段）。

    消费 `me`（己方全量）与 `opponent`（敌方白名单）两个口径里都存在的字段：
    命数 / 在场精灵名 / 能量档 / 已揭示技能数 / 存活后备数——结构上碰不到敌方隐藏字段。
    """
    me, foe = view["me"], view["opponent"]
    rules = view.get("rules") or {}
    energy_max = rules.get("energy_max", DEFAULT_RULES.energy_max)
    lives = rules.get("lives", DEFAULT_RULES.lives)
    my_lives, foe_lives = me["lives"], foe["lives"]
    return {
        "my_lives": my_lives,
        "foe_lives": foe_lives,
        "my_active": me["units"][me["active"]]["name"],
        "foe_active": foe["units"][foe["active"]]["name"],
        "my_energy_band": _energy_band(me["units"][me["active"]]["energy"], energy_max),
        "foe_energy_band": _energy_band(foe["units"][foe["active"]]["energy"], energy_max),
        "foe_revealed_skills": sum(len(u.get("skills") or []) for u in foe["units"]),
        "phase": _phase(my_lives, foe_lives, lives),
        "my_bench": _bench_count(me),
        "foe_bench": _bench_count(foe),
    }


def situation_key(view: dict) -> str:
    """迷雾 view() → 定长局面键（§3.2 / §6.3 的可读形式，可审计、可硬过滤）。

    格式：`my{lives}/foe{lives}/{我方在场}/{敌方在场}/{能量档}/{能量档}/{已揭示数}/{阶段}/{后备}/{后备}`
    例：`my2/foe3/迪莫/火苗/low/mid/2/mid/1/1`。完全由 situation_features 派生，确定性。
    """
    f = situation_features(view)
    return (f"my{f['my_lives']}/foe{f['foe_lives']}/{f['my_active']}/{f['foe_active']}/"
            f"{f['my_energy_band']}/{f['foe_energy_band']}/{f['foe_revealed_skills']}/"
            f"{f['phase']}/{f['my_bench']}/{f['foe_bench']}")


# ---------------------------------------------------------------------------
# v_heuristic（§3.3 档位 A-0，确定性启发式价值）
# ---------------------------------------------------------------------------


def _hp_ratio(side_state) -> float:
    """血量比例：`Σ current / Σ max`（0..1，全队口径）。"""
    total_max = sum(u.max_hp for u in side_state.units)
    if total_max <= 0:
        return 0.0
    return sum(u.current_hp for u in side_state.units) / total_max


def _energy_ratio(side_state, rules: BattleRules) -> float:
    """能量比例：`Σ energy / (精灵数 × energy_max)`（0..1，全队口径）。"""
    denom = len(side_state.units) * rules.energy_max
    if denom <= 0:
        return 0.0
    return sum(u.energy for u in side_state.units) / denom


def v_heuristic(state, side: str) -> float:
    """启发式价值：`0.6·命数差 + 0.3·血量比差 + 0.1·能量差`（§3.3 档位 A-0）。

    - 命数差 = my.lives − foe.lives（**原始整数**，主导信号，见 §3.3 权重）；
    - 血量比差 / 能量比差 = 双方全队比例差，均归一化到 [−1, 1]。
    输入 `state`（引擎完整状态，非迷雾 view）——价值估计是离线分析/信度分配用，
    不需要也不该被迷雾遮蔽（RL 价值函数本来就建立在完整状态上）。
    确定性、可单测；`v_heuristic(state, "a") == −v_heuristic(state, "b")`（严格反对称）。
    """
    my, foe = state.side(side), state.foe(side)
    lives_diff = my.lives - foe.lives
    hp_diff = _hp_ratio(my) - _hp_ratio(foe)
    energy_diff = _energy_ratio(my, state.rules) - _energy_ratio(foe, state.rules)
    return 0.6 * lives_diff + 0.3 * hp_diff + 0.1 * energy_diff


# ---------------------------------------------------------------------------
# ko_thresholds（R6 构筑剪枝 / 伤害线参考，纯引擎穷举）
# ---------------------------------------------------------------------------


def hits_to_ko(attacker: Unit, defender: Unit, skill, *,
               rules: BattleRules = DEFAULT_RULES, cap: int = KO_HITS_CAP) -> int | None:
    """攻击技能对防守单位的击杀所需连续命中次数；`cap` 内打不死 → None。

    假设：无增益、无回复、无连击变化（裸单位间的伤害**每击恒定**），故
    `hits = ceil(defender.max_hp / dmg)`，一次 compute_damage 即够（克制/STAB 已计入）。
    `skill.power ≤ 0`（防御/状态技）→ None。
    """
    if skill.power <= 0:
        return None
    state_proxy = SimpleNamespace(rules=rules, weather=None, side=lambda _s: SimpleNamespace(units=()))
    eff = type_effectiveness(skill.type, defender.types)
    stab = stab_multiplier(skill.type, attacker.types)
    dmg = compute_damage(state_proxy, attacker, defender, skill, effectiveness=eff, stab=stab)
    if dmg <= 0:
        return None
    hits = (defender.max_hp + dmg - 1) // dmg  # ceil(max_hp / dmg)
    return hits if hits <= cap else None


def _unit_for(spirit_name: str, skills: list[str], rules: BattleRules) -> Unit:
    """按精灵名 + 技能名构造一个裸单位（无 IV、坦率性格、满血满能）——击杀线计算用。"""
    sp = load_spirits(DataSource.VALID)[spirit_name]
    return build_unit(
        {
            "name": sp.name,
            "types": list(sp.types),
            "stats": calc_combat_stats(sp.stats, {}, "坦率"),
            "skills": skills,
            "nature": "坦率",
            "bloodline": "",
            "iv": {},
            "trait": sp.trait_name,
        },
        rules,
    )


def _first_battle_ready(sp) -> str | None:
    """精灵任意技能池（默认/技能石/传说/血脉）里第一个 battle_ready 技能名；无 → None。

    build_unit 要求技能在可对战白名单内——没有 battle_ready 技能的精灵无法构造单位，
    不能上场，击杀线表里跳过（既当不了攻击者也当不了靶）。
    """
    for pool in (sp.skills_default, sp.skills_stone, sp.skills_legend, sp.skills_bloodline):
        for s in pool:
            if battle_ready(s):
                return s
    return None


@lru_cache(maxsize=8)
def _defenders(rules: BattleRules) -> dict[str, Unit]:
    """防御池：所有可构造单位的 VALID 精灵 → 裸单位（按 rules 缓存）。"""
    out: dict[str, Unit] = {}
    for name, sp in load_spirits(DataSource.VALID).items():
        first = _first_battle_ready(sp)
        if first is None:
            continue
        out[name] = _unit_for(name, [first], rules)
    return out


@lru_cache(maxsize=8)
def _ko_table(rules: BattleRules) -> dict:
    """击杀线表主体（lru_cache：BattleRules 是 frozen dataclass，可作缓存键）。

    攻击者 = 跨家族候选池（valid_spirit_candidates，每家族一只、可上场），技能 = 其
    battle_ready 默认攻击技；防御者 = 全部可构造 VALID 精灵。只记录 1/2 击击杀线。
    """
    spirits = load_spirits(DataSource.VALID)
    raw_skills = load_skills(DataSource.VALID)
    defenders = _defenders(rules)
    table: dict = {}
    for att_name in valid_spirit_candidates():
        sp = spirits[att_name]
        atk_skills = [s for s in sp.skills_default
                      if battle_ready(s) and raw_skills[s].kind in ("物攻", "魔攻")
                      and raw_skills[s].power > 0]
        if not atk_skills:
            continue
        row: dict = {}
        for sname in atk_skills:
            attacker = _unit_for(att_name, [sname], rules)
            skill = attacker.skills[0]
            sub: dict = {}
            for def_name, defender in defenders.items():
                h = hits_to_ko(attacker, defender, skill, rules=rules)
                if h is not None:
                    sub[def_name] = h
            if sub:
                row[sname] = sub
        if row:
            table[att_name] = row
    return table


def ko_thresholds(rules: BattleRules = DEFAULT_RULES) -> dict:
    """每攻击技能对每防御精灵的击杀线（R0 交付）：`{攻击精灵: {技能: {防御精灵: 1|2}}}`。

    值 1 = 一击必杀，2 = 两回合击杀；超过 2 击或打不死 → 该键不存在。纯引擎穷举
    （compute_damage + 克制/STAB），确定性、可复现，同 `rules` 幂等（lru_cache）。
    覆盖范围 = 跨家族候选攻击者（valid_spirit_candidates）→ 全部可构造防御精灵。
    **返回逐层拷贝**——lru_cache 持有的是共享可变表，直接返回会让调用方
    的改动污染全局缓存（R6 剪枝会注解这张表）。
    """
    table = _ko_table(rules)
    return {att: {s: dict(sub) for s, sub in row.items()} for att, row in table.items()}
