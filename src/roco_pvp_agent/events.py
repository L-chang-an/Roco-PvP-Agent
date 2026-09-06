"""Execution facts and the versioned public event envelope (no HTTP/storage dependency)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class ExecutionEvent:
    event: str
    round_id: str
    round_index: int
    payload: dict[str, Any] = field(default_factory=dict)
    # Internal typed result; a presentation projector must whitelist public fields.
    result: Any = None


ExecutionObserver = Callable[[ExecutionEvent], None]


class PublicEvent(BaseModel):
    schema_version: Literal[2] = 2
    event: str
    session_id: str
    turn_id: str
    seq: int
    created_at: str
    round_id: str | None = None
    round_index: int | None = None
    payload: dict[str, Any]


def split_round_summary(text: str) -> tuple[str | None, str]:
    """Extract one leading public summary, never provider reasoning.

    Broken/duplicate blocks are removed from the answer but do not become a summary.
    An unclosed block consumes the remaining text, so it cannot masquerade as a reply.
    """
    pattern = r"<round_summary>\s*([\s\S]*?)\s*</round_summary>"
    matches = list(re.finditer(pattern, text))
    valid = (len(matches) == 1 and not text[:matches[0].start()].strip()
             and text.count("<round_summary>") == text.count("</round_summary>") == 1)
    summary = None
    if valid:
        lines = [line.strip() for line in matches[0].group(1).splitlines() if line.strip()]
        summary = "\n".join(lines[:3])[:240].strip() or None
    answer = re.sub(pattern, "", text)
    answer = re.sub(r"<round_summary>[\s\S]*$", "", answer)
    answer = answer.replace("</round_summary>", "").strip()
    return summary, answer
