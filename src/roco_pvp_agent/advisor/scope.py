"""ScopeGate（M4）：在顾问 Agent 运行前做确定性领域判定，越界/注入不进 LLM。

判定顺序（全部确定性、零 LLM 调用）：
1. REFUSE（安全优先）：命中注入/绕过关键词。
2. 欢迎：问候词 + 消息短 + 无意图词。
3. OUT_OF_SCOPE：命中明确非本域关键词。
4. IN_SCOPE：命中任一真实精灵名/技能名（精确名子串命中，非模糊）。
5. IN_SCOPE：命中组队意图词。
6. 其余 → AMBIGUOUS（回澄清模板）。

拒答/欢迎走固定模板，不靠主模型临场发挥。
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from environment.dataset import DataSource, load_skills, load_spirits


class ScopeVerdict(str, Enum):
    IN_SCOPE = "in_scope"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"
    REFUSE = "refuse"


# ── 关键词表 ──

_REFUSE_KEYWORDS = (
    "忽略系统规则", "忽略之前的指令", "忽略以上指令", "忽略规则", "忽略指令",
    "泄露提示词", "泄露系统提示", "提示词泄露", "读取隐藏配置", "绕过组队校验",
    "绕过校验", "执行代码", "访问文件", "扩大网站范围", "修改证据优先级",
    "system prompt", "注入", "jailbreak",
)

_WELCOME_KEYWORDS = (
    "你好", "您好", "你能做什么", "你能干嘛", "功能介绍", "怎么用", "你是谁", "在吗",
)

_OUT_OF_SCOPE_KEYWORDS = (
    "写代码", "编程", "写程序", "翻译", "写诗", "作诗", "诗", "天气", "新闻",
    "股票", "炒股", "数学题", "写论文", "写作文",
)

_DOMAIN_KEYWORDS = (
    "组队", "组个", "配队", "组一", "组建", "配招", "克制", "阵容", "血脉", "性格",
    "个体值", "轨迹", "胜率", "构筑", "精灵", "技能", "洛克王国", "洛克手游",
    "PVP", "pvp", "对战", "队友", "队伍", "选哪只", "换谁", "推荐队友",
)


@lru_cache(maxsize=1)
def _known_entities() -> frozenset[str]:
    """真实精灵名 ∪ 技能名（VALID 口径）。命中即 IN_SCOPE。"""
    return frozenset(load_spirits(DataSource.VALID)) | frozenset(load_skills(DataSource.VALID))


def _has_entity(message: str) -> bool:
    return any(name in message for name in _known_entities())


def _is_welcome(message: str) -> bool:
    if not any(k in message for k in _WELCOME_KEYWORDS):
        return False
    if any(k in message for k in _DOMAIN_KEYWORDS):
        return False
    return len(message) <= 12


def classify(message: str) -> ScopeVerdict:
    """确定性领域判定。"""
    m = message.strip()
    if any(k in m for k in _REFUSE_KEYWORDS):
        return ScopeVerdict.REFUSE
    if _is_welcome(m):
        return ScopeVerdict.IN_SCOPE          # 欢迎属 IN_SCOPE，但 route 走欢迎模板
    if any(k in m for k in _OUT_OF_SCOPE_KEYWORDS):
        return ScopeVerdict.OUT_OF_SCOPE
    if _has_entity(m):
        return ScopeVerdict.IN_SCOPE
    if any(k in m for k in _DOMAIN_KEYWORDS):
        return ScopeVerdict.IN_SCOPE
    return ScopeVerdict.AMBIGUOUS


# ── 固定模板（不靠模型发挥）──

REFUSE_TEMPLATE = (
    "我不能更改系统范围、读取隐藏配置或绕过组队校验。不过我可以按现有规则继续帮你优化阵容。"
)
OUT_OF_SCOPE_TEMPLATE = (
    "这个助手主要负责精灵图鉴、配招、组队和对战分析，不适合回答这个话题。"
    "你可以告诉我你想组几只精灵、已有精灵或目标对手，我来帮你配队。"
)
AMBIGUOUS_TEMPLATE = (
    "如果你是在问某只精灵或技能，请告诉我它的名称；我可以帮你查属性、配招或推荐队友。"
)
WELCOME_TEMPLATE = (
    "你好，我可以帮你查精灵/技能资料、推荐 3–6 人阵容与配招、分析克制与轨迹。"
    "例如：① 组一只 3 人火系强攻队 ② 给『迪莫』推荐配招 ③ 我的队伍被水系克制怎么办？"
)


def route(message: str) -> tuple[str, str]:
    """路由入口：返回 (target, text)。

    target ∈ agent/refuse/out_of_scope/ambiguous/welcome。非 agent 时 text 为固定模板，
    调用方直接返回、不进 LLM。
    """
    verdict = classify(message)
    if verdict is ScopeVerdict.REFUSE:
        return "refuse", REFUSE_TEMPLATE
    if verdict is ScopeVerdict.OUT_OF_SCOPE:
        return "out_of_scope", OUT_OF_SCOPE_TEMPLATE
    if verdict is ScopeVerdict.AMBIGUOUS:
        return "ambiguous", AMBIGUOUS_TEMPLATE
    if _is_welcome(message):
        return "welcome", WELCOME_TEMPLATE
    return "agent", ""
