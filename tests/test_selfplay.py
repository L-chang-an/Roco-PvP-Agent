"""自博弈编排 + 轨迹落盘（E6）：`rock_pvp_agent/battle/selfplay.py` + `store.py`。

覆盖：自博弈打完 + 重放自检、同 seed 确定性、落盘 + index.jsonl 保序、坏行容错、
battle_id 防穿越、**迷雾隔离**（传给玩家的观测 == E4 白名单）、`run_match` 记录补位。
真实 LLM 玩家是 E6.5；本文件全部用 FakeLLM / 脚本玩家，不碰网络。
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from dataclasses import replace as dr
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from environment.actions import Decision, skill_action
from environment.match import run_match
from environment.players import RandomPlayer, ScriptedPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession

from rock_pvp_agent.battle.player import FakeLLMPlayer, LLMPlayer
from rock_pvp_agent.battle.selfplay import build_player, run_selfplay, run_spectate
from rock_pvp_agent.battle.store import TrajectoryStore
from rock_pvp_agent.config import Settings

from fakes import AlwaysToolLLM
from rosters import spec

# E4 敌方单位白名单（view.py 口径）：其余一律屏蔽。
FOG_WHITELIST = {"name", "types", "hp_pct", "energy", "fainted", "trait", "skills",
                 "stat_mods", "energy_cost_mods"}


# ---------- 自博弈 ----------


def test_run_selfplay_completes() -> None:
    """fake vs fake 自博弈打完：胜者/回合数/重放自检全通过。"""
    out = run_selfplay(seed=7, team_size=3, lives=2, saved_at="t")
    assert out["done"] is True and out["winner"] in ("a", "b")
    assert out["turn_count"] >= 1 and out["turn_count"] == len(out["record"]["turns"])
    assert out["replay_ok"] is True
    assert out["record"]["players"] == {"a": "fake_llm", "b": "fake_llm"}


def test_run_selfplay_deterministic_same_seed() -> None:
    """同 seed 两次 → 逐回合提交序列与 hash 完全一致（FakeLLM 独立 RNG 流 + 引擎纯转移）。"""
    r1 = run_selfplay(seed=7, team_size=3, lives=2, saved_at="t")
    r2 = run_selfplay(seed=7, team_size=3, lives=2, saved_at="t")
    assert r1["record"]["turns"] == r2["record"]["turns"]
    assert r1["winner"] == r2["winner"] and r1["turn_count"] == r2["turn_count"]


# ---------- 落盘 + 索引 ----------


def test_run_selfplay_writes_store_and_index(tmp_path) -> None:
    """两局落盘：记录文件 + index.jsonl 保序追加（先 7 后 8）。"""
    run_selfplay(seed=7, out_dir=tmp_path, battle_id="selfplay-7-1", saved_at="t")
    run_selfplay(seed=8, out_dir=tmp_path, battle_id="selfplay-8-2", saved_at="t")
    assert (tmp_path / "selfplay-7-1.json").exists()
    assert (tmp_path / "selfplay-8-2.json").exists()
    idx = TrajectoryStore(tmp_path).index()
    assert [i["battle_id"] for i in idx] == ["selfplay-7-1", "selfplay-8-2"]   # 保序
    assert idx[0]["players"] == {"a": "fake_llm", "b": "fake_llm"}
    assert idx[0]["turn_count"] >= 1 and idx[0]["winner"] in ("a", "b")


def test_store_skips_bad_index_lines(tmp_path) -> None:
    """index.jsonl 坏行（非 JSON）跳过——坏行容错。"""
    (tmp_path / "index.jsonl").write_text("{broken\n", encoding="utf-8")
    run_selfplay(seed=7, out_dir=tmp_path, battle_id="selfplay-7-1", saved_at="t")
    idx = TrajectoryStore(tmp_path).index()
    assert [i["battle_id"] for i in idx] == ["selfplay-7-1"]


def test_store_rejects_traversal_battle_id(tmp_path) -> None:
    """battle_id 含路径穿越成分 → 拒绝写盘（防逃逸到目录外）。"""
    st = TrajectoryStore(tmp_path)
    with pytest.raises(ValueError, match="穿越"):
        st.save({"version": 1, "battle_id": "../../evil", "saved_at": "t", "seed": 1,
                 "players": {}, "rules": {}, "team_a": [], "team_b": [], "winner": None,
                 "done": False, "turns": []})


# ---------- 迷雾隔离 ----------


class _CapturePlayer:
    """包一层真实玩家并记录收到的观测（迷雾隔离断言用）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.side = inner.side
        self.kind = inner.kind
        self.decide_obs: list[dict] = []
        self.replace_obs: list[dict] = []

    def on_match_start(self, observation: dict) -> None:
        self.inner.on_match_start(observation)

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        self.decide_obs.append(observation)
        return self.inner.decide(observation, legal, items)

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        self.replace_obs.append(observation)
        return self.inner.choose_replacement(observation, bench)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        self.inner.on_turn_result(observation, events)


def test_selfplay_players_get_fogged_views() -> None:
    """E6 迷雾隔离：传给每方玩家的观测 == 白名单（己方全见、敌方屏蔽）。

    钉死 run_match 给玩家的是 `view()` 而非全量 `observe()`——E6.5 真实 LLM 接入即自动得正确口径。
    """
    cap_a = _CapturePlayer(FakeLLMPlayer("a", seed=8))
    cap_b = _CapturePlayer(FakeLLMPlayer("b", seed=9))
    run_selfplay(seed=7, team_size=3, lives=2, players={"a": cap_a, "b": cap_b}, saved_at="t")
    assert cap_a.decide_obs and cap_b.decide_obs            # 确实收到观测
    for cap in (cap_a, cap_b):
        for obs in cap.decide_obs:
            for u in obs["me"]["units"]:                    # 己方全见
                assert "current_hp" in u and "max_hp" in u and "stats" in u
            for u in obs["opponent"]["units"]:              # 敌方白名单（精确键集）
                assert set(u.keys()) == FOG_WHITELIST, f"{cap.side} 敌方泄漏：{sorted(u)}"
                assert "hp_pct" in u and "current_hp" not in u and "max_hp" not in u
                assert "stats" not in u and "nature" not in u and "iv" not in u


# ---------- run_match 记录补位 ----------


def test_run_match_records_replacements() -> None:
    """run_match 把补位选择写进 TurnRecord.replace_a/b——轨迹重放的必需输入。"""
    rules = dr(DEFAULT_RULES, team_size=2)
    a = [spec("弱甲", 1, 1, 1, 1, 1, 1, ["撞击"]), spec("弱乙", 500, 100, 100, 100, 100, 100, ["撞击"])]
    b = [spec("强乙", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强丙", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    session = BattleSession.start(a, b, seed=1, rules=rules, battle_id="ko")
    players = {"a": ScriptedPlayer("a", script=[Decision(skill_action(0))]),
               "b": ScriptedPlayer("b", script=[Decision(skill_action(0))])}
    result = run_match(session, players)
    assert result.turns[0].replace_a == 1       # a 首回合被 KO → 补位第一个存活后备（槽位 1）
    assert result.turns[0].replace_b is None    # b 未阵亡


# ---------- selfplay CLI 冒烟 ----------


def test_selfplay_cli_smoke(tmp_path) -> None:
    """`python -m rock_pvp_agent selfplay --games 1 --seed 7`：打一局 + 落盘 + replay=✅。"""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(
        [sys.executable, "-m", "rock_pvp_agent", "selfplay", "--games", "1",
         "--seed", "7", "--out", str(tmp_path)],
        capture_output=True, text=True, cwd=root, env=env, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "game#1 seed=7" in out.stdout and "replay=✅" in out.stdout
    assert (tmp_path / "selfplay-7-1.json").exists()
    assert len(TrajectoryStore(tmp_path).index()) == 1
    # 落盘记录能被 environment.replay 重放
    from environment.replay import replay_record
    rec = json.loads((tmp_path / "selfplay-7-1.json").read_text(encoding="utf-8"))
    assert replay_record(rec)["all_match"]


# ---------- 补充：错误路径 / 边界（钉死行为，兼顾覆盖） ----------


def test_run_selfplay_with_max_turns() -> None:
    """max_turns 覆盖 + saved_at 缺省（_now 时间戳）——超短局仍打完且重放一致。"""
    out = run_selfplay(seed=7, team_size=3, lives=2, max_turns=2)
    assert out["done"] is True and out["replay_ok"] is True
    assert out["turn_count"] <= 2


def test_build_player_kinds() -> None:
    """build_player：random 走独立 RNG 流；未知 kind → ValueError。"""
    p = build_player("a", "random", seed=1)
    assert p.side == "a" and p.kind == "random"
    with pytest.raises(ValueError, match="未知玩家类型"):
        build_player("a", "bogus", seed=1)


def test_store_save_missing_keys_raises(tmp_path) -> None:
    """save 缺必需键 → ValueError（防把残缺记录写进磁盘）。"""
    with pytest.raises(ValueError, match="缺少必需键"):
        TrajectoryStore(tmp_path).save({"battle_id": "x"})


def test_store_index_missing_dir_empty(tmp_path) -> None:
    """index.jsonl 不存在 → 空列表。"""
    assert TrajectoryStore(tmp_path).index() == []


def test_store_index_skips_non_utf8(tmp_path) -> None:
    """index 文件非 UTF-8（读失败）→ 空列表（坏行容错）。"""
    (tmp_path / "index.jsonl").write_bytes(b"\xff\xfe\x00\x01")
    assert TrajectoryStore(tmp_path).index() == []


def test_store_index_skips_blank_lines(tmp_path) -> None:
    """index 空行跳过。"""
    (tmp_path / "index.jsonl").write_text('{"battle_id":"a"}\n\n{"battle_id":"b"}\n',
                                          encoding="utf-8")
    assert [x["battle_id"] for x in TrajectoryStore(tmp_path).index()] == ["a", "b"]


def test_store_append_without_trailing_newline(tmp_path) -> None:
    """追加到无尾换行的 index：自动补换行，两行都读回。"""
    (tmp_path / "index.jsonl").write_text('{"battle_id":"a"}', encoding="utf-8")
    st = TrajectoryStore(tmp_path)
    st.append_index({"battle_id": "b"})
    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines == ['{"battle_id":"a"}', '{"battle_id": "b"}']
    assert len(st.index()) == 2


def test_store_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch) -> None:
    """os.replace 失败 → 异常上抛且临时文件被清理（原子写不留半成品）。"""
    def _boom(src, dst):
        raise OSError("磁盘故障")
    monkeypatch.setattr("os.replace", _boom)
    rec = {"version": 1, "battle_id": "x", "saved_at": "t", "seed": 1,
           "players": {}, "rules": {}, "team_a": [], "team_b": [], "winner": None,
           "done": False, "turns": []}
    with pytest.raises(OSError, match="磁盘故障"):
        TrajectoryStore(tmp_path).save(rec)
    assert list(tmp_path.glob(".trace-*.tmp")) == []


# ---------- E6.5：真实 LLM 玩家接入自博弈 ----------


def _settings() -> Settings:
    return Settings(api_key="", base_url="http://test.invalid", model="test-model", timeout=5.0)


def _recharge_act(tool_name: str) -> AlwaysToolLLM:
    """恒返 battle_act(recharge) 的 fake LLM——每回合都合法，整局可打完。"""
    return AlwaysToolLLM(tool_name=tool_name,
                         args={"action_type": "recharge", "target": None, "item": ""})


def test_run_selfplay_with_llm_players() -> None:
    """双 LLMPlayer（fake LLM 注入）自博弈：打完 + 重放自检 + 记录 kind=llm。"""
    st = _settings()
    players = {
        "a": LLMPlayer("a", settings=st, seed=8, llm=_recharge_act("battle_act_a")),
        "b": LLMPlayer("b", settings=st, seed=9, llm=_recharge_act("battle_act_b")),
    }
    out = run_selfplay(seed=7, team_size=3, lives=2, players=players, saved_at="t")
    assert out["done"] is True and out["winner"] in ("a", "b")
    assert out["replay_ok"] is True
    assert out["record"]["players"] == {"a": "llm", "b": "llm"}


def test_build_player_llm_no_key_degrades() -> None:
    """无 API key 时 `--a llm` 降级假LLM（记录里 kind 如实为 fake_llm，仍能打完）。"""
    p = build_player("a", "llm", seed=1, settings=_settings())
    assert isinstance(p, FakeLLMPlayer) and p.kind == "fake_llm"


def test_build_player_llm_with_key_returns_llm() -> None:
    """有 API key 时 `--a llm` 构造真实 LLMPlayer。"""
    st = Settings(api_key="sk-test", base_url="http://test.invalid", model="test-model", timeout=5.0)
    p = build_player("a", "llm", seed=1, settings=st)
    assert isinstance(p, LLMPlayer) and p.kind == "llm"


class _RecordingActLLM:
    """记录每次 invoke 收到的消息列表，再委托给恒返 recharge 的 fake。"""

    def __init__(self, tool_name: str) -> None:
        self._inner = AlwaysToolLLM(tool_name=tool_name,
                                    args={"action_type": "recharge", "target": None, "item": ""})
        self.history: list[list] = []

    def invoke(self, messages):
        self.history.append(list(messages))
        return self._inner.invoke(messages)


def test_selfplay_llm_history_isolation() -> None:
    """两侧 LLMPlayer 私有 history：a 方收到的提示里绝不含 b 方自己的全量观测（含绝对血量）。

    结构保证：每侧独立 `_history` + 独立 LLM 实例 + `run_match` 只喂各自的 `view()`。
    测试用记录 fake 钉死：b 渲染的「我方全量」文本不会出现在 a 的 history 里。
    """
    st = _settings()
    rec_a, rec_b = _RecordingActLLM("battle_act_a"), _RecordingActLLM("battle_act_b")
    players = {
        "a": LLMPlayer("a", settings=st, seed=8, llm=rec_a),
        "b": LLMPlayer("b", settings=st, seed=9, llm=rec_b),
    }
    run_selfplay(seed=7, team_size=3, lives=2, players=players, saved_at="t")

    def _side_render(rec) -> str:
        for msgs in rec.history:
            for m in msgs:
                if isinstance(m, HumanMessage) and "你是我方" in m.content:
                    return m.content
        raise AssertionError("未找到渲染后的观测消息")

    render_a, render_b = _side_render(rec_a), _side_render(rec_b)
    assert "血量" in render_b                                        # b 自己的全量（绝对血量）
    text_a = "\n".join(m.content for msgs in rec_a.history for m in msgs if hasattr(m, "content"))
    text_b = "\n".join(m.content for msgs in rec_b.history for m in msgs if hasattr(m, "content"))
    assert render_b not in text_a                                     # b 的全量视图未泄漏进 a
    assert render_a not in text_b                                     # a 的全量视图未泄漏进 b


def test_selfplay_cli_llm_no_key_degrades(tmp_path) -> None:
    """CLI `selfplay --a llm --b llm`：无 key 自动降级假LLM，仍打完 + 轨迹可重放。"""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["LLM_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    out = subprocess.run(
        [sys.executable, "-m", "rock_pvp_agent", "selfplay",
         "--a", "llm", "--b", "llm", "--games", "1", "--seed", "7", "--out", str(tmp_path)],
        capture_output=True, text=True, cwd=root, env=env, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "replay=✅" in out.stdout
    idx = TrajectoryStore(tmp_path).index()
    assert idx and idx[0]["players"] == {"a": "fake_llm", "b": "fake_llm"}


# ---------- E7：观战流（人类全局视角，LLM 仍迷雾） ----------


def test_spectate_frames_sequence() -> None:
    """观战流帧序 = meta → state → turn* → done；turn 帧含全局快照 + 全量事件。"""
    frames = list(run_spectate(seed=7, team_size=3, lives=2,
                               a_kind="fake_llm", b_kind="fake_llm", settings=_settings()))
    seq = [f["event"] for f in frames]
    assert seq[0] == "meta" and seq[1] == "state"
    assert "turn" in seq and seq[-1] == "done"
    assert seq.count("turn") >= 1
    meta = frames[0]
    assert meta["players"] == {"a": "fake_llm", "b": "fake_llm"}
    assert meta["team_a"] and meta["team_b"]
    turn = [f for f in frames if f["event"] == "turn"][0]
    assert turn["turn"] >= 1 and turn["events"] and turn["decisions"]["a"]
    # 观战者全局快照：双方都全量（绝对血量 / 六维 / 性格）
    for side in ("a", "b"):
        u = turn["state"][side]["units"][0]
        assert "current_hp" in u and "max_hp" in u and "stats" in u and "nature" in u
    assert frames[-1]["winner"] in ("a", "b")


def test_spectate_players_get_fogged_views() -> None:
    """观战者是全局视角；两个 LLM 玩家仍迷雾（drive_turn 只喂 view() 白名单）。"""
    cap_a = _CapturePlayer(FakeLLMPlayer("a", seed=8))
    cap_b = _CapturePlayer(FakeLLMPlayer("b", seed=9))
    frames = list(run_spectate(seed=7, team_size=3, lives=2,
                               players={"a": cap_a, "b": cap_b}, settings=_settings()))
    # 玩家观测 = 迷雾白名单（无绝对血量/隐藏字段）
    for cap in (cap_a, cap_b):
        assert cap.decide_obs
        for obs in cap.decide_obs:
            for u in obs["opponent"]["units"]:
                assert set(u.keys()) == FOG_WHITELIST, f"{cap.side} 敌方泄漏：{sorted(u)}"
    # 观战帧 = 全局（双方绝对血量）
    turn = [f for f in frames if f["event"] == "turn"][0]
    for side in ("a", "b"):
        u = turn["state"][side]["units"][0]
        assert "current_hp" in u and "max_hp" in u
