"""战斗 REST 契约测试（/api/battle/*，见 ui/routes_battle.py + ui/battle.py）。

覆盖：开局校验闸门（非法队伍/非法配置/extra 字段）、快照迷雾口径、出招/补位流程、
非法 act/replace 拒绝、轨迹持久化 + **重放逐回合 state_hash 一致**（马尔可夫）、
坏文件跳过。落盘目录 monkeypatch 到临时目录，测试零副作用。
"""

from __future__ import annotations

import dataclasses
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import ui.routes_battle as routes_battle  # noqa: E402
from environment.actions import Decision, skill_action  # noqa: E402
from environment.dataset import DataSource, load_spirits  # noqa: E402
from environment.players import ScriptedPlayer  # noqa: E402
from environment.rules import DEFAULT_RULES  # noqa: E402
from environment.session import BattleSession  # noqa: E402
from environment.teambuilder import learnable_skills  # noqa: E402
from rock_pvp_agent.config import Settings  # noqa: E402
from ui.battle import BattleController  # noqa: E402
from ui.server import create_chat_app  # noqa: E402

from rosters import spec  # noqa: E402


def _settings() -> Settings:
    return Settings(api_key="", base_url="http://test.invalid", model="test-model", timeout=5.0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试一个独立应用 + 独立对局落盘目录（零副作用）。"""
    monkeypatch.setattr(routes_battle, "BATTLES_DIR", tmp_path)
    app = create_chat_app(_settings())
    return TestClient(app)


# ---------- 组队数据构造（真实数据，与前端同一来源） ----------


def _ready(spirit: str) -> list[str]:
    return list(learnable_skills(spirit, "", DataSource.VALID))


def _pick(spirit: str, n: int = 2, bloodline: str = "") -> dict:
    return {"spirit": spirit, "skills": _ready(spirit)[:n],
            "bloodline": bloodline, "nature": "坦率", "iv": {}}


def _valid_team() -> list[dict]:
    return [_pick("迪莫", 2), _pick("喵喵", 1), _pick("火花", 1)]


def _start(client, **kw) -> dict:
    body = {"team_a": _valid_team(), "team_size": 3, "lives": 2, "seed": 42, **kw}
    r = client.post("/api/battle/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _starter(client, bid: str, bench_idx: int = 0) -> dict:
    """第 0 回合：选首发（默认首只），返回出招阶段快照。"""
    r = client.post(f"/api/battle/{bid}/starter", json={"bench_idx": bench_idx})
    assert r.status_code == 200, r.text
    return r.json()


def _start_ready(client, **kw) -> dict:
    """开局 + 选首发 → 进入出招阶段（多数测试的起点）。"""
    b = _start(client, **kw)
    return _starter(client, b["battle_id"])


def _drive_to_done(client, bid: str) -> dict:
    """用「合法动作第一个 + 自动补位」把一局打到 done，返回终局快照。"""
    st = client.get(f"/api/battle/{bid}").json()
    if st["phase"] == "starter":                 # 第 0 回合：先选首发
        _starter(client, bid)
    for _ in range(40):
        st = client.get(f"/api/battle/{bid}").json()
        if st["done"]:
            return st
        r = client.post(f"/api/battle/{bid}/act", json={"action": st["legal"][0], "item": ""}).json()
        if not r["ok"]:
            raise AssertionError(f"act 被拒：{r}")
        if r["need_replacement"]:
            rep = client.get(f"/api/battle/{bid}").json()
            me = rep["observation"]["me"]
            bench = [i for i, u in enumerate(me["units"]) if i != me["active"] and not u["fainted"]]
            rr = client.post(f"/api/battle/{bid}/replace", json={"bench_idx": bench[0]}).json()
            assert rr["ok"], rr
    raise AssertionError("40 次 act 未打完")


# ---------- 开局 ----------


def test_start_ok_and_snapshot_masked(client):
    b = _start(client)
    assert b["ok"] and b["phase"] == "starter" and b["opponent"] == "fake_llm"
    assert b["seed"] == 42 and b["battle_id"].startswith("battle_")
    assert b["starter_options"] == [0, 1, 2]                   # 第 0 回合：三个存活单位可选
    obs = b["observation"]
    # 己方全量 / 敌方白名单（E4 修正：增减益可见 → 含 stat_mods）
    assert set(obs["me"]["units"][0]) >= {"stats", "max_hp", "current_hp", "skills", "nature"}
    assert set(obs["opponent"]["units"][0]) == {"id", "name", "types", "hp_pct", "energy",
                                                "fainted", "trait", "skills", "stat_mods"}
    assert obs["opponent"]["units"][0]["skills"] == []          # 敌方技能起始未知
    # 选首发后进入出招阶段
    b2 = _starter(client, b["battle_id"])
    assert b2["phase"] == "decision" and b2["legal"] and b2["legal_items"] == ["草魔法"]


def test_start_uses_fixed_preset_when_team_b_missing(client):
    b = _start(client, team_b=None)
    assert len(b["observation"]["opponent"]["units"]) == 3
    assert all(u["skills"] == [] for u in b["observation"]["opponent"]["units"])


def test_start_accepts_explicit_team_b(client):
    b = _start(client, team_b=_valid_team())
    foe_names = [u["name"] for u in b["observation"]["opponent"]["units"]]
    assert set(foe_names) == {"迪莫", "喵喵", "火花"}


def test_start_rejects_invalid_team(client):
    team = _valid_team()
    team[0]["skills"] = ["不存在的技能"]
    r = client.post("/api/battle/start", json={"team_a": team, "team_size": 3})
    assert r.status_code == 422 and "errors" in r.json()["detail"]


def test_start_rejects_bad_config(client):
    team = _valid_team()
    assert client.post("/api/battle/start", json={"team_a": team, "team_size": 7}).status_code == 422
    assert client.post("/api/battle/start", json={"team_a": team, "team_size": 3, "lives": 3}).status_code == 422
    assert client.post("/api/battle/start", json={"team_a": team, "team_size": 3, "max_turns": 0}).status_code == 422
    assert client.post("/api/battle/start", json={"team_a": team, "team_size": 3,
                                                  "opponent": "llm"}).status_code == 422


def test_start_extra_field_422(client):
    r = client.post("/api/battle/start", json={"team_a": _valid_team(), "team_size": 3, "hack": 1})
    assert r.status_code == 422


def test_snapshot_unknown_battle_404(client):
    assert client.get("/api/battle/nope").status_code == 404


# ---------- 出招 / 补位 ----------


def test_act_advances_turn(client):
    b = _start_ready(client)
    bid = b["battle_id"]
    r = client.post(f"/api/battle/{bid}/act", json={"action": b["legal"][0], "item": ""}).json()
    assert r["ok"] and r["turn"] > 1 and isinstance(r["events"], list)
    assert set(r["observation"]["opponent"]["units"][0]) == {"id", "name", "types", "hp_pct",
                                                             "energy", "fainted", "trait", "skills",
                                                             "stat_mods"}


def test_act_invalid_rejected_no_advance(client):
    b = _start_ready(client)
    bid = b["battle_id"]
    r = client.post(f"/api/battle/{bid}/act", json={"action": {"type": "skill", "value": 99}}).json()
    assert r["ok"] is False and "越界" in r["error"]
    assert client.get(f"/api/battle/{bid}").json()["turn"] == 1     # 零状态变更


def test_replace_without_pending_rejected(client):
    b = _start(client)
    r = client.post(f"/api/battle/{b['battle_id']}/replace", json={"bench_idx": 1}).json()
    assert r["ok"] is False and "等待补位" in r["error"]


def test_controller_replacement_flow():
    """补位流程（确定性）：对手 ScriptedPlayer 首回合 KO 人类在场 → 暂停等补位 → 补位完成。"""
    rules = dataclasses.replace(DEFAULT_RULES, team_size=2)
    a = [spec("弱甲", 1, 1, 1, 1, 1, 1, ["抓挠"]), spec("弱乙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("强乙", 500, 100, 100, 100, 100, 100, ["抓挠"]),
         spec("强丙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    session = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    ctrl = BattleController("t", session, seed=1, opponent="fake_llm",
                            team_a=[], team_b=[], rules=rules, saved_at="x",
                            player=ScriptedPlayer("b", script=[Decision(skill_action(0))]))
    ctrl.choose_starter(0)
    out = ctrl.act(skill_action(0))
    assert out["need_replacement"] == "a" and out["phase"] == "replacement"
    assert out["legal"] == []                                     # 补位等待期不给出招池
    rep = ctrl.replace(1)
    assert rep["ok"] and rep["phase"] == "decision" and not rep["done"]
    rec = ctrl.record()["turns"][0]
    assert rec["turn"] == 1 and rec["replace_a"] == 1 and rec["decision_a"] == {"action": skill_action(0), "item": "", "item_arg": ""}


def test_controller_replace_returns_delta_events():
    """补位续步只返回**新增**事件（不重复出招步已展示的），完整轨迹累积在记录里。

    出招步与续步 `events_turn` 同回合 → 前端不重复插「第 N 回合」头；事件则按步分割追加。
    """
    rules = dataclasses.replace(DEFAULT_RULES, team_size=2)
    a = [spec("弱甲", 1, 1, 1, 1, 1, 1, ["抓挠"]), spec("弱乙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("强乙", 500, 100, 100, 100, 100, 100, ["抓挠"]),
         spec("强丙", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    session = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    ctrl = BattleController("t", session, seed=1, opponent="fake_llm",
                            team_a=[], team_b=[], rules=rules, saved_at="x",
                            player=ScriptedPlayer("b", script=[Decision(skill_action(0))]))
    ctrl.choose_starter(0)
    out = ctrl.act(skill_action(0))
    assert out["need_replacement"] == "a" and out["events_turn"] == 1
    rep = ctrl.replace(1)
    assert rep["events_turn"] == out["events_turn"]                # 同回合 → 前端不重复插头
    assert rep["events"] and all(e not in out["events"] for e in rep["events"])   # 增量，不重复
    rec_events = ctrl.record()["turns"][0]["events"]               # 记录 = 出招步 + 续步全量
    assert len(rec_events) == len(out["events"]) + len(rep["events"])


def test_replacement_flow_via_api(client):
    """自然补位（seed 3：人类首回合阵亡）→ API replace 推进。"""
    team = [_pick("火花", 1), _pick("喵喵", 1), _pick("迪莫", 1)]
    b = client.post("/api/battle/start", json={"team_a": team, "team_size": 3,
                                               "lives": 2, "seed": 3}).json()
    bid = b["battle_id"]
    _starter(client, bid)                      # 第 0 回合：选首发
    seen = False
    for _ in range(30):
        st = client.get(f"/api/battle/{bid}").json()
        if st["done"]:
            break
        r = client.post(f"/api/battle/{bid}/act", json={"action": st["legal"][0], "item": ""}).json()
        if r["need_replacement"]:
            seen = True
            break
    assert seen, "seed 3 应触发补位"
    rep = client.get(f"/api/battle/{bid}").json()
    me = rep["observation"]["me"]
    bench = [i for i, u in enumerate(me["units"]) if i != me["active"] and not u["fainted"]]
    rr = client.post(f"/api/battle/{bid}/replace", json={"bench_idx": bench[0]}).json()
    assert rr["ok"] and rr["phase"] != "replacement"


# ---------- 持久化 + 重放 ----------


def test_persist_and_replay_hashes_match(client, tmp_path):
    b = _start(client)
    bid = b["battle_id"]
    done = _drive_to_done(client, bid)
    assert done["done"] and done["winner"] in ("a", "b")
    # 结束后 act → 拒绝
    r = client.post(f"/api/battle/{bid}/act", json={"action": {"type": "recharge"}}).json()
    assert r["ok"] is False and "已结束" in r["error"]

    saved = client.get("/api/battle/saved").json()["battles"]
    assert len(saved) == 1
    f = tmp_path / saved[0]["name"]
    assert f.exists() and saved[0]["winner"] == done["winner"]
    # 重放：逐回合 state_hash 一致
    rr = client.post("/api/battle/replay", json={"path": saved[0]["name"]}).json()
    assert rr["ok"] and rr["all_match"] and len(rr["turns"]) >= 1
    # load 往返
    ld = client.get("/api/battle/load", params={"path": saved[0]["name"]}).json()
    assert ld["battle_id"] == bid and len(ld["turns"]) == len(rr["turns"])


def _normalize_loaded_pick(p: dict) -> dict:
    """复刻前端 battle.js normalizePick：把 /api/team/load 的 v2 富化技能 {name,type,desc}
    归一为字符串名数组（/api/battle/start 只吃最简形状）。"""
    return {
        "spirit": p["spirit"],
        "skills": [(x if isinstance(x, str) else x.get("name", "")) for x in (p.get("skills") or [])],
        "bloodline": p.get("bloodline", ""),
        "nature": p.get("nature", "坦率"),
        "iv": p.get("iv", {}),
    }


def test_load_saved_team_then_start(client, tmp_path):
    """组队页保存（v2 富化）→ 加载 → 归一化 → 开局：完整「加载已存队伍开战」链路。

    回归：战斗页直接拿 /api/team/load 的富化结果开局会 422（skills 是 {name,type,desc} 列表、
    trait 是 extra 字段）——前端必须归一化，本测试钉死这条契约。
    """
    target = str(tmp_path / "已存队.json")
    r = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": target})
    assert r.status_code == 200
    loaded = client.get("/api/team/load", params={"path": target}).json()
    # 富化确认：技能是 {name,type,desc}、带 trait 字段
    assert isinstance(loaded["team"][0]["skills"][0], dict)
    picks = [_normalize_loaded_pick(p) for p in loaded["team"]]
    b = client.post("/api/battle/start", json={"team_a": picks, "team_size": 3, "lives": 2,
                                                "seed": 42}).json()
    assert b["ok"] and b["battle_id"]
    assert [u["name"] for u in b["observation"]["me"]["units"]] == ["迪莫", "喵喵", "火花"]


def test_start_records_items_and_replay(client, tmp_path):
    """非默认道具栏（首领进化）进轨迹记录 → 重放逐回合仍一致（items 闭环）。"""
    b = client.post("/api/battle/start", json={"team_a": _valid_team(), "team_size": 3,
                                                "lives": 2, "seed": 42,
                                                "items_a": ["首领进化"]}).json()
    bid = b["battle_id"]
    done = _drive_to_done(client, bid)
    assert done["done"]
    saved = client.get("/api/battle/saved").json()["battles"]
    raw = json.loads((tmp_path / saved[0]["name"]).read_text(encoding="utf-8"))
    assert raw["items_a"] == ["首领进化"]          # 道具栏进记录
    rr = client.post("/api/battle/replay", json={"path": saved[0]["name"]}).json()
    assert rr["ok"] and rr["all_match"]


def test_boss_item_via_ui(client):
    """首领进化道具经 Web UI 可达：魔力猫（boss 上一阶）+ item_arg 分支 → 原地进化。"""
    team = [_pick("魔力猫", 1, bloodline="首领"), _pick("迪莫", 1), _pick("火花", 1)]
    b = client.post("/api/battle/start", json={"team_a": team, "team_size": 3, "lives": 2,
                                                "seed": 7, "items_a": ["首领进化"]}).json()
    bid = b["battle_id"]
    b = _starter(client, bid)                    # 选首发（魔力猫首发）
    # 出招阶段快照带首领化分支列表（前端据此渲染分支选择）
    assert set(b["boss_options"]) == {"叶冕魔力猫", "武斗酷猫"}
    r = client.post(f"/api/battle/{bid}/act", json={"action": b["legal"][0],
                                                     "item": "首领进化",
                                                     "item_arg": "武斗酷猫"}).json()
    assert r["ok"], r
    me = r["observation"]["me"]
    assert me["units"][me["active"]]["name"] == "武斗酷猫"   # 原地进化，unit_id 不变、名字改变


def test_boss_item_multi_branch_requires_item_arg(client):
    """多分支首领化缺 item_arg → 拒绝（零状态变更，报「首领化分支」）。"""
    team = [_pick("魔力猫", 1, bloodline="首领"), _pick("迪莫", 1), _pick("火花", 1)]
    b = client.post("/api/battle/start", json={"team_a": team, "team_size": 3, "lives": 2,
                                                "seed": 7, "items_a": ["首领进化"]}).json()
    b = _starter(client, b["battle_id"])          # 选首发（魔力猫首发）
    r = client.post(f"/api/battle/{b['battle_id']}/act",
                    json={"action": b["legal"][0], "item": "首领进化"}).json()
    assert r["ok"] is False and "首领化分支" in r["error"]
    assert client.get(f"/api/battle/{b['battle_id']}").json()["turn"] == 1   # 零状态变更


def test_boss_options_empty_for_non_boss_active(client):
    """非首领血脉/非 boss 上一阶的在场精灵 → boss_options 空（前端据此禁用首领进化）。"""
    team = [_pick("喵喵", 1), _pick("迪莫", 1), _pick("火花", 1)]
    b = client.post("/api/battle/start", json={"team_a": team, "team_size": 3, "lives": 2,
                                                "seed": 42, "items_a": ["首领进化"]}).json()
    b = _starter(client, b["battle_id"])          # 喵喵首发（非 boss 上一阶）
    assert b["boss_options"] == []


def test_saved_skips_corrupt_files(client, tmp_path):
    _start(client)
    tmp_path.joinpath("bad.json").write_text("{ 不是 json", encoding="utf-8")
    tmp_path.joinpath("bad_utf8.json").write_bytes(b"\xff\xfe\x00\x01")
    d = client.get("/api/battle/saved").json()
    assert d["ok"]
    names = [x["name"] for x in d["battles"]]
    assert len(names) >= 1 and "bad.json" not in names and "bad_utf8.json" not in names


def test_load_missing_404(client):
    assert client.get("/api/battle/load", params={"path": "nope.json"}).status_code == 404


def test_load_non_utf8_422(client, tmp_path):
    tmp_path.joinpath("bad_utf8.json").write_bytes(b"\xff\xfe\x00\x01")
    assert client.get("/api/battle/load", params={"path": "bad_utf8.json"}).status_code == 422


# ---------- 页面与回归哨兵 ----------


def test_battle_page_served(client):
    res = client.get("/battle")
    assert res.status_code == 200 and "对战" in res.text
    assert res.text.count('src="/static/battle.js"') == 1


def test_battle_static_assets_served(client):
    assert client.get("/static/battle.js").status_code == 200
    assert client.get("/static/battle.html").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_team_and_chat_still_work(client):
    """新增对战路由不影响既有页面/组队/聊天（回归哨兵）。"""
    assert client.get("/team").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/team/config").status_code == 200
    assert client.get("/api/battle/saved").status_code == 200


# ---------- E7：观战流（SSE /api/battle/stream + /spectate 页） ----------


def _read_spectate_frames(client, query: str) -> list[dict]:
    """读 /api/battle/stream 的全部 SSE 帧（data: {json}\n\n）。"""
    with client.stream("GET", "/api/battle/stream?" + query) as resp:
        assert resp.status_code == 200
        return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_spectate_stream_global_view(client):
    """观战流帧序 meta → state → turn* → done；turn 帧双方全量（绝对血量/六维）。"""
    frames = _read_spectate_frames(client, "seed=7&a=fake_llm&b=fake_llm&team_size=3")
    seq = [f["event"] for f in frames]
    assert seq[0] == "meta" and seq[1] == "state"
    assert "turn" in seq and seq[-1] == "done"
    turn = [f for f in frames if f["event"] == "turn"][0]
    assert turn["turn"] >= 1 and turn["events"] and turn["decisions"]["a"]
    for side in ("a", "b"):
        u = turn["state"][side]["units"][0]
        assert "current_hp" in u and "max_hp" in u and "stats" in u
    assert frames[-1]["winner"] in ("a", "b")


def test_spectate_invalid_kind_error_frame(client):
    """非法玩家 kind → 首帧 error（配置错误不外抛崩溃）。"""
    frames = _read_spectate_frames(client, "seed=7&a=bogus&b=fake_llm&team_size=3")
    assert frames[0]["event"] == "error"
    assert "未知玩家类型" in frames[0]["message"]


def test_spectate_page_served(client):
    """观战页 /spectate：200 + 加载 spectate.js + 导航含观战链接。"""
    res = client.get("/spectate")
    assert res.status_code == 200 and "观战" in res.text
    assert res.text.count('src="/static/spectate.js"') == 1
    assert 'href="/spectate"' in res.text
