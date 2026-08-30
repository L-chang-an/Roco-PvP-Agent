"""特性静态目录：TraitDef + TRAIT_CATALOG + 白板特性 + 查找/解析。

特性是统一效果架构里的一个来源类型（与技能/印记并列）——携带一批 EffectBinding。
S1 只建目录骨架（空的）；S2 起按批次挂入具体特性（第一批：火花·助燃 / 喵喵·氧循环 /
水蓝蓝·浸润；迪莫「最好的伙伴」在 S3 挂入）。

**白板特性 `default`**：零绑定的占位特性。真实数据有 227 个唯一特性，绝大多数尚未实现——
这些精灵一律装备白板，于是**能正常上场对战，且对战斗过程零影响**。它是显式占位而不是
静默 no-op：`Unit.trait.name == "default"` 一眼可见「这只精灵的特性还没实现」，
覆盖率由 `--data-report --effects` 打印成可见数字。

数据源：`mydocs/spirits_details.json` 的 trait 字段（227 个唯一特性，按名，见 trait-batches/）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .effects import Effect, EffectBinding
from .hooks import Hook

DEFAULT_TRAIT_NAME = "default"   # 白板特性名（负责人指定）


@dataclass(frozen=True)
class TraitDef:
    """一个特性的静态定义。运行时实例是 models.TraitState（name 指向这里）。"""

    name: str
    bindings: tuple[EffectBinding, ...] = ()
    stackable: bool = True          # 多次触发是否叠层（不可叠加 → 同源刷新）
    once_per_battle: bool = False   # 每场战斗 1 次（如不死鸟保命）


# 唯一特性名 → TraitDef。`default` 是白板（零绑定，对战斗无任何影响）；
# 第一批三只精灵（S2）：助燃 / 氧循环 / 浸润 都是「使用了某系技能后」→ 挂在 SKILL_RESOLVE。
TRAIT_CATALOG: dict[str, TraitDef] = {
    # ── 白板：零绑定 → emit 遍历它时一个效果都不执行 ──
    DEFAULT_TRAIT_NAME: TraitDef(name=DEFAULT_TRAIT_NAME, bindings=()),
    "助燃": TraitDef(
        name="助燃",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="used_fire", effects=(
                # 使用火系技能后，双攻 +20%（每层 10%，可叠层）
                Effect("stat_mod", stat="atk", mode="pct", layers=2, trait=True, permanent=False),
                Effect("stat_mod", stat="sp_atk", mode="pct", layers=2, trait=True, permanent=False),
            )),
        ),
    ),
    "氧循环": TraitDef(
        name="氧循环",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="used_grass", effects=(
                # 使用草系技能后，回复 10% 生命（一次性，每次草系技能各触发一次）
                Effect("heal_pct", value=10),
            )),
        ),
    ),
    "浸润": TraitDef(
        name="浸润",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="used_water", effects=(
                # 使用水系技能后，全技能能耗 −1（层数 = 能耗修正值 -1，可叠层，非永久离场清除）
                Effect("energy_cost_mod", layers=-1, trait=True, permanent=False),
            )),
        ),
    ),
    # S3：迪莫「最好的伙伴」——造成克制伤害后，攻防速+20% 并回复 2 能量。
    # 攻防速 = 物攻/魔攻/物防/魔防/速度 五维（负责人确认）；非永久、可叠层；
    # 克制判定：克制系数 > 1（dealt_counter，见 engine.resolve_skill）。
    "最好的伙伴": TraitDef(
        name="最好的伙伴",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="dealt_counter", effects=(
                Effect("stat_mod", stat="atk", mode="pct", layers=2, trait=True, permanent=False),
                Effect("stat_mod", stat="sp_atk", mode="pct", layers=2, trait=True, permanent=False),
                Effect("stat_mod", stat="def", mode="pct", layers=2, trait=True, permanent=False),
                Effect("stat_mod", stat="sp_def", mode="pct", layers=2, trait=True, permanent=False),
                Effect("stat_mod", stat="speed", mode="pct", layers=2, trait=True, permanent=False),
                Effect("energy_gain", value=2),
            )),
        ),
    ),
    # S4：里拉鳐「吟游之弦」——赋予的印记不会替换其他印记（进 exclusive_marks 独立空间）。
    # 路由在 compiler._mark_space 读本名判定；此处注册名以阻止 resolve_trait_name
    # 落到 default 白板。零绑定（效果是印记施加时的路由规则，非事件反应）。
    "吟游之弦": TraitDef(name="吟游之弦", bindings=()),
    # 冻结批 L1（2026-08-30）：灵魂灼伤——冰系技能使敌方+4层灼烧，火系技能使敌方+2层冻结。
    # foe_status op（triggers._effect_to_atoms）：对敌方在场施状态（属性免疫同漏斗拦截）。
    "灵魂灼伤": TraitDef(
        name="灵魂灼伤",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="used_ice", effects=(
                Effect("foe_status", stat="灼烧", layers=4),
            )),
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="used_fire", effects=(
                Effect("foe_status", stat="冻结", layers=2),
            )),
        ),
    ),
}


def trait_implemented(name: str) -> bool:
    """该特性名是否有**真实效果**实现（白板 `default` 与空名都不算）。覆盖率报告用。"""
    return bool(name) and name != DEFAULT_TRAIT_NAME and name in TRAIT_CATALOG


def resolve_trait_name(name: str) -> str:
    """精灵表里的特性名 → **实际装备**的特性名。

    未实现（或空）→ 白板 `default`。这是唯一的解析点（`build_unit` 调它），
    于是「特性没实现」在战斗状态里是显式的 `default`，不是静默 no-op。
    """
    return name if trait_implemented(name) else DEFAULT_TRAIT_NAME


def trait_defs_for(unit) -> list[TraitDef]:
    """unit.trait → 静态定义列表。

    无特性实例（旧快照 trait=null）→ `[]`；目录里查不到的名字 → 白板（零绑定），
    于是引擎里**一个效果都不执行**，但不会因为脏名字炸掉。
    """
    if unit.trait is None:
        return []
    return [TRAIT_CATALOG.get(unit.trait.name, TRAIT_CATALOG[DEFAULT_TRAIT_NAME])]
