"""技能效果表（`SkillEffect`）+ **P1/P2 效果编译器**（batch-P1 125 / batch-P2 技能）。

引擎永不读 `desc`。两条效果来源：
- `P1_EFFECTS`：由 `compile_p1_effect` 从 P1 批次技能 desc 的**固定模式**编译生成——
  纯伤害 / 纯防御 / 纯六维状态（P1 全部 125 条都应命中）。
- `P2_EFFECTS`：P2 扩展（连击/先手/吸血/能量/每连击状态…）。
`battle_ready(name)` = P1 ∪ P2（可对战白名单）。

这是「不做 DSL 编译器」的代价与边界：P1/P2 用固定模式编译，
553 条时的任意 desc 仍不支持（`compile_effect` 返回 None，battle_ready=False）。
效果参数**只**从效果表读——引擎里出现正则就是设计事故（参考项目的
`re.search(r"(\\d+)%")` 写进了 engine.py，减伤比例从 power 反推）。

三条从 `mydocs/E0_skills.json` 读出来、必须落进代码的事实：
- **抓挠（基础款）没有应对子句，撞击（基础款）有。** 所以效果表必须逐技能手写，
  不能按 `kind` 推。
- **加速度是扁平 +80，其余四个状态技能是百分比。** 所以 `mode` 从第一天就区分
  `pct` / `flat`。
- **撞击2 能耗 3，而抓挠2 能耗 4。** 数据如此，不要"顺手修正"——那是 E0_skills.json
  里的事，不在本表（本表不含 power / energy_cost）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .dataset import DataSource, RawSkill, load_skills
from .statuses import STATUS_TABLE, status_kwargs


class SkillCategory(str, Enum):
    """技能三类。**应对判定只看类别**，不看 kind。"""

    ATTACK = "攻击"
    DEFENSE = "防御"
    STATUS = "状态"


KIND_TO_CATEGORY: dict[str, SkillCategory] = {
    "物攻": SkillCategory.ATTACK,
    "魔攻": SkillCategory.ATTACK,
    "防御": SkillCategory.DEFENSE,
    "状态": SkillCategory.STATUS,
}


@dataclass(frozen=True)
class SkillStatEffect:
    """状态系一条目标效果（P1/P2）：目标 + 维度 + 模式 + 有符号层数。

    层数沿用「1 层 = 10%(pct) 或 +10(flat)」记账单位；负层 = 减益。
    `stat` 除六维外还可为特殊维度：
      - "combo"：连击数buff（flat 1 层 = +1 连击；pct 1 层 = +10%）
      - "lifesteal"：吸血buff（flat 1 层 = +100% 吸血）
      - "energy_cost"：全技能能耗（正值 = 能耗+N → 内部 EnergyCostMod 层 -N）
      - 纯负面中文名（中毒/灼烧/寄生/冻结/引电/萌化）：mode = dot/special，
        kwargs 从 statuses.STATUS_TABLE 取（DOT 批 2026-08-30）
    """

    target: str        # "self" | "foe"
    stat: str          # atk/sp_atk/def/sp_def/speed/combo/lifesteal/energy_cost/中毒/…
    mode: str          # "pct" | "flat" | "dot" | "special"
    layers: int        # 有符号（-6 = -60% 等）
    kwargs: dict = field(default_factory=dict)   # 纯负面 buff 扩展参数


@dataclass(frozen=True)
class SkillMarkEffect:
    """状态/防御系一条印记效果（印记/天气批 2026-08-30）：目标 + 印记名 + 层数。

    印记名在编译时查 `marks.MARK_CATALOG`（未知名 → 不编译）。
    """

    target: str        # "self" | "foe"
    name: str          # 印记名（MARK_CATALOG 键）
    layers: int


@dataclass(frozen=True)
class SkillEffect:
    """一个技能的全部结构化效果。引擎永不读 `desc`——desc 只用于展示与将来的提示词。

    注意防御系的减伤**本身就是应对效果**：`应对攻击时，减伤70%` 意味着对手没出攻击
    就什么也不发生。所以 `reduction_pct` 只在 `counter_vs` 命中时生效。

    P2 连击/先手/资源扩展：
      - `hits`：基础连击数（显式「N连击」；默认 1）。
      - `combo_eligible`：是否受连击数buff加成——**带有连击描述的技能**才为 True
        （负责人规则：常规连击数buff 1 层 = +1 连击；特性连击 buff 以特性描述为准，P2 无）。
      - `priority`：先手修正（先手+N → 出手优先级 +N，常规技能为 0；同优先级比速度）。
      - `self_energy_gain`/`energy_gain`：自己回复 N 能量（伤害后 / 状态后）。
      - `heal_pct_self`：自己回复 X% 生命。
      - `lifesteal_pct`：本次伤害吸血 X%。
      - `steal_energy`：偷取敌方 N 能量。
      - `bench_energy_gain`：场下（自己队伍后备）每只回复 N 能量。
      - `buff_effects`：一次性目标效果（连击数/吸血/能耗 buff 或减益）。
    `stat_effects`（P1/P2）：状态系每连击应用的目标效果列表；为空则走 E0 的
    单 `stat/mode/layers` 旧字段（教学技能）。
    """

    category: SkillCategory
    hits: int = 1
    combo_eligible: bool = False          # 受连击数buff加成（显式「N连击」描述）
    combo_per_team_skill: str = ""        # 虫鸣类：队伍中每携带 1 个该技能，基础连击 +1
    priority: int = 0                     # 先手修正（先手+N）
    self_energy_gain: int = 0             # 伤害后自己回复 N 能量
    energy_gain: int = 0                  # 状态后自己回复 N 能量
    heal_pct_self: int = 0                # 自己回复 X% 生命
    lifesteal_pct: int = 0                # 本次伤害吸血 X%
    steal_energy: int = 0                 # 偷取敌方 N 能量
    bench_energy_gain: int = 0            # 场下每只回复 N 能量
    energy_foe_cost_ratio: float = 0.0    # 雾气环绕：回复 = 敌方当前在场精灵全部技能能耗 × 该比例（0.5）
    reduction_pct: float = 0.0            # 防御系：应对命中时的减伤比例
    stat: str = ""                        # 状态系：作用于哪个属性（旧单条路径）
    mode: str = ""                        # "pct"（每层 10%）| "flat"（每层 +10）
    layers: int = 0                       # 基础层数
    counter_vs: SkillCategory | None = None  # 应对哪一类
    counter_damage_mult: float = 1.0         # 攻击系应对成功时的伤害乘子
    counter_extra_layers: int = 0            # 状态系应对成功时的额外层数
    stat_effects: tuple[SkillStatEffect, ...] = ()   # 状态系每连击应用
    buff_effects: tuple[SkillStatEffect, ...] = ()   # 一次性目标效果（连击/吸血/能耗）
    # 印记/天气批（2026-08-30）
    mark_effects: tuple[SkillMarkEffect, ...] = ()          # 状态系每连击应用（普通施加以 hits=1）
    counter_mark_effects: tuple[SkillMarkEffect, ...] = ()  # 防御系应对命中时施加
    set_weather: str = ""       # 天气名（雨天/沙暴/暴风雪/雷鸣；"" = 无）
    weather_turns: int = 0      # 天气持续回合
    # 冻结批（2026-08-30）
    freeze_power_per_layer: int = 0         # 碎冰冰：敌方每层冻结 → 本次威力 +N
    freeze_energy_gain_per_layer: int = 0   # 冷凝：敌方每层冻结 → 自己回 N 能量
    freeze_cost_per_foe_layer: int = 0      # 霜天：敌方每层冻结 → 敌方全技能能耗 +N（读钩子）
    counter_status_effects: tuple[SkillStatEffect, ...] = ()   # 应对命中施状态（冰墙/冰点）
    freeze_power_if_frozen: int = 0         # 极寒领域：敌方有冻结 → 本次威力 +N
    freeze_double_on_counter: bool = False  # 极寒领域：应对状态 → 敌方冻结层翻倍


def category_of(skill) -> SkillCategory:
    """按 kind 推类别。效果表的键集合 == 技能表的键集合由测试钉死（双向包含）。"""
    return KIND_TO_CATEGORY[skill.kind]


# ── P1 效果编译器（batch-P1.json：纯伤害 / 纯防御 / 纯六维状态）──────────────────
P1_SKILLS_FILE = Path(__file__).resolve().parent / "data" / "p1_skills.json"

# 属性名 → 英文 key。双攻/双防是复合（_expand_stats 里展开）。
_STAT_NAMES: dict[str, str] = {
    "物攻": "atk",
    "魔攻": "sp_atk",
    "物防": "def",
    "魔防": "sp_def",
    "速度": "speed",
}


def _expand_stats(text: str) -> tuple[str, ...]:
    """属性名序列 → 英文 key 元组。容忍「物攻和魔攻」「魔攻魔防」「双攻」等写法。

    先把 双攻/双防 展开，剥掉 和/，再贪心按 2 字符统计名扫描。
    """
    text = text.replace("双攻", "物攻魔攻").replace("双防", "物防魔防")
    text = text.replace("和", "").replace("，", "").replace("、", "").replace(" ", "")
    out: list[str] = []
    i = 0
    while i < len(text):
        two = text[i:i + 2]
        if two in _STAT_NAMES:
            out.append(_STAT_NAMES[two])
            i += 2
        else:
            raise ValueError(f"P1/P2 状态 desc 里未知属性名「{text[i:]}」。")
    return tuple(out)


def _stat_effect_from_value(stat: str, target: str, value: str) -> SkillStatEffect:
    """`+140%` → pct +14 层；`-30`（无%）→ flat -3 层。P1/P2 数值全是 10 的倍数。"""
    m = re.fullmatch(r"([+-])(\d+)(%?)", value)
    if m is None:
        raise ValueError(f"非法数值 token「{value}」。")
    sign = 1 if m.group(1) == "+" else -1
    num = int(m.group(2))
    if num % 10:
        raise ValueError(f"数值「{value}」不是 10 的倍数，无法换算成层数。")
    mode = "pct" if m.group(3) == "%" else "flat"
    return SkillStatEffect(target=target, stat=stat, mode=mode, layers=sign * num // 10)


def _parse_stat_specs(content: str, target: str) -> list[SkillStatEffect]:
    """解析 desc 去掉前缀后的部分，如「物攻和魔攻+140%」「物防+140%和速度-30」「魔攻魔防+10%，速度+10」。

    以值 token（`[+-]\\d+%?`）为界：值之前的文字是该规格的统计名（可 和/，连接），
    每个统计名 × 该值生成一条 SkillStatEffect。
    """
    effects: list[SkillStatEffect] = []
    pos = 0
    for m in re.finditer(r"[+-]\d+%?", content):
        stats_text = content[pos:m.start()].strip("和，、 ")
        for stat in _expand_stats(stats_text):
            effects.append(_stat_effect_from_value(stat, target, m.group()))
        pos = m.end()
    return effects


def compile_p1_effect(skill: RawSkill) -> SkillEffect | None:
    """P1 效果编译器：识别固定模式 → SkillEffect；未命中 → None（未支持）。

    三个模式（P1 全部技能都应命中其一）：
      - 纯伤害：desc 恰为「对敌方精灵造成物理/魔法伤害。」
      - 纯防御：「减伤X%，应对攻击」
      - 纯六维状态：「自己获得 / 敌方获得 …」（多目标/多维度经 stat_effects）
    desc 推得的类别与 kind 矛盾（数据异常）→ None，不编译。
    """
    desc = skill.desc.strip().rstrip("。").strip()
    if desc in ("对敌方精灵造成物理伤害", "对敌方精灵造成魔法伤害"):
        effect = SkillEffect(category=SkillCategory.ATTACK)
    elif re.fullmatch(r"减伤\d+%，应对攻击", desc) is not None:
        m = re.fullmatch(r"减伤(\d+)%，应对攻击", desc)
        effect = SkillEffect(category=SkillCategory.DEFENSE, counter_vs=SkillCategory.ATTACK,
                             reduction_pct=int(m.group(1)) / 100)
    elif desc.startswith("自己获得") or desc.startswith("敌方获得"):
        target = "self" if desc.startswith("自己获得") else "foe"
        content = desc[len("自己获得"):] if target == "self" else desc[len("敌方获得"):]
        stat_effects = _parse_stat_specs(content, target)
        if not stat_effects:
            return None
        effect = SkillEffect(category=SkillCategory.STATUS, stat_effects=tuple(stat_effects))
    else:
        return None
    expected = KIND_TO_CATEGORY.get(skill.kind)
    if expected is not None and expected != effect.category:
        return None   # desc 与 kind 矛盾：不编译（数据异常）
    return effect


# ── P2 效果编译器（连击 / 先手 / 吸血 / 能量 / 场下 / 每连击状态）─────────────────
_DMG = r"(?:物伤|魔伤|物理伤害|魔法伤害)"


def _finalize(effect: SkillEffect, skill: RawSkill) -> SkillEffect | None:
    """desc 推得的类别与 kind 矛盾（数据异常）→ None，不编译。"""
    expected = KIND_TO_CATEGORY.get(skill.kind)
    if expected is not None and expected != effect.category:
        return None
    return effect


def _attack(skill: RawSkill, **kw) -> SkillEffect | None:
    return _finalize(SkillEffect(category=SkillCategory.ATTACK, **kw), skill)


def _status(skill: RawSkill, **kw) -> SkillEffect | None:
    return _finalize(SkillEffect(category=SkillCategory.STATUS, **kw), skill)


def _compile_combo_effect(skill: RawSkill, desc: str) -> SkillEffect | None:
    """处理所有含「连击」描述的技能：连击伤害 / 连击buff / 每连击状态 / 虫鸣；歧义 → None。"""
    # 连击 + 印记（印记/天气批 2026-08-30）：N连击，每次连击使?敌方获得M层X印记（星链）
    m = re.fullmatch(r"(\d+)连击，每次连击使?敌方获得(\d+)层(.+印记)", desc)
    if m is not None:
        mark = m.group(3)
        if mark not in _mark_names():
            return None
        return _status(skill, hits=int(m.group(1)), combo_eligible=True,
                       mark_effects=(SkillMarkEffect("foe", mark, int(m.group(2))),))
    # 连击 + 状态（DOT 批 2026-08-30）：N连击，每次连击(使?敌方)获得M层X
    m = re.fullmatch(r"(\d+)连击，每次连击使?敌方获得(\d+)层(中毒|灼烧|寄生|冻结|引电|萌化)", desc)
    if m is not None:
        return _status(skill, hits=int(m.group(1)), combo_eligible=True,
                       stat_effects=tuple(_status_effect("foe", m.group(3), int(m.group(2)))))
    # 连击伤害 + 状态（DOT 批）：造成X，N连击，每次连击使?敌方获得M层X（易燃物质/连续毒针）
    m = re.fullmatch(rf"造成{_DMG}，(\d+)连击，每次连击使?敌方获得(\d+)层(中毒|灼烧|寄生|冻结|引电|萌化)", desc)
    if m is not None:
        return _attack(skill, hits=int(m.group(1)), combo_eligible=True,
                       stat_effects=tuple(_status_effect("foe", m.group(3), int(m.group(2)))))
    # 虫鸣：队伍中每携带 1 个 X，本次技能连击数 +1
    m = re.fullmatch(rf"造成{_DMG}，队伍中的精灵每携带1个(.+?)，本次技能连击数\+1", desc)
    if m is not None:
        return _attack(skill, hits=1, combo_eligible=True, combo_per_team_skill=m.group(1))
    # 连击伤害：造成X，N连击
    m = re.fullmatch(rf"造成{_DMG}，(\d+)连击", desc)
    if m is not None:
        return _attack(skill, hits=int(m.group(1)), combo_eligible=True)
    # 伤害 + 敌方连击数-N
    m = re.fullmatch(rf"造成{_DMG}，敌方获得连击数([+-]\d+)", desc)
    if m is not None:
        return _attack(skill, buff_effects=(SkillStatEffect("foe", "combo", "flat", int(m.group(1))),))
    # 状态：N连击，每次连击X（花炮 / 冰捆缚）
    m = re.fullmatch(r"(\d+)连击，每次连击(.+)", desc)
    if m is not None:
        hits, per = int(m.group(1)), m.group(2)
        if per.startswith("敌方获得全技能能耗"):
            mm = re.fullmatch(r"敌方获得全技能能耗([+-]\d+)", per)
            return _status(skill, hits=hits, combo_eligible=True,
                           stat_effects=(SkillStatEffect("foe", "energy_cost", "flat", int(mm.group(1))),))
        target = "self" if per.startswith("自己获得") else "foe"
        content = per[len("自己获得"):] if target == "self" else per[len("敌方获得"):]
        se_list = _parse_stat_specs(content, target)
        if not se_list:
            return None
        return _status(skill, hits=hits, combo_eligible=True, stat_effects=tuple(se_list))
    # 连击数buff：获得连击数+N%（暴风眼，无自己前缀）
    m = re.fullmatch(r"获得连击数([+-]\d+)%", desc)
    if m is not None:
        return _status(skill, buff_effects=(SkillStatEffect("self", "combo", "pct", int(m.group(1)) // 10),))
    # 连击数buff：自己获得连击数+N（热身运动）
    m = re.fullmatch(r"自己获得连击数([+-]\d+)", desc)
    if m is not None:
        return _status(skill, buff_effects=(SkillStatEffect("self", "combo", "flat", int(m.group(1))),))
    # 连击数buff：敌方获得连击数-N（耀眼）
    m = re.fullmatch(r"敌方获得连击数([+-]\d+)", desc)
    if m is not None:
        return _status(skill, buff_effects=(SkillStatEffect("foe", "combo", "flat", int(m.group(1))),))
    # 状态：自己获得X，N连击（三连破）——每连击应用 stat_effects，吃连击数buff（人工裁决 2026-08-25）
    m = re.fullmatch(r"自己获得(.+?)，(\d+)连击", desc)
    if m is not None:
        return _status(skill, hits=int(m.group(2)), combo_eligible=True,
                       stat_effects=tuple(_parse_stat_specs(m.group(1), "self")))
    # 状态：敌方获得X，N连击（电离爆破）——每连击应用，吃连击数buff（人工裁决 2026-08-25）
    m = re.fullmatch(r"敌方获得(.+?)，(\d+)连击", desc)
    if m is not None:
        return _status(skill, hits=int(m.group(2)), combo_eligible=True,
                       stat_effects=tuple(_parse_stat_specs(m.group(1), "foe")))
    return None


def compile_effect(skill: RawSkill) -> SkillEffect | None:
    """效果编译器（P1 ∪ P2）：识别固定模式 → SkillEffect；未命中 → None（未支持）。

    含「连击」的 desc 走 P2 连击路径（含歧义拦截）；其余先试 P2 非连击模式（先手 /
    吸血 / 能量 / 场下 / 每连击…），再兜底 P1 模式（纯伤害 / 纯防御 / 纯六维状态）。
    """
    desc = skill.desc.strip().rstrip("。").strip()
    if "连击" in desc:
        return _compile_combo_effect(skill, desc)

    # ── P2 非连击模式 ──
    # 伤害 + 先手
    m = re.fullmatch(rf"造成{_DMG}，先手([+-]\d+)", desc)
    if m is not None:
        return _attack(skill, priority=int(m.group(1)))
    # 伤害 + 吸血
    m = re.fullmatch(rf"造成{_DMG}，并吸血(\d+)%", desc)
    if m is not None:
        return _attack(skill, lifesteal_pct=int(m.group(1)))
    # 伤害 + 自己回复 N 能量
    m = re.fullmatch(rf"造成{_DMG}，自己回复(\d+)能量", desc)
    if m is not None:
        return _attack(skill, self_energy_gain=int(m.group(1)))
    # 伤害 + 自己回复 N% 生命
    m = re.fullmatch(rf"造成{_DMG}，自己回复(\d+)%生命", desc)
    if m is not None:
        return _attack(skill, heal_pct_self=int(m.group(1)))
    # 伤害 + 为场下所有精灵回复 N 能量
    m = re.fullmatch(rf"造成{_DMG}，为场下所有精灵回复(\d+)能量", desc)
    if m is not None:
        return _attack(skill, bench_energy_gain=int(m.group(1)))
    # 状态：自己回复 N 能量 和 M% 生命，并获得 X（缓一缓）
    m = re.fullmatch(r"自己回复(\d+)能量和(\d+)%生命，并获得(.+)", desc)
    if m is not None:
        return _status(skill, energy_gain=int(m.group(1)), heal_pct_self=int(m.group(2)),
                       stat_effects=tuple(_parse_stat_specs(m.group(3), "self")))
    # 状态：自己回复 N 能量，并获得 X（氧输送）
    m = re.fullmatch(r"自己回复(\d+)能量，并获得(.+)", desc)
    if m is not None:
        return _status(skill, energy_gain=int(m.group(1)),
                       stat_effects=tuple(_parse_stat_specs(m.group(2), "self")))
    # 状态：自己回复 N% 生命 和 M 能量（根吸收）
    m = re.fullmatch(r"自己回复(\d+)%生命和(\d+)能量", desc)
    if m is not None:
        return _status(skill, heal_pct_self=int(m.group(1)), energy_gain=int(m.group(2)))
    # 状态：自己回复 N 能量（徒长）
    m = re.fullmatch(r"自己回复(\d+)能量", desc)
    if m is not None:
        return _status(skill, energy_gain=int(m.group(1)))
    # 状态：自己回复 N% 生命（休息回复）
    m = re.fullmatch(r"自己回复(\d+)%生命", desc)
    if m is not None:
        return _status(skill, heal_pct_self=int(m.group(1)))
    # 状态：为场下每个精灵回复 N 能量（富养化）
    m = re.fullmatch(r"为场下每个精灵回复(\d+)能量", desc)
    if m is not None:
        return _status(skill, bench_energy_gain=int(m.group(1)))
    # 状态：自己获得 N% 吸血（贪婪；1 层 = 100%）
    m = re.fullmatch(r"自己获得(\d+)%吸血", desc)
    if m is not None:
        return _status(skill, buff_effects=(SkillStatEffect("self", "lifesteal", "flat", int(m.group(1)) // 100),))
    # 状态：偷取敌方 N 能量（勾魂）
    m = re.fullmatch(r"偷取敌方(\d+)能量", desc)
    if m is not None:
        return _status(skill, steal_energy=int(m.group(1)))
    # 状态：回复能量 = 敌方当前在场精灵全部技能能耗的一半（雾气环绕；人工裁决 2026-08-25）
    m = re.fullmatch(r"回复能量，回复值等于敌方技能总能耗的一半", desc)
    if m is not None:
        return _status(skill, energy_foe_cost_ratio=0.5)

    # ── 印记/天气模式（2026-08-30：施加类；驱散/偷取/转化/条件类不匹配 → 下批）──
    # 获得 N 层 X 印记（主场优势/棘刺/光合作用/打湿/蓄势待发/速冻/龙威/增程电池/…）
    m = re.fullmatch(r"(自己|敌方)获得(\d+)层(.+印记)", desc)
    if m is not None:
        mark = m.group(3)
        if mark not in _mark_names():
            return None
        return _status(skill, mark_effects=(SkillMarkEffect(
            "self" if m.group(1) == "自己" else "foe", mark, int(m.group(2))),))
    # 减伤 + 应对攻击施加印记（潮汐/冰蛋壳/委屈/冥想）
    m = re.fullmatch(r"减伤(\d+)%，应对攻击：(.+?)获得(\d+)层(.+印记)", desc)
    if m is not None:
        mark = m.group(4)
        if mark not in _mark_names():
            return None
        return _finalize(SkillEffect(
            category=SkillCategory.DEFENSE, counter_vs=SkillCategory.ATTACK,
            reduction_pct=int(m.group(1)) / 100,
            counter_mark_effects=(SkillMarkEffect(
                "self" if m.group(2) == "自己" else "foe", mark, int(m.group(3))),)),
            skill)
    # 将天气改为 X，持续 N 回合（落雨/沙涌/冬至/惊雷）
    m = re.fullmatch(r"将天气改为(雨天|沙暴|暴风雪|雷鸣)，持续(\d+)回合", desc)
    if m is not None:
        return _status(skill, set_weather=m.group(1), weather_turns=int(m.group(2)))

    # ── DOT 状态模式（2026-08-30：A 类施加；应对/条件/驱散/转化类不匹配 → 下批）──
    # 敌方获得 N 层 X（退化/孢子/引燃/霜降/毒孢子）
    m = re.fullmatch(r"敌方获得(\d+)层(中毒|灼烧|寄生|冻结|引电|萌化)", desc)
    if m is not None:
        return _status(skill, stat_effects=tuple(_status_effect("foe", m.group(2), int(m.group(1)))))
    # 造成(物|魔)伤，敌方获得 N 层 X（毒针/腐蚀酸液/烈焰风暴/花火/暴风雪/通电）
    m = re.fullmatch(rf"造成{_DMG}，敌方获得(\d+)层(中毒|灼烧|寄生|冻结|引电|萌化)", desc)
    if m is not None:
        return _attack(skill, stat_effects=tuple(_status_effect("foe", m.group(2), int(m.group(1)))))

    # ── 冻结批模式（2026-08-30：读钩子 / 应对施状态；寒潮含巧变不匹配）──
    # 造成(物|魔)伤，敌方每有1层冻结，本次技能威力+N（碎冰冰）
    m = re.fullmatch(rf"造成{_DMG}，敌方每有1层冻结，本次技能威力\+(\d+)", desc)
    if m is not None:
        return _attack(skill, freeze_power_per_layer=int(m.group(1)))
    # 造成(物|魔)伤，敌方每有1层冻结，自己回复N能量（冷凝）
    m = re.fullmatch(rf"造成{_DMG}，敌方每有1层冻结，自己回复(\d+)能量", desc)
    if m is not None:
        return _attack(skill, freeze_energy_gain_per_layer=int(m.group(1)))
    # 敌方获得N层冻结，且每有1层冻结获得全技能能耗+M（霜天）
    m = re.fullmatch(r"敌方获得(\d+)层冻结，且每有1层冻结获得全技能能耗\+(\d+)", desc)
    if m is not None:
        return _status(skill, stat_effects=tuple(_status_effect("foe", "冻结", int(m.group(1)))),
                       freeze_cost_per_foe_layer=int(m.group(2)))
    # 敌方获得N层冻结，应对防御：额外获得M层（冰点）
    m = re.fullmatch(r"敌方获得(\d+)层冻结，应对防御：额外获得(\d+)层", desc)
    if m is not None:
        return _status(skill, counter_vs=SkillCategory.DEFENSE,
                       stat_effects=tuple(_status_effect("foe", "冻结", int(m.group(1)))),
                       counter_status_effects=tuple(_status_effect("foe", "冻结", int(m.group(2)))))
    # 减伤N%，应对攻击：敌方获得M层冻结（冰墙）
    m = re.fullmatch(r"减伤(\d+)%，应对攻击：敌方获得(\d+)层冻结", desc)
    if m is not None:
        return _finalize(SkillEffect(
            category=SkillCategory.DEFENSE, counter_vs=SkillCategory.ATTACK,
            reduction_pct=int(m.group(1)) / 100,
            counter_status_effects=tuple(_status_effect("foe", "冻结", int(m.group(2))))),
            skill)
    # 造成(物|魔)伤，敌方获得N层冻结，应对状态：额外获得M层，本次技能威力翻倍（滚雪球）
    m = re.fullmatch(rf"造成{_DMG}，敌方获得(\d+)层冻结，应对状态：额外获得(\d+)层，本次技能威力翻倍", desc)
    if m is not None:
        return _attack(skill, counter_vs=SkillCategory.STATUS, counter_damage_mult=2.0,
                       stat_effects=tuple(_status_effect("foe", "冻结", int(m.group(1)))),
                       counter_status_effects=tuple(_status_effect("foe", "冻结", int(m.group(2)))))
    # 造成(物|魔)伤，若敌方有冻结，本次技能威力+N，应对状态：使冻结翻倍（极寒领域）
    m = re.fullmatch(rf"造成{_DMG}，若敌方有冻结，本次技能威力\+(\d+)，应对状态：使冻结翻倍", desc)
    if m is not None:
        return _attack(skill, counter_vs=SkillCategory.STATUS,
                       freeze_power_if_frozen=int(m.group(1)),
                       freeze_double_on_counter=True)

    # ── P1 兜底（纯伤害 / 纯防御 / 纯六维状态）──
    return compile_p1_effect(skill)


def _status_effect(target: str, name: str, layers: int) -> tuple[SkillStatEffect, ...]:
    """状态施加效果：mode/kwargs 从 statuses.STATUS_TABLE 取（单一事实源）。"""
    mode, _ = STATUS_TABLE[name]
    return (SkillStatEffect(target, name, mode, layers, kwargs=status_kwargs(name)),)


def _mark_names() -> frozenset[str]:
    """印记目录名集合（编译时校验：未知名印记 → 不编译）。"""
    from .marks import MARK_CATALOG

    return frozenset(MARK_CATALOG)


def _load_p1_names() -> frozenset[str]:
    """p1_skills.json（batch-P1 拷贝）的技能名集合——P1 批次的唯一锚点。"""
    raw = json.loads(P1_SKILLS_FILE.read_text(encoding="utf-8"))
    return frozenset(item["name"] for item in raw["skills"])


# P1/P2 效果表：对批次里每个技能（技能数据取 FULL 权威表）用 compile_effect 编译；
# P1 全命中 125、P2 命中 51（3 条歧义 desc 返回 None，测试钉死）。
P1_EFFECTS: dict[str, SkillEffect] = {}
for _name in sorted(_load_p1_names()):
    _skill = load_skills(DataSource.FULL).get(_name)
    if _skill is None:
        continue
    _effect = compile_effect(_skill)
    if _effect is not None:
        P1_EFFECTS[_name] = _effect

P2_SKILLS_FILE = Path(__file__).resolve().parent / "data" / "p2_skills.json"


def _load_p2_names() -> frozenset[str]:
    """p2_skills.json（batch-P2 拷贝）的技能名集合——P2 批次的唯一锚点。"""
    raw = json.loads(P2_SKILLS_FILE.read_text(encoding="utf-8"))
    return frozenset(item["name"] for item in raw["skills"])


P2_EFFECTS: dict[str, SkillEffect] = {}
for _name in sorted(_load_p2_names()):
    _skill = load_skills(DataSource.FULL).get(_name)
    if _skill is None:
        continue
    _effect = compile_effect(_skill)
    if _effect is not None:
        P2_EFFECTS[_name] = _effect


# 印记/天气白名单（2026-08-30）：扫描 FULL 表，编译命中印记/天气模式的技能。
# 施加类（获得N层印记 / 应对施印 / 连击施印 / 设置天气）；驱散/偷取/转化/条件类
# 不匹配任何模式 → 自动排除（下批）。FULL 表存在「模式形状但数值不可解析」的描述
# （如「自己获得全技能威力+10%」命中 P1 状态分支后 parse 失败）→ ValueError 视为
# 未命中；批次锚定表（P1/P2）的严格校验由各自测试钉死，不受此容错影响。
MW_EFFECTS: dict[str, SkillEffect] = {}
for _name, _skill in sorted(load_skills(DataSource.FULL).items()):
    try:
        _effect = compile_effect(_skill)
    except ValueError:
        _effect = None
    if _effect is not None and (_effect.mark_effects or _effect.counter_mark_effects
                                or _effect.set_weather):
        MW_EFFECTS[_name] = _effect


# DOT 状态白名单（2026-08-30）：扫描 FULL 表，编译命中且含状态类效果的技能。
# A 类施加（获得N层X / 伤害+获得N层X / 连击逐击）+ 冻结批（读冻结层 / 应对施状态）；
# 应对/条件/驱散/转化/巧变类不匹配 → 下批。
def _has_status_effects(effect: SkillEffect) -> bool:
    return any(se.stat in STATUS_TABLE for se in effect.stat_effects) \
        or any(se.stat in STATUS_TABLE for se in effect.buff_effects) \
        or any(se.stat in STATUS_TABLE for se in effect.counter_status_effects) \
        or effect.freeze_power_per_layer > 0 \
        or effect.freeze_energy_gain_per_layer > 0 \
        or effect.freeze_cost_per_foe_layer > 0 \
        or effect.freeze_power_if_frozen > 0 \
        or effect.freeze_double_on_counter


ST_EFFECTS: dict[str, SkillEffect] = {}
for _name, _skill in sorted(load_skills(DataSource.FULL).items()):
    try:
        _effect = compile_effect(_skill)
    except ValueError:
        _effect = None
    if _effect is not None and _has_status_effects(_effect):
        ST_EFFECTS[_name] = _effect


def battle_ready(name: str) -> bool:
    """可对战白名单：P1 效果表 ∪ P2 效果表 ∪ 印记/天气（MW）∪ DOT 状态（ST）。"""
    return name in P1_EFFECTS or name in P2_EFFECTS or name in MW_EFFECTS or name in ST_EFFECTS
