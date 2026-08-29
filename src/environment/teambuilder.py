"""组队：把玩家意图（`TeamPick`）校验通过后变成引擎唯一认识的 roster spec。

数据源：
- **FULL**（真实数据）：规则生效（负责人 2026-08-24 指定）——
  ① 同一家族只能入队一只（家族 = evolution 链首精灵的编号一致）；
  ② 血脉技能（`skills.血脉`）的系别必须等于玩家所选血脉系别（无血脉禁带血脉技能）；
  ③ 首领形态（`is_boss`）不可入队。
- **VALID**（E3）：精灵表同 FULL（全部 593 只），技能池只保留**已实装效果的** P1∪P2
  白名单（`valid_skills.json`）——三条规则同样生效，非白名单技能给「效果未实装」文案。

roster spec 是数据层与引擎之间唯一的一层缝，形状两源一致。

**系别与血脉的职责（负责人 2026-08-25 澄清）**：`types` 恒为精灵**自身系别**——它是克制/STAB
的依据；血脉系别**不改写** types，只决定可携带的血脉技能是哪个系（规则 2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import (
    DEFAULT_SOURCE, DataSource, STAT_KEYS, load_skills, load_spirits, load_types,
)
from .rules import DEFAULT_RULES, ITEMS, BattleRules
from .statline import calc_combat_stats, is_valid_nature


@dataclass(frozen=True)
class TeamPick:
    """一只精灵的组队意图。这是**玩家侧**的输入形状。"""

    spirit: str                       # 精灵名，必须在数据表里（FULL 真实）
    skills: list[str]                 # 1–rules.skill_slots 个（至少 1、至多 4），都在该精灵可学池内
    bloodline: str = ""               # 血脉（系别）；FULL/VALID 下由玩家自定义，任意 18 系
    nature: str = "坦率"
    iv: dict[str, int] = field(default_factory=dict)   # 每项 0–iv_max，最多 3 个维度有投入，缺省 0


def learnable_skills(spirit: str, bloodline: str = "",
                     source: DataSource = DEFAULT_SOURCE) -> list[str]:
    """该精灵的可学技能池。

    FULL：`默认 ∪ 技能石 ∪ 传说` +（选了血脉则并上 `skills.血脉` 里**系别 == 血脉**
    的技能；无血脉 → 不带任何血脉技能）。
    VALID：FULL 池再 ∩ **已实装效果**的技能（只保留可对战的）。
    精灵名不存在 → 直接 KeyError（`validate_team` 会先拦住精灵名，这里不兜）。
    """
    sp = load_spirits(source)[spirit]
    pool = list(sp.skills_default) + list(sp.skills_stone) + list(sp.skills_legend)
    if bloodline:
        skills = load_skills(source)
        pool += [n for n in sp.skills_bloodline if n in skills and skills[n].type == bloodline]
    if source is DataSource.VALID:
        valid = set(load_skills(DataSource.VALID))
        pool = [n for n in pool if n in valid]
    return pool


def validate_team(picks: list[TeamPick], items: list[str],
                  rules: BattleRules = DEFAULT_RULES,
                  source: DataSource = DEFAULT_SOURCE) -> list[str]:
    """返回全部错误（中文），空列表 = 合法。

    **一次报全部错误，不是遇到第一个就返回**——组队是人在填表，一次看清所有问题
    比来回试八次强。

    公共校验：队伍规模 == rules.team_size；每只技能数 1–rules.skill_slots；
    技能不存在 / 不在可学池；精灵名不存在；性格未知；个体值键/值/维度数；
    道具名不存在 / 道具重复。
    公共校验：队伍规模 == rules.team_size；每只技能数 1–rules.skill_slots；
    技能不存在 / 不在可学池；精灵名不存在；性格未知；个体值键/值/维度数；
    道具名不存在 / 道具重复。
    FULL/VALID 规则：首领形态不可入队；血脉技能系别必须等于所选血脉（含无血脉禁带）；
    同一家族（evolution 链首编号一致）只能入队一只。VALID 另：非白名单技能给「效果未实装」文案。
    """
    errors: list[str] = []
    spirits = load_spirits(source)
    skills = load_skills(source)

    if len(picks) != rules.team_size:
        errors.append(f"队伍规模必须为 {rules.team_size} 只，实际 {len(picks)} 只。")

    for i, pick in enumerate(picks, start=1):
        label = f"第{i}只（{pick.spirit or '<未选精灵>'}）"
        if pick.spirit not in spirits:
            errors.append(f"{label}：精灵「{pick.spirit}」不存在。")
            continue  # 精灵都不存在，关于它的其余校验无意义

        sp = spirits[pick.spirit]

        # ── 血脉：合法系别（玩家自定义 18 系任一）──
        if pick.bloodline and pick.bloodline not in load_types(source):
            errors.append(f"{label}：血脉「{pick.bloodline}」不是合法系别"
                          f"（{sorted(load_types(source))}）。")

        # ── 首领形态不可入队 ──
        if sp.is_boss:
            errors.append(f"{label}：首领形态不可入队。")

        # ── 技能：数量 + 可学池（FULL 对血脉技能走规则 2 的更明确文案）──
        if not 1 <= len(pick.skills) <= rules.skill_slots:
            errors.append(
                f"{label}：技能数必须为 1–{rules.skill_slots} 个，实际 {len(pick.skills)} 个。"
            )
        pool = learnable_skills(pick.spirit, pick.bloodline, source)
        for skill_name in pick.skills:
            if skill_name not in skills:
                if skill_name in load_skills(DataSource.FULL):
                    errors.append(
                        f"{label}：技能「{skill_name}」效果未实装（P1∪P2 白名单外），当前不可携带。"
                    )
                else:
                    errors.append(f"{label}：技能「{skill_name}」不存在。")
                continue
            if skill_name in sp.skills_bloodline:
                # 规则 2：血脉技能必须匹配所选血脉系别（优先于通用「不在可学池」）
                if not pick.bloodline:
                    errors.append(f"{label}：血脉技能「{skill_name}」需要先选择血脉系别。")
                elif pick.bloodline in load_types(source) and skills[skill_name].type != pick.bloodline:
                    errors.append(
                        f"{label}：血脉技能「{skill_name}」系别为「{skills[skill_name].type}」，"
                        f"与所选血脉「{pick.bloodline}」不符。"
                    )
                continue  # 匹配的血脉技能已在池内；不匹配/无血脉的已由规则 2 报掉
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

    # ── 同一家族只能入队一只 ──
    fam: dict[str | None, list[str]] = {}
    for i, pick in enumerate(picks, start=1):
        key = spirits[pick.spirit].family_key if pick.spirit in spirits else None
        if key is not None:
            fam.setdefault(key, []).append(f"第{i}只「{pick.spirit}」")
    for key, holders in fam.items():
        if len(holders) > 1:
            errors.append(
                f"同一家族只能入队一只：{'、'.join(holders)} 同属一个家族"
                f"（进化链最低阶编号 {key}）。"
            )

    if len(set(items)) != len(items):
        errors.append(f"道具列表含重复项：{items}。")
    for item in items:
        if item not in ITEMS:
            errors.append(f"道具「{item}」不存在。")

    return errors


def build_roster(picks: list[TeamPick], source: DataSource = DEFAULT_SOURCE,
                 rules: BattleRules = DEFAULT_RULES) -> list[dict]:
    """校验通过后产出 roster spec —— 引擎唯一认识的形状（build_unit 吃它）。

    真实数据加载器产出**同一个形状**，引擎测试不依赖数据源。
    这里做防御性再校验：不合法的意图在此抛 ValueError，而不是带病产出。

    `rules`：管理员（E3 battle_config）可传自定义 team_size/lives，build_roster 按它校验
    （默认 DEFAULT_RULES）。

    roster spec 的形状：
        {"name": "迪莫",
         "types": ["光"],
         "stats": {...},      # 已经是 calc_combat_stats 的输出
         "skills": ["闪光", "魔法增效"],
         "nature": "坦率", "bloodline": "", "iv": {},
         "trait": "最好的伙伴"}   # 特性名（build_unit 据此绑定 TraitState）
    """
    errors = validate_team(picks, items=[], rules=rules, source=source)
    if errors:
        raise ValueError("组队不合法：" + "；".join(errors))

    spirits = load_spirits(source)
    roster: list[dict] = []
    for pick in picks:
        sp = spirits[pick.spirit]
        roster.append(
            {
                "name": sp.name,
                # 系别**恒为精灵自身系别**（影响克制/STAB）——血脉系别不改写 types，
                # 它只决定可携带的血脉技能是哪个系（规则 2 校验，见 validate_team）。
                "types": list(sp.types),
                "stats": calc_combat_stats(sp.stats, pick.iv, pick.nature),
                "skills": list(pick.skills),
                "nature": pick.nature,
                "bloodline": pick.bloodline,
                "iv": dict(pick.iv),
                "trait": sp.trait_name,
            }
        )
    return roster
