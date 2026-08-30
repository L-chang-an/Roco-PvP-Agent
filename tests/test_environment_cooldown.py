"""防御技能冷却测试（2026-08-30 修订）：全防御技 cd=1；冷却衰减只看精灵是否
**完整经历「冷却回合」的两个节点**——「冷却回合开始」节点（释放防御后的下一回合
开始时在场）+「冷却回合结束」节点（经历回合结束 / 换下 / 阵亡等边界），**无需完整
在场持续一回合**，但**两个节点缺一不可**（只经历「结束」节点不衰减）。

规则走查：
- 基础：N 回合防御 → N+1 提交被禁 → N+1 入口递减（开始节点）→ N+2 可用；
- 规则 1：被禁回合换下 → 入口递减先于换人动作（已经历开始节点）→ 换回即解禁；
- 规则 2：释放当回合即离场 → 未经历「开始」节点 → 回场仍被禁；
- 规则 3：阵亡补位 → 只经历「结束」节点、未经历「开始」节点 → 不递减（cd 保持）。
"""

from __future__ import annotations

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action, switch_action
from environment.actions import legal_actions, validate_decision
from environment.engine import apply_replacement, execute_turn, resolve_turn
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.rules import DEFAULT_RULES


def _unit(name: str, skills: list[tuple[str, str, str, int, int]]) -> Unit:
    u = Unit(name=name, types=["普通"],
             stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                    "speed": 100},
             max_hp=300, current_hp=300, energy=10)
    u.current_skills = [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c,
                                      power=p) for n, t, k, c, p in skills]
    return u


def _state(turn: int = 2) -> BattleState:
    a1 = _unit("甲", [("防御", "普通", "防御", 1, 0), ("抓挠", "普通", "物攻", 1, 30)])
    a2 = _unit("乙", [("防御", "普通", "防御", 1, 0)])
    b = _unit("丙", [("抓挠", "普通", "物攻", 1, 30)])
    for i, u in enumerate((a1, a2)):
        u.id = f"a-{i}-{u.name}"
    b.id = "b-0-丙"
    return BattleState(side_a=SideState(units=[a1, a2], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES, turn=turn)


# ── 基础链路 ──
def test_defense_sets_cooldown_on_all_defense_skills() -> None:
    s = _state()
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert any(e["type"] == "cooldown" for e in events)
    for sk in s.active("a").current_skills:
        assert sk.cooldown == (1 if sk.kind == "防御" else 0)
    assert s.side_a.units[1].current_skills[0].cooldown == 0   # 场下不受影响


def test_cooldown_gate_blocks_and_then_releases() -> None:
    s = _state()
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))   # 防御 → cd=1
    # N+1 提交：防御被禁、非防御不受影响
    assert any("冷却中" in (validate_decision(s, "a", Decision(skill_action(0))) or "")
               for _ in [0])
    legal = legal_actions(s, "a")
    assert all(not (a["type"] == "skill" and a["value"] == 0) for a in legal)
    assert validate_decision(s, "a", Decision(skill_action(1))) is None
    # N+1 结算（入口递减：甲已完整经历「冷却回合开始」节点）
    execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    assert s.active("a").current_skills[0].cooldown == 0
    # N+2 可再用防御
    assert validate_decision(s, "a", Decision(skill_action(0))) is None


def test_cooldown_rejected_at_session_submit() -> None:
    from environment.session import BattleSession

    s = _state()
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    session = BattleSession(s)
    res = session.submit("a", Decision(skill_action(0)))
    assert res["ok"] is False and "冷却中" in res["error"]


# ── 规则 1：被禁回合换下 → 入口递减先于换人动作 → 换回即解禁 ──
def test_rule1_switch_out_during_blocked_turn() -> None:
    s = _state()
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))   # N：防御 → cd=1
    # N+1：被禁回合换下（入口递减先于换人动作 → 甲已完整经历「开始」节点 → cd 归零）
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert s.side_a.units[0].current_skills[0].cooldown == 0
    # N+2：换回 → 立即可防御
    resolve_turn(s, Decision(switch_action(0)), Decision(recharge_action()))
    assert s.active("a").name == "甲"
    assert validate_decision(s, "a", Decision(skill_action(0))) is None


# ── 规则 2：场下冷却不递减（释放当回合离场 → 未经历「开始」节点）→ 回场仍被禁 ──
def test_rule2_benched_cooldown_frozen() -> None:
    s = _state()
    # 乙：场下带冷却（释放当回合离场；frozen 字段用 replace 重建）
    bench = s.side_a.units[1]
    bench.current_skills = [replace(sk, cooldown=1) if sk.kind == "防御" else sk
                            for sk in bench.current_skills]
    execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))   # 甲攻击
    assert s.side_a.units[1].current_skills[0].cooldown == 1   # 场下不递减
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))  # 换乙上场
    assert s.active("a").name == "乙"
    assert validate_decision(s, "a", Decision(skill_action(0))) is not None   # 仍被禁
    execute_turn(s, Decision(recharge_action()), Decision(recharge_action()))  # 乙入口递减
    assert validate_decision(s, "a", Decision(skill_action(0))) is None        # 解禁


# ── 规则 3：阵亡补位 → 只经历「结束」节点、未经历「开始」节点 → 不递减 ──
def test_rule3_faint_replacement_no_decrement() -> None:
    s = _state()
    # 乙场下带冷却（模拟释放防御当回合即离场 → 未经历「开始」节点）
    bench = s.side_a.units[1]
    bench.current_skills = [replace(sk, cooldown=1) if sk.kind == "防御" else sk
                            for sk in bench.current_skills]
    s.active("a").fainted = True
    apply_replacement(s, "a", 1)
    assert s.active("a").name == "乙"
    assert s.active("a").current_skills[0].cooldown == 1   # 不递减（未经历「开始」节点）
    assert validate_decision(s, "a", Decision(skill_action(0))) is not None   # 仍被禁


def test_switchout_then_faint_replacement_keeps_cooldown() -> None:
    """x 释放防御当回合离场（未经历「开始」节点）→ y 上场阵亡 → x 补位 → 不递减。"""
    s = _state()
    # 甲（x）：释放防御当回合即离场 → 场下 cd=1（模拟「脱离」，未经历「开始」节点）
    x = s.side_a.units[0]
    x.current_skills = [replace(sk, cooldown=1) if sk.kind == "防御" else sk
                        for sk in x.current_skills]
    # x 离场后乙（y）上场，y 阵亡
    s.side_a.active = 1
    s.active("a").fainted = True
    # 补位：x 重新上场
    apply_replacement(s, "a", 0)
    assert s.active("a").name == "甲"
    assert s.active("a").current_skills[0].cooldown == 1   # 只经历「结束」节点 → 不递减
    assert validate_decision(s, "a", Decision(skill_action(0))) is not None   # 仍被禁


# ── 序列化往返 ──
def test_cooldown_serialization_roundtrip() -> None:
    s = _state()
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    r = BattleState.from_dict(s.to_dict())
    assert r.active("a").current_skills[0].cooldown == 1
    assert r.state_hash() == s.state_hash()
