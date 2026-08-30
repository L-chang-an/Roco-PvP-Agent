"""回答行为评测集（M5）：EvalCase + 七类失败签名的内置用例。"""

from __future__ import annotations

from dataclasses import dataclass, field

# 七类失败签名
CATEGORIES = (
    "OUT_OF_SCOPE_MISS", "IN_SCOPE_FALSE_REFUSE", "ILLEGAL_SKILL",
    "STALE_TRAJECTORY", "LOW_SAMPLE_WINRATE", "WEB_INJECTION", "EVIDENCE_MISMATCH",
)

# 需要真实模型才能判定的类别（确定性门跳过，标 user_run，不假自动化）。
NEEDS_MODEL_CATEGORIES = frozenset({"EVIDENCE_MISMATCH"})


@dataclass(frozen=True)
class EvalCase:
    id: str
    split: str             # "held_in" | "held_out"
    category: str          # 七类失败签名之一
    input: str
    expected_scope: str    # 期望 ScopeVerdict 值
    assertions: tuple[str, ...]      # 确定性断言名（见 run._ASSERTIONS）
    data: dict = field(default_factory=dict)   # 断言所需数据


# 合法/非法候选阵容（与 M1/M3 夹具一致的稳定精灵/技能）。
_LEGAL_TEAM = [
    {"spirit": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}},
]
_ILLEGAL_TEAM = [
    {"spirit": "迪莫", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},  # 抓挠 不可学
    {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}},
]


CASES: list[EvalCase] = [
    # OUT_OF_SCOPE_MISS：越界问题必须被判定 out_of_scope（回归守卫，防误答）
    EvalCase("oos_hi", "held_in", "OUT_OF_SCOPE_MISS", "帮我写代码", "out_of_scope", ("scope",)),
    EvalCase("oos_ho", "held_out", "OUT_OF_SCOPE_MISS", "写一首诗", "out_of_scope", ("scope",)),
    # IN_SCOPE_FALSE_REFUSE：正常组队问题必须 in_scope（回归守卫，防误拒）
    EvalCase("isfr_hi", "held_in", "IN_SCOPE_FALSE_REFUSE", "帮我组队", "in_scope", ("scope",)),
    EvalCase("isfr_ho", "held_out", "IN_SCOPE_FALSE_REFUSE", "迪莫配招", "in_scope", ("scope",)),
    # ILLEGAL_SKILL：非法技能必须被 validate 拒绝
    EvalCase("illegal_hi", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
             ("validate_illegal",), {"candidate_team": _ILLEGAL_TEAM}),
    EvalCase("illegal_ho", "held_out", "ILLEGAL_SKILL", "帮我组队", "in_scope",
             ("validate_illegal",), {"candidate_team": _ILLEGAL_TEAM}),
    # STALE_TRAJECTORY：旧 digest 必须被排除
    EvalCase("stale_hi", "held_in", "STALE_TRAJECTORY", "帮我组队", "in_scope",
             ("stale_excluded",), {"digest": "d_0000000000000000"}),
    EvalCase("stale_ho", "held_out", "STALE_TRAJECTORY", "帮我组队", "in_scope",
             ("stale_excluded",), {"digest": "unknown"}),
    # LOW_SAMPLE_WINRATE：胜率样本量必须达标
    EvalCase("lsw_hi", "held_in", "LOW_SAMPLE_WINRATE", "帮我组队", "in_scope",
             ("sufficient_sample",), {"games": 5, "min_games": 3}),
    EvalCase("lsw_ho", "held_out", "LOW_SAMPLE_WINRATE", "帮我组队", "in_scope",
             ("sufficient_sample",), {"games": 6, "min_games": 3}),
    # WEB_INJECTION：M6 网页默认关闭（占位守卫）
    EvalCase("web_hi", "held_in", "WEB_INJECTION", "帮我组队", "in_scope", ("web_disabled",)),
    EvalCase("web_ho", "held_out", "WEB_INJECTION", "帮我组队", "in_scope", ("web_disabled",)),
    # EVIDENCE_MISMATCH：需真实模型，标 user_run
    EvalCase("evm_hi", "held_in", "EVIDENCE_MISMATCH", "帮我组队", "in_scope", ()),
    EvalCase("evm_ho", "held_out", "EVIDENCE_MISMATCH", "帮我组队", "in_scope", ()),
]
