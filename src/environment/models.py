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

**2026-08-29 数据协议 v2 改造（mydocs/battle_docs.md §D）**：
- Unit 新增 `id`（unit_id，`{side}-{槽位}-{精灵名}`）、`base_stats`（种族值）；
- 技能栏详情化：`skills`（初始携带）与 `current_skills`（当前回合，带 cooldown）都是
  `SkillInstance`（五要素：desc/type/kind/energy_cost/power）；
- 状态栏三合一：`energy_cost_mods`/`statuses`/`cooldowns` 取消，全部进 `stat_mods`
  （能耗减益 = stat="energy_cost" 的负层；DOT = stat="中毒/…" mode="dot|special"）；
- 特性增益转移到 `TraitState.gains`（取消 `used_once`），不再污染 `stat_mods`；
- SideState 新增印记槽 `positive_marks/negative_marks/exclusive_marks`；
- BattleState 新增天气 `weather`。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import Any

from .dataset import DataSource, load_skills
from .rng import BattleRng
from .rules import DEFAULT_RULES, ITEMS, BattleRules
from .skillbook import P1_EFFECTS, P2_EFFECTS, battle_ready

SIDES: tuple[str, str] = ("a", "b")

# 六维 + 技能相关 buff 的合法 stat 名（用于校验/展示；DOT 类用中文名如 "中毒"）。
_STAT_NAMES: frozenset[str] = frozenset({
    "hp", "atk", "sp_atk", "def", "sp_def", "speed",
    "combo", "lifesteal", "attack_power", "poison_attach", "energy_cost",
})
# 纯负面 buff 的 stat 名（mode="dot"/"special"，见 battle_docs.md §F.2）。
_STATUS_NAMES: frozenset[str] = frozenset({"中毒", "灼烧", "寄生", "冻结", "引电", "萌化"})

# StatModifier 可用的 mode（pct=每层10% / flat=每层+10 / dot=回合末结算 / special=特殊规则）。
_MODES: frozenset[str] = frozenset({"pct", "flat", "dot", "special"})


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
    effect: "Any"        # SkillEffect（P1∪P2）
    priority: int = 0
    desc: str = ""       # 只用于展示，引擎永不解析


@dataclass(frozen=True)
class SkillInstance:
    """**技能详情**（数据协议 v2 §D.1）：五要素 + 冷却。

    由 `build_unit` 从 FULL 技能表按名补全；`cooldown` 只出现在 `current_skills`
    （初始携带 `skills` 不带冷却）。
    """

    name: str
    desc: str          # 技能描述
    type: str          # 技能系别（克制/STAB 依据）
    kind: str          # 技能类别：物攻 / 魔攻 / 防御 / 状态
    energy_cost: int   # 技能能耗
    power: int         # 威力
    cooldown: int = 0  # 冷却剩余回合（0 = 不在冷却；**仅 current_skills 使用**）


def _skill_instance(name: str) -> SkillInstance:
    """按技能名构造 SkillInstance（五要素查 FULL 权威表；未知技能给空详情占位）。"""
    raw = load_skills(DataSource.FULL).get(name)
    if raw is None:
        return SkillInstance(name=name, desc="", type="普通", kind="", energy_cost=0, power=0)
    return SkillInstance(
        name=raw.name, desc=raw.desc, type=raw.type, kind=raw.kind,
        energy_cost=raw.energy_cost, power=raw.power,
    )


@dataclass
class StatModifier:
    """一条通用 buff 记录（数据协议 v2 §D.2）：满足所有 Buff 类型。

    `stat` 除六维（atk/sp_atk/def/sp_def/speed）外，还有技能相关维度：
      - "combo"       连击数（flat 1 层 = +1 连击；pct 1 层 = +10%）
      - "lifesteal"   吸血（flat 1 层 = +100% 吸血）
      - "attack_power" 攻击技能威力（pct 1 层 = +10%）
      - "poison_attach" 附加中毒（flat 1 层 = 攻击附带 1 层中毒）
      - "energy_cost" 全技能能耗（flat 负层 = 能耗 −1；正层 = 能耗 +1）
    纯负面 buff 用中文 stat：中毒 / 灼烧 / 寄生 / 冻结 / 引电 / 萌化（mode="dot"/"special"）。

    **层数是记账单位**：1 层 = 10%(pct) 或 +10(flat)；DOT 结算逻辑由引擎按 mode 分派。
    `trait=True` 表示特性增益（免疫常规驱散）；数据协议 v2 起特性增益转移进
    `TraitState.gains`，`Unit.stat_mods` 里不再出现 trait=True 的条目。
    """

    stat: str            # 维度（六维 / combo / lifesteal / attack_power / poison_attach /
                         # energy_cost / 中毒 / 灼烧 / 寄生 / 冻结 / 引电 / 萌化 / …）
    mode: str            # "pct"（每层 10%）| "flat"（每层 +10）| "dot"（回合末结算）| "special"（特殊规则）
    layers: int          # 层数（有符号：energy_cost 负层 = 能耗 −1）
    permanent: bool = False   # 离场是否保留（非永久 → 离场消失）
    source: str = ""         # 来源标签（技能名 / 特性名），同源刷新与驱散区分用
    desc: str = ""           # 描述文本（展示用）
    kwargs: dict = field(default_factory=dict)   # 扩展参数（DOT 百分比 / 冻结阈值 / 引电触发层数等）
    trait: bool = False       # True = 特性增益（免疫常规驱散）


def buff_layers(unit: "Unit", stat: str, mode: str = "") -> int:
    """某 stat 在 `stat_mods` + `trait.gains` 两处的层数总和（有符号）。

    mode="" 时不计 mode（连击/吸血等特殊维度用）。
    """
    total = 0
    for m in list(unit.stat_mods) + [g for g in (unit.trait.gains if unit.trait else [])]:
        if m.stat == stat and (mode == "" or m.mode == mode):
            total += m.layers
    return total


@dataclass
class TraitState:
    """每单位特性实例（数据协议 v2 §D.3）。

    `name` 指向 traits.TRAIT_CATALOG 静态定义；**特性产生的增益挂 `gains`**（复用
    StatModifier），离场清除 / 驱散隔离 / 属性聚合（aggregate_stats 并入）都从这里读，
    不污染 Unit.stat_mods。`used_once` 已取消，once_per_battle 状态由 kwargs 表达。
    """

    name: str
    desc: str = ""                     # 特性描述文本
    kwargs: dict = field(default_factory=dict)   # 传参字典（条件 / 数值 / once 标记）
    gains: list[StatModifier] = field(default_factory=list)   # 本特性产生的增益层


@dataclass
class Unit:
    """场上单位。**没有 take_damage / heal 方法**——`current_hp` 与 `fainted` 的唯一
    写者是 damage.apply_hp_loss / apply_heal（源码扫描测试钉死）。只经 build_unit 构造。

    数据协议 v2：`id`（unit_id）、`base_stats`（种族值）、`skills`/`current_skills`
    （技能详情）、`stat_mods` 合一（全部 buff）、`trait.gains`（特性增益）。
    直构测试（helper）可不传 id/base_stats/current_skills（走默认）；正式对局由
    new_battle / build_unit 显式生成。
    """

    name: str
    types: list[str]
    stats: dict[str, int]          # calc_combat_stats 的输出，**只读基线**
    skills: list[SkillInstance] = field(default_factory=list)   # 初始携带技能详情（五要素）
    id: str = ""                   # unit_id："{side}-{槽位}-{精灵名}"，开战即定、永不漂移
    base_stats: dict[str, int] = field(default_factory=dict)    # 种族值（六维原始值）
    current_skills: list[SkillInstance] = field(default_factory=list)  # 当前回合技能详情（+cooldown）
    nature: str = "坦率"
    bloodline: str = ""
    iv: dict[str, int] = field(default_factory=dict)
    max_hp: int = 0
    current_hp: int = 0
    energy: int = 0
    fainted: bool = False
    stat_mods: list[StatModifier] = field(default_factory=list)   # 全部 buff 合一
    trait: TraitState | None = None   # 特性实例（build_unit 从精灵表绑定）

    def refresh_current_skills(self) -> None:
        """从 skills（基线）重建 current_skills（当前生效视图，冷却清零）。"""
        self.current_skills = [replace(s, cooldown=0) for s in self.skills]


def aggregate_stats(unit: Unit, rules: BattleRules = DEFAULT_RULES) -> dict[str, int]:
    """派生视图：`base × (1 + 0.10 × Σpct层) + 10 × Σflat层`。

    每个属性的 pct 层与 flat 层**各自**夹到 `rules.stat_layer_cap`；读取 `stat_mods` +
    `trait.gains` 两处（特性增益并入）。
    **绝不把结果写回 unit.stats**——基线值必须保持可回溯，否则「离场清除增益」
    就没有可以退回的原点。伤害计算一律读这个函数，不读 unit.stats。
    """
    out: dict[str, int] = {}
    for key, base_val in unit.stats.items():
        pct = buff_layers(unit, key, "pct")
        flat = buff_layers(unit, key, "flat")
        pct = min(pct, rules.stat_layer_cap)
        flat = min(flat, rules.stat_layer_cap)
        out[key] = int(base_val * (1 + rules.stat_pct_per_layer * pct)) + rules.stat_flat_per_layer * flat
    return out


@dataclass
class MarkState:
    """一个印记实例（数据协议 v2 §F.3）：阵营级，数据只存 `{name, layers, source}`。"""

    name: str
    layers: int = 1
    source: str = ""


@dataclass
class SideState:
    """一方的持久状态。引入它是为了消灭约 30 处 a-if-else 三元分支。

    `revealed`（E4 迷雾）：`(队内下标, 技能名)` 集合——该方某只精灵**已释放过**的技能。
    对手视角只看得见已揭示技能；释放时机在 engine.resolve_skill（能量支付后）。
    **这是回合间持久状态，必须进 to_dict/from_dict**——否则快照回放会丢迷雾状态。

    `positive_marks / negative_marks / exclusive_marks`（数据协议 v2 §F.3）：
    **印记栏**——正面 / 负面普通空间各至多 1 个（新印记覆盖同类型旧印记）；
    `exclusive_marks` 为里拉鳐「吟游之弦」独立空间（共存、不顶替、仅全量驱散可移除）。
    """

    units: list[Unit]
    lives: int
    active: int = 0
    item_uses: dict[str, int] = field(default_factory=dict)   # 道具名 → 剩余次数
    revealed: set[tuple[int, str]] = field(default_factory=set)  # (队内下标, 技能名) 已揭示集
    positive_marks: list[MarkState] = field(default_factory=list)
    negative_marks: list[MarkState] = field(default_factory=list)
    exclusive_marks: list[MarkState] = field(default_factory=list)

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

    @property
    def field_empty(self) -> bool:
        """输出：本侧场上是否为空（在场精灵已阵亡 / 阵亡后补位尚未发生）。"""
        return self.active_unit.fainted


@dataclass
class WeatherState:
    """天气实例（数据协议 v2 §F.4）：全局（不分阵营）挂 BattleState 顶层。"""

    kind: str            # 雨天 / 沙暴 / 暴风雪 / 雷鸣
    turns_left: int = 3
    source: str = ""


@dataclass
class BattleState:
    """一局对战的全部**回合间**事实。rng 无默认值 → 构造不出没有种子的对局。

    这里**没有任何**回合内字段——那是引擎回合内局部上下文的事（见 engine.py）。
    数据协议 v2 新增：`weather`（天气，全局）。
    """

    side_a: SideState
    side_b: SideState
    rng: BattleRng
    rules: BattleRules = DEFAULT_RULES
    turn: int = 1
    winner: str | None = None
    done: bool = False
    battle_id: str = ""
    weather: WeatherState | None = None

    def side(self, s: str) -> SideState:
        """唯一的 a/b 分支点。输入：side；输出：该方 SideState。"""
        return self.side_a if s == "a" else self.side_b

    def foe(self, s: str) -> SideState:
        """输入：side；输出：**对手方** SideState。"""
        return self.side_b if s == "a" else self.side_a

    def active(self, s: str) -> Unit:
        """输入：side；输出：该方当前在场 Unit（side(s).active_unit）。"""
        return self.side(s).active_unit

    def unit(self, unit_id: str) -> Unit:
        """输入：unit_id（"{side}-{槽位}-{精灵名}"）；输出：对应 Unit。"""
        side, idx, _ = unit_id.split("-", 2)
        return self.side(side).units[int(idx)]

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
            "weather": _weather_to_dict(self.weather),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BattleState":
        """to_dict 的逆。**这是核心不变式的验证工具，不是可选项。**
        RNG 还原方式：`Random(seed)` 之后丢弃 calls 次抽取，随机流位置完全复原。
        `rules` 也从 dict 还原，于是自定义 rules 的对局也能往返。
        旧快照容错：weather/印记槽缺省 → 默认值。"""
        return cls(
            side_a=_side_from_dict(d["side_a"]),
            side_b=_side_from_dict(d["side_b"]),
            rng=BattleRng(seed=d["rng"]["seed"], calls=d["rng"]["calls"]),
            rules=_rules_from_dict(d["rules"]),
            turn=d["turn"],
            winner=d["winner"],
            done=d["done"],
            battle_id=d.get("battle_id", ""),
            weather=_weather_from_dict(d.get("weather")),
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
            "source": m.source, "desc": m.desc, "kwargs": dict(m.kwargs), "trait": m.trait}


def _mod_from_dict(m: dict) -> StatModifier:
    return StatModifier(
        stat=m["stat"], mode=m["mode"], layers=m["layers"],
        permanent=m.get("permanent", False), source=m.get("source", ""),
        desc=m.get("desc", ""), kwargs=dict(m.get("kwargs", {})),
        trait=m.get("trait", False),
    )


def _skill_inst_to_dict(s: SkillInstance) -> dict:
    return {"name": s.name, "desc": s.desc, "type": s.type, "kind": s.kind,
            "energy_cost": s.energy_cost, "power": s.power, "cooldown": s.cooldown}


def _skill_inst_from_dict(s) -> SkillInstance:
    """技能详情反序列化：兼容 dict（v2）与旧字符串（v1 按名补详情）。"""
    if isinstance(s, str):
        inst = _skill_instance(s)
        return inst
    return SkillInstance(
        name=s["name"], desc=s.get("desc", ""), type=s.get("type", "普通"),
        kind=s.get("kind", ""), energy_cost=s.get("energy_cost", 0),
        power=s.get("power", 0), cooldown=s.get("cooldown", 0),
    )


def _unit_to_dict(u: Unit) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "types": list(u.types),
        "base_stats": dict(u.base_stats),
        "stats": dict(u.stats),
        "skills": [_skill_inst_to_dict(s) for s in u.skills],
        "current_skills": [_skill_inst_to_dict(s) for s in u.current_skills],
        "nature": u.nature,
        "bloodline": u.bloodline,
        "iv": dict(u.iv),
        "max_hp": u.max_hp,
        "current_hp": u.current_hp,
        "energy": u.energy,
        "fainted": u.fainted,
        "stat_mods": [_mod_to_dict(m) for m in u.stat_mods],
        "trait": _trait_to_dict(u.trait),
    }


def _trait_to_dict(t: TraitState | None) -> dict | None:
    if t is None:
        return None
    return {"name": t.name, "desc": t.desc, "kwargs": dict(t.kwargs),
            "gains": [_mod_to_dict(m) for m in t.gains]}


def _trait_from_dict(t: dict | None) -> TraitState | None:
    if not t:
        return None
    return TraitState(
        name=t["name"], desc=t.get("desc", ""), kwargs=dict(t.get("kwargs", {})),
        gains=[_mod_from_dict(m) for m in t.get("gains", [])],
    )


def _side_to_dict(s: SideState) -> dict:
    return {
        "units": [_unit_to_dict(u) for u in s.units],
        "lives": s.lives,
        "active": s.active,
        "item_uses": dict(s.item_uses),
        "revealed": sorted([list(p) for p in s.revealed]),   # set → 排序列表（JSON 原生，跨进程稳定）
        "positive_marks": [{"name": m.name, "layers": m.layers, "source": m.source} for m in s.positive_marks],
        "negative_marks": [{"name": m.name, "layers": m.layers, "source": m.source} for m in s.negative_marks],
        "exclusive_marks": [{"name": m.name, "layers": m.layers, "source": m.source} for m in s.exclusive_marks],
    }


def _marks_from_dict(rows) -> list[MarkState]:
    return [MarkState(name=r["name"], layers=r.get("layers", 1), source=r.get("source", "")) for r in rows]


def _weather_to_dict(w: WeatherState | None) -> dict | None:
    if w is None:
        return None
    return {"kind": w.kind, "turns_left": w.turns_left, "source": w.source}


def _weather_from_dict(w) -> WeatherState | None:
    if not w:
        return None
    return WeatherState(kind=w["kind"], turns_left=w.get("turns_left", 3), source=w.get("source", ""))


def _rules_to_dict(r: BattleRules) -> dict:
    return {f.name: getattr(r, f.name) for f in fields(BattleRules)}


def _rules_from_dict(d: dict) -> BattleRules:
    known = {f.name for f in fields(BattleRules)}
    return BattleRules(**{k: v for k, v in d.items() if k in known})


def skill_from_instance(inst: SkillInstance) -> Skill | None:
    """从 `SkillInstance`（当前回合技能详情）构建引擎 Skill。

    数据协议 v2：五要素取实例（愿力替换 / 冷却后的能耗、威力、类别以 current_skills
    为准），效果按名查 P1∪P2 表；不在白名单 → None。engine._combat_skill 与
    prediction（预估）共用此构造，消灭复制。
    """
    effect = P1_EFFECTS.get(inst.name) or P2_EFFECTS.get(inst.name)
    if effect is None:
        return None
    return Skill(name=inst.name, kind=inst.kind, type=inst.type, power=inst.power,
                 energy_cost=inst.energy_cost, effect=effect,
                 priority=effect.priority, desc=inst.desc)


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
    skills = [_skill_inst_from_dict(s) for s in d["skills"]]
    cur = [_skill_inst_from_dict(s) for s in d.get("current_skills", d["skills"])]
    # 旧快照容错：有 energy_cost_mods → 并入 stat_mods（stat="energy_cost" flat 层）
    stat_mods = [_mod_from_dict(m) for m in d.get("stat_mods", [])]
    for ecm in d.get("energy_cost_mods", []):
        stat_mods.append(StatModifier(
            stat="energy_cost", mode="flat", layers=-ecm.get("layers", 0),
            permanent=ecm.get("permanent", False), source=ecm.get("source", ""),
            trait=ecm.get("trait", False),
        ))
    # 旧快照容错：statuses dict → 并入 stat_mods（mode="dot"，kwargs={pct: 值}）
    for st_name, st in (d.get("statuses") or {}).items():
        stat_mods.append(StatModifier(
            stat=st_name, mode="dot", layers=st.get("layers", 1),
            permanent=False, source=st.get("source", ""),
            kwargs={"pct": st.get("pct", 3)},
        ))
    return Unit(
        id=d.get("id", ""),
        name=d["name"],
        types=list(d["types"]),
        base_stats=dict(d.get("base_stats", d["stats"])),
        stats=dict(d["stats"]),
        skills=skills,
        current_skills=cur,
        nature=d.get("nature", "坦率"),
        bloodline=d.get("bloodline", ""),
        iv=dict(d.get("iv", {})),
        max_hp=d["max_hp"],
        current_hp=d["current_hp"],
        energy=d["energy"],
        fainted=d["fainted"],
        stat_mods=stat_mods,
        trait=_trait_from_dict(d.get("trait")),
    )


def _side_from_dict(d: dict) -> SideState:
    return SideState(
        units=[_unit_from_dict(u) for u in d["units"]],
        lives=d["lives"],
        active=d["active"],
        item_uses=dict(d["item_uses"]),
        revealed=set(tuple(x) for x in d.get("revealed", [])),
        positive_marks=_marks_from_dict(d.get("positive_marks", [])),
        negative_marks=_marks_from_dict(d.get("negative_marks", [])),
        exclusive_marks=_marks_from_dict(d.get("exclusive_marks", [])),
    )


def build_unit(spec: dict, rules: BattleRules = DEFAULT_RULES) -> Unit:
    """唯一构造路径：吃 roster spec，按名从可对战白名单（P1 ∪ P2）取技能定义，
    派生 max_hp / current_hp / energy / base_stats / current_skills，并把特性名解析成
    **实际装备**的特性（未实现 → 白板 `default`）。非法 spec 抛 ValueError。

    数据协议 v2：spec["skills"] 仍为技能名列表（roster spec 保持最小），此处按名补全
    SkillInstance 五要素；unit_id = `"{side}-{槽位}-{name}"` 由调用方（new_battle）传入
    spec["id"]（缺省空串，单测可直构）。
    """
    # 函数内 import：traits → hooks → primitives → damage → models 成环，模块级 import 会炸。
    from .dataset import load_spirits
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
    # 特性描述文本：从图鉴表查（数据协议 v2 §D.3：desc 进状态，敌方视角可直接展示）
    trait_desc = ""
    if tname and tname != "default":
        sp = load_spirits().get(spec["name"])
        if sp is not None:
            trait_desc = sp.trait_desc
    unit = Unit(
        id=spec.get("id", ""),
        name=spec["name"],
        types=list(spec.get("types", [])),
        base_stats=dict(spec.get("base_stats", stats)),
        stats=dict(stats),
        skills=[_skill_instance(sname) for sname in spec["skills"]],
        current_skills=[],
        nature=spec.get("nature", "坦率"),
        bloodline=spec.get("bloodline", ""),
        iv=dict(spec.get("iv", {})),
        max_hp=max_hp,
        current_hp=max_hp,
        energy=rules.energy_start,
        trait=TraitState(name=tname, desc=trait_desc),
    )
    unit.refresh_current_skills()
    return unit


def new_battle(roster_a: list[dict], roster_b: list[dict], *,
               seed: int, items_a: list[str] | None = None,
               items_b: list[str] | None = None, rules: BattleRules = DEFAULT_RULES,
               battle_id: str = "") -> BattleState:
    """用两份 roster spec 开一局。items 为 None → 每方带满全部道具各 1 次。

    unit_id = `"{side}-{槽位}-{精灵名}"` 在此生成（开战即定、永不漂移）。
    """
    if len(roster_a) != rules.team_size or len(roster_b) != rules.team_size:
        raise ValueError(
            f"队伍规模必须为 {rules.team_size}，实际 {len(roster_a)} / {len(roster_b)}。"
        )
    units_a = [build_unit({**spec, "id": f"a-{i}-{spec['name']}"}, rules)
               for i, spec in enumerate(roster_a)]
    units_b = [build_unit({**spec, "id": f"b-{i}-{spec['name']}"}, rules)
               for i, spec in enumerate(roster_b)]
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
