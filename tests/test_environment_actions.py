"""E0b 动作空间测试：合法池 / 唯一谓词 / 校验一致性 / 道具合法性（约 12 组）。

三条断言线都打在「技能可用性」上：`legal_actions` 的池、`skill_block_reason` 的谓词、
`validate_decision` 的校验，三者的看法必须永远一致。
"""

from __future__ import annotations

import pytest

from environment.actions import (Decision, legal_actions, legal_items, recharge_action,
                                 replacement_options, skill_action, skill_block_reason,
                                 switch_action, validate_decision, validate_replacement)
from environment.engine import execute_turn
from environment.models import new_battle
from environment.session import BattleSession

from rosters import RULES_1V1, duel, mirror_pair, strong_weak


def _battle(seed: int = 7, roster=None, rules=RULES_1V1):
    ra, rb = roster or duel()
    return new_battle(ra, rb, seed=seed, rules=rules)


def test_legal_actions_pass_validate() -> None:
    """合法池里的每一条都要过校验。"""
    s = _battle()
    for act in legal_actions(s, "a"):
        assert validate_decision(s, "a", Decision(action=act)) is None


def test_block_reason_is_single_truth() -> None:
    """「池 / 谓词 / 校验」三者对每个槽位可用性的看法必须一致。"""
    s = _battle()
    unit = s.active("a")
    for idx in range(len(unit.skills)):
        reason = skill_block_reason(s, "a", unit, idx)
        in_pool = any(a.get("type") == "skill" and a.get("value") == idx
                      for a in legal_actions(s, "a"))
        verdict = validate_decision(s, "a", Decision(skill_action(idx)))
        assert (reason is None) == in_pool, f"槽位 {idx}：谓词与池不一致"
        assert (reason is None) == (verdict is None), f"槽位 {idx}：谓词与校验不一致"


def test_energy_insufficient_is_illegal_not_downgraded() -> None:
    """能量不足 = 非法，不自动降级为聚能；非法提交不消耗回合、不产生任何事件。"""
    s = _battle()
    unit = s.active("a")
    unit.energy = 0
    expensive = [i for i in range(len(unit.skills)) if unit.skills[i].energy_cost > 0]
    assert expensive
    act = skill_action(expensive[0])
    assert act not in legal_actions(s, "a")
    assert validate_decision(s, "a", Decision(act)) is not None
    sess = BattleSession(s)
    r = sess.submit("a", Decision(act))
    assert r["ok"] is False and "能量不足" in r["error"]
    assert sess.pending_sides() == []          # 没有自动转聚能入缓冲
    assert sess.state.state_hash() == s.state_hash()  # 零状态变更


@pytest.mark.parametrize("idx", [9, None, "0", True])
def test_bad_slot_values_rejected(idx) -> None:
    """槽位越界与错类型（含 True——bool 是 int 的子类）全被拒。"""
    s = _battle()
    assert validate_decision(s, "a", Decision(skill_action(idx))) is not None


def test_switch_rejected_self_and_fainted() -> None:
    s = new_battle(*mirror_pair(), seed=7)   # 3v3，有后备可测阵亡目标
    ss = s.side("a")
    assert validate_decision(s, "a", Decision(switch_action(ss.active))) is not None
    bench = ss.first_living_bench()
    assert bench is not None
    ss.units[bench].fainted = True
    assert validate_decision(s, "a", Decision(switch_action(bench))) is not None


def test_unknown_action_type_rejected() -> None:
    s = _battle()
    assert validate_decision(s, "a", Decision({"type": "teleport", "value": 0})) is not None


def test_after_done_everything_rejected() -> None:
    s = _battle(roster=strong_weak(), rules=RULES_1V1)
    s.winner, s.done = "a", True
    for act in legal_actions(s, "a"):
        assert validate_decision(s, "a", Decision(act)) is not None
    assert validate_decision(s, "b", Decision(recharge_action())) is not None


def test_recharge_legal_at_full_energy_gained_zero() -> None:
    s = _battle()
    assert validate_decision(s, "a", Decision(recharge_action())) is None
    assert validate_decision(s, "b", Decision(recharge_action())) is None
    events = execute_turn(s, Decision(recharge_action()), Decision(recharge_action()))
    rg = [e for e in events if e["type"] == "recharge"]
    assert len(rg) == 2 and all(e["gained"] == 0 and e["energy"] == 10 for e in rg)


def test_item_exhausted_rejected() -> None:
    s = _battle()
    ss = s.side("a")
    assert "草魔法" in legal_items(s, "a")
    ss.item_uses["草魔法"] = 0
    assert "草魔法" not in legal_items(s, "a")
    assert validate_decision(s, "a", Decision(recharge_action(), item="草魔法")) is not None


def test_unknown_item_rejected() -> None:
    s = _battle()
    assert validate_decision(s, "a", Decision(recharge_action(), item="不存在道具")) is not None


def test_action_factories_return_fresh_dicts() -> None:
    """工厂函数返回全新 dict，不设共享模块级常量——下游 mutate 不会全局串味。"""
    assert skill_action(0) is not skill_action(0)
    assert switch_action(1) is not switch_action(1)
    assert recharge_action() is not recharge_action()


def test_legal_pool_has_no_reason_key() -> None:
    """参考项目把被门控的技能塞进池子再标 reason；本项目池里只含合法项，无 reason 键。"""
    s = _battle()
    assert all("reason" not in a for a in legal_actions(s, "a"))


# ── 补位合法池与校验 ─────────────────────────────────────────────────────────
def test_replacement_options_are_living_bench() -> None:
    """补位池 = 存活且非当前在场的槽位。"""
    s = new_battle(*mirror_pair(), seed=7)   # 3v3
    opts = replacement_options(s, "a")
    assert opts == [1, 2]
    ss = s.side("a")
    ss.units[1].fainted = True
    assert replacement_options(s, "a") == [2]


def test_validate_replacement_rejects_bad_choices() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    assert validate_replacement(s, "a", 0) is not None        # 自己（当前在场）
    assert validate_replacement(s, "a", 1) is None            # 合法后备
    assert validate_replacement(s, "a", 9) is not None        # 越界
    assert validate_replacement(s, "a", True) is not None     # bool 错类型
    assert validate_replacement(s, "a", "1") is not None      # 字符串错类型
    s.side("a").units[2].fainted = True
    assert validate_replacement(s, "a", 2) is not None        # 已倒下


def test_validate_replacement_rejects_after_done() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    s.winner, s.done = "a", True
    assert validate_replacement(s, "a", 1) is not None
