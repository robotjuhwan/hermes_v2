from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from tradecraft.services.binance import STABLE_USD_ASSETS
from tradecraft.services.market import recalculate_dashboard_totals, upsert_venue_assets

KIS_PRIMARY_LABEL = "국장1"
KIS_SECONDARY_LABEL = "국장2"


@dataclass
class DashboardPayloadCache:
    upbit_cache_key: tuple[Any, ...] | None = None
    upbit_assets: list[dict[str, Any]] | None = None
    upbit_fetched_at: datetime | None = None
    bithumb_cache_key: tuple[Any, ...] | None = None
    bithumb_assets: list[dict[str, Any]] | None = None
    bithumb_fetched_at: datetime | None = None
    binance_spot_cache_key: tuple[Any, ...] | None = None
    binance_spot_assets: list[dict[str, Any]] | None = None
    binance_spot_fetched_at: datetime | None = None
    binance_futures_cache_key: tuple[Any, ...] | None = None
    binance_futures_assets: list[dict[str, Any]] | None = None
    binance_futures_fetched_at: datetime | None = None
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    kis_primary_cache_key: tuple[Any, ...] | None = None
    kis_primary_assets: list[dict[str, Any]] | None = None
    kis_primary_fetched_at: datetime | None = None
    kis_primary_error_at: datetime | None = None
    kis_primary_error_message: str = ""
    kis_primary_us_cache_key: tuple[Any, ...] | None = None
    kis_primary_us_assets: list[dict[str, Any]] | None = None
    kis_primary_us_fetched_at: datetime | None = None
    kis_primary_us_error_at: datetime | None = None
    kis_primary_us_error_message: str = ""
    kis_secondary_cache_key: tuple[Any, ...] | None = None
    kis_secondary_assets: list[dict[str, Any]] | None = None
    kis_secondary_fetched_at: datetime | None = None
    kis_secondary_error_at: datetime | None = None
    kis_secondary_error_message: str = ""
    disk_cache_loaded: bool = False


@dataclass(frozen=True)
class DashboardPayloadDeps:
    settings: Any
    fx_rates: Any
    upbit: Any
    bithumb: Any
    binance: Any
    kis_primary: Any
    kis_secondary: Any
    runtime_reader: Any
    research_reader: Any
    telegram: Any
    dashboard_template: Callable[[], dict[str, Any]]
    replace_venue_assets: Callable[[dict[str, Any], str, list[dict[str, Any]]], bool]
    upsert_venue_assets: Callable[..., None]
    logger: Any
    research_status_provider: Callable[[], dict[str, Any]] | None = None


def safe_positive_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        out = float(value)
        return out if out > 0 else 0.0
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        out = float(text)
    except ValueError:
        return 0.0
    return out if out > 0 else 0.0


def _parse_cached_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _await_balance_fetch(
    awaitable: Awaitable[list[dict[str, Any]]],
    *,
    timeout_sec: float,
    label: str,
) -> list[dict[str, Any]]:
    if timeout_sec <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"{label} balance fetch timed out after {timeout_sec:g}s"
        ) from exc


def _cached_datetime_iso(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat() if value is not None else ""


def _dashboard_payload_cache_path(settings: Any) -> Path:
    explicit = str(getattr(settings, "dashboard_payload_cache_path", "") or "").strip()
    if explicit:
        return Path(explicit)
    runtime_state_path = str(getattr(settings, "runtime_state_path", "") or "").strip()
    runtime_dir = Path(runtime_state_path).parent if runtime_state_path else Path(".runtime")
    return runtime_dir / "dashboard_payload_cache.json"


def _dashboard_payload_disk_cache_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "dashboard_payload_disk_cache_enabled", False))


def _research_status_summary_from_reports(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    report_count = int(payload.get("report_count") or 0)
    rag_count = int(payload.get("rag_count") or 0)
    fundamentals_symbol_count = int(payload.get("fundamentals_symbol_count") or 0)
    active = (
        bool(payload.get("enabled"))
        or report_count > 0
        or rag_count > 0
        or fundamentals_symbol_count > 0
    )
    if not active:
        return None

    intelligence = payload.get("intelligence")
    intelligence = intelligence if isinstance(intelligence, dict) else {}
    codex_runtime = intelligence.get("codex_runtime")
    codex_runtime = codex_runtime if isinstance(codex_runtime, dict) else {}
    latest_report_at = str(payload.get("latest_report_at") or "").strip()
    latest_published_at = str(payload.get("latest_published_at") or "").strip()
    return {
        "updated_at": latest_report_at or latest_published_at or None,
        "source": "reports_rag",
        "query": "general",
        "status": str(payload.get("status") or "ok"),
        "count": report_count,
        "items": [],
        "stale": False,
        "report_count": report_count,
        "latest_report_at": latest_report_at,
        "latest_published_at": latest_published_at,
        "symbol_count": int(payload.get("symbol_count") or 0),
        "symbol_link_count": int(payload.get("symbol_link_count") or 0),
        "rag_available": bool(payload.get("rag_available")),
        "rag_count": rag_count,
        "fundamentals_symbol_count": fundamentals_symbol_count,
        "fundamentals_stale_ratio": float(
            payload.get("fundamentals_stale_ratio") or 0.0
        ),
        "fundamentals_latest_symbols_stale_ratio": float(
            payload.get("fundamentals_latest_symbols_stale_ratio") or 0.0
        ),
        "model": str(codex_runtime.get("model") or ""),
        "reasoning_effort": str(codex_runtime.get("reasoning_effort") or ""),
    }


def _dashboard_cache_identity(settings: Any, attr: str) -> str:
    fields_by_attr = {
        "upbit": ("upbit_access_key",),
        "bithumb": ("bithumb_access_key",),
        "binance_spot": ("binance_spot_api_key",),
        "binance_futures": ("binance_futures_key_resolved", "binance_futures_api_key"),
        "kis_primary": (
            "kis_primary_app_key",
            "kis_primary_account_no",
            "kis_primary_product_code",
        ),
        "kis_primary_us": (
            "kis_primary_app_key",
            "kis_primary_account_no",
            "kis_primary_product_code",
        ),
        "kis_secondary": (
            "kis_secondary_app_key",
            "kis_secondary_account_no",
            "kis_secondary_product_code",
        ),
    }
    fields = fields_by_attr.get(attr, ())
    parts = [attr]
    for setting_name in fields:
        value = str(getattr(settings, setting_name, "") or "").strip()
        if value:
            parts.append(
                f"{setting_name}=set:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
            )
            continue
        parts.append(f"{setting_name}=unset")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _cached_asset_rows(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    rows = [dict(row) for row in value if isinstance(row, dict)]
    return rows


def hydrate_dashboard_payload_cache_from_disk(
    settings: Any,
    cache: DashboardPayloadCache,
) -> bool:
    if not _dashboard_payload_disk_cache_enabled(settings) or cache.disk_cache_loaded:
        return False
    cache.disk_cache_loaded = True
    path = _dashboard_payload_cache_path(settings)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or int(payload.get("version") or 0) not in {1, 2}:
        return False
    venues = payload.get("venues")
    if not isinstance(venues, dict):
        return False

    hydrated = False
    for attr, row in (
        ("upbit", venues.get("upbit")),
        ("bithumb", venues.get("bithumb")),
        ("binance_spot", venues.get("binance_spot")),
        ("binance_futures", venues.get("binance_futures")),
        ("kis_primary", venues.get("kis_primary")),
        ("kis_primary_us", venues.get("kis_primary_us")),
        ("kis_secondary", venues.get("kis_secondary")),
    ):
        if not isinstance(row, dict):
            continue
        if str(row.get("cache_identity") or "") != _dashboard_cache_identity(
            settings,
            attr,
        ):
            continue
        assets = _cached_asset_rows(row.get("assets"))
        fetched_at = _parse_cached_datetime(row.get("fetched_at"))
        if assets is not None and fetched_at is not None:
            setattr(cache, f"{attr}_assets", assets)
            setattr(cache, f"{attr}_fetched_at", fetched_at)
            hydrated = True
        error_at = _parse_cached_datetime(row.get("error_at"))
        error_message = str(row.get("error_message") or "")
        if hasattr(cache, f"{attr}_error_at"):
            setattr(cache, f"{attr}_error_at", error_at)
        if hasattr(cache, f"{attr}_error_message"):
            setattr(cache, f"{attr}_error_message", error_message)
    return hydrated


def persist_dashboard_payload_cache_to_disk(
    settings: Any,
    cache: DashboardPayloadCache,
) -> None:
    if not _dashboard_payload_disk_cache_enabled(settings):
        return
    path = _dashboard_payload_cache_path(settings)
    venues: dict[str, dict[str, Any]] = {}
    for attr in (
        "upbit",
        "bithumb",
        "binance_spot",
        "binance_futures",
        "kis_primary",
        "kis_primary_us",
        "kis_secondary",
    ):
        assets = getattr(cache, f"{attr}_assets", None)
        fetched_at = getattr(cache, f"{attr}_fetched_at", None)
        if assets is None and fetched_at is None:
            continue
        row = {
            "assets": assets if isinstance(assets, list) else [],
            "cache_identity": _dashboard_cache_identity(settings, attr),
            "fetched_at": _cached_datetime_iso(fetched_at),
        }
        error_at = getattr(cache, f"{attr}_error_at", None)
        error_message = getattr(cache, f"{attr}_error_message", "")
        if error_at is not None or error_message:
            row["error_at"] = _cached_datetime_iso(error_at)
            row["error_message"] = str(error_message or "")
        venues[attr] = row
    if not venues:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "venues": venues,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        tmp_path.replace(path)
    except (OSError, TypeError, ValueError):
        return


def is_fx_source_degraded(source: str) -> bool:
    normalized = source.strip().lower()
    if not normalized:
        return True
    return normalized.startswith("fallback") or normalized.endswith("_proxy")


def apply_dashboard_fx_rates(
    dashboard: dict[str, Any],
    *,
    usdt_krw: float,
    usd_krw: float,
) -> None:
    stable_assets = set(STABLE_USD_ASSETS)
    changed = False
    for venue in dashboard.get("venues", []):
        venue_id = str(venue.get("id") or "")
        for asset in venue.get("assets", []):
            if not isinstance(asset, dict):
                continue
            if str(asset.get("kind") or "") != "cash":
                continue

            code = str(asset.get("asset") or "").upper().strip()
            qty = safe_positive_float(asset.get("qty"))
            if qty <= 0:
                continue

            target_rate = 0.0
            if code == "USD" and venue_id == "us_stock":
                target_rate = safe_positive_float(usd_krw)
            elif code in stable_assets:
                target_rate = safe_positive_float(usdt_krw)
            elif code.endswith("-FUT") and code.removesuffix("-FUT") in stable_assets:
                target_rate = safe_positive_float(usdt_krw)

            if target_rate <= 0:
                continue

            asset["avg_price"] = target_rate
            asset["mark_price"] = target_rate
            asset["value_krw"] = qty * target_rate
            changed = True

    if changed:
        recalculate_dashboard_totals(dashboard)


def mark_venue_unavailable(
    dashboard: dict[str, Any],
    *,
    venue_id: str,
    label: str,
    market: str,
    status: str,
    event_type: str,
    message: str,
    error_message: str = "",
) -> None:
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["label"] = venue.get("label") or label
        venue["market"] = venue.get("market") or market
        venue["assets"] = []
        venue["status"] = status
        venue["error_message"] = error_message
        recalculate_dashboard_totals(dashboard)
        break
    else:
        upsert_venue_assets(
            dashboard,
            venue_id=venue_id,
            label=label,
            market=market,
            assets=[],
        )
        for venue in dashboard.get("venues", []):
            if venue.get("id") == venue_id:
                venue["status"] = status
                venue["error_message"] = error_message
                break
    dashboard["events"].append({"type": event_type, "message": message})


def mark_venue_stale_with_cached_assets(
    dashboard: dict[str, Any],
    *,
    venue_id: str,
    label: str,
    market: str,
    cached_assets: list[dict[str, Any]] | None,
    event_type: str,
    message: str,
    error_message: str = "",
    cached_at: datetime | None = None,
) -> bool:
    if cached_assets is None:
        return False
    upsert_venue_assets(
        dashboard,
        venue_id=venue_id,
        label=label,
        market=market,
        assets=[dict(asset) for asset in cached_assets],
    )
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["status"] = "stale"
        venue["cache_status"] = "stale"
        if cached_at is not None:
            venue["cached_at"] = cached_at.isoformat()
        venue["error_message"] = error_message
        break
    dashboard["events"].append({"type": event_type, "message": message})
    return True


def mark_venue_fresh_with_cached_assets(
    dashboard: dict[str, Any],
    *,
    venue_id: str,
    label: str,
    market: str,
    cached_assets: list[dict[str, Any]] | None,
    fetched_at: datetime | None,
    ttl_sec: int,
    now: datetime,
    event_type: str,
    message: str,
) -> bool:
    if cached_assets is None or not _cached_assets_are_fresh(
        fetched_at,
        ttl_sec=ttl_sec,
        now=now,
    ):
        return False
    upsert_venue_assets(
        dashboard,
        venue_id=venue_id,
        label=label,
        market=market,
        assets=[dict(asset) for asset in cached_assets],
    )
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["cache_status"] = "fresh"
        venue["cached_at"] = fetched_at.isoformat() if fetched_at else ""
        break
    dashboard["events"].append({"type": event_type, "message": message})
    return True


def _cached_assets_are_fresh(
    fetched_at: datetime | None,
    *,
    ttl_sec: int,
    now: datetime,
) -> bool:
    if ttl_sec <= 0 or fetched_at is None:
        return False
    return now - fetched_at <= timedelta(seconds=ttl_sec)


def _kis_cached_assets_missing_positions(
    assets: list[dict[str, Any]] | None,
) -> bool:
    if not assets:
        return False
    has_position = any(
        str(row.get("kind") or "").lower() != "cash"
        and (
            safe_positive_float(row.get("qty")) > 0
            or safe_positive_float(row.get("value_krw")) > 0
        )
        for row in assets
        if isinstance(row, dict)
    )
    if has_position:
        return False

    cash_rows = [
        row
        for row in assets
        if isinstance(row, dict) and str(row.get("kind") or "").lower() == "cash"
    ]
    if not cash_rows:
        return False

    visible_value = sum(safe_positive_float(row.get("value_krw")) for row in assets)
    receivable_value = sum(
        safe_positive_float(row.get("receivable_cash_krw")) for row in cash_rows
    )
    broker_total = max(
        safe_positive_float(row.get(key))
        for row in cash_rows
        for key in (
            "net_asset_krw",
            "broker_total_value_krw",
            "total_value_krw",
            "total_asset_krw",
        )
    )
    if broker_total <= 0:
        return False

    unexplained_gap = broker_total - visible_value - receivable_value
    return unexplained_gap > max(1_000.0, broker_total * 0.001)


def _usable_kis_cached_assets(
    assets: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if _kis_cached_assets_missing_positions(assets):
        return None
    return assets


def mark_venue_stale_display_cache(
    dashboard: dict[str, Any],
    *,
    venue_id: str,
    label: str,
    market: str,
    cached_assets: list[dict[str, Any]] | None,
    fetched_at: datetime | None,
    stale_ttl_sec: int,
    now: datetime,
    event_type: str,
    message: str,
) -> bool:
    if cached_assets is None or not _cached_assets_are_fresh(
        fetched_at,
        ttl_sec=stale_ttl_sec,
        now=now,
    ):
        return False
    return mark_venue_stale_with_cached_assets(
        dashboard,
        venue_id=venue_id,
        label=label,
        market=market,
        cached_assets=cached_assets,
        event_type=event_type,
        message=message,
        cached_at=fetched_at,
    )


def _error_is_in_cooldown(
    error_at: datetime | None,
    *,
    cooldown_sec: int,
    now: datetime,
) -> bool:
    if cooldown_sec <= 0 or error_at is None:
        return False
    return now - error_at <= timedelta(seconds=cooldown_sec)


def _kis_balance_fetch_needed(
    *,
    cached_assets: list[dict[str, Any]] | None,
    fetched_at: datetime | None,
    error_at: datetime | None,
    ttl_sec: int,
    cooldown_sec: int,
    now: datetime,
) -> bool:
    if cached_assets is not None and _cached_assets_are_fresh(
        fetched_at,
        ttl_sec=ttl_sec,
        now=now,
    ):
        return False
    if _error_is_in_cooldown(error_at, cooldown_sec=cooldown_sec, now=now):
        return False
    return True


def _callable_cache_identity(func: Any) -> tuple[Any, ...]:
    owner = getattr(func, "__self__", None)
    inner = getattr(func, "__func__", None)
    if owner is not None and inner is not None:
        return ("bound", id(owner), id(inner))
    return ("callable", id(func))


def _setting_fingerprint(value: Any) -> tuple[str, int]:
    text = str(value or "")
    return ("set", hash(text)) if text else ("unset", 0)


def _exchange_cache_key(
    settings: Any,
    *,
    prefix: str,
    fetch_func: Any,
    credential_fields: tuple[str, ...],
) -> tuple[Any, ...]:
    return (
        prefix,
        tuple(
            (field, _setting_fingerprint(getattr(settings, field, "")))
            for field in credential_fields
        ),
        _callable_cache_identity(fetch_func),
    )


def _kis_cache_key(
    settings: Any,
    *,
    prefix: str,
    fetch_func: Any,
) -> tuple[Any, ...]:
    return (
        prefix,
        str(getattr(settings, f"{prefix}_app_key", "") or ""),
        str(getattr(settings, f"{prefix}_account_no", "") or ""),
        str(getattr(settings, f"{prefix}_product_code", "") or ""),
        _callable_cache_identity(fetch_func),
    )


def _optional_method(obj: Any, name: str) -> Any:
    return getattr(obj, name, None) if obj is not None else None


def _reset_simple_venue_cache(cache: DashboardPayloadCache, attr: str) -> None:
    setattr(cache, f"{attr}_assets", None)
    setattr(cache, f"{attr}_fetched_at", None)


def _reset_kis_primary_cache(cache: DashboardPayloadCache) -> None:
    cache.kis_primary_assets = None
    cache.kis_primary_fetched_at = None
    cache.kis_primary_error_at = None
    cache.kis_primary_error_message = ""


def _reset_kis_primary_us_cache(cache: DashboardPayloadCache) -> None:
    cache.kis_primary_us_assets = None
    cache.kis_primary_us_fetched_at = None
    cache.kis_primary_us_error_at = None
    cache.kis_primary_us_error_message = ""


def _reset_kis_secondary_cache(cache: DashboardPayloadCache) -> None:
    cache.kis_secondary_assets = None
    cache.kis_secondary_fetched_at = None
    cache.kis_secondary_error_at = None
    cache.kis_secondary_error_message = ""


def _cache_key_changed(
    cache: DashboardPayloadCache,
    attr: str,
    new_key: tuple[Any, ...],
) -> bool:
    key_attr = f"{attr}_cache_key"
    old_key = getattr(cache, key_attr)
    if old_key is None:
        setattr(cache, key_attr, new_key)
        return False
    if old_key == new_key:
        return False
    setattr(cache, key_attr, new_key)
    return True


def _refresh_kis_cache_keys(
    settings: Any,
    cache: DashboardPayloadCache,
    *,
    kis_primary: Any,
    kis_secondary: Any,
) -> None:
    primary_key = _kis_cache_key(
        settings,
        prefix="kis_primary",
        fetch_func=_optional_method(kis_primary, "fetch_balance_assets"),
    )
    if _cache_key_changed(cache, "kis_primary", primary_key):
        _reset_kis_primary_cache(cache)

    primary_us_key = _kis_cache_key(
        settings,
        prefix="kis_primary",
        fetch_func=_optional_method(kis_primary, "fetch_us_balance_assets"),
    )
    if _cache_key_changed(cache, "kis_primary_us", primary_us_key):
        _reset_kis_primary_us_cache(cache)

    secondary_key = _kis_cache_key(
        settings,
        prefix="kis_secondary",
        fetch_func=_optional_method(kis_secondary, "fetch_balance_assets"),
    )
    if _cache_key_changed(cache, "kis_secondary", secondary_key):
        _reset_kis_secondary_cache(cache)


def _refresh_crypto_cache_keys(
    settings: Any,
    cache: DashboardPayloadCache,
    *,
    upbit: Any,
    bithumb: Any,
    binance: Any,
) -> None:
    upbit_key = _exchange_cache_key(
        settings,
        prefix="upbit",
        fetch_func=_optional_method(upbit, "fetch_balance_assets"),
        credential_fields=("upbit_access_key", "upbit_secret_key"),
    )
    if _cache_key_changed(cache, "upbit", upbit_key):
        _reset_simple_venue_cache(cache, "upbit")

    bithumb_key = _exchange_cache_key(
        settings,
        prefix="bithumb",
        fetch_func=_optional_method(bithumb, "fetch_balance_assets"),
        credential_fields=("bithumb_access_key", "bithumb_secret_key"),
    )
    if _cache_key_changed(cache, "bithumb", bithumb_key):
        _reset_simple_venue_cache(cache, "bithumb")

    binance_spot_key = _exchange_cache_key(
        settings,
        prefix="binance_spot",
        fetch_func=_optional_method(binance, "fetch_spot_assets"),
        credential_fields=("binance_spot_api_key", "binance_spot_api_secret"),
    )
    if _cache_key_changed(cache, "binance_spot", binance_spot_key):
        _reset_simple_venue_cache(cache, "binance_spot")

    binance_futures_key = _exchange_cache_key(
        settings,
        prefix="binance_futures",
        fetch_func=_optional_method(binance, "fetch_futures_assets"),
        credential_fields=(
            "binance_futures_key_resolved",
            "binance_futures_secret_resolved",
        ),
    )
    if _cache_key_changed(cache, "binance_futures", binance_futures_key):
        _reset_simple_venue_cache(cache, "binance_futures")


def _refresh_dashboard_cache_keys(
    settings: Any,
    cache: DashboardPayloadCache,
    *,
    upbit: Any,
    bithumb: Any,
    binance: Any,
    kis_primary: Any,
    kis_secondary: Any,
) -> None:
    _refresh_crypto_cache_keys(
        settings,
        cache,
        upbit=upbit,
        bithumb=bithumb,
        binance=binance,
    )
    _refresh_kis_cache_keys(
        settings,
        cache,
        kis_primary=kis_primary,
        kis_secondary=kis_secondary,
    )


def _apply_telegram_status(data: dict[str, Any], status: dict[str, Any]) -> None:
    data["telegram"] = status
    if not bool(status.get("ready")):
        return
    events = [
        row
        for row in list(data.get("events") or [])
        if not (
            isinstance(row, dict)
            and row.get("type") == "telegram"
            and "연동 대기" in str(row.get("message") or "")
        )
    ]
    if not any(
        isinstance(row, dict)
        and row.get("type") == "telegram"
        and "연동 완료" in str(row.get("message") or "")
        for row in events
    ):
        events.append({"type": "telegram", "message": "Telegram 브릿지 연동 완료"})
    data["events"] = events


def _mark_successful_venues(data: dict[str, Any]) -> None:
    for venue in data.get("venues") or []:
        if not isinstance(venue, dict):
            continue
        if venue.get("status") or venue.get("error_message"):
            continue
        assets = venue.get("assets")
        if isinstance(assets, list) and assets:
            venue["status"] = "ok"


def _apply_dashboard_venue_labels(data: dict[str, Any]) -> None:
    labels = {
        "kr_stock": KIS_PRIMARY_LABEL,
        "kr_stock_2": KIS_SECONDARY_LABEL,
    }
    for venue in data.get("venues") or []:
        if not isinstance(venue, dict):
            continue
        venue_id = str(venue.get("id") or "")
        if venue_id in labels:
            venue["label"] = labels[venue_id]


def mark_venue_error_cooldown(
    dashboard: dict[str, Any],
    *,
    venue_id: str,
    label: str,
    market: str,
    cached_assets: list[dict[str, Any]] | None,
    cached_at: datetime | None,
    error_at: datetime | None,
    cooldown_sec: int,
    now: datetime,
    event_type: str,
    message: str,
    error_message: str = "",
) -> bool:
    if not _error_is_in_cooldown(error_at, cooldown_sec=cooldown_sec, now=now):
        return False

    if cached_assets is not None:
        mark_venue_stale_with_cached_assets(
            dashboard,
            venue_id=venue_id,
            label=label,
            market=market,
            cached_assets=cached_assets,
            event_type=event_type,
            message=message,
            error_message=error_message,
        )
    else:
        mark_venue_unavailable(
            dashboard,
            venue_id=venue_id,
            label=label,
            market=market,
            status="error_cooldown",
            event_type=event_type,
            message=message,
            error_message=error_message,
        )

    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["cache_status"] = "error_cooldown"
        venue["last_error_at"] = error_at.isoformat() if error_at else ""
        if cached_at is not None:
            venue["cached_at"] = cached_at.isoformat()
        break
    return True


async def build_dashboard_payload(
    deps: DashboardPayloadDeps,
    cache: DashboardPayloadCache,
    *,
    include_telegram: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    async with cache.refresh_lock:
        return await _build_dashboard_payload_unlocked(
            deps,
            cache,
            include_telegram=include_telegram,
            force_refresh=force_refresh,
        )


async def _build_dashboard_payload_unlocked(
    deps: DashboardPayloadDeps,
    cache: DashboardPayloadCache,
    *,
    include_telegram: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    settings = deps.settings
    data = deps.dashboard_template()
    now = datetime.now(timezone.utc)
    _refresh_dashboard_cache_keys(
        settings,
        cache,
        upbit=deps.upbit,
        bithumb=deps.bithumb,
        binance=deps.binance,
        kis_primary=deps.kis_primary,
        kis_secondary=deps.kis_secondary,
    )
    hydrate_dashboard_payload_cache_from_disk(settings, cache)
    kis_cache_ttl_sec = max(
        int(getattr(settings, "dashboard_kis_balance_cache_ttl_sec", 60) or 0),
        0,
    )
    kis_error_cooldown_sec = max(
        int(getattr(settings, "dashboard_kis_balance_error_cooldown_sec", 60) or 0),
        0,
    )
    crypto_cache_ttl_sec = max(
        int(getattr(settings, "dashboard_crypto_balance_cache_ttl_sec", 15) or 0),
        0,
    )
    stale_cache_ttl_sec = max(
        int(getattr(settings, "dashboard_stale_balance_cache_ttl_sec", 0) or 0),
        0,
    )
    balance_fetch_timeout_sec = max(
        float(getattr(settings, "dashboard_balance_fetch_timeout_sec", 0) or 0),
        0.0,
    )
    kis_us_balance_enabled = bool(
        getattr(settings, "dashboard_kis_us_balance_enabled", True)
    )
    fx_snapshot: dict[str, Any]
    try:
        fx_snapshot = await deps.fx_rates.get_snapshot()
    except Exception as exc:
        deps.logger.warning("fx rate fetch failed: %s", exc)
        fx_snapshot = {
            "usdt_krw": None,
            "usd_krw": None,
            "usdt_source": "error",
            "usd_source": "error",
            "status": "error",
            "error_message": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        data["events"].append({"type": "fx", "message": f"환율 조회 실패: {exc}"})

    usdt_krw = safe_positive_float(fx_snapshot.get("usdt_krw"))
    usd_krw = safe_positive_float(fx_snapshot.get("usd_krw")) or usdt_krw
    usdt_source = str(fx_snapshot.get("usdt_source") or "unknown").strip() or "unknown"
    usd_source = str(fx_snapshot.get("usd_source") or "unknown").strip() or "unknown"
    if str(fx_snapshot.get("status") or "").strip().lower() == "error":
        fx_status = "error"
    else:
        fx_status = (
            "warn"
            if is_fx_source_degraded(usdt_source) or is_fx_source_degraded(usd_source)
            else "ok"
        )
    fx_snapshot["usdt_krw"] = usdt_krw if usdt_krw > 0 else None
    fx_snapshot["usd_krw"] = usd_krw if usd_krw > 0 else None
    fx_snapshot["usdt_source"] = usdt_source
    fx_snapshot["usd_source"] = usd_source
    fx_snapshot["status"] = fx_status
    data["fx"] = fx_snapshot
    if usdt_krw > 0 or usd_krw > 0:
        apply_dashboard_fx_rates(data, usdt_krw=usdt_krw, usd_krw=usd_krw)
    if fx_status == "warn":
        data["events"].append(
            {"type": "fx", "message": "환율 품질 주의: fallback/proxy 소스 포함"}
        )
    if fx_status != "error":
        data["events"].append(
            {
                "type": "fx",
                "message": (
                    f"환율 반영 USDT/KRW {usdt_krw:,.2f} ({usdt_source}), "
                    f"USD/KRW {usd_krw:,.2f} ({usd_source})"
                ),
            }
        )

    # Crypto venue balances are independent. Refresh them together so a slow
    # venue does not stretch the first dashboard load for every other venue.
    crypto_tasks = []

    async def _refresh_upbit() -> None:
        try:
            upbit_assets = await _await_balance_fetch(
                deps.upbit.fetch_balance_assets(),
                timeout_sec=balance_fetch_timeout_sec,
                label="업비트",
            )
            cache.upbit_assets = upbit_assets
            cache.upbit_fetched_at = now
            if deps.replace_venue_assets(data, "upbit", upbit_assets):
                data["events"].append(
                    {"type": "upbit", "message": "업비트 실잔고 연동 완료"}
                )
        except Exception as exc:
            deps.logger.warning("upbit balance fetch failed: %s", exc)
            if mark_venue_stale_with_cached_assets(
                data,
                venue_id="upbit",
                label="업비트",
                market="국내 가상자산",
                cached_assets=cache.upbit_assets,
                event_type="upbit",
                message=f"업비트 잔고 조회 실패: 최근 성공 잔고 유지 ({exc})",
                error_message=str(exc),
                cached_at=cache.upbit_fetched_at,
            ):
                return
            mark_venue_unavailable(
                data,
                venue_id="upbit",
                label="업비트",
                market="국내 가상자산",
                status="error",
                event_type="upbit",
                message=f"업비트 잔고 조회 실패: {exc}",
                error_message=str(exc),
            )

    if settings.upbit_ready:
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="upbit",
            label="업비트",
            market="국내 가상자산",
            cached_assets=cache.upbit_assets,
            fetched_at=cache.upbit_fetched_at,
            ttl_sec=crypto_cache_ttl_sec,
            now=now,
            event_type="upbit",
            message="업비트 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="upbit",
            label="업비트",
            market="국내 가상자산",
            cached_assets=cache.upbit_assets,
            fetched_at=cache.upbit_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="upbit",
            message="업비트 stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            crypto_tasks.append(_refresh_upbit())
    else:
        mark_venue_unavailable(
            data,
            venue_id="upbit",
            label="업비트",
            market="국내 가상자산",
            status="not_configured",
            event_type="upbit",
            message="업비트 키 미설정",
        )

    async def _refresh_bithumb() -> None:
        try:
            bithumb_assets = await _await_balance_fetch(
                deps.bithumb.fetch_balance_assets(),
                timeout_sec=balance_fetch_timeout_sec,
                label="빗썸",
            )
            cache.bithumb_assets = bithumb_assets
            cache.bithumb_fetched_at = now
            deps.upsert_venue_assets(
                data,
                venue_id="bithumb",
                label="빗썸",
                market="국내 가상자산",
                assets=bithumb_assets,
            )
            data["events"].append(
                {"type": "bithumb", "message": "빗썸 실잔고 연동 완료"}
            )
        except Exception as exc:
            deps.logger.warning("bithumb balance fetch failed: %s", exc)
            if mark_venue_stale_with_cached_assets(
                data,
                venue_id="bithumb",
                label="빗썸",
                market="국내 가상자산",
                cached_assets=cache.bithumb_assets,
                event_type="bithumb",
                message=f"빗썸 조회 실패: 최근 성공 잔고 유지 ({exc})",
                error_message=str(exc),
                cached_at=cache.bithumb_fetched_at,
            ):
                return
            mark_venue_unavailable(
                data,
                venue_id="bithumb",
                label="빗썸",
                market="국내 가상자산",
                status="error",
                event_type="bithumb",
                message=f"빗썸 조회 실패: {exc}",
                error_message=str(exc),
            )

    if settings.bithumb_ready:
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="bithumb",
            label="빗썸",
            market="국내 가상자산",
            cached_assets=cache.bithumb_assets,
            fetched_at=cache.bithumb_fetched_at,
            ttl_sec=crypto_cache_ttl_sec,
            now=now,
            event_type="bithumb",
            message="빗썸 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="bithumb",
            label="빗썸",
            market="국내 가상자산",
            cached_assets=cache.bithumb_assets,
            fetched_at=cache.bithumb_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="bithumb",
            message="빗썸 stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            crypto_tasks.append(_refresh_bithumb())
    else:
        mark_venue_unavailable(
            data,
            venue_id="bithumb",
            label="빗썸",
            market="국내 가상자산",
            status="not_configured",
            event_type="bithumb",
            message="빗썸 키 미설정",
        )

    async def _refresh_binance_spot() -> None:
        try:
            spot_assets = await _await_balance_fetch(
                deps.binance.fetch_spot_assets(usdt_krw_rate=usdt_krw),
                timeout_sec=balance_fetch_timeout_sec,
                label="바이낸스 Spot",
            )
            cache.binance_spot_assets = spot_assets
            cache.binance_spot_fetched_at = now
            if not deps.replace_venue_assets(data, "binance", spot_assets):
                deps.upsert_venue_assets(
                    data,
                    venue_id="binance",
                    label="바이낸스 현물",
                    market="해외 가상자산 (Spot)",
                    assets=spot_assets,
                )
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Spot 잔고 연동 완료"}
            )
        except Exception as exc:
            deps.logger.warning("binance spot balance fetch failed: %s", exc)
            if mark_venue_stale_with_cached_assets(
                data,
                venue_id="binance",
                label="바이낸스 현물",
                market="해외 가상자산 (Spot)",
                cached_assets=cache.binance_spot_assets,
                event_type="binance",
                message=f"바이낸스 Spot 조회 실패: 최근 성공 잔고 유지 ({exc})",
                error_message=str(exc),
                cached_at=cache.binance_spot_fetched_at,
            ):
                return
            mark_venue_unavailable(
                data,
                venue_id="binance",
                label="바이낸스 현물",
                market="해외 가상자산 (Spot)",
                status="error",
                event_type="binance",
                message=f"바이낸스 Spot 조회 실패: {exc}",
                error_message=str(exc),
            )

    if settings.binance_spot_ready:
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="binance",
            label="바이낸스 현물",
            market="해외 가상자산 (Spot)",
            cached_assets=cache.binance_spot_assets,
            fetched_at=cache.binance_spot_fetched_at,
            ttl_sec=crypto_cache_ttl_sec,
            now=now,
            event_type="binance",
            message="바이낸스 Spot 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="binance",
            label="바이낸스 현물",
            market="해외 가상자산 (Spot)",
            cached_assets=cache.binance_spot_assets,
            fetched_at=cache.binance_spot_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="binance",
            message="바이낸스 Spot stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            crypto_tasks.append(_refresh_binance_spot())
    else:
        mark_venue_unavailable(
            data,
            venue_id="binance",
            label="바이낸스 현물",
            market="해외 가상자산 (Spot)",
            status="not_configured",
            event_type="binance",
            message="바이낸스 Spot 키 미설정",
        )

    async def _refresh_binance_futures() -> None:
        try:
            futures_assets = await _await_balance_fetch(
                deps.binance.fetch_futures_assets(usdt_krw_rate=usdt_krw),
                timeout_sec=balance_fetch_timeout_sec,
                label="바이낸스 Futures",
            )
            cache.binance_futures_assets = futures_assets
            cache.binance_futures_fetched_at = now
            deps.upsert_venue_assets(
                data,
                venue_id="binance_futures",
                label="바이낸스 선물",
                market="해외 가상자산 (Futures)",
                assets=futures_assets,
            )
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Futures 잔고 연동 완료"}
            )
        except Exception as exc:
            deps.logger.warning("binance futures balance fetch failed: %s", exc)
            if mark_venue_stale_with_cached_assets(
                data,
                venue_id="binance_futures",
                label="바이낸스 선물",
                market="해외 가상자산 (Futures)",
                cached_assets=cache.binance_futures_assets,
                event_type="binance",
                message=f"바이낸스 Futures 조회 실패: 최근 성공 잔고 유지 ({exc})",
                error_message=str(exc),
                cached_at=cache.binance_futures_fetched_at,
            ):
                return
            mark_venue_unavailable(
                data,
                venue_id="binance_futures",
                label="바이낸스 선물",
                market="해외 가상자산 (Futures)",
                status="error",
                event_type="binance",
                message=f"바이낸스 Futures 조회 실패: {exc}",
                error_message=str(exc),
            )

    if settings.binance_futures_ready:
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="binance_futures",
            label="바이낸스 선물",
            market="해외 가상자산 (Futures)",
            cached_assets=cache.binance_futures_assets,
            fetched_at=cache.binance_futures_fetched_at,
            ttl_sec=crypto_cache_ttl_sec,
            now=now,
            event_type="binance",
            message="바이낸스 Futures 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="binance_futures",
            label="바이낸스 선물",
            market="해외 가상자산 (Futures)",
            cached_assets=cache.binance_futures_assets,
            fetched_at=cache.binance_futures_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="binance",
            message="바이낸스 Futures stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            crypto_tasks.append(_refresh_binance_futures())
    else:
        mark_venue_unavailable(
            data,
            venue_id="binance_futures",
            label="바이낸스 선물",
            market="해외 가상자산 (Futures)",
            status="not_configured",
            event_type="binance",
            message="바이낸스 Futures 키 미설정",
        )

    if crypto_tasks:
        await asyncio.gather(*crypto_tasks)

    # KIS account balance endpoints share a stricter broker-side ledger limit.
    # Keep these as sequential fetches; the adapter-level limiter remains the
    # final cross-process gate.

    if settings.kis_primary_ready:
        kis_primary_cached_assets = _usable_kis_cached_assets(cache.kis_primary_assets)
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="kr_stock",
            label=KIS_PRIMARY_LABEL,
            market="KRX",
            cached_assets=kis_primary_cached_assets,
            fetched_at=cache.kis_primary_fetched_at,
            ttl_sec=kis_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_error_cooldown(
            data,
            venue_id="kr_stock",
            label=KIS_PRIMARY_LABEL,
            market="KRX",
            cached_assets=kis_primary_cached_assets,
            cached_at=cache.kis_primary_fetched_at,
            error_at=cache.kis_primary_error_at,
            cooldown_sec=kis_error_cooldown_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 최근 조회 오류 대기",
            error_message=cache.kis_primary_error_message,
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="kr_stock",
            label=KIS_PRIMARY_LABEL,
            market="KRX",
            cached_assets=kis_primary_cached_assets,
            fetched_at=cache.kis_primary_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            try:
                primary_assets = await _await_balance_fetch(
                    deps.kis_primary.fetch_balance_assets(),
                    timeout_sec=balance_fetch_timeout_sec,
                    label="KIS 1번 계좌",
                )
                if not deps.replace_venue_assets(data, "kr_stock", primary_assets):
                    deps.upsert_venue_assets(
                        data,
                        venue_id="kr_stock",
                        label=KIS_PRIMARY_LABEL,
                        market="KRX",
                        assets=primary_assets,
                    )
                cache.kis_primary_assets = primary_assets
                cache.kis_primary_fetched_at = now
                cache.kis_primary_error_at = None
                cache.kis_primary_error_message = ""
                data["events"].append(
                    {"type": "kis", "message": "KIS 1번 계좌 실잔고 연동 완료"}
                )
            except Exception as exc:
                cache.kis_primary_error_at = now
                cache.kis_primary_error_message = str(exc)
                deps.logger.warning("kis primary balance fetch failed: %s", exc)
                if not mark_venue_stale_with_cached_assets(
                    data,
                    venue_id="kr_stock",
                    label=KIS_PRIMARY_LABEL,
                    market="KRX",
                    cached_assets=kis_primary_cached_assets,
                    event_type="kis",
                    message=f"KIS 1번 계좌 조회 실패: 최근 성공 잔고 유지 ({exc})",
                    error_message=str(exc),
                ):
                    mark_venue_unavailable(
                        data,
                        venue_id="kr_stock",
                        label=KIS_PRIMARY_LABEL,
                        market="KRX",
                        status="error",
                        event_type="kis",
                        message=f"KIS 1번 계좌 조회 실패: {exc}",
                        error_message=str(exc),
                    )
        if not kis_us_balance_enabled:
            mark_venue_unavailable(
                data,
                venue_id="us_stock",
                label="미장",
                market="NASDAQ/NYSE",
                status="disabled",
                event_type="kis",
                message="KIS 1번 미장 대시보드 조회 비활성",
            )
        elif not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="us_stock",
            label="미장",
            market="NASDAQ/NYSE",
            cached_assets=cache.kis_primary_us_assets,
            fetched_at=cache.kis_primary_us_fetched_at,
            ttl_sec=kis_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 미장 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_error_cooldown(
            data,
            venue_id="us_stock",
            label="미장",
            market="NASDAQ/NYSE",
            cached_assets=cache.kis_primary_us_assets,
            cached_at=cache.kis_primary_us_fetched_at,
            error_at=cache.kis_primary_us_error_at,
            cooldown_sec=kis_error_cooldown_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 미장 최근 조회 오류 대기",
            error_message=cache.kis_primary_us_error_message,
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="us_stock",
            label="미장",
            market="NASDAQ/NYSE",
            cached_assets=cache.kis_primary_us_assets,
            fetched_at=cache.kis_primary_us_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 1번 계좌 미장 stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            try:
                primary_us_assets = await _await_balance_fetch(
                    deps.kis_primary.fetch_us_balance_assets(usd_krw_rate=usd_krw),
                    timeout_sec=balance_fetch_timeout_sec,
                    label="KIS 1번 계좌 미장",
                )
                if not deps.replace_venue_assets(data, "us_stock", primary_us_assets):
                    deps.upsert_venue_assets(
                        data,
                        venue_id="us_stock",
                        label="미장",
                        market="NASDAQ/NYSE",
                        assets=primary_us_assets,
                    )
                cache.kis_primary_us_assets = primary_us_assets
                cache.kis_primary_us_fetched_at = now
                cache.kis_primary_us_error_at = None
                cache.kis_primary_us_error_message = ""
                data["events"].append(
                    {
                        "type": "kis",
                        "message": "KIS 1번 계좌 미장 실잔고 연동 완료",
                    }
                )
            except Exception as exc:
                cache.kis_primary_us_error_at = now
                cache.kis_primary_us_error_message = str(exc)
                deps.logger.warning("kis primary us balance fetch failed: %s", exc)
                if not mark_venue_stale_with_cached_assets(
                    data,
                    venue_id="us_stock",
                    label="미장",
                    market="NASDAQ/NYSE",
                    cached_assets=cache.kis_primary_us_assets,
                    event_type="kis",
                    message=f"KIS 1번 계좌 미장 조회 실패: 최근 성공 잔고 유지 ({exc})",
                    error_message=str(exc),
                ):
                    mark_venue_unavailable(
                        data,
                        venue_id="us_stock",
                        label="미장",
                        market="NASDAQ/NYSE",
                        status="error",
                        event_type="kis",
                        message=f"KIS 1번 계좌 미장 조회 실패: {exc}",
                        error_message=str(exc),
                    )
    else:
        mark_venue_unavailable(
            data,
            venue_id="kr_stock",
            label=KIS_PRIMARY_LABEL,
            market="KRX",
            status="not_configured",
            event_type="kis",
            message="KIS 1번 키 미설정",
        )
        mark_venue_unavailable(
            data,
            venue_id="us_stock",
            label="미장",
            market="NASDAQ/NYSE",
            status="not_configured",
            event_type="kis",
            message="KIS 1번 미장 키 미설정",
        )

    if settings.kis_secondary_ready:
        kis_secondary_cached_assets = _usable_kis_cached_assets(
            cache.kis_secondary_assets
        )
        if not force_refresh and mark_venue_fresh_with_cached_assets(
            data,
            venue_id="kr_stock_2",
            label=KIS_SECONDARY_LABEL,
            market="KRX",
            cached_assets=kis_secondary_cached_assets,
            fetched_at=cache.kis_secondary_fetched_at,
            ttl_sec=kis_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 2번 계좌 최근 잔고 캐시 사용",
        ):
            pass
        elif not force_refresh and mark_venue_error_cooldown(
            data,
            venue_id="kr_stock_2",
            label=KIS_SECONDARY_LABEL,
            market="KRX",
            cached_assets=kis_secondary_cached_assets,
            cached_at=cache.kis_secondary_fetched_at,
            error_at=cache.kis_secondary_error_at,
            cooldown_sec=kis_error_cooldown_sec,
            now=now,
            event_type="kis",
            message="KIS 2번 계좌 최근 조회 오류 대기",
            error_message=cache.kis_secondary_error_message,
        ):
            pass
        elif not force_refresh and mark_venue_stale_display_cache(
            data,
            venue_id="kr_stock_2",
            label=KIS_SECONDARY_LABEL,
            market="KRX",
            cached_assets=kis_secondary_cached_assets,
            fetched_at=cache.kis_secondary_fetched_at,
            stale_ttl_sec=stale_cache_ttl_sec,
            now=now,
            event_type="kis",
            message="KIS 2번 계좌 stale 잔고 캐시 즉시 표시",
        ):
            pass
        else:
            try:
                secondary_assets = await _await_balance_fetch(
                    deps.kis_secondary.fetch_balance_assets(),
                    timeout_sec=balance_fetch_timeout_sec,
                    label="KIS 2번 계좌",
                )
                cache.kis_secondary_assets = secondary_assets
                cache.kis_secondary_fetched_at = now
                cache.kis_secondary_error_at = None
                cache.kis_secondary_error_message = ""
                deps.upsert_venue_assets(
                    data,
                    venue_id="kr_stock_2",
                    label=KIS_SECONDARY_LABEL,
                    market="KRX",
                    assets=secondary_assets,
                )
                data["events"].append(
                    {"type": "kis", "message": "KIS 2번 계좌 실잔고 연동 완료"}
                )
            except Exception as exc:
                cache.kis_secondary_error_at = now
                cache.kis_secondary_error_message = str(exc)
                deps.logger.warning("kis secondary balance fetch failed: %s", exc)
                if not mark_venue_stale_with_cached_assets(
                    data,
                    venue_id="kr_stock_2",
                    label=KIS_SECONDARY_LABEL,
                    market="KRX",
                    cached_assets=kis_secondary_cached_assets,
                    event_type="kis",
                    message=f"KIS 2번 계좌 조회 실패: 최근 성공 잔고 유지 ({exc})",
                    error_message=str(exc),
                ):
                    mark_venue_unavailable(
                        data,
                        venue_id="kr_stock_2",
                        label=KIS_SECONDARY_LABEL,
                        market="KRX",
                        status="error",
                        event_type="kis",
                        message=f"KIS 2번 계좌 조회 실패: {exc}",
                        error_message=str(exc),
                    )
    else:
        mark_venue_unavailable(
            data,
            venue_id="kr_stock_2",
            label=KIS_SECONDARY_LABEL,
            market="KRX",
            status="not_configured",
            event_type="kis",
            message="KIS 2번 키 미설정",
        )

    runtime_snapshot, runtime_status = deps.runtime_reader.read_snapshot()
    runtime_sessions = (
        list(runtime_snapshot.get("sessions") or [])
        if isinstance(runtime_snapshot, dict)
        else None
    )
    if runtime_sessions is not None:
        data["sessions"] = runtime_sessions
        data["runtime"] = {
            **dict(runtime_snapshot.get("runtime") or {}),
            "updated_at": runtime_snapshot.get("updated_at"),
            "status": runtime_status,
            "age_sec": runtime_snapshot.get("age_sec"),
            "max_age_sec": runtime_snapshot.get("max_age_sec"),
        }
        runtime_message = (
            "세션 상태 런타임 stale: 마지막 상태 표시"
            if runtime_status == "stale"
            else "세션 상태 런타임 연결됨"
        )
        data["events"].append({"type": "runtime", "message": runtime_message})
    else:
        data["runtime"] = {
            "status": runtime_status,
            "updated_at": None,
            "age_sec": None,
            "max_age_sec": None,
        }
        if runtime_status == "missing":
            data["events"].append(
                {
                    "type": "runtime",
                    "message": "세션 상태 런타임 미연결: 세션 없음",
                }
            )
        elif runtime_status == "stale":
            data["events"].append(
                {"type": "runtime", "message": "세션 상태 런타임 stale: 세션 없음"}
            )
        else:
            data["events"].append(
                {"type": "runtime", "message": "세션 상태 런타임 오류: 세션 없음"}
            )

    if not settings.research_enabled:
        reports_summary = None
        if deps.research_status_provider is not None:
            try:
                reports_summary = _research_status_summary_from_reports(
                    deps.research_status_provider()
                )
            except Exception as exc:
                deps.logger.warning("dashboard reports status unavailable: %s", exc)
        if reports_summary is not None:
            data["research"] = reports_summary
            data["events"].append(
                {"type": "research", "message": "리포트/RAG 리서치 상태 반영됨"}
            )
        else:
            data["research"] = {
                "updated_at": None,
                "source": "disabled",
                "query": "general",
                "status": "disabled",
                "count": 0,
                "items": [],
                "stale": False,
            }
            data["events"].append(
                {
                    "type": "research",
                    "message": "요약리서치 비활성화: 전략 판단에서 제외",
                }
            )
    else:
        research_payload, research_status = deps.research_reader.read_feed(
            allow_stale=True
        )
        if research_payload is not None:
            data["research"] = research_payload
            if research_status == "stale":
                data["events"].append(
                    {
                        "type": "research",
                        "message": "리서치 스냅샷 오래됨: 마지막 결과 표시",
                    }
                )
            else:
                data["events"].append(
                    {"type": "research", "message": "최근 리서치 결과 반영됨"}
                )
        elif research_status == "missing":
            data["research"] = {
                "updated_at": None,
                "source": "scheduled",
                "query": "general",
                "status": "missing",
                "count": 0,
                "items": [],
            }
            data["events"].append(
                {"type": "research", "message": "리서치 스냅샷 미연결: no data"}
            )
        else:
            data["research"] = {
                "updated_at": None,
                "source": "scheduled",
                "query": "general",
                "status": research_status,
                "count": 0,
                "items": [],
            }
            data["events"].append(
                {"type": "research", "message": f"리서치 상태 오류: {research_status}"}
            )

    if include_telegram:
        _apply_telegram_status(data, deps.telegram.status())
    _apply_dashboard_venue_labels(data)
    _mark_successful_venues(data)
    data["status"] = "ok"
    data["updated_at"] = now.isoformat()
    persist_dashboard_payload_cache_to_disk(settings, cache)
    return data
