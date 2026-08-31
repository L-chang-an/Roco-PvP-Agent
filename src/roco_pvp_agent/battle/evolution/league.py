"""R0 联赛基架（部分）+ R4 补全：Elo + α-rank + 收益矩阵 + 对手采样 + 剥削者 + 回归门。

R0 交付**数学底座**与**数据结构**（Elo / α-rank / PayoffMatrix）。R4 把这里补全为
真正的联赛：
- `sample_opponent`：Pareto 前沿按**领先实例数**加权采样；前沿退化（领先数齐平/只剩
  Champion）→ PFSP `P(o) ∝ (1−wr)^2` 兜底；前沿只剩自己 → 风格对手（多样性兜底）；
- `Exploiter`：专职剥削者（§九）——独立分支，唯一目标是打爆当前 Champion。不进主线、
  不参与晋级；胜率 = Champion 的**可利用性**（进复合分 0.15）；每 `EXPLOITER_RESET_EVERY`
  step 从 Champion fork 重置（防它自己过拟合）；
- `promotion_gate`：历史回归门——晋级前对历史池（archive + 历次 Champion）全部跑一遍，
  **对任一历史成员胜率 ≥ 45%** 才放行（「真实变强而非循环」的硬检验）。

一切纯函数、零第三方依赖；α-rank 的 2×2 支配与 RPS 均匀两个性质被单测钉死。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from roco_pvp_agent.battle.evolution.playbook import Playbook
    from roco_pvp_agent.battle.evolution.pool import PlaybookPool

# α-rank 的默认温度（winrate 差 Δ∈[−1,1]，α=10 → |Δ|=0.2 时 sigmoid(2)≈0.88，
# 能显著区分强弱；α=0 退化为均匀分布）。
DEFAULT_ALPHA = 10.0

# Elo 默认 K 值（标准棋类；评分只用于相对排序，量纲不重要）。
DEFAULT_K = 32

# 历史回归门下限（§九：对任一历史成员胜率 ≥ 45% 才晋级）。
REGRESSION_FLOOR = 0.45

# 专职剥削者重置周期（每 EXPLOITER_RESET_EVERY step 从当前 Champion fork）。
EXPLOITER_RESET_EVERY = 2

# PFSP 兜底的权重下限（(1−wr)^2 可为 0 → 除零/零权重兜底）。
_PFSP_EPS = 1e-3

# 风格标签前缀（sample_opponent 多样性兜底返回 `"style:attack"` 等，run.py 解析成 StylePlayer）。
STYLE_TAG_PREFIX = "style:"


def elo_update(ra: float, rb: float, score_a: float, *, K: float = DEFAULT_K) -> tuple[float, float]:
    """一对双方评分 → 对局后新评分。`score_a` = a 方结果 ∈ {0, 0.5, 1}。

    标准 Elo：`expected_a = 1/(1+10^((rb-ra)/400))`；`new_a = ra + K(score_a − expected_a)`。
    """
    expected_a = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
    new_a = ra + K * (score_a - expected_a)
    new_b = rb + K * ((1.0 - score_a) - (1.0 - expected_a))
    return new_a, new_b


def alpha_rank(M: list[list[float]], *, alpha: float = DEFAULT_ALPHA,
               tol: float = 1e-12, max_iter: int = 10_000) -> list[float]:
    """α-rank 平稳分布（策略强度排序）。`M[i][j]` = i 对 j 的胜率（0..1）。

    `a[i][j] = 1/(1+exp(−α·(M[i][j]−M[j][i])))` 是「i 在成对比较中胜过 j」的概率；
    转移矩阵 `C[i][j] = a[j][i] / Σ_k a[k][i]`（从 i 流向打得过 i 的策略 j，行随机）；
    平稳分布 = C 的**左**特征向量（`π[j] = Σ_i π[i]·C[i][j]`），幂迭代收敛。
    返回值归一化、和为 1。`alpha=0` → 均匀分布（对循环游戏也是均匀，见 RPS 单测）。
    """
    n = len(M)
    if n == 0:
        return []
    a = [[1.0 / (1.0 + math.exp(-alpha * (M[i][j] - M[j][i]))) for j in range(n)] for i in range(n)]
    col_sums = [sum(a[k][i] for k in range(n)) for i in range(n)]
    # col_sums[i] 恒 > 0（a[i][i] = 0.5 是正项），无需除零兜底。
    C = [[a[j][i] / col_sums[i] for j in range(n)] for i in range(n)]
    pi = [1.0 / n] * n
    for _ in range(max_iter):
        nxt = [sum(pi[i] * C[i][j] for i in range(n)) for j in range(n)]
        diff = max(abs(nxt[j] - pi[j]) for j in range(n))
        pi = nxt
        if diff < tol:
            break
    return pi


@dataclass
class PayoffMatrix:
    """收益矩阵（R4 联赛数据载体，R0 占位版）：策略名 → 成对胜率累积。

    `record(i, j, score)` 记一局 i 对 j 的 **i 视角** 结果（score ∈ {0, 0.5, 1}）。
    同一对局双方视角各记一次（调用方负责），`matrix()` 给出 M[i][j] = i 对 j 的胜率。
    """

    names: list[str] = field(default_factory=list)
    _wins: dict[tuple[int, int], float] = field(default_factory=dict)
    _games: dict[tuple[int, int], int] = field(default_factory=dict)

    def add(self, name: str) -> int:
        """登记一个策略名，返回其下标（已存在 → 返回原下标）。"""
        if name in self.names:
            return self.names.index(name)
        self.names.append(name)
        return len(self.names) - 1

    def record(self, i: str, j: str, score: float) -> None:
        """记一局：`i` 对 `j`，i 视角得分 score ∈ {0, 0.5, 1}。"""
        if score not in (0, 0.5, 1):
            raise ValueError(f"score 必须是 0/0.5/1，实际 {score!r}。")
        ii, jj = self.add(i), self.add(j)
        key = (ii, jj)
        self._wins[key] = self._wins.get(key, 0.0) + score
        self._games[key] = self._games.get(key, 0) + 1

    def record_winrate(self, i: str, j: str, winrate: float, *, n: int = 1) -> None:
        """批量记一组成对结果：`i` 对 `j` 的 `n` 局整体胜率（R4 门禁/采样共用）。

        `winrate ∈ [0,1]`、`n ≥ 1`；等价于把 `winrate·n` 局胜利摊进 `n` 局里——
        `winrate()` 读出的累积胜率一致，省掉逐局 record 的 N 次调用。
        """
        if not 0.0 <= winrate <= 1.0:
            raise ValueError(f"winrate 必须在 [0,1]，实际 {winrate!r}。")
        if n < 1:
            raise ValueError(f"n 必须 ≥1，实际 {n!r}。")
        ii, jj = self.add(i), self.add(j)
        key = (ii, jj)
        self._wins[key] = self._wins.get(key, 0.0) + winrate * n
        self._games[key] = self._games.get(key, 0) + n

    def winrate(self, i: str, j: str) -> float:
        """i 对 j 的累积胜率（无对局 → 0.5 中性）。"""
        ii, jj = self.add(i), self.add(j)
        key = (ii, jj)
        games = self._games.get(key, 0)
        return (self._wins.get(key, 0.0) / games) if games else 0.5

    def matrix(self) -> list[list[float]]:
        """M[i][j] = i 对 j 的胜率（α-rank 的输入形状）。"""
        n = len(self.names)
        return [[self.winrate(self.names[i], self.names[j]) for j in range(n)] for i in range(n)]

    def to_dict(self) -> dict:
        """纯 dict（池持久化）。"""
        return {"names": list(self.names),
                "wins": {f"{i}:{j}": v for (i, j), v in self._wins.items()},
                "games": {f"{i}:{j}": v for (i, j), v in self._games.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "PayoffMatrix":
        m = cls()
        m.names = list(d.get("names", []))
        for k, v in (d.get("wins") or {}).items():
            i, j = (int(x) for x in k.split(":"))
            m._wins[(i, j)] = v
        for k, v in (d.get("games") or {}).items():
            i, j = (int(x) for x in k.split(":"))
            m._games[(i, j)] = v
        return m


# ---------------------------------------------------------------------------
# R4 联赛补全：对手采样 / 专职剥削者 / 历史回归门
# ---------------------------------------------------------------------------


def pfsp_weights(winrates: list[float]) -> list[float]:
    """PFSP 兜底权重：`P(o) ∝ (1−wr)^2`（§四——前沿退化时，越打不过的对手权重越高）。

    用于暴露 Champion 弱点的对手采样；恒给 `_PFSP_EPS` 保底（零权重 → 永不被选）。
    """
    return [(1.0 - w) ** 2 + _PFSP_EPS for w in winrates]


def sample_opponent(pool: "PlaybookPool", *, seed: int):
    """对手采样（§四）：Pareto 前沿按**领先实例数**加权；退化 → PFSP 兜底。

    - 前沿有 ≥2 个非 Champion 成员且领先数分化 → `meta_share` 加权（能打赢多少种对手）；
    - 前沿退化（领先数齐平 / 只剩 Champion+1）→ PFSP `P(o) ∝ (1−wr)^2`（优先打 Champion
      打不过的，暴露弱点）；
    - 前沿只剩 Champion 自己 → 返回风格标签 `"style:attack"` 等（多样性兜底，不与自己打）。

    确定性：同 `seed` 同池状态 → 同选择（stdlib `random.Random`，MT19937 跨版本稳定）。
    返回值：前沿成员的 `Playbook`，或 `"style:<name>"` 标签（run.py 解析成 StylePlayer）。
    """
    from roco_pvp_agent.battle.evolution.playbook import Playbook  # noqa: F401 (hint only)
    rng = random.Random(seed)
    front = pool.pareto_front()
    champ = pool.champion()
    if champ is None:
        # 池为空 / 未设 Champion（防御，run_steps 初始化后不会触发）→ 风格兜底
        style = rng.choice(["attack", "status", "stall", "energy_denial"])
        return f"{STYLE_TAG_PREFIX}{style}"
    others = [e for e in front if e.playbook.version != champ.playbook.version]
    if others:
        leading = [pool.meta_share(e) for e in others]
        if len(others) >= 2 and any(l > 1 for l in leading):
            chosen = rng.choices(others, weights=leading)[0]
        else:
            wrs = [pool.payoff.winrate(champ.playbook.version, e.playbook.version)
                   for e in others]
            chosen = rng.choices(others, weights=pfsp_weights(wrs))[0]
        return chosen.playbook
    style = rng.choice(["attack", "status", "stall", "energy_denial"])
    return f"{STYLE_TAG_PREFIX}{style}"


def promotion_gate(candidate: "Playbook", history: list["Playbook"],
                   eval_winrate: Callable[["Playbook", "Playbook"], float]
                   ) -> tuple[bool, list[dict]]:
    """历史回归门（§九）：候选对每个历史成员（archive + 历次 Champion）胜率 ≥45%。

    `eval_winrate(candidate, member) -> float` 由调用方注入（确定性小批 / 真实评测）。
    任一成员 <45% → 拒绝晋级（「真实变强而非循环」的硬检验；对规则极端的对手也如此）。
    **空历史 → 空真通过**（无成员可回归 = 不存在策略循环的载体；首个改进可晋级）。
    返回 (pass, 逐成员行 [{against, winrate, pass}]——审计可追溯)。
    """
    rows: list[dict] = []
    for h in history:
        wr = eval_winrate(candidate, h)
        rows.append({"against": h.version, "winrate": wr,
                     "pass": wr >= REGRESSION_FLOOR})
    ok = all(r["pass"] for r in rows)
    return ok, rows


def _mark_exploiter(pb: "Playbook") -> "Playbook":
    """给手册打上「打爆 Champion」标记：M2 追加一行 + 独立版本标签。

    离线路径（PlaybookPlayer）里标记改变手册**文本** → 指纹种子不同 → 策略与 Champion
    分化，可利用性成为真实信号（否则同文本同种子的纯副本恒平局 0.5，0.15 项惰性）；
    真实路径（LLMPlayer strategy）里标记指导 LLM 专攻 Champion 薄弱点。
    返回**新对象**，不污染传入手册。
    """
    pb = pb.copy()
    pb.version = f"{pb.version}-exploiter"
    m = pb.module("M2 action_selector")
    if m is not None:
        m.text = (m.text + "\n专注打爆当前 Champion（抓其薄弱点）。").strip()
    return pb


@dataclass
class Exploiter:
    """专职剥削者（§九）：独立分支，唯一目标是打爆当前 Champion。

    - **fork**：克隆当前 Champion 并打上「打爆」标记（`_mark_exploiter`）——策略与
      Champion 分化（离线：指纹种子变；真实：LLM 收到专攻指令）；
    - **不进主线、不参与晋级**（不调用 pool.add/pool.try_promote）；
    - `measure(champion, eval_winrate)`：胜率 = Champion 的**可利用性**（进复合分 0.15）；
    - 对局注入收益矩阵（§九②：对手池数据可见，供 α-rank/PFSP 采样参考）；
    - 每 `EXPLOITER_RESET_EVERY` step 重置为当前 Champion 的新 fork（防它自己过拟合；
      R5 接 epoch 后按规划改为每 2 epoch 重置）。
    """

    playbook: "Playbook"
    created_at: int = 0
    last_exploitability: float = 0.5

    @classmethod
    def fork(cls, champion: "Playbook", *, step: int, seed: int) -> "Exploiter":
        """从 Champion 克隆 + 打标记（不沿用旧分支的进化史）。"""
        return cls(playbook=_mark_exploiter(champion), created_at=step,
                   last_exploitability=0.5)

    def measure(self, champion: "Playbook",
                eval_winrate: Callable[["Playbook", "Playbook"], float]) -> float:
        """对 Champion 跑一组成对局 → 剥削者胜率 = Champion 的可利用性。"""
        self.last_exploitability = eval_winrate(self.playbook, champion)
        return self.last_exploitability

    def reset(self, champion: "Playbook", *, step: int) -> None:
        """重置：从当前 Champion 重新 fork（保持 seed 确定性由调用方控制）。"""
        self.playbook = _mark_exploiter(champion)
        self.created_at = step
        self.last_exploitability = 0.5
