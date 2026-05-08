from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore


def _reports_ui_dist_index() -> Path:
    return Path(__file__).resolve().parent / "web_dist" / "index.html"


def _parse_datetime(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale(raw: str, *, stale_after_sec: int) -> bool:
    parsed = _parse_datetime(raw)
    if parsed is None:
        return True
    age_sec = (datetime.now(timezone.utc) - parsed).total_seconds()
    return age_sec >= max(int(stale_after_sec), 1)


def worker_state_store(settings: AppSettings) -> RuntimeStateStore:
    return RuntimeStateStore(settings.reports_worker_state_path)


def read_worker_state(settings: AppSettings) -> dict[str, Any] | None:
    return worker_state_store(settings).read_snapshot()


def worker_stale_after_sec(settings: AppSettings) -> int:
    interval = max(int(settings.naver_reports_interval_sec), 300)
    return max(interval * 2, 900)


def build_worker_health_payload(settings: AppSettings) -> dict[str, Any]:
    state = read_worker_state(settings)
    stale_after_sec = worker_stale_after_sec(settings)

    if not settings.naver_reports_enabled:
        return {
            "status": "disabled",
            "stale_after_sec": stale_after_sec,
        }
    if state is None:
        return {
            "status": "missing",
            "stale_after_sec": stale_after_sec,
        }

    updated_at = str(state.get("updated_at") or "")
    raw_status = str(state.get("status") or "").strip().lower() or "unknown"
    status = raw_status
    if raw_status != "disabled" and _is_stale(updated_at, stale_after_sec=stale_after_sec):
        status = "stale"

    payload = {
        "status": status,
        "cycle": int(state.get("cycle") or 0),
        "interval_sec": int(state.get("interval_sec") or 0),
        "updated_at": updated_at,
        "last_success_at": str(state.get("last_success_at") or ""),
        "last_error_at": str(state.get("last_error_at") or ""),
        "last_error": str(state.get("last_error") or ""),
        "stale_after_sec": stale_after_sec,
    }
    snapshot = state.get("snapshot")
    if isinstance(snapshot, dict):
        payload["snapshot"] = snapshot
    symbol_refresh = state.get("symbol_refresh")
    if isinstance(symbol_refresh, dict) and symbol_refresh:
        payload["symbol_refresh"] = symbol_refresh
    rag_sync = state.get("rag_sync")
    if isinstance(rag_sync, dict) and rag_sync:
        payload["rag_sync"] = rag_sync
    return payload


def build_data_quality_payload(
    settings: AppSettings,
    repository_status: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    total_reports = int(repository_status.get("total_reports") or 0)
    total_symbols = int(repository_status.get("total_symbols") or 0)
    last_updated_at = str(repository_status.get("last_updated_at") or "")
    symbol_last_updated_at = str(repository_status.get("symbol_last_updated_at") or "")
    raw_metrics = dict(repository_status.get("quality") or {})

    if total_reports <= 0:
        issues.append(
            {
                "level": "error",
                "code": "report_dataset_empty",
                "count": 0,
                "detail": "no reports are indexed yet",
            }
        )
    elif _is_stale(last_updated_at, stale_after_sec=worker_stale_after_sec(settings)):
        issues.append(
            {
                "level": "warn",
                "code": "report_ingest_stale",
                "count": 1,
                "detail": f"latest report update is stale: {last_updated_at or 'missing'}",
            }
        )

    if total_symbols <= 0:
        issues.append(
            {
                "level": "error",
                "code": "symbol_directory_empty",
                "count": 0,
                "detail": "symbol directory is empty",
            }
        )
    elif _is_stale(symbol_last_updated_at, stale_after_sec=12 * 3600):
        issues.append(
            {
                "level": "warn",
                "code": "symbol_directory_stale",
                "count": 1,
                "detail": f"symbol directory is stale: {symbol_last_updated_at or 'missing'}",
            }
        )

    metric_defs = (
        ("missing_company_name_count", "warn", "missing_company_name", "reports missing company_name"),
        ("html_company_name_count", "warn", "html_company_name", "reports still contain HTML-tainted company names"),
        ("missing_symbol_count", "warn", "missing_symbol", "reports missing symbol mapping"),
        ("missing_broker_count", "warn", "missing_broker", "reports missing broker"),
        ("missing_analyst_count", "warn", "missing_analyst", "reports missing analyst"),
        ("unknown_category_count", "warn", "unknown_category", "reports still have unknown category"),
        (
            "symbol_directory_drift_count",
            "warn",
            "symbol_directory_drift",
            "report symbol rows disagree with the symbol directory",
        ),
    )

    normalized_metrics: dict[str, int] = {}
    for key, level, code, detail in metric_defs:
        count = int(raw_metrics.get(key) or 0)
        normalized_metrics[key] = count
        if count > 0:
            issues.append(
                {
                    "level": level,
                    "code": code,
                    "count": count,
                    "detail": detail,
                }
            )

    status = "ok"
    if any(item["level"] == "error" for item in issues):
        status = "error"
    elif issues:
        status = "warn"

    return {
        "status": status,
        "issues": issues,
        "metrics": {
            "total_reports": total_reports,
            "total_symbols": total_symbols,
            "last_updated_at": last_updated_at,
            "symbol_last_updated_at": symbol_last_updated_at,
            **normalized_metrics,
        },
    }


def build_deployment_checks(
    settings: AppSettings,
    *,
    require_worker: bool,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    token_count = len(settings.reports_api_token_list)

    if token_count == 0:
        issues.append(
            {
                "level": "error",
                "code": "api_token_missing",
                "detail": "configure TRADECRAFT_REPORTS_API_TOKEN or TRADECRAFT_REPORTS_API_TOKENS",
            }
        )
    elif token_count == 1:
        issues.append(
            {
                "level": "warn",
                "code": "single_api_token",
                "detail": "configure TRADECRAFT_REPORTS_API_TOKENS with overlapping tokens before rotation",
            }
        )

    if require_worker and not settings.naver_reports_enabled:
        issues.append(
            {
                "level": "error",
                "code": "worker_disabled",
                "detail": "TRADECRAFT_NAVER_REPORTS_ENABLED must be true for the reports stack",
            }
        )

    if settings.naver_reports_llm_facts_enabled and not settings.llm_bridge_ready:
        issues.append(
            {
                "level": "warn",
                "code": "llm_facts_bridge_missing",
                "detail": "TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED=true requires TRADECRAFT_LLM_BRIDGE_COMMAND or TRADECRAFT_LLM_BRIDGE_URL",
            }
        )

    if not settings.reports_ui_allowed_cidr_list:
        issues.append(
            {
                "level": "error",
                "code": "ui_allowlist_empty",
                "detail": "configure TRADECRAFT_REPORTS_UI_ALLOWED_CIDRS with at least one CIDR",
            }
        )

    if not settings.naver_reports_seed_url_list:
        issues.append(
            {
                "level": "error",
                "code": "seed_urls_missing",
                "detail": "configure at least one TRADECRAFT_NAVER_REPORTS_SEED_URL or TRADECRAFT_NAVER_REPORTS_SEED_URLS entry",
            }
        )

    db_parent = Path(settings.naver_reports_db_path).expanduser().parent
    if not db_parent.exists():
        issues.append(
            {
                "level": "warn",
                "code": "db_parent_missing",
                "detail": f"database parent directory does not exist yet: {db_parent}",
            }
        )

    if not _reports_ui_dist_index().exists():
        issues.append(
            {
                "level": "warn",
                "code": "ui_build_missing",
                "detail": "reports console build is missing; run tradecraft-reports-stack or npm run build",
            }
        )

    status = "ok"
    if any(item["level"] == "error" for item in issues):
        status = "error"
    elif issues:
        status = "warn"

    return {
        "status": status,
        "token_count": token_count,
        "issues": issues,
    }
