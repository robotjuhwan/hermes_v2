from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tradecraft.runtime.binance_block_trader_runner import (
    _latest_manager_timestamp,
    _build_crypto_pattern_service,
    _build_trader,
    _cycle_log_level,
    _parse_telegram_report_slots,
    _runner_source_freshness,
    _telegram_report_due_slot,
    run_binance_block_trader_loop,
)
from tradecraft.runtime.crypto_market_research_runner import (
    parse_crypto_universe,
    run_crypto_market_research_loop,
)


class FakeTrader:
    def __init__(self) -> None:
        self.executor_ticks = 0
        self.manager_runs = 0
        self.performance_runs = 0
        self.snapshot_runs = 0
        self.retention_runs = 0

    async def executor_tick(self) -> dict:
        self.executor_ticks += 1
        return {"status": "ok", "actions": []}

    async def run_manager_once(self) -> dict:
        self.manager_runs += 1
        return {"status": "ok", "created_blocks": []}

    def run_performance_feedback_once(self) -> dict:
        self.performance_runs += 1
        return {"status": "ok", "reflection_count": 0}

    async def snapshot_compact(self) -> dict[str, Any]:
        self.snapshot_runs += 1
        return {
            "status": "ok",
            "execution_mode": "live",
            "open_block_count": 1,
            "proposed_block_count": 0,
            "model": "gpt-5.5",
            "account": {
                "status": "ok",
                "total_equity_usdt": 1234.5,
                "stale": False,
            },
            "risk_guard": {
                "status": "ok",
                "current_equity_usdt": 1234.5,
                "allow_new_entries": True,
            },
            "growth_governor": {
                "status": "edge_rebuild",
                "allow_new_blocks": True,
                "max_new_blocks": 1,
            },
            "kill_switch": {"enabled": False},
            "performance_today": {
                "sample_count": 2,
                "realized_pnl_usdt": 0.1234,
                "win_rate_pct": 50.0,
            },
            "active_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "status": "open",
                    "qty_open": 0.01,
                    "entry_price": 100.0,
                    "current_price": 99.0,
                    "target_price": 95.0,
                    "stop_price": 103.0,
                    "unrealized_pnl_usdt": 0.01,
                }
            ],
            "manager_runs": [
                {
                    "run_at": "2026-06-06T00:00:00+00:00",
                    "status": "ok",
                    "action_count": 0,
                    "hold_decision": {"summary": "관망하면서 트리거를 기다립니다."},
                }
            ],
            "events": [
                {"event_type": "manager_run", "message": "관망 노트 저장"},
            ],
        }

    def prune_operational_history(self, **kwargs: Any) -> dict[str, Any]:
        self.retention_runs += 1
        return {"status": "ok", "kwargs": kwargs}


class Settings:
    binance_block_trader_state_path = ""
    binance_block_trader_rule_interval_sec = 1
    binance_block_trader_manager_interval_sec = 1
    binance_block_trader_retention_interval_sec = 3600
    binance_block_trader_once = True


def test_runner_source_freshness_flags_source_changed_after_start(
    tmp_path: Path,
) -> None:
    source = tmp_path / "binance_block_trader.py"
    source.write_text("old", encoding="utf-8")
    started_at = datetime(2026, 6, 20, 2, 0, tzinfo=timezone.utc)
    os.utime(source, (started_at.timestamp() + 120, started_at.timestamp() + 120))

    payload = _runner_source_freshness(
        started_at=started_at,
        source_paths=[source],
    )

    assert payload["status"] == "stale_source"
    assert payload["restart_recommended"] is True
    assert payload["changed_paths"] == [str(source)]


def test_runner_writes_state_once(tmp_path: Path) -> None:
    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = FakeTrader()

    asyncio.run(
        run_binance_block_trader_loop(
            settings=settings,
            trader=trader,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    assert trader.executor_ticks == 1
    assert trader.manager_runs == 1
    assert trader.performance_runs == 1
    assert trader.snapshot_runs == 1
    assert Path(settings.binance_block_trader_state_path).exists()
    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert payload["status_snapshot"] == {
        "status": "ok",
        "execution_mode": "live",
        "open_block_count": 1,
        "proposed_block_count": 0,
        "model": "gpt-5.5",
        "account": {
            "status": "ok",
            "total_equity_usdt": 1234.5,
            "stale": False,
        },
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1234.5,
            "allow_new_entries": True,
        },
        "growth_governor": {
            "status": "edge_rebuild",
            "allow_new_blocks": True,
            "max_new_blocks": 1,
        },
        "kill_switch": {"enabled": False},
        "performance_today": {
            "sample_count": 2,
            "realized_pnl_usdt": 0.1234,
            "win_rate_pct": 50.0,
        },
        "active_block_count": 1,
        "latest_manager_run_status": "ok",
        "latest_manager_run_at": "2026-06-06T00:00:00+00:00",
    }
    assert payload["performance_result"]["status"] == "ok"
    assert payload["telegram_report_result"] is None
    assert payload["runner_source_freshness"]["status"] in {"fresh", "stale_source"}
    assert payload["runner_source_freshness"]["started_at"]


def test_runner_reuses_status_snapshot_during_idle_ticks(tmp_path: Path) -> None:
    class IdleSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 9_999_999_999

    class IdleTrader(FakeTrader):
        async def snapshot_compact(self) -> dict[str, Any]:
            self.snapshot_runs += 1
            return {
                "status": "ok",
                "execution_mode": "live",
                "open_block_count": 0,
                "proposed_block_count": 0,
                "model": "gpt-5.5",
                "account": {
                    "status": "ok",
                    "spot_cash_usdt": 100.0,
                },
            }

    class StopLoop(Exception):
        pass

    settings = IdleSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = IdleTrader()

    async def stop_after_third_tick(_: float) -> None:
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_third_tick,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.executor_ticks == 3
    assert trader.manager_runs == 0
    assert trader.performance_runs == 1
    assert trader.snapshot_runs == 1
    assert payload["status_snapshot"]["open_block_count"] == 0
    assert payload["status_snapshot"]["account"]["spot_cash_usdt"] == 100.0


def test_binance_runner_crypto_pattern_service_propagates_optimizer_settings(
    tmp_path: Path,
) -> None:
    class PatternSettings:
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_db_path = str(tmp_path / "pattern_lab.db")
        crypto_market_research_db_path = str(tmp_path / "crypto_research.db")
        crypto_pattern_lab_strategy_paths = ""
        crypto_pattern_lab_freqtrade_data_paths = ""
        crypto_pattern_lab_max_symbols = 13
        crypto_pattern_lab_intervals = "5m,15m"
        crypto_pattern_lab_lookback_bars = 432
        crypto_pattern_lab_context_limit = 7
        crypto_pattern_lab_retention_days = 21
        crypto_pattern_lab_backtests_per_tuple_retention = 3
        crypto_pattern_lab_optimizer_runs_per_tuple_retention = 4
        crypto_pattern_lab_optimizer_trials_per_run_retention = 5
        crypto_pattern_lab_optimizer_enabled = False
        crypto_pattern_lab_optimizer_max_scorecards = 6
        crypto_pattern_lab_optimizer_max_trials_per_scorecard = 7

    service = _build_crypto_pattern_service(PatternSettings())

    assert service is not None
    assert service.config.retention_days == 21
    assert service.config.backtests_per_tuple_retention == 3
    assert service.config.optimizer_runs_per_tuple_retention == 4
    assert service.config.optimizer_trials_per_run_retention == 5
    assert service.config.optimizer_enabled is False
    assert service.config.optimizer_max_scorecards == 6
    assert service.config.optimizer_max_trials_per_scorecard == 7


def test_runner_records_adoption_timeout_without_blocking_tick(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SlowAdoptionTrader(FakeTrader):
        async def run_spot_adoption_once(self) -> dict[str, Any]:
            await asyncio.sleep(1)
            return {"status": "ok"}

    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.ACCOUNT_STAGE_TIMEOUT_SEC",
        0.01,
    )
    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = SlowAdoptionTrader()

    asyncio.run(
        run_binance_block_trader_loop(
            settings=settings,
            trader=trader,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.executor_ticks == 1
    assert trader.manager_runs == 1
    assert payload["adoption_result"]["status"] == "error"
    assert "spot_adoption_timeout" in payload["adoption_result"]["error_message"]
    assert payload["tick_result"]["status"] == "ok"


def test_runner_snapshot_failure_logs_trace_and_writes_error_state(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    class BrokenSnapshotTrader(FakeTrader):
        async def snapshot_compact(self) -> dict[str, Any]:
            self.snapshot_runs += 1
            raise OverflowError("math range error")

    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = BrokenSnapshotTrader()

    with caplog.at_level(logging.WARNING):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=lambda _: asyncio.sleep(0),
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    matching_records = [
        record
        for record in caplog.records
        if "binance runner status snapshot failed" in record.getMessage()
    ]

    assert trader.executor_ticks == 1
    assert trader.manager_runs == 1
    assert trader.snapshot_runs == 1
    assert payload["status"] == "ok"
    assert payload["status_snapshot"]["status"] == "error"
    assert payload["status_snapshot"]["error_message"] == "math range error"
    assert matching_records
    assert matching_records[0].exc_info is not None


def test_runner_keeps_executor_ticking_while_manager_runs(tmp_path: Path) -> None:
    class SlowManagerTrader(FakeTrader):
        async def run_manager_once(self) -> dict:
            self.manager_runs += 1
            await asyncio.sleep(3600)
            return {"status": "ok", "created_blocks": []}

    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 1

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = SlowManagerTrader()

    async def stop_after_second_tick(_: float) -> None:
        if trader.executor_ticks >= 2:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_second_tick,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 1
    assert trader.executor_ticks >= 2
    assert payload["tick_result"]["status"] == "ok"
    assert payload["manager_result"]["status"] == "running"


def test_runner_preserves_last_completed_manager_result(tmp_path: Path) -> None:
    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 999

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = FakeTrader()

    async def stop_after_third_tick(_: float) -> None:
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_third_tick,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 1
    assert trader.executor_ticks >= 3
    assert payload["manager_result"] is None
    assert payload["last_manager_result"] == {"status": "ok", "created_blocks": []}


def test_runner_compacts_large_manager_result_in_state(tmp_path: Path) -> None:
    class LargeManagerTrader(FakeTrader):
        async def run_manager_once(self) -> dict[str, Any]:
            self.manager_runs += 1
            large_note = "raw manager candidate payload " * 900
            return {
                "status": "ok",
                "manager_run_id": 77,
                "hold_decision": {
                    "summary": "시장 방향을 확인하며 대기합니다. " + large_note,
                    "reasons": [large_note for _ in range(8)],
                    "watch_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    "action_count": 20,
                },
                "lane_review": {
                    "status": "ok",
                    "dominant_lane": "futures_short",
                    "candidate_lane_summary": large_note,
                },
                "actions": {
                    "create_blocks": [
                        {
                            "symbol": f"COIN{i}USDT",
                            "market": "futures",
                            "side": "short",
                            "horizon": "intraday",
                            "lane": "futures",
                            "entry_style": "wait_pullback",
                            "entry_price": 100 + i,
                            "target_price": 90 + i,
                            "stop_price": 105 + i,
                            "confidence": 0.61,
                            "llm_reason": large_note,
                            "calculated": {"raw": large_note},
                            "block_template": {"raw": large_note},
                        }
                        for i in range(20)
                    ],
                    "update_blocks": [],
                    "close_blocks": [],
                    "pause_blocks": [],
                },
                "applied": {
                    "created": [
                        {
                            "status": "created",
                            "reason": large_note,
                            "input": {
                                "symbol": f"COIN{i}USDT",
                                "market": "futures",
                                "side": "short",
                                "entry_price": 100 + i,
                                "target_price": 90 + i,
                                "stop_price": 105 + i,
                                "calculated": {"raw": large_note},
                            },
                        }
                        for i in range(20)
                    ],
                    "updated": [],
                    "closed": [],
                    "paused": [],
                },
                "prompt_context": {
                    "research": large_note,
                    "quant": [large_note for _ in range(20)],
                },
            }

    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = LargeManagerTrader()

    asyncio.run(
        run_binance_block_trader_loop(
            settings=settings,
            trader=trader,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    state_path = Path(settings.binance_block_trader_state_path)
    raw = state_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload["manager_result"]["state_compacted"] is True
    assert payload["last_manager_result"]["state_compacted"] is True
    assert payload["last_manager_result"]["status"] == "ok"
    assert payload["last_manager_result"]["manager_run_id"] == 77
    assert payload["last_manager_result"]["hold_decision"]["action_count"] == 20
    assert payload["last_manager_result"]["actions"]["create_blocks"]["item_count"] == 20
    assert len(payload["last_manager_result"]["actions"]["create_blocks"]["items"]) <= 3
    assert payload["last_manager_result"]["applied"]["created"]["item_count"] == 20
    assert ("raw manager candidate payload " * 10).strip() not in raw
    assert "prompt_context" not in payload["last_manager_result"]
    assert state_path.stat().st_size < 24_000


def test_runner_throttles_retention_cleanup_between_ticks(tmp_path: Path) -> None:
    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 999
        binance_block_trader_retention_interval_sec = 3600

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = FakeTrader()

    async def stop_after_third_tick(_: float) -> None:
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_third_tick,
                now_provider=lambda: datetime(2026, 6, 20, 2, 0, tzinfo=timezone.utc),
            )
        )

    assert trader.executor_ticks >= 3
    assert trader.retention_runs == 1


def test_runner_logs_noop_ticks_at_debug_level() -> None:
    assert _cycle_log_level(status="ok", manager_used=False, action_count=0) == 10
    assert _cycle_log_level(status="ok", manager_used=True, action_count=0) == 20
    assert _cycle_log_level(status="ok", manager_used=False, action_count=1) == 20
    assert _cycle_log_level(status="error", manager_used=False, action_count=0) == 20


def test_runner_recovers_last_manager_result_from_repository_on_restart(
    tmp_path: Path,
) -> None:
    class FakeRepository:
        def latest_manager_run(
            self, *, include_payload: bool = True
        ) -> dict[str, Any]:
            return {
                "id": 42,
                "run_at": "2026-06-20T02:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "model": "gpt-5.5",
                "error_message": "",
            }

    class TraderWithRepository(FakeTrader):
        repository = FakeRepository()

    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 999

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = TraderWithRepository()
    now = datetime(2026, 6, 20, 2, 0, 30, tzinfo=timezone.utc)

    async def stop_after_first_tick(_: float) -> None:
        raise StopLoop()

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_first_tick,
                now_provider=lambda: now,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 0
    assert payload["manager_result"] is None
    assert payload["last_manager_result"] == {
        "status": "ok",
        "run_id": 42,
        "run_at": "2026-06-20T02:00:00+00:00",
        "mode": "llm",
        "model": "gpt-5.5",
    }


def test_runner_retries_recent_manager_error_after_short_cooldown(
    tmp_path: Path,
) -> None:
    class FakeRepository:
        def latest_manager_run(
            self, *, include_payload: bool = True
        ) -> dict[str, Any]:
            return {
                "id": 43,
                "run_at": "2026-06-20T02:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "model": "gpt-5.5",
                "error_message": "lane_review_missing_from_model",
            }

    class TraderWithRepository(FakeTrader):
        repository = FakeRepository()

        async def run_manager_once(self) -> dict[str, Any]:
            self.manager_runs += 1
            await asyncio.sleep(3600)
            return {"status": "ok", "created_blocks": []}

    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 9_999
        binance_block_trader_manager_error_retry_sec = 300

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = TraderWithRepository()
    now = datetime(2026, 6, 20, 2, 5, 1, tzinfo=timezone.utc)

    async def stop_after_second_tick(_: float) -> None:
        if trader.executor_ticks >= 2:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_second_tick,
                now_provider=lambda: now,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 1
    assert payload["manager_result"]["status"] == "running"
    assert payload["manager_due_reason"] == "retry_after_manager_error"
    assert payload["manager_error_retry_sec"] == 300
    assert payload["last_manager_result"] == {
        "status": "error",
        "run_id": 43,
        "run_at": "2026-06-20T02:00:00+00:00",
        "mode": "llm",
        "model": "gpt-5.5",
        "error_message": "lane_review_missing_from_model",
    }


def test_runner_marks_stale_manager_task_timed_out_without_stopping_ticks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SlowManagerTrader(FakeTrader):
        def __init__(self) -> None:
            super().__init__()
            self.saved_manager_runs: list[dict[str, Any]] = []
            self.repository = self

        def save_manager_run(self, **kwargs: Any) -> int:
            self.saved_manager_runs.append(kwargs)
            return 777

        async def run_manager_once(self) -> dict:
            self.manager_runs += 1
            await asyncio.sleep(3600)
            return {"status": "ok", "created_blocks": []}

    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 1
        codex_runtime_timeout_ms = 1

    class StopLoop(Exception):
        pass

    base = datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
    ticks = 0

    def now_provider() -> datetime:
        nonlocal ticks
        ticks += 1
        return base + timedelta(seconds=ticks)

    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.MANAGER_TASK_TIMEOUT_FLOOR_SEC",
        0.01,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.MANAGER_TASK_TIMEOUT_GRACE_SEC",
        0.0,
    )

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = SlowManagerTrader()

    async def stop_after_second_tick(_: float) -> None:
        if trader.executor_ticks >= 2:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_second_tick,
                now_provider=now_provider,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 1
    assert trader.executor_ticks >= 2
    assert payload["tick_result"]["status"] == "ok"
    assert payload["manager_result"]["status"] == "error"
    assert payload["manager_result"]["error_message"] == "manager_task_timeout_after_1s"
    assert payload["manager_result"]["elapsed_sec"] >= 1
    assert trader.saved_manager_runs == [
        {
            "prompt": {"runner_timeout": True},
            "response": {},
            "actions": {"create_blocks": []},
            "status": "error",
            "error_message": "manager_task_timeout_after_1s",
            "model": "",
        }
    ]


def test_runner_records_cancelled_manager_task_without_stopping_ticks(
    tmp_path: Path,
) -> None:
    class CancelledManagerTrader(FakeTrader):
        async def run_manager_once(self) -> dict:
            self.manager_runs += 1
            raise asyncio.CancelledError()

    class ContinuousSettings(Settings):
        binance_block_trader_once = False
        binance_block_trader_manager_interval_sec = 1

    class StopLoop(Exception):
        pass

    settings = ContinuousSettings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = CancelledManagerTrader()

    async def stop_after_second_tick(_: float) -> None:
        if trader.executor_ticks >= 2:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_binance_block_trader_loop(
                settings=settings,
                trader=trader,
                sleep=stop_after_second_tick,
            )
        )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.manager_runs == 1
    assert trader.executor_ticks >= 2
    assert payload["tick_result"]["status"] == "ok"
    assert payload["manager_result"] == {
        "status": "error",
        "error_message": "manager task cancelled",
    }


def test_runner_marks_cycle_manager_error_when_manager_result_fails(
    tmp_path: Path,
) -> None:
    class BrokenManagerTrader(FakeTrader):
        async def run_manager_once(self) -> dict[str, Any]:
            self.manager_runs += 1
            return {
                "status": "error",
                "manager_run_id": 88,
                "error_message": "validation_repair_resolution_missing_from_model",
            }

    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = BrokenManagerTrader()

    asyncio.run(
        run_binance_block_trader_loop(
            settings=settings,
            trader=trader,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())
    assert trader.executor_ticks == 1
    assert trader.manager_runs == 1
    assert payload["tick_result"]["status"] == "ok"
    assert payload["manager_result"]["status"] == "error"
    assert payload["last_manager_result"]["status"] == "error"
    assert payload["status"] == "manager_error"


def test_binance_telegram_report_slots_skip_dawn_and_dedupe() -> None:
    slots = _parse_telegram_report_slots("morning:06:00,noon:12:00,night:20:00")
    sent: dict[str, Any] = {}

    dawn = datetime(2026, 6, 5, 20, 30, tzinfo=timezone.utc)  # 05:30 KST
    morning = datetime(2026, 6, 5, 21, 5, tzinfo=timezone.utc)  # 06:05 KST

    assert _telegram_report_due_slot(now=dawn, slots=slots, sent=sent) is None
    due = _telegram_report_due_slot(now=morning, slots=slots, sent=sent)
    assert due is not None
    assert due["name"] == "morning"
    sent[due["key"]] = {"ok": True}
    assert _telegram_report_due_slot(now=morning, slots=slots, sent=sent) is None


def test_runner_sends_due_binance_telegram_report_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    settings.binance_block_trader_telegram_reports_enabled = True
    settings.binance_block_trader_telegram_report_slots = (
        "morning:06:00,noon:12:00,night:20:00"
    )
    settings.telegram_bot_token = "test-token"
    settings.telegram_chat_id = "test-chat"
    sent_messages: list[str] = []

    class FakeTelegramBridge:
        def __init__(self, config: Any) -> None:
            self.config = config

        async def send_message(self, text: str) -> dict[str, Any]:
            sent_messages.append(text)
            return {
                "ok": True,
                "detail": {
                    "ok": True,
                    "result": {
                        "message_id": 1,
                        "chat": {"id": "should-not-be-stored"},
                    },
                },
            }

    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.TelegramBridge",
        FakeTelegramBridge,
    )
    trader = FakeTrader()
    now = datetime(2026, 6, 5, 21, 5, tzinfo=timezone.utc)

    asyncio.run(
        run_binance_block_trader_loop(
            settings=settings,
            trader=trader,
            sleep=lambda _: asyncio.sleep(0),
            now_provider=lambda: now,
        )
    )

    payload = json.loads(Path(settings.binance_block_trader_state_path).read_text())

    assert len(sent_messages) == 1
    assert "쥬 Binance 아침 보고" in sent_messages[0]
    assert "BTCUSDT futures/short/futures open" in sent_messages[0]
    assert payload["telegram_report_result"]["status"] == "sent"
    assert payload["telegram_report_result"]["result"] == {"ok": True, "message_id": 1}
    assert "2026-06-06:morning" in payload["telegram_reports_sent"]
    serialized_state = json.dumps(payload, ensure_ascii=False)
    assert "should-not-be-stored" not in serialized_state


def test_latest_manager_timestamp_uses_existing_manager_run() -> None:
    class FakeRepository:
        def latest_manager_run(self) -> dict[str, str]:
            return {"run_at": "2026-05-23T14:36:32+00:00"}

    class FakeTraderWithRepository:
        repository = FakeRepository()

    assert _latest_manager_timestamp(FakeTraderWithRepository()) == pytest.approx(
        1779546992.0
    )


def test_build_trader_wires_crypto_research_provider(monkeypatch) -> None:
    constructed: dict[str, Any] = {}

    class FakeCodexNativeRuntime:
        def __init__(self, config: Any) -> None:
            self.config = config

    class FakeMemory:
        def __init__(self, *, config: Any, codex_runtime: Any, **kwargs: Any) -> None:
            self.config = config
            self.codex_runtime = codex_runtime
            self.kwargs = kwargs

        def context_pack(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "kwargs": kwargs}

    class FakeBinance:
        def __init__(self, config: Any) -> None:
            self.config = config

    class FakeCryptoConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeCryptoService:
        def __init__(
            self,
            *,
            config: Any,
            binance: Any,
            codex_runtime: Any,
            memory_provider: Any,
            quant_repository: Any | None = None,
        ) -> None:
            self.config = config
            self.binance = binance
            self.codex_runtime = codex_runtime
            self.memory_provider = memory_provider
            self.quant_repository = quant_repository

    class FakeAlphaConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeAlphaService:
        def __init__(self, *, config: Any, binance: Any) -> None:
            self.config = config
            self.binance = binance

    class FakeRiskConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeRiskSizer:
        def __init__(self, config: Any) -> None:
            self.config = config

    class FakeQuantRepository:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

    class FakePatternConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeKlineReader:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

    class FakePatternService:
        def __init__(self, *, config: Any, kline_reader: Any) -> None:
            self.config = config
            self.kline_reader = kline_reader

    class FakeTrader:
        def __init__(self, **kwargs: Any) -> None:
            constructed.update(kwargs)

    class Settings:
        codex_runtime_timeout_ms = 60000
        llm_usage_enabled = True
        llm_usage_db_path = ".runtime/llm_usage.db"
        binance_block_trader_llm_model = "manager-model"
        binance_block_trader_llm_reasoning_effort = "high"
        binance_block_trader_llm_timeout_ms = 234000
        investment_memory_root_path = ".runtime/memory"
        investment_memory_db_path = ".runtime/memory.db"
        research_strategy_md_path = "docs/strategy.md"
        investment_memory_policy_mode = "soft_auto"
        investment_memory_persona_tone = "friendly_partner"
        investment_memory_context_max_chars = 8000
        jue_wiki_db_path = ".runtime/test-jue-wiki.db"
        jue_wiki_shadow_db_path = ".tradecraft/test-jue-wiki-shadow.db"
        binance_spot_api_key = ""
        binance_spot_api_secret = ""
        binance_spot_base_url = "https://api.binance.com"
        binance_futures_api_key = ""
        binance_futures_api_secret = ""
        binance_futures_base_url = "https://fapi.binance.com"
        binance_usdt_krw = 1387.0
        binance_block_trader_db_path = ".runtime/binance_blocks.db"
        binance_block_trader_state_path = ".runtime/binance_block_trader.json"
        binance_block_trader_enabled = False
        binance_block_trader_execute_spot_orders = False
        binance_block_trader_execute_futures_orders = False
        binance_block_trader_quote_interval_sec = 15
        binance_block_trader_rule_interval_sec = 15
        binance_block_trader_manager_interval_sec = 600
        binance_block_trader_entry_pending_max_age_sec = 123
        binance_block_trader_aggressive_limit_bps = 20.0
        binance_block_trader_max_manager_symbols = 12
        binance_block_trader_jue_wiki_context_max_chars = 36_000
        binance_block_trader_max_futures_leverage = 2
        binance_block_trader_min_liquidation_distance_pct = 12.0
        binance_block_trader_account_risk_pct = 0.25
        binance_block_trader_max_total_exposure_usdt = 0.0
        binance_block_trader_max_symbol_exposure_pct = 25.0
        binance_block_trader_min_reward_risk = 1.3
        binance_block_trader_spot_universe = "BTCUSDT,ETHUSDT"
        binance_block_trader_futures_universe = "BTCUSDT"
        crypto_market_research_db_path = ".runtime/crypto_market_research.db"
        crypto_market_research_max_symbols = 8
        crypto_market_research_auto_universe_enabled = True
        crypto_market_research_auto_universe_limit = 30
        crypto_market_research_llm_top_symbols = 15
        crypto_market_research_kline_intervals = "1m:10,15m:20"
        crypto_market_research_regime_enabled = False
        crypto_market_research_squeeze_guard_enabled = False
        crypto_market_research_llm_model = "gpt-5.5"
        crypto_market_research_llm_reasoning_effort = "xhigh"
        crypto_market_research_external_enabled = True
        crypto_market_research_external_sources = "coingecko,defillama,fear_greed"
        crypto_quant_enabled = True
        crypto_quant_db_path = ".runtime/crypto_quant.db"
        crypto_quant_context_limit = 16
        crypto_alpha_enabled = True
        crypto_alpha_db_path = ".runtime/crypto_alpha.db"
        crypto_alpha_source_ids = "binance_announcements,coinbase_blog,kraken_blog"
        crypto_alpha_rate_limit_sec = 2.0
        crypto_alpha_context_limit = 12
        crypto_alpha_llm_model = "gpt-5.5"
        crypto_alpha_llm_reasoning_effort = "xhigh"
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_db_path = ".runtime/crypto_pattern_lab.db"
        crypto_pattern_lab_strategy_paths = "strategies"
        crypto_pattern_lab_freqtrade_data_paths = "user_data/data"
        crypto_pattern_lab_max_symbols = 9
        crypto_pattern_lab_intervals = "5m,15m,1h"
        crypto_pattern_lab_lookback_bars = 400
        crypto_pattern_lab_context_limit = 12
        crypto_pattern_lab_retention_days = 60

    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CodexNativeRuntime",
        FakeCodexNativeRuntime,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.InvestmentMemoryService",
        FakeMemory,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.BinanceAdapter",
        FakeBinance,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoMarketResearchConfig",
        FakeCryptoConfig,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoMarketResearchService",
        FakeCryptoService,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoAlphaConfig",
        FakeAlphaConfig,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoAlphaService",
        FakeAlphaService,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoQuantRepository",
        FakeQuantRepository,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoPatternLabConfig",
        FakePatternConfig,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.CryptoPatternLabService",
        FakePatternService,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.HermesKlineReader",
        FakeKlineReader,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.BinanceBlockTrader",
        FakeTrader,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.BinanceRiskConfig",
        FakeRiskConfig,
    )
    monkeypatch.setattr(
        "tradecraft.runtime.binance_block_trader_runner.BinanceRiskSizer",
        FakeRiskSizer,
    )

    trader = _build_trader(Settings())  # type: ignore[arg-type]
    crypto = constructed["crypto_research_provider"]
    crypto_alpha = constructed["crypto_alpha_provider"]
    crypto_patterns = constructed["crypto_pattern_provider"]
    risk_sizer = constructed["risk_sizer"]

    assert isinstance(trader, FakeTrader)
    assert constructed["config"].quant_context_limit == 16
    assert constructed["config"].llm_timeout_ms == 234000
    assert constructed["config"].jue_wiki_context_max_chars == 36_000
    assert constructed["config"].waiting_entry_max_age_sec == 172800
    assert constructed["config"].entry_pending_max_age_sec == 123
    assert constructed["codex_runtime"].config.timeout_ms == 234000
    assert constructed["wiki_shadow_recording_recorder"].db_path == Path(
        Settings.jue_wiki_shadow_db_path
    )
    assert crypto.config.kwargs["llm_model"] == "gpt-5.5"
    assert crypto.config.kwargs["llm_reasoning_effort"] == "xhigh"
    assert crypto.config.kwargs["kline_intervals"] == {"1m": 10, "15m": 20}
    assert crypto.config.kwargs["regime_enabled"] is False
    assert crypto.config.kwargs["squeeze_guard_enabled"] is False
    assert crypto.codex_runtime.config.model == "gpt-5.5"
    assert crypto.codex_runtime.config.reasoning_effort == "xhigh"
    assert constructed["binance"] is crypto.binance
    assert isinstance(crypto.quant_repository, FakeQuantRepository)
    assert isinstance(constructed["quant_provider"], FakeQuantRepository)
    assert crypto_alpha.config.kwargs["llm_model"] == "gpt-5.5"
    assert crypto_alpha.config.kwargs["llm_reasoning_effort"] == "xhigh"
    assert constructed["binance"] is crypto_alpha.binance
    assert isinstance(crypto_patterns, FakePatternService)
    assert crypto_patterns.config.kwargs["db_path"] == ".runtime/crypto_pattern_lab.db"
    assert crypto_patterns.config.kwargs["max_symbols"] == 9
    assert crypto_patterns.config.kwargs["lookback_bars"] == 400
    assert crypto_patterns.kline_reader.db_path == ".runtime/crypto_market_research.db"
    assert isinstance(risk_sizer, FakeRiskSizer)
    assert risk_sizer.config.kwargs["account_risk_pct"] == pytest.approx(0.25)
    assert risk_sizer.config.kwargs["min_reward_risk"] == pytest.approx(1.3)


def test_crypto_market_research_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "crypto_market_research.json"

    class Settings:
        crypto_market_research_enabled = True
        crypto_market_research_once = True
        crypto_market_research_state_path = str(state_path)
        crypto_market_research_feature_interval_sec = 1
        crypto_market_research_llm_interval_sec = 1
        crypto_market_research_universe = "BTCUSDT, ETHUSDT BTCUSDT"
        crypto_market_research_max_symbols = 4
        crypto_market_research_auto_universe_enabled = True
        crypto_market_research_auto_universe_limit = 3
        crypto_market_research_llm_top_symbols = 2

    class FakeService:
        def __init__(self) -> None:
            self.resolve_inputs: list[dict[str, Any]] = []
            self.collect_symbols: list[list[str]] = []
            self.research_symbols: list[list[str]] = []
            self.prune_calls: list[dict[str, Any]] = []

        async def resolve_universe(
            self,
            seed_symbols: list[str],
            *,
            auto_enabled: bool = True,
            auto_limit: int | None = None,
            max_symbols: int | None = None,
        ) -> dict[str, Any]:
            self.resolve_inputs.append(
                {
                    "seed_symbols": seed_symbols,
                    "auto_enabled": auto_enabled,
                    "auto_limit": auto_limit,
                    "max_symbols": max_symbols,
                }
            )
            return {
                "status": "ok",
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
                "static_count": 2,
                "dynamic_count": 2,
            }

        async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
            self.collect_symbols.append(symbols)
            return {"status": "ok", "collected_count": len(symbols)}

        async def run_research_once(
            self,
            symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            self.research_symbols.append(list(symbols or []))
            return {"status": "ok", "candidate_count": 1}

        def prune_history(self, **kwargs: Any) -> dict[str, Any]:
            self.prune_calls.append(dict(kwargs))
            return {"status": "ok"}

    service = FakeService()

    asyncio.run(
        run_crypto_market_research_loop(
            settings=Settings(),  # type: ignore[arg-type]
            service=service,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert parse_crypto_universe("BTCUSDT, ETHUSDT BTCUSDT") == ["BTCUSDT", "ETHUSDT"]
    assert service.resolve_inputs == [
        {
            "seed_symbols": ["BTCUSDT", "ETHUSDT"],
            "auto_enabled": True,
            "auto_limit": 3,
            "max_symbols": 4,
        }
    ]
    assert service.collect_symbols == [["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]]
    assert service.research_symbols == [["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]]
    assert service.prune_calls == [
        {
            "market_retention_days": 3,
            "quant_retention_days": 3,
            "market_archive_retention_days": 7,
            "quant_archive_retention_days": 7,
            "quant_hot_window_rows": 360,
            "quant_archive_window_rows": 360,
        }
    ]
    assert payload["service"] == "tradecraft-crypto-market-research"
    assert payload["status"] == "ok"
    assert payload["symbol_count"] == 4
    assert payload["focus_symbol_count"] == 0
    assert payload["universe"]["dynamic_count"] == 2
    assert payload["research"]["candidate_count"] == 1


def test_crypto_market_research_runner_preserves_llm_cadence_after_restart(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "crypto_market_research.json"
    last_llm_run_at = datetime.now(timezone.utc).isoformat()
    state_path.write_text(
        json.dumps(
            {
                "service": "tradecraft-crypto-market-research",
                "status": "ok",
                "last_llm_run_at": last_llm_run_at,
            }
        ),
        encoding="utf-8",
    )

    class Settings:
        crypto_market_research_enabled = True
        crypto_market_research_once = True
        crypto_market_research_state_path = str(state_path)
        crypto_market_research_feature_interval_sec = 1
        crypto_market_research_llm_interval_sec = 3600
        crypto_market_research_universe = "BTCUSDT,ETHUSDT"
        crypto_market_research_max_symbols = 2
        crypto_market_research_auto_universe_enabled = False
        crypto_market_research_auto_universe_limit = 0
        crypto_market_research_research_universe_limit = 2

    class FakeService:
        def __init__(self) -> None:
            self.research_symbols: list[list[str]] = []

        async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
            return {"status": "ok", "collected_count": len(symbols)}

        async def run_research_once(
            self,
            symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            self.research_symbols.append(list(symbols or []))
            return {"status": "ok"}

    service = FakeService()

    asyncio.run(
        run_crypto_market_research_loop(
            settings=Settings(),  # type: ignore[arg-type]
            service=service,
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert service.research_symbols == []
    assert payload["research"] == {"status": "skipped", "reason": "cadence"}
    assert payload["last_llm_run_at"] == last_llm_run_at


def test_crypto_market_research_runner_compacts_large_state_payload(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "crypto_market_research.json"

    class Settings:
        crypto_market_research_enabled = True
        crypto_market_research_once = True
        crypto_market_research_state_path = str(state_path)
        crypto_market_research_feature_interval_sec = 1
        crypto_market_research_llm_interval_sec = 1
        crypto_market_research_universe = "BTCUSDT,ETHUSDT,SOLUSDT"
        crypto_market_research_max_symbols = 8
        crypto_market_research_auto_universe_enabled = True
        crypto_market_research_auto_universe_limit = 8
        crypto_market_research_research_universe_limit = 8

    class FakeService:
        async def resolve_universe(
            self,
            seed_symbols: list[str],
            *,
            auto_enabled: bool = True,
            auto_limit: int | None = None,
            max_symbols: int | None = None,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "symbols": [f"COIN{i}USDT" for i in range(8)],
                "static_count": len(seed_symbols),
                "dynamic_count": 8,
                "dynamic_candidates": [
                    {
                        "symbol": f"COIN{i}USDT",
                        "score": i,
                        "raw_reason": "volatile setup detail " * 500,
                    }
                    for i in range(40)
                ],
                "excluded_symbols": [f"DROP{i}USDT" for i in range(50)],
            }

        async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
            return {
                "status": "ok",
                "collected_count": len(symbols),
                "requested_count": len(symbols),
                "error_count": 0,
                "items": [
                    {
                        "symbol": symbol,
                        "quote_volume_usdt": 1_000_000 + index,
                        "raw_klines": ["raw candle payload " * 200 for _ in range(5)],
                        "narrative": "market structure payload " * 700,
                    }
                    for index, symbol in enumerate(symbols * 8)
                ],
                "errors": [],
            }

        async def run_research_once(
            self,
            symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "candidate_count": len(symbols or []),
                "raw_notes": "research output payload " * 600,
            }

        def select_llm_focus_symbols(self, *, symbols: list[str]) -> list[str]:
            return list(symbols)[:3]

    asyncio.run(
        run_crypto_market_research_loop(
            settings=Settings(),  # type: ignore[arg-type]
            service=FakeService(),
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    raw = state_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload["state_compacted"] is True
    assert payload["collect"]["item_count"] == 64
    assert len(payload["collect"]["items"]) <= 3
    assert payload["universe"]["dynamic_candidate_count"] == 40
    assert len(payload["universe"]["dynamic_candidates"]) <= 3
    assert payload["research"]["candidate_count"] == 8
    assert "raw candle payload raw candle payload" not in raw
    assert "market structure payload market structure payload" not in raw
    assert state_path.stat().st_size < 32_000


def test_binance_jue_wiki_provider_preserves_selected_page_quality_metadata(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.binance_block_trader_runner import _selector_context_provider
    from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="binance",
        page_type="symbol",
        key="ETHUSDT",
        title="ETHUSDT quality",
        symbols=["ETHUSDT"],
        content_sections={
            "Current Stance": "품질 메타 전달 테스트",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "crypto_market_research",
                "source_id": "ETHUSDT:research",
                "quality_status": "weak",
                "quality_warnings": ["market_structure_stale", "funding_missing"],
            }
        ],
        confidence=0.8,
        freshness="stale",
    )["page_id"]

    class WikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"
        jue_wiki_effectiveness_weight = 0.0
        jue_wiki_effectiveness_max_adjustment = 0.0

    payload = _selector_context_provider(
        service,
        WikiSettings(),  # type: ignore[arg-type]
    )(target_scope="binance", symbols=["ETHUSDT"])

    page = payload["pages"][0]
    assert page["page_id"] == page_id
    assert page["quality_status"] == "weak"
    assert set(page["quality_warnings"]) == {
        "market_structure_stale",
        "funding_missing",
    }


def test_binance_jue_wiki_provider_defaults_to_effectiveness_learning(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.binance_block_trader_runner import _selector_context_provider
    from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="binance",
        page_type="symbol",
        key="ETHUSDT",
        title="ETHUSDT",
        symbols=["ETHUSDT"],
        content_sections={
            "Current Stance": "효과성 기본값으로도 선택돼야 하는 크립토 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "binance_blocks", "source_id": "blk-default"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "binance",
            "sample_count": 8,
            "helpful_score": 25.0,
            "status": "active",
            "confidence": 1.0,
        }
    )

    class MinimalWikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"

    payload = _selector_context_provider(
        service,
        MinimalWikiSettings(),  # type: ignore[arg-type]
    )(target_scope="binance", symbols=["ETHUSDT"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert "effectiveness_adjustment:3.0000" in payload["pages"][0][
        "selection_reasons"
    ]


def test_binance_jue_wiki_provider_passes_requested_horizons_to_selector(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.binance_block_trader_runner import _selector_context_provider
    from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="binance",
        page_type="symbol",
        key="ETHUSDT",
        title="ETHUSDT futures horizon memory",
        symbols=["ETHUSDT"],
        content_sections={
            "Current Stance": "선물 판단에서 검증된 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "binance_blocks", "source_id": "futures-win"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "binance",
            "venue": "binance",
            "horizon": "futures",
            "sample_count": 8,
            "helpful_score": 8.0,
            "status": "active",
            "confidence": 1.0,
            "reasons": ["futures horizon worked"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "binance",
            "venue": "binance",
            "horizon": "spot",
            "sample_count": 8,
            "helpful_score": -8.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": ["spot horizon failed"],
        }
    )

    class MinimalWikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"

    payload = _selector_context_provider(
        service,
        MinimalWikiSettings(),  # type: ignore[arg-type]
    )(target_scope="binance", symbols=["ETHUSDT"], horizons=["futures"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert payload["pages"][0]["effectiveness"]["horizon"] == "futures"
    assert payload["pages"][0]["effectiveness"]["status"] == "active"
    assert payload["pages"][0]["effectiveness"]["reasons"] == [
        "futures horizon worked"
    ]
