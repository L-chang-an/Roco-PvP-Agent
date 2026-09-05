"""模型可见的沙箱查询参数；路径、后端和资源限制均不属于契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from roco_pvp_agent.tooling.schema import StrictToolArgs

DatasetId = Literal[
    "full_spirits",
    "full_skills",
    "valid_skills",
    "type_chart",
    "families",
    "evolution_chains",
    "p1_skills",
    "p2_skills",
]


class SandboxPythonQueryArgs(StrictToolArgs):
    code: str = Field(min_length=1, max_length=16_000)
    dataset_ids: list[DatasetId] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def dataset_ids_must_be_unique(self) -> "SandboxPythonQueryArgs":
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError("dataset_ids 不允许重复")
        return self


__all__ = ["DatasetId", "SandboxPythonQueryArgs"]
