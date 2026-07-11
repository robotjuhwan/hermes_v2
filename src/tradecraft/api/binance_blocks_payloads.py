from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

from tradecraft.api.binance_blocks import (
    BinanceBlockRouteDeps,
    compact_binance_blocks_payload,
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_manager_error_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary = {
        key: value.get(key)
        for key in ("run_at", "status", "mode", "error_message")
        if value.get(key) not in (None, "", [], {})
    }
    if "error_message" in summary:
        summary["error_message"] = str(summary["error_message"])[:240]
    return summary


def _iso_to_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _manager_error_stale_after_restart(
    error_summary: dict[str, Any],
    runner: dict[str, Any],
) -> bool:
    if not isinstance(error_summary, dict) or not isinstance(runner, dict):
        return False
    run_at = _iso_to_utc(error_summary.get("run_at"))
    started_epoch = runner.get("started_at_epoch")
    if run_at is None or started_epoch is None:
        return False
    try:
        started_at = datetime.fromtimestamp(float(started_epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    return bool(started_at > run_at)


def _manager_error_fields(
    status_source: dict[str, Any],
    *,
    runner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(status_source, dict):
        return {}
    recovered = bool(status_source.get("latest_manager_error_recovered")) or (
        _contract_replay_resolved_stored_error(status_source)
    )
    latest_error = _compact_manager_error_summary(
        status_source.get("latest_manager_error")
    )
    unresolved_error = _compact_manager_error_summary(
        status_source.get("latest_unresolved_manager_error")
    )
    fields: dict[str, Any] = {}
    if (
        recovered
        or "latest_manager_error_recovered" in status_source
        or latest_error
        or unresolved_error
    ):
        fields["latest_manager_error_recovered"] = recovered
    if recovered:
        recovered_error = latest_error or unresolved_error
        if recovered_error:
            fields["latest_recovered_manager_error"] = recovered_error
    elif unresolved_error:
        if _manager_error_stale_after_restart(unresolved_error, runner or {}):
            fields["latest_manager_error_stale_after_restart"] = True
            fields["latest_stale_manager_error"] = unresolved_error
            return fields
        fields["latest_manager_error"] = unresolved_error
        fields["latest_unresolved_manager_error"] = unresolved_error
    elif latest_error:
        if _manager_error_stale_after_restart(latest_error, runner or {}):
            fields["latest_manager_error_stale_after_restart"] = True
            fields["latest_stale_manager_error"] = latest_error
            return fields
        fields["latest_manager_error"] = latest_error
    return fields


def _contract_replay_resolved_stored_error(status_source: dict[str, Any]) -> bool:
    latest = status_source.get("latest_decision_input")
    if not isinstance(latest, dict):
        return False
    return (
        latest.get("contract_replay_status")
        == "stored_error_resolved_by_current_contract"
        and not str(latest.get("current_contract_error") or "").strip()
    )


def _compact_activity_pressure(status_source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status_source, dict):
        return {}
    latest = status_source.get("latest_decision_input")
    source_payload = (
        {**status_source, **latest}
        if isinstance(latest, dict)
        else status_source
    )
    field_map = {
        "current_replay_pressure_status": "status",
        "current_replay_pressure_level": "level",
        "current_replay_pressure_source": "source",
        "current_replay_zero_action_streak": "zero_action_streak",
        "current_replay_binance_zero_action_streak": "binance_zero_action_streak",
        "current_replay_binance_activity_gap_status": "activity_gap_status",
        "current_replay_binance_entry_stale_hours": "entry_stale_hours",
        "current_replay_binance_candidate_symbols": "candidate_symbols",
    }
    payload: dict[str, Any] = {}
    for source, target in field_map.items():
        value = source_payload.get(source)
        if value in (None, "", [], {}):
            continue
        if target == "candidate_symbols" and isinstance(value, list):
            value = [str(item) for item in value[:8] if item not in (None, "")]
        payload[target] = value
    return payload


def _compact_contract_replay_recovery(status_source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status_source, dict):
        return {}
    latest = status_source.get("latest_decision_input")
    if not isinstance(latest, dict):
        return {}
    replay_status = str(latest.get("contract_replay_status") or "").strip()
    if replay_status not in {
        "stored_error_resolved_by_current_contract",
        "current_contract_error",
    }:
        return {}
    field_names = (
        "contract_replay_status",
        "stored_error_message",
        "current_contract_error",
        "action_count",
        "current_replay_action_count",
        "current_replay_auto_action_count",
        "current_replay_action_sections",
        "current_replay_hold_summary",
        "current_replay_watch_symbols",
        "current_replay_next_triggers",
        "current_replay_data_gaps",
        "current_replay_auto_create_preview",
    )
    payload: dict[str, Any] = {}
    for key in field_names:
        value = latest.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "current_replay_auto_create_preview":
            value = _compact_contract_replay_auto_create_preview(value)
            if not value:
                continue
        if isinstance(value, str):
            value = value[:240]
        elif isinstance(value, list):
            compact_items: list[Any] = []
            for item in value[:8]:
                if item in (None, "", [], {}):
                    continue
                if isinstance(item, dict):
                    compact_item: dict[str, Any] = {}
                    for item_key, item_value in item.items():
                        if item_value in (None, "", [], {}):
                            continue
                        if isinstance(item_value, str):
                            item_value = item_value[:160]
                        compact_item[str(item_key)] = item_value
                    if compact_item:
                        compact_items.append(compact_item)
                    continue
                compact_items.append(str(item)[:120])
            value = compact_items
        payload[key] = value
    return payload


def _compact_contract_replay_auto_create_preview(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    text_keys = {
        "symbol",
        "market",
        "side",
        "entry_style",
        "entry_trigger_operator",
        "auto_materialized_reason",
    }
    numeric_keys = {
        "entry_trigger_price",
        "entry_price",
        "target_price",
        "stop_price",
        "qty",
        "quote_budget_usdt",
        "quote_budget_krw",
        "min_executable_notional_usdt",
        "min_executable_notional_krw",
        "min_executable_qty",
        "notional_estimate_usdt",
        "notional_estimate_krw",
    }
    previews: list[dict[str, Any]] = []
    for row in value[:8]:
        if not isinstance(row, dict):
            continue
        preview: dict[str, Any] = {}
        for key in text_keys:
            item = row.get(key)
            if item not in (None, "", [], {}):
                preview[key] = str(item)[:160]
        for key in numeric_keys:
            item = row.get(key)
            if item not in (None, "", [], {}):
                parsed = _safe_float(item)
                if parsed > 0:
                    preview[key] = parsed
        if preview:
            previews.append(preview)
    return previews


def _contract_replay_recovered_warning(replay: dict[str, Any]) -> bool:
    if not replay:
        return False
    if replay.get("contract_replay_status") != "stored_error_resolved_by_current_contract":
        return False
    return _safe_float(replay.get("current_replay_action_count")) > _safe_float(
        replay.get("action_count")
    )


def _contract_replay_current_error_warning(replay: dict[str, Any]) -> bool:
    if not replay:
        return False
    return replay.get("contract_replay_status") == "current_contract_error"


def build_binance_block_readiness_payload(
    *,
    status_payload: dict[str, Any] | None,
    runner: dict[str, Any],
    enabled: bool,
    spot_live: bool,
    futures_live: bool,
    upbit_live: bool,
    model: str,
    reasoning_effort: str,
    account_risk_pct: Any,
    max_total_exposure_usdt: Any,
    max_symbol_exposure_pct: Any,
    min_reward_risk: Any,
    manager_interval_sec: Any,
    next_from_latest: Callable[[Any, int], str],
) -> dict[str, Any]:
    status_source = status_payload if isinstance(status_payload, dict) else {}
    active_blocks = status_source.get("active_blocks")
    block_history = status_source.get("block_history")
    status = {
        key: value
        for key, value in {
            "status": status_source.get("status"),
            "enabled": status_source.get("enabled"),
            "compact": status_source.get("compact"),
            "updated_at": status_source.get("updated_at"),
            "latest_manager_run_at": status_source.get("latest_manager_run_at"),
            "latest_manager_status": status_source.get("latest_manager_status"),
            "manager_operational_status": (
                "manager_contract_replay_recovered"
                if _contract_replay_resolved_stored_error(status_source)
                and str(status_source.get("latest_manager_status") or "").lower()
                == "error"
                else status_source.get("manager_operational_status")
            ),
            "latest_manager_mode": status_source.get("latest_manager_mode"),
            **_manager_error_fields(status_source, runner=runner),
            "active_block_count": len(active_blocks)
            if isinstance(active_blocks, list)
            else None,
            "block_history_count": len(block_history)
            if isinstance(block_history, list)
            else None,
        }.items()
        if value not in (None, "", [], {})
    }
    interval_sec = int(_safe_float(manager_interval_sec))
    payload = {
        "enabled": bool(enabled),
        "status": status,
        "execution": {
            "spot_mode": "live" if bool(spot_live) else "paper",
            "futures_mode": "live" if bool(futures_live) else "paper",
            "upbit_spot_mode": "live" if bool(upbit_live) else "paper",
        },
        "runner": runner,
        "model": str(model or ""),
        "reasoning_effort": str(reasoning_effort or ""),
        "risk": {
            "account_risk_pct": _safe_float(account_risk_pct),
            "max_total_exposure_usdt": _safe_float(max_total_exposure_usdt),
            "max_symbol_exposure_pct": _safe_float(max_symbol_exposure_pct),
            "min_reward_risk": _safe_float(min_reward_risk),
        },
        "next_manager_run_at": next_from_latest(
            status.get("latest_manager_run_at"),
            interval_sec,
        ),
    }
    entry_activity = _compact_entry_activity(status_source)
    if entry_activity:
        payload["entry_activity"] = entry_activity
    activity_pressure = _compact_activity_pressure(status_source)
    warnings: list[str] = []
    if activity_pressure:
        payload["activity_pressure"] = activity_pressure
        if str(activity_pressure.get("status") or "") == "action_required":
            warnings.append("binance_activity_pressure_open")
    contract_replay = _compact_contract_replay_recovery(status_source)
    if contract_replay:
        payload["manager_contract_replay"] = contract_replay
        if _contract_replay_recovered_warning(contract_replay):
            warnings.append("binance_manager_contract_replay_recovered")
        if _contract_replay_current_error_warning(contract_replay):
            warnings.append("binance_manager_contract_replay_current_error")
    if warnings:
        payload["warnings"] = warnings
    return payload


def _compact_entry_activity(status_source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status_source, dict):
        return {}
    activity = status_source.get("entry_activity")
    if not isinstance(activity, dict):
        return {}
    field_names = (
        "version",
        "status",
        "latest_binance_entry_at",
        "latest_binance_entry_market",
        "latest_upbit_entry_at",
        "binance_entry_stale_hours",
        "binance_entry_count",
        "upbit_entry_count",
    )
    payload: dict[str, Any] = {}
    for key in field_names:
        value = activity.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            value = value[:240]
        payload[key] = value
    return payload


def build_binance_block_route_deps(
    *,
    require_admin_auth: Callable[..., Any],
    trader: Any,
    memory_service: Any,
    build_readiness: Callable[[dict[str, Any]], dict[str, Any]],
    quant_repository_factory: Callable[[], Any],
    pattern_repository_cls: type[Any] | None,
    pattern_db_path: Callable[[], str] | str,
    pattern_import_error: Exception | None = None,
) -> BinanceBlockRouteDeps:
    return BinanceBlockRouteDeps(
        require_admin_auth=require_admin_auth,
        blocks_snapshot=lambda compact=False: build_binance_blocks_snapshot(
            trader,
            compact=compact,
        ),
        validation_repair_ops_summary=(
            lambda target_scope, limit: memory_service.validation_repair_ops_summary(
                target_scope=target_scope,
                limit=limit,
            )
        ),
        build_readiness=build_readiness,
        quant_signals=lambda symbols, limit: build_binance_quant_signals_payload(
            repository=quant_repository_factory(),
            symbols=symbols,
            limit=limit,
        ),
        pattern_context=lambda symbols, limit: build_binance_pattern_context_payload(
            repository_cls=pattern_repository_cls,
            db_path=(
                pattern_db_path() if callable(pattern_db_path) else str(pattern_db_path)
            ),
            symbols=symbols,
            limit=limit,
            import_error=pattern_import_error,
        ),
        manager_run_once=lambda: trader.run_manager_once(),
        spot_adoption_once=lambda: trader.run_spot_adoption_once(),
        upbit_adoption_once=(
            (lambda: trader.run_upbit_adoption_once())
            if hasattr(trader, "run_upbit_adoption_once")
            else None
        ),
        executor_tick=lambda: trader.executor_tick(),
        set_kill_switch=lambda enabled, reason: trader.set_kill_switch(
            bool(enabled),
            reason=reason,
        ),
    )


async def build_binance_blocks_snapshot(
    trader: Any,
    *,
    compact: bool = False,
    compact_payload: Callable[[dict[str, Any]], dict[str, Any]] = compact_binance_blocks_payload,
) -> dict[str, Any]:
    if compact and hasattr(trader, "snapshot_compact"):
        snapshot = trader.snapshot_compact()
    elif hasattr(trader, "snapshot"):
        snapshot = trader.snapshot()
    else:
        blocks = list(trader.list_blocks()) if hasattr(trader, "list_blocks") else []
        payload = {**trader.status(), "blocks": blocks}
        return compact_payload(payload) if compact else payload

    if inspect.isawaitable(snapshot):
        snapshot = await snapshot
    if isinstance(snapshot, dict):
        return compact_payload(snapshot) if compact else snapshot
    return {"status": "ok", "blocks": []}


def build_binance_quant_signals_payload(
    *,
    repository: Any,
    symbols: list[str],
    limit: int,
) -> dict[str, Any]:
    items = repository.latest_signals(
        symbols=symbols or None,
        limit=limit,
    )
    history = repository.retrieval_context(
        symbols=[str(item.get("symbol") or "") for item in items],
        horizon="intraday",
        points_per_symbol=12,
    )
    return {
        "status": "ok",
        "items": items,
        "history": history,
        "count": len(items),
    }


def build_binance_pattern_context_payload(
    *,
    repository_cls: type[Any] | None,
    db_path: str,
    symbols: list[str],
    limit: int,
    import_error: Exception | None = None,
) -> dict[str, Any]:
    if repository_cls is None:
        detail = "crypto pattern lab service is not importable"
        if import_error is not None:
            detail = str(import_error)
        raise HTTPException(status_code=503, detail=detail)
    repository = repository_cls(str(db_path))
    return repository.pattern_context(
        symbols=symbols or None,
        limit=limit,
    )
