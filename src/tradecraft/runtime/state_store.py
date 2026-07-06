from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = dict(snapshot)
        payload.setdefault("updated_at", utc_now_iso())

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def read_snapshot(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "failed to read runtime state snapshot from %s: %s",
                self.path,
                exc,
                exc_info=True,
            )
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "runtime state snapshot at %s is not a JSON object: %s",
                self.path,
                type(payload).__name__,
            )
            return None
        return payload
