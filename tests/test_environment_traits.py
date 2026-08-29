"""三只精灵的特性（S2 第一批）：火花·助燃 / 喵喵·氧循环 / 水蓝蓝·浸润。

覆盖三层：
1. 目录与查找：TRAIT_CATALOG 注册 / trait_defs_for 空与未知。
2. emit 分发：用了对应系别技能才触发；可叠层；heal_pct / energy_cost_mod 新原语。
3. 引擎接线（白盒）：SKILL_RESOLVE 在技能结算后发射；浸润降低下回合能耗并放开门控；
   离场清除非永久能耗减益；序列化往返；马尔可夫性不被破坏。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from environment.actions import Decision, recharge_action, skill_action, skill_block_reason, switch_action
from environment.dataset import DataSource
from environment.engine import execute_turn
from environment.hooks import Hook, emit
from environment.models import (
    BattleRng, BattleState, SideState, SkillInstance, StatModifier, TraitState, Unit,
    _unit_from_dict, _unit_to_dict, build_unit,
)
from environment.primitives import apply_energy_cost_mod, dispel_gains, skill_energy_cost
from environment.rules import DEFAULT_RULES
from environment.skillbook import SkillCategory, SkillEffect
from environment.teambuilder import TeamPick, build_roster
from environment.traits import (
    DEFAULT_TRAIT_NAME, TRAIT_CATALOG, resolve_trait_name, trait_defs_for, trait_implemented,
)


def _skill(name: str, s_type: str, *, power: int = 60, cost: int = 3) -> SkillInstance:
    """构造 SkillInstance（数据协议 v2：Unit.skills 存五要素）。

    引擎按技能名查 P1∪P2 效果表（name 必须是 battle_ready 真实技能），但 power/cost 取
    current_skills 的自定义值——所以这里用真实系别技能名 + 自定义数值即可精确控制。
    """
    return SkillInstance(name=name, desc="", type=s_type, kind="物攻", power=power, energy_cost=cost)


def _equip(u: Unit, *skills: SkillInstance) -> None:
    """给直构 Unit 装上技能（skills 基线 + current_skills 当前视图同步）。"""
    u.skills = list(skills)
    u.current_skills = list(skills)


def _unit(name: str = "测试", s_type: str = "普通", *, hp: int = 100,
          energy: int = 10, trait: str | None = None) -> Unit:
    stats = {"hp": hp, "atk": 80, "sp_atk": 80, "def": 80, "sp_def": 80, "speed": 80}
    return Unit(
        id=f"a-0-{name}", name=name, types=[s_type],
        base_stats=dict(stats), stats=dict(stats),
        skills=[], current_skills=[], max_hp=hp, current_hp=hp, energy=energy,
        trait=TraitState(name=trait) if trait else None,
    )


def _emit_skill(u: Unit, s_type: str, *, dealt_counter: bool = False) -> None:
    ctx = SimpleNamespace(unit=u, skill=SimpleNamespace(type=s_type),
                          dealt_counter=dealt_counter, energy_max=10)
    emit(None, Hook.SKILL_RESOLVE, ctx, trait_defs_for(u))


# ── 目录与查找 ──
def test_catalog_registers_default_and_four_traits() -> None:
    """目录 = 白板 default + 已实现特性（三系 + 迪莫）。"""
    assert set(TRAIT_CATALOG) == {DEFAULT_TRAIT_NAME, "助燃", "氧循环", "浸润", "最好的伙伴"}


def test_default_trait_has_no_bindings() -> None:
    """白板特性零绑定 → emit 时一个效果都不执行。"""
    blank = TRAIT_CATALOG[DEFAULT_TRAIT_NAME]
    assert blank.name == "default" and blank.bindings == ()


def test_trait_implemented_excludes_default() -> None:
    """`default` 与空名都不算「已实现」（覆盖率报告据此计数）。"""
    assert trait_implemented("助燃") and trait_implemented("浸润") and trait_implemented("最好的伙伴")
    assert not trait_implemented(DEFAULT_TRAIT_NAME)
    assert not trait_implemented("")
    assert not trait_implemented("茂盛")     # 真实特性（布布种子）但尚未实现


def test_resolve_trait_name_falls_back_to_default() -> None:
    assert resolve_trait_name("助燃") == "助燃"
    assert resolve_trait_name("最好的伙伴") == "最好的伙伴"     # 已实现 → 绑真实特性
    assert resolve_trait_name("茂盛") == DEFAULT_TRAIT_NAME    # 未实现 → 白板
    assert resolve_trait_name("") == DEFAULT_TRAIT_NAME
    assert resolve_trait_name(DEFAULT_TRAIT_NAME) == DEFAULT_TRAIT_NAME


def test_trait_defs_for_none_and_unknown() -> None:
    u = _unit()
    u.trait = None
    assert trait_defs_for(u) == []                               # 旧快照 trait=null
    assert [t.name for t in trait_defs_for(_unit(trait="脏名字"))] == [DEFAULT_TRAIT_NAME]
    assert [t.name for t in trait_defs_for(_unit(trait=DEFAULT_TRAIT_NAME))] == [DEFAULT_TRAIT_NAME]
    assert [t.name for t in trait_defs_for(_unit(trait="助燃"))] == ["助燃"]


# ── 火花·助燃：用火系技能后 双攻+20% ──
def test_zhuran_fire_skill_buffs_both_attacks() -> None:
    u = _unit("火花", "火", trait="助燃")
    _emit_skill(u, "火")
    gains = {m.stat: m for m in u.trait.gains}
    assert gains["atk"].layers == 2 and gains["sp_atk"].layers == 2
    assert gains["atk"].mode == "pct" and gains["atk"].trait is True and gains["atk"].permanent is False
    assert u.stat_mods == []                     # 特性增益在 trait.gains，不污染 stat_mods


def test_zhuran_stacks_layers() -> None:
    u = _unit("火花", "火", trait="助燃")
    _emit_skill(u, "火")
    _emit_skill(u, "火")          # 叠层：单次 20% × 2
    assert sum(m.layers for m in u.trait.gains if m.stat == "atk") == 4


def test_zhuran_ignores_other_types() -> None:
    u = _unit("火花", "火", trait="助燃")
    _emit_skill(u, "草")
    assert u.trait.gains == [] and u.stat_mods == []


# ── 喵喵·氧循环：用草系技能后 回复10%生命 ──
def test_yangxunhuan_heals_10pct_after_grass() -> None:
    u = _unit("喵喵", "草", hp=100, trait="氧循环")
    u.current_hp = 50
    _emit_skill(u, "草")
    assert u.current_hp == 60                                 # 10% of 100


def test_yangxunhuan_each_grass_skill_heals_again() -> None:
    u = _unit("喵喵", "草", hp=100, trait="氧循环")
    u.current_hp = 50
    _emit_skill(u, "草")
    _emit_skill(u, "草")          # 每次草系技能都回一次，不叠加成"层"
    assert u.current_hp == 70


def test_yangxunhuan_ignores_non_grass() -> None:
    u = _unit("喵喵", "草", hp=100, trait="氧循环")
    u.current_hp = 50
    _emit_skill(u, "水")
    assert u.current_hp == 50


# ── 水蓝蓝·浸润：用水系技能后 全技能能耗-1 ──
def test_jinrun_adds_cost_mod_after_water() -> None:
    u = _unit("水蓝蓝", "水", trait="浸润")
    _emit_skill(u, "水")
    cost_mods = [m for m in u.trait.gains if m.stat == "energy_cost"]
    assert [m.layers for m in cost_mods] == [-1]          # 能耗修正值 −1 = 能耗−1
    assert cost_mods[0].trait is True and cost_mods[0].permanent is False
    assert skill_energy_cost(u, 3) == 2


def test_jinrun_stacks_and_clamps_to_zero() -> None:
    u = _unit("水蓝蓝", "水", trait="浸润")
    for _ in range(3):
        _emit_skill(u, "水")
    assert skill_energy_cost(u, 3) == 0
    assert skill_energy_cost(u, 1) == 0


def test_jinrun_ignores_non_water() -> None:
    u = _unit("水蓝蓝", "水", trait="浸润")
    _emit_skill(u, "火")
    assert u.trait.gains == []


# ── 驱散 scope 覆盖能耗减益 ──
def test_dispel_scopes_cover_energy_cost_mods() -> None:
    u = _unit("水蓝蓝", "水")
    apply_energy_cost_mod(u, layers=-1, trait=True, source="浸润")
    apply_energy_cost_mod(u, layers=-2, trait=False, source="水冷")
    assert dispel_gains(u, scope="regular") == -2             # 只清常规 2 层（能耗负层）
    cost_mods = [m for m in u.stat_mods if m.stat == "energy_cost"]
    assert [m.layers for m in cost_mods] == [-1]              # 特性标记的保留
    assert dispel_gains(u, scope="all") == -1                 # 连特性一起清
    assert [m for m in u.stat_mods if m.stat == "energy_cost"] == []


# ── 序列化往返 / 旧快照 ──
def test_trait_gain_energy_cost_roundtrip() -> None:
    u = _unit("水蓝蓝", "水", trait="浸润")
    u.trait.gains.append(StatModifier(stat="energy_cost", mode="flat", layers=-1,
                                      permanent=False, trait=True, source="浸润"))
    r = _unit_from_dict(_unit_to_dict(u))
    gains = [m for m in r.trait.gains if m.stat == "energy_cost"]
    assert gains[0].layers == -1 and gains[0].trait is True
    assert r.trait.name == "浸润"


def test_legacy_snapshot_no_energy_cost_mods() -> None:
    d = {
        "name": "水蓝蓝", "types": ["水"],
        "stats": {"hp": 100, "atk": 80, "sp_atk": 80, "def": 80, "sp_def": 80, "speed": 80},
        "skills": [], "nature": "坦率", "bloodline": "", "iv": {},
        "max_hp": 100, "current_hp": 100, "energy": 10, "fainted": False,
        "stat_mods": [], "trait": None,
        # 无 "energy_cost_mods" 键（旧快照）
    }
    u = _unit_from_dict(d)
    assert [m for m in u.stat_mods if m.stat == "energy_cost"] == []
    assert u.trait is None


# ── roster → build_unit 的特性绑定 ──
def test_build_roster_carries_trait() -> None:
    roster = build_roster(
        [TeamPick("火花", ["火苗"]), TeamPick("喵喵", ["抓挠"]), TeamPick("水蓝蓝", ["拍击"])],
        source=DataSource.FULL,
    )
    by_name = {e["name"]: e for e in roster}
    assert by_name["火花"]["trait"] == "助燃"
    assert by_name["喵喵"]["trait"] == "氧循环"
    assert by_name["水蓝蓝"]["trait"] == "浸润"


def _spec(trait=None) -> dict:
    d = {
        "name": "火花", "types": ["火"],
        "stats": {"hp": 100, "atk": 84, "sp_atk": 37, "def": 56, "sp_def": 43, "speed": 78},
        "skills": ["抓挠"],
    }
    if trait is not None:
        d["trait"] = trait
    return d


def test_build_unit_attaches_trait_from_spec() -> None:
    u = build_unit(_spec("助燃"))
    assert u.trait is not None and u.trait.name == "助燃"


def test_build_unit_unimplemented_trait_gets_default() -> None:
    """特性未实现（茂盛）→ 装备白板 default（精灵仍能正常上场）。"""
    assert build_unit(_spec("茂盛")).trait.name == DEFAULT_TRAIT_NAME


def test_build_unit_implemented_trait_kept() -> None:
    """特性已实现（最好的伙伴）→ 绑真实特性，不落白板。"""
    assert build_unit(_spec("最好的伙伴")).trait.name == "最好的伙伴"


def test_build_unit_no_trait_gets_default() -> None:
    """spec 无 trait 键 → 也装备白板（Unit.trait 不再是 None）。"""
    assert build_unit(_spec()).trait.name == DEFAULT_TRAIT_NAME
    assert build_unit(_spec("")).trait.name == DEFAULT_TRAIT_NAME


# ── 引擎接线（白盒）──
def _battle(a: Unit, b: Unit, *, team_size: int = 1) -> BattleState:
    return BattleState(
        side_a=SideState(units=[a], lives=2),
        side_b=SideState(units=[b], lives=2),
        rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=team_size),
    )


def test_engine_zhuran_fires_after_fire_skill() -> None:
    fire = _skill("炎息", "火", power=10, cost=2)   # 真实火系 battle_ready；低威力不让沙包阵亡
    a = _unit("火花", "火", trait="助燃")
    _equip(a, fire)
    b = _unit("沙包", "草")
    s = _battle(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    gains = {m.stat: m for m in a.trait.gains}
    assert gains["atk"].layers == 2 and gains["sp_atk"].layers == 2


def test_engine_zhuran_not_other_type() -> None:
    grass = _skill("飞叶", "草", cost=2)
    a = _unit("火花", "火", trait="助燃")
    _equip(a, grass)
    b = _unit("沙包", "水")
    s = _battle(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.trait.gains == []


def test_engine_yangxunhuan_heals_after_grass_skill() -> None:
    grass = _skill("飞叶", "草", power=10, cost=2)   # 草打水被抵抗，低威力更稳
    a = _unit("喵喵", "草", hp=100, trait="氧循环")
    a.current_hp = 50
    _equip(a, grass)
    b = _unit("沙包", "水")
    s = _battle(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.current_hp == 60


def test_engine_jinrun_reduces_next_skill_cost() -> None:
    water = _skill("泡沫", "水", power=10, cost=3)   # 真实水系 battle_ready；低威力两回合不阵亡
    a = _unit("水蓝蓝", "水", energy=10, trait="浸润")
    _equip(a, water)
    b = _unit("沙包", "火")
    s = _battle(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.energy == 7                       # 第一发无减益，全价 3
    cost_mods = [m for m in a.trait.gains if m.stat == "energy_cost"]
    assert cost_mods[0].layers == -1           # 浸润：能耗修正 −1
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.energy == 5                       # 第二发 3−1=2


def test_engine_jinrun_opens_energy_gate() -> None:
    water = _skill("泡沫", "水", cost=3)
    a = _unit("水蓝蓝", "水", energy=2, trait="浸润")
    _equip(a, water)
    b = _unit("沙包", "火")
    s = _battle(a, b)
    apply_energy_cost_mod(a, layers=-1, trait=True, source="浸润")   # 能耗 3→2
    assert skill_block_reason(s, a, 0) is None                      # 2 ≥ 2 放行


def test_engine_switch_clears_energy_cost_mods() -> None:
    water = _skill("泡沫", "水", cost=3)
    rules = replace(DEFAULT_RULES, team_size=2)
    a0 = _unit("水蓝蓝", "水", trait="浸润")
    _equip(a0, water)
    a1 = _unit("候补", "普通")
    b0 = _unit("沙包", "火")
    b1 = _unit("沙包2", "火")
    s = BattleState(side_a=SideState(units=[a0, a1], lives=2),
                    side_b=SideState(units=[b0, b1], lives=2),
                    rng=BattleRng(7), rules=rules)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    cost_mods = [m for m in a0.trait.gains if m.stat == "energy_cost"]
    assert cost_mods[0].layers == -1
    events = execute_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert [m for m in a0.trait.gains if m.stat == "energy_cost"] == []   # 非永久随离场清除
    sw = [e for e in events if e["type"] == "switch"][0]
    assert sw["cleared_layers"] == -1               # 清掉的是能耗修正 −1 层
    execute_turn(s, Decision(switch_action(0)), Decision(recharge_action()))
    assert [m for m in a0.trait.gains if m.stat == "energy_cost"] == []   # 清掉的不回来


def test_trait_does_not_break_markov() -> None:
    """特性单位走 clone/step 往返不破坏马尔可夫性：原始 state 不变、特性字段随克隆保留。

    E0 技能全为普通系，助燃（火系）在这里不会触发——本测试验的是「特性字段可随
    to_dict/from_dict 干净往返」这一不改变不变式的底线。特性触发后的状态捕获由
    序列化往返测试覆盖。
    """
    from environment.engine import step

    scratch = _skill("抓挠", "普通", cost=2)   # 真实普通系 battle_ready → clone 可重建
    a = _unit("火花", "火", trait="助燃")
    _equip(a, scratch)
    b = _unit("沙包", "草")
    s = _battle(a, b)
    h = s.state_hash()
    new_s, events = step(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.state_hash() == h and events
    assert new_s.side_a.units[0].trait.name == "助燃"


# ── 白板特性的战斗行为（零效果）──
def test_default_trait_produces_no_effects_in_battle() -> None:
    """装白板的单位打满一回合：无任何特性增益（stat_mods / trait.gains 全空）。"""
    fire = _skill("炎息", "火", power=10, cost=2)
    a = _unit("未实现特性精灵", "火", trait=DEFAULT_TRAIT_NAME)
    _equip(a, fire)
    b = _unit("沙包", "草")
    s = _battle(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.stat_mods == [] and a.trait.gains == []
    assert b.stat_mods == [] and (b.trait.gains if b.trait else []) == []


def test_default_trait_matches_no_trait_behaviour() -> None:
    """白板 vs 无特性实例：战斗行为完全一致（白板只是显式占位）。"""
    def _run(trait_name):
        fire = _skill("炎息", "火", power=10, cost=2)
        a = _unit("甲", "火", trait=trait_name)
        if trait_name is None:
            a.trait = None
        _equip(a, fire)
        b = _unit("乙", "草")
        s = _battle(a, b)
        events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
        return [(e["type"], e.get("damage"), e.get("stat")) for e in events], b.current_hp

    assert _run(DEFAULT_TRAIT_NAME) == _run(None)


def test_real_data_spirit_traits_equipped() -> None:
    """真实数据精灵的特性正确装备：已实现的绑真实特性（迪莫·最好的伙伴等）。"""
    roster = build_roster(
        [TeamPick("迪莫", ["闪光"]), TeamPick("喵喵", ["抓挠"]), TeamPick("火花", ["火苗"])],
        source=DataSource.FULL,
    )
    units = [build_unit(r) for r in roster]
    equipped = {u.name: u.trait.name for u in units}
    assert equipped["迪莫"] == "最好的伙伴"              # 已实现 → 真实特性
    assert equipped["喵喵"] == "氧循环"
    assert equipped["火花"] == "助燃"
    # 迪莫(光)用闪光(光)打 喵喵(草)：eff 0.5 非克制 → 特性不触发 → 零增益
    s = _battle(units[0], units[1])
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert units[0].stat_mods == [] and units[0].trait.gains == []


def test_default_trait_roundtrips() -> None:
    u = build_unit(_spec("茂盛"))                        # 未实现 → 白板
    r = _unit_from_dict(_unit_to_dict(u))
    assert r.trait.name == DEFAULT_TRAIT_NAME


# ── 迪莫「最好的伙伴」（S3）：造成克制伤害后 攻防速+20% 并回复 2 能量 ──
def _dimo_battle(b_type: str):
    """迪莫(光)·最好的伙伴 用 闪光(光) 打 指定系别对手（1v1）。"""
    light = _skill("闪光", "光", power=10, cost=1)   # 真实光系 battle_ready
    a = _unit("迪莫", "光", energy=5, trait="最好的伙伴")
    _equip(a, light)
    b = _unit("靶子", b_type)
    s = _battle(a, b)
    return s, a, b


def test_dimo_trait_fires_on_counter_damage() -> None:
    """造成克制伤害后：攻防速 5 维各 +20%（2 层 pct）+ 回复 2 能量。"""
    s, a, b = _dimo_battle("幽")          # 光 克 幽 → eff 2.0
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    d = [e for e in events if e["type"] == "damage"][0]
    assert d["eff"] == 2.0
    mods = {m.stat: m.layers for m in a.trait.gains}
    assert mods == {"atk": 2, "sp_atk": 2, "def": 2, "sp_def": 2, "speed": 2}
    assert all(m.trait for m in a.trait.gains)      # 特性增益（免疫常规驱散）
    assert a.stat_mods == []                        # 特性增益不污染 stat_mods
    assert a.energy == 5 - 1 + 2                  # 先付能耗 1，特性回 2


def test_dimo_trait_not_on_non_counter() -> None:
    """非克制伤害（eff 0.5）→ 特性不触发。"""
    s, a, _ = _dimo_battle("草")          # 光 打 草 → eff 0.5
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.trait.gains == [] and a.energy == 4   # 只付能耗，不触发


def test_dimo_trait_not_on_status_skill() -> None:
    """非攻击技能（聚能/状态）不可能造成克制伤害 → 不触发。"""
    light = _skill("闪光", "光", power=10, cost=1)
    a = _unit("迪莫", "光", energy=5, trait="最好的伙伴")
    _equip(a, light)
    b = _unit("靶子", "幽")
    s = _battle(a, b)
    execute_turn(s, Decision(recharge_action()), Decision(recharge_action()))   # 迪莫聚能
    assert a.trait.gains == [] and a.energy == 10


def test_dimo_trait_stacks() -> None:
    """多次造成克制伤害 → 攻防速 5 维层数叠加（可叠层，非永久离场清除）。"""
    s, a, b = _dimo_battle("幽")
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert all(m.layers == 4 for m in a.trait.gains)   # 2 次 × 2 层
    # 换人 → 非永久特性增益离场清除
    rules = replace(DEFAULT_RULES, team_size=2)
    bench = _unit("候补", "普通")
    s2 = BattleState(side_a=SideState(units=[a, bench], lives=2),
                     side_b=SideState(units=[b, _unit("靶子2", "幽")], lives=2),
                     rng=BattleRng(7), rules=rules)
    s2.side_a.active = 0
    execute_turn(s2, Decision(switch_action(1)), Decision(recharge_action()))
    assert a.trait.gains == []                   # 非永久特性增益随离场清除
