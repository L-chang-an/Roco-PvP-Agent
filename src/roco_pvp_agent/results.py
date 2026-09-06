"""Explicit assistant results, independent of tool text truncation."""
from typing import Any, Literal

from pydantic import BaseModel, Field

TurnStatus = Literal["completed", "degraded", "failed", "cancelled", "timed_out", "interrupted"]


class AssistantResult(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["text", "team_advice", "partial", "error"] = "text"
    message: str
    status: TurnStatus = "completed"
    reason_code: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    # Trusted terminal payload, stored separately by the coordinator, never in tool events.
    advice: dict[str, Any] | None = Field(default=None, exclude=True)
