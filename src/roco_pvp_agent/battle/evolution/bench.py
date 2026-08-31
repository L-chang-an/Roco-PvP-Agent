"""R0 配对评测：固定实例集 + 双向先后手配对胜率 + 95% CI（强弱可测的底座）。

- `build_instances(kind)`：确定性构造 d_sel / d_test 实例集（§八）。每个实例 =
  (名称, 场景标签, 双方 roster, 8 个固定 seed)。**d_sel 与 d_test 按家族切分候选池**
  （d_sel 前 3/4 家族、d_test 后 1/4 家族）——隐藏集与选择集 roster 不相交
  （§八「未公开阵容」，对抗「同一阵容跨集泄漏」）；seed 空间也完全不相交
  （第二道闸，`data_digest` 在 R1 接上）。同队家族唯一（跨家族候选天然满足）。
- `paired_eval(make_subject, make_opponent, instances)`：**双向先后手配对**——同一
  (实例, seed) 打两局：subject 先拿 roster_a 再拿 roster_b，对手反之。这样阵容强度
  差被对消，测得的是「玩家类型」的强度差而非阵容差。**镜像实例（双方同阵）只跑
  一个方向**——两方向逐位同局，去重否则 n_games 虚高、CI 过窄。subject 恒为 a 方，
  胜 = winner=="a"。确定性：引擎纯转移 + 玩家固定 seed → 同 seed 两次结果逐位相同
  （真实 LLM 除外）。返回含平铺别名 `winrate/ci95/ci95_low/ci95_high/n_games`
  （对齐 plan 关键签名）+ 更丰富的 `aggregate`。
- `wilson_ci`：Wilson 95% 二项置信区间（0/n、n/n 不退化）。

`make_subject/make_opponent: (side, seed) -> Player`——由 CLI 用 `build_player` 闭包注入，
真实 LLM / 假 LLM / 随机都走同一协议。实例 roster 由 `build_roster`（VALID）产出，
`run_match` 复用 E6 的迷雾收口（玩家只见 view()）。
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass

from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.match import run_match
from environment.presets import p1_team, valid_spirit_candidates
from environment.session import BattleSession
from environment.teambuilder import build_roster

# ── 进度观测钩子（横切关注点，同 logging 性质；**不是**业务依赖）──
# CLI 进度条用 `progress_sink()` 作用域化设置；库调用方不设 → `paired_eval` 零开销、
# 行为完全不变。作用域退出恢复前值，不留长期全局状态。
_progress_sink = None


@contextmanager
def progress_sink(fn):
    """作用域化注册进度观测回调 `fn({"total": int, "done": int})`；退出恢复前值。"""
    global _progress_sink
    prev, _progress_sink = _progress_sink, fn
    try:
        yield
    finally:
        _progress_sink = prev


# 场景标签（§八：能量压制/高速强攻/耐久消耗/状态控制/镜像）。R0 里标签是结构元数据
# （决定配对模式：镜像 = 同阵），真正的「原型语义」由 R6 Build Oracle 落地。
SCENARIOS: tuple[str, ...] = ("mirror", "energy_denial", "fast_attack", "stall", "status_control")

# 实例规模（§八：D_sel 固定 60；D_test 隐藏集，季度换血）。主要成本旋钮在 R4 门禁。
D_SEL_INSTANCES = 60
D_TEST_INSTANCES = 16
_INSTANCE_COUNT = {"d_sel": D_SEL_INSTANCES, "d_test": D_TEST_INSTANCES}

# 每实例固定 seed 数（双向先后手配对已内建，一实例 = 2×SEEDS_PER_INSTANCE 局）。
SEEDS_PER_INSTANCE = 8

# seed 空间隔离：d_sel 从 1000 起、d_test 从 8000 起，永不重叠。
_SEED_BASE = {"d_sel": 1000, "d_test": 8000}


@dataclass(frozen=True)
class Instance:
    """一个评测实例：固定阵容对 + 固定 seed 集（§四的「实例 = 对手×阵容族×种子」雏形）。"""

    name: str                      # "d_sel-mirror-003"
    scenario: str                  # SCENARIOS 之一
    roster_a: list                 # build_roster(VALID) 产物（roster spec）
    roster_b: list
    seeds: tuple[int, ...]         # 8 个固定引擎 seed（双向先后手已内建）


def _instance_pool(kind: str) -> list[str]:
    """按实例集切分跨家族候选池：**阵容族级隔离**（§八「未公开阵容」）。

    **三路切分**（R5 补 D_tr 训练家族池）：
    - d_sel 用前 1/2 家族（评估/选择集）、d_test 用次 1/4（隐藏集）、D_tr 用后 1/4
      （训练轨迹家族，`tr_spirit_pool`）——**三者结构不相交**：隐藏集不是 d_sel 的
      seed 重编号副本，训练 minibatch 也不与评估实例 roster 重合
      （否则 rollout/反思 LLM 反复看到基准阵容 → 评测污染，§九 头号失败模式）。
    """
    cands = valid_spirit_candidates()
    n = len(cands)
    split_sel = n * 1 // 2
    split_test = n * 3 // 4
    if kind == "d_sel":
        return cands[:split_sel]
    if kind == "d_test":
        return cands[split_sel:split_test]
    raise ValueError(f"未知实例集「{kind}」（d_sel / d_test）。")


def tr_spirit_pool() -> list[str]:
    """D_tr 训练家族池（后 1/4）——与 d_sel/d_test 实例 roster **结构不相交**（§八 阵容族隔离）。

    R5 `_tr_minibatch` 用它构造训练 minibatch：rollout/反思 LLM 只看到训练家族的精灵，
    永不接触 D_sel/D_test 基准阵容（评测污染的防渗漏）。
    """
    cands = valid_spirit_candidates()
    return cands[len(cands) * 3 // 4:]


def build_instances(kind: str, *, team_size: int = 3) -> list[Instance]:
    """确定性构造实例集。`kind` ∈ {d_sel, d_test}。

    阵容：`kind` 对应的候选池（d_sel/d_test 家族不相交）按 `span = 2×team_size`
    步长轮转切出两组（镜像场景两组相同）。每个实例的 seed 只依赖 (kind, idx)，
    与选择顺序无关——`--games N` 子采样和全量评测对同一实例给出**一致的 seed**。
    """
    if kind not in _INSTANCE_COUNT:
        raise ValueError(f"未知实例集「{kind}」（d_sel / d_test）。")
    cands = _instance_pool(kind)
    n = len(cands)
    span = 2 * team_size
    if n <= span:                       # 需要至少一个不环绕的双窗口（也防 (n-span) 除零）
        raise ValueError(
            f"候选池不足（{kind} 族隔离后仅 {n} 只）：需要 >{span} 只才能构造 3v3 双向对。")
    rules = build_battle_rules(team_size=team_size, lives=2)
    base = _SEED_BASE[kind]
    out: list[Instance] = []
    for idx in range(_INSTANCE_COUNT[kind]):
        scenario = SCENARIOS[idx % len(SCENARIOS)]
        offset = (idx * span) % (n - span)          # 不环绕 → 两组候选天然不相交
        a_names = cands[offset:offset + team_size]
        b_names = list(a_names) if scenario == "mirror" \
            else cands[offset + team_size:offset + span]
        roster_a = build_roster(p1_team(a_names), DataSource.VALID, rules)
        roster_b = build_roster(p1_team(b_names), DataSource.VALID, rules)
        seeds = tuple(base + idx * SEEDS_PER_INSTANCE + i for i in range(SEEDS_PER_INSTANCE))
        out.append(Instance(
            name=f"{kind}-{scenario}-{idx:03d}",
            scenario=scenario,
            roster_a=roster_a,
            roster_b=roster_b,
            seeds=seeds,
        ))
    return out


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 二项置信区间（0/n 与 n/n 不退化到 [0,0]/[1,1]）。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def run_game(roster_a: list, roster_b: list, *, seed: int, rules,
             make_subject, make_opponent) -> tuple[str | None, str, str]:
    """一局固定阵容的评测局：subject 恒为 a 方、opponent 恒为 b 方。

    玩家 seed：subject=seed、opponent=seed+1（各自独立 RNG 流，不碰引擎流）。
    返回 (winner, subject.kind, opponent.kind)。
    """
    session = BattleSession.start(roster_a, roster_b, seed=seed, rules=rules,
                                  battle_id=f"bench-{seed}")
    players = {
        "a": make_subject("a", seed),
        "b": make_opponent("b", seed + 1),
    }
    # R7：SMC 增强玩家需要会话句柄（fork 关键回合）——有 `.bind` 则注入（向后兼容）。
    for p in players.values():
        bind = getattr(p, "bind", None)
        if callable(bind):
            bind(session)
    result = run_match(session, players)
    return result.winner, players["a"].kind, players["b"].kind


def paired_eval(make_subject, make_opponent, instances, *,
                seeds: int | None = None, seed_offset: int = 0,
                team_size: int = 3, lives: int = 2, rules=None) -> dict:
    """双向配对评测：返回实例级胜率表 + 汇总（winrate / Wilson 95% CI / n_games）。

    - 每 (实例, seed) 两局（subject 分别拿 roster_a/roster_b）→ 阵容强度被对消；
    - subject 胜 = winner=="a"；确定性玩家（random/fake_llm）同参数两次逐位相同；
    - `seeds` 限每实例 seed 数（快速冒烟）；`seed_offset` 全局平移（CLI --seed）；
    - `rules` 直接注入引擎规则（测试可传 team_size=1 等管理接口之外的小规模；
      缺省用 `build_battle_rules(team_size, lives)`）。

    进度观测：本次总局数**开跑前可精确算出**（镜像 1 方向 / 非镜像 2 方向），逐局上报
    `progress_sink`（未设 sink 时零开销、零行为变化）。
    """
    if rules is None:
        rules = build_battle_rules(team_size=team_size, lives=lives) if rules is None else rules
    subject_kind = opponent_kind = None
    rows: list[dict] = []
    total_wins = total_games = 0
    sink = _progress_sink
    if sink is not None:                      # 开跑前算出本阶段总局数（确定进度的依据）
        planned = 0
        for inst in instances:
            n_seeds = len(inst.seeds if seeds is None else inst.seeds[:seeds])
            planned += n_seeds * (1 if inst.roster_a == inst.roster_b else 2)
        sink({"total": planned, "done": 0})
    for inst in instances:
        if len(inst.roster_a) != rules.team_size or len(inst.roster_b) != rules.team_size:
            raise ValueError(
                f"实例「{inst.name}」阵容规模 {len(inst.roster_a)}/{len(inst.roster_b)} "
                f"与 team_size={rules.team_size} 不符。")
        inst_seeds = inst.seeds if seeds is None else inst.seeds[:seeds]
        # 双向先后手配对：非镜像跑两方向（对消阵容强度）；镜像两边同阵、
        # 两方向逐位同局（同 seed 同玩家 seed），只跑一个方向——否则 n_games 虚高、
        # Wilson CI 被当成独立样本变窄。
        orderings = ((inst.roster_a, inst.roster_b),) if inst.roster_a == inst.roster_b \
            else ((inst.roster_a, inst.roster_b), (inst.roster_b, inst.roster_a))
        wins = games = 0
        for s in inst_seeds:
            for ra, rb in orderings:
                winner, sk, ok_ = run_game(ra, rb, seed=s + seed_offset, rules=rules,
                                           make_subject=make_subject, make_opponent=make_opponent)
                if subject_kind is None:
                    subject_kind, opponent_kind = sk, ok_
                games += 1
                wins += 1 if winner == "a" else 0
                if sink is not None:
                    sink({"total": planned, "done": total_games + games})
        if games:
            rows.append({
                "name": inst.name,
                "scenario": inst.scenario,
                "wins": wins,
                "n_games": games,
                "winrate": wins / games,
                "ci95": wilson_ci(wins, games),
            })
            total_wins += wins
            total_games += games
    agg_ci = wilson_ci(total_wins, total_games) if total_games else (0.0, 1.0)
    agg_winrate = total_wins / total_games if total_games else 0.0
    return {
        "players": {"subject": subject_kind, "opponent": opponent_kind},
        "n_instances": len(rows),
        "instances": rows,
        # 平铺别名（对齐 plan 关键签名 `{"winrate", "ci95", "n_games"}`，
        # 供 R4 晋级门/汇报直接读；`aggregate` 保留更丰富的形状）
        "winrate": agg_winrate,
        "ci95": agg_ci,
        "ci95_low": agg_ci[0],
        "ci95_high": agg_ci[1],
        "n_games": total_games,
        "aggregate": {
            "wins": total_wins,
            "n_games": total_games,
            "winrate": agg_winrate,
            "ci95": agg_ci,
            "ci95_low": agg_ci[0],
            "ci95_high": agg_ci[1],
        },
    }
