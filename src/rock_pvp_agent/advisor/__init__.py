"""顾问只读底座（M1）：目录查询 DSL + 组队硬闸。纯确定性、无 LLM、无网络、无随机。

本包只读 `environment`，绝不反向被 `environment` import（隔离不变量）。
"""

from .catalog import (
    QueryNotAllowed,
    SpiritFilter,
    get_build_options,
    get_catalog_version,
    get_skill_profile,
    get_spirit_profile,
    search_spirits,
)
from .trajectory import TrajectoryEvidence, aggregate, discover, normalize, query_trajectory_evidence
from .validate import TeamValidation, validate_team

__all__ = [
    "QueryNotAllowed",
    "SpiritFilter",
    "get_catalog_version",
    "search_spirits",
    "get_spirit_profile",
    "get_skill_profile",
    "get_build_options",
    "TrajectoryEvidence",
    "discover",
    "normalize",
    "aggregate",
    "query_trajectory_evidence",
    "TeamValidation",
    "validate_team",
]
