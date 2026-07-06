from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService
from tradecraft.services.jue_wiki_performance import JueWikiPerformanceProjector
from tradecraft.services.jue_wiki_playbooks import JueWikiPlaybookCompiler

logger = logging.getLogger(__name__)

STATE_PATH = Path(".runtime/jue_wiki_runner.json")
MIN_INTERVAL_SEC = 300


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _step_count(payload: dict[str, Any]) -> int:
    if "updated_count" in payload:
        return int(payload.get("updated_count") or 0)
    if isinstance(payload.get("actions"), list):
        return len(payload["actions"])
    if isinstance(payload.get("open_findings"), list):
        return len(payload["open_findings"])
    return int(payload.get("count") or 0)


def _run_step(name: str, operation: Any) -> dict[str, Any]:
    started_at = _utc_now_iso()
    try:
        payload = operation()
    except Exception as exc:
        logger.exception("jue wiki %s step failed: %s", name, exc)
        return {
            "status": "error",
            "error_message": str(exc),
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "count": 0,
        }
    if not isinstance(payload, dict):
        payload = {"status": "ok", "result": payload}
    result = dict(payload)
    result.setdefault("status", "ok")
    result.setdefault("started_at", started_at)
    result.setdefault("finished_at", _utc_now_iso())
    result.setdefault("count", _step_count(result))
    return result


def _path_from(
    *,
    explicit: str | Path | None,
    settings: AppSettings | None,
    service_value: Path | None = None,
    setting_name: str = "",
    default: str,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    if service_value is not None:
        return Path(service_value)
    if settings is not None and setting_name:
        value = getattr(settings, setting_name, None)
        if value:
            return Path(value)
    return Path(default)


def run_once(
    *,
    service: JueWikiService,
    state_path: Path = STATE_PATH,
    settings: AppSettings | None = None,
    investment_memory_db_path: str | Path | None = None,
    performance_db_path: str | Path | None = None,
    market_judgment_db_path: str | Path | None = None,
    repair_enabled: bool = True,
    application_enabled: bool = True,
    effectiveness_min_samples: int = 5,
    mode_recommendation_min_samples: int = 20,
) -> dict[str, Any]:
    started_at = _utc_now_iso()
    resolved_investment_memory_db_path = _path_from(
        explicit=investment_memory_db_path,
        settings=settings,
        service_value=service.config.investment_memory_db_path,
        setting_name="investment_memory_db_path",
        default=".runtime/investment_memory.db",
    )
    resolved_performance_db_path = _path_from(
        explicit=performance_db_path,
        settings=settings,
        setting_name="live_performance_db_path",
        default=".runtime/live_performance.db",
    )
    resolved_market_judgment_db_path = _path_from(
        explicit=market_judgment_db_path,
        settings=settings,
        setting_name="market_judge_db_path",
        default=".runtime/market_judgment.db",
    )
    if settings is not None:
        repair_enabled = bool(
            getattr(settings, "jue_wiki_repair_enabled", repair_enabled)
        )
        application_enabled = bool(
            getattr(settings, "jue_wiki_application_enabled", application_enabled)
        )
        effectiveness_min_samples = int(
            getattr(
                settings,
                "jue_wiki_effectiveness_min_samples",
                effectiveness_min_samples,
            )
        )
        mode_recommendation_min_samples = int(
            getattr(
                settings,
                "jue_wiki_mode_recommendation_min_samples",
                mode_recommendation_min_samples,
            )
        )

    rebuild = _run_step(
        "rebuild",
        lambda: service.rebuild(scope="all", force=False),
    )
    lint = _run_step("lint", lambda: service.lint(scope="all"))
    if repair_enabled:
        repair = _run_step("repair", lambda: service.repair_once(scope=None))
    else:
        repair = {
            "status": "skipped",
            "enabled": False,
            "started_at": _utc_now_iso(),
            "finished_at": _utc_now_iso(),
            "count": 0,
        }
    playbooks = _run_step(
        "playbooks",
        lambda: JueWikiPlaybookCompiler(
            service,
            investment_memory_db_path=resolved_investment_memory_db_path,
        ).compile_all(),
    )
    performance = _run_step(
        "performance",
        lambda: JueWikiPerformanceProjector(
            service,
            performance_db_path=resolved_performance_db_path,
        ).project_all(),
    )
    if application_enabled:
        application_service = JueWikiApplicationService(service)
        application = _run_step(
            "application",
            lambda: {
                "status": "ok",
                "decision_links": application_service.project_decision_links(
                    market_judgment_db_path=resolved_market_judgment_db_path,
                ),
                "decision_link_backfill": (
                    application_service.backfill_decision_link_selected_wiki_pages()
                ),
                "outcomes": application_service.project_selection_outcomes(
                    market_judgment_db_path=resolved_market_judgment_db_path,
                ),
                "effectiveness": application_service.project_page_effectiveness(
                    min_samples=effectiveness_min_samples,
                ),
                "mode_recommendations": application_service.project_mode_recommendations(
                    min_samples=mode_recommendation_min_samples,
                    current_modes={},
                ),
                **application_service.status(),
            },
        )
        _promote_application_warning(application)
    else:
        application = {
            "status": "disabled",
            "effectiveness": {"status": "disabled"},
            "mode_recommendations": {"status": "disabled", "recommendations": []},
            "started_at": _utc_now_iso(),
            "finished_at": _utc_now_iso(),
            "count": 0,
        }
    if repair_enabled and application_enabled:
        repair_finalization = _run_step(
            "repair_finalization",
            lambda: service.repair_once(scope=None),
        )
    else:
        repair_finalization = {
            "status": "skipped",
            "enabled": False,
            "started_at": _utc_now_iso(),
            "finished_at": _utc_now_iso(),
            "count": 0,
        }
    steps = [
        rebuild,
        lint,
        repair,
        playbooks,
        performance,
        application,
        repair_finalization,
    ]
    status = _combined_step_status(steps)
    result = {
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now_iso(),
        "rebuild": rebuild,
        "lint": lint,
        "repair": repair,
        "playbooks": playbooks,
        "performance": performance,
        "application": application,
        "repair_finalization": repair_finalization,
    }
    _write_state(state_path, result)
    return result


def _promote_application_warning(payload: dict[str, Any]) -> None:
    if str(payload.get("status") or "").lower() != "ok":
        return
    if str(payload.get("wiki_application_health") or "").lower() != "warning":
        return
    alerts = payload.get("wiki_application_alerts")
    warning_count = len(alerts) if isinstance(alerts, list) else 0
    payload["status"] = "warning"
    payload["warning_count"] = warning_count


def _combined_step_status(steps: list[dict[str, Any]]) -> str:
    statuses = {str(step.get("status") or "").lower() for step in steps}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


def build_service(settings: AppSettings | None = None) -> JueWikiService:
    resolved_settings = settings or AppSettings()
    report_stack = build_report_intelligence_stack(resolved_settings)
    return JueWikiService(
        JueWikiConfig(
            root_path=Path(resolved_settings.jue_wiki_root_path),
            db_path=Path(resolved_settings.jue_wiki_db_path),
            enabled=bool(resolved_settings.jue_wiki_enabled),
            context_max_chars=resolved_settings.jue_wiki_context_max_chars,
            page_max_chars=resolved_settings.jue_wiki_page_max_chars,
            context_page_limit=resolved_settings.jue_wiki_context_page_limit,
            kis_blocks_db_path=Path(resolved_settings.kis_block_trader_db_path),
            binance_blocks_db_path=Path(
                resolved_settings.binance_block_trader_db_path
            ),
            investment_memory_db_path=Path(
                resolved_settings.investment_memory_db_path
            ),
            daily_discovery_db_path=Path(resolved_settings.daily_discovery_db_path),
            trading_validation_db_path=Path(
                resolved_settings.trading_validation_db_path
            ),
            naver_reports_db_path=Path(resolved_settings.naver_reports_db_path),
            symbol_fundamentals_db_path=Path(resolved_settings.valuation_db_path),
            crypto_market_research_db_path=Path(
                resolved_settings.crypto_market_research_db_path
            ),
            market_pulse_db_path=Path(resolved_settings.market_pulse_db_path),
            etf_research_db_path=Path(resolved_settings.etf_research_db_path),
            strategy_insights_db_path=Path(
                resolved_settings.strategy_insight_db_path
            ),
            crypto_quant_db_path=Path(resolved_settings.crypto_quant_db_path),
            crypto_pattern_lab_db_path=Path(
                resolved_settings.crypto_pattern_lab_db_path
            ),
            crypto_alpha_db_path=Path(resolved_settings.crypto_alpha_db_path),
        ),
        rag_store=report_stack.rag_store,
    )


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    write_current_runner_pid("jue_wiki")
    settings = AppSettings()
    service = build_service(settings)
    interval = max(int(settings.jue_wiki_runner_interval_sec), MIN_INTERVAL_SEC)
    try:
        while True:
            try:
                result = run_once(
                    service=service,
                    state_path=STATE_PATH,
                    settings=settings,
                    repair_enabled=bool(settings.jue_wiki_repair_enabled),
                    application_enabled=bool(settings.jue_wiki_application_enabled),
                    effectiveness_min_samples=int(
                        settings.jue_wiki_effectiveness_min_samples
                    ),
                    mode_recommendation_min_samples=int(
                        settings.jue_wiki_mode_recommendation_min_samples
                    ),
                )
                logger.info(
                    "jue wiki cycle status=%s updated=%s lint=%s",
                    result["status"],
                    result["rebuild"].get("updated_count"),
                    result["lint"].get("status"),
                )
            except Exception as exc:
                logger.exception("jue wiki cycle failed: %s", exc)
                _write_state(
                    STATE_PATH,
                    {
                        "status": "error",
                        "error_message": str(exc),
                        "finished_at": _utc_now_iso(),
                    },
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("jue wiki runner interrupted; stopping")
