from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from tradecraft.runtime.state_store import RuntimeStateStore


RestartOne = Callable[[str], Any]
StatusProvider = Callable[[str], dict[str, Any]]
SleepFn = Callable[[float], None]
MonotonicFn = Callable[[], float]
ProgressFn = Callable[[dict[str, Any]], None]

RECOVERY_ORDER: tuple[str, ...] = (
    "naver_reports",
    "investment_memory",
    "live_evaluator",
    "jue_wiki",
    "market_judge",
    "kis_block_trader",
    "binance_block_trader",
    "market_pulse",
    "strategy_insights",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
    "runtime",
    "watchdog",
    "control",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_recovery_order(keys: Sequence[str]) -> list[str]:
    unique = list(dict.fromkeys(str(key).strip() for key in keys if str(key).strip()))
    rank = {key: index for index, key in enumerate(RECOVERY_ORDER)}
    original_rank = {key: index for index, key in enumerate(unique)}
    return sorted(
        unique,
        key=lambda key: (
            1 if key == "control" else 0,
            rank.get(key, len(rank)),
            original_rank[key],
        ),
    )


def _runner_identity_changed(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    before_pid = before.get("pid")
    after_pid = after.get("pid")
    if after_pid is not None and after_pid != before_pid:
        return True
    before_started = before.get("started_at_epoch")
    after_started = after.get("started_at_epoch")
    try:
        return bool(
            after_started is not None
            and (
                before_started is None
                or float(after_started) > float(before_started)
            )
        )
    except (TypeError, ValueError):
        return False


def _runner_verified(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    return bool(
        after.get("alive")
        and str(after.get("pid_file_status") or "") == "ok"
        and not bool(after.get("stale_process"))
        and not bool(after.get("stale_runtime_state"))
        and _runner_identity_changed(before, after)
    )


def recover_runners_rolling(
    keys: Sequence[str],
    *,
    restart_one: RestartOne,
    status_provider: StatusProvider,
    verify_timeout_sec: float = 60.0,
    poll_interval_sec: float = 0.5,
    sleep: SleepFn = time.sleep,
    monotonic: MonotonicFn = time.monotonic,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    ordered_keys = normalize_recovery_order(keys)
    started_at = _utc_now_iso()
    verified_keys: list[str] = []
    rows: list[dict[str, Any]] = []

    for key in ordered_keys:
        before = dict(status_provider(key) or {})
        key_started = monotonic()
        if progress is not None:
            progress(
                {
                    "status": "restarting",
                    "current_key": key,
                    "ordered_keys": ordered_keys,
                    "verified_keys": list(verified_keys),
                    "before": before,
                }
            )
        try:
            restart_result = restart_one(key)
        except Exception as exc:
            return {
                "status": "restart_failed",
                "failed_key": key,
                "error_message": str(exc) or exc.__class__.__name__,
                "ordered_keys": ordered_keys,
                "verified_keys": verified_keys,
                "rows": rows,
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
            }
        if isinstance(restart_result, dict) and str(
            restart_result.get("status") or ""
        ).lower() in {"error", "failed"}:
            return {
                "status": "restart_failed",
                "failed_key": key,
                "error_message": str(
                    restart_result.get("error_message") or "restart scheduling failed"
                ),
                "ordered_keys": ordered_keys,
                "verified_keys": verified_keys,
                "rows": rows,
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
            }

        deadline = key_started + max(float(verify_timeout_sec), 0.0)
        after: dict[str, Any] = {}
        while True:
            after = dict(status_provider(key) or {})
            if _runner_verified(before, after):
                break
            if monotonic() >= deadline:
                failed_row = {
                    "key": key,
                    "status": "verification_failed",
                    "before": before,
                    "after": after,
                    "elapsed_sec": round(monotonic() - key_started, 3),
                }
                rows.append(failed_row)
                result = {
                    "status": "verification_failed",
                    "failed_key": key,
                    "ordered_keys": ordered_keys,
                    "verified_keys": verified_keys,
                    "rows": rows,
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                }
                if progress is not None:
                    progress(result)
                return result
            sleep(max(float(poll_interval_sec), 0.01))

        verified_keys.append(key)
        row = {
            "key": key,
            "status": "verified",
            "before": before,
            "after": after,
            "elapsed_sec": round(monotonic() - key_started, 3),
        }
        rows.append(row)
        if progress is not None:
            progress(
                {
                    "status": "verified",
                    "current_key": key,
                    "ordered_keys": ordered_keys,
                    "verified_keys": list(verified_keys),
                    "row": row,
                }
            )

    result = {
        "status": "ok",
        "ordered_keys": ordered_keys,
        "verified_keys": verified_keys,
        "rows": rows,
        "started_at": started_at,
        "finished_at": _utc_now_iso(),
    }
    if progress is not None:
        progress(result)
    return result


def run_recovery(
    *,
    keys: Sequence[str],
    state_path: str,
    verify_timeout_sec: float,
    poll_interval_sec: float,
) -> dict[str, Any]:
    from tradecraft.runtime.process_status import (
        restart_runner_processes,
        runner_process_status,
    )

    store = RuntimeStateStore(state_path)

    def publish(payload: dict[str, Any]) -> None:
        store.write_snapshot({"service": "tradecraft-runner-recovery", **payload})

    publish(
        {
            "status": "starting",
            "ordered_keys": normalize_recovery_order(keys),
            "verified_keys": [],
        }
    )
    return recover_runners_rolling(
        keys,
        restart_one=lambda key: restart_runner_processes([key], delay_sec=0.1),
        status_provider=runner_process_status,
        verify_timeout_sec=verify_timeout_sec,
        poll_interval_sec=poll_interval_sec,
        progress=publish,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified rolling runner recovery")
    parser.add_argument("--keys", required=True)
    parser.add_argument(
        "--state-path",
        default=".runtime/runner_recovery.json",
    )
    parser.add_argument("--verify-timeout-sec", type=float, default=60.0)
    parser.add_argument("--poll-interval-sec", type=float, default=0.5)
    args = parser.parse_args(argv)
    keys = [key.strip() for key in args.keys.split(",") if key.strip()]
    result = run_recovery(
        keys=keys,
        state_path=args.state_path,
        verify_timeout_sec=args.verify_timeout_sec,
        poll_interval_sec=args.poll_interval_sec,
    )
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
