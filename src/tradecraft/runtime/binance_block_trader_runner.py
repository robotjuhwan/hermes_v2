from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from tradecraft.config import AppSettings
from tradecraft.runtime.live_evaluator_runner import build_live_authority_payload
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.binance_block_trader import (
    BinanceBlockTrader,
    BinanceBlockTraderConfig,
)
from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer
from tradecraft.services.upbit import UpbitAdapter, UpbitConfig
from tradecraft.services.telegram import TelegramBridge, TelegramConfig
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_context import (
    JueWikiContextService,
    evaluate_wiki_decision_gate,
)
from tradecraft.services.jue_wiki_contract import WikiContextRequestV1
from tradecraft.services.jue_wiki_repository import JueWikiRepository
from tradecraft.services.jue_wiki_shadow import (
    JueWikiShadowStore,
    WikiCompletionSigner,
    build_runtime_recording_recorder,
)
from tradecraft.services.jue_wiki_selector import (
    JueWikiSelectionRequest,
    JueWikiSelector,
    resolve_jue_wiki_prompt_mode,
)
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.llm_model_policy import llm_model_config_kwargs

try:
    from tradecraft.services.crypto_market_research import (
        CryptoMarketResearchConfig,
        CryptoMarketResearchService,
    )
except ImportError:
    CryptoMarketResearchConfig = None  # type: ignore[assignment]
    CryptoMarketResearchService = None  # type: ignore[assignment]

try:
    from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService
except ImportError:
    CryptoAlphaConfig = None  # type: ignore[assignment]
    CryptoAlphaService = None  # type: ignore[assignment]

try:
    from tradecraft.services.crypto_quant import CryptoQuantRepository
except ImportError:
    CryptoQuantRepository = None  # type: ignore[assignment]

try:
    from tradecraft.services.crypto_pattern_lab import (
        CryptoPatternLabConfig,
        CryptoPatternLabService,
        HermesKlineReader,
    )
except Exception:
    CryptoPatternLabConfig = None  # type: ignore[assignment]
    CryptoPatternLabService = None  # type: ignore[assignment]
    HermesKlineReader = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], datetime]
KST = timezone(timedelta(hours=9))
DEFAULT_TELEGRAM_REPORT_SLOTS = "morning:06:00,noon:12:00,night:20:00"
ACCOUNT_STAGE_TIMEOUT_SEC = 45.0
EXECUTOR_STAGE_TIMEOUT_SEC = 45.0
MANAGER_TASK_TIMEOUT_FLOOR_SEC = 60.0
MANAGER_TASK_TIMEOUT_GRACE_SEC = 30.0
TELEGRAM_REPORT_SLOT_LABELS = {
    "morning": "아침",
    "noon": "정오",
    "night": "밤",
}


def _cycle_log_level(*, status: str, manager_used: bool, action_count: int) -> int:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {"ok", "skipped"} and not manager_used and action_count <= 0:
        return logging.DEBUG
    return logging.INFO


def _setting(settings: AppSettings, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _jue_wiki_arg_list(kwargs: dict[str, Any], name: str) -> list[str]:
    raw = kwargs.get(name)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = [raw]
    return [str(value).strip() for value in values if str(value).strip()]


def _jue_wiki_prompt_mode(settings: AppSettings) -> str:
    mode = str(_setting(settings, "jue_wiki_prompt_mode", "assist") or "assist").strip().lower()
    return mode if mode in {"observe", "assist", "primary"} else "assist"


def _jue_wiki_read_mode(settings: AppSettings) -> str:
    mode = str(_setting(settings, "jue_wiki_read_mode", "shadow") or "shadow")
    return mode if mode in {"shadow", "prefer", "required"} else "shadow"


def _selector_context_provider(
    service: JueWikiService,
    settings: AppSettings,
) -> Callable[..., dict[str, Any]]:
    def provider(**kwargs: Any) -> dict[str, Any]:
        default_max_chars = int(
            _setting(
                settings,
                "jue_wiki_full_prompt_max_chars",
                _setting(settings, "jue_wiki_context_max_chars", 24000),
            )
        )
        target_scope = str(kwargs.get("target_scope") or "")
        symbols = _jue_wiki_arg_list(kwargs, "symbols")
        page_types = _jue_wiki_arg_list(kwargs, "page_types")
        lanes = _jue_wiki_arg_list(kwargs, "lanes")
        regimes = _jue_wiki_arg_list(kwargs, "regimes")
        block_ids = _jue_wiki_arg_list(kwargs, "block_ids")
        horizons = _jue_wiki_arg_list(kwargs, "horizons")
        max_chars = int(
            kwargs["max_chars"]
            if kwargs.get("max_chars") is not None
            else default_max_chars
        )
        read_mode = _jue_wiki_read_mode(settings)
        try:
            context_service = JueWikiContextService(
                JueWikiRepository(
                    Path(str(_setting(settings, "jue_wiki_db_path", "")))
                ),
                health_reader=service.status,
                eligibility_reader=JueWikiShadowStore(
                    Path(str(_setting(
                        settings, "jue_wiki_shadow_db_path",
                        str(Path.home() / ".tradecraft" / "jue_wiki_shadow.db"),
                    ))),
                    completion_verifier=WikiCompletionSigner(
                        Path(str(_setting(
                            settings, "jue_wiki_provenance_key_path",
                            os.environ.get(
                                "TRADECRAFT_JUE_WIKI_PROVENANCE_KEY_PATH",
                                str(Path.home() / ".tradecraft" / "jue_wiki_provenance.key"),
                            ),
                        )))
                    ),
                ),
            )
            packet = context_service.context_packet(
                WikiContextRequestV1(
                    target_scope=target_scope,
                    symbols=tuple(symbols),
                    page_types=tuple(page_types),
                    lanes=tuple(lanes),
                    regimes=tuple(regimes),
                    block_ids=tuple(block_ids),
                    horizons=tuple(horizons),
                    max_chars=max_chars,
                ),
                read_mode=read_mode,
            )
            gate_payload = evaluate_wiki_decision_gate(packet).to_dict()
            packet_payload = packet.to_dict()
        except Exception as exc:
            packet_payload = {
                "status": "error",
                "read_mode": read_mode,
                "snapshot_id": "",
                "error_message": str(exc),
            }
            gate_payload = {
                "allow_new_risk": read_mode != "required",
                "allow_exit_actions": True,
                "reason": (
                    "wiki_required_context_unavailable"
                    if read_mode == "required"
                    else "wiki_context_advisory"
                ),
                "read_mode": read_mode,
                "snapshot_id": "",
                "version": "wiki_decision_gate_v1",
            }
        base_payload = {
            "read_mode": read_mode,
            "jue_wiki_context_packet": packet_payload,
            "jue_wiki_decision_gate": gate_payload,
        }
        wiki_enabled = bool(_setting(settings, "jue_wiki_enabled", True))
        if read_mode == "required" and not wiki_enabled:
            return {
                **base_payload,
                "status": "disabled",
                "target_scope": target_scope,
                "prompt_mode": _jue_wiki_prompt_mode(settings),
                "jue_wiki_decision_gate": {
                    "allow_new_risk": False,
                    "allow_exit_actions": True,
                    "reason": "wiki_required_disabled",
                    "read_mode": "required",
                    "snapshot_id": "",
                    "version": "wiki_decision_gate_v1",
                },
            }
        try:
            result = JueWikiSelector(service).select(
                JueWikiSelectionRequest(
                    target_scope=target_scope,
                    symbols=symbols,
                    page_types=page_types,
                    lanes=lanes,
                    regimes=regimes,
                    block_ids=block_ids,
                    horizons=horizons,
                    max_chars=max_chars,
                    max_pages=int(
                        _setting(settings, "jue_wiki_selector_max_pages", 24)
                    ),
                    min_confidence=float(
                        _setting(settings, "jue_wiki_selector_min_confidence", 0.15)
                    ),
                    exclude_lint_warnings=bool(
                        _setting(settings, "jue_wiki_exclude_lint_warnings", False)
                    ),
                    effectiveness_weight=float(
                        _setting(settings, "jue_wiki_effectiveness_weight", 0.12)
                    ),
                    effectiveness_max_adjustment=float(
                        _setting(settings, "jue_wiki_effectiveness_max_adjustment", 8.0)
                    ),
                )
            )
        except Exception as exc:
            if read_mode == "required":
                base_payload["jue_wiki_decision_gate"] = {
                    "allow_new_risk": False,
                    "allow_exit_actions": True,
                    "reason": "wiki_required_selector_unavailable",
                    "read_mode": "required",
                    "snapshot_id": str(gate_payload.get("snapshot_id") or ""),
                    "version": "wiki_decision_gate_v1",
                }
            return {
                **base_payload,
                "status": "error",
                "target_scope": target_scope,
                "prompt_mode": _jue_wiki_prompt_mode(settings),
                "error_message": str(exc),
            }
        if read_mode == "required" and result.status != "ok":
            base_payload["jue_wiki_decision_gate"] = {
                "allow_new_risk": False,
                "allow_exit_actions": True,
                "reason": "wiki_required_selector_ineligible",
                "read_mode": "required",
                "snapshot_id": str(gate_payload.get("snapshot_id") or ""),
                "version": "wiki_decision_gate_v1",
            }
        configured_prompt_mode = _jue_wiki_prompt_mode(settings)
        prompt_mode_resolution = resolve_jue_wiki_prompt_mode(
            configured_prompt_mode,
            result.mode_recommendation,
        )
        return {
            **base_payload,
            "status": result.status,
            "selection_run_id": result.selection_run_id,
            "target_scope": result.target_scope,
            "prompt_mode": prompt_mode_resolution["prompt_mode"],
            "configured_prompt_mode": prompt_mode_resolution[
                "configured_prompt_mode"
            ],
            "mode_recommendation": prompt_mode_resolution["mode_recommendation"],
            "prompt_mode_policy": prompt_mode_resolution["prompt_mode_policy"],
            "trust_profile_effectiveness": result.trust_profile_effectiveness,
            "repair_priority_effectiveness": result.repair_priority_effectiveness,
            "validation_repair_effectiveness": (
                result.validation_repair_effectiveness
            ),
            "wiki_application_coverage": result.wiki_application_coverage,
            "content": result.content,
            "effectiveness_policy": result.effectiveness_policy,
            "repair_priorities": result.repair_priorities,
            "repair_action_batches": result.repair_action_batches,
            "evidence_quality": result.evidence_quality,
            "pages": [
                {
                    "page_id": page.page_id,
                    "rank": page.rank,
                    "score": page.score,
                    "selection_reasons": page.reasons,
                    "selection_penalties": page.penalties,
                    "char_count": page.char_count,
                    "source_refs": page.source_refs,
                    "effectiveness": page.effectiveness,
                    "evidence_quality": page.evidence_quality,
                    "quality_status": page.quality_status,
                    "quality_warnings": page.quality_warnings,
                }
                for page in result.pages
            ],
            "rejected_pages": result.rejected_pages,
            "budget_report": result.budget_report,
        }

    return provider


def _build_jue_wiki_context_provider(
    settings: AppSettings,
) -> Callable[..., dict[str, Any]] | None:
    if (
        not bool(_setting(settings, "jue_wiki_enabled", True))
        and _jue_wiki_read_mode(settings) != "required"
    ):
        return None
    service = JueWikiService(
        config=JueWikiConfig(
            root_path=str(
                _setting(settings, "jue_wiki_root_path", ".runtime/jue_wiki")
            ),
            db_path=str(
                _setting(settings, "jue_wiki_db_path", ".runtime/jue_wiki/wiki.db")
            ),
            enabled=bool(_setting(settings, "jue_wiki_enabled", True)),
            context_max_chars=int(
                _setting(settings, "jue_wiki_context_max_chars", 24000)
            ),
            page_max_chars=int(_setting(settings, "jue_wiki_page_max_chars", 12000)),
            context_page_limit=int(
                _setting(settings, "jue_wiki_context_page_limit", 8)
            ),
            kis_blocks_db_path=str(
                _setting(settings, "kis_block_trader_db_path", ".runtime/kis_blocks.db")
            ),
            binance_blocks_db_path=str(
                _setting(
                    settings,
                    "binance_block_trader_db_path",
                    ".runtime/binance_blocks.db",
                )
            ),
            investment_memory_db_path=str(
                _setting(
                    settings,
                    "investment_memory_db_path",
                    ".runtime/investment_memory.db",
                )
            ),
            daily_discovery_db_path=str(
                _setting(
                    settings,
                    "daily_discovery_db_path",
                    ".runtime/jue_daily_discovery.db",
                )
            ),
            trading_validation_db_path=str(
                _setting(
                    settings,
                    "trading_validation_db_path",
                    ".runtime/trading_validation.db",
                )
            ),
            naver_reports_db_path=str(
                _setting(
                    settings,
                    "naver_reports_db_path",
                    ".runtime/naver_reports.db",
                )
            ),
            crypto_market_research_db_path=str(
                _setting(
                    settings,
                    "crypto_market_research_db_path",
                    ".runtime/crypto_market_research.db",
                )
            ),
        )
    )
    return _selector_context_provider(service, settings)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_runner_source_paths() -> list[Path]:
    paths = [Path(__file__).resolve()]
    service_dir: Path | None = None
    for obj in (AppSettings, BinanceBlockTrader):
        try:
            source = inspect.getsourcefile(obj)
        except TypeError:
            source = None
        if not source:
            continue
        path = Path(source).resolve()
        if path not in paths:
            paths.append(path)
        if obj is BinanceBlockTrader:
            service_dir = path.parent
    for filename in ("binance_manager_prompt.py", "binance_manager_contract.py"):
        if service_dir is None:
            continue
        path = (service_dir / filename).resolve()
        if path not in paths:
            paths.append(path)
    return paths


def _runner_source_freshness(
    *,
    started_at: datetime,
    source_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started_ts = started.timestamp()
    changed_paths: list[str] = []
    latest_mtime: datetime | None = None
    checked_paths = 0
    for source_path in source_paths or _default_runner_source_paths():
        path = Path(source_path)
        try:
            stat = path.stat()
        except OSError:
            continue
        checked_paths += 1
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
        if stat.st_mtime > started_ts + 1.0:
            changed_paths.append(str(path))
    stale = bool(changed_paths)
    return {
        "status": "stale_source" if stale else "fresh",
        "restart_recommended": stale,
        "started_at": started.isoformat(),
        "checked_path_count": checked_paths,
        "changed_paths": changed_paths[:8],
        "latest_source_mtime": latest_mtime.isoformat() if latest_mtime else "",
    }


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _manager_task_timeout_sec(settings: AppSettings) -> float:
    timeout_ms = _to_float(
        _setting(
            settings,
            "binance_block_trader_llm_timeout_ms",
            _setting(settings, "codex_runtime_timeout_ms", 600_000),
        )
    )
    runtime_timeout_sec = max(timeout_ms / 1000.0, 0.0)
    return max(
        runtime_timeout_sec + MANAGER_TASK_TIMEOUT_GRACE_SEC,
        MANAGER_TASK_TIMEOUT_FLOOR_SEC,
    )


def _manager_task_timeout_message(timeout_sec: float) -> str:
    seconds = max(int(math.ceil(max(float(timeout_sec), 1.0))), 1)
    return f"manager_task_timeout_after_{seconds}s"


def _record_manager_task_timeout_run(
    trader: Any,
    *,
    settings: AppSettings,
    timeout_message: str,
) -> dict[str, Any]:
    repository = getattr(trader, "repository", None)
    save_manager_run = getattr(repository, "save_manager_run", None)
    if not callable(save_manager_run):
        return {"recorded": False, "reason": "repository_unavailable"}
    try:
        run_id = save_manager_run(
            prompt={"runner_timeout": True},
            response={},
            actions={"create_blocks": []},
            status="error",
            error_message=timeout_message,
            model=str(_setting(settings, "binance_block_trader_llm_model", "")),
        )
    except Exception as exc:
        logger.exception(
            "failed to record binance manager runner timeout in manager_runs"
        )
        return {"recorded": False, "error_message": str(exc)}
    return {"recorded": True, "run_id": run_id}


def _format_number(value: Any, *, digits: int = 4) -> str:
    number = _to_float(value)
    if abs(number) >= 100:
        digits = min(digits, 2)
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_usdt(value: Any) -> str:
    number = _to_float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.4f} USDT"


def _parse_telegram_report_slots(value: Any) -> list[dict[str, Any]]:
    raw = str(value or "").strip() or DEFAULT_TELEGRAM_REPORT_SLOTS
    slots: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    pattern = re.compile(
        r"^(?:(?P<name>[A-Za-z0-9_-]+):)?(?P<hour>\d{1,2}):(?P<minute>\d{2})$"
    )
    for raw_part in re.split(r"[\s,;]+", raw):
        part = raw_part.strip()
        if not part:
            continue
        match = pattern.match(part)
        if not match:
            continue
        try:
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
        except (TypeError, ValueError):
            continue
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            continue
        name = str(match.group("name") or f"{hour:02d}{minute:02d}").strip().lower()
        key = (name, hour, minute)
        if key in seen:
            continue
        seen.add(key)
        slots.append({"name": name, "hour": hour, "minute": minute})
    if not slots and raw != DEFAULT_TELEGRAM_REPORT_SLOTS:
        return _parse_telegram_report_slots(DEFAULT_TELEGRAM_REPORT_SLOTS)
    slots.sort(key=lambda row: (int(row["hour"]), int(row["minute"]), str(row["name"])))
    return slots


def _telegram_report_due_slot(
    *,
    now: datetime,
    slots: list[dict[str, Any]],
    sent: dict[str, Any],
) -> dict[str, Any] | None:
    if not slots:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(KST)
    current_minute = local_now.hour * 60 + local_now.minute
    for index, slot in enumerate(slots):
        start_minute = int(slot["hour"]) * 60 + int(slot["minute"])
        next_slot = slots[index + 1] if index + 1 < len(slots) else None
        end_minute = (
            int(next_slot["hour"]) * 60 + int(next_slot["minute"])
            if next_slot is not None
            else 24 * 60
        )
        if current_minute < start_minute or current_minute >= end_minute:
            continue
        trading_day = local_now.date().isoformat()
        name = str(slot["name"])
        report_key = f"{trading_day}:{name}"
        if report_key in sent:
            return None
        return {
            **slot,
            "key": report_key,
            "trading_day": trading_day,
            "label": TELEGRAM_REPORT_SLOT_LABELS.get(name, name),
        }
    return None


def _prune_telegram_report_state(sent: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    if not sent:
        return {}
    local_now = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(KST)
    cutoff = local_now.date() - timedelta(days=14)
    pruned: dict[str, Any] = {}
    for key, value in sent.items():
        date_text = str(key).split(":", 1)[0]
        try:
            report_date = datetime.fromisoformat(date_text).date()
        except ValueError:
            continue
        if report_date >= cutoff:
            pruned[str(key)] = _compact_telegram_report_state_value(value)
    return pruned


def _compact_telegram_send_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"ok": False, "detail": "invalid_telegram_result"}
    detail = value.get("detail")
    message_id = None
    if isinstance(detail, dict):
        result = detail.get("result")
        if isinstance(result, dict):
            message_id = result.get("message_id")
    out: dict[str, Any] = {"ok": bool(value.get("ok"))}
    if message_id is not None:
        out["message_id"] = message_id
    if not out["ok"]:
        out["detail"] = _compact_text(detail, limit=220)
    return out


def _compact_telegram_report_state_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "slot": value.get("slot") if isinstance(value.get("slot"), dict) else {},
        "sent_at": str(value.get("sent_at") or ""),
        "result": _compact_telegram_send_result(value.get("result")),
    }


def _compact_text(value: Any, *, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "..."


async def _await_stage(
    label: str,
    awaitable: Awaitable[Any],
    *,
    timeout_sec: float,
) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=max(float(timeout_sec), 1.0))
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{label}_timeout_after_{timeout_sec:.0f}s") from exc


def _format_binance_block_line(block: dict[str, Any]) -> str:
    symbol = str(block.get("symbol") or "UNKNOWN").upper()
    market = str(block.get("market") or "spot")
    side = str(block.get("side") or "long")
    horizon = str(block.get("horizon") or block.get("lane") or "")
    status = str(block.get("status") or "")
    entry = _format_number(block.get("entry_price"))
    current = _format_number(block.get("current_price") or block.get("current_price_usdt"))
    target = _format_number(block.get("target_price"))
    stop = _format_number(block.get("stop_price"))
    qty = _format_number(block.get("qty_open") or block.get("qty_initial"), digits=6)
    pnl = _format_usdt(block.get("unrealized_pnl_usdt"))
    return (
        f"- {symbol} {market}/{side}/{horizon} {status} · "
        f"수량 {qty} · 진입 {entry} 현재 {current} 목표 {target} 손절 {stop} · {pnl}"
    )


def _latest_hold_summary(snapshot: dict[str, Any]) -> str:
    runs = snapshot.get("manager_runs")
    if not isinstance(runs, list) or not runs:
        return "최근 매니저 판단 기록 없음"
    latest = runs[0] if isinstance(runs[0], dict) else {}
    hold = latest.get("hold_decision") if isinstance(latest.get("hold_decision"), dict) else {}
    summary = _compact_text(hold.get("summary"), limit=220)
    action_count = int(_to_float(latest.get("action_count")))
    run_at = _compact_text(latest.get("run_at"), limit=48)
    if summary:
        return f"{summary} · 액션 {action_count}건 · {run_at}"
    status = _compact_text(latest.get("status"), limit=80)
    return f"최근 판단 {status or '확인됨'} · 액션 {action_count}건 · {run_at}"


async def _build_binance_telegram_report(
    *,
    trader: BinanceBlockTrader,
    slot: dict[str, Any],
    now: datetime,
) -> str:
    snapshot_method = getattr(trader, "snapshot_compact", None)
    if snapshot_method is not None:
        maybe_snapshot = snapshot_method()
        snapshot = await maybe_snapshot if asyncio.iscoroutine(maybe_snapshot) else maybe_snapshot
    else:
        status_method = getattr(trader, "status", None)
        snapshot = status_method() if status_method is not None else {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    local_now = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(KST)
    label = str(slot.get("label") or slot.get("name") or "정기")
    execution_mode = str(snapshot.get("execution_mode") or "unknown")
    kill_switch = snapshot.get("kill_switch") if isinstance(snapshot.get("kill_switch"), dict) else {}
    kill_text = "정상" if not kill_switch.get("enabled") else "KILL SWITCH ON"
    performance_today = (
        snapshot.get("performance_today")
        if isinstance(snapshot.get("performance_today"), dict)
        else {}
    )
    active_blocks = [
        row for row in list(snapshot.get("active_blocks") or []) if isinstance(row, dict)
    ]
    open_count = int(_to_float(snapshot.get("open_block_count")))
    proposed_count = int(_to_float(snapshot.get("proposed_block_count")))
    events = [row for row in list(snapshot.get("events") or []) if isinstance(row, dict)]

    lines = [
        f"쥬 Binance {label} 보고",
        local_now.strftime("%Y-%m-%d %H:%M KST"),
        "",
        (
            f"상태: {execution_mode} · kill {kill_text} · "
            f"모델 {snapshot.get('model') or ''} · 활성 {open_count} / 대기 {proposed_count}"
        ).strip(),
        (
            "오늘 성과: "
            f"{_format_usdt(performance_today.get('realized_pnl_usdt'))} · "
            f"승률 {_format_number(performance_today.get('win_rate_pct'), digits=1)}% · "
            f"표본 {int(_to_float(performance_today.get('sample_count')))}건"
        ),
        f"최근 판단: {_latest_hold_summary(snapshot)}",
        "",
        "활성 블록:",
    ]
    if active_blocks:
        lines.extend(_format_binance_block_line(block) for block in active_blocks[:6])
        if len(active_blocks) > 6:
            lines.append(f"- 외 {len(active_blocks) - 6}개 블록은 UI에서 확인")
    else:
        lines.append("- 현재 활성/대기 블록 없음")

    if events:
        lines.extend(["", "최근 이벤트:"])
        for event in events[:3]:
            event_type = _compact_text(event.get("event_type"), limit=40)
            message = _compact_text(event.get("message"), limit=130)
            lines.append(f"- {event_type}: {message}")

    text = "\n".join(lines).strip()
    return text[:3900]


async def _send_due_telegram_report(
    *,
    settings: AppSettings,
    trader: BinanceBlockTrader,
    sent: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    if not bool(_setting(settings, "binance_block_trader_telegram_reports_enabled", False)):
        return None
    slots = _parse_telegram_report_slots(
        _setting(
            settings,
            "binance_block_trader_telegram_report_slots",
            DEFAULT_TELEGRAM_REPORT_SLOTS,
        )
    )
    due_slot = _telegram_report_due_slot(now=now, slots=slots, sent=sent)
    if due_slot is None:
        return None
    bot_token = str(_setting(settings, "telegram_bot_token", "") or "").strip()
    chat_id = str(_setting(settings, "telegram_chat_id", "") or "").strip()
    if not bot_token or not chat_id:
        return {
            "status": "skipped",
            "reason": "telegram_config_missing",
            "slot": due_slot,
        }
    message = await _build_binance_telegram_report(
        trader=trader,
        slot=due_slot,
        now=now,
    )
    bridge = TelegramBridge(TelegramConfig(bot_token=bot_token, chat_id=chat_id))
    result = await bridge.send_message(message)
    sent_payload = {
        "slot": due_slot,
        "sent_at": _utc_now().isoformat(),
        "result": _compact_telegram_send_result(result),
    }
    if bool(result.get("ok")):
        sent[due_slot["key"]] = sent_payload
        return {"status": "sent", **sent_payload}
    return {"status": "error", **sent_payload}


def _parse_kline_intervals(value: Any) -> dict[str, int]:
    intervals: dict[str, int] = {}
    for raw_part in re.split(r"[\s,;]+", str(value or "")):
        part = raw_part.strip()
        if ":" not in part:
            continue
        interval, raw_limit = part.split(":", 1)
        try:
            limit = int(raw_limit.strip())
        except ValueError:
            continue
        if interval.strip() and limit > 0:
            intervals[interval.strip()] = limit
    return intervals


def _build_binance_adapter(settings: AppSettings) -> BinanceAdapter:
    return BinanceAdapter(
        BinanceConfig(
            spot_api_key=settings.binance_spot_api_key,
            spot_api_secret=settings.binance_spot_api_secret,
            spot_base_url=settings.binance_spot_base_url,
            futures_api_key=settings.binance_futures_api_key,
            futures_api_secret=settings.binance_futures_api_secret,
            futures_base_url=settings.binance_futures_base_url,
            usdt_krw_rate=settings.binance_usdt_krw,
        )
    )


def _build_upbit_adapter(settings: AppSettings) -> UpbitAdapter:
    return UpbitAdapter(
        UpbitConfig(
            access_key=str(_setting(settings, "upbit_access_key", "") or ""),
            secret_key=str(_setting(settings, "upbit_secret_key", "") or ""),
            base_url=str(_setting(settings, "upbit_base_url", "https://api.upbit.com")),
        )
    )


def _build_crypto_research_service(
    settings: AppSettings,
    *,
    binance: BinanceAdapter,
    memory_provider: Callable[..., dict[str, Any] | None],
) -> Any | None:
    if CryptoMarketResearchConfig is None or CryptoMarketResearchService is None:
        logger.warning("crypto market research service is not available")
        return None

    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=str(_setting(settings, "codex_runtime_mode", "auto")),
            sdk_codex_bin=str(_setting(settings, "codex_runtime_sdk_codex_bin", "")),
            timeout_ms=int(_setting(settings, "codex_runtime_timeout_ms", 60000)),
            **llm_model_config_kwargs(settings, component="crypto_market_research"),
            usage_enabled=bool(_setting(settings, "llm_usage_enabled", True)),
            usage_db_path=str(_setting(settings, "llm_usage_db_path", ".runtime/llm_usage.db")),
            usage_component="crypto_market_research",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    config = CryptoMarketResearchConfig(
        db_path=str(
            _setting(
                settings,
                "crypto_market_research_db_path",
                ".runtime/crypto_market_research.db",
            )
        ),
        max_symbols=int(_setting(settings, "crypto_market_research_max_symbols", 300)),
        llm_model=str(
            _setting(
                settings,
                "crypto_market_research_llm_model",
                "gpt-5.6-terra",
            )
        ),
        llm_reasoning_effort=str(
            _setting(settings, "crypto_market_research_llm_reasoning_effort", "xhigh")
        ),
        external_enabled=bool(
            _setting(settings, "crypto_market_research_external_enabled", True)
        ),
        external_sources=str(
            _setting(
                settings,
                "crypto_market_research_external_sources",
                "coingecko,defillama,fear_greed",
            )
        ),
        auto_universe_enabled=bool(
            _setting(settings, "crypto_market_research_auto_universe_enabled", True)
        ),
        auto_universe_limit=int(
            _setting(settings, "crypto_market_research_auto_universe_limit", 300)
        ),
        research_universe_limit=int(
            _setting(settings, "crypto_market_research_research_universe_limit", 80)
        ),
        llm_top_symbols=int(_setting(settings, "crypto_market_research_llm_top_symbols", 30)),
        min_quote_volume_usdt=float(
            _setting(settings, "crypto_market_research_min_quote_volume_usdt", 100_000.0)
        ),
        kline_intervals=_parse_kline_intervals(
            _setting(
                settings,
                "crypto_market_research_kline_intervals",
                "1m:120,5m:96,15m:96,1h:168,4h:180",
            )
        ),
        regime_enabled=bool(
            _setting(settings, "crypto_market_research_regime_enabled", True)
        ),
        squeeze_guard_enabled=bool(
            _setting(settings, "crypto_market_research_squeeze_guard_enabled", True)
        ),
    )
    quant_repository = None
    if bool(_setting(settings, "crypto_quant_enabled", True)) and CryptoQuantRepository is not None:
        quant_repository = CryptoQuantRepository(
            str(_setting(settings, "crypto_quant_db_path", ".runtime/crypto_quant.db"))
        )
    return CryptoMarketResearchService(
        config=config,
        binance=binance,
        codex_runtime=bridge,
        memory_provider=memory_provider,
        quant_repository=quant_repository,
    )


def _build_crypto_alpha_service(
    settings: AppSettings,
    *,
    binance: BinanceAdapter,
) -> Any | None:
    if CryptoAlphaConfig is None or CryptoAlphaService is None:
        logger.warning("crypto alpha service is not available")
        return None
    if not bool(_setting(settings, "crypto_alpha_enabled", True)):
        return None
    return CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=str(_setting(settings, "crypto_alpha_db_path", ".runtime/crypto_alpha.db")),
            source_ids=str(
                _setting(
                    settings,
                    "crypto_alpha_source_ids",
                    "binance_announcements,coinbase_blog,kraken_blog",
                )
            ),
            rate_limit_sec=float(_setting(settings, "crypto_alpha_rate_limit_sec", 2.0)),
            context_limit=int(_setting(settings, "crypto_alpha_context_limit", 12)),
            llm_model=str(
                _setting(settings, "crypto_alpha_llm_model", "gpt-5.6-luna")
            ),
            llm_reasoning_effort=str(
                _setting(settings, "crypto_alpha_llm_reasoning_effort", "xhigh")
            ),
        ),
        binance=binance,
    )


def _build_crypto_pattern_service(settings: Any) -> Any | None:
    if not bool(_setting(settings, "crypto_pattern_lab_enabled", True)):
        return None
    if (
        CryptoPatternLabConfig is None
        or CryptoPatternLabService is None
        or HermesKlineReader is None
    ):
        logger.warning("crypto pattern lab service is not available")
        return None
    return CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(
                _setting(
                    settings,
                    "crypto_pattern_lab_db_path",
                    ".runtime/crypto_pattern_lab.db",
                )
            ),
            enabled=bool(_setting(settings, "crypto_pattern_lab_enabled", True)),
            strategy_paths=str(_setting(settings, "crypto_pattern_lab_strategy_paths", "")),
            freqtrade_data_paths=str(
                _setting(settings, "crypto_pattern_lab_freqtrade_data_paths", "")
            ),
            max_symbols=int(_setting(settings, "crypto_pattern_lab_max_symbols", 30)),
            intervals=str(_setting(settings, "crypto_pattern_lab_intervals", "5m,15m,1h")),
            lookback_bars=int(_setting(settings, "crypto_pattern_lab_lookback_bars", 500)),
            context_limit=int(_setting(settings, "crypto_pattern_lab_context_limit", 12)),
            retention_days=int(_setting(settings, "crypto_pattern_lab_retention_days", 90)),
            backtests_per_tuple_retention=int(
                _setting(
                    settings,
                    "crypto_pattern_lab_backtests_per_tuple_retention",
                    8,
                )
            ),
            optimizer_runs_per_tuple_retention=int(
                _setting(
                    settings,
                    "crypto_pattern_lab_optimizer_runs_per_tuple_retention",
                    8,
                )
            ),
            optimizer_trials_per_run_retention=int(
                _setting(
                    settings,
                    "crypto_pattern_lab_optimizer_trials_per_run_retention",
                    12,
                )
            ),
            max_backtest_rows=int(
                _setting(settings, "crypto_pattern_lab_max_backtest_rows", 80_000)
            ),
            max_optimizer_runs=int(
                _setting(settings, "crypto_pattern_lab_max_optimizer_runs", 2_500)
            ),
            max_optimizer_trials=int(
                _setting(settings, "crypto_pattern_lab_max_optimizer_trials", 24_000)
            ),
            optimizer_enabled=bool(
                _setting(settings, "crypto_pattern_lab_optimizer_enabled", True)
            ),
            optimizer_max_scorecards=int(
                _setting(settings, "crypto_pattern_lab_optimizer_max_scorecards", 60)
            ),
            optimizer_max_trials_per_scorecard=int(
                _setting(
                    settings,
                    "crypto_pattern_lab_optimizer_max_trials_per_scorecard",
                    24,
                )
            ),
        ),
        kline_reader=HermesKlineReader(
            str(
                _setting(
                    settings,
                    "crypto_market_research_db_path",
                    ".runtime/crypto_market_research.db",
                )
            )
        ),
    )


def _build_trader(settings: AppSettings) -> BinanceBlockTrader:
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=str(_setting(settings, "codex_runtime_mode", "auto")),
            sdk_codex_bin=str(_setting(settings, "codex_runtime_sdk_codex_bin", "")),
            timeout_ms=int(
                _setting(
                    settings,
                    "binance_block_trader_llm_timeout_ms",
                    _setting(settings, "codex_runtime_timeout_ms", 60000),
                )
            ),
            **llm_model_config_kwargs(settings, component="binance_block_manager"),
            usage_enabled=bool(_setting(settings, "llm_usage_enabled", True)),
            usage_db_path=str(_setting(settings, "llm_usage_db_path", ".runtime/llm_usage.db")),
            usage_component="binance_block_manager",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    memory_bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=str(_setting(settings, "codex_runtime_mode", "auto")),
            sdk_codex_bin=str(_setting(settings, "codex_runtime_sdk_codex_bin", "")),
            timeout_ms=int(_setting(settings, "codex_runtime_timeout_ms", 60000)),
            **llm_model_config_kwargs(settings, component="investment_memory"),
            usage_enabled=bool(_setting(settings, "llm_usage_enabled", True)),
            usage_db_path=str(
                _setting(settings, "llm_usage_db_path", ".runtime/llm_usage.db")
            ),
            usage_component="investment_memory",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    wiki_context_provider = _build_jue_wiki_context_provider(settings)
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=settings.investment_memory_root_path,
            db_path=settings.investment_memory_db_path,
            strategy_md_path=settings.research_strategy_md_path,
            policy_mode=settings.investment_memory_policy_mode,
            persona_tone=settings.investment_memory_persona_tone,
            telegram_enabled=False,
            context_max_chars=settings.investment_memory_context_max_chars,
            ops_summary_cache_ttl_sec=int(
                getattr(settings, "investment_memory_ops_summary_cache_ttl_sec", 10)
            ),
        ),
        codex_runtime=memory_bridge,
        wiki_context_provider=wiki_context_provider,
    )
    binance = _build_binance_adapter(settings)
    upbit = _build_upbit_adapter(settings)
    telegram = TelegramBridge(
        TelegramConfig(
            bot_token=str(_setting(settings, "telegram_bot_token", "") or ""),
            chat_id=str(_setting(settings, "telegram_chat_id", "") or ""),
        )
    )
    crypto_research = _build_crypto_research_service(
        settings,
        binance=binance,
        memory_provider=memory.context_pack,
    )
    crypto_alpha = _build_crypto_alpha_service(settings, binance=binance)
    crypto_patterns = _build_crypto_pattern_service(settings)
    quant_provider = None
    if bool(_setting(settings, "crypto_quant_enabled", True)) and CryptoQuantRepository is not None:
        quant_provider = CryptoQuantRepository(
            str(_setting(settings, "crypto_quant_db_path", ".runtime/crypto_quant.db"))
        )
    risk_sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=float(
                _setting(settings, "binance_block_trader_account_risk_pct", 0.25)
            ),
            max_total_exposure_usdt=float(
                _setting(settings, "binance_block_trader_max_total_exposure_usdt", 0.0)
            ),
            max_symbol_exposure_pct=float(
                _setting(settings, "binance_block_trader_max_symbol_exposure_pct", 25.0)
            ),
            min_reward_risk=float(
                _setting(settings, "binance_block_trader_min_reward_risk", 1.3)
            ),
        )
    )
    return BinanceBlockTrader(
        config=BinanceBlockTraderConfig(
            db_path=settings.binance_block_trader_db_path,
            state_path=settings.binance_block_trader_state_path,
            live_performance_db_path=str(
                _setting(settings, "live_performance_db_path", ".runtime/live_performance.db")
            ),
            enabled=settings.binance_block_trader_enabled,
            execute_spot_orders=settings.binance_block_trader_execute_spot_orders,
            execute_futures_orders=(
                settings.binance_block_trader_execute_futures_orders
            ),
            execute_upbit_orders=bool(
                _setting(settings, "binance_block_trader_execute_upbit_orders", False)
            ),
            quote_interval_sec=settings.binance_block_trader_quote_interval_sec,
            rule_interval_sec=settings.binance_block_trader_rule_interval_sec,
            manager_interval_sec=settings.binance_block_trader_manager_interval_sec,
            jue_wiki_read_mode=str(
                _setting(settings, "jue_wiki_read_mode", "shadow") or "shadow"
            ),
            waiting_entry_max_age_sec=int(
                _setting(
                    settings,
                    "binance_block_trader_waiting_entry_max_age_sec",
                    48 * 60 * 60,
                )
            ),
            entry_pending_max_age_sec=int(
                _setting(
                    settings,
                    "binance_block_trader_entry_pending_max_age_sec",
                    10 * 60,
                )
            ),
            aggressive_limit_bps=settings.binance_block_trader_aggressive_limit_bps,
            failed_exit_retry_cooldown_sec=(
                int(
                    _setting(
                        settings,
                        "binance_block_trader_failed_exit_retry_cooldown_sec",
                        60,
                    )
                )
            ),
            min_entry_confidence=float(
                _setting(settings, "binance_block_trader_min_entry_confidence", 0.58)
            ),
            min_entry_expected_r=float(
                _setting(settings, "binance_block_trader_min_entry_expected_r", 0.55)
            ),
            min_entry_directional_score=float(
                _setting(
                    settings,
                    "binance_block_trader_min_entry_directional_score",
                    62.0,
                )
            ),
            min_candidate_stop_pct=float(
                _setting(settings, "binance_block_trader_min_candidate_stop_pct", 1.2)
            ),
            profit_lock_trigger_r=float(
                _setting(settings, "binance_block_trader_profit_lock_trigger_r", 1.2)
            ),
            weak_lane_profit_lock_trigger_r=float(
                _setting(
                    settings,
                    "binance_block_trader_weak_lane_profit_lock_trigger_r",
                    0.8,
                )
            ),
            distressed_lane_profit_lock_trigger_r=float(
                _setting(
                    settings,
                    "binance_block_trader_distressed_lane_profit_lock_trigger_r",
                    0.55,
                )
            ),
            entry_quality_loss_tighten_trigger_r=float(
                _setting(
                    settings,
                    "binance_block_trader_entry_quality_loss_tighten_trigger_r",
                    0.5,
                )
            ),
            distressed_lane_min_samples=int(
                _setting(
                    settings,
                    "binance_block_trader_distressed_lane_min_samples",
                    5,
                )
            ),
            distressed_lane_max_win_rate_pct=float(
                _setting(
                    settings,
                    "binance_block_trader_distressed_lane_max_win_rate_pct",
                    20.0,
                )
            ),
            distressed_lane_max_profit_factor=float(
                _setting(
                    settings,
                    "binance_block_trader_distressed_lane_max_profit_factor",
                    0.5,
                )
            ),
            distressed_entry_quality_partial_profit_fraction=float(
                _setting(
                    settings,
                    "binance_block_trader_distressed_entry_quality_partial_profit_fraction",
                    0.75,
                )
            ),
            profit_lock_stop_r=float(
                _setting(settings, "binance_block_trader_profit_lock_stop_r", 0.25)
            ),
            spot_quote_budget_pct=float(
                _setting(settings, "binance_block_trader_spot_quote_budget_pct", 5.0)
            ),
            spot_min_quote_budget_usdt=float(
                _setting(settings, "binance_block_trader_spot_min_quote_budget_usdt", 50.0)
            ),
            spot_max_quote_budget_usdt=float(
                _setting(settings, "binance_block_trader_spot_max_quote_budget_usdt", 300.0)
            ),
            futures_quote_budget_pct=float(
                _setting(settings, "binance_block_trader_futures_quote_budget_pct", 10.0)
            ),
            futures_min_quote_budget_usdt=float(
                _setting(settings, "binance_block_trader_futures_min_quote_budget_usdt", 25.0)
            ),
            futures_max_quote_budget_usdt=float(
                _setting(settings, "binance_block_trader_futures_max_quote_budget_usdt", 150.0)
            ),
            volatile_attack_enabled=bool(
                _setting(settings, "binance_block_trader_volatile_attack_enabled", True)
            ),
            volatile_attack_candidate_limit=int(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_candidate_limit",
                    12,
                )
            ),
            volatile_attack_budget_multiplier=float(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_budget_multiplier",
                    0.35,
                )
            ),
            volatile_attack_min_change_pct=float(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_min_change_pct",
                    8.0,
                )
            ),
            volatile_attack_min_volume_expansion=float(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_min_volume_expansion",
                    1.8,
                )
            ),
            volatile_attack_min_reward_risk=float(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_min_reward_risk",
                    2.0,
                )
            ),
            volatile_attack_stop_multiplier=float(
                _setting(
                    settings,
                    "binance_block_trader_volatile_attack_stop_multiplier",
                    1.35,
                )
            ),
            daily_loss_stop_pct=float(
                _setting(
                    settings,
                    "binance_block_trader_daily_loss_stop_pct",
                    7.0,
                )
            ),
            monthly_loss_stop_pct=float(
                _setting(
                    settings,
                    "binance_block_trader_monthly_loss_stop_pct",
                    20.0,
                )
            ),
            budget_performance_scale_enabled=bool(
                _setting(
                    settings,
                    "binance_block_trader_budget_performance_scale_enabled",
                    True,
                )
            ),
            budget_performance_scale_min_samples=int(
                _setting(
                    settings,
                    "binance_block_trader_budget_performance_scale_min_samples",
                    10,
                )
            ),
            budget_performance_scale_win_rate_pct=float(
                _setting(
                    settings,
                    "binance_block_trader_budget_performance_scale_win_rate_pct",
                    55.0,
                )
            ),
            budget_performance_scale_multiplier=float(
                _setting(
                    settings,
                    "binance_block_trader_budget_performance_scale_multiplier",
                    1.5,
                )
            ),
            execution_defect_loss_multiplier=float(
                _setting(
                    settings,
                    "binance_block_trader_execution_defect_loss_multiplier",
                    0.5,
                )
            ),
            performance_scorecard_feedback_limit=int(
                _setting(
                    settings,
                    "binance_block_trader_performance_scorecard_feedback_limit",
                    120,
                )
            ),
            llm_timeout_ms=int(
                _setting(
                    settings,
                    "binance_block_trader_llm_timeout_ms",
                    _setting(settings, "codex_runtime_timeout_ms", 600_000),
                )
            ),
            max_manager_symbols=settings.binance_block_trader_max_manager_symbols,
            prompt_target_chars=int(
                _setting(settings, "binance_block_trader_prompt_target_chars", 45_000)
            ),
            prompt_warn_chars=int(
                _setting(settings, "binance_block_trader_prompt_warn_chars", 65_000)
            ),
            prompt_max_chars=int(
                _setting(settings, "binance_block_trader_prompt_max_chars", 190_000)
            ),
            jue_wiki_context_max_chars=int(
                _setting(
                    settings,
                    "binance_block_trader_jue_wiki_context_max_chars",
                    18_000,
                )
            ),
            strategy_revision_id=str(
                _setting(settings, "jue_strategy_revision_id", "jue_edge_repair_v1")
            ),
            quant_context_limit=int(_setting(settings, "crypto_quant_context_limit", 18)),
            max_futures_leverage=settings.binance_block_trader_max_futures_leverage,
            min_liquidation_distance_pct=(
                settings.binance_block_trader_min_liquidation_distance_pct
            ),
            spot_universe=settings.binance_block_trader_spot_universe,
            futures_universe=settings.binance_block_trader_futures_universe,
            upbit_universe=str(
                _setting(
                    settings,
                    "binance_block_trader_upbit_universe",
                    "KRW-BTC,KRW-ETH,KRW-SOL,KRW-XRP,KRW-DOGE",
                )
            ),
            upbit_quote_budget_pct=float(
                _setting(settings, "binance_block_trader_upbit_quote_budget_pct", 5.0)
            ),
            upbit_min_quote_budget_krw=float(
                _setting(settings, "binance_block_trader_upbit_min_quote_budget_krw", 10_000.0)
            ),
            upbit_max_quote_budget_krw=float(
                _setting(settings, "binance_block_trader_upbit_max_quote_budget_krw", 150_000.0)
            ),
            upbit_usdt_krw_rate=float(_setting(settings, "binance_usdt_krw", 1387.0)),
            llm_model=settings.binance_block_trader_llm_model,
            llm_reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
        ),
        binance=binance,
        upbit=upbit,
        codex_runtime=bridge,
        memory_context_provider=memory.context_pack,
        wiki_context_provider=wiki_context_provider,
        wiki_shadow_recording_recorder=build_runtime_recording_recorder(
            str(
                _setting(
                    settings,
                    "jue_wiki_shadow_db_path",
                    str(Path.home() / ".tradecraft" / "jue_wiki_shadow.db"),
                )
            ),
            provenance_key_path=str(_setting(
                settings,
                "jue_wiki_provenance_key_path",
                os.environ.get(
                    "TRADECRAFT_JUE_WIKI_PROVENANCE_KEY_PATH",
                    str(Path.home() / ".tradecraft" / "jue_wiki_provenance.key"),
                ),
            )),
        ),
        crypto_research_provider=crypto_research,
        crypto_alpha_provider=crypto_alpha,
        quant_provider=quant_provider,
        crypto_pattern_provider=crypto_patterns,
        live_authority_provider=lambda: build_live_authority_payload(settings)[
            "venues"
        ]["binance"],
        risk_sizer=risk_sizer,
        telegram=telegram,
    )


def _latest_manager_timestamp(trader: BinanceBlockTrader) -> float:
    repository = getattr(trader, "repository", None)
    latest_method = getattr(repository, "latest_manager_run", None)
    if latest_method is None:
        return 0.0
    try:
        latest = latest_method()
    except Exception:
        logger.exception("failed to read latest binance manager run timestamp")
        return 0.0
    if not isinstance(latest, dict):
        return 0.0
    raw = str(latest.get("run_at") or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _recover_last_manager_result(trader: BinanceBlockTrader) -> dict[str, Any] | None:
    repository = getattr(trader, "repository", None)
    latest_method = getattr(repository, "latest_manager_run", None)
    if latest_method is None:
        return None
    try:
        try:
            latest = latest_method(include_payload=False)
        except TypeError:
            latest = latest_method()
    except Exception:
        logger.exception("failed to recover latest binance manager result")
        return None
    if not isinstance(latest, dict):
        return None
    status = str(latest.get("status") or "").strip()
    if not status or status == "missing":
        return None
    result: dict[str, Any] = {"status": status}
    if latest.get("id") is not None:
        result["run_id"] = latest.get("id")
    for key in ("run_at", "mode", "model", "error_message"):
        value = latest.get(key)
        if value not in (None, ""):
            result[key] = value
    return result


def _runner_cycle_status(
    *,
    tick_result: dict[str, Any],
    manager_result: dict[str, Any] | None,
    last_manager_result: dict[str, Any] | None,
) -> str:
    tick_status = str(tick_result.get("status") or "ok").strip() or "ok"
    if tick_status.lower() not in {"ok", "success"}:
        return tick_status
    current_manager_status = (
        str(manager_result.get("status") or "").strip().lower()
        if isinstance(manager_result, dict)
        else ""
    )
    last_manager_status = (
        str(last_manager_result.get("status") or "").strip().lower()
        if isinstance(last_manager_result, dict)
        else ""
    )
    if current_manager_status == "error" or (
        current_manager_status in {"", "running"} and last_manager_status == "error"
    ):
        return "manager_error"
    return tick_status


def _compact_manager_text_list(value: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value[:limit]:
        text = _compact_text(item, limit=220)
        if text:
            items.append(text)
    return items


def _compact_manager_symbol_list(value: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    symbols: list[str] = []
    for item in value[:limit]:
        text = str(item or "").strip()
        if text:
            symbols.append(text)
    return symbols


def _compact_manager_trigger(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        text = _compact_text(item, limit=180)
        return {"text": text} if text else {}
    out: dict[str, Any] = {}
    for key in ("symbol", "condition", "price", "horizon", "reason", "side", "market"):
        value = item.get(key)
        if value in (None, "", [], {}):
            continue
        out[key] = _compact_text(value, limit=220) if isinstance(value, str) else value
    return out


def _compact_hold_decision_for_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in ("summary", "status", "stance"):
        text = _compact_text(value.get(key), limit=180)
        if text:
            compact[key] = text
    action_count = value.get("action_count")
    if action_count not in (None, ""):
        compact["action_count"] = action_count
    for key in ("watch_symbols", "long_watch_symbols"):
        symbols = _compact_manager_symbol_list(value.get(key))
        if symbols:
            compact[key] = symbols
    for key in ("reasons", "risks", "risk_notes", "data_gaps", "planned_actions"):
        items = _compact_manager_text_list(value.get(key))
        if items:
            compact[key] = items
    triggers = value.get("next_triggers")
    if isinstance(triggers, list) and triggers:
        compact["next_triggers"] = [
            item
            for item in (_compact_manager_trigger(trigger) for trigger in triggers[:5])
            if item
        ]
    return compact


def _first_manager_value(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    if value not in (None, "", [], {}):
        return value
    nested = row.get("input")
    if isinstance(nested, dict):
        value = nested.get(key)
        if value not in (None, "", [], {}):
            return value
    nested = row.get("block_template")
    if isinstance(nested, dict):
        value = nested.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _compact_manager_action_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        text = _compact_text(row, limit=220)
        return {"text": text} if text else {}
    compact: dict[str, Any] = {}
    for key in (
        "block_id",
        "symbol",
        "market",
        "side",
        "horizon",
        "lane",
        "status",
        "decision",
        "entry_style",
        "entry_trigger_operator",
        "confidence",
        "score",
        "entry_price",
        "entry_price_usdt",
        "entry_trigger_price",
        "target_price",
        "stop_price",
        "quote_budget",
        "quote_budget_usdt",
        "qty",
        "quantity",
        "leverage",
        "reward_risk",
        "expected_r",
    ):
        value = _first_manager_value(row, key)
        if value not in (None, "", [], {}):
            compact[key] = value
    for key in ("reason", "llm_reason", "thesis", "risk_note", "claim", "invalidation"):
        text = _compact_text(_first_manager_value(row, key), limit=180)
        if text:
            compact[key] = text
    evidence = _first_manager_value(row, "evidence_refs") or _first_manager_value(row, "evidence")
    if isinstance(evidence, list) and evidence:
        compact["evidence_refs"] = [_compact_text(item, limit=120) for item in evidence[:5]]
    blockers = _first_manager_value(row, "execution_blockers")
    if isinstance(blockers, list) and blockers:
        compact["execution_blockers"] = [
            _compact_text(item, limit=160) for item in blockers[:5]
        ]
    calculated = _first_manager_value(row, "calculated")
    if isinstance(calculated, dict):
        compact_calculated = {
            key: value
            for key, value in calculated.items()
            if key
            in {
                "method_version",
                "lane",
                "market",
                "side",
                "reward_risk",
                "expected_r",
                "stop_pct",
                "target_pct",
                "spread_bps",
                "volume_expansion_ratio",
                "funding_rate",
                "open_interest_change_pct",
            }
            and value not in (None, "", [], {})
        }
        if compact_calculated:
            compact["calculated"] = compact_calculated
    return compact


def _compact_manager_action_list(value: Any, *, sample_limit: int = 3) -> dict[str, Any]:
    if not isinstance(value, list):
        return {"item_count": 0, "items": []}
    items = [
        item
        for item in (_compact_manager_action_item(row) for row in value[:sample_limit])
        if item
    ]
    return {"item_count": len(value), "items": items}


def _compact_manager_action_section(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, rows in value.items():
        if isinstance(rows, list):
            compact[str(key)] = _compact_manager_action_list(rows)
        elif isinstance(rows, dict):
            compact[str(key)] = _compact_manager_action_item(rows)
    return compact


def _compact_lane_review_for_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "dominant_lane",
        "review_required",
        "selected_lanes",
        "lanes_reviewed",
        "concentration_note",
        "exploration_watch",
    ):
        item = value.get(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            compact[key] = _compact_text(item, limit=260)
        elif isinstance(item, list):
            compact[key] = item[:8]
        else:
            compact[key] = item
    summary = _compact_text(value.get("candidate_lane_summary"), limit=180)
    if summary:
        compact["candidate_lane_summary"] = summary
    reasons = value.get("non_selected_lane_reasons")
    if isinstance(reasons, dict):
        compact["non_selected_lane_reasons"] = {
            str(key): _compact_text(reason, limit=180)
            for key, reason in list(reasons.items())[:8]
        }
    return compact


def _manager_action_count_from_section(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    total = 0
    for rows in value.values():
        if isinstance(rows, list):
            total += len(rows)
        elif isinstance(rows, dict) and rows:
            total += 1
    return total


def _compact_manager_result_for_state(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if value.get("state_compacted") is True:
        return value
    try:
        original_size = len(json.dumps(value, ensure_ascii=False))
    except Exception:
        original_size = len(str(value))
    heavyweight_keys = {
        "actions",
        "applied",
        "hold_decision",
        "lane_review",
        "prompt_context",
        "context",
        "raw_prompt",
        "prompt",
    }
    should_compact = original_size > 6_000 or any(key in value for key in heavyweight_keys)
    if not should_compact:
        return value

    compact: dict[str, Any] = {"state_compacted": True}
    for key in (
        "status",
        "manager_run_id",
        "run_id",
        "run_at",
        "mode",
        "model",
        "started_at",
        "elapsed_sec",
        "timeout_sec",
    ):
        item = value.get(key)
        if item not in (None, "", [], {}):
            compact[key] = item
    error = _compact_text(value.get("error_message"), limit=360)
    if error:
        compact["error_message"] = error
    hold_decision = _compact_hold_decision_for_state(value.get("hold_decision"))
    if hold_decision:
        compact["hold_decision"] = hold_decision
    lane_review = _compact_lane_review_for_state(value.get("lane_review"))
    if lane_review:
        compact["lane_review"] = lane_review
    actions = _compact_manager_action_section(value.get("actions"))
    if actions:
        compact["actions"] = actions
    applied = _compact_manager_action_section(value.get("applied"))
    if applied:
        compact["applied"] = applied
    action_count = value.get("action_count")
    if action_count in (None, "") and isinstance(hold_decision, dict):
        action_count = hold_decision.get("action_count")
    if action_count in (None, ""):
        action_count = _manager_action_count_from_section(value.get("actions"))
    if action_count not in (None, ""):
        compact["action_count"] = action_count
    return compact


def _compact_runner_status_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "execution_mode",
        "open_block_count",
        "proposed_block_count",
        "error_block_count",
        "inactive_error_block_count",
        "total_error_block_count",
        "dangling_error_qty_block_count",
        "model",
    ):
        value = snapshot.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    account = snapshot.get("account")
    if isinstance(account, dict):
        compact["account"] = {
            key: value
            for key, value in account.items()
            if key
            in {
                "status",
                "stale",
                "account_snapshot_source",
                "total_equity_usdt",
                "spot_cash_usdt",
                "futures_cash_usdt",
                "upbit_cash_krw",
                "upbit_cash_usdt",
                "upbit_usdt_krw_rate",
            }
            and value not in (None, "", [], {})
        }
    risk_guard = snapshot.get("risk_guard")
    if isinstance(risk_guard, dict):
        compact["risk_guard"] = {
            key: value
            for key, value in risk_guard.items()
            if key
            in {
                "status",
                "current_equity_usdt",
                "allow_new_entries",
                "daily_loss_stop_pct",
                "monthly_loss_stop_pct",
            }
            and value not in (None, "", [], {})
        }
    growth_governor = snapshot.get("growth_governor")
    if isinstance(growth_governor, dict):
        compact["growth_governor"] = {
            key: value
            for key, value in growth_governor.items()
            if key
            in {
                "status",
                "mode",
                "allow_new_blocks",
                "max_new_blocks",
                "require_waiting_entry",
                "aggression_multiplier",
                "reasons",
            }
            and value not in (None, "", [], {})
        }
    growth_unlock = snapshot.get("growth_unlock")
    if isinstance(growth_unlock, dict):
        compact["growth_unlock"] = {
            key: value
            for key, value in growth_unlock.items()
            if key in {"phase", "can_leave_edge_rebuild"} and value not in (None, "", [], {})
        }
    kill_switch = snapshot.get("kill_switch")
    if isinstance(kill_switch, dict):
        compact["kill_switch"] = {
            key: value
            for key, value in kill_switch.items()
            if key in {"enabled", "reason", "updated_at"} and value not in (None, "", [], {})
        }
    performance_today = snapshot.get("performance_today")
    if isinstance(performance_today, dict):
        compact["performance_today"] = {
            key: value
            for key, value in performance_today.items()
            if key
            in {
                "sample_count",
                "realized_pnl_usdt",
                "total_cost_usdt",
                "win_rate_pct",
                "profit_factor",
            }
            and value not in (None, "", [], {})
        }
    active_blocks = snapshot.get("active_blocks")
    if isinstance(active_blocks, list):
        compact["active_block_count"] = len(active_blocks)
    manager_runs = snapshot.get("manager_runs")
    if isinstance(manager_runs, list) and manager_runs and isinstance(manager_runs[0], dict):
        latest = manager_runs[0]
        compact["latest_manager_run_status"] = latest.get("status")
        compact["latest_manager_run_at"] = latest.get("run_at")
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _status_snapshot_has_live_work(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    for key in (
        "open_block_count",
        "proposed_block_count",
        "active_block_count",
        "pending_order_count",
    ):
        try:
            if float(snapshot.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _tick_result_has_actions(tick_result: dict[str, Any] | None) -> bool:
    if not isinstance(tick_result, dict):
        return False
    actions = tick_result.get("actions")
    if isinstance(actions, list):
        return bool(actions)
    try:
        return float(tick_result.get("action_count") or 0) > 0
    except (TypeError, ValueError):
        return False


async def _runner_status_snapshot(trader: BinanceBlockTrader) -> dict[str, Any]:
    method = getattr(trader, "snapshot_compact", None)
    if method is None:
        return {}
    try:
        snapshot = method()
        if inspect.isawaitable(snapshot):
            snapshot = await _await_stage(
                "status_snapshot",
                snapshot,
                timeout_sec=EXECUTOR_STAGE_TIMEOUT_SEC,
            )
        return _compact_runner_status_snapshot(snapshot)
    except Exception as exc:
        logger.warning("binance runner status snapshot failed: %s", exc, exc_info=True)
        return {"status": "error", "error_message": _compact_text(exc, limit=180)}


async def run_binance_block_trader_loop(
    *,
    settings: AppSettings,
    trader: BinanceBlockTrader | None = None,
    sleep: SleepFn = asyncio.sleep,
    now_provider: NowFn = _utc_now,
) -> None:
    resolved_trader = trader or _build_trader(settings)
    store = RuntimeStateStore(settings.binance_block_trader_state_path)
    rule_interval = max(int(settings.binance_block_trader_rule_interval_sec), 5)
    manager_interval = max(int(settings.binance_block_trader_manager_interval_sec), 60)
    manager_error_retry_sec = max(
        int(_setting(settings, "binance_block_trader_manager_error_retry_sec", 300)),
        60,
    )
    retention_interval = max(
        int(_setting(settings, "binance_block_trader_retention_interval_sec", 3600)),
        60,
    )
    performance_feedback_interval = max(
        int(
            _setting(
                settings,
                "binance_block_trader_performance_feedback_interval_sec",
                300,
            )
        ),
        60,
    )
    runner_started_at = now_provider()
    if runner_started_at.tzinfo is None:
        runner_started_at = runner_started_at.replace(tzinfo=timezone.utc)
    cycle = 0
    last_manager_at = _latest_manager_timestamp(resolved_trader)
    last_retention_at: float | None = None
    last_performance_feedback_at: float | None = None
    manager_task: asyncio.Task[dict[str, Any]] | None = None
    manager_started_at: datetime | None = None
    previous_snapshot = store.read_snapshot() or {}
    last_manager_result = (
        dict(previous_snapshot.get("last_manager_result"))
        if isinstance(previous_snapshot.get("last_manager_result"), dict)
        else None
    )
    if last_manager_result is None:
        last_manager_result = _recover_last_manager_result(resolved_trader)
    telegram_reports_sent = (
        dict(previous_snapshot.get("telegram_reports_sent"))
        if isinstance(previous_snapshot.get("telegram_reports_sent"), dict)
        else {}
    )
    last_status_snapshot = (
        dict(previous_snapshot.get("status_snapshot"))
        if isinstance(previous_snapshot.get("status_snapshot"), dict)
        else None
    )
    last_manager_due_reason = str(
        previous_snapshot.get("last_manager_due_reason")
        or previous_snapshot.get("manager_due_reason")
        or ""
    ).strip() or None

    while True:
        cycle += 1
        now_dt = now_provider()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        now = now_dt.timestamp()
        manager_used = False
        manager_result: dict[str, Any] | None = None
        adoption_result: dict[str, Any] | None = None
        performance_result: dict[str, Any] | None = None
        retention_result: dict[str, Any] | None = None
        telegram_report_result: dict[str, Any] | None = None
        status_snapshot: dict[str, Any] | None = None
        manager_due_reason: str | None = None
        try:
            manager_finished_this_cycle = False
            if manager_task is not None and manager_task.done():
                try:
                    manager_result = manager_task.result()
                except asyncio.CancelledError:
                    logger.exception("binance block trader manager task was cancelled")
                    manager_result = {
                        "status": "error",
                        "error_message": "manager task cancelled",
                    }
                except Exception as exc:
                    logger.exception("binance block trader manager task failed")
                    manager_result = {"status": "error", "error_message": str(exc)}
                manager_task = None
                manager_started_at = None
                last_manager_at = now
                manager_used = True
                manager_finished_this_cycle = True
            if (
                manager_task is not None
                and not manager_task.done()
                and manager_started_at is not None
            ):
                elapsed_sec = max((now_dt - manager_started_at).total_seconds(), 0.0)
                manager_timeout_sec = _manager_task_timeout_sec(settings)
                if elapsed_sec >= manager_timeout_sec:
                    manager_task.cancel()
                    try:
                        await manager_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "binance block trader manager task raised during timeout cancel: %s",
                            exc,
                        )
                    timeout_message = _manager_task_timeout_message(
                        manager_timeout_sec
                    )
                    logger.error(
                        "binance block trader manager task timed out: elapsed=%.1fs timeout=%.1fs",
                        elapsed_sec,
                        manager_timeout_sec,
                    )
                    manager_result = {
                        "status": "error",
                        "error_message": timeout_message,
                        "started_at": manager_started_at.isoformat(),
                        "elapsed_sec": round(elapsed_sec, 3),
                        "timeout_sec": round(manager_timeout_sec, 3),
                    }
                    timeout_record = _record_manager_task_timeout_run(
                        resolved_trader,
                        settings=settings,
                        timeout_message=timeout_message,
                    )
                    if timeout_record.get("recorded"):
                        manager_result["run_id"] = timeout_record.get("run_id")
                    elif timeout_record.get("error_message"):
                        manager_result["record_error"] = timeout_record.get(
                            "error_message"
                        )
                    manager_task = None
                    manager_started_at = None
                    last_manager_at = now
                    manager_used = True
                    manager_finished_this_cycle = True
            latest_manager_failed = (
                isinstance(last_manager_result, dict)
                and str(last_manager_result.get("status") or "").strip().lower()
                == "error"
            )
            manager_error_retry_due = (
                latest_manager_failed
                and not manager_finished_this_cycle
                and manager_task is None
                and now - last_manager_at >= manager_error_retry_sec
                and now - last_manager_at < manager_interval
            )
            regular_manager_due = (
                not manager_finished_this_cycle
                and manager_task is None
                and now - last_manager_at >= manager_interval
            )
            if settings.binance_block_trader_once:
                manager_due = True
                manager_due_reason = "once"
            elif manager_error_retry_due:
                manager_due = True
                manager_due_reason = "retry_after_manager_error"
            elif regular_manager_due:
                manager_due = True
                manager_due_reason = "regular_interval"
            else:
                manager_due = False
            adoption_method = getattr(resolved_trader, "run_spot_adoption_once", None)
            if adoption_method is not None and (cycle == 1 or manager_due):
                try:
                    adoption_result = await _await_stage(
                        "spot_adoption",
                        adoption_method(),
                        timeout_sec=ACCOUNT_STAGE_TIMEOUT_SEC,
                    )
                except Exception as exc:
                    logger.warning("binance spot adoption stage failed: %s", exc)
                    adoption_result = {
                        "status": "error",
                        "error_message": str(exc),
                    }
            upbit_adoption_method = getattr(resolved_trader, "run_upbit_adoption_once", None)
            if upbit_adoption_method is not None and (cycle == 1 or manager_due):
                try:
                    upbit_adoption_result = await _await_stage(
                        "upbit_spot_adoption",
                        upbit_adoption_method(),
                        timeout_sec=ACCOUNT_STAGE_TIMEOUT_SEC,
                    )
                    if isinstance(adoption_result, dict):
                        adoption_result = {
                            **adoption_result,
                            "upbit_spot": upbit_adoption_result,
                        }
                    else:
                        adoption_result = {"upbit_spot": upbit_adoption_result}
                except Exception as exc:
                    logger.warning("upbit spot adoption stage failed: %s", exc)
                    if isinstance(adoption_result, dict):
                        adoption_result = {
                            **adoption_result,
                            "upbit_spot": {
                                "status": "error",
                                "error_message": str(exc),
                            },
                        }
                    else:
                        adoption_result = {
                            "upbit_spot": {
                                "status": "error",
                                "error_message": str(exc),
                            }
                        }
            tick_result = await _await_stage(
                "executor_tick",
                resolved_trader.executor_tick(),
                timeout_sec=EXECUTOR_STAGE_TIMEOUT_SEC,
            )
            if manager_due:
                last_manager_at = now
                manager_used = True
                last_manager_due_reason = manager_due_reason
                if settings.binance_block_trader_once:
                    manager_result = await resolved_trader.run_manager_once()
                else:
                    manager_started_at = now_dt
                    manager_task = asyncio.create_task(resolved_trader.run_manager_once())
                    manager_result = {
                        "status": "running",
                        "started_at": now_dt.isoformat(),
                    }
            elif manager_task is not None:
                manager_due_reason = last_manager_due_reason
                manager_result = {
                    "status": "running",
                    "started_at": manager_started_at.isoformat()
                    if manager_started_at is not None
                    else "",
                }
            performance_method = getattr(
                resolved_trader,
                "run_performance_feedback_once",
                None,
            )
            performance_due = (
                last_performance_feedback_at is None
                or manager_used
                or _tick_result_has_actions(tick_result)
                or now - last_performance_feedback_at >= performance_feedback_interval
            )
            if performance_method is not None and performance_due:
                last_performance_feedback_at = now
                performance_result = performance_method()
            if (
                isinstance(manager_result, dict)
                and str(manager_result.get("status") or "") != "running"
            ):
                last_manager_result = _compact_manager_result_for_state(manager_result)
            telegram_reports_sent = _prune_telegram_report_state(
                telegram_reports_sent,
                now=now_dt,
            )
            telegram_report_result = await _send_due_telegram_report(
                settings=settings,
                trader=resolved_trader,
                sent=telegram_reports_sent,
                now=now_dt,
            )
            prune_method = getattr(resolved_trader, "prune_operational_history", None)
            retention_due = (
                last_retention_at is None
                or now - last_retention_at >= retention_interval
            )
            if prune_method is not None and retention_due:
                last_retention_at = now
                try:
                    retention_result = prune_method(
                        quote_retention_days=int(
                            _setting(
                                settings,
                                "binance_block_trader_quote_retention_days",
                                7,
                            )
                        ),
                        manager_run_retention_days=int(
                            _setting(
                                settings,
                                "binance_block_trader_manager_run_retention_days",
                                14,
                            )
                        ),
                        archive_retention_days=int(
                            _setting(
                                settings,
                                "binance_block_trader_archive_retention_days",
                                14,
                            )
                        ),
                    )
                except Exception as exc:
                    logger.warning("binance block trader retention cleanup failed: %s", exc)
                    retention_result = {
                        "status": "error",
                        "error_message": str(exc),
                    }
            status_snapshot_due = (
                cycle == 1
                or manager_used
                or _tick_result_has_actions(tick_result)
                or _status_snapshot_has_live_work(last_status_snapshot)
            )
            if status_snapshot_due:
                status_snapshot = await _runner_status_snapshot(resolved_trader)
                last_status_snapshot = status_snapshot
            else:
                status_snapshot = last_status_snapshot or await _runner_status_snapshot(
                    resolved_trader
                )
                last_status_snapshot = status_snapshot
            status = _runner_cycle_status(
                tick_result=tick_result,
                manager_result=manager_result,
                last_manager_result=last_manager_result,
            )
        except Exception as exc:
            logger.exception("binance block trader cycle failed")
            status = "error"
            tick_result = {"status": "error", "error_message": str(exc)}
            status_snapshot = status_snapshot or {
                "status": "error",
                "error_message": str(exc),
            }

        snapshot = {
            "service": "tradecraft-binance-block-trader",
            "status": status,
            "cycle": cycle,
            "updated_at": now_provider().isoformat(),
            "rule_interval_sec": rule_interval,
            "manager_interval_sec": manager_interval,
            "manager_error_retry_sec": manager_error_retry_sec,
            "manager_due_reason": manager_due_reason,
            "last_manager_due_reason": last_manager_due_reason,
            "adoption_result": adoption_result,
            "manager_used": manager_used,
            "manager_result": _compact_manager_result_for_state(manager_result),
            "last_manager_result": _compact_manager_result_for_state(last_manager_result),
            "performance_result": performance_result,
            "retention_result": retention_result,
            "telegram_report_result": telegram_report_result,
            "telegram_reports_sent": telegram_reports_sent,
            "telegram_report_slots": _parse_telegram_report_slots(
                _setting(
                    settings,
                    "binance_block_trader_telegram_report_slots",
                    DEFAULT_TELEGRAM_REPORT_SLOTS,
                )
            ),
            "runner_source_freshness": _runner_source_freshness(
                started_at=runner_started_at,
            ),
            "tick_result": tick_result,
            "status_snapshot": status_snapshot,
        }
        store.write_snapshot(snapshot)
        action_count = (
            len(list(tick_result.get("actions") or []))
            if isinstance(tick_result, dict)
            else 0
        )
        logger.log(
            _cycle_log_level(
                status=status,
                manager_used=manager_used,
                action_count=action_count,
            ),
            "binance block trader cycle=%s status=%s manager=%s actions=%s",
            cycle,
            status,
            manager_used,
            action_count,
        )
        if settings.binance_block_trader_once:
            return
        await sleep(float(rule_interval))


def run() -> None:
    write_current_runner_pid("binance_block_trader")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if not settings.binance_block_trader_enabled:
            logger.info(
                "binance block trader disabled: "
                "TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED=false"
            )
            return
        asyncio.run(run_binance_block_trader_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("binance block trader runner interrupted; stopping")
    finally:
        clear_current_runner_pid("binance_block_trader")


if __name__ == "__main__":
    run()
