from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any


@dataclass
class EvidenceItem:
    evidence_id: str
    source: str
    signal_type: str
    symbol: str
    scope: str
    confidence: float
    captured_at: str
    expires_at: str
    payload: dict[str, Any]
    outcome_status: str = "pending"
    used_by_block_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["used_by_block_ids"] = self.used_by_block_ids or []
        return data

    def is_expired(self, now: str | None = None) -> bool:
        now_dt = _parse_iso_datetime(now or utc_now_iso())
        expires_at_dt = _parse_iso_datetime(self.expires_at)
        return expires_at_dt <= now_dt


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: Any) -> str:
    normalized = str(symbol or "").strip().upper()
    for token in ("/", "-", "_"):
        normalized = normalized.replace(token, "")
    return normalized


def normalize_scope(scope: Any) -> str:
    normalized = str(scope or "").strip().lower()
    if normalized in {"binance", "crypto"}:
        return "binance"
    if normalized in {"kis", "krx", "domestic"}:
        return "kis"
    if normalized == "global":
        return "global"
    return "global"


def evidence_from_signal(
    *,
    source: str,
    signal_type: str,
    symbol: Any,
    scope: Any,
    confidence: float,
    ttl_sec: int | float,
    captured_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> EvidenceItem:
    captured_at_value = captured_at or utc_now_iso()
    captured_at_dt = _parse_iso_datetime(captured_at_value)
    ttl_seconds = max(1, int(ttl_sec))
    payload_value = payload or {}
    symbol_value = normalize_symbol(symbol)
    scope_value = normalize_scope(scope)
    confidence_value = _clamp_confidence(confidence)
    expires_at = (captured_at_dt + timedelta(seconds=ttl_seconds)).isoformat()
    evidence_id = _build_evidence_id(
        source=source,
        signal_type=signal_type,
        symbol=symbol_value,
        scope=scope_value,
        captured_at=captured_at_value,
        payload=payload_value,
    )
    return EvidenceItem(
        evidence_id=evidence_id,
        source=source,
        signal_type=signal_type,
        symbol=symbol_value,
        scope=scope_value,
        confidence=confidence_value,
        captured_at=captured_at_value,
        expires_at=expires_at,
        payload=payload_value,
    )


def scorecard_from_evidence(
    *,
    policy_id: str,
    evidence: list[EvidenceItem],
    now: str | None = None,
    max_items: int = 5,
) -> dict[str, Any]:
    now_value = now or utc_now_iso()
    fresh = [item for item in evidence if not item.is_expired(now=now_value)]
    expired = [item for item in evidence if item.is_expired(now=now_value)]
    strongest = _sort_evidence(fresh)[: max(0, max_items)]
    average_confidence = (
        round(sum(item.confidence for item in fresh) / len(fresh), 4) if fresh else 0.0
    )
    return {
        "policy_id": policy_id,
        "policy_mode": "advisory_preference_caution",
        "hard_filters_enabled": False,
        "fresh_count": len(fresh),
        "expired_count": len(expired),
        "average_confidence": average_confidence,
        "strongest_evidence": [_compact_evidence(item) for item in strongest],
        "generated_at": now_value,
    }


def build_decision_packet(
    *,
    target_scope: Any,
    symbols: list[Any] | None,
    evidence: list[EvidenceItem],
    scorecards: list[dict[str, Any]],
    active_policies: list[dict[str, Any]],
    max_items: int = 10,
    now: str | None = None,
) -> dict[str, Any]:
    now_value = now or utc_now_iso()
    scope_value = normalize_scope(target_scope)
    symbol_set = {normalize_symbol(symbol) for symbol in symbols or []}
    filtered_evidence = [
        item
        for item in evidence
        if not item.is_expired(now=now_value)
        and item.scope in {scope_value, "global"}
        and (not symbol_set or item.symbol in symbol_set)
    ]
    evidence_limit = max(0, max_items)
    return {
        "target_scope": scope_value,
        "symbols": sorted(symbol_set),
        "policy_mode": "advisory_preference_caution",
        "hard_filters_enabled": False,
        "policy_note": "Policies express preferences, caution, and context; they are not trade bans.",
        "evidence": [
            _compact_evidence(item) for item in _sort_evidence(filtered_evidence)[:evidence_limit]
        ],
        "scorecards": scorecards[:evidence_limit],
        "active_policies": active_policies[:evidence_limit],
        "generated_at": now_value,
    }


def _build_evidence_id(
    *,
    source: str,
    signal_type: str,
    symbol: str,
    scope: str,
    captured_at: str,
    payload: dict[str, Any],
) -> str:
    identity = {
        "captured_at": captured_at,
        "payload": payload,
        "scope": scope,
        "signal_type": signal_type,
        "source": source,
        "symbol": symbol,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"ev_{digest[:16]}"


def _compact_evidence(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "source": item.source,
        "signal_type": item.signal_type,
        "symbol": item.symbol,
        "scope": item.scope,
        "confidence": item.confidence,
        "captured_at": item.captured_at,
        "expires_at": item.expires_at,
        "outcome_status": item.outcome_status,
        "payload": item.payload,
    }


def _sort_evidence(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(
        evidence,
        key=lambda item: (item.confidence, _parse_iso_datetime(item.captured_at)),
        reverse=True,
    )


def _clamp_confidence(confidence: float) -> float:
    return max(0.0, min(1.0, float(confidence)))


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
