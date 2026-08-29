"""类型化 Effect（v3 骨架·声明层）：引擎要执行的一条具体指令（纯数据，不执行）。

对应 `mydocs/battle_docs.md` §3.1 —— Effect 是等待执行的数据；Reducer 是唯一执行者；
Trigger 只返回新 Effect。这里的 Atom（原子效果）是 SkillEffect（旧大字段袋）的
**类型化展开**：一个技能被 `compiler.compile_skill` 编译成有序 Atom 列表，每个 Atom
由 `reducer` 表执行并产出 DomainEvent + 展示事件。

**为何用对象引用而非 unit_id**：Atom 是**回合内局部对象**（不序列化、不进轨迹），
直接携带 Unit 对象引用最稳——手写测试单位无 id 也能工作，且无需 state.unit 反查。
副作用（伤害/回血/回能）仍由 Reducer 唯一执行（走 damage/primitives 漏斗）。

为什么类型化而不是继续用大字段袋：字段袋每加一种效果就在 engine 里多一个 if 分支
（`resolve_skill` 的 20 个效果字段就是这么膨胀的）；类型化后每种效果是一个独立
reducer，加效果 = 加一个 Atom 类型 + 一个 reducer，引擎零改动（v3 扩展铁律）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from .models import Unit


@dataclass(frozen=True)
class SpendEnergy:
    """扣能量（金额 = 技能实际能耗，含能耗减益；扣不扣得动由执行时校验）。"""

    unit: "Unit"
    amount: int        # 技能基础能耗（skill_energy_cost 会叠能耗减益）
    source: str


@dataclass(frozen=True)
class RevealSkill:
    """E4 迷雾揭示：技能确实释放 → 该方该精灵该技能对对手可见。"""

    unit: "Unit"
    side: str
    skill: str


@dataclass(frozen=True)
class DealDamage:
    """造成一次伤害（一个连击段）。每段一个 DealDamage，目标阵亡后剩余段自动跳过。"""

    side: str              # 发起方（a/b），事件归属用
    source: "Unit"
    target: "Unit"
    skill: str
    power: int
    skill_type: str
    damage_kind: str      # 物攻 / 魔攻
    hit: int = 1
    total_hits: int = 1
    counter_mult: float = 1.0      # 应对成功伤害乘子
    reduction: float = 0.0         # 防御方已武装减伤
    effectiveness: float = 1.0     # 克制倍率
    stab: float = 1.0              # 本系加成
    counter_cat: str = ""          # 应对命中的对手类别（damage 事件展示）
    acted_first: bool = False      # 本回合执行顺序先于对手（风起印记读钩子）


@dataclass(frozen=True)
class HealPct:
    """按 max_hp 的百分比回复。"""

    side: str
    unit: "Unit"
    pct: int           # 百分比整数（如 10 = 回复 10% max_hp）
    source: str


@dataclass(frozen=True)
class AddModifier:
    """追加一条属性/连击/吸血/能耗/纯负面 增减益层（写 stat_mods；能耗走 energy_cost 层）。

    `kwargs` 承载扩展参数（冻结 {pct:5}、引电 {pct:25, at:2} 等），随记录进 stat_mods。
    """

    side: str
    unit: "Unit"
    stat: str          # atk/sp_atk/def/sp_def/speed/combo/lifesteal/energy_cost/中毒/…/引电
    mode: str          # pct / flat / special
    layers: int
    source: str
    target: str = ""   # self / foe（展示用）
    counter_cat: str = ""   # 应对命中的对手类别（stat_change 事件展示）
    kwargs: dict = field(default_factory=dict)   # 扩展参数（DOT 百分比/冻结阈值/引电触发层数）


@dataclass(frozen=True)
class GainEnergy:
    """自己回复能量。"""

    side: str
    unit: "Unit"
    amount: int
    source: str
    target: str = "self"


@dataclass(frozen=True)
class BenchEnergy:
    """为场下（同侧非自身非阵亡）每只精灵回复能量。"""

    side: str
    self_unit: "Unit"
    amount: int
    source: str


@dataclass(frozen=True)
class Lifesteal:
    """吸血：本次伤害总额 × pct%（Reducer 读 frame.total_damage）。"""

    side: str
    unit: "Unit"
    pct: int           # 百分比（如 30 = 吸血 30%）
    source: str


@dataclass(frozen=True)
class StealEnergy:
    """偷取敌方当前在场精灵 N 能量给自己。"""

    side: str
    unit: "Unit"
    foe: "Unit"
    amount: int
    source: str


@dataclass(frozen=True)
class FoeCostGain:
    """回复能量 = 敌方当前在场精灵全部技能能耗 × ratio（雾气环绕）。"""

    side: str
    unit: "Unit"
    foe: "Unit"
    ratio: float
    source: str


@dataclass(frozen=True)
class ApplyMark:
    """施加印记（阵营级）：同种叠加、异种顶替（每极性至多 1）；exclusive 独立空间共存。"""

    side: str            # 目标阵营
    name: str            # 印记名（查 marks.MARK_CATALOG）
    layers: int
    source: str
    space: str = "normal"   # "normal" | "exclusive"（里拉鳐「吟游之弦」独立空间）


@dataclass(frozen=True)
class SetWeather:
    """设置天气（全局）：覆盖设置，同种刷新剩余回合。"""

    kind: str            # 雨天 / 沙暴 / 暴风雪 / 雷鸣
    turns: int
    source: str


@dataclass(frozen=True)
class LoseHp:
    """按 max_hp 百分比失去生命（印记/天气伤害，经 apply_hp_loss 唯一漏斗）。"""

    side: str
    unit: "Unit"
    pct: int            # 百分比整数（如 3 = 失去 3% max_hp）
    source: str


@dataclass(frozen=True)
class LoseEnergy:
    """失去能量（夹 0）。"""

    side: str
    unit: "Unit"
    amount: int
    source: str


@dataclass(frozen=True)
class ConsumeMarkLayers:
    """消耗印记层数（星陨触发后全清）；层数 ≤0 → 移除印记。"""

    side: str
    name: str
    amount: int
    source: str


@dataclass(frozen=True)
class TraitGain:
    """特性增益：写 unit.trait.gains（trait=True，免疫常规驱散）。

    Trigger（collect_reactions）从 SkillResolved 事件产出它，Reducer 执行——特性效果
    不再由 Hook 直接改状态，而是走「事件 → 新 Atom → Reducer」管道（v3 §6）。
    """

    unit: "Unit"
    stat: str          # atk/sp_atk/def/sp_def/speed / energy_cost
    mode: str          # pct / flat
    layers: int
    source: str
    permanent: bool = False


Atom: TypeAlias = (
    SpendEnergy | RevealSkill | DealDamage | HealPct | AddModifier
    | GainEnergy | BenchEnergy | Lifesteal | StealEnergy | FoeCostGain | TraitGain
    | ApplyMark | SetWeather | LoseHp | LoseEnergy | ConsumeMarkLayers
)
