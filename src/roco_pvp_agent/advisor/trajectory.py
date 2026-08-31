"""轨迹证据（M2）：发现 / 归一化 / 重放闸 / 版本闸 / 聚合 / 查询。

把两类原始轨迹（人机 `battles/*.json`、自博弈 `runs/*.json`）归一化成统一的
`TrajectoryEvidence`，做构筑级胜率统计。两条硬闸在代码里落地，不靠提示词：

- **重放闸**：`replay_record(record)["all_match"]` 为真才算；人机 TeamPick 形态先
  `build_roster` 转 roster 再重放，转换/重放失败 → `replay_ok=False`（不猜、不抛）。
- **版本闸**：`rules_digest` 与 `data_digest` 同时匹配当前才进硬证据；旧记录（无 stamp）
  标 `"unknown"`，默认排除，只在 `digest_unknown_count`/`version_mismatch_count` 可见。

人机与自博弈**绝不合并**：`kind` 是聚合的硬分桶键，接口层面不提供合并胜率。
只回聚合统计与 `evidence_id`，不回 `turns` 原文、不回 `llm_reply`/`events`。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from environment.datafingerprint import data_digest as _current_data_digest
from environment.datafingerprint import rules_digest as _current_rules_digest
from environment.dataset import DataSource
from environment.replay import replay_record
from environment.rules import BattleRules
from environment.teambuilder import TeamPick, build_roster

from roco_pvp_agent.battle.store import TrajectoryStore

# 默认目录：项目根 / battles、/ runs（测试注入 tmp 覆盖）。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BATTLES_DIR = _PROJECT_ROOT / "battles"
_DEFAULT_RUNS_DIR = _PROJECT_ROOT / "runs"

_KINDS = ("human", "selfplay")
_UNKNOWN_TEAM_PREFIX = "unknown:"


@dataclass(frozen=True)
class TrajectoryEvidence:
    """一条归一化轨迹证据（人机或自博弈）。"""

    kind: str                  # "human" | "selfplay"
    evidence_id: str           # f"{kind}:{digest8}:{battle_id}"
    battle_id: str
    team_key: str              # 构筑规范化指纹（team_a）
    opponent_key: str          # 对手构筑指纹（team_b）
    winner_side: str | None    # "a" | "b" | None
    winner_key: str | None     # 胜方 team_key；winner 非 a/b → None
    rules_digest: str          # 记录 stamp；旧记录 = "unknown"
    data_digest: str           # 记录 stamp；旧记录 = "unknown"
    replay_ok: bool
    sample_seed: int
    team_names: tuple[str, ...]  # team_a 精灵名（team_filter 用）


# ── 构筑指纹 ──

def _unit_name(u: dict) -> str | None:
    """TeamPick(spirit) 与 roster(name) 的统一取名。"""
    return u.get("name") or u.get("spirit")


def _unit_key(u: dict) -> tuple:
    """单只精灵的可比较键：名字 + 技能 + 血脉 + 性格 + IV（字段缺失 → 空）。"""
    return (
        _unit_name(u),
        tuple(u.get("skills") or []),
        u.get("bloodline", ""),
        u.get("nature", ""),
        tuple(sorted((u.get("iv") or {}).items())),
    )


def team_key(team: list[dict]) -> str:
    """构筑规范化指纹：单只键排序后整体 sha256 前 16 位（同构筑去重/胜率聚合用）。"""
    units = sorted(_unit_key(u) for u in team)
    return hashlib.sha256(repr(units).encode("utf-8")).hexdigest()[:16]


def _team_key_or_unknown(team: list[dict], battle_id: str) -> str:
    """构筑指纹；任一精灵名缺失 → `unknown:{battle_id}`（不进胜率，只计数）。"""
    if any(_unit_name(u) in (None, "") for u in team):
        return f"{_UNKNOWN_TEAM_PREFIX}{battle_id}"
    return team_key(team)


def _team_names(team: list[dict]) -> tuple[str, ...]:
    """team 的精灵名（去空、排序）。"""
    return tuple(sorted(n for u in team if (n := _unit_name(u))))


def _digest8(digest: str) -> str:
    """data_digest 前 8 个 hex 字符（`d_` 前缀剥掉）；`unknown` 原样。"""
    body = digest[2:] if digest.startswith("d_") else digest
    return body[:8]


# ── 发现 ──

def _read_json(path: Path) -> dict | None:
    """读 JSON → dict；坏文件 / 非 dict → None（坏文件不拖垮整批）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def discover(kind: str, *, battles_dir: str | Path | None = None,
             runs_dir: str | Path | None = None) -> list[dict]:
    """发现某类轨迹的原始记录列表。

    - selfplay：`TrajectoryStore(runs_dir).index()` → 逐条读 `runs/{path}`。
    - human：glob `battles/*.json`（跳过非 dict / 缺 turns）。
    """
    if kind == "selfplay":
        store = TrajectoryStore(runs_dir or _DEFAULT_RUNS_DIR)
        records: list[dict] = []
        for meta in store.index():
            path = meta.get("path")
            if not path:
                continue
            rec = _read_json(store.dir / path)
            if rec is not None:
                records.append(rec)
        return records
    if kind == "human":
        d = Path(battles_dir or _DEFAULT_BATTLES_DIR)
        if not d.is_dir():
            return []
        records = []
        for f in sorted(d.glob("*.json")):
            rec = _read_json(f)
            if rec is not None and "turns" in rec:
                records.append(rec)
        return records
    raise ValueError(f"未知 kind「{kind}」（{'/'.join(_KINDS)}）。")


# ── 归一化 ──

def _replay(record: dict, kind: str, source: DataSource) -> bool:
    """重放闸：`replay_record` 的 all_match；human 先 TeamPick→roster。失败 → False（不猜、不抛）。"""
    try:
        if kind == "human":
            rules = BattleRules(**record["rules"])
            roster_a = build_roster([TeamPick(**p) for p in record.get("team_a") or []],
                                    source, rules)
            roster_b = build_roster([TeamPick(**p) for p in record.get("team_b") or []],
                                    source, rules)
            record = {**record, "team_a": roster_a, "team_b": roster_b}
        return replay_record(record)["all_match"]
    except Exception:
        return False


def normalize(record: dict, kind: str, *, source: DataSource = DataSource.VALID) -> TrajectoryEvidence:
    """一条原始记录 → TrajectoryEvidence。"""
    bid = record.get("battle_id", "")
    team_a = record.get("team_a") or []
    team_b = record.get("team_b") or []
    tk = _team_key_or_unknown(team_a, bid)
    ok = _team_key_or_unknown(team_b, bid)

    winner = record.get("winner")
    winner_side = winner if winner in ("a", "b") else None
    if winner_side == "a":
        winner_key = tk
    elif winner_side == "b":
        winner_key = ok
    else:
        winner_key = None

    dd = record.get("data_digest", "unknown")
    rd = record.get("rules_digest", "unknown")
    return TrajectoryEvidence(
        kind=kind,
        evidence_id=f"{kind}:{_digest8(dd)}:{bid}",
        battle_id=bid,
        team_key=tk,
        opponent_key=ok,
        winner_side=winner_side,
        winner_key=winner_key,
        rules_digest=rd,
        data_digest=dd,
        replay_ok=_replay(record, kind, source),
        sample_seed=record.get("seed", 0),
        team_names=_team_names(team_a),
    )


# ── 聚合 ──

def _wilson(wins: int, games: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson 95% 置信区间（不除 0：games<=0 → None）。"""
    if games <= 0:
        return None, None
    p = wins / games
    z2 = z * z
    denom = 1 + z2 / games
    center = (p + z2 / (2 * games)) / denom
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * games)) / games) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _bucket(evidences: list[TrajectoryEvidence]) -> dict:
    """单 kind 桶：total_games + by_team（胜率/CI/对手/evidence_ids）。"""
    by_team: dict[str, dict] = {}
    for e in evidences:
        if e.team_key.startswith(_UNKNOWN_TEAM_PREFIX):
            continue  # 不可用样本：不进胜率，只计入 total_games
        t = by_team.setdefault(e.team_key, {"games": 0, "wins": 0, "opponents": {}, "evidence_ids": []})
        t["games"] += 1
        t["opponents"][e.opponent_key] = t["opponents"].get(e.opponent_key, 0) + 1
        t["evidence_ids"].append(e.evidence_id)
        if e.winner_key is not None and e.winner_key == e.team_key:
            t["wins"] += 1
    for t in by_team.values():
        games, wins = t["games"], t["wins"]
        t["win_rate"] = (wins / games) if games else None
        t["ci95_low"], t["ci95_high"] = _wilson(wins, games)
    return {"total_games": len(evidences), "by_team": by_team}


def aggregate(evidences: list[TrajectoryEvidence]) -> dict[str, dict]:
    """按 kind 分桶聚合（人机/自博弈硬分桶，绝不合并）。每桶附 replay_ok_rate / digest_unknown_count。"""
    by_kind: dict[str, list[TrajectoryEvidence]] = {}
    for e in evidences:
        by_kind.setdefault(e.kind, []).append(e)
    out: dict[str, dict] = {}
    for kind, es in by_kind.items():
        bucket = _bucket(es)
        total = len(es)
        bucket["replay_ok_rate"] = (sum(1 for e in es if e.replay_ok) / total) if total else None
        bucket["digest_unknown_count"] = sum(1 for e in es if e.data_digest == "unknown")
        out[kind] = bucket
    return out


# ── 查询（M3 消费的接口）──

def query_trajectory_evidence(kind: str, *, data_digest: str | None = None,
                              team_filter: list[str] | None = None, min_games: int = 3,
                              battles_dir: str | Path | None = None,
                              runs_dir: str | Path | None = None) -> dict:
    """查询某 kind 下的构筑级胜率证据（只含版本匹配且 replay_ok 的硬证据）。

    - `data_digest` 缺省用当前；`team_filter` 只留含这些精灵名的构筑；`min_games` 滤样本不足。
    - 返回 `total_games`/`by_team` 只含硬证据；`replay_ok_rate`/`digest_unknown_count`/
      `version_mismatch_count` 覆盖全量（透明标注）。
    """
    if kind not in _KINDS:
        raise ValueError(f"未知 kind「{kind}」（{'/'.join(_KINDS)}）。")
    gate_dd = data_digest if data_digest is not None else _current_data_digest()
    gate_rd = _current_rules_digest()

    records = discover(kind, battles_dir=battles_dir, runs_dir=runs_dir)
    all_evs = [normalize(r, kind) for r in records]

    total = len(all_evs)
    replay_ok_rate = (sum(1 for e in all_evs if e.replay_ok) / total) if total else None
    digest_unknown_count = sum(1 for e in all_evs if e.data_digest == "unknown")
    version_mismatch_count = sum(
        1 for e in all_evs if not (e.rules_digest == gate_rd and e.data_digest == gate_dd))

    # 硬证据：重放闸 + 版本闸
    hard = [e for e in all_evs if e.replay_ok and e.rules_digest == gate_rd and e.data_digest == gate_dd]
    if team_filter:
        wanted = set(team_filter)
        hard = [e for e in hard if wanted & set(e.team_names)]

    bucket = _bucket(hard)
    bucket["by_team"] = {k: v for k, v in bucket["by_team"].items() if v["games"] >= min_games}

    return {
        "kind": kind,
        "data_digest": gate_dd,
        "rules_digest": gate_rd,
        "total_games": bucket["total_games"],
        "by_team": bucket["by_team"],
        "replay_ok_rate": replay_ok_rate,
        "digest_unknown_count": digest_unknown_count,
        "version_mismatch_count": version_mismatch_count,
    }
