from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RepairTask:
    task_id: str
    venue: str
    discipline_id: str
    source_validation_run_id: str
    status: str
    priority: int
    owner: str
    automation_hook: str
    failure_status: str
    failure_evidence: str = ""
    green_condition: dict[str, Any] = field(default_factory=dict)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
