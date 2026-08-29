"""UI 组队页契约测试（/api/team/*，见 ui/routes_team.py）+ 提级回归哨兵。

覆盖五层：
1. config / spirits / skills 三个只读端点（元数据、搜索、技能池含 battle_ready 标记）。
2. validate 闸门：合法通过；规模 / 未实装技能 / 首领 / 同家族 / 4 维个体值 / 非法规模 / extra 字段。
3. save/load/saved/delete 持久化往返（绝对路径 / 相对路径 / 嵌套子目录 / 路径逃逸 / 非 json）。
4. /team 静态页可访问；聊天路由仍工作（回归不变性由 test_agent_ui 全量覆盖，这里只做哨兵）。

环境侧使用真实数据（FULL 精灵 + VALID 技能池）；落盘目录 monkeypatch 到临时目录，测试零副作用。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # 未装 ui extra 时优雅跳过，不硬失败

from fastapi.testclient import TestClient  # noqa: E402

import ui.routes_team as routes_team  # noqa: E402
from environment.dataset import DataSource, load_skills, load_spirits  # noqa: E402
from environment.teambuilder import learnable_skills  # noqa: E402
from rock_pvp_agent.config import Settings  # noqa: E402
from ui.server import create_chat_app  # noqa: E402


def _settings() -> Settings:
    # api_key 为空 → ChatAgent 走离线模式（test_chat_still_works 不触发网络请求）
    return Settings(api_key="", base_url="http://test.invalid", model="test-model", timeout=5.0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试一个独立应用 + 独立队伍落盘目录（零副作用）。"""
    monkeypatch.setattr(routes_team, "TEAMS_DIR", tmp_path)
    app = create_chat_app(_settings())
    return TestClient(app)


# ---------- 组队数据构造（真实数据，与前端同一来源） ----------


def _ready(spirit: str, bloodline: str = "") -> list[str]:
    return list(learnable_skills(spirit, bloodline, DataSource.VALID))


def _pick(spirit: str, n: int = 2, bloodline: str = "") -> dict:
    return {
        "spirit": spirit,
        "skills": _ready(spirit, bloodline)[:n],
        "bloodline": bloodline,
        "nature": "坦率",
        "iv": {},
    }


def _valid_team() -> list[dict]:
    return [_pick("迪莫", 2), _pick("喵喵", 1), _pick("火花", 1)]


# ---------- config ----------


def test_config_metadata(client):
    """队伍规模仅 {3,6} / 技能槽 4 / 个体值上限 10 且最多 3 维 / 31 性格 / 18 系。"""
    body = client.get("/api/team/config").json()
    assert body["team_size"] == {"allowed": [3, 6], "default": 3}
    assert body["skill_slots"] == 4
    assert body["iv_max"] == 10
    assert body["iv_dims_max"] == 3
    assert body["source"] == "VALID"                       # 技能池口径：已实装效果的子集
    names = [n["name"] for n in body["natures"]]
    assert "坦率" in names and len(names) == 31            # 中性 1 + 非中性 30
    assert len(body["types"]) >= 18


# ---------- spirits ----------


def test_spirits_catalog_full_and_fields(client):
    d = client.get("/api/team/spirits").json()
    assert d["total"] >= 590
    names = {s["name"] for s in d["spirits"]}
    assert "迪莫" in names
    hit = next(s for s in d["spirits"] if s["name"] == "迪莫")
    assert hit["types"] == ["光"] and "hp" in hit["stats"] and hit["stats"]["hp"] > 0
    assert "is_boss" in hit and "family_key" in hit and "trait_name" in hit


def test_spirits_search_by_name(client):
    q = client.get("/api/team/spirits?search=迪莫").json()
    assert q["total"] >= 1 and all("迪莫" in s["name"] for s in q["spirits"])


def test_spirits_mark_boss(client):
    q = client.get("/api/team/spirits?search=圣光迪莫").json()
    assert q["spirits"][0]["is_boss"] is True


# ---------- skills 池 ----------


def test_skills_pool_all_learnable_with_battle_ready(client):
    d = client.get("/api/team/skills?spirit=迪莫").json()
    assert d["spirit"] == "迪莫" and d["implemented"] > 0
    assert d["total"] >= d["implemented"]                   # 全部可学 ≥ 已实装
    full = load_skills(DataSource.FULL)
    for s in d["skills"]:
        assert s["name"] in full and isinstance(s["battle_ready"], bool)
    ready = {s["name"] for s in d["skills"] if s["battle_ready"]}
    assert ready == set(_ready("迪莫"))                      # 可配置集合 == VALID 可学池


def test_skills_pool_bloodline_adds_matching_type(client):
    """血脉只拓宽系别匹配的血脉技能：火花 + 火血脉 → 新增技能全是火系。"""
    base = {s["name"] for s in client.get("/api/team/skills?spirit=火花").json()["skills"]}
    with_bl = client.get("/api/team/skills?spirit=火花&bloodline=火").json()
    delta = [s for s in with_bl["skills"] if s["name"] not in base]
    assert delta                                        # 火血脉必带来新技能（火焰箭）
    full = load_skills(DataSource.FULL)
    assert all(full[s["name"]].type == "火" for s in delta)


def test_skills_spirit_missing_404(client):
    assert client.get("/api/team/skills?spirit=不存在的精灵").status_code == 404


# ---------- validate 闸门 ----------


def test_validate_ok_returns_roster(client):
    res = client.post("/api/team/validate", json={"team": _valid_team(), "team_size": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["errors"] == []
    assert len(body["roster"]) == 3
    assert set(body["roster"][0]["stats"]) == {"hp", "atk", "sp_atk", "def", "sp_def", "speed"}


def test_validate_rejects_wrong_team_size(client):
    body = client.post("/api/team/validate", json={"team": _valid_team()[:2], "team_size": 3}).json()
    assert body["ok"] is False
    assert any("队伍规模" in e for e in body["errors"])


def test_validate_rejects_unimplemented_skill(client):
    """未实装效果（FULL 可学但不在 VALID 白名单）→ 清晰文案，不能配置。"""
    nonready = [n for n in learnable_skills("迪莫", "", DataSource.FULL)
                if n not in set(_ready("迪莫"))]
    assert nonready
    team = _valid_team()
    team[0] = _pick("迪莫", 1)
    team[0]["skills"] = [nonready[0]]
    body = client.post("/api/team/validate", json={"team": team, "team_size": 3}).json()
    assert body["ok"] is False
    assert any("效果未实装" in e for e in body["errors"])


def test_validate_rejects_boss(client):
    team = _valid_team()
    team[1] = {"spirit": "圣光迪莫", "skills": _ready("迪莫")[:1],
               "bloodline": "", "nature": "坦率", "iv": {}}
    body = client.post("/api/team/validate", json={"team": team, "team_size": 3}).json()
    assert body["ok"] is False
    assert any("首领形态不可入队" in e for e in body["errors"])


def test_validate_rejects_family_duplicate(client):
    team = [_pick("喵喵", 1), _pick("喵呜", 1), _pick("迪莫", 1)]
    body = client.post("/api/team/validate", json={"team": team, "team_size": 3}).json()
    assert body["ok"] is False
    assert any("同一家族只能入队一只" in e for e in body["errors"])


def test_validate_rejects_4_iv_dims(client):
    team = _valid_team()
    team[0]["iv"] = {"atk": 1, "def": 2, "speed": 3, "hp": 4}
    body = client.post("/api/team/validate", json={"team": team, "team_size": 3}).json()
    assert body["ok"] is False
    assert any("最多 3 个维度" in e for e in body["errors"])


def test_validate_rejects_iv_out_of_range(client):
    team = _valid_team()
    team[0]["iv"] = {"atk": 11}                          # iv_max=10
    body = client.post("/api/team/validate", json={"team": team, "team_size": 3}).json()
    assert body["ok"] is False
    assert any("0–10" in e for e in body["errors"])


def test_validate_rejects_team_size_out_of_admin_range(client):
    res = client.post("/api/team/validate", json={"team": _valid_team(), "team_size": 7})
    assert res.status_code == 422


def test_validate_extra_field_422(client):
    res = client.post("/api/team/validate",
                      json={"team": _valid_team(), "team_size": 3, "hack": 1})
    assert res.status_code == 422


# ---------- 保存 / 加载 / 列表 / 删除 ----------


def test_save_requires_valid_team(client):
    team = _valid_team()
    team[0]["skills"] = ["不存在的技能"]
    res = client.post("/api/team/save", json={"team": team, "team_size": 3, "path": "bad.json"})
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]


def test_save_load_roundtrip_absolute(client, tmp_path):
    target = tmp_path / "绝对路径队伍.json"
    team = _valid_team()
    res = client.post("/api/team/save", json={"team": team, "team_size": 3, "path": str(target)})
    assert res.status_code == 200
    assert target.exists()
    d = client.get("/api/team/load", params={"path": str(target)}).json()
    assert d["ok"] is True and d["team_size"] == 3
    assert [p["spirit"] for p in d["team"]] == ["迪莫", "喵喵", "火花"]
    # v2 富化：技能带 {name,type,desc}，精灵带 trait{name,desc}
    assert d["version"] == 2 and d["saved_at"]
    sk = d["team"][0]["skills"]
    assert isinstance(sk, list) and sk and set(sk[0]) == {"name", "type", "desc"}
    assert sk[0]["name"] == team[0]["skills"][0]
    assert d["team"][0]["trait"] == {"name": "最好的伙伴", "desc": "造成克制伤害后，获得攻防速+20%，并回复2能量。"}


def test_save_rejects_enriched_fields_in_request(client):
    """富化字段（技能 {name,type,desc} / trait）只出现在落盘文件，请求体 extra="forbid" → 422。"""
    team = _valid_team()
    team[0]["skills"] = [{"name": "闪光", "type": "光", "desc": "..."}]
    res = client.post("/api/team/save", json={"team": team, "team_size": 3, "path": "富化.json"})
    assert res.status_code == 422


def test_load_tolerates_v1_string_skills(client, tmp_path):
    """v1 队伍（技能为字符串数组）仍可加载——历史文件兼容。"""
    target = tmp_path / "v1队.json"
    team = _valid_team()
    payload = {
        "version": 1,
        "saved_at": "2026-01-01T00:00:00+00:00",
        "team_size": 3,
        "team": [{"spirit": p["spirit"], "skills": p["skills"],
                  "bloodline": p["bloodline"], "nature": p["nature"], "iv": p["iv"]} for p in team],
    }
    import json as _json
    target.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    d = client.get("/api/team/load", params={"path": str(target)}).json()
    assert d["version"] == 1 and d["team"][0]["skills"] == team[0]["skills"]


def test_save_relative_into_teams_dir(client):
    res = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "测试队.json"})
    assert res.status_code == 200
    path = res.json()["path"]
    base = str(routes_team.TEAMS_DIR.resolve())
    assert path.startswith(base) and path.endswith("测试队.json")
    saved = client.get("/api/team/saved").json()
    assert any(t["name"] == "测试队.json" and t["spirits"] == ["迪莫", "喵喵", "火花"]
               for t in saved["teams"])


def test_save_relative_nested_subdir(client):
    res = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "sub/嵌套队.json"})
    assert res.status_code == 200
    assert routes_team.TEAMS_DIR.joinpath("sub/嵌套队.json").exists()


def test_save_rejects_path_traversal(client):
    res = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "../../x.json"})
    assert res.status_code == 422


def test_save_rejects_non_json_suffix(client):
    res = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "team.txt"})
    assert res.status_code == 422


def test_load_missing_404(client):
    assert client.get("/api/team/load", params={"path": "不存在.json"}).status_code == 404


def test_delete_removes_file_and_list(client):
    client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "del.json"})
    assert routes_team.TEAMS_DIR.joinpath("del.json").exists()
    assert client.post("/api/team/delete", json={"path": "del.json"}).status_code == 200
    assert not routes_team.TEAMS_DIR.joinpath("del.json").exists()
    assert client.get("/api/team/load", params={"path": "del.json"}).status_code == 404
    assert client.get("/api/team/saved").json()["teams"] == []


def test_saved_empty_when_none(client):
    assert client.get("/api/team/saved").json()["teams"] == []


# ---------- 审计修复的回归（2026-08-25 对抗审查确认的问题） ----------


def test_saved_ignores_corrupt_and_non_utf8_files(client):
    """一个坏文件（非 UTF-8 / 非 JSON）不拖垮整个列表：逐个跳过，只列合法队伍。"""
    client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": "ok.json"})
    routes_team.TEAMS_DIR.joinpath("bad_utf8.json").write_bytes(b"\xff\xfe\x00\x01")
    routes_team.TEAMS_DIR.joinpath("bad_json.json").write_text("{ 不是 json", encoding="utf-8")
    d = client.get("/api/team/saved").json()
    assert d["ok"] is True
    names = [t["name"] for t in d["teams"]]
    assert "ok.json" in names
    assert "bad_utf8.json" not in names and "bad_json.json" not in names


def test_load_non_utf8_returns_422_not_500(client):
    """非 UTF-8 队伍文件 → 干净 422（不是 500）。"""
    bad = routes_team.TEAMS_DIR / "bad_utf8.json"
    bad.write_bytes(b"\xff\xfe\x00\x01")
    res = client.get("/api/team/load", params={"path": str(bad)})
    assert res.status_code == 422


def test_delete_outside_teams_dir_rejected(client, tmp_path):
    """delete 只允许 TEAMS_DIR 内：绝对路径指向外部 → 422，不删。"""
    victim = tmp_path / ".." / "victim.json"          # TEAMS_DIR 的父目录
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("{}", encoding="utf-8")
    res = client.post("/api/team/delete", json={"path": str(victim)})
    assert res.status_code == 422
    assert victim.exists()                            # 未被删除


def test_two_empty_path_saves_produce_distinct_files(client):
    """空 path 的默认文件名带微秒时间戳：同一秒两次保存不互相覆盖。"""
    r1 = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": ""})
    r2 = client.post("/api/team/save", json={"team": _valid_team(), "team_size": 3, "path": ""})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["path"] != r2.json()["path"]
    assert len(client.get("/api/team/saved").json()["teams"]) == 2


# ---------- 页面与提级回归哨兵 ----------


def test_team_page_served(client):
    res = client.get("/team")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "组队" in res.text
    assert res.text.count('src="/static/team.js"') == 1


def test_team_static_assets_served(client):
    assert client.get("/static/team.js").status_code == 200
    assert client.get("/static/team.html").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_chat_still_works_after_relocation(client):
    """提级哨兵：/ 聊天页与 SSE/chat 契约在 ui 包移动后不受影响。"""
    assert client.get("/").status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200


def test_spirits_catalog_has_all_expected_spirits(client):
    """精灵目录与 FULL 数据源一致（593 = 594 原始 - 学院呱呱脏记录）。"""
    d = client.get("/api/team/spirits").json()
    assert d["total"] == len(load_spirits(DataSource.FULL))
    assert d["total"] == 593
