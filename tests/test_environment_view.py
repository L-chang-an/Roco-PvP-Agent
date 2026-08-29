"""E4 迷雾观测测试：view.py（按 viewer 屏蔽）+ visibility.py（事件过滤）。

覆盖：己方全见/敌方按白名单屏蔽（hp_pct 而非绝对、无六维/性格/血脉/IV/增益层/道具次数）；
技能用过才揭示且 `revealed` 进 to_dict/from_dict 往返（马尔可夫——快照回放不丢迷雾）；
`filter_events_for` 各类型变换；未登记事件类型 fail-closed 丢弃；CLI `--viewer` 冒烟。
"""

from __future__ import annotations

import pytest

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import BattleState
from environment.session import BattleSession
from environment.view import observe
from environment.visibility import filter_events_for

from rosters import RULES_1V1, duel, mirror_pair


def _session() -> BattleSession:
    """1v1 对局：a=甲（速100）/ b=乙（速90），三系技能齐全。"""
    return BattleSession.start(*duel(), seed=1, rules=RULES_1V1)


# ── view：己方全见 / 敌方白名单 ───────────────────────────────────────────────
def test_own_full_foe_masked() -> None:
    view = _session().view("a")
    me = view["me"]
    foe = view["opponent"]
    # 己方全量
    assert set(me["units"][0]) >= {"name", "types", "stats", "skills", "nature", "bloodline",
                                   "iv", "max_hp", "current_hp", "energy", "fainted",
                                   "stat_mods", "energy_cost_mods", "trait"}
    assert "item_uses" in me                     # 己方道具可见
    # 敌方只留白名单（E4 修正：增减益可见 → 含 stat_mods / energy_cost_mods）
    assert set(foe["units"][0]) == {"name", "types", "hp_pct", "energy", "fainted",
                                    "trait", "skills", "stat_mods", "energy_cost_mods"}
    assert "item_uses" not in foe                # 敌方道具次数隐藏
    assert foe["lives"] is not None              # 敌方命数可见
    assert foe["active"] is not None             # 敌方在场下标可见


def test_hp_is_percentage_not_absolute() -> None:
    s = _session()
    execute_turn(s.state, Decision(skill_action(0)), Decision(recharge_action()))   # a 打 b
    b_unit = s.state.side("b").active_unit
    foe = observe(s.state, "a", "partial")["opponent"]["units"][0]
    assert "hp_pct" in foe and "current_hp" not in foe and "max_hp" not in foe
    assert foe["hp_pct"] == b_unit.current_hp * 100 // b_unit.max_hp


def test_foe_buffs_visible_in_status_bar() -> None:
    """E4 修正：敌方增减益可见——b 用「加魔攻」后，a 视角能看到 b 的 stat_mods 层数。"""
    s = _session()
    execute_turn(s.state, Decision(recharge_action()), Decision(skill_action(1)))   # b 加魔攻（状态系）
    foe = s.view("a")["opponent"]["units"][0]
    assert foe["stat_mods"], "敌方状态栏应显示增减益"
    assert any(m["stat"] == "sp_atk" and m["layers"] >= 1 for m in foe["stat_mods"])


def test_types_visible_public() -> None:
    """负责人拍板放行：敌方系别（公开图鉴数据）可见。"""
    foe = _session().view("a")["opponent"]["units"][0]
    assert foe["types"] == ["普通"]


def test_skills_hidden_until_revealed() -> None:
    s = _session()
    # 起始：敌方技能全未知
    assert s.view("a")["opponent"]["units"][0]["skills"] == []
    # b 使用技能（拍击）→ a 视角该技能揭示（含详情描述）
    execute_turn(s.state, Decision(recharge_action()), Decision(skill_action(0)))
    foe_skills = s.view("a")["opponent"]["units"][0]["skills"]
    assert [x["name"] for x in foe_skills] == ["拍击"]
    assert foe_skills[0]["power"] > 0 and foe_skills[0]["desc"] and "energy_cost" in foe_skills[0]


def test_revealed_survives_snapshot_roundtrip() -> None:
    """马尔可夫：revealed 进 to_dict/from_dict，快照回放不丢迷雾。"""
    s = _session()
    execute_turn(s.state, Decision(recharge_action()), Decision(skill_action(0)))   # b 揭示技能
    s2 = BattleState.from_dict(s.state.to_dict())
    assert s2.side("b").revealed == s.state.side("b").revealed
    assert observe(s.state, "a", "partial") == observe(s2, "a", "partial")


def test_view_global_is_full_state() -> None:
    s = _session()
    assert observe(s.state, "a", "global") == s.state.to_dict()


def test_view_invalid_viewer_raises() -> None:
    with pytest.raises(ValueError):
        observe(_session().state, "x", "partial")


def test_foe_trait_shows_real_desc() -> None:
    """特性描述：图鉴公开数据（真实特性名+描述，不是白板 default）。"""
    from environment.dataset import DataSource, load_spirits
    s = BattleSession.start(*mirror_pair(), seed=1)      # 真实 E0 精灵
    sp = load_spirits(DataSource.FULL)["迪莫"]
    foe_trait = s.view("a")["opponent"]["units"][0]["trait"]
    assert foe_trait["name"] == sp.trait_name and foe_trait["desc"] == sp.trait_desc


# ── visibility：事件过滤 ────────────────────────────────────────────────────
def test_filter_damage_to_foe_shows_percent() -> None:
    s = _session()
    events = execute_turn(s.state, Decision(skill_action(0)), Decision(recharge_action()))  # a 打 b
    out = filter_events_for("a", events, s.state)
    dmg = [e for e in out if e["type"] == "damage"][0]
    assert "target_hp_pct" in dmg and "target_hp_left" not in dmg      # 敌方绝对血量 → 百分比
    b_unit = s.state.side("b").active_unit
    assert dmg["target_hp_pct"] == b_unit.current_hp * 100 // b_unit.max_hp
    assert "eff" in dmg and "stab" in dmg                               # 系别放行 → 倍率保留


def test_filter_damage_on_self_keeps_absolute() -> None:
    s = _session()
    events = execute_turn(s.state, Decision(recharge_action()), Decision(skill_action(0)))  # b 打 a
    out = filter_events_for("a", events, s.state)
    dmg = [e for e in out if e["type"] == "damage"][0]
    assert "target_hp_left" in dmg and "target_hp_pct" not in dmg       # 己方绝对血量保留


def test_filter_foe_heal_strips_numbers() -> None:
    s = _session()
    events = [{"type": "heal", "side": "b", "unit": "乙", "applied": 50, "overflow": 0,
               "hp": 150, "source": "草魔法"}]
    h = filter_events_for("a", events, s.state)[0]
    assert "applied" not in h and "overflow" not in h and "hp" not in h
    assert h["unit"] == "乙" and h["source"] == "草魔法"                # 来源（技能名）保留


def test_filter_foe_stat_change_visible() -> None:
    """E4 修正：增减益可见 → stat_change 作用于敌方原样保留（状态栏数据）。"""
    s = _session()
    events = [{"type": "stat_change", "side": "b", "unit": "乙", "skill": "加魔攻",
               "stat": "sp_atk", "mode": "pct", "layers": 9, "total_layers": 9, "counter": ""}]
    assert filter_events_for("a", events, s.state) == events


def test_filter_foe_switch_cleared_layers_visible() -> None:
    """E4 修正：增益可见 → 敌方 switch 的 cleared_layers 保留（由可见层数派生）。"""
    s = _session()
    events = [{"type": "switch", "side": "b", "out": "乙", "in": "丙", "cleared_layers": 9}]
    assert filter_events_for("a", events, s.state) == events


def test_filter_foe_item_use_strips_uses_left() -> None:
    s = _session()
    events = [{"type": "item_use", "side": "b", "item": "草魔法", "unit": "乙", "uses_left": 0}]
    h = filter_events_for("a", events, s.state)[0]
    assert "uses_left" not in h and h["item"] == "草魔法"


def test_filter_energy_and_lives_passthrough() -> None:
    """能量/命数/名字在白名单内 → 相关事件原样保留。"""
    s = _session()
    events = [
        {"type": "recharge", "side": "b", "unit": "乙", "gained": 3, "energy": 10},
        {"type": "life_loss", "side": "b", "unit": "乙", "lives_left": 1},
        {"type": "battle_end", "side": "a", "winner": "a", "turn": 9},
    ]
    out = filter_events_for("a", events, s.state)
    assert out == events


def test_filter_unregistered_event_dropped() -> None:
    s = _session()
    with pytest.warns(UserWarning):
        out = filter_events_for("a", [{"type": "mystery_event", "side": "a"}], s.state)
    assert out == []


# ── CLI --viewer 冒烟 ───────────────────────────────────────────────────────
def test_cli_viewer_prints_masked_observation(monkeypatch, capsys) -> None:
    import sys
    from environment.__main__ import main
    monkeypatch.setattr(sys, "argv", ["environment", "battle", "--seed", "7", "--viewer", "a"])
    assert main() == 0
    out = capsys.readouterr().out
    assert "观测（viewer=a" in out
    opp = out.split('"opponent"', 1)[1]          # opponent 块在观测 JSON 末尾
    assert '"hp_pct"' in opp and '"current_hp"' not in opp
