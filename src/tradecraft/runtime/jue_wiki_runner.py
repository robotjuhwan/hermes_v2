from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_context import wiki_eligibility_freshness_reason
from tradecraft.services.jue_wiki_application import JueWikiApplicationService
from tradecraft.services.jue_wiki_compiler import JueWikiPublisherV1
from tradecraft.services.jue_wiki_lint import lint_snapshot
from tradecraft.services.jue_wiki_performance import JueWikiPerformanceProjector
from tradecraft.services.jue_wiki_playbooks import JueWikiPlaybookCompiler
from tradecraft.services.jue_wiki_projection import JueWikiProjectionWriter
from tradecraft.services.jue_wiki_sources import (
    CryptoWikiSourceAdapter,
    NaverWikiSourceAdapter,
)
from tradecraft.services.jue_wiki_shadow import (
    JueWikiShadowStore,
    WikiCompletionSigner,
)
from tradecraft.services.naver_reports import NaverReportRepository
from tradecraft.services.runtime_cold_archive_status import (
    persist_runtime_cold_archive_status,
)

logger = logging.getLogger(__name__)

STATE_PATH = Path(".runtime/jue_wiki_runner.json")
MIN_INTERVAL_SEC = 300
V3_SCOPES = ("kis", "binance")
V3_SOURCE_LIMIT = 100


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


def _v3_step_payload(
    *,
    scope: str,
    status: str,
    candidate_count: int = 0,
    snapshot_id: str = "",
    page_count: int = 0,
    warning_count: int = 0,
    elapsed_sec: float = 0.0,
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "scope": scope,
        "candidate_count": int(candidate_count),
        "snapshot_id": snapshot_id,
        "snapshot_count": 1 if snapshot_id else 0,
        "page_count": int(page_count),
        "warning_count": int(warning_count),
        "elapsed_sec": max(float(elapsed_sec), 0.0),
        "error_message": error_message,
    }


def _v3_setup_failure(
    *,
    scope: str,
    error: Exception | str,
    elapsed_sec: float = 0.0,
) -> dict[str, Any]:
    error_message = str(error)
    ingest = _v3_step_payload(
        scope=scope,
        status="error",
        elapsed_sec=elapsed_sec,
        error_message=error_message,
    )
    ingest["phase"] = "setup"
    skipped = _v3_step_payload(
        scope=scope,
        status="skipped",
        error_message="v3_setup_failed",
    )
    result = {
        **_v3_step_payload(
            scope=scope,
            status="error",
            elapsed_sec=elapsed_sec,
            error_message=error_message,
        ),
        "phase": "setup",
        "cleanup_warnings": [],
        "v3_ingest": ingest,
        "v3_compile": dict(skipped),
        "v3_lint": dict(skipped),
        "v3_publish": dict(skipped),
        "v3_projection": dict(skipped),
    }
    return result


def run_v3_scope(
    service: JueWikiService,
    scope: str,
    adapters: Any,
    publisher: Any,
    projection_writer: Any | None,
) -> dict[str, Any]:
    """Ingest and publish one isolated V3 Wiki scope."""
    started = time.perf_counter()
    try:
        repository = service.repository()
        repository.initialize()
    except Exception as exc:
        return _v3_setup_failure(
            scope=scope,
            error=exc,
            elapsed_sec=time.perf_counter() - started,
        )
    artifacts: list[Any] = []
    ingest_started = time.perf_counter()
    try:
        observed_at = _utc_now_iso()
        for adapter in adapters:
            artifacts.extend(adapter.collect((), observed_at))
        ordered_artifacts = tuple(
            sorted(artifacts, key=lambda artifact: artifact.artifact_id)
        )
        for artifact in ordered_artifacts:
            for evidence in artifact.source_refs:
                repository.register_evidence(evidence)
            repository.store_candidate(artifact)
        artifact_ids = tuple(artifact.artifact_id for artifact in ordered_artifacts)
        v3_ingest = _v3_step_payload(
            scope=scope,
            status="ok",
            candidate_count=len(artifact_ids),
            elapsed_sec=time.perf_counter() - ingest_started,
        )
    except Exception as exc:
        elapsed_sec = time.perf_counter() - started
        v3_ingest = _v3_step_payload(
            scope=scope,
            status="error",
            candidate_count=len(artifacts),
            elapsed_sec=time.perf_counter() - ingest_started,
            error_message=str(exc),
        )
        skipped = _v3_step_payload(
            scope=scope,
            status="skipped",
            candidate_count=len(artifacts),
            error_message="v3_ingest_failed",
        )
        return {
            **_v3_step_payload(
                scope=scope,
                status="error",
                candidate_count=len(artifacts),
                elapsed_sec=elapsed_sec,
                error_message=str(exc),
            ),
            "cleanup_warnings": [],
            "v3_ingest": v3_ingest,
            "v3_compile": dict(skipped),
            "v3_lint": dict(skipped),
            "v3_publish": dict(skipped),
            "v3_projection": dict(skipped),
        }

    compile_started = time.perf_counter()
    try:
        snapshot = publisher.compile_snapshot(
            scope=scope,
            artifact_ids=artifact_ids,
        )
    except Exception as exc:
        elapsed_sec = time.perf_counter() - started
        error_message = str(exc)
        v3_compile = _v3_step_payload(
            scope=scope,
            status="error",
            candidate_count=len(artifact_ids),
            elapsed_sec=time.perf_counter() - compile_started,
            error_message=error_message,
        )
        skipped = _v3_step_payload(
            scope=scope,
            status="skipped",
            candidate_count=len(artifact_ids),
            error_message="v3_compile_failed",
        )
        return {
            **_v3_step_payload(
                scope=scope,
                status="error",
                candidate_count=len(artifact_ids),
                elapsed_sec=elapsed_sec,
                error_message=error_message,
            ),
            "cleanup_warnings": [],
            "v3_ingest": v3_ingest,
            "v3_compile": v3_compile,
            "v3_lint": dict(skipped),
            "v3_publish": dict(skipped),
            "v3_projection": dict(skipped),
        }

    compile_elapsed_sec = time.perf_counter() - compile_started
    snapshot_id = snapshot.snapshot_id
    page_count = len(snapshot.pages)
    common = {
        "scope": scope,
        "candidate_count": len(artifact_ids),
        "snapshot_id": snapshot_id,
        "page_count": page_count,
    }
    v3_compile = _v3_step_payload(
        **common,
        status="ok",
        elapsed_sec=compile_elapsed_sec,
    )
    lint_started = time.perf_counter()
    try:
        findings = lint_snapshot(
            snapshot,
            known_evidence_ids=repository.evidence_ids(),
        )
    except Exception as exc:
        warning_count = 0
        error_message = str(exc)
        v3_lint = _v3_step_payload(
            **common,
            status="error",
            elapsed_sec=time.perf_counter() - lint_started,
            error_message=error_message,
        )
        skipped = _v3_step_payload(
            **common,
            status="skipped",
            error_message="v3_lint_failed",
        )
        return {
            **_v3_step_payload(
                **common,
                status="error",
                elapsed_sec=time.perf_counter() - started,
                error_message=error_message,
            ),
            "cleanup_warnings": [],
            "v3_ingest": v3_ingest,
            "v3_compile": v3_compile,
            "v3_lint": v3_lint,
            "v3_publish": dict(skipped),
            "v3_projection": dict(skipped),
        }
    warning_count = sum(finding.severity == "warning" for finding in findings)
    error_count = sum(finding.severity == "error" for finding in findings)
    lint_error_message = "wiki_snapshot_lint_failed" if error_count else ""
    v3_lint = _v3_step_payload(
        **common,
        status="error" if error_count else "ok",
        warning_count=warning_count,
        elapsed_sec=time.perf_counter() - lint_started,
        error_message=lint_error_message,
    )
    v3_lint["error_count"] = error_count
    if error_count:
        skipped = _v3_step_payload(
            **common,
            status="skipped",
            warning_count=warning_count,
            error_message="v3_lint_failed",
        )
        return {
            **_v3_step_payload(
                **common,
                status="error",
                warning_count=warning_count,
                elapsed_sec=time.perf_counter() - started,
                error_message=lint_error_message,
            ),
            "cleanup_warnings": [],
            "v3_ingest": v3_ingest,
            "v3_compile": v3_compile,
            "v3_lint": v3_lint,
            "v3_publish": dict(skipped),
            "v3_projection": dict(skipped),
        }

    publish_started = time.perf_counter()
    try:
        published_snapshot = publisher.publish_snapshot(snapshot)
        if published_snapshot is not None:
            snapshot = published_snapshot
    except Exception as exc:
        error_message = str(exc)
        v3_publish = _v3_step_payload(
            **common,
            status="error",
            warning_count=warning_count,
            elapsed_sec=time.perf_counter() - publish_started,
            error_message=error_message,
        )
        skipped = _v3_step_payload(
            **common,
            status="skipped",
            warning_count=warning_count,
            error_message="v3_publish_failed",
        )
        return {
            **_v3_step_payload(
                **common,
                status="error",
                warning_count=warning_count,
                elapsed_sec=time.perf_counter() - started,
                error_message=error_message,
            ),
            "cleanup_warnings": [],
            "v3_ingest": v3_ingest,
            "v3_compile": v3_compile,
            "v3_lint": v3_lint,
            "v3_publish": v3_publish,
            "v3_projection": dict(skipped),
        }
    v3_publish = _v3_step_payload(
        **common,
        status="ok",
        warning_count=warning_count,
        elapsed_sec=time.perf_counter() - publish_started,
    )
    projection_started = time.perf_counter()
    try:
        projection = (
            projection_writer.project(snapshot)
            if projection_writer is not None
            else None
        )
        cleanup_warnings = list(
            getattr(projection, "cleanup_warnings", ()) if projection else ()
        )
        v3_projection = _v3_step_payload(
            **common,
            status="warning" if cleanup_warnings else "ok",
            warning_count=warning_count + len(cleanup_warnings),
            elapsed_sec=time.perf_counter() - projection_started,
        )
        v3_projection["cleanup_warnings"] = cleanup_warnings
    except Exception as exc:
        v3_projection = _v3_step_payload(
            **common,
            status="error",
            warning_count=warning_count,
            elapsed_sec=time.perf_counter() - projection_started,
            error_message=str(exc),
        )
        cleanup_warnings = []

    status = str(v3_projection["status"])
    return {
        **_v3_step_payload(
            **common,
            status=status,
            warning_count=warning_count + len(cleanup_warnings),
            elapsed_sec=time.perf_counter() - started,
            error_message=str(v3_projection["error_message"]),
        ),
        "cleanup_warnings": cleanup_warnings,
        "v3_ingest": v3_ingest,
        "v3_compile": v3_compile,
        "v3_lint": v3_lint,
        "v3_publish": v3_publish,
        "v3_projection": v3_projection,
    }


class _ReadOnlyNaverReportRepository(NaverReportRepository):
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        return self._connect_readonly()


class _BoundedSourceAdapter:
    def __init__(self, adapter: Any, symbols: tuple[str, ...]) -> None:
        self.adapter = adapter
        self.symbols = symbols[:V3_SOURCE_LIMIT]

    def collect(
        self,
        _symbols: tuple[str, ...],
        observed_at: str,
    ) -> tuple[Any, ...]:
        return self.adapter.collect(self.symbols, observed_at)


def _read_only_source_symbols(path: Path, *, scope: str) -> tuple[str, ...]:
    source_path = Path(path)
    if not source_path.exists():
        return ()
    uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        queries: list[str] = []
        if scope == "kis":
            if "report_symbol_links" in tables:
                queries.append(
                    "SELECT DISTINCT UPPER(TRIM(symbol)) AS symbol "
                    "FROM report_symbol_links "
                    "WHERE TRIM(COALESCE(symbol, '')) != '' ORDER BY 1"
                )
            if "reports" in tables:
                queries.append(
                    "SELECT DISTINCT UPPER(TRIM(symbol)) AS symbol FROM reports "
                    "WHERE TRIM(COALESCE(symbol, '')) != '' ORDER BY 1"
                )
        else:
            queries.extend(
                f"SELECT DISTINCT UPPER(TRIM(symbol)) AS symbol FROM {table} "
                "WHERE TRIM(COALESCE(symbol, '')) != '' ORDER BY 1"
                for table in (
                    "crypto_symbol_notes",
                    "crypto_candidates",
                    "crypto_features",
                )
                if table in tables
            )
        symbols: set[str] = set()
        for query in queries:
            for row in conn.execute(f"{query} LIMIT ?", (V3_SOURCE_LIMIT,)):
                symbol = str(row[0] or "").strip().upper()
                if symbol:
                    symbols.add(symbol)
                if len(symbols) >= V3_SOURCE_LIMIT:
                    break
            if len(symbols) >= V3_SOURCE_LIMIT:
                break
    return tuple(sorted(symbols))[:V3_SOURCE_LIMIT]


def _build_v3_scope_dependencies(
    service: JueWikiService,
    scope: str,
) -> tuple[tuple[Any, ...], Any | None, Any | None]:
    repository_factory = getattr(service, "repository", None)
    config = getattr(service, "config", None)
    if not callable(repository_factory) or config is None:
        return (), None, None
    repository = repository_factory()
    adapters: tuple[Any, ...] = ()
    if scope == "kis":
        source_path = getattr(config, "naver_reports_db_path", None)
        if source_path is not None and Path(source_path).exists():
            path = Path(source_path)
            adapters = (
                _BoundedSourceAdapter(
                    NaverWikiSourceAdapter(_ReadOnlyNaverReportRepository(path)),
                    _read_only_source_symbols(path, scope=scope),
                ),
            )
    elif scope == "binance":
        source_path = getattr(config, "crypto_market_research_db_path", None)
        if source_path is not None and Path(source_path).exists():
            path = Path(source_path)
            adapters = (
                _BoundedSourceAdapter(
                    CryptoWikiSourceAdapter(path, max_rows=V3_SOURCE_LIMIT),
                    _read_only_source_symbols(path, scope=scope),
                ),
            )
    configured_root = Path(config.root_path)
    projection_parent = configured_root / ".v3"
    configured_root.mkdir(parents=True, exist_ok=True)
    if projection_parent.is_symlink() or (
        projection_parent.exists() and not projection_parent.is_dir()
    ):
        raise ValueError("v3_projection_parent_must_be_owned_directory")
    projection_parent.mkdir(exist_ok=True)
    if projection_parent.is_symlink() or not projection_parent.is_dir():
        raise ValueError("v3_projection_parent_must_be_owned_directory")
    resolved_root = configured_root.resolve()
    resolved_parent = projection_parent.resolve()
    if not resolved_parent.is_relative_to(resolved_root):
        raise ValueError("v3_projection_parent_must_be_owned_directory")
    projection_root = projection_parent / scope
    return (
        adapters,
        JueWikiPublisherV1(repository),
        JueWikiProjectionWriter(
            projection_root,
            containment_root=configured_root,
        ),
    )


def _skipped_v3_scope(scope: str, reason: str) -> dict[str, Any]:
    step = _v3_step_payload(
        scope=scope,
        status="skipped",
        error_message=reason,
    )
    return {
        **step,
        "cleanup_warnings": [],
        "v3_ingest": dict(step),
        "v3_compile": dict(step),
        "v3_lint": dict(step),
        "v3_publish": dict(step),
        "v3_projection": dict(step),
    }


def _project_runner_status(
    service: JueWikiService,
    v3_results: dict[str, dict[str, Any]],
    *,
    active_read_mode: str,
    mode_eligibility: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    projector = service.project_status_snapshot
    if isinstance(service, JueWikiService):
        return projector(
            v3_run_results=v3_results,
            active_read_mode=active_read_mode,
            mode_eligibility=mode_eligibility,
        )
    return projector()


def _stored_mode_eligibility(
    application: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    recommendation_payload = (
        application.get("wiki_mode_recommendations")
        if isinstance(application.get("wiki_mode_recommendations"), dict)
        else {}
    )
    recommendations = recommendation_payload.get("recommendations")
    if not isinstance(recommendations, list):
        return None
    by_venue: dict[str, dict[str, Any]] = {}
    for raw_row in recommendations:
        if not isinstance(raw_row, dict):
            continue
        venue = str(raw_row.get("venue") or "").strip().lower()
        if venue not in {"kis", "binance"}:
            continue
        sample_count = raw_row.get("complete_sample_count")
        if sample_count is None:
            sample_count = raw_row.get("sample_count")
        blockers_value = raw_row.get("blockers")
        invalid = (
            raw_row.get("version") != "wiki_shadow_eligibility_v1"
            or type(raw_row.get("required_eligible")) is not bool
            or type(sample_count) is not int
            or sample_count < 0
            or not isinstance(blockers_value, list)
            or not str(raw_row.get("evaluated_at") or "").strip()
            or not str(raw_row.get("evaluated_through") or "").strip()
        )
        blockers = (
            [str(value) for value in blockers_value if str(value)]
            if isinstance(blockers_value, list)
            else []
        )
        if invalid and "eligibility_invalid" not in blockers:
            blockers.append("eligibility_invalid")
        if not invalid:
            freshness_reason = wiki_eligibility_freshness_reason(
                raw_row,
                now=datetime.now(timezone.utc),
            )
            if freshness_reason and freshness_reason not in blockers:
                blockers.append(freshness_reason)
        eligible = (
            not invalid
            and not blockers
            and sample_count >= 500
            and raw_row.get("required_eligible") is True
        )
        by_venue[venue] = {
            "version": str(raw_row.get("version") or ""),
            "venue": venue,
            "required_eligible": eligible,
            "complete_sample_count": (
                sample_count if type(sample_count) is int and sample_count >= 0 else 0
            ),
            "blockers": blockers,
            "reason": str(raw_row.get("reason") or ""),
            "evaluated_at": str(raw_row.get("evaluated_at") or ""),
            "evaluated_through": str(raw_row.get("evaluated_through") or ""),
        }
    return by_venue or None


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
    eligibility_db_path: str | Path | None = None,
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
    resolved_eligibility_db_path = _path_from(
        explicit=eligibility_db_path,
        settings=settings,
        setting_name="jue_wiki_shadow_db_path",
        default=str(Path.home() / ".tradecraft" / "jue_wiki_shadow.db"),
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
        application_service.shadow_eligibility_reader = JueWikiShadowStore(
            resolved_eligibility_db_path,
            completion_verifier=WikiCompletionSigner(
                Path(str(getattr(
                    settings,
                    "jue_wiki_provenance_key_path",
                    os.environ.get(
                        "TRADECRAFT_JUE_WIKI_PROVENANCE_KEY_PATH",
                        str(Path.home() / ".tradecraft" / "jue_wiki_provenance.key"),
                    ),
                )))
            ),
        )
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
                "outcome_archive": getattr(
                    application_service,
                    "archive_selection_outcomes",
                    lambda **_kwargs: {
                        "status": "skipped",
                        "reason": "outcome_archive_unavailable",
                    },
                )(
                    retention_days=30,
                    dry_run=False,
                ),
                "mode_recommendations": application_service.project_mode_recommendations(
                    min_samples=mode_recommendation_min_samples,
                    current_modes={},
                ),
                "wiki_mode_recommendations": getattr(
                    application_service,
                    "project_wiki_mode_recommendations",
                    lambda: {
                        "status": "unavailable",
                        "read_only": True,
                        "recommendations": [],
                    },
                )(),
                **getattr(
                    application_service,
                    "project_status_snapshot",
                    application_service.status,
                )(),
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
    selection_audit_compaction = _run_step(
        "selection_audit_compaction",
        lambda: _selection_audit_compaction_payload(
            service,
            cutoff=datetime.now(timezone.utc) - timedelta(hours=24),
        ),
    )
    v3_results: dict[str, dict[str, Any]] = {}
    for scope in V3_SCOPES:
        try:
            adapters, publisher, projection_writer = _build_v3_scope_dependencies(
                service,
                scope,
            )
            if publisher is None:
                v3_results[scope] = _skipped_v3_scope(
                    scope,
                    "v3_repository_unavailable",
                )
            else:
                v3_results[scope] = run_v3_scope(
                    service=service,
                    scope=scope,
                    adapters=adapters,
                    publisher=publisher,
                    projection_writer=projection_writer,
                )
        except Exception as exc:
            logger.exception("jue wiki V3 %s scope failed: %s", scope, exc)
            v3_results[scope] = _v3_setup_failure(
                scope=scope,
                error=exc,
            )
    ops_snapshot = _run_step(
        "ops_snapshot",
        lambda: _project_runner_status(
            service,
            v3_results,
            active_read_mode=str(
                getattr(settings, "jue_wiki_read_mode", "shadow") or "shadow"
            ).strip().lower(),
            mode_eligibility=_stored_mode_eligibility(application),
        ),
    )
    steps = [
        rebuild,
        lint,
        repair,
        playbooks,
        performance,
        application,
        repair_finalization,
        selection_audit_compaction,
        *v3_results.values(),
        ops_snapshot,
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
        "ops_snapshot": ops_snapshot,
        "repair_finalization": repair_finalization,
        "selection_audit_compaction": selection_audit_compaction,
        "v3": v3_results,
        "v3_ingest": {
            scope: payload["v3_ingest"] for scope, payload in v3_results.items()
        },
        "v3_compile": {
            scope: payload["v3_compile"] for scope, payload in v3_results.items()
        },
        "v3_lint": {
            scope: payload["v3_lint"] for scope, payload in v3_results.items()
        },
        "v3_publish": {
            scope: payload["v3_publish"] for scope, payload in v3_results.items()
        },
        "v3_projection": {
            scope: payload["v3_projection"] for scope, payload in v3_results.items()
        },
    }
    _write_state(state_path, result)
    return result


def _selection_audit_compaction_payload(
    service: JueWikiService,
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    store_factory = getattr(service, "selection_audit_store", None)
    if not callable(store_factory):
        return {
            "status": "skipped",
            "reason": "selection_audit_store_unavailable",
            "verified": False,
            "exported_count": 0,
            "deleted_count": 0,
            "entry_ids": [],
        }
    store = store_factory()
    result = store.compact_rejected(
        cutoff=cutoff,
        apply=True,
    )
    vacuum = getattr(store, "vacuum_hot_database", None)
    database_compaction = (
        vacuum()
        if int(getattr(result, "deleted_count", len(result.deleted_keys))) > 0
        and callable(vacuum)
        else {
            "status": "skipped",
            "reason": "no_deleted_selection_rows",
            "backup_verified": False,
            "reclaimed_bytes": 0,
        }
    )
    entry_ids = list(result.entry_ids)
    cold_archive_verification = _persist_cold_archive_verification(
        service,
        archive_changed=bool(entry_ids),
    )
    verification_status = str(
        cold_archive_verification.get("status") or ""
    ).lower()
    verification_snapshot = cold_archive_verification.get(
        "verification_snapshot"
    )
    snapshot_status = (
        str(verification_snapshot.get("status") or "").lower()
        if isinstance(verification_snapshot, dict)
        else ""
    )
    compaction_status = "ok"
    if entry_ids and not bool(result.verified):
        compaction_status = "warning"
    elif verification_status not in {"ok", "skipped"}:
        compaction_status = "warning"
    elif verification_status == "ok" and snapshot_status != "current":
        compaction_status = "warning"
    return {
        "status": compaction_status,
        "verified": bool(result.verified),
        "exported_count": int(
            getattr(result, "exported_count", len(result.exported_keys))
        ),
        "deleted_count": int(
            getattr(result, "deleted_count", len(result.deleted_keys))
        ),
        "entry_ids": entry_ids,
        "database_compaction": database_compaction,
        "cold_archive_verification": cold_archive_verification,
    }


def _persist_cold_archive_verification(
    service: JueWikiService,
    *,
    archive_changed: bool,
) -> dict[str, Any]:
    if not archive_changed:
        return {
            "status": "skipped",
            "reason": "archive_unchanged",
            "verification_snapshot": {"status": "not_required"},
        }
    config = getattr(service, "config", None)
    root = getattr(config, "cold_archive_root", None)
    db_path = getattr(config, "db_path", None)
    if root is None or db_path is None:
        return {
            "status": "warning",
            "reason": "cold_archive_verification_paths_unavailable",
            "verification_snapshot": {"status": "missing"},
        }
    payload = persist_runtime_cold_archive_status(
        root=Path(root),
        jue_wiki_db_path=Path(db_path),
    )
    return {
        key: payload[key]
        for key in (
            "status",
            "entry_count",
            "archive_bytes",
            "corrupt_entry_ids",
            "verification_snapshot",
            "verified_at",
        )
        if key in payload
    }


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
            repair_overdue_sec=resolved_settings.jue_wiki_repair_overdue_sec,
            repair_stall_sec=resolved_settings.jue_wiki_repair_stall_sec,
            repair_growth_window_sec=(
                resolved_settings.jue_wiki_repair_growth_window_sec
            ),
            repair_growth_warn_count=(
                resolved_settings.jue_wiki_repair_growth_warn_count
            ),
            cold_archive_root=Path(resolved_settings.runtime_cold_archive_root),
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
