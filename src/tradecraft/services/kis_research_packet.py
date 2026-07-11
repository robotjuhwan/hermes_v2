from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from statistics import median
from typing import Any, Protocol

from tradecraft.services.jue_wiki_contract import EvidenceRefV1, WikiClaimV3


FRESH_REPORT_MAX_AGE_DAYS = 30
MIN_SYMBOL_LINK_CONFIDENCE = 0.80
MATERIAL_TARGET_DISPERSION_PCT = 25.0
MATERIAL_NEGATIVE_REVISION_PCT = -10.0
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class KisResearchRepository(Protocol):
    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class KisResearchEvidenceV1:
    report_id: int
    symbol: str
    published_at: str
    broker: str
    rating: str
    target_price: float
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_quotes: tuple[str, ...]
    source_ref: dict[str, str]
    link_confidence: float
    freshness: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KisResearchPacketV2:
    symbol: str
    asset_class: str
    status: str
    entry_support: str
    addition_allowed: bool
    revisions: dict[str, float | str]
    conflict_status: str
    confirmed_facts: tuple[str, ...]
    interpretation: tuple[str, ...]
    missing_data: tuple[str, ...]
    evidence: tuple[KisResearchEvidenceV1, ...]
    version: str = "kis_research_packet_v2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kis_packet_candidate_claims(
    packet: KisResearchPacketV2,
    *,
    artifact_id: str,
) -> tuple[WikiClaimV3, ...]:
    """Convert source-linked packet facts into draft or verified Wiki claims."""
    hashed_evidence = tuple(
        (row, str(row.source_ref.get("pdf_sha256") or "").strip())
        for row in packet.evidence
    )
    evidence_refs = tuple(
        EvidenceRefV1(
            evidence_id=f"naver-report:{row.report_id}",
            source_type="naver_report",
            source_id=str(row.report_id),
            content_hash=content_hash.lower(),
            observed_at=row.published_at,
            source_path=str(row.source_ref.get("pdf_archived_path") or ""),
            hash_origin="source",
        )
        for row, content_hash in hashed_evidence
        if _SHA256_RE.fullmatch(content_hash)
    )
    claims: list[WikiClaimV3] = []
    for index, text in enumerate(packet.confirmed_facts):
        claims.append(
            WikiClaimV3(
                claim_id=f"{artifact_id}:fact:{index}",
                claim_type="fact",
                text=text,
                status="verified" if evidence_refs else "draft",
                scope="kis",
                evidence=evidence_refs,
                symbols=(packet.symbol,),
                provenance_id=artifact_id,
            )
        )
    for claim_type, rows in (
        ("interpretation", packet.interpretation),
        ("hypothesis", packet.missing_data),
    ):
        for index, text in enumerate(rows):
            claims.append(
                WikiClaimV3(
                    claim_id=f"{artifact_id}:{claim_type}:{index}",
                    claim_type=claim_type,
                    text=text,
                    status="draft",
                    scope="kis",
                    evidence=evidence_refs,
                    symbols=(packet.symbol,),
                    provenance_id=artifact_id,
                )
            )
    return tuple(claims)


def _timestamp(value: Any) -> datetime | None:
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


def _text_items(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            text = str(raw.get("quote") or raw.get("text") or raw.get("content") or "")
        else:
            text = str(raw or "")
        text = " ".join(text.split()).strip()
        if text and text not in items:
            items.append(text[:300])
        if len(items) >= limit:
            break
    return tuple(items)


def _target_price(facts: dict[str, Any]) -> float:
    target = facts.get("target_price")
    if isinstance(target, dict):
        value = target.get("value")
    else:
        value = target
    try:
        return max(float(value or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rating_direction(value: str) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_")
    if normalized in {
        "BUY",
        "STRONG_BUY",
        "OUTPERFORM",
        "OVERWEIGHT",
        "매수",
    }:
        return "bullish"
    if normalized in {"SELL", "UNDERPERFORM", "UNDERWEIGHT", "매도"}:
        return "bearish"
    return "neutral"


def _source_ref(report: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(report.get(key) or "").strip()
        for key in (
            "pdf_sha256",
            "pdf_archived_path",
            "pdf_url",
            "detail_url",
        )
        if str(report.get(key) or "").strip()
    }


def _evidence_from_report(
    *,
    symbol: str,
    report: dict[str, Any],
    facts: dict[str, Any],
    now: datetime,
) -> KisResearchEvidenceV1 | None:
    try:
        report_id = int(report.get("report_id") or 0)
        link_confidence = float(report.get("link_confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    published = _timestamp(report.get("published_at"))
    source_ref = _source_ref(report)
    if (
        report_id <= 0
        or link_confidence < MIN_SYMBOL_LINK_CONFIDENCE
        or published is None
        or published > now
        or not source_ref
    ):
        return None
    age_days = (now - published).total_seconds() / 86_400.0
    freshness = "fresh" if age_days <= FRESH_REPORT_MAX_AGE_DAYS else "stale"
    return KisResearchEvidenceV1(
        report_id=report_id,
        symbol=symbol,
        published_at=published.isoformat(),
        broker=str(report.get("broker") or "").strip(),
        rating=str(facts.get("rating") or "UNKNOWN").strip(),
        target_price=_target_price(facts),
        catalysts=_text_items(facts.get("catalysts"), limit=6),
        risks=_text_items(facts.get("risks"), limit=6),
        evidence_quotes=_text_items(facts.get("evidence_quotes"), limit=8),
        source_ref=source_ref,
        link_confidence=link_confidence,
        freshness=freshness,
    )


def build_kis_research_packet(
    *,
    symbol: str,
    asset_class: str,
    reports: list[dict[str, Any]],
    facts_by_report: dict[int, dict[str, Any]],
    now: str,
) -> KisResearchPacketV2:
    symbol_key = str(symbol or "").strip().upper()
    now_at = _timestamp(now)
    if now_at is None:
        raise ValueError("invalid research packet timestamp")

    evidence_rows: list[KisResearchEvidenceV1] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        try:
            report_id = int(report.get("report_id") or 0)
        except (TypeError, ValueError):
            continue
        facts = facts_by_report.get(report_id)
        if not isinstance(facts, dict):
            continue
        evidence = _evidence_from_report(
            symbol=symbol_key,
            report=report,
            facts=facts,
            now=now_at,
        )
        if evidence is not None:
            evidence_rows.append(evidence)
    evidence_rows.sort(key=lambda row: row.published_at, reverse=True)
    evidence_rows = evidence_rows[:6]
    fresh_rows = [row for row in evidence_rows if row.freshness == "fresh"]

    revisions: dict[str, float | str] = {}
    if fresh_rows:
        newest = fresh_rows[0]
        previous = next(
            (
                row
                for row in evidence_rows[1:]
                if row.broker == newest.broker and row.target_price > 0
            ),
            None,
        )
        if newest.target_price > 0 and previous is not None:
            revisions["target_price_pct"] = round(
                (newest.target_price - previous.target_price)
                / previous.target_price
                * 100.0,
                6,
            )
        revisions["latest_rating"] = newest.rating

    latest_by_broker: dict[str, KisResearchEvidenceV1] = {}
    for row in fresh_rows:
        latest_by_broker.setdefault(row.broker or f"report:{row.report_id}", row)
    directions = {
        _rating_direction(row.rating) for row in latest_by_broker.values()
    }
    current_targets = [
        row.target_price for row in latest_by_broker.values() if row.target_price > 0
    ]
    target_dispersion_pct = 0.0
    if len(current_targets) >= 2 and median(current_targets) > 0:
        target_dispersion_pct = (
            (max(current_targets) - min(current_targets))
            / median(current_targets)
            * 100.0
        )
    material_conflict = (
        {"bullish", "bearish"}.issubset(directions)
        or target_dispersion_pct >= MATERIAL_TARGET_DISPERSION_PCT
    )
    conflict_status = "material" if material_conflict else "none"

    missing_data: list[str] = []
    if not evidence_rows:
        missing_data.append("traceable_report_evidence_missing")
    elif not fresh_rows:
        missing_data.append("fresh_report_evidence_missing")
    if fresh_rows and not any(row.target_price > 0 for row in fresh_rows):
        missing_data.append("target_price_missing")

    if material_conflict:
        status = "conflicted"
        entry_support = "waiting_entry"
    elif fresh_rows:
        status = "eligible"
        entry_support = "supported"
    else:
        status = "ineligible"
        entry_support = "ineligible"

    target_revision_pct = float(revisions.get("target_price_pct") or 0.0)
    latest_direction = (
        _rating_direction(fresh_rows[0].rating) if fresh_rows else "neutral"
    )
    addition_allowed = (
        status == "eligible"
        and latest_direction != "bearish"
        and target_revision_pct > MATERIAL_NEGATIVE_REVISION_PCT
    )

    confirmed_facts = tuple(
        item
        for row in fresh_rows[:3]
        for item in (
            f"report:{row.report_id}:rating:{row.rating}",
            f"report:{row.report_id}:target:{row.target_price:.0f}"
            if row.target_price > 0
            else "",
        )
        if item
    )
    interpretation: list[str] = []
    if "target_price_pct" in revisions:
        interpretation.append(
            f"same_broker_target_revision_pct:{target_revision_pct:.6f}"
        )
    if material_conflict:
        interpretation.append("fresh_broker_evidence_conflicts")

    return KisResearchPacketV2(
        symbol=symbol_key,
        asset_class=str(asset_class or "stock").strip().lower(),
        status=status,
        entry_support=entry_support,
        addition_allowed=addition_allowed,
        revisions=revisions,
        conflict_status=conflict_status,
        confirmed_facts=confirmed_facts,
        interpretation=tuple(interpretation),
        missing_data=tuple(missing_data),
        evidence=tuple(evidence_rows),
    )


def build_kis_research_packets_for_symbols(
    *,
    repository: KisResearchRepository,
    symbols: list[str],
    asset_classes: dict[str, str] | None,
    now: str,
) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for symbol in dict.fromkeys(str(value or "").strip().upper() for value in symbols):
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        reports = repository.latest_symbol_linked_reports(symbol, limit=12)
        facts_by_report: dict[int, dict[str, Any]] = {}
        for report in reports:
            if not isinstance(report, dict):
                continue
            try:
                report_id = int(report.get("report_id") or 0)
            except (TypeError, ValueError):
                continue
            facts = repository.get_report_facts(report_id)
            if isinstance(facts, dict):
                facts_by_report[report_id] = facts
        packets[symbol] = build_kis_research_packet(
            symbol=symbol,
            asset_class=str((asset_classes or {}).get(symbol) or "stock"),
            reports=reports,
            facts_by_report=facts_by_report,
            now=now,
        ).to_dict()
    return packets
