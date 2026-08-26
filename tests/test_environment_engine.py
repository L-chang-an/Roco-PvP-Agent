"""E0b 回合循环测试：伤害 / 应对三角 / 增益层 / 道具 / 队列 / 终局 / 事件（约 28 组）。

源码扫描类断言钉死两条不变式：
- `current_hp` 的写点只出现在 damage.py 与 models.build_unit；
- 回合号推进语句在 engine.py 里只出现一次。
"""

from __future__ import annotations

import json
import pathlib
import re

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action, switch_action
from environment.damage import apply_heal, apply_hp_loss, compute_damage
from environment.engine import (apply_replacement, check_winner, execute_turn,
                                settle_faints, step)
from environment.models import StatModifier, aggregate_stats, new_battle
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession

from rosters import RULES_1V1, duel, fast_slow, mirror_pair, strong_weak, tanky_pair, spec


def _battle(seed: int = 7, roster=None, rules=RULES_1V1):
    ra, rb = roster or duel()
    return new_battle(ra, rb, seed=seed, rules=rules)


def _solo(a_spec, b_spec, *, rules=RULES_1V1, seed: int = 7):
    return new_battle([a_spec], [b_spec], seed=seed, rules=rules)


# ── 伤害 ────────────────────────────────────────────────────────────────────
def test_damage_neutral_exact() -> None:
    """atk=100/def=100 抓挠1(power 80)：80×0.9 = 72。"""
    s = _solo(spec("攻", 200, 100, 100, 100, 100, 100, ["抓挠1"]),
              spec("守", 200, 100, 100, 100, 100, 100, ["抓挠1"]))
    assert compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0]) == 72


def test_damage_magic_uses_sp_atk_sp_def() -> None:
    """撞击（魔攻）走 sp_atk/sp_def：物理防御再高也没用。"""
    s = _solo(spec("攻", 200, 1, 100, 100, 100, 100, ["撞击"]),   # atk=1，但 spa=100
              spec("守", 200, 1, 1, 999, 50, 100, ["撞击"]))     # def=999 高物防，sp_def=50
    dmg = compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0])
    assert dmg == 108  # (100/50)×60×0.9


def test_damage_power_zero_is_zero() -> None:
    s = _solo(spec("攻", 200, 100, 100, 100, 100, 100, ["防御"]),
              spec("守", 200, 1, 1, 1, 1, 1, ["撞击"]))
    assert compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0]) == 0


def test_damage_single_truncation() -> None:
    """只在出口 int() 一次：int(80×0.9×1.5×0.3) = int(32.4) = 32。"""
    s = _solo(spec("攻", 200, 100, 100, 100, 100, 100, ["抓挠1"]),
              spec("守", 200, 100, 100, 100, 100, 100, ["抓挠1"]))
    dmg = compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0],
                         counter_mult=1.5, reduction=0.7)
    assert dmg == 32
    assert s.rng.calls == 0  # 纯函数：零 RNG


def test_damage_overkill_clamps_and_faints() -> None:
    s = _solo(spec("攻", 200, 100, 100, 100, 100, 100, ["抓挠1"]),
              spec("守", 30, 1, 1, 1, 1, 1, ["撞击"]))
    dmg = compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0])
    loss = apply_hp_loss(s, s.active("b"), dmg, source="测试")
    assert loss.applied == 30 and s.active("b").current_hp == 0 and loss.fainted


def test_source_scan_current_hp_writers() -> None:
    """current_hp 的写点只允许出现在 damage.py 与 models.py（build_unit 初始化）。"""
    env_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "environment"
    writers: list[tuple[str, int, str]] = []
    for py in sorted(env_dir.glob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"current_hp\s*(=|-=|\+=)", line):
                writers.append((py.name, lineno, line.strip()))
    files = {w[0] for w in writers}
    assert files <= {"damage.py", "models.py"}, writers


# ── 应对三角 ────────────────────────────────────────────────────────────────
def test_attack_counters_status_x15() -> None:
    s = _battle()   # a [抓挠1, 加物攻, 防御]，b [撞击1, 加魔攻, 防御]
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(1)))
    d = [e for e in events if e["type"] == "damage"][0]
    assert d["mult"] == 1.5 and d["counter"] == "状态" and d["side"] == "a"


def test_scratch_base_no_counter_bonus() -> None:
    """抓挠（基础款）没有应对子句：对手出状态也不加伤。"""
    a = spec("小火猴", 300, 100, 100, 100, 100, 100, ["抓挠", "撞击1"])
    b = spec("水蓝蓝", 300, 100, 100, 100, 100, 100, ["撞击", "加魔攻"])
    s = new_battle([a], [b], seed=7, rules=RULES_1V1)
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(1)))
    d = [e for e in events if e["type"] == "damage"][0]
    assert d["skill"] == "抓挠" and d["mult"] == 1.0


def test_defense_reduces_attack() -> None:
    s = _battle()
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(2)))  # 抓挠1 vs 防御
    arm = [e for e in events if e["type"] == "reduce_arm" and e["side"] == "b"][0]
    d = [e for e in events if e["type"] == "damage"][0]
    assert arm["armed"] is True and arm["pct"] == 0.7
    assert d["reduced"] == 0.7


def test_defense_vs_status_white_guard_energy_paid() -> None:
    """防御遇状态：armed=False 白防，能量照扣（防御 cost=1）。"""
    s = _battle()
    b_unit = s.active("b")
    events = execute_turn(s, Decision(skill_action(1)), Decision(skill_action(2)))  # 加物攻 vs 防御
    arm = [e for e in events if e["type"] == "reduce_arm" and e["side"] == "b"][0]
    assert arm["armed"] is False
    assert b_unit.energy == 10 - 1  # 防御 cost=1，白防也扣


def test_status_counters_defense_extra_layers() -> None:
    s = _battle()
    events = execute_turn(s, Decision(skill_action(1)), Decision(skill_action(2)))  # 加物攻 vs 防御
    sc = [e for e in events if e["type"] == "stat_change"][0]
    assert sc["layers"] == 11 and sc["counter"] == "防御"   # 9 + 2 应对层


def test_same_category_no_counter() -> None:
    """双方同类（都是攻击）→ 三边都不触发。"""
    s = _battle()
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    d = [e for e in events if e["type"] == "damage"][0]
    assert d["mult"] == 1.0 and d["reduced"] == 0.0 and d["counter"] == ""


def test_counter_not_triggered_on_switch_or_recharge() -> None:
    s = _battle()
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    d = [e for e in events if e["type"] == "damage"][0]
    assert d["mult"] == 1.0 and d["reduced"] == 0.0


# ── 增益层 ──────────────────────────────────────────────────────────────────
def test_stat_first_9_layers() -> None:
    s = _battle()
    events = execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    sc = [e for e in events if e["type"] == "stat_change"][0]
    assert sc["layers"] == 9 and sc["total_layers"] == 9
    assert sc["stat"] == "atk" and sc["mode"] == "pct"


def test_stat_second_accumulates_18() -> None:
    s = _battle()
    execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    events = execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    sc = [e for e in events if e["type"] == "stat_change"][0]
    assert sc["layers"] == 9 and sc["total_layers"] == 18


def test_stat_layer_cap_clamp() -> None:
    s = _battle()
    unit = s.active("a")
    unit.stat_mods.append(StatModifier(stat="atk", mode="pct", layers=150, source="测试"))
    agg = aggregate_stats(unit)
    assert agg["atk"] == int(unit.stats["atk"] * 10.9)   # 150 层夹到 99 → ×10.9
    assert agg["hp"] == unit.stats["hp"]                  # 无关项不变


def test_speed_flat_80() -> None:
    s = _battle()
    unit = s.active("a")   # speed 100
    unit.stat_mods.append(StatModifier(stat="speed", mode="flat", layers=8, source="加速度"))
    assert aggregate_stats(unit)["speed"] == 180   # 100 + 10×8，不是 100×1.8


def test_aggregate_does_not_write_back() -> None:
    s = _battle()
    unit = s.active("a")
    unit.stat_mods.append(StatModifier(stat="atk", mode="pct", layers=9, source="加物攻"))
    before = dict(unit.stats)
    aggregate_stats(unit)
    assert unit.stats == before


def test_switch_clears_nonpermanent_layers() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    a_unit = s.active("a")
    a_unit.stat_mods.append(StatModifier(stat="atk", mode="pct", layers=9, source="加物攻"))
    a_unit.stat_mods.append(StatModifier(stat="def", mode="pct", layers=5,
                                         permanent=True, source="永久"))
    events = execute_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    sw = [e for e in events if e["type"] == "switch"][0]
    assert sw["cleared_layers"] == 9   # 只清非永久
    for u in s.side("a").units:
        if u.name == sw["out"]:
            assert len(u.stat_mods) == 1 and u.stat_mods[0].permanent is True


# ── 道具 ────────────────────────────────────────────────────────────────────
def test_item_priority_99_first() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    events = execute_turn(s, Decision(skill_action(0), item="草魔法"), Decision(skill_action(0)))
    assert events[0]["type"] == "item_use"   # 99 > 0，道具先于所有技能


def test_item_uses_decrement() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    assert s.side("a").item_uses["草魔法"] == 1
    execute_turn(s, Decision(skill_action(0), item="草魔法"), Decision(skill_action(0)))
    assert s.side("a").item_uses["草魔法"] == 0


def test_item_heals_to_full_with_overflow() -> None:
    s = new_battle(*mirror_pair(), seed=7)
    a_unit = s.active("a")
    a_unit.current_hp = a_unit.max_hp - 40
    events = execute_turn(s, Decision(skill_action(0), item="草魔法"), Decision(recharge_action()))
    heal = [e for e in events if e["type"] == "heal"][0]
    assert heal["applied"] == 40
    assert heal["overflow"] == a_unit.max_hp // 2 - 40
    assert heal["hp"] == a_unit.max_hp


def test_heal_cannot_revive_fainted() -> None:
    """已阵亡单位不可回复（applied=0）——E0 没有复活语义。"""
    s = _battle()
    u = s.active("a")
    u.current_hp = 0
    u.fainted = True
    hr = apply_heal(s, u, u.max_hp // 2, source="草魔法")
    assert hr.applied == 0 and hr.overflow == u.max_hp // 2


def test_item_with_switch_heals_leaving_unit() -> None:
    """同回合「换人 + 道具」：道具先结算，治的是离场那只。"""
    s = new_battle(*mirror_pair(), seed=7)
    s.active("a").current_hp = 100
    events = execute_turn(s, Decision(switch_action(1), item="草魔法"), Decision(recharge_action()))
    heal = [e for e in events if e["type"] == "heal"][0]
    sw = [e for e in events if e["type"] == "switch"][0]
    assert heal["unit"] == sw["out"] == "迪莫" and sw["in"] == "小火猴"


# ── 队列 ────────────────────────────────────────────────────────────────────
def test_queue_4_sorted_items_first() -> None:
    """双方各道具+主动作 → 4 条；两个道具都在技能前（各带一条 heal）。"""
    s = new_battle(*mirror_pair(), seed=7)
    events = execute_turn(s, Decision(skill_action(0), item="草魔法"),
                          Decision(skill_action(0), item="草魔法"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "item_use" and kinds[2] == "item_use"   # item_use 与 heal 成对
    assert "damage" not in kinds[:4]                           # 道具（含 heal）都在技能前
    assert s.rng.calls >= 2   # 跨方同速：道具与技能各有一次硬币


def test_same_side_item_before_main() -> None:
    """同方同优先级：道具先于主动作（item_before_main_action=True）。"""
    s = new_battle(*mirror_pair(), seed=7)
    events = execute_turn(s, Decision(switch_action(1), item="草魔法"), Decision(recharge_action()))
    kinds = [(e["type"], e.get("side")) for e in events]
    assert kinds[0] == ("item_use", "a")
    assert kinds[1] == ("heal", "a")
    assert kinds[2] == ("switch", "a")


def test_turn_ends_on_first_faint() -> None:
    """阵亡 → 回合立即结束：剩余队列条目不再结算（连 skipped 都不发）。

    这是交互式补位（判断 9）的引擎侧语义：a 一回合 KO b 的在场，b 的主动作
    （actor 已死）被整体丢弃，不再有「继续结算」或 skipped。
    """
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠1", "加物攻", "防御"]),
         spec("甲2", 300, 100, 100, 100, 100, 100, ["抓挠1"])]
    b = [spec("乙", 30, 1, 1, 1, 1, 50, ["撞击1", "加魔攻", "防御"]),
         spec("乙2", 300, 100, 100, 100, 100, 100, ["撞击1"])]
    s = new_battle(a, b, seed=7, rules=replace(RULES_1V1, team_size=2))
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    types = [e["type"] for e in events]
    # a 快（100>50）先出手一回合 KO 乙；抓挠1 回 1 能量 → energy_gain；b 的主动作被整体丢弃
    assert types == ["damage", "energy_gain", "faint", "life_loss", "replace"]
    assert "skipped" not in types
    assert s.active("b").name == "乙2"   # execute_turn 默认补位第一个存活后备
    assert s.turn == 2 and not s.done


def test_coin_only_on_cross_side_tie() -> None:
    """速度互异 → 从不平手 → 引擎一次 RNG 都不抽。"""
    s = _battle(roster=fast_slow())
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.rng.calls == 0


# ── 终局 ────────────────────────────────────────────────────────────────────
def test_faint_life_loss_replace_order() -> None:
    """阵亡 → faint → life_loss(−1) → 首个存活后备补位，三件事在一处。"""
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻2", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b1 = spec("弱1", 30, 1, 1, 1, 1, 10, ["撞击"])
    b2 = spec("弱2", 200, 1, 1, 1, 1, 10, ["撞击"])
    s = new_battle(a, [b1, b2], seed=7, rules=replace(RULES_1V1, team_size=2))
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    types = [e["type"] for e in events]
    i = types.index("faint")
    assert types[i:i + 3] == ["faint", "life_loss", "replace"]
    assert events[i + 1]["lives_left"] == 1
    assert s.active("b").name == "弱2"   # 已补位


def test_settle_then_replace_lives_drop_once() -> None:
    """settle_faints 只发 faint/life_loss 并返回需要补位的方；apply_replacement 补位
    后再 settle 不再掉命（一次阵亡只掉 1 命）。"""
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻2", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b1 = spec("弱1", 30, 1, 1, 1, 1, 10, ["撞击"])
    b2 = spec("弱2", 200, 1, 1, 1, 1, 10, ["撞击"])
    s = new_battle(a, [b1, b2], seed=7, rules=replace(RULES_1V1, team_size=2))
    lives_before = s.side("b").lives
    s.active("b").fainted = True
    s.active("b").current_hp = 0
    events, need = settle_faints(s)
    assert need == "b" and s.side("b").lives == lives_before - 1
    assert [e["type"] for e in events] == ["faint", "life_loss"]
    apply_replacement(s, "b", 1)
    assert s.active("b").name == "弱2"
    _, need2 = settle_faints(s)   # 补位后的新在场单位没阵亡
    assert need2 is None and s.side("b").lives == lives_before - 1


def test_lives_zero_ends() -> None:
    """命归零 → 终局（默认配置下的唯一常规败因）。"""
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻2", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b1 = spec("弱1", 30, 1, 1, 1, 1, 10, ["撞击"])
    b2 = spec("弱2", 30, 1, 1, 1, 1, 10, ["撞击"])
    s = new_battle(a, [b1, b2], seed=7, rules=replace(RULES_1V1, team_size=2, lives=2))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side("b").lives == 1 and not s.done
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.done and s.winner == "a" and s.side("b").lives == 0


def test_lives5_no_living_ends() -> None:
    """死锁兜底：lives 还有余但无存活单位也终局（默认 3 只/2 命走不到，需 lives=5）。"""
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻2", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻3", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b = [spec("弱1", 30, 1, 1, 1, 1, 10, ["撞击"]),
         spec("弱2", 30, 1, 1, 1, 1, 10, ["撞击"]),
         spec("弱3", 30, 1, 1, 1, 1, 10, ["撞击"])]
    s = new_battle(a, b, seed=7, rules=replace(RULES_1V1, team_size=3, lives=5))
    for _ in range(2):
        execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
        assert not s.done
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.done and s.winner == "a"
    assert s.side("b").lives == 2 > 0 and not s.side("b").has_living()


def test_max_turns_tiebreak() -> None:
    """E4 取消平局：超过回合上限必分胜负（此场景命数/血量和相同 → 随机硬币）。"""
    s = _battle(roster=tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.done and s.winner in ("a", "b")
    be = [e for e in events if e["type"] == "battle_end"][0]
    assert be["side"] == be["winner"] and "超过回合上限" in be["message"]


def test_after_done_error_no_advance() -> None:
    s = _battle(roster=strong_weak(), rules=RULES_1V1)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.done
    h_before = s.state_hash()
    turn_before = s.turn
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert events == [{"type": "error", "side": "", "message": "对局已结束。"}]
    assert s.state_hash() == h_before and s.turn == turn_before


def test_turn_increments_all_three_kinds() -> None:
    """常规 / 决胜 / 超时定胜负三种回合，turn 都恰好 +1。"""
    # 常规
    s = _battle()
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.turn == 2 and not s.done
    # 决胜
    s2 = _battle(roster=strong_weak(), rules=RULES_1V1)
    execute_turn(s2, Decision(skill_action(0)), Decision(recharge_action()))
    assert s2.turn == 2 and s2.done
    # 超时定胜负（E4 取消平局）：命数/血量和相同 → 随机硬币定胜方
    s3 = _battle(roster=tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    execute_turn(s3, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s3.turn == 2 and s3.done and s3.winner in ("a", "b")


def test_source_scan_turn_increment_once() -> None:
    """回合号推进语句在 engine.py 里只出现一次（整个代码库唯一）。"""
    engine_file = pathlib.Path(__file__).resolve().parents[1] / "src" / "environment" / "engine.py"
    count = engine_file.read_text(encoding="utf-8").count("state.turn += 1")
    assert count == 1


def test_step_does_not_mutate_input() -> None:
    """纯转移语义：step 不改入参 state。"""
    s = new_battle(*mirror_pair(), seed=9)
    h = s.state_hash()
    _, events = step(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.state_hash() == h and events


# ── 事件 ────────────────────────────────────────────────────────────────────
def test_all_events_have_type_and_side_in_enum() -> None:
    from environment.events import EVENT_TYPES
    from environment.match import run_match
    from environment.players import RandomPlayer
    session = BattleSession.start(*mirror_pair(), seed=5)
    players = {"a": RandomPlayer("a", seed=6), "b": RandomPlayer("b", seed=7)}
    result = run_match(session, players)
    assert result.turns
    for t in result.turns:
        for e in t.events:
            assert "type" in e and e["type"] in EVENT_TYPES
            assert "side" in e and e["side"] in ("a", "b", "both", "")


def test_events_json_serializable() -> None:
    s = new_battle(*mirror_pair(), seed=5)
    events = execute_turn(s, Decision(skill_action(0), item="草魔法"),
                          Decision(skill_action(0), item="草魔法"))
    json.dumps(events)
