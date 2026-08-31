"""R4 Pareto 候选池（GEPA 实例级 Pareto 选择 + SkillOpt 晋级门）。

把 GEPA 的「实例级 Pareto 候选选择」与 PSRO 的「策略种群」合成一个数据结构（§四）：
每个池成员 = Playbook + D_sel 实例分数向量（对冻结基准对手的配对胜率）。三个免费好处——
防策略循环（三代前的候选只要还在某个实例上领先就不被淘汰）、对手分布不用手调（数据决定）、
非传递性从缺陷变资产（收益矩阵反对称分量 = 池健康度，见 league.py）。

- `add(playbook, scores)`：**入池门（宽）**——分数向量在至少一个实例上取得**池内最优**
  （严格优于全部前沿成员）才入池；入池后跑 Pareto 剪枝（被支配者 → archive）与
  `P_max=12` 冗余剔除（高相关 + 领先实例最少者 → archive）。archive 永不删除，仍作回归对手。
- `try_promote(name, regression_ok)`：**晋级门（严）**——复合分**严格**超过当前 Champion 才
  晋级（SkillOpt 平手拒绝：被拒编辑成为有信息的负反馈，而非隐藏状态）；历史回归门结果由
  调用方（run_steps + league.promotion_gate）算好传入。
- `best()`：**双游标**——历史最优仅当复合分**超越**才封存（best_skill）。
- 复合分 = `0.70·对前沿按 meta_share 加权的配对胜率 + 0.15·(1−可利用性) + 0.10·(1−Brier) +
  0.05·健康度`（§五；缺失项取中性默认，不阻塞、但可审计地压低全量可比性）。

一切纯函数/纯数据结构（`_dominates` / `_correlation`），零第三方依赖、确定性可复现——
分数向量相同则池状态相同（`save`/`load` 可续跑）。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from roco_pvp_agent.battle.evolution.league import PayoffMatrix
from roco_pvp_agent.battle.evolution.playbook import Playbook

# 前沿上限（§四：P_max = 12；超出剔除「冗余而非弱」的成员进 archive）。
P_MAX = 12
# 两成员分数向量相关性超过此值 → 冗余候选（在同一批实例上赢一样的对手）。
CORR_REDUNDANT = 0.9

# 复合分权重（§五）。
W_PARED = 0.70
W_EXPLOIT = 0.15
W_BRIER = 0.10
W_HEALTH = 0.05

# 缺失项的中性默认：未测可利用性/校准/健康时不伪造数值（默认把该项权重压到中间值）。
_DEF_EXPLOIT = 0.5
_DEF_BRIER = 0.5
_DEF_HEALTH = 1.0


def _dominates(a: dict[str, float], b: dict[str, float]) -> bool:
    """分数向量支配：a 对每个实例 ≥ b，且至少一个实例严格 >（GEPA 策略支配关系）。"""
    strict = False
    for k in b:
        if a[k] < b[k]:
            return False
        if a[k] > b[k]:
            strict = True
    return strict


def _version_num(v: str) -> int:
    """版本号 → 数值（`"pb_v10"` → 10；非 `pb_vN` → -1）。P_max 平手剔除按数值序。"""
    if v.startswith("pb_v"):
        try:
            return int(v[len("pb_v"):])
        except ValueError:
            pass
    return -1


def _copy_payoff_rows(payoff, src_version: str, dst_version: str) -> None:
    """把 src 版本的成对胜率复制给 dst（行为相同的慢更新副本——复合分不被中性 0.5 稀释，m3）。

    PayoffMatrix 按 (src_index, 对手) 与 (对手, src_index) 存 wins/games；逐一复制到 dst 下标。
    """
    src = payoff.add(src_version)
    dst = payoff.add(dst_version)
    for (i, j), wins in list(payoff._wins.items()):
        games = payoff._games.get((i, j), 0)
        if i == src:
            payoff._wins[(dst, j)] = payoff._wins.get((dst, j), 0.0) + wins
            payoff._games[(dst, j)] = payoff._games.get((dst, j), 0) + games
        if j == src:
            payoff._wins[(i, dst)] = payoff._wins.get((i, dst), 0.0) + wins
            payoff._games[(i, dst)] = payoff._games.get((i, dst), 0) + games


def _correlation(a: dict[str, float], b: dict[str, float]) -> float:
    """两个分数向量的 Pearson 相关（-1..1）。常数向量无方差 → 相同=1.0、否则 0.0。"""
    keys = list(a)
    if not keys:
        return 0.0
    va = [a[k] for k in keys]
    vb = [b[k] for k in keys]
    ma = sum(va) / len(va)
    mb = sum(vb) / len(vb)
    sxy = sum((x - ma) * (y - mb) for x, y in zip(va, vb))
    sxx = sum((x - ma) ** 2 for x in va)
    syy = sum((y - mb) ** 2 for y in vb)
    den = math.sqrt(sxx * syy)
    if den == 0.0:
        return 1.0 if all(x == y for x, y in zip(va, vb)) else 0.0
    return sxy / den


@dataclass
class PoolEntry:
    """一个池成员：Playbook + D_sel 分数向量 + 元数据。"""

    playbook: Playbook
    scores: dict[str, float]           # 实例名 → 胜率（与池的 instance_names 对齐）
    status: str = "front"              # "front" | "archived"
    brier: float | None = None         # 校准 (1−Brier)；None → 中性 0.5
    health: float | None = None        # 合法/降级健康度；None → 1.0
    composite: float = 0.0             # 最近一次计算的复合分（审计快照）
    archived_reason: str = ""          # 入 archive 的原因（支配 / P_max 冗余 / 硬上限）


def _entry_to_dict(e: PoolEntry) -> dict:
    return {"playbook": e.playbook.to_dict(), "scores": e.scores, "status": e.status,
            "brier": e.brier, "health": e.health, "composite": e.composite,
            "archived_reason": e.archived_reason}


def _entry_from_dict(d: dict) -> PoolEntry:
    return PoolEntry(playbook=Playbook.from_dict(d["playbook"]), scores=dict(d["scores"]),
                     status=d.get("status", "front"), brier=d.get("brier"),
                     health=d.get("health"), composite=d.get("composite", 0.0),
                     archived_reason=d.get("archived_reason", ""))


class PlaybookPool:
    """Pareto 候选池（§四/§五 ⑩）：GEPA 宽入池门 + SkillOpt 严晋级门。"""

    def __init__(self, instance_names: list[str]) -> None:
        """`instance_names`：D_sel 实例名（有序，冻结）——池的选择空间。"""
        self._instances: list[str] = list(instance_names)
        self._members: list[PoolEntry] = []
        self._by_version: dict[str, PoolEntry] = {}
        self._champion_name: str | None = None
        self._best_name: str | None = None
        self._best_score: float = -math.inf
        self._past_champions: list[str] = []             # 历次 Champion（历史回归门的「历史池」）
        self._exploitability: dict[str, float] = {}    # 成员版本 → 专职剥削者攻破率
        self._payoff = PayoffMatrix()                  # 成员间成对胜率（PFSP/α-rank/复合分原料）

    # ── 只读视图 ──
    @property
    def instances(self) -> list[str]:
        return list(self._instances)

    @property
    def payoff(self) -> PayoffMatrix:
        """成员间收益矩阵（PFSP 兜底 / 复合分加权 / α-rank 健康度共用）。"""
        return self._payoff

    @property
    def members(self) -> list[PoolEntry]:
        return list(self._members)

    @property
    def archive(self) -> list[PoolEntry]:
        return [e for e in self._members if e.status == "archived"]

    def member(self, version: str) -> PoolEntry | None:
        return self._by_version.get(version)

    def next_version(self) -> str:
        """下一个全局唯一版本号（R4 多步循环：父代不变时候选版本不撞车）。"""
        nums: list[int] = []
        for e in self._members:
            v = e.playbook.version
            if v.startswith("pb_v"):
                try:
                    nums.append(int(v[len("pb_v"):]))
                except ValueError:
                    pass
        return f"pb_v{max(nums, default=-1) + 1}"

    def pareto_front(self) -> list[PoolEntry]:
        """未被任何前沿成员支配的成员（§四「不被任何单一策略全面压制」）。

        **Champion 恒在前沿**（在线策略保护）：即使其分数向量被支配，也不从前沿消失
        ——晋级只经 `try_promote`（复合分严格超过），不与支配关系混同（GEPA 宽门 ≠
        SkillOpt 严门）。
        """
        champ = self._by_version.get(self._champion_name)
        front = [e for e in self._members if e.status == "front"]
        return [e for e in front
                if e is champ
                or not any(o is not e and _dominates(o.scores, e.scores) for o in front)]

    def winners(self) -> list[PoolEntry]:
        """GEPA Alg.2：每实例最高分候选集合（并列都算）；空池 → []（防 max() 空序列）。"""
        out: list[PoolEntry] = []
        front = self.pareto_front()
        if not front:
            return out
        for inst in self._instances:
            mx = max(m.scores[inst] for m in front)
            for m in front:
                if m.scores[inst] == mx and m not in out:
                    out.append(m)
        return out

    def leading_instances(self, entry: PoolEntry) -> list[str]:
        """成员「领先实例」：该成员是（并列）池内最优的实例（GEPA 领先实例数原料）。
        不在前沿的成员 → []（防空洞真值返回全部实例）。"""
        front = self.pareto_front()
        if entry not in front:
            return []
        return [inst for inst in self._instances
                if all(entry.scores[inst] >= m.scores[inst] for m in front)]

    def meta_share(self, entry: PoolEntry) -> int:
        """成员的 meta_share = 领先实例数（§四「按领先实例数加权」）。"""
        return len(self.leading_instances(entry))

    def champion(self) -> PoolEntry | None:
        """当前 Champion（仅经 `try_promote` 晋级更新；平手拒绝 → 不自动换）。"""
        return self._by_version.get(self._champion_name) if self._champion_name else None

    def best(self) -> PoolEntry | None:
        """历史最优（双游标：仅当复合分**超越** best_score 才封存）。"""
        return self._by_version.get(self._best_name) if self._best_name else None

    def past_champions(self) -> list[PoolEntry]:
        """历次 Champion（晋级被替换掉的在线策略——历史回归门的「历史池」之一）。"""
        return [self._by_version[v] for v in self._past_champions if v in self._by_version]

    def history(self) -> list[PoolEntry]:
        """历史池（§九）：archive（被剔除成员）+ 历次 Champion（去重）——回归门全集。"""
        seen: dict[str, PoolEntry] = {}
        for e in self.archive + self.past_champions():
            seen.setdefault(e.playbook.version, e)
        return list(seen.values())

    # ── 复合分（§五，全部按需重算，缺失项取中性默认）──
    def composite_of(self, e: PoolEntry) -> float:
        paired = self._paired_winrate(e)
        expl = self._exploitability.get(e.playbook.version, _DEF_EXPLOIT)
        brier = _DEF_BRIER if e.brier is None else e.brier
        health = _DEF_HEALTH if e.health is None else e.health
        return (W_PARED * paired + W_EXPLOIT * (1.0 - expl)
                + W_BRIER * brier + W_HEALTH * health)

    def _paired_winrate(self, e: PoolEntry) -> float:
        """0.70 项：对 Pareto 前沿按 meta_share 加权的配对胜率（§五）。

        用收益矩阵的成对胜率；与 e 自己不算（wr(c,c)=0.5 是噪声）。未测对 → 0.5 中性。
        **权重取 `max(1, meta_share)`**：受保护的 Champion（即使被支配、领先实例数为 0）
        仍是必须打败的对象——权重塌成 0 会让「打不过 Champion 的挑战者」从自己的复合分里
        把 Champion 剔除（晋级失真）。每个前沿成员至少权重 1（也是「你要打过所有前沿策略」）。
        """
        front = self.pareto_front()
        others = [o for o in front if o.playbook.version != e.playbook.version]
        if not others:
            return 0.5
        num = sum(max(1, self.meta_share(o)) * self._payoff.winrate(e.playbook.version, o.playbook.version)
                  for o in others)
        den = sum(max(1, self.meta_share(o)) for o in others)
        return num / den if den else 0.5

    def set_exploitability(self, version: str, value: float) -> None:
        """记录某成员的**可利用性**（专职剥削者对它的胜率，进复合分 0.15 项）。"""
        self._exploitability[version] = float(value)

    def record_game(self, a_version: str, b_version: str, score_a: float, *, n: int = 1) -> None:
        """记一局（或 n 局整体）成对结果：a 对 b 的胜率（双方视角各记，调用方负责）。"""
        self._payoff.record_winrate(a_version, b_version, score_a, n=n)

    # ── 入池门（宽） + 剪枝 ──
    def add(self, playbook: Playbook, scores: dict[str, float], *,
            brier: float | None = None, health: float | None = None) -> dict:
        """入池门：向量在至少一个实例上取得池内最优才入池（§五 ⑩）。

        返回 outcome：`entered` / `reason` / `composite` / `front_size` / `archived`。
        """
        missing = [k for k in self._instances if k not in scores]
        if missing:
            raise ValueError(f"分数向量缺实例（{len(missing)} 个，如 {missing[:3]}）。")
        if playbook.version in self._by_version:
            raise ValueError(f"版本「{playbook.version}」已在池中。")
        entry = PoolEntry(playbook=playbook,
                          scores={k: scores[k] for k in self._instances},
                          brier=brier, health=health)
        entry.composite = self.composite_of(entry)

        best_on = None
        for inst in self._instances:
            s = entry.scores[inst]
            if not self._members or all(s > m.scores[inst] for m in self.pareto_front()):
                best_on = inst
                break
        if best_on is None:
            return {"entered": False,
                    "reason": f"宽门：未在任何实例上取得池内最优（{self._n_front()} 成员前沿）",
                    "composite": entry.composite, "front_size": self._n_front(), "archived": []}

        self._members.append(entry)
        self._by_version[playbook.version] = entry
        if self._champion_name is None:          # 首个成员即初始 Champion
            self._champion_name = playbook.version
            self._best_name = playbook.version
            self._best_score = entry.composite
        self._prune_dominated()
        self._prune_to_max()
        return {"entered": True, "reason": f"入池（实例「{best_on}」池内最优）",
                "composite": entry.composite,
                "front_size": self._n_front(),
                "archived": [e.playbook.version for e in self.archive]}

    def add_candidate(self, playbook: Playbook, scores: dict[str, float], *,
                      brier: float | None = None, health: float | None = None) -> bool:
        """`add` 的 bool 别名（对齐 plan 关键签名 `add_candidate(...) -> bool`）。"""
        return self.add(playbook, scores, brier=brier, health=health)["entered"]

    def per_instance_scores(self, pb: Playbook) -> dict[str, float]:
        """成员在 D_sel 实例集上的分数向量（对齐 plan 签名；不在池 → KeyError）。"""
        entry = self._by_version.get(pb.version)
        if entry is None:
            raise KeyError(f"Playbook「{pb.version}」不在池中。")
        return dict(entry.scores)

    def try_promote(self, version: str, *, regression_ok: bool) -> dict:
        """晋级门（严）：复合分**严格**超过当前 Champion 才晋级（§五 ⑩ / SkillOpt）。

        `regression_ok`：历史回归门结果（league.promotion_gate，调用方算好）。
        平手拒绝：被拒候选留在池内成为有信息的负反馈；晋级同时封存 best_skill（双游标）。
        """
        entry = self._by_version.get(version)
        if entry is None:
            return {"promoted": False, "reason": "候选不在池中", "composite": 0.0}
        if entry.status != "front":
            return {"promoted": False, "reason": "候选已被剪枝出前沿（archive）", "composite": 0.0}
        cur = self.champion()
        if cur is not None and entry.playbook.version == cur.playbook.version:
            return {"promoted": False, "reason": "已是当前 Champion", "composite": 0.0}
        if not regression_ok:
            return {"promoted": False, "reason": "历史回归门未过（对某历史成员胜率 < 45%）",
                    "composite": 0.0}
        cur_c = self.composite_of(cur) if cur else -math.inf
        cand_c = self.composite_of(entry)
        if not (cand_c > cur_c):
            return {"promoted": False,
                    "reason": f"复合分未严格超过（{cand_c:.3f} ≤ {cur_c:.3f}），平手拒绝",
                    "composite": cand_c}
        self._champion_name = entry.playbook.version
        if cand_c > self._best_score:            # 双游标：仅超越才封存 best
            self._best_name = entry.playbook.version
            self._best_score = cand_c
        if cur is not None:                      # 被替换的在线策略进「历史池」（回归门）
            if cur.playbook.version not in self._past_champions:
                self._past_champions.append(cur.playbook.version)
        return {"promoted": True,
                "reason": f"复合分 {cand_c:.3f} 严格超过 {cur_c:.3f}（{cur.playbook.version}）",
                "composite": cand_c}

    def apply_slow_update(self, playbook: Playbook, scores: dict[str, float]) -> dict:
        """epoch 慢更新的应用（**非竞争路径**，R5）。

        慢更新把被反复验证的规则固化进 `[PROTECTED]` 是**保守管理动作**：保护不改变对局
        行为（分数向量与现 Champion 相同），却阻止未来快速编辑破坏它——若走竞争晋级门
        （需复合分严格超过）必然平手拒绝，保护区永远无法生效。

        **门禁不豁免**：调用方（run_epochs）必须先过**非降级门**（新手册 D_sel 不劣于
        现 Champion）才调用本方法——会回退的慢更新照样被拒，不是免检通道。
        被替换的在线策略进「历史池」（回归门）。
        """
        if playbook.version in self._by_version:
            return {"applied": False, "reason": "版本已存在", "composite": 0.0}
        missing = [k for k in self._instances if k not in scores]
        if missing:
            raise ValueError(f"分数向量缺实例（{len(missing)} 个）。")
        entry = PoolEntry(playbook=playbook,
                          scores={k: scores[k] for k in self._instances})
        entry.composite = self.composite_of(entry)
        self._members.append(entry)
        self._by_version[playbook.version] = entry
        cur = self.champion()
        self._champion_name = playbook.version    # 慢更新后的手册成为新 Champion
        if cur is not None:
            if cur.playbook.version not in self._past_champions:
                self._past_champions.append(cur.playbook.version)
            # 新 Champion 与旧版本行为相同：复制旧版本的成对胜率（否则复合分 0.70 项全取
            # 中性 0.5 虚高改写 best_score——m3 修复）
            _copy_payoff_rows(self._payoff, cur.playbook.version, playbook.version)
            if cur.status == "front":
                # 旧 Champion 被慢更新版本替换：归档（避免前沿塞满行为相同的保护副本）
                cur.status = "archived"
                cur.archived_reason = "被 epoch 慢更新替换"
        if entry.composite > self._best_score:
            self._best_name = playbook.version
            self._best_score = entry.composite
        self._prune_to_max()                      # m3：慢更新入池也受 P_max 硬上限
        return {"applied": True, "composite": entry.composite}

    # ── 内部：剪枝 / 归档 ──
    def _n_front(self) -> int:
        return sum(1 for e in self._members if e.status == "front")

    def _front_list(self) -> list[PoolEntry]:
        return [e for e in self._members if e.status == "front"]

    def _archive_entry(self, e: PoolEntry, reason: str) -> None:
        if self._champion_name == e.playbook.version:
            return                              # Champion 永不被剪枝出前沿（在线策略保护）
        e.status = "archived"
        e.archived_reason = reason
        e.composite = self.composite_of(e)      # 归档时快照复合分（审计）

    def _prune_dominated(self) -> None:
        """Pareto 剪枝：被支配者 → archive（GEPA：前沿 = 不被任何策略全面压制）。
        **Champion 永不被剪**（在线策略保护）——即使其分数向量被支配（复合分由成对数据决定，
        支配≠复合分更高），也只归档非 Champion 的被支配者。"""
        champ = self._by_version.get(self._champion_name)
        changed = True
        while changed:
            changed = False
            front = self._front_list()
            for a in front:
                for b in front:
                    if a is not b and a is not champ and _dominates(b.scores, a.scores):
                        self._archive_entry(a, f"被 {b.playbook.version} 支配")
                        changed = True
                        break
                if changed:
                    break

    def _prune_to_max(self) -> None:
        """P_max=12 硬上限：优先剔除冗余对（相关 > CORR_REDUNDANT）里领先实例更少者；
        无冗余对仍超上限 → 剔领先实例最少者（冗余而非弱，进 archive 仍作回归对手）。
        **Champion 永不被剪**（在线策略保护）：冗余对被指向 Champion 时换另一侧。"""
        champ = self._by_version.get(self._champion_name)
        front = self._front_list()
        while len(front) > P_MAX:
            victim = None
            for a in front:
                for b in front:
                    if a is not b and _correlation(a.scores, b.scores) > CORR_REDUNDANT:
                        ca, cb = self.meta_share(a), self.meta_share(b)
                        cand = a if ca <= cb else b
                        victim = b if cand is champ else cand
                        break
                if victim is not None:
                    break
            reason = "P_max 冗余（分数向量高相关）" if victim is not None else "P_max 硬上限"
            if victim is None:
                candidates = [e for e in front if e is not champ]
                if not candidates:
                    break                       # 只剩 Champion，不能剪（防御）
                victim = min(candidates, key=lambda e: (self.meta_share(e),
                                                        _version_num(e.playbook.version)))
            self._archive_entry(victim, reason)
            front = self._front_list()

    # ── 持久化（审计 / 续跑）──
    def to_dict(self) -> dict:
        return {
            "instances": self._instances,
            "champion": self._champion_name,
            "best": self._best_name,
            "best_score": self._best_score,
            "past_champions": self._past_champions,
            "exploitability": self._exploitability,
            "members": [_entry_to_dict(e) for e in self._members],
            "payoff": self._payoff.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlaybookPool":
        pool = cls(d["instances"])
        pool._champion_name = d.get("champion")
        pool._best_name = d.get("best")
        pool._best_score = d.get("best_score", -math.inf)
        pool._past_champions = list(d.get("past_champions", []))
        pool._exploitability = dict(d.get("exploitability", {}))
        pool._payoff = PayoffMatrix.from_dict(d.get("payoff", {}))
        for md in d.get("members", []):
            e = _entry_from_dict(md)
            pool._members.append(e)
            pool._by_version[e.playbook.version] = e
        return pool

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "PlaybookPool":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
