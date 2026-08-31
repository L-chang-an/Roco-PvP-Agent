"""G1 GlobalMem 数据层：画像 / matchup_key / 相似度 / append-only Store / Q 更新 / 护栏。"""

from __future__ import annotations

import pytest

from environment.dataset import DataSource
from environment.presets import p1_preset
from environment.teambuilder import build_roster
from roco_pvp_agent.battle.evolution.globalmem import (
    DEFAULT_MAX_STRATEGY_TOKENS,
    WARN_MAX_STRATEGY_TOKENS,
    GlobalMemStore,
    MatchupQuery,
    make_global_entry_id,
    matchup_key,
    roster_profile,
    search_global_mem,
    similarity,
    update_global_q,
)
from roco_pvp_agent.config import Settings


def _rosters():
    pa, pb = p1_preset(3)
    return (build_roster(pa, DataSource.VALID), build_roster(pb, DataSource.VALID))


def _entry(key: str, text: str = "开局压能量，残局换人吸伤", *, digest: str = "d_x",
           Q: float = 0.0) -> dict:
    return {
        "entry_id": make_global_entry_id(key, text, digest),
        "matchup_key": key,
        "my_roster": ["迪莫", "喵喵", "火花"],
        "foe_roster": ["水蓝蓝", "板板壳", "鸭吉吉"],
        "strategy_text": text,
        "Q": Q, "n_used": 0, "n_wins": 0,
        "provenance": {"data_digest": digest, "rules_digest": "r_x"},
    }


# ---------- 画像 / key（含迷雾口径断言）----------

def test_own_profile_has_kinds_but_foe_profile_does_not():
    """迷雾口径：我方画像含技能类别；**对手画像绝不含 kinds/speed**（技能名开局未揭示）。"""
    ra, rb = _rosters()
    mine = roster_profile(ra, own=True, source=DataSource.VALID)
    foe = roster_profile(rb, own=False, source=DataSource.VALID)
    assert "kinds" in mine and "speed_tier" in mine
    assert "kinds" not in foe and "speed_tier" not in foe
    assert set(foe) == {"types", "names"}          # 只有 team preview 可见的两项


def test_matchup_key_shape_and_no_foe_kinds():
    ra, rb = _rosters()
    key = matchup_key(ra, rb, team_size=3, lives=2, source=DataSource.VALID)
    assert key.startswith("3v2|my:")
    my_seg, foe_seg = key.split("|")[1], key.split("|")[2]
    assert "k:" in my_seg and "spd:" in my_seg     # 我方有技能类别 + 速度
    assert "k:" not in foe_seg and "spd:" not in foe_seg   # 对手侧没有（防透题）


def test_matchup_key_deterministic():
    ra, rb = _rosters()
    k1 = matchup_key(ra, rb, team_size=3, lives=2, source=DataSource.VALID)
    k2 = matchup_key(ra, rb, team_size=3, lives=2, source=DataSource.VALID)
    assert k1 == k2


# ---------- 相似度（只保证序）----------

def test_similarity_ordering():
    """同阵=1.0 > 同系别不同分布 > 完全不同系别；跨规模=0。"""
    same = "3v2|my:types:光1草1火1/spd:high/k:a7d2s3|foe:types:水1普2"
    close = "3v2|my:types:光1草1火1/spd:high/k:a6d2s4|foe:types:水1普2"
    far = "3v2|my:types:冰3/spd:low/k:d6s6|foe:types:火3"
    other_scale = "6v4|my:types:光1草1火1/spd:high/k:a7d2s3|foe:types:水1普2"
    assert similarity(same, same) == 1.0
    assert similarity(same, close) > similarity(same, far)
    assert similarity(same, other_scale) == 0.0          # 规模段不同 → 硬 0
    assert 0.0 <= similarity(same, far) <= 1.0


def test_similarity_symmetric():
    a = "3v2|my:types:光2火1/spd:mid/k:a5d2s2|foe:types:水3"
    b = "3v2|my:types:光1火2/spd:mid/k:a4d3s2|foe:types:水2草1"
    assert similarity(a, b) == similarity(b, a)


def test_similarity_bad_key_returns_zero():
    assert similarity("垃圾", "3v2|my:types:火3|foe:types:水3") == 0.0


# ---------- Store：append-only + supersedes + token 上限 ----------

def test_add_and_idempotent(tmp_path):
    store = GlobalMemStore(tmp_path)
    e = _entry("3v2|my:types:火3/spd:mid/k:a6|foe:types:水3")
    assert store.add(e)["reason"] == "added"
    assert store.add(e)["reason"] == "exists"        # 幂等
    assert store.count() == 1


def test_token_limit_rejects_without_truncation(tmp_path):
    store = GlobalMemStore(tmp_path)
    long_text = "策" * (DEFAULT_MAX_STRATEGY_TOKENS * 2 + 10)     # 远超上限
    out = store.add(_entry("3v2|my:types:火3/spd:mid/k:a6|foe:types:水3", long_text))
    assert out["ok"] is False and "超上限" in out["reason"]
    assert store.count() == 0                        # 拒绝写入，且未截断存半句


def test_token_limit_configurable_per_instance(tmp_path):
    """上限三层可配：构造参数覆盖模块默认；放宽后同一文本可写入。"""
    text = "策" * (DEFAULT_MAX_STRATEGY_TOKENS * 2 + 10)   # 约 2×默认上限
    key = "3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"
    strict = GlobalMemStore(tmp_path / "strict")
    assert strict.add(_entry(key, text))["ok"] is False
    loose = GlobalMemStore(tmp_path / "loose",
                           max_strategy_tokens=DEFAULT_MAX_STRATEGY_TOKENS * 3)
    assert loose.max_strategy_tokens == DEFAULT_MAX_STRATEGY_TOKENS * 3
    assert loose.add(_entry(key, text))["ok"] is True
    assert loose.count() == 1


def test_token_limit_warns_when_too_large(tmp_path):
    """超过 WARN 阈值仍允许，但必须发警告（告知注入成本 ≈ 上限 × 回合数）。"""
    with pytest.warns(UserWarning, match="token 上限"):
        GlobalMemStore(tmp_path, max_strategy_tokens=WARN_MAX_STRATEGY_TOKENS + 1)


def test_token_limit_rejects_invalid(tmp_path):
    with pytest.raises(ValueError):
        GlobalMemStore(tmp_path, max_strategy_tokens=0)


def test_settings_exposes_globalmem_knobs():
    """Settings 层可配（对齐已有 memory_* 模式），默认值与模块默认一致。"""
    s = Settings()
    assert s.globalmem_max_tokens == DEFAULT_MAX_STRATEGY_TOKENS
    assert s.globalmem_delta == 0.5 and s.globalmem_lam == 0.5
    assert s.globalmem_top_k == 1 and s.globalmem_alpha == 0.3
    assert Settings(globalmem_max_tokens=1200).globalmem_max_tokens == 1200


def test_supersede_is_append_only(tmp_path):
    store = GlobalMemStore(tmp_path)
    key = "3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"
    old = _entry(key, "旧策略")
    store.add(old)
    new = _entry(key, "新策略")
    store.supersede(old["entry_id"], new)
    assert store.count() == 1                                     # active 只剩新的
    assert [e["entry_id"] for e in store.active()] == [new["entry_id"]]
    kept = store.get(old["entry_id"])
    assert kept is not None and kept["strategy_text"] == "旧策略"   # 旧文本没被销毁
    assert kept["superseded_by"] == new["entry_id"]
    assert store.get(new["entry_id"])["supersedes"] == old["entry_id"]


def test_apply_report_written(tmp_path):
    store = GlobalMemStore(tmp_path)
    store.add(_entry("3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"), battle_id="b1")
    lines = store.report_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and "b1" in lines[0]


# ---------- 检索：硬过滤 + 桶内排序 ----------

def test_search_hard_filters_digest_and_scale(tmp_path):
    store = GlobalMemStore(tmp_path)
    key = "3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"
    store.add(_entry(key, digest="d_old"))
    q = MatchupQuery(matchup_key=key, data_digest="d_new")
    assert search_global_mem(store, q) == []            # data_digest 不符 → 排除
    q_ok = MatchupQuery(matchup_key=key, data_digest="d_old")
    assert len(search_global_mem(store, q_ok)) == 1


def test_search_returns_top1_and_skips_superseded(tmp_path):
    store = GlobalMemStore(tmp_path)
    key = "3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"
    old = _entry(key, "旧策略")
    store.add(old)
    new = _entry(key, "新策略")
    store.supersede(old["entry_id"], new)
    hits = search_global_mem(store, MatchupQuery(matchup_key=key, data_digest="d_x"))
    assert [h["entry_id"] for h in hits] == [new["entry_id"]]   # 被替代的不再命中


def test_search_below_delta_excluded(tmp_path):
    store = GlobalMemStore(tmp_path)
    store.add(_entry("3v2|my:types:冰3/spd:low/k:d6s6|foe:types:草3"))
    q = MatchupQuery(matchup_key="3v2|my:types:火3/spd:high/k:a6|foe:types:水3",
                     data_digest="d_x")
    assert search_global_mem(store, q, delta=0.5) == []


# ---------- Q 更新 ----------

def test_update_global_q_win_and_counters(tmp_path):
    store = GlobalMemStore(tmp_path)
    e = _entry("3v2|my:types:火3/spd:mid/k:a6|foe:types:水3")
    store.add(e)
    q = update_global_q(store, e["entry_id"], True)
    assert q == pytest.approx(0.3)                     # 0 + 0.3*(1-0)
    got = store.get(e["entry_id"])
    assert got["n_used"] == 1 and got["n_wins"] == 1
    update_global_q(store, e["entry_id"], False)
    got = store.get(e["entry_id"])
    assert got["n_used"] == 2 and got["n_wins"] == 1
    assert got["Q"] == pytest.approx(0.21)             # 0.3 + 0.3*(0-0.3)


def test_update_global_q_missing_raises(tmp_path):
    with pytest.raises(KeyError):
        update_global_q(GlobalMemStore(tmp_path), "gm_nope", True)
