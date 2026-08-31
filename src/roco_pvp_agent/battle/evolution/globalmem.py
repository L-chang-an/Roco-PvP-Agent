"""G1 GlobalMem 数据层：全局对局经验的存储 / 检索 / Q 更新（占据原 Playbook 生态位）。

与局部 `memory.py` 的分工：
- 局部 mem：`situation_key`（回合内局面）→ 每回合 `decide` 注入 top-3；
- **GlobalMem**：`matchup_key`（双方阵容画像）→ 每场 `on_match_start` 注入 top-1。

**迷雾口径（G1 就钉死）**：引擎的迷雾模型是「团队预览」——开局双方精灵名/系别/特性公开，
**技能池不公开**。因此：
- 我方画像可含 `speed_tier` / 技能 `kinds`（自己的技能自己全见）；
- **对手画像只含 `types` + `names`**——技能 `kind` 由技能名派生，而对手技能名开局未揭示，
  放进 key 就等于透题（下一场注入即泄漏）。

三处护栏（对齐 Playbook 原有纪律）：
1. **token 上限**：单条 `strategy_text` ≤ 上限，超限**拒绝不截断**（截断会
   产生半句规则）。上限三层可配：模块默认 `DEFAULT_MAX_STRATEGY_TOKENS` →
   `GlobalMemStore(max_strategy_tokens=)` 构造参数 → `Settings.globalmem_max_tokens`；
2. **append-only + supersedes**：`update` 不改写原条目，而是新写一条 + 旧条目标
   `superseded_by`，检索只看未被替代的 → 天然可回滚、审计链完整；
3. **审计**：每次 add/update 落 `globalmem_apply_report.jsonl`。

**Q 的比较边界（硬性）**：检索按 matchup 硬过滤，Q 只在**同一 matchup 桶内**可比——
桶内阵容强度这个混杂因素基本抵消；**跨桶比较 Q 是错的**。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from environment.dataset import DEFAULT_SOURCE, DataSource, load_skills

from roco_pvp_agent.battle.evolution.memory import _zscore, sigmoid

# 检索默认参数（与局部 mem 同口径，便于对照调参）。
DEFAULT_DELTA = 0.5
DEFAULT_LAM = 0.5
DEFAULT_TOP_K = 1
DEFAULT_ALPHA = 0.3

# 单条策略文本 token 上限的**默认值**（可由 `GlobalMemStore(max_strategy_tokens=)` 或
# `Settings.globalmem_max_tokens` 覆盖）。
#
# 为什么默认这么小：GlobalMem 注入在 **system prompt**，而 LLMPlayer 每回合重发整个
# history —— 成本是**乘以回合数**的。400 token × 20 回合 ≈ 8,000 输入 token/局
# （实测一局累计输入 ~110,000，占 ~7%）。调到 2000 就占 ~36%。提示缓存实现后这条约束会松。
# 另一依据：SkillOpt 实测最终技能 379–1,995 token，且**全部收益来自 1–4 次被接受的编辑**
# ——设计目标不是写长手册。
DEFAULT_MAX_STRATEGY_TOKENS = 400

# 超过此值仍允许，但发警告（告知注入成本 ≈ 上限 × 回合数 / 局）。
WARN_MAX_STRATEGY_TOKENS = 2000

# 与 Playbook 先例的偏离（刻意）：`playbook.MAX_MODULE_TOKENS` 是写死的，因为 Playbook 另有
# 有界编辑 `L_t` + 两级门禁双重约束，token 上限只是兜底；GlobalMem **没有**有界编辑机制，
# token 上限是唯一的膨胀约束，因此它该是可调的运营旋钮而非硬编码。

# 速度档阈值（roster 平均速度；确定性分档）。
_SPEED_HIGH = 130
_SPEED_MID = 90

# 技能类别缩写（key 里紧凑表示；攻 = 物攻+魔攻）。
_KIND_ABBR = {"物攻": "a", "魔攻": "a", "防御": "d", "状态": "s"}

_ENTRIES_FILE = "global_entries.jsonl"
_REPORT_FILE = "globalmem_apply_report.jsonl"

_ENTRY_REQUIRED = ("entry_id", "matchup_key", "my_roster", "foe_roster",
                   "strategy_text", "Q", "n_used", "n_wins", "provenance")


def estimate_tokens(text: str) -> int:
    """确定性 token 近似 `max(1, len//2)`（与 playbook.estimate_tokens 同口径，CJK 保守）。"""
    return max(1, len(text or "") // 2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _speed_tier(rosters: list[dict]) -> str:
    """全队平均速度 → high/mid/low（确定性分档）。"""
    speeds = [u.get("stats", {}).get("speed", 0) for u in rosters]
    avg = sum(speeds) / len(speeds) if speeds else 0
    if avg >= _SPEED_HIGH:
        return "high"
    if avg >= _SPEED_MID:
        return "mid"
    return "low"


def _type_counts(roster: list[dict]) -> dict[str, int]:
    """系别多重集（公开信息：team preview 可见）。"""
    counts: dict[str, int] = {}
    for u in roster:
        for t in u.get("types") or []:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items()))


def _kind_counts(roster: list[dict], source: DataSource) -> dict[str, int]:
    """技能类别计数（攻 a / 防 d / 状态 s）。**仅用于我方画像**——对手技能名开局未揭示。"""
    skills = load_skills(source)
    counts: dict[str, int] = {}
    for u in roster:
        for name in u.get("skills") or []:
            sk = skills.get(name)
            if sk is None:
                continue
            ab = _KIND_ABBR.get(sk.kind)
            if ab:
                counts[ab] = counts.get(ab, 0) + 1
    return dict(sorted(counts.items()))


def roster_profile(roster: list[dict], *, own: bool,
                   source: DataSource = DEFAULT_SOURCE) -> dict:
    """roster spec → 确定性画像。

    `own=True`（我方）：含 `types` / `names` / `speed_tier` / `kinds`；
    `own=False`（对手）：**只含 `types` / `names`**（team preview 口径，防技能透题）。
    """
    prof = {
        "types": _type_counts(roster),
        "names": sorted(u.get("name", "") for u in roster),
    }
    if own:
        prof["speed_tier"] = _speed_tier(roster)
        prof["kinds"] = _kind_counts(roster, source)
    return prof


def _profile_str(prof: dict, *, own: bool) -> str:
    """画像 → 可读紧凑串（进 matchup_key）。"""
    types = "".join(f"{t}{n}" for t, n in prof["types"].items())
    if not own:
        return f"types:{types}"
    kinds = "".join(f"{k}{n}" for k, n in prof.get("kinds", {}).items())
    return f"types:{types}/spd:{prof.get('speed_tier', '?')}/k:{kinds}"


def matchup_key(my_roster: list[dict], foe_roster: list[dict], *,
                team_size: int, lives: int,
                source: DataSource = DEFAULT_SOURCE) -> str:
    """有向对局键（可读、可审计）。

    形如 `3v2|my:types:光1草1火1/spd:high/k:a7d2s3|foe:types:水1普2`。
    `team_size`/`lives` 进 key 首段作**硬过滤**依据（3v3 经验不可用于 6v6）。
    """
    my = _profile_str(roster_profile(my_roster, own=True, source=source), own=True)
    foe = _profile_str(roster_profile(foe_roster, own=False, source=source), own=False)
    return f"{team_size}v{lives}|my:{my}|foe:{foe}"


def make_global_entry_id(matchup_key_str: str, strategy_text: str, scope: str = "") -> str:
    """确定性 id：`gm_` + sha256(matchup_key|strategy_text|scope) 前 16 位（64 bit）。

    含 `strategy_text` → 同 matchup 的不同策略文本是不同条目（append-only 的前提）；
    `scope` = data_digest（跨版本不合并）。
    """
    payload = json.dumps({"matchup": matchup_key_str, "text": strategy_text, "scope": scope},
                         sort_keys=True, ensure_ascii=False)
    return "gm_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 相似度（硬过滤后在桶内排序用）
# ---------------------------------------------------------------------------

# 权重：我方系别 / 对手系别 / 我方技能类别分布 / 速度档（和为 1）。
_W_MY_TYPES, _W_FOE_TYPES, _W_KINDS, _W_SPEED = 0.35, 0.35, 0.20, 0.10


def _parse_key(key: str) -> dict | None:
    """matchup_key → 分量 dict；形状不符 → None（坏行容错，不抛）。"""
    parts = key.split("|")
    if len(parts) != 3 or not parts[1].startswith("my:") or not parts[2].startswith("foe:"):
        return None
    out: dict = {"scale": parts[0], "my": {}, "foe": {}}
    for seg in parts[1][len("my:"):].split("/"):
        if ":" in seg:
            k, v = seg.split(":", 1)
            out["my"][k] = v
    for seg in parts[2][len("foe:"):].split("/"):
        if ":" in seg:
            k, v = seg.split(":", 1)
            out["foe"][k] = v
    return out


def _counts_from_str(s: str) -> dict[str, int]:
    """`光1草1火1` / `a7d2s3` → {符号: 数量}（逐字符扫描，确定性）。"""
    out: dict[str, int] = {}
    label, num = "", ""
    for ch in s or "":
        if ch.isdigit():
            num += ch
        else:
            if label and num:
                out[label] = out.get(label, 0) + int(num)
            label, num = ch, ""
    if label and num:
        out[label] = out.get(label, 0) + int(num)
    return out


def _multiset_overlap(a: dict[str, int], b: dict[str, int]) -> float:
    """多重集重合度 = 2·|A∩B| / (|A|+|B|)（Sørensen–Dice，[0,1]、对称）。"""
    total = sum(a.values()) + sum(b.values())
    if total == 0:
        return 1.0                     # 两边都空 → 视为完全一致
    inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b))
    return 2 * inter / total


def similarity(a_key: str, b_key: str) -> float:
    """两条 matchup_key 的相似度 ∈ [0,1]（确定性、对称）。

    - 规模段（`3v2`）不同 → 0（硬过滤本已拦住，这里双保险）；
    - 精灵名多重集两侧都完全相同 → 1.0（精确同阵，短路）；
    - 否则 = 0.35·我方系别 + 0.35·对手系别 + 0.20·我方技能类别 + 0.10·速度档。
    """
    pa, pb = _parse_key(a_key), _parse_key(b_key)
    if pa is None or pb is None or pa["scale"] != pb["scale"]:
        return 0.0
    my_t = _multiset_overlap(_counts_from_str(pa["my"].get("types", "")),
                             _counts_from_str(pb["my"].get("types", "")))
    foe_t = _multiset_overlap(_counts_from_str(pa["foe"].get("types", "")),
                              _counts_from_str(pb["foe"].get("types", "")))
    kinds = _multiset_overlap(_counts_from_str(pa["my"].get("k", "")),
                              _counts_from_str(pb["my"].get("k", "")))
    speed = 1.0 if pa["my"].get("spd") == pb["my"].get("spd") else 0.0
    if my_t == 1.0 and foe_t == 1.0 and kinds == 1.0 and speed == 1.0:
        return 1.0
    return round(_W_MY_TYPES * my_t + _W_FOE_TYPES * foe_t
                 + _W_KINDS * kinds + _W_SPEED * speed, 6)


@dataclass(frozen=True)
class MatchupQuery:
    """检索查询：当前对局的 matchup_key + 版本隔离键。"""

    matchup_key: str
    data_digest: str = ""


class GlobalMemStore:
    """GlobalMem 读写口：`global_entries.jsonl` 追加式 + 原子写（复用 MemoryStore 模式）。

    **append-only**：`supersede` 只给旧条目打 `superseded_by` 标记并新写一条，
    旧文本永不销毁（可回滚 + 审计链完整）。`active()` 只返回未被替代的条目。
    """

    def __init__(self, dir_path: str | Path, *,
                 max_strategy_tokens: int = DEFAULT_MAX_STRATEGY_TOKENS) -> None:
        if max_strategy_tokens < 1:
            raise ValueError(f"max_strategy_tokens 须 ≥1，实际 {max_strategy_tokens}")
        if max_strategy_tokens > WARN_MAX_STRATEGY_TOKENS:
            warnings.warn(
                f"GlobalMem token 上限设为 {max_strategy_tokens}（> {WARN_MAX_STRATEGY_TOKENS}）："
                f"它注入 system prompt 并随每回合重发，单局额外输入 ≈ "
                f"{max_strategy_tokens} × 回合数 token（当前无提示缓存）。",
                stacklevel=2)
        self._dir = Path(dir_path)
        self._max_tokens = max_strategy_tokens

    @property
    def max_strategy_tokens(self) -> int:
        """本实例的策略文本 token 上限（构造时可覆盖默认值）。"""
        return self._max_tokens

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def entries_path(self) -> Path:
        return self._dir / _ENTRIES_FILE

    @property
    def report_path(self) -> Path:
        return self._dir / _REPORT_FILE

    # ── 写 ──
    def add(self, entry: dict, *, battle_id: str = "") -> dict:
        """新增一条。返回 `{ok, entry_id, reason}`；token 超限 → **拒绝不截断**。

        `entry_id` 已存在 → 幂等跳过（ok=True，reason="exists"）。
        """
        missing = [k for k in _ENTRY_REQUIRED if k not in entry]
        if missing:
            raise ValueError(f"GlobalMem 条目缺少必需键：{missing}。")
        tokens = estimate_tokens(entry["strategy_text"])
        if tokens > self._max_tokens:
            out = {"ok": False, "entry_id": entry["entry_id"],
                   "reason": f"策略文本 {tokens} token 超上限 {self._max_tokens}（拒绝，不截断）"}
            self._report("add", out, entry, battle_id, tokens)
            return out
        if self.get(entry["entry_id"]) is not None:
            out = {"ok": True, "entry_id": entry["entry_id"], "reason": "exists"}
            self._report("add", out, entry, battle_id, tokens)
            return out
        self._dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.entries_path, json.dumps(entry, ensure_ascii=False), append=True)
        out = {"ok": True, "entry_id": entry["entry_id"], "reason": "added"}
        self._report("add", out, entry, battle_id, tokens)
        return out

    def supersede(self, old_entry_id: str, new_entry: dict, *, battle_id: str = "") -> dict:
        """用 `new_entry` 替代旧条目（append-only）：新写一条 + 旧条目标 `superseded_by`。

        旧条目**不删除、不改文本**——只加标记，检索自动跳过。旧 id 不存在 → 退化为 add。
        """
        new_entry = dict(new_entry)
        new_entry["supersedes"] = old_entry_id
        out = self.add(new_entry, battle_id=battle_id)
        if not out["ok"]:
            return out
        if self.get(old_entry_id) is not None:
            self._patch(old_entry_id, {"superseded_by": new_entry["entry_id"]})
        self._report("supersede", out, new_entry, battle_id,
                     estimate_tokens(new_entry["strategy_text"]))
        return out

    def _patch(self, entry_id: str, fields: dict) -> bool:
        """按 id 改字段（Q / superseded_by），读全量 → 改 → 原子重写。"""
        entries = self.all()
        target = next((e for e in entries if e["entry_id"] == entry_id), None)
        if target is None:
            return False
        target.update(fields)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.entries_path,
                           "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
        return True

    # ── 读 ──
    def get(self, entry_id: str) -> dict | None:
        return next((e for e in self.all() if e["entry_id"] == entry_id), None)

    def all(self) -> list[dict]:
        """全部条目（含已被替代的）；坏行跳过——坏行容错。"""
        if not self.entries_path.exists():
            return []
        out: list[dict] = []
        try:
            lines = self.entries_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and "entry_id" in item:
                out.append(item)
        return out

    def active(self) -> list[dict]:
        """未被替代的条目（检索只看这些）。"""
        return [e for e in self.all() if not e.get("superseded_by")]

    def count(self) -> int:
        return len(self.active())

    # ── 内部 ──
    def _report(self, action: str, out: dict, entry: dict, battle_id: str, tokens: int) -> None:
        """审计落盘（每次 add/supersede 一行，可追溯到 battle_id）。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        row = {"ts": _now(), "action": action, "entry_id": out["entry_id"],
               "supersedes": entry.get("supersedes"), "battle_id": battle_id,
               "matchup_key": entry.get("matchup_key", ""), "token_est": tokens,
               "status": "ok" if out["ok"] else "rejected", "reason": out["reason"]}
        self._atomic_write(self.report_path, json.dumps(row, ensure_ascii=False), append=True)

    @staticmethod
    def _atomic_write(target: Path, text: str, *, append: bool = False) -> None:
        """同目录临时文件 + fsync + `os.replace`（与 MemoryStore 同款原子写）。"""
        existing = ""
        if append and target.exists():
            existing = target.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".gmem-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if existing:
                    f.write(existing)
                    if not existing.endswith("\n"):
                        f.write("\n")
                f.write(text)
                if append:
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def search_global_mem(store: GlobalMemStore, query: MatchupQuery, *,
                      delta: float = DEFAULT_DELTA, lam: float = DEFAULT_LAM,
                      top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """两段式检索（与局部 mem 同纪律）→ top_k（默认 1）。

    - **Phase A 硬过滤**：规模段（`3v2`）精确一致 + `data_digest` 一致（跨版本不可信）；
      再取 `similarity ≥ delta`；
    - **Phase B 排序**：`(1−λ)·z(sim) + λ·z(sigmoid(Q))`。

    只在硬过滤后的桶内排序 → **Q 的比较天然限定在同 matchup 桶内**（跨桶不可比）。
    """
    q_scale = (_parse_key(query.matchup_key) or {}).get("scale")
    cands: list[tuple[float, dict]] = []
    for e in store.active():
        prov = e.get("provenance") or {}
        if query.data_digest and prov.get("data_digest") \
                and query.data_digest != prov["data_digest"]:
            continue
        ep = _parse_key(e.get("matchup_key", ""))
        if ep is None or ep["scale"] != q_scale:
            continue
        sim = similarity(query.matchup_key, e["matchup_key"])
        if sim < delta:
            continue
        cands.append((sim, e))
    if not cands:
        return []
    z_sims = _zscore([c[0] for c in cands])
    z_qs = _zscore([sigmoid(c[1].get("Q", 0.0)) for c in cands])
    scored = [((1 - lam) * zs + lam * zq, sim, e)
              for zs, zq, (sim, e) in zip(z_sims, z_qs, cands)]
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["entry_id"]))   # 稳定序（分数→sim→id）
    return [e for _, _, e in scored[:top_k]]


def matchup_key_from_observation(observation: dict, *,
                                 source: DataSource = DEFAULT_SOURCE) -> str:
    """迷雾 observation → matchup_key（G2 注入路径用，**不需要 roster spec**）。

    为什么能这么做：引擎的迷雾模型是「团队预览」——`me` 侧全见（含 stats.speed 与技能名），
    `opponent` 侧公开精灵名/系别但技能未揭示，`rules` 段含 team_size/lives。三个分量齐全，
    且全部是玩家**合法可见**的信息，比把 roster spec 传进玩家更安全（后者会绕过迷雾）。

    对手侧只取 `types`/`names`（与 `matchup_key` 同口径，防技能透题）。
    """
    me, foe = observation["me"], observation["opponent"]
    rules = observation.get("rules") or {}
    my_roster = [{"name": u.get("name", ""), "types": u.get("types") or [],
                  "stats": u.get("stats") or {},
                  "skills": [s.get("name") for s in (u.get("skills") or [])]}
                 for u in me["units"]]
    foe_roster = [{"name": u.get("name", ""), "types": u.get("types") or []}
                  for u in foe["units"]]
    return matchup_key(my_roster, foe_roster,
                       team_size=rules.get("team_size", len(my_roster)),
                       lives=rules.get("lives", 0), source=source)


def make_global_retriever(store: "GlobalMemStore", *, data_digest: str,
                          source: DataSource = DEFAULT_SOURCE,
                          delta: float = DEFAULT_DELTA, lam: float = DEFAULT_LAM,
                          top_k: int = DEFAULT_TOP_K):
    """构造 GlobalMem 检索器 `(side, observation) -> list[dict]`（注入 LLMPlayer 用）。

    闭包捕获 store 与当前 `data_digest`（跨版本硬隔离）。签名吃 observation 而非
    matchup_key —— 因为建键需要 evolution 层的 `matchup_key`，玩家不能 import（循环依赖）。
    """
    def retrieve(_side: str, observation: dict) -> list[dict]:
        key = matchup_key_from_observation(observation, source=source)
        return search_global_mem(store, MatchupQuery(matchup_key=key, data_digest=data_digest),
                                 delta=delta, lam=lam, top_k=top_k)
    return retrieve


def update_global_q(store: GlobalMemStore, entry_id: str, won: bool, *,
                    alpha: float = DEFAULT_ALPHA) -> float:
    """加载过的 GlobalMem 打完一局 → Q 的 EMA 更新 + 使用计数。

    `Q ← Q + α(win − Q)`；同时 `n_used += 1`、`n_wins += won`。
    条目不存在 → KeyError（宁失败不抛，防脏更新）。

    ⚠️ **Q 只在同 matchup 桶内可比**——桶内阵容强度混杂基本抵消；跨桶比 Q 是错的。
    """
    entry = store.get(entry_id)
    if entry is None:
        raise KeyError(f"GlobalMem 条目不存在：{entry_id}")
    reward = 1.0 if won else 0.0
    new_q = round(entry["Q"] + alpha * (reward - entry["Q"]), 4)
    store._patch(entry_id, {"Q": new_q,
                            "n_used": entry.get("n_used", 0) + 1,
                            "n_wins": entry.get("n_wins", 0) + (1 if won else 0)})
    return new_q


