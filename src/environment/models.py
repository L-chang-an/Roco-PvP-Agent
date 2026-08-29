"""领域模型：一局对战的全部**回合间**事实，以及完整往返序列化。

核心不变式（回合制的本质）：`execute_turn` 的输出**只**由 `(state.to_dict(),
decision_a, decision_b)` 决定。物质基础是 `to_dict` / `from_dict` —— 一份**完备的
回合间快照**。回合内派生量（谁应对了谁、减伤多少、出手顺序）一律住在引擎的
局部上下文对象里（见 engine.py），**绝不落进 BattleState**（本文件不允许出现
回合内字段）。

三条边现在就拆：
- 持久状态按方收进 `SideState`（消灭约 30 处 `team_a if s=="a" else team_b` 三元分支）；
- 运行时服务（RNG）只把 seed/calls 进 to_dict（`BattleRng.audit()`）；
- 回合内派生量住在引擎局部上下文对象里，见 engine.py。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from enum import Enum

from .dataset import DataSource, load_skills
from .rng import BattleRng
from .rules import DEFAULT_RULES, ITEMS, BattleRules
from .skillbook import P1_EFFECTS, P2_EFFECTS, SkillEffect, battle_ready

SIDES: tuple[str, str] = ("a", "b")


class ActionType(str, Enum):
    """主动作类型枚举。值即动作 dict 的 `type` 字段值（"skill"/"switch"/"recharge"）。"""

    SKILL = "skill"
    SWITCH = "switch"
    RECHARGE = "recharge"


@dataclass(frozen=True)
class Skill:
    """静态定义，不可变、可共享。effect 来自 skillbook.P1/P2 效果表。

    序列化只存技能名，from_dict 按名从技能表重建——技能是静态数据，不需要全量落盘。
    """

    name: str
    kind: str            # 物攻 / 魔攻 / 防御 / 状态
    type: str            # 系别（克制/STAB 依据，真实 18 系）
    power: int
    energy_cost: int
    effect: SkillEffect
    priority: int = 0
    desc: str = ""       # 只用于展示，引擎永不解析


@dataclass
class StatModifier:
    """一条增益/减益记录。**层数是记账单位**：1 层 = 10%(pct) 或 +10(flat)。

    离散对象而不是求和字典——离场清除非永久层、将来驱散 N 层／层数翻倍都要求可枚举。
    `trait=True` 表示**特性增益**：免疫常规「驱散增益」（scope="regular"）。
    """

    stat: str            # atk / sp_atk / def / sp_def / speed
    mode: str            # "pct" | "flat"
    layers: int
    permanent: bool = False   # 离场是否保留（非永久 → 离场消失）
    source: str = ""         # 来源标签（技能名 / 特性名），同源刷新与驱散区分用
    trait: bool = False       # True = 特性增益（免疫常规驱散）


@dataclass
class EnergyCostMod:
    """一条「全技能能耗−1」减益层（水蓝蓝·浸润）。

    与 StatModifier 平行、只作用于能量门槛：`skill_energy_cost` 从 base 里
    扣掉全部层数（夹到 0）。同款 trait / permanent / source 语义——非永久随离场
    清除，trait=True 免疫常规驱散。
    """

    layers: int
    permanent: bool = False   # 离场是否保留（非永久 → 离场消失）
    trait: bool = False       # True = 特性增益（免疫常规驱散）
    source: str = ""         # 来源标签（技能名 / 特性名）


@dataclass
class TraitState:
    """每单位特性实例（运行时状态）。`name` 指向 traits.TRAIT_CATALOG 静态定义。"""

    name: str
    used_once: bool = False   # once_per_battle：首次触发后置位，随 to_dict 序列化


@dataclass
class Unit:
    """场上单位。**没有 take_damage / heal 方法**——`current_hp` 与 `fainted` 的唯一
    写者是 damage.apply_hp_loss / apply_heal（源码扫描测试钉死）。只经 build_unit 构造。"""

    name: str
    types: list[str]
    stats: dict[str, int]     # calc_combat_stats 的输出，**只读基线**
    skills: list[Skill]
    nature: str = "坦率"
    bloodline: str = ""
    iv: dict[str, int] = field(default_factory=dict)
    max_hp: int = 0
    current_hp: int = 0
    energy: int = 0
    fainted: bool = False
    stat_mods: list[StatModifier] = field(default_factory=list)
    energy_cost_mods: list[EnergyCostMod] = field(default_factory=list)   # 全技能能耗−X
    trait: TraitState | None = None   # 特性实例（S2 起 build_unit 从精灵表绑定）


def aggregate_stats(unit: Unit, rules: BattleRules = DEFAULT_RULES) -> dict[str, int]:
    """派生视图：`base × (1 + 0.10 × Σpct层) + 10 × Σflat层`。

    每个属性的 pct 层与 flat 层**各自**夹到 `rules.stat_layer_cap`。
    **绝不把结果写回 unit.stats**——基线值必须保持可回溯，否则「离场清除增益」
    就没有可以退回的原点。伤害计算一律读这个函数，不读 unit.stats。
    """
    out: dict[str, int] = {}
    for key, base_val in unit.stats.items():
        pct = flat = 0
        for m in unit.stat_mods:
            if m.stat != key:
                continue
            if m.mode == "pct":
                pct += m.layers
            elif m.mode == "flat":
                flat += m.layers
        pct = min(pct, rules.stat_layer_cap)
        flat = min(flat, rules.stat_layer_cap)
        out[key] = int(base_val * (1 + rules.stat_pct_per_layer * pct)) + rules.stat_flat_per_layer * flat
    return out


@dataclass
class SideState:
    """一方的持久状态。引入它是为了消灭约 30 处 a-if-else 三元分支。

    `revealed`（E4 迷雾）：`(队内下标, 技能名)` 集合——该方某只精灵**已释放过**的技能。
    对手视角只看得见已揭示技能；释放时机在 engine.resolve_skill（能量支付后）。
    **这是回合间持久状态，必须进 to_dict/from_dict**——否则快照回放会丢迷雾状态，
    E0b 的马尔可夫性用例会在写错的那一刻当场变红（参考项目的 revealed 不序列化是已知弱点）。
    """

    units: list[Unit]
    lives: int
    active: int = 0
    item_uses: dict[str, int] = field(default_factory=dict)   # 道具名 → 剩余次数
    revealed: set[tuple[int, str]] = field(default_factory=set)  # (队内下标, 技能名) 已揭示集

    @property
    def active_unit(self) -> Unit:
        """输出：当前在场单位（units[active]）。"""
        return self.units[self.active]

    def first_living_bench(self) -> int | None:
        """输入：无。输出：排除 active 的首个存活后备下标；无则 None。"""
        for i, u in enumerate(self.units):
            if i != self.active and not u.fainted:
                return i
        return None

    def has_living(self) -> bool:
        """输出：本侧是否还有存活单位（判负兜底用）。"""
        return any(not u.fainted for u in self.units)


@dataclass
class BattleState:
    """一局对战的全部**回合间**事实。rng 无默认值 → 构造不出没有种子的对局。

    这里**没有任何**回合内字段——那是引擎回合内局部上下文的事（见 engine.py）。
    """

    side_a: SideState
    side_b: SideState
    rng: BattleRng
    rules: BattleRules = DEFAULT_RULES
    turn: int = 1
    winner: str | None = None
    done: bool = False
    battle_id: str = ""

    def side(self, s: str) -> SideState:
        """唯一的 a/b 分支点。输入：side；输出：该方 SideState。"""
        return self.side_a if s == "a" else self.side_b

    def foe(self, s: str) -> SideState:
        """输入：side；输出：**对手方** SideState。"""
        return self.side_b if s == "a" else self.side_a

    def active(self, s: str) -> Unit:
        """输入：side；输出：该方当前在场 Unit（side(s).active_unit）。"""
        return self.side(s).active_unit

    def reveal_skill(self, side: str, unit_index: int, skill_name: str) -> None:
        """E4 迷雾：记录一次技能揭示（该方该下标精灵的该技能名）。

        揭示时机 = 技能**确实释放**（engine.resolve_skill 能量支付后）——对手视角从此
        可见该技能详情。队内下标指向释放者；同回合每方最多执行一次主动作，active 即下标。
        """
        self.side(side).revealed.add((unit_index, skill_name))

    def to_dict(self) -> dict:
        """全量 JSON 快照。**只含 JSON 原生类型**；`json.dumps` 不许传 `default=`，
        塞进非序列化对象要当场炸。要序列化集合就 sorted()。"""
        return {
            "side_a": _side_to_dict(self.side_a),
            "side_b": _side_to_dict(self.side_b),
            "rng": self.rng.audit(),
            "rules": _rules_to_dict(self.rules),
            "turn": self.turn,
            "winner": self.winner,
            "done": self.done,
            "battle_id": self.battle_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BattleState":
        """to_dict 的逆。**这是核心不变式的验证工具，不是可选项。**
        RNG 还原方式：`Random(seed)` 之后丢弃 calls 次抽取，随机流位置完全复原。
        `rules` 也从 dict 还原，于是自定义 rules 的对局也能往返。"""
        return cls(
            side_a=_side_from_dict(d["side_a"]),
            side_b=_side_from_dict(d["side_b"]),
            rng=BattleRng(seed=d["rng"]["seed"], calls=d["rng"]["calls"]),
            rules=_rules_from_dict(d["rules"]),
            turn=d["turn"],
            winner=d["winner"],
            done=d["done"],
            battle_id=d.get("battle_id", ""),
        )

    def clone(self) -> "BattleState":
        """E0 实现 = `from_dict(to_dict())`：显然正确，且天然复用序列化往返测试。
        性能不够时再优化，**别用 copy.deepcopy**（参考项目为此付过代价）。"""
        return type(self).from_dict(self.to_dict())

    def state_hash(self) -> str:
        """sha256(json.dumps(to_dict(), sort_keys=True, ensure_ascii=False))。"""
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


# ── 序列化辅助 ──
def _mod_to_dict(m: StatModifier) -> dict:
    return {"stat": m.stat, "mode": m.mode, "layers": m.layers, "permanent": m.permanent,
            "source": m.source, "trait": m.trait}


def _ecm_to_dict(m: EnergyCostMod) -> dict:
    return {"layers": m.layers, "permanent": m.permanent, "trait": m.trait, "source": m.source}


def _ecm_from_dict(m: dict) -> EnergyCostMod:
    return EnergyCostMod(layers=m["layers"], permanent=m.get("permanent", False),
                         trait=m.get("trait", False), source=m.get("source", ""))


def _unit_to_dict(u: Unit) -> dict:
    return {
        "name": u.name,
        "types": list(u.types),
        "stats": dict(u.stats),
        "skills": [s.name for s in u.skills],   # 只存名字，技能是静态数据
        "nature": u.nature,
        "bloodline": u.bloodline,
        "iv": dict(u.iv),
        "max_hp": u.max_hp,
        "current_hp": u.current_hp,
        "energy": u.energy,
        "fainted": u.fainted,
        "stat_mods": [_mod_to_dict(m) for m in u.stat_mods],
        "energy_cost_mods": [_ecm_to_dict(m) for m in u.energy_cost_mods],
        "trait": {"name": u.trait.name, "used_once": u.trait.used_once} if u.trait else None,
    }


def _side_to_dict(s: SideState) -> dict:
    return {
        "units": [_unit_to_dict(u) for u in s.units],
        "lives": s.lives,
        "active": s.active,
        "item_uses": dict(s.item_uses),
        "revealed": sorted([list(p) for p in s.revealed]),   # set → 排序列表（JSON 原生，跨进程稳定）
    }


def _rules_to_dict(r: BattleRules) -> dict:
    return {f.name: getattr(r, f.name) for f in fields(BattleRules)}


def _rules_from_dict(d: dict) -> BattleRules:
    known = {f.name for f in fields(BattleRules)}
    return BattleRules(**{k: v for k, v in d.items() if k in known})


def _skill_from_name(name: str) -> Skill:
    """按名从可对战白名单（P1 ∪ P2）重建 Skill（from_dict 用）。

    技能数据查 `load_skills()`（默认 FULL 权威表）；效果查 `P1_EFFECTS ∪ P2_EFFECTS`；
    `priority`（先手修正）从效果表读。名字不在白名单 → KeyError（不该发生）。
    """
    raw = load_skills().get(name)
    effect = P1_EFFECTS.get(name) or P2_EFFECTS.get(name)
    if raw is None or effect is None:
        raise KeyError(f"技能「{name}」不在可对战白名单（P1 ∪ P2）。")
    return Skill(
        name=raw.name,
        kind=raw.kind,
        type=raw.type,
        power=raw.power,
        energy_cost=raw.energy_cost,
        effect=effect,
        priority=effect.priority,
        desc=raw.desc,
    )


def _unit_from_dict(d: dict) -> Unit:
    trait_d = d.get("trait")
    return Unit(
        name=d["name"],
        types=list(d["types"]),
        stats=dict(d["stats"]),
        skills=[_skill_from_name(n) for n in d["skills"]],
        nature=d.get("nature", "坦率"),
        bloodline=d.get("bloodline", ""),
        iv=dict(d.get("iv", {})),
        max_hp=d["max_hp"],
        current_hp=d["current_hp"],
        energy=d["energy"],
        fainted=d["fainted"],
        stat_mods=[StatModifier(
            stat=m["stat"], mode=m["mode"], layers=m["layers"],
            permanent=m.get("permanent", False), source=m.get("source", ""),
            trait=m.get("trait", False),
        ) for m in d.get("stat_mods", [])],
        energy_cost_mods=[_ecm_from_dict(m) for m in d.get("energy_cost_mods", [])],
        trait=TraitState(name=trait_d["name"], used_once=trait_d.get("used_once", False))
        if trait_d else None,
    )


def _side_from_dict(d: dict) -> SideState:
    return SideState(
        units=[_unit_from_dict(u) for u in d["units"]],
        lives=d["lives"],
        active=d["active"],
        item_uses=dict(d["item_uses"]),
        revealed=set(tuple(x) for x in d.get("revealed", [])),
    )


def build_unit(spec: dict, rules: BattleRules = DEFAULT_RULES) -> Unit:
    """唯一构造路径：吃 roster spec，按名从可对战白名单（P1 ∪ P2）取技能定义，
    派生 max_hp / current_hp / energy，并把特性名解析成**实际装备**的特性（未实现 → 白板
    `default`）。非法 spec 抛 ValueError。"""
    # 函数内 import：traits → hooks → primitives → damage → models 成环，模块级 import 会炸。
    from .traits import resolve_trait_name

    skills: list[Skill] = []
    for sname in spec["skills"]:
        if not battle_ready(sname):
            raise ValueError(f"技能「{sname}」不在可对战白名单（P1 ∪ P2）中。")
        skills.append(_skill_from_name(sname))
    stats = spec["stats"]
    max_hp = stats["hp"]
    # 特性：未实现（或精灵表无特性名）→ 装备白板 `default`（零效果占位，见 traits.py）
    tname = resolve_trait_name(spec.get("trait") or "")
    return Unit(
        name=spec["name"],
        types=list(spec.get("types", [])),
        stats=dict(stats),
        skills=skills,
        nature=spec.get("nature", "坦率"),
        bloodline=spec.get("bloodline", ""),
        iv=dict(spec.get("iv", {})),
        max_hp=max_hp,
        current_hp=max_hp,
        energy=rules.energy_start,
        trait=TraitState(name=tname),
    )


def new_battle(roster_a: list[dict], roster_b: list[dict], *,
               seed: int, items_a: list[str] | None = None,
               items_b: list[str] | None = None, rules: BattleRules = DEFAULT_RULES,
               battle_id: str = "") -> BattleState:
    """用两份 roster spec 开一局。items 为 None → 每方带满全部道具各 1 次。"""
    if len(roster_a) != rules.team_size or len(roster_b) != rules.team_size:
        raise ValueError(
            f"队伍规模必须为 {rules.team_size}，实际 {len(roster_a)} / {len(roster_b)}。"
        )
    units_a = [build_unit(spec, rules) for spec in roster_a]
    units_b = [build_unit(spec, rules) for spec in roster_b]
    item_names_a = items_a if items_a is not None else list(ITEMS)
    item_names_b = items_b if items_b is not None else list(ITEMS)
    return BattleState(
        side_a=SideState(units=units_a, lives=rules.lives,
                         item_uses={name: ITEMS[name] for name in item_names_a}),
        side_b=SideState(units=units_b, lives=rules.lives,
                         item_uses={name: ITEMS[name] for name in item_names_b}),
        rng=BattleRng(seed),
        rules=rules,
        battle_id=battle_id,
    )
