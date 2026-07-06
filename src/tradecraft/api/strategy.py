from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException

_BRIEF_TEXT_LIMIT = 180
_BRIEF_SHORT_TEXT_LIMIT = 120
_BRIEF_FACT_LIMIT = 140
_BRIEF_CITATION_LIMIT = 100
_BRIEF_SUITABILITY_TEXT_LIMIT = 80


@dataclass(frozen=True)
class StrategyRouteDeps:
    require_admin_auth: Callable[..., Any]
    strategy_engine: Callable[[], Any]
    classify_intent: Callable[[str], str]
    default_query: Callable[[Any], str]
    safe_limit: Callable[[Any], int]
    read_research_feed: Callable[[], dict[str, Any] | None]
    collect_source_ids: Callable[[dict[str, Any] | None], list[str] | None]
    safe_collect_sources: Callable[[list[str] | None], list[dict[str, Any]]]
    build_insight_collector: Callable[[list[dict[str, Any]] | None], Any]
    now: Callable[[], datetime]


def build_strategy_router(deps: StrategyRouteDeps) -> APIRouter:
    router = APIRouter()

    def _insights_status_payload(*, include_signals: bool = True) -> dict[str, Any]:
        sources = deps.strategy_engine().source_status()
        if not include_signals:
            sources = [_compact_insight_source_status(source) for source in sources]
        return {
            "status": "ok",
            "updated_at": deps.now().isoformat(),
            "sources": sources,
            "schema": {
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "large_holder_change | after_close_flow",
                "direction": "positive | negative | neutral",
                "strength": 0,
                "summary": "근거 요약",
                "as_of": "ISO-8601 timestamp",
                "stale": False,
                "stale_days": 0,
                "stale_after_days": 5,
                "stale_reason": "",
                "tags": ["optional"],
            },
        }

    @router.post("/api/strategy/intent")
    async def strategy_intent(payload: dict[str, Any]) -> dict[str, Any]:
        query = deps.default_query(payload.get("query"))
        return {
            "status": "ok",
            "query": query,
            "intent": deps.classify_intent(query),
        }

    @router.get("/api/strategy/insights")
    async def strategy_insights() -> dict[str, Any]:
        return _insights_status_payload()

    @router.get("/api/strategy/insights/status")
    async def strategy_insights_status() -> dict[str, Any]:
        return _insights_status_payload(include_signals=False)

    @router.get("/api/strategy/insights/signals")
    async def strategy_insight_signals(
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        return deps.strategy_engine().list_external_signals(
            source_id=source_id,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @router.post("/api/strategy/insights/collect")
    async def strategy_insights_collect(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        source_ids = deps.collect_source_ids(payload)
        sources = deps.safe_collect_sources(source_ids)
        try:
            return await _maybe_await(deps.build_insight_collector(sources).collect_once())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/strategy/insights/{source_id}")
    async def strategy_insight_append(
        source_id: str,
        payload: dict[str, Any],
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        try:
            return deps.strategy_engine().append_external_signals(
                source_id=source_id,
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/strategy/candidates")
    async def strategy_candidates(
        query: str = "",
        limit: int = 8,
    ) -> dict[str, Any]:
        return deps.strategy_engine().build_candidates(
            query=deps.default_query(query),
            research_feed=deps.read_research_feed(),
            limit=deps.safe_limit(limit),
        )

    @router.post("/api/strategy/candidates")
    async def strategy_candidates_post(payload: dict[str, Any]) -> dict[str, Any]:
        return deps.strategy_engine().build_candidates(
            query=deps.default_query(payload.get("query")),
            research_feed=deps.read_research_feed(),
            limit=deps.safe_limit(payload.get("limit")),
        )

    @router.get("/api/strategy/brief")
    async def strategy_brief(
        query: str = "",
        limit: int = 8,
        use_llm: bool = False,
        compact: bool = False,
        authorization: str | None = Header(default=None),
        admin_token: str | None = Header(default=None, alias="X-TradeCraft-Admin-Token"),
    ) -> dict[str, Any]:
        if bool(use_llm):
            deps.require_admin_auth(
                authorization=authorization,
                admin_token=admin_token,
            )
        payload = await _maybe_await(
            deps.strategy_engine().build_brief(
                query=deps.default_query(query),
                research_feed=deps.read_research_feed(),
                use_llm=bool(use_llm),
                limit=deps.safe_limit(limit),
            )
        )
        return _compact_strategy_brief(payload) if compact else payload

    @router.post("/api/strategy/brief")
    async def strategy_brief_post(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
        admin_token: str | None = Header(default=None, alias="X-TradeCraft-Admin-Token"),
    ) -> dict[str, Any]:
        if bool(payload.get("use_llm")):
            deps.require_admin_auth(
                authorization=authorization,
                admin_token=admin_token,
            )
        payload_result = await _maybe_await(
            deps.strategy_engine().build_brief(
                query=deps.default_query(payload.get("query")),
                research_feed=deps.read_research_feed(),
                use_llm=bool(payload.get("use_llm")),
                limit=deps.safe_limit(payload.get("limit")),
            )
        )
        return _compact_strategy_brief(payload_result) if bool(payload.get("compact")) else payload_result

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _compact_insight_source_status(source: Any) -> Any:
    if not isinstance(source, dict):
        return source
    return {key: value for key, value in source.items() if key != "signals"}


def _compact_strategy_brief(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown", "compact": True}
    compact: dict[str, Any] = {
        key: value
        for key, value in payload.items()
        if key
        in {
            "status",
            "updated_at",
            "query",
            "intent",
            "model",
            "score_method_version",
            "regime",
            "next_session",
            "candidate_count",
            "brief_mode",
            "brief_md",
            "cache_status",
            "error_message",
        }
        and value not in (None, "", [], {})
    }
    compact["compact"] = True
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        compact["candidates"] = [
            _compact_strategy_candidate(row)
            for row in candidates[:30]
            if isinstance(row, dict)
        ]
    exclusions = payload.get("exclusions")
    if isinstance(exclusions, list):
        compact["exclusions"] = [
            _compact_strategy_exclusion(row)
            for row in exclusions[:6]
            if isinstance(row, dict)
        ]
    sources = payload.get("sources")
    if isinstance(sources, list):
        compact["sources"] = [
            _compact_strategy_source(row)
            for row in sources[:12]
            if isinstance(row, dict)
        ]
    methodology = payload.get("methodology")
    if isinstance(methodology, list):
        compact["methodology"] = [
            _clean_strategy_text(item, limit=_BRIEF_TEXT_LIMIT)
            for item in methodology[:5]
            if str(item or "").strip()
        ]
    return compact


def _compact_strategy_candidate(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in row.items()
        if key
        in {
            "symbol",
            "name",
            "asset_class",
            "horizon_bias",
            "score",
            "score_method_version",
            "risk_score",
            "confidence",
            "confidence_label",
            "stance",
        }
        and value not in (None, "", [], {})
    }
    score_components = row.get("score_components")
    if isinstance(score_components, dict):
        compact["score_components"] = {
            key: value
            for key, value in score_components.items()
            if key
            in {
                "report",
                "research",
                "whale",
                "after_close",
                "valuation",
                "recency",
                "evidence",
                "fit",
                "risk_penalty",
                "risk_score",
            }
            and value not in (None, "", [], {})
        }
    data_coverage = row.get("data_coverage")
    if isinstance(data_coverage, dict):
        compact["data_coverage"] = _compact_strategy_data_coverage(data_coverage)
    for key in ("suitability", "identity_status", "valuation"):
        value = row.get(key)
        if isinstance(value, dict):
            if key == "suitability":
                compact_value = _compact_strategy_suitability(value)
            elif key == "valuation":
                compact_value = _compact_strategy_valuation(value)
            else:
                compact_value = _compact_strategy_identity(value)
            if compact_value:
                compact[key] = compact_value
    for key, limit, text_limit in (
        ("data_warnings", 4, _BRIEF_SHORT_TEXT_LIMIT),
        ("reasons", 3, _BRIEF_TEXT_LIMIT),
        ("risks", 2, _BRIEF_TEXT_LIMIT),
        ("checks", 3, _BRIEF_TEXT_LIMIT),
        ("sources", 5, _BRIEF_SHORT_TEXT_LIMIT),
        ("report_ids", 8, _BRIEF_SHORT_TEXT_LIMIT),
        ("citations", 3, _BRIEF_CITATION_LIMIT),
        ("facts", 3, _BRIEF_FACT_LIMIT),
    ):
        values = _compact_text_list(row.get(key), limit=limit, text_limit=text_limit)
        if values:
            compact[key] = values
    return compact


def _compact_strategy_exclusion(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            _clean_strategy_text(value, limit=_BRIEF_SHORT_TEXT_LIMIT)
            if key == "reason"
            else value
        )
        for key, value in row.items()
        if key in {"symbol", "name", "reason", "score"}
        and value not in (None, "", [], {})
    }


def _compact_strategy_source(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "source_id",
            "label",
            "status",
            "count",
            "returned_count",
            "usable_count",
            "stale_count",
            "latest_as_of",
            "summary",
        }
        and value not in (None, "", [], {})
    }


def _compact_strategy_suitability(value: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("short_term", "mid_term", "long_term", "balanced"):
        row = value.get(key)
        if not isinstance(row, dict):
            continue
        item = {
            field: row[field]
            for field in ("score", "grade")
            if row.get(field) not in (None, "", [], {})
        }
        drivers = _compact_text_list(
            row.get("drivers"),
            limit=1,
            text_limit=_BRIEF_SUITABILITY_TEXT_LIMIT,
        )
        risks = _compact_text_list(
            row.get("risks"),
            limit=1,
            text_limit=_BRIEF_SUITABILITY_TEXT_LIMIT,
        )
        if drivers:
            item["drivers"] = drivers
        if risks:
            item["risks"] = risks
        if item:
            compact[key] = item
    return compact


def _compact_strategy_data_coverage(value: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item
        for key, item in value.items()
        if key in {"coverage_score", "source_count"}
        and item not in (None, "", [], {})
    }
    missing = value.get("missing")
    if isinstance(missing, list):
        compact["missing"] = [
            _clean_strategy_text(item, limit=60)
            for item in missing[:4]
            if str(item or "").strip()
        ]
    return compact


def _compact_strategy_identity(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key in {"status", "label", "confidence", "source"}
        and item not in (None, "", [], {})
    }


def _compact_strategy_valuation(value: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item
        for key, item in value.items()
        if key in {"status", "label", "as_of", "source_url"}
        and item not in (None, "", [], {})
    }
    metrics = value.get("metrics")
    if isinstance(metrics, dict):
        compact["metrics"] = {
            key: item
            for key, item in metrics.items()
            if key
            in {
                "price",
                "market_cap_krw",
                "per",
                "eps",
                "pbr",
                "bps",
                "dividend_yield_pct",
                "industry_per",
                "industry_name",
            }
            and item not in (None, "", [], {})
        }
    score = value.get("score")
    if isinstance(score, dict):
        compact["score"] = {
            key: item
            for key, item in score.items()
            if key
            in {
                "undervalued_score",
                "overvalued_risk",
                "quality_score",
                "growth_score",
                "relative_per_discount_pct",
                "pbr_roe_fit",
                "label",
            }
            and item not in (None, "", [], {})
        }
    for key in ("reasons", "risks"):
        values = _compact_text_list(value.get(key), limit=3, text_limit=_BRIEF_TEXT_LIMIT)
        if values:
            compact[key] = values
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_text_list(value: Any, *, limit: int, text_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for text in (
            _clean_strategy_text(item, limit=text_limit)
            for item in value[: max(0, int(limit))]
        )
        if text
    ]


def _clean_strategy_text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[: max(0, int(limit))]
