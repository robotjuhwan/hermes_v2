from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.investment_memory_runner import run_investment_memory_loop


class _FakeMemoryService:
    def __init__(self) -> None:
        self.compaction_calls: list[dict[str, Any]] = []

    def initialize(self) -> None:
        return None

    def due_slots(self) -> list[str]:
        return []

    def status(self) -> dict[str, Any]:
        return {
            "seeded": True,
            "pending_event_count": 0,
            "policy_rule_count": 100,
        }

    def run_due_reflections(self, *, context: dict[str, Any]) -> dict[str, Any]:
        return {"status": "skipped", "reason": "test_no_due_reflections"}

    def compact_runtime_storage(
        self,
        *,
        policy_retired_keep: int,
        validation_event_retained_rows_per_venue: int = 0,
        memory_run_recent_rows_per_group: int = 0,
        symbol_analysis_recent_rows_per_symbol: int = 0,
        vacuum: bool,
    ) -> dict[str, Any]:
        call = {
            "policy_retired_keep": policy_retired_keep,
            "validation_event_retained_rows_per_venue": validation_event_retained_rows_per_venue,
            "memory_run_recent_rows_per_group": memory_run_recent_rows_per_group,
            "symbol_analysis_recent_rows_per_symbol": symbol_analysis_recent_rows_per_symbol,
            "vacuum": vacuum,
        }
        self.compaction_calls.append(call)
        return {
            "status": "ok",
            "policy_rules": {
                "deleted_count": 42,
                "retained_retired_per_policy": policy_retired_keep,
            },
            "vacuum": vacuum,
        }


def test_investment_memory_runner_compacts_runtime_storage_once(
    tmp_path: Path,
) -> None:
    service = _FakeMemoryService()
    state_path = tmp_path / "investment_memory_runner.json"
    settings = AppSettings(_env_file=None).model_copy(
        update={
            "investment_memory_once": True,
            "investment_memory_state_path": str(state_path),
            "investment_memory_compaction_interval_sec": 3600,
            "investment_memory_policy_retired_keep": 3,
            "daily_discovery_enabled": False,
            "kis_block_trader_db_path": str(tmp_path / "kis_blocks.db"),
            "binance_block_trader_db_path": str(tmp_path / "binance_blocks.db"),
        }
    )

    asyncio.run(
        run_investment_memory_loop(
            settings=settings,
            service=service,  # type: ignore[arg-type]
        )
    )

    assert service.compaction_calls == [
        {
            "policy_retired_keep": 3,
            "validation_event_retained_rows_per_venue": 720,
            "memory_run_recent_rows_per_group": 24,
            "symbol_analysis_recent_rows_per_symbol": 3,
            "vacuum": True,
        }
    ]
    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["runtime_compaction"]["status"] == "ok"
    assert snapshot["runtime_compaction"]["policy_rules"]["deleted_count"] == 42
    assert snapshot["last_compaction_at"]


def test_investment_memory_runner_restores_recent_compaction_timestamp(
    tmp_path: Path,
) -> None:
    service = _FakeMemoryService()
    state_path = tmp_path / "investment_memory_runner.json"
    state_path.write_text(
        json.dumps(
            {
                "last_compaction_at": "2026-07-02T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings(_env_file=None).model_copy(
        update={
            "investment_memory_once": True,
            "investment_memory_state_path": str(state_path),
            "investment_memory_compaction_interval_sec": 3600,
            "daily_discovery_enabled": False,
            "kis_block_trader_db_path": str(tmp_path / "kis_blocks.db"),
            "binance_block_trader_db_path": str(tmp_path / "binance_blocks.db"),
        }
    )

    from tradecraft.runtime import investment_memory_runner

    original_now = investment_memory_runner.datetime

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return original_now(2026, 7, 2, 1, 10, tzinfo=tz)

        @classmethod
        def fromisoformat(cls, value: str):
            return original_now.fromisoformat(value)

    try:
        investment_memory_runner.datetime = _FrozenDateTime  # type: ignore[assignment]
        asyncio.run(
            run_investment_memory_loop(
                settings=settings,
                service=service,  # type: ignore[arg-type]
            )
        )
    finally:
        investment_memory_runner.datetime = original_now  # type: ignore[assignment]

    assert service.compaction_calls == []
    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["runtime_compaction"] == {
        "status": "skipped",
        "reason": "not_due",
    }
    assert snapshot["last_compaction_at"] == "2026-07-02T01:00:00+00:00"


def test_investment_memory_runner_restores_legacy_compaction_timestamp_from_updated_at(
    tmp_path: Path,
) -> None:
    service = _FakeMemoryService()
    state_path = tmp_path / "investment_memory_runner.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-07-02T01:00:00+00:00",
                "runtime_compaction": {"status": "ok"},
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings(_env_file=None).model_copy(
        update={
            "investment_memory_once": True,
            "investment_memory_state_path": str(state_path),
            "investment_memory_compaction_interval_sec": 3600,
            "daily_discovery_enabled": False,
            "kis_block_trader_db_path": str(tmp_path / "kis_blocks.db"),
            "binance_block_trader_db_path": str(tmp_path / "binance_blocks.db"),
        }
    )

    from tradecraft.runtime import investment_memory_runner

    original_now = investment_memory_runner.datetime

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return original_now(2026, 7, 2, 1, 10, tzinfo=tz)

        @classmethod
        def fromisoformat(cls, value: str):
            return original_now.fromisoformat(value)

    try:
        investment_memory_runner.datetime = _FrozenDateTime  # type: ignore[assignment]
        asyncio.run(
            run_investment_memory_loop(
                settings=settings,
                service=service,  # type: ignore[arg-type]
            )
        )
    finally:
        investment_memory_runner.datetime = original_now  # type: ignore[assignment]

    assert service.compaction_calls == []
    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["last_compaction_at"] == "2026-07-02T01:00:00+00:00"


class _LargeStatusMemoryService(_FakeMemoryService):
    def status(self) -> dict[str, Any]:
        long_text = "Jue memory detail " * 4000
        return {
            "status": "ok",
            "seeded": True,
            "pending_event_count": 2,
            "policy_rule_count": 123,
            "active_policy_rule_count": 9,
            "latest_reflection_at": "2026-06-29T06:00:00+00:00",
            "today_journals": [
                {
                    "slot": "pre_open",
                    "content": long_text,
                    "created_at": "2026-06-29T00:00:00+00:00",
                }
            ],
            "active_policies": [
                {
                    "policy_id": "policy.large",
                    "rule": long_text,
                    "confidence": 0.77,
                }
            ],
            "policy_scorecards": [
                {
                    "policy_id": "policy.large",
                    "notes": long_text,
                    "sample_count": 7,
                }
            ],
            "policy_rules": [
                {
                    "policy_id": "policy.large",
                    "body": long_text,
                    "status": "active",
                }
            ],
            "recent_reflections": [
                {
                    "block_id": "block-1",
                    "lesson": long_text,
                    "created_at": "2026-06-29T01:00:00+00:00",
                }
            ],
            "validation_repair_backlog": {
                "status": "warn",
                "instruction": long_text,
                "items": [{"id": "validation.large", "detail": long_text}],
            },
        }


def test_investment_memory_runner_compacts_memory_status_in_state(
    tmp_path: Path,
) -> None:
    service = _LargeStatusMemoryService()
    state_path = tmp_path / "investment_memory_runner.json"
    settings = AppSettings(_env_file=None).model_copy(
        update={
            "investment_memory_once": True,
            "investment_memory_state_path": str(state_path),
            "investment_memory_compaction_interval_sec": 0,
            "daily_discovery_enabled": False,
            "kis_block_trader_db_path": str(tmp_path / "kis_blocks.db"),
            "binance_block_trader_db_path": str(tmp_path / "binance_blocks.db"),
        }
    )

    asyncio.run(
        run_investment_memory_loop(
            settings=settings,
            service=service,  # type: ignore[arg-type]
        )
    )

    raw = state_path.read_text(encoding="utf-8")
    snapshot = json.loads(raw)
    memory = snapshot["memory"]

    assert memory["state_compacted"] is True
    assert memory["seeded"] is True
    assert memory["pending_event_count"] == 2
    assert memory["policy_rule_count"] == 123
    assert memory["today_journals"]["count"] == 1
    assert memory["active_policies"]["count"] == 1
    assert memory["recent_reflections"]["count"] == 1
    assert "Jue memory detail Jue memory detail" not in raw
    assert state_path.stat().st_size < 24_000
