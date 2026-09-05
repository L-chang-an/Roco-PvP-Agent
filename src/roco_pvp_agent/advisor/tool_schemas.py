"""Chat Mode 顾问工具的显式入参契约。

这些模型只负责验证模型工具调用的“形状”：必填字段、类型、枚举和嵌套结构。
精灵/技能是否存在、阵容是否合法、数据版本是否匹配等业务规则仍由 handler 后的
Catalog / LegalityGate / VersionGate / EvidenceGate 负责。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from roco_pvp_agent.advisor.advice import TeamAdviceSchema
from roco_pvp_agent.tooling.schema import StrictToolArgs


class NoArgs(StrictToolArgs):
    """显式的零参数工具契约。"""


class SpiritFilterInput(StrictToolArgs):
    """白名单精灵检索 DSL 的一个过滤原子。"""

    field: Literal[
        "name", "type", "trait", "learnable_skill", "family", "is_boss", "number"
    ]
    op: Literal["eq", "in", "contains"]
    value: str | bool | list[str]

    @model_validator(mode="after")
    def validate_field_operation_value(self) -> "SpiritFilterInput":
        """在调用 catalog handler 前钉住 field/op/value 的组合关系。"""

        if self.field == "is_boss":
            if self.op != "eq" or not isinstance(self.value, bool):
                raise ValueError("is_boss 只支持 op=eq，且 value 必须是 bool")
            return self

        if self.field in {"type", "learnable_skill"}:
            expected = list if self.op == "eq" else str
            if not isinstance(self.value, expected):
                shape = "list[str]" if expected is list else "str"
                raise ValueError(f"{self.field} 使用 op={self.op} 时 value 必须是 {shape}")
            return self

        expected = list if self.op == "in" else str
        if not isinstance(self.value, expected):
            shape = "list[str]" if expected is list else "str"
            raise ValueError(f"{self.field} 使用 op={self.op} 时 value 必须是 {shape}")
        return self


class TeamPickInput(StrictToolArgs):
    """工具侧的一只精灵构筑；业务合法性由 validate_team 继续判定。"""

    spirit: str
    skills: list[str]
    bloodline: str = ""
    nature: str = "坦率"
    iv: dict[str, int] = Field(default_factory=dict)


class SearchSpiritsArgs(StrictToolArgs):
    filters: list[list[SpiritFilterInput]]


class NameArgs(StrictToolArgs):
    name: str


class GetBuildOptionsArgs(NameArgs):
    bloodline: str = ""


class ValidateTeamArgs(StrictToolArgs):
    team: list[TeamPickInput]
    items: list[str] | None = None


class QueryTrajectoryEvidenceArgs(StrictToolArgs):
    kind: Literal["human", "selfplay"]
    team_filter: list[str] | None = None


class AnalyzeTeamArgs(StrictToolArgs):
    team: list[TeamPickInput]


class SimulateMatchupsArgs(StrictToolArgs):
    team: list[TeamPickInput]
    opponents: list[list[TeamPickInput]]
    seeds: list[int]


class RetrieveTeamSkillArgs(StrictToolArgs):
    query: str


class QueryGlobalMemArgs(StrictToolArgs):
    my_team: list[TeamPickInput]
    foe_team: list[TeamPickInput]
    team_size: Literal[3, 6] = 3
    lives: int = 2

    @model_validator(mode="after")
    def validate_lives(self) -> "QueryGlobalMemArgs":
        if not 1 <= self.lives < self.team_size:
            raise ValueError("lives 必须大于等于 1 且小于 team_size")
        return self


class QueryLocalMemArgs(StrictToolArgs):
    situation_key: str


class SubmitTeamAdviceArgs(StrictToolArgs):
    payload: TeamAdviceSchema


__all__ = [
    "AnalyzeTeamArgs",
    "GetBuildOptionsArgs",
    "NameArgs",
    "NoArgs",
    "QueryGlobalMemArgs",
    "QueryLocalMemArgs",
    "QueryTrajectoryEvidenceArgs",
    "RetrieveTeamSkillArgs",
    "SearchSpiritsArgs",
    "SimulateMatchupsArgs",
    "SpiritFilterInput",
    "StrictToolArgs",
    "SubmitTeamAdviceArgs",
    "TeamPickInput",
    "ValidateTeamArgs",
]
