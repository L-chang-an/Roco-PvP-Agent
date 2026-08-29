"""真实 LLM 对战玩家（E6.5）：`LLMPlayer` 单测（fake LLM 鸭子类型注入，零网络）。

覆盖：`battle_act` 拦截为 Decision（工具未真执行——合成的 ToolMessage 可辨）、非法重试 →
兜底、异常直接兜底、未调 battle_act 提示重试、`choose_replacement`、历史可重放（每个 tool_use
都回填 ToolMessage）、迷雾渲染只消费白名单字段、工具名 side 后缀 → 独立实例。

与 `test_selfplay.py` 的分工：这里测 LLMPlayer 自身；那边测双 LLM 自博弈 + 历史隔离 + 无 key 降级。
"""

from __future__ import annotations

import re
from dataclasses import replace as dr

from langchain_core.messages import AIMessage, ToolMessage

from environment.actions import legal_actions
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from rock_pvp_agent.battle.player import LLMPlayer, build_side_tools
from rock_pvp_agent.battle.prompts import (
    _describe_action,
    render_events,
    render_observation,
    render_replacement,
)
from rock_pvp_agent.config import Settings

from fakes import AlwaysToolLLM, ScriptedLLM, tool_call
from rosters import RULES_1V1, duel, spec


def _settings() -> Settings:
    return Settings(api_key="", base_url="http://test.invalid", model="test-model", timeout=5.0)


def _session() -> BattleSession:
    return BattleSession.start(*duel(), seed=1, rules=RULES_1V1)


def _session2() -> BattleSession:
    """2v2 手写阵容（有存活后备，供补位测试）。"""
    rules = dr(DEFAULT_RULES, team_size=2)
    a = [spec("弱甲", 1, 1, 1, 1, 1, 1, ["抓挠"]), spec("弱乙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("强乙", 500, 100, 100, 100, 100, 100, ["抓挠"]),
         spec("强丙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    return BattleSession.start(a, b, seed=1, rules=rules)


def _act_llm(action_type: str, target=None, item: str = "", tool_name: str = "battle_act_a"):
    """恒返一个 battle_act 工具调用的 fake LLM。"""
    return AlwaysToolLLM(tool_name=tool_name,
                         args={"action_type": action_type, "target": target, "item": item})


# ---------- decide：拦截 / 重试 / 兜底 ----------


def test_decide_intercepts_battle_act_into_decision() -> None:
    """battle_act 被拦截为 Decision（合法），工具函数未真执行。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=_act_llm("skill", 0))
    llm.on_match_start(obs)
    dec = llm.decide(obs, legal, items)
    assert dec.action == {"type": "skill", "value": 0} and dec.item == ""
    assert dec.action in legal_actions(sess.state, "a")
    # 拦截而非执行：历史末条 ToolMessage 是合成的「行动已提交。」（真工具会返回「行动已接收。」）
    last = llm._history[-1]
    assert isinstance(last, ToolMessage) and last.content == "行动已提交。"


def test_decide_recharge() -> None:
    """recharge 动作直通（无需 target）。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=_act_llm("recharge"))
    llm.on_match_start(obs)
    assert llm.decide(obs, legal, items).action == {"type": "recharge"}


def test_invalid_retries_then_valid() -> None:
    """非法提交（槽位越界）→ 原因回灌重试 → 第二次合法。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "skill", "target": 99})]),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "skill", "target": 0})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    dec = llm.decide(obs, legal, items)
    assert dec.action == {"type": "skill", "value": 0}
    assert fake.invocations == 2
    # 首次非法的原因已回灌进 ToolMessage
    assert any(m.content and "不在合法动作池" in m.content for m in llm._history if isinstance(m, ToolMessage))


def test_retries_exhausted_falls_back() -> None:
    """重试耗尽 → 随机兜底（Decision 仍合法）。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    fake = ScriptedLLM([AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "skill", "target": 99})])])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=1)
    llm.on_match_start(obs)
    dec = llm.decide(obs, legal, items)
    assert dec.action in legal_actions(sess.state, "a")


def test_exception_falls_back_immediately() -> None:
    """LLM 抛异常不重试 → 随机兜底（宁失败不抛）。"""
    class BoomLLM:
        def invoke(self, messages):
            raise RuntimeError("api down")

    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=BoomLLM())
    llm.on_match_start(obs)
    assert llm.decide(obs, legal, items).action in legal_actions(sess.state, "a")


def test_no_tool_call_retries() -> None:
    """模型未调 battle_act → 提示重试 → 第二轮合法。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    fake = ScriptedLLM([
        AIMessage(content="我在思考，尚未行动。"),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "recharge"})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    assert llm.decide(obs, legal, items).action == {"type": "recharge"}
    assert fake.invocations == 2


# ---------- choose_replacement ----------


def test_choose_replacement() -> None:
    """补位：battle_act(action_type='replace', target=…) → 返回选中槽位。"""
    sess = _session2()
    obs = sess.view("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=_act_llm("replace", 1))
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) == 1


def test_choose_replacement_invalid_retries() -> None:
    """补位目标不在存活后备 → 重试 → 第二次合法。"""
    sess = _session2()
    obs = sess.view("a")
    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": 5})]),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": 1})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) == 1
    assert fake.invocations == 2


# ---------- 历史可重放（网关 400 的坑） ----------


def test_history_replayable_every_tool_use_paired() -> None:
    """每个带 tool_calls 的 AIMessage 之后都有 ToolMessage 覆盖全部 call id。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=_act_llm("recharge"))
    llm.on_match_start(obs)
    llm.decide(obs, legal, items)
    hist = llm._history
    for i, m in enumerate(hist):
        calls = getattr(m, "tool_calls", None) or []
        if not calls:
            continue
        ids = {c.get("id") for c in calls}
        filled: set = set()
        for j in range(i + 1, len(hist)):
            if not isinstance(hist[j], ToolMessage):
                break
            filled.add(hist[j].tool_call_id)
        assert ids <= filled, f"未回填的 tool_call id：{ids - filled}"


def test_kind_and_side() -> None:
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=AlwaysToolLLM())
    assert llm.side == "a" and llm.kind == "llm"


# ---------- 迷雾渲染（第二道信息隔离闸） ----------


def test_render_observation_foe_has_no_absolute_hp() -> None:
    """渲染只消费白名单字段：敌方段无绝对血量（只有 %），我方段有 cur/max。"""
    sess = _session()
    text = render_observation(sess.view("a"), sess.legal_actions("a"), sess.legal_items("a"))
    me_section = text.split("【敌方】")[0]
    foe_section = text.split("【敌方】")[1].split("【本回合合法动作】")[0]
    assert re.search(r"\d+/\d+", me_section) is not None      # 我方有绝对血量
    assert re.search(r"\d+/\d+", foe_section) is None          # 敌方无绝对血量
    assert "%" in foe_section                                   # 敌方血量是百分比


def test_render_events_filtered_damage_shape() -> None:
    """过滤后的 damage 事件（敌方 target_hp_pct）渲染正常。"""
    text = render_events([{"type": "damage", "side": "a", "attacker": "迪莫", "skill": "抓挠",
                           "target": "布布", "damage": 55, "target_hp_pct": 82}])
    assert "剩82%" in text


def test_tool_name_side_suffix_splits_cache_key() -> None:
    """工具名带 side 后缀 → build_chat_llm 缓存键不同 → 两侧独立 LLM 实例。"""
    a, b = build_side_tools("a"), build_side_tools("b")
    assert a[0].name == "battle_act_a" and b[0].name == "battle_act_b"
    assert a[0].name != b[0].name


def test_battle_act_tool_standalone_sentinel() -> None:
    """battle_act 工具函数体（LLMPlayer 拦截从不调用它）独立可调用，返回哨兵文案。"""
    assert build_side_tools("a")[0].invoke({"action_type": "skill", "target": 0}) == "行动已接收。"


def test_fake_llm_summarize_unknown_action() -> None:
    """_summarize_decision 兜底分支（未知 action type）——防御性文案。"""
    from environment.actions import Decision
    from rock_pvp_agent.battle.player import _summarize_decision
    assert "行动 banana" in _summarize_decision(Decision(action={"type": "banana"}))


# ---------- 边界：未知工具 / 多余调用 / 重试耗尽 / 解析异常 ----------


def test_decide_unknown_and_extra_tool_calls() -> None:
    """未知工具调用 → 提示重试；一个响应里多余的 battle_act → 忽略（取第一个）。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("some_other_tool", {"x": 1})]),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "recharge"}),
                                          tool_call("battle_act_a", {"action_type": "skill", "target": 0})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    dec = llm.decide(obs, legal, items)
    assert dec.action == {"type": "recharge"}          # 取第一个 battle_act
    assert fake.invocations == 2                       # 未知工具触发一次重试


def test_decide_retries_exhausted_loop_falls_back() -> None:
    """重试循环自然耗尽（非异常）→ 随机兜底仍合法。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "skill", "target": 99})])
        for _ in range(2)
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=1)
    llm.on_match_start(obs)
    assert llm.decide(obs, legal, items).action in legal_actions(sess.state, "a")


def test_parse_decision_edge_cases() -> None:
    """_parse_decision：字符串槽位 / 未知 action_type / 非法道具 → 全部拒；recharge 免 target → 合法。"""
    sess = _session()
    obs, legal, items = sess.view("a"), sess.legal_actions("a"), sess.legal_items("a")
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=AlwaysToolLLM())
    llm.on_match_start(obs)
    assert llm._parse_decision({"args": {"action_type": "skill", "target": "abc"}}, legal, items)[0] is None
    assert llm._parse_decision({"args": {"action_type": "banana", "target": 0}}, legal, items)[0] is None
    assert llm._parse_decision({"args": {"action_type": "recharge", "item": "不存在"}}, legal, items)[0] is None
    dec, reason = llm._parse_decision({"args": {"action_type": "recharge"}}, legal, items)
    assert dec is not None and reason is None and dec.action == {"type": "recharge"}


def test_choose_replacement_edge_paths() -> None:
    """补位：异常兜底 / 未知工具重试 / 目标非整数 / 多余调用忽略 / 重试耗尽兜底。"""
    sess = _session2()
    obs = sess.view("a")

    class BoomLLM:
        def invoke(self, messages):
            raise RuntimeError("down")

    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=BoomLLM())
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) in (0, 1)                      # 异常直接兜底

    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("other_tool", {})]),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": 1})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) == 1 and fake.invocations == 2  # 未知工具 → 重试

    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": "abc"})]),
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": 1})]),
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) == 1                            # 非整数目标 → 重试

    fake = ScriptedLLM([AIMessage(content="", tool_calls=[
        tool_call("battle_act_a", {"action_type": "replace", "target": 1}),
        tool_call("battle_act_a", {"action_type": "replace", "target": 0}),
    ])])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=2)
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) == 1                            # 多余调用忽略

    fake = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("battle_act_a", {"action_type": "replace", "target": 9})])
        for _ in range(2)
    ])
    llm = LLMPlayer("a", settings=_settings(), seed=1, llm=fake, max_retries=1)
    llm.on_match_start(obs)
    assert llm.choose_replacement(obs, [0, 1]) in (0, 1)                       # 重试耗尽兜底


# ---------- 渲染全分支（增减益 pct/flat/负层/未知键 / 阵亡跳过 / 事件全集） ----------


def test_render_all_branches() -> None:
    """渲染全分支：增减益三种、能耗减益、阵亡跳过、已揭示技能、空合法池、_describe_action 兜底、
    补位带增减益、全部事件类型。"""
    sess = _session2()
    obs = sess.view("a")
    me, foe = obs["me"], obs["opponent"]
    me["units"][0]["stat_mods"] = [
        {"stat": "atk", "mode": "pct", "layers": 10},
        {"stat": "speed", "mode": "flat", "layers": -2},
        {"stat": "weird", "mode": "pct", "layers": 3},
    ]
    me["units"][0]["energy_cost_mods"] = [{"layers": 1, "permanent": False, "trait": False, "source": "x"}]
    me["units"][1]["fainted"] = True
    foe["units"][0]["skills"] = [{"name": "抓挠", "type": "普通", "energy_cost": 3, "desc": "造成物理伤害。"}]
    foe["units"][0]["stat_mods"] = [{"stat": "def", "mode": "pct", "layers": 5}]
    foe["units"][1]["fainted"] = True

    text = render_observation(obs, [], ["草魔法"])        # legal=[] → 「（无）」分支
    assert "（无）" in text and "草魔法" in text
    assert "物攻+100%" in text and "速度-20" in text and "weird+30%" in text   # pct/flat/负层/未知键
    assert "能耗减益 1 条" in text                                               # ecm 分支
    assert "已见技能" in text and "抓挠" in text and "增减益 物防+50%" in text   # 敌方揭示技能/增减益
    assert "（*在场）" in text                                                    # 在场标记（阵亡被跳过）

    # _describe_action 兜底分支
    assert "技能（槽位 9）" in _describe_action({"type": "skill", "value": 9}, obs)
    assert "换人（槽位 9）" in _describe_action({"type": "switch", "value": 9}, obs)
    assert "banana（None）" in _describe_action({"type": "banana"}, obs)

    # 补位渲染（带增减益的后备）
    rp = render_replacement(obs, [0])
    assert "槽位 0" in rp and "增减益" in rp

    # 空事件
    assert render_events([]) == "（本回合无事件）"

    # 全部事件类型
    ev = render_events([
        {"type": "damage", "side": "a", "attacker": "迪莫", "skill": "抓挠", "target": "布布",
         "damage": 55, "target_hp_pct": 82},
        {"type": "damage", "side": "a", "attacker": "迪莫", "skill": "抓挠", "target": "布布",
         "damage": 20, "target_hp_left": 50},
        {"type": "heal", "side": "a", "unit": "迪莫", "applied": 30},
        {"type": "stat_change", "side": "a", "unit": "迪莫", "stat": "atk", "mode": "pct", "layers": 5},
        {"type": "recharge", "side": "a", "unit": "迪莫", "gained": 5},
        {"type": "switch", "side": "a", "out": "迪莫", "in": "喵喵"},
        {"type": "replace", "side": "b", "out": "布布", "in": "火苗"},
        {"type": "faint", "side": "b", "unit": "布布"},
        {"type": "life_loss", "side": "b", "unit": "布布", "lives_left": 1},
        {"type": "item_use", "side": "a", "item": "草魔法"},
        {"type": "reduce_arm", "side": "a", "pct": 0.7},
        {"type": "battle_end", "side": "a", "winner": "a", "message": "命数 2 对 0"},
        {"type": "skipped", "side": "b", "unit": "布布", "reason": "已被击倒"},
        {"type": "error", "side": "", "message": "对局已结束。"},
        {"type": "mystery", "side": "a"},
    ])
    for needle in ("剩82%", "剩50", "回复 30", "物攻+50%", "聚能 +5", "换人 迪莫 → 喵喵",
                   "补位 布布 → 火苗", "倒下", "命数-1（剩 1）", "草魔法", "武装防御",
                   "对局结束：命数 2 对 0", "已被击倒", "错误：对局已结束。", "mystery"):
        assert needle in ev, needle
