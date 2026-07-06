from __future__ import annotations

import inspect
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


def _manager_error_fields(status_source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status_source, dict):
        return {}
    recovered = bool(status_source.get("latest_manager_error_recovered"))
    latest_error = _compact_manager_error_summary(
        status_source.get("latest_manager_error")
    )
    unresolved_error = _compact_manager_error_summary(
        status_source.get("latest_unresolved_manager_error")
    )
    fields: dict[str, Any] = {
        "latest_manager_error_recovered": status_source.get(
            "latest_manager_error_recovered"
        ),
    }
    if recovered:
        if latest_error:
            fields["latest_recovered_manager_error"] = latest_error
    elif unresolved_error:
        fields["latest_manager_error"] = unresolved_error
        fields["latest_unresolved_manager_error"] = unresolved_error
    elif latest_error:
        fields["latest_manager_error"] = latest_error
    return fields


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
            "manager_operational_status": status_source.get(
                "manager_operational_status"
            ),
            "latest_manager_mode": status_source.get("latest_manager_mode"),
            **_manager_error_fields(status_source),
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
    return {
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
