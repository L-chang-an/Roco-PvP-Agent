"""R5 价值函数档位：V_heuristic 上线（R0 已有）+ 可选档位 A（轨迹回归，中局 AUC 门）。

- **档位 A-0（默认）**：`v_heuristic(state, side)`（R0，确定性启发式：0.6·Δlives +
  0.3·Δhpratio + 0.1·Δenergy）——零训练、立即可用；
- **档位 A（可选训练）**：`train_value_fn(records)` —— situation_key 特征 → 终局结果
  的**逻辑回归**（纯 Python 梯度下降，零第三方依赖）。**中局（phase=mid）样本
  AUC ≥ 0.75 才启用**，否则返回 None 降级回 A-0（宁用简单启发式，不用坏模型）。
- 训练/评测**确定性**：固定 seed 初始化、固定特征顺序、无随机采样。
- `value_fn` 只供**反思/信度分配**离线使用（`show_v=False` 的玩家路径永远拿不到）——
  迷雾不变量：V 值从不注入对战玩家。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from roco_pvp_agent.battle.evolution.analysis import analyze_record

# 能量档 → 数值（与 run.py 健康度同口径；energy_max=10 → low≤2/mid≤5/high>5）。
_BAND_NUM = {"low": 0.15, "mid": 0.40, "high": 0.80}
# 阶段 → 数值（early/mid/late）。
_PHASE_NUM = {"early": 0.0, "mid": 1.0, "late": 2.0}

# 档位 A 启用门槛（§ R5：中局 AUC ≥ 0.75 才启用训练模型）。
AUC_GATE = 0.75

# 特征维度（situation_key 的 9 个数值分量）。
_FEAT_DIM = 9


def _sigmoid(z: float) -> float:
    """数值稳定 sigmoid（大正/大负不溢出）。"""
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _key_features(situation_key: str) -> list[float]:
    """situation_key（10 段）→ 9 维数值特征（确定性；异常键 → 全 0 容错）。"""
    p = situation_key.split("/")
    if len(p) != 10:
        return [0.0] * _FEAT_DIM
    return [
        float(p[0][2:]) if p[0].startswith("my") else 0.0,     # my_lives
        float(p[1][3:]) if p[1].startswith("foe") else 0.0,    # foe_lives
        _BAND_NUM.get(p[4], 0.5),                              # my_energy_band
        _BAND_NUM.get(p[5], 0.5),                              # foe_energy_band
        float(p[6]) if p[6].lstrip("-").isdigit() else 0.0,    # foe_revealed_skills
        _PHASE_NUM.get(p[7], 1.0),                             # phase
        float(p[8]) if p[8].lstrip("-").isdigit() else 0.0,    # my_bench
        float(p[9]) if p[9].lstrip("-").isdigit() else 0.0,    # foe_bench
        1.0,                                                   # 偏置项（内建 bias）
    ]


def _logistic_train(X: list[list[float]], y: list[float], *,
                    seed: int = 0, lr: float = 0.1, max_iter: int = 800) -> list[float]:
    """逻辑回归（梯度下降，纯 Python）：返回权重 w（含 bias 分量）。

    二元交叉熵 + 全批梯度下降。确定性：固定 seed 初始化、固定样本顺序。
    """
    if not X or len(X) != len(y):
        raise ValueError("X/y 数量不一致或为空。")
    n = len(X)
    rng = random.Random(seed)
    w = [rng.uniform(-0.01, 0.01) for _ in range(_FEAT_DIM)]
    for _ in range(max_iter):
        grad = [0.0] * _FEAT_DIM
        for xi, yi in zip(X, y):
            p = _sigmoid(sum(xi[j] * w[j] for j in range(_FEAT_DIM)))
            err = p - yi
            for j in range(_FEAT_DIM):
                grad[j] += err * xi[j]
        for j in range(_FEAT_DIM):
            w[j] -= lr * grad[j] / n
    return w


def _auc(y_true: list[float], y_score: list[float]) -> float:
    """秩 AUC（Mann-Whitney U / trapezoid，纯 Python）。退化（无正/负例）→ 0.5。"""
    pos = [(s, 1) for s, y in zip(y_score, y_true) if y > 0]
    neg = [(s, 0) for s, y in zip(y_score, y_true) if y <= 0]
    if not pos or not neg:
        return 0.5
    pairs = 0
    for ps, _ in pos:
        for ns, _ in neg:
            if ps > ns:
                pairs += 1
            elif ps == ns:
                pairs += 0.5
    return pairs / (len(pos) * len(neg))


@dataclass
class ValueFn:
    """可训练价值函数（档位 A）：`value(state, side)` 接口与启发式同形。"""

    weights: list[float]
    auc: float                # 中局 AUC（达标才启用）

    def value(self, state, side: str) -> float:
        """按特征给状态打分：P(侧获胜) 的 logit（正 = 对 side 有利）。

        `state` 是 BattleSession 或带 `.view(side)` 的对象——取**迷雾 view()**（白名单口径）
        派生 situation_key（m5 修复：evaluate 无顶层 `view` 函数，误 import 会首调即炸）。
        """
        from environment.evaluate import situation_key
        v = state if isinstance(state, dict) else state.view(side)
        key = situation_key(v)
        x = _key_features(key)
        return sum(x[j] * self.weights[j] for j in range(_FEAT_DIM))


def train_value_fn(records: list[dict], *, seed: int = 0,
                   auc_gate: float = AUC_GATE) -> ValueFn | None:
    """档位 A 训练：轨迹湖 → 特征 × 终局结果 → 逻辑回归。

    - 每条轨迹每回合每方一条样本：situation_key 特征 + 该方是否最终获胜；
    - **中局（phase=mid）样本算 AUC**，≥ `auc_gate`（0.75）才返回可启用模型；
    - 未达标 / 数据不足 → None（**降级回 V_heuristic**——宁用简单启发式不用坏模型）。
    - 确定性：`seed` 固定初始化；重放走 `analyze_record`（引擎纯转移）。
    """
    X: list[list[float]] = []
    y: list[float] = []
    mid_idx: list[int] = []            # 中局样本下标（AUC 门用）
    for rec in records:
        winner = rec.get("winner")
        if winner not in ("a", "b"):
            continue
        try:
            analysis = analyze_record(rec)
        except Exception:
            continue                    # 轨迹不可信 → 跳过（宁缺毋滥）
        for ta in analysis.turns:
            for side in ("a", "b"):
                key = ta.situation_keys.get(side) or ""
                features = _key_features(key)
                idx = len(X)
                X.append(features)
                y.append(1.0 if winner == side else 0.0)
                if key.split("/")[7:8] == ["mid"] and len(key.split("/")) == 10:
                    mid_idx.append(idx)
    if len(X) < 16 or len(mid_idx) < 4:                 # 数据不足 → 不启用
        return None
    w = _logistic_train(X, y, seed=seed)
    mid_auc = _auc([y[i] for i in mid_idx],
                   [sum(X[i][j] * w[j] for j in range(_FEAT_DIM)) for i in mid_idx])
    if mid_auc < auc_gate:
        return None                                      # 中局 AUC 不达标 → 降级
    return ValueFn(weights=w, auc=mid_auc)


def v_provider(records: list[dict] | None = None, *, seed: int = 0) -> "ValueFn | None":
    """V 档位选择（§ R5 上线入口）：给了可信轨迹湖 → 试档位 A；否则 None（调用方用启发式）。"""
    if records:
        trained = train_value_fn(records, seed=seed)
        if trained is not None:
            return trained
    return None
