"""组队：把玩家意图（`TeamPick`）校验通过后变成引擎唯一认识的 roster spec。

**血脉在 E0 的语义（说清楚，不假装实现）**：E0 的 14 个技能全是「普通」系，而克制表
要到 E2 才存在，所以血脉在 E0 **对战斗没有任何影响**。它做三件真实的事：
① 被校验（必须是该精灵的合法血脉）；② 拓宽可学技能池；③ 进 roster 与
`--team-report` 输出。E2 起它改写 `Unit.types`，从而通过克制表影响伤害。

判断 3（来自环境计划）：同名精灵**允许**重复入队——E0 只有 6 只精灵、3 个槽位，
禁止重复会让组队空间小到无趣。因此本模块**不**做重复校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import STAT_KEYS, load_skills, load_spirits
from .rules import DEFAULT_RULES, E0_ITEMS, BattleRules
from .statline import calc_combat_stats, is_valid_nature


@dataclass(frozen=True)
class TeamPick:
    """一只精灵的组队意图。这是**玩家侧**的输入形状。"""

    spirit: str                       # 精灵名，必须在 e0_spirits.json 里
    skills: list[str]                 # 1–rules.skill_slots 个（至少 1、至多 3），都在该精灵可学池内
    bloodline: str = ""               # 血脉（系别）；见模块 docstring 的语义说明
    nature: str = "坦率"
    iv: dict[str, int] = field(default_factory=dict)   # 每项 0–iv_max，最多 3 个维度有投入，缺省 0


def learnable_skills(spirit: str, bloodline: str = "") -> list[str]:
    """该精灵的可学技能池 = `skills.默认` ∪（选了**合法**血脉才并上 `skills.血脉`）。

    非法的血脉选择不给任何好处——`validate_team` 会单独报「血脉不在合法列表」，这里
    只把它当成"没选血脉"处理，于是非法血脉偷渡血脉技能的事在结构上不可能发生。
    精灵名不存在 → 直接 KeyError（`validate_team` 会先拦住精灵名，这里不兜）。
    """
    sp = load_spirits()[spirit]
    pool = list(sp.skills_default)
    if bloodline and bloodline in sp.bloodlines:
        pool.extend(sp.skills_bloodline)
    return pool


def validate_team(picks: list[TeamPick], items: list[str],
                  rules: BattleRules = DEFAULT_RULES) -> list[str]:
    """返回全部错误（中文），空列表 = 合法。

    **一次报全部错误，不是遇到第一个就返回**——组队是人在填表，一次看清所有问题
    比来回试八次强。

    校验项：队伍规模 == rules.team_size；每只技能数 1–rules.skill_slots（至少 1、至多 3）；
    技能不在可学池；技能名不存在；精灵名不存在；血脉不在该精灵的血脉列表；
    性格未知；个体值键不是六维之一 / 值越界（0–iv_max）/ 有投入的维度 > 3；
    道具名不存在 / 道具重复。
    """
    errors: list[str] = []
    spirits = load_spirits()
    skills = load_skills()

    if len(picks) != rules.team_size:
        errors.append(f"队伍规模必须为 {rules.team_size} 只，实际 {len(picks)} 只。")

    for i, pick in enumerate(picks, start=1):
        label = f"第{i}只（{pick.spirit or '<未选精灵>'}）"
        if pick.spirit not in spirits:
            errors.append(f"{label}：精灵「{pick.spirit}」不存在。")
            continue  # 精灵都不存在，关于它的其余校验无意义

        sp = spirits[pick.spirit]

        if pick.bloodline and pick.bloodline not in sp.bloodlines:
            errors.append(
                f"{label}：血脉「{pick.bloodline}」不在「{pick.spirit}」的合法血脉列表 "
                f"{list(sp.bloodlines)} 内。"
            )

        if not 1 <= len(pick.skills) <= rules.skill_slots:
            errors.append(
                f"{label}：技能数必须为 1–{rules.skill_slots} 个，实际 {len(pick.skills)} 个。"
            )
        pool = learnable_skills(pick.spirit, pick.bloodline)
        for skill_name in pick.skills:
            if skill_name not in skills:
                errors.append(f"{label}：技能「{skill_name}」不存在。")
            elif skill_name not in pool:
                errors.append(
                    f"{label}：技能「{skill_name}」不在「{pick.spirit}」"
                    f"（血脉「{pick.bloodline or '无'}」）的可学池内。"
                )

        if not is_valid_nature(pick.nature):
            errors.append(f"{label}：性格「{pick.nature}」未知。")

        ev_dims: list[str] = []
        for key, val in pick.iv.items():
            if key not in STAT_KEYS:
                errors.append(f"{label}：个体值键「{key}」不是六维之一。")
            elif isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= rules.iv_max:
                errors.append(f"{label}：个体值 {key}={val!r} 越界（须为 0–{rules.iv_max} 的整数）。")
            elif val > 0:
                ev_dims.append(key)  # 只有合法正整数值才算「有投入的维度」
        if len(ev_dims) > 3:
            errors.append(
                f"{label}：最多 3 个维度可加个体值，实际 {len(ev_dims)} 个维度（{ev_dims}）。"
            )

    if len(set(items)) != len(items):
        errors.append(f"道具列表含重复项：{items}。")
    for item in items:
        if item not in E0_ITEMS:
            errors.append(f"道具「{item}」不存在。")

    return errors


def build_roster(picks: list[TeamPick]) -> list[dict]:
    """校验通过后产出 roster spec —— 引擎唯一认识的形状（E0b 的 build_unit 吃它）。

    E3 的真实数据加载器产出**同一个形状**，所以 E0b 的引擎测试到 E3 一条都不用改。
    这里做防御性再校验：不合法的意图在此抛 ValueError，而不是带病产出。

    roster spec 的形状：
        {"name": "迪莫",
         "types": ["光"],
         "stats": {...},      # 已经是 calc_combat_stats 的输出
         "skills": ["抓挠1", "加物攻"],
         "nature": "坦率", "bloodline": "", "iv": {}}
    """
    errors = validate_team(picks, items=[])
    if errors:
        raise ValueError("组队不合法：" + "；".join(errors))

    spirits = load_spirits()
    roster: list[dict] = []
    for pick in picks:
        sp = spirits[pick.spirit]
        roster.append(
            {
                "name": sp.name,
                "types": list(sp.types),
                "stats": calc_combat_stats(sp.stats, pick.iv, pick.nature),
                "skills": list(pick.skills),
                "nature": pick.nature,
                "bloodline": pick.bloodline,
                "iv": dict(pick.iv),
            }
        )
    return roster
