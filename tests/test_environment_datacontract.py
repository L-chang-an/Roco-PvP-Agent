"""数据协议 v2 施工验收（mydocs/battle_docs.md §B–§D + §F）：Unit 新模型 + 序列化 + 旧快照容错。

覆盖六层：
1. 构造路径：new_battle 生成 unit_id；build_unit 生成 SkillInstance 五要素 + current_skills。
2. roster spec：build_roster 输出含 base_stats（种族值），build_unit 绑定到 Unit.base_stats。
3. stat_mods 合一：能耗减益（stat=energy_cost）与技能 buff 同列表；特性增益进 trait.gains。
4. 序列化：marks / weather / trait.gains 全往返；state_hash 一致。
5. 旧快照容错：v1 energy_cost_mods / statuses 并入 stat_mods。
6. 视图：view 输出 id / base_stats / current_skills；不再有 energy_cost_mods。
"""

from __future__ import annotations

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action
from environment.dataset import DataSource
from environment.engine import execute_turn
from environment.models import (
    BattleRng, BattleState, MarkState, SideState, StatModifier, Unit,
    WeatherState, _unit_from_dict, build_unit, new_battle,
)
from environment.primitives import apply_energy_cost_mod, skill_energy_cost
from environment.rules import DEFAULT_RULES
from environment.teambuilder import TeamPick, build_roster


def _roster() -> list[dict]:
    picks = [
        TeamPick("迪莫", ["闪光", "力量增效"]),
        TeamPick("喵喵", ["抓挠"]),
        TeamPick("火花", ["火苗"]),
    ]
    return build_roster(picks, source=DataSource.FULL)


def _battle() -> BattleState:
    return new_battle(_roster(), _roster(), seed=7, battle_id="dc-1")


# ── 1. 构造路径 ──
def test_new_battle_assigns_unit_id() -> None:
    s = _battle()
    assert [u.id for u in s.side_a.units] == ["a-0-迪莫", "a-1-喵喵", "a-2-火花"]
    assert [u.id for u in s.side_b.units] == ["b-0-迪莫", "b-1-喵喵", "b-2-火花"]
    assert s.unit("a-0-迪莫").name == "迪莫"


def test_build_unit_skill_instance_five_elements() -> None:
    u = build_unit(_roster()[0])
    assert len(u.skills) == 2
    s0 = u.skills[0]
    # 五要素齐备：name / desc / type / kind / energy_cost / power
    assert {s0.name, s0.desc, s0.type, s0.kind, s0.energy_cost, s0.power}
    assert s0.type == "光" and s0.kind in ("物攻", "魔攻", "防御", "状态")
    # current_skills 与 skills 同构（五要素 + cooldown）
    assert len(u.current_skills) == len(u.skills)
    assert u.current_skills[0].name == s0.name
    assert u.current_skills[0].cooldown == 0


def test_roster_spec_includes_base_stats() -> None:
    roster = _roster()
    assert "base_stats" in roster[0]
    # 迪莫种族值（图鉴）：hp 120 / atk 80 / sp_atk 80 / def 105 / sp_def 105 / speed 92
    assert roster[0]["base_stats"] == {"hp": 120, "atk": 80, "sp_atk": 80,
                                       "def": 105, "sp_def": 105, "speed": 92}
    u = build_unit(roster[0])
    assert u.base_stats == roster[0]["base_stats"]


# ── 2. stat_mods 合一 ──
def test_energy_cost_mod_merged_into_stat_mods() -> None:
    u = Unit(name="测试", types=["水"],
             stats={"hp": 100, "atk": 80, "sp_atk": 80, "def": 80, "sp_def": 80, "speed": 80})
    apply_energy_cost_mod(u, layers=-1, source="浸润")
    assert u.stat_mods[0].stat == "energy_cost" and u.stat_mods[0].mode == "flat"
    assert skill_energy_cost(None, "a", u, 3) == 2   # state=None：只算单位自身层数
    # 与属性 buff 同列表（合一）
    u.stat_mods.append(StatModifier(stat="atk", mode="pct", layers=2, source="力量增效"))
    assert len(u.stat_mods) == 2


def test_trait_gains_not_in_stat_mods() -> None:
    """迪莫·最好的伙伴 克制触发 → 增益进 trait.gains，stat_mods 保持干净。"""
    spec = _roster()[0]
    a = build_unit(spec)
    b = build_unit({"name": "靶子", "types": ["幽"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100,
                              "sp_def": 100, "speed": 100},
                    "skills": ["抓挠"], "trait": ""})
    s = BattleState(side_a=SideState(units=[a], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert a.stat_mods == []                 # 特性增益不污染 stat_mods
    gains = {m.stat: m for m in a.trait.gains}
    assert set(gains) >= {"atk", "sp_atk", "def", "sp_def", "speed"}
    assert gains["atk"].trait is True        # 特性增益标记


# ── 3. 序列化：marks / weather / trait.gains 往返 ──
def test_marks_roundtrip() -> None:
    s = _battle()
    s.side_a.positive_marks.append(MarkState(name="攻击印记", layers=1, source="战歌"))
    s.side_a.negative_marks.append(MarkState(name="中毒印记", layers=3, source="毒刃"))
    r = BattleState.from_dict(s.to_dict())
    assert r.side_a.positive_marks[0].name == "攻击印记"
    assert r.side_a.negative_marks[0].layers == 3
    assert r.state_hash() == s.state_hash()


def test_weather_roundtrip() -> None:
    s = _battle()
    s.weather = WeatherState(kind="雨天", turns_left=3, source="祈雨")
    r = BattleState.from_dict(s.to_dict())
    assert r.weather.kind == "雨天" and r.weather.turns_left == 3
    assert r.state_hash() == s.state_hash()


def test_trait_gains_roundtrip() -> None:
    s = _battle()
    u = s.side_a.units[0]
    u.trait.gains.append(StatModifier(stat="atk", mode="pct", layers=2,
                                      permanent=False, trait=True, source="最好的伙伴"))
    r = BattleState.from_dict(s.to_dict())
    assert r.side_a.units[0].trait.gains[0].layers == 2
    assert r.state_hash() == s.state_hash()


def test_full_state_roundtrip_hash_identical() -> None:
    s = _battle()
    # 打一回合制造变化（扣血/揭示/特性触发）后再往返
    rules = replace(DEFAULT_RULES, team_size=1)
    s2 = BattleState(side_a=SideState(units=[build_unit(_roster()[0])], lives=2),
                     side_b=SideState(units=[build_unit({"name": "靶", "types": ["草"],
                                                         "stats": {"hp": 300, "atk": 100, "sp_atk": 100,
                                                                   "def": 100, "sp_def": 100, "speed": 100},
                                                         "skills": ["抓挠"], "trait": ""})], lives=2),
                     rng=BattleRng(7), rules=rules)
    execute_turn(s2, Decision(skill_action(0)), Decision(recharge_action()))
    r = BattleState.from_dict(s2.to_dict())
    assert r.state_hash() == s2.state_hash()


# ── 4. 旧快照容错 ──
def test_legacy_snapshot_energy_cost_mods_and_statuses_merged() -> None:
    d = {
        "name": "旧快照", "types": ["水"],
        "stats": {"hp": 100, "atk": 80, "sp_atk": 80, "def": 80, "sp_def": 80, "speed": 80},
        "skills": [], "nature": "坦率", "bloodline": "", "iv": {},
        "max_hp": 100, "current_hp": 100, "energy": 10, "fainted": False,
        "stat_mods": [],
        "energy_cost_mods": [{"layers": 2, "permanent": False, "trait": False, "source": "旧能耗"}],
        "statuses": {"中毒": {"layers": 1, "source": "毒刃"}},
        "trait": None,
    }
    u = _unit_from_dict(d)
    ec = [m for m in u.stat_mods if m.stat == "energy_cost"]
    assert ec[0].layers == -2                       # 旧 energy_cost_mods 层 2 = 能耗 −2
    st = [m for m in u.stat_mods if m.stat == "中毒"]
    assert st[0].mode == "dot" and st[0].layers == 1


# ── 5. 视图新字段 ──
def test_view_exposes_new_fields() -> None:
    from environment.view import observe

    s = _battle()
    obs = observe(s, "a", "partial")
    me = obs["me"]["units"][0]
    assert "id" in me and "base_stats" in me and "current_skills" in me
    assert me["current_skills"][0]["cooldown"] == 0
    foe = obs["opponent"]["units"][0]
    assert "id" in foe and "energy_cost_mods" not in foe
    assert "current_skills" not in foe               # 敌方隐藏当前技能（未揭示）
