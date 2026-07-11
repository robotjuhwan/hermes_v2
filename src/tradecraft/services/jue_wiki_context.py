from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    JueWikiPageV3,
    ReadMode,
    WikiClaimV3,
    WikiContextPacketV1,
    WikiContextRequestV1,
    WikiDecisionGateV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_lint import WikiLintFindingV1, lint_snapshot


_DIRECT_RAW_RAG_KEYS = frozenset(
    {
        "rag",
        "rag_context",
        "raw_rag",
        "raw_reports",
        "retrieved_documents",
        "research_documents",
    }
)
_POSITIVE_CLAIM_TYPES = frozenset({"fact", "interpretation"})
_NON_POSITIVE_STATUSES = frozenset(
    {"stale", "conflicted", "rejected", "superseded"}
)


class _WikiContextRepository(Protocol):
    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None: ...

    def evidence_refs(self) -> dict[str, EvidenceRefV1]: ...


class _WikiEligibilityReader(Protocol):
    def eligibility(self, venue: str) -> dict[str, Any]: ...


class _StoredWikiEligibilityReader:
    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 0.5) -> None:
        self.db_path = Path(db_path)
        self.timeout_seconds = max(min(float(timeout_seconds), 5.0), 0.05)

    def eligibility(self, venue: str) -> dict[str, Any]:
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(
            uri,
            uri=True,
            timeout=self.timeout_seconds,
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            row = conn.execute(
                """
                SELECT *
                FROM wiki_shadow_eligibility_v1
                WHERE venue = ?
                """,
                (venue,),
            ).fetchone()
        if row is None:
            return {}
        values = dict(row)
        blockers: list[str] = ["eligibility_signature_unverified"]
        if int(values.get("complete_sample_count") or 0) < 500:
            blockers.append("insufficient_complete_comparisons")
        if int(values.get("safety_gate_loss_count") or 0):
            blockers.append("safety_gate_divergence")
        if int(values.get("required_raw_rag_path_count") or 0):
            blockers.append("required_mode_raw_rag_path_present")
        if int(values.get("snapshot_trace_gap_count") or 0):
            blockers.append("snapshot_trace_incomplete")
        if int(values.get("outage_new_risk_expansion_count") or 0):
            blockers.append("wiki_outage_new_risk_expansion")
        return {
            **values,
            "venue": str(values.get("venue") or ""),
            "required_eligible": not blockers,
            "blockers": blockers,
            "evaluated_at": str(values.get("updated_at") or ""),
        }


def wiki_eligibility_freshness_reason(
    result: dict[str, Any],
    *,
    now: datetime,
    max_age_seconds: int = 3600,
) -> str:
    evaluated_at = _explicit_timezone_instant(str(result.get("evaluated_at") or ""))
    evaluated_through = _explicit_timezone_instant(
        str(result.get("evaluated_through") or "")
    )
    current = now
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    if evaluated_at is None or evaluated_through is None:
        return "eligibility_timestamp_invalid"
    if evaluated_through > evaluated_at:
        return "eligibility_timestamp_order_invalid"
    if evaluated_at > current:
        return "eligibility_timestamp_future"
    if current - evaluated_at > timedelta(seconds=max(int(max_age_seconds), 1)):
        return "eligibility_stale"
    return ""


def _normalized(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(str(value).strip().lower() for value in values if value.strip())


def _canonical_instant(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw or ("T" not in raw and " " not in raw):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _explicit_timezone_instant(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw or ("T" not in raw and " " not in raw):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _claim_validity(
    claim: WikiClaimV3,
    reference_instant: datetime | None,
) -> tuple[bool, tuple[str, ...]]:
    if reference_instant is None:
        return False, ()
    warnings: list[str] = []
    valid_from = None
    valid_to = None
    if claim.valid_from:
        valid_from = _canonical_instant(claim.valid_from)
        if valid_from is None:
            warnings.append(f"claim_valid_from_invalid:{claim.claim_id}")
    if claim.valid_to:
        valid_to = _canonical_instant(claim.valid_to)
        if valid_to is None:
            warnings.append(f"claim_valid_to_invalid:{claim.claim_id}")
    if warnings:
        return False, tuple(warnings)
    if valid_from is not None and valid_from > reference_instant:
        warnings.append(f"claim_not_yet_valid:{claim.claim_id}")
    if valid_to is not None and reference_instant >= valid_to:
        warnings.append(f"claim_expired:{claim.claim_id}")
    return not warnings, tuple(warnings)


def _canonical_observed_at(value: str) -> tuple[str, str]:
    instant = _canonical_instant(value)
    if instant is None:
        return "invalid", str(value or "").strip()
    return "utc", instant.isoformat(timespec="microseconds")


def _evidence_payload(ref: EvidenceRefV1) -> tuple[str | tuple[str, str], ...]:
    return (
        ref.evidence_id,
        ref.source_type,
        ref.source_id,
        ref.content_hash.strip().lower(),
        _canonical_observed_at(ref.observed_at),
        ref.source_path,
        ref.hash_origin,
    )


def _evidence_mismatch_fields(
    ref: EvidenceRefV1,
    registry_ref: EvidenceRefV1,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    if ref.evidence_id != registry_ref.evidence_id:
        mismatches.append("evidence_id")
    if ref.source_type != registry_ref.source_type:
        mismatches.append("source_type")
    if ref.source_id != registry_ref.source_id:
        mismatches.append("source_id")
    if ref.content_hash.strip().lower() != registry_ref.content_hash.strip().lower():
        mismatches.append("content_hash")
    ref_observed_at = _canonical_instant(ref.observed_at)
    registry_observed_at = _canonical_instant(registry_ref.observed_at)
    if (
        ref_observed_at is None
        or registry_observed_at is None
        or ref_observed_at != registry_observed_at
    ):
        mismatches.append("observed_at")
    if ref.source_path != registry_ref.source_path:
        mismatches.append("source_path")
    if ref.hash_origin != registry_ref.hash_origin:
        mismatches.append("hash_origin")
    return tuple(mismatches)


def _evidence_resolves(
    ref: EvidenceRefV1,
    evidence_refs: dict[str, EvidenceRefV1],
) -> bool:
    registry_ref = evidence_refs.get(ref.evidence_id)
    return registry_ref is not None and not _evidence_mismatch_fields(
        ref,
        registry_ref,
    )


def _page_symbols(page: JueWikiPageV3) -> frozenset[str]:
    return frozenset(
        symbol
        for claim in page.claims
        for symbol in claim.symbols
        if symbol
    )


def _symbol_relation(page: JueWikiPageV3, request: WikiContextRequestV1) -> int:
    requested_symbols = frozenset(request.symbols)
    if not requested_symbols:
        return 0
    page_symbols = _page_symbols(page)
    if page_symbols == requested_symbols:
        return 2
    if page_symbols & requested_symbols:
        return 1
    return 0


def _typed_claim_matches(
    page: JueWikiPageV3,
    requested: tuple[str, ...],
    field: str,
) -> int:
    requested_tokens = _normalized(requested)
    if not requested_tokens:
        return 0
    page_tokens = frozenset(
        token
        for claim in page.claims
        for token in _normalized(tuple(getattr(claim, field)))
    )
    return len(page_tokens & requested_tokens)


def _relationship_relevance(
    page: JueWikiPageV3,
    request: WikiContextRequestV1,
) -> int:
    requested_targets = _normalized(
        (
            *request.symbols,
            *request.lanes,
            *request.regimes,
            *request.block_ids,
            *request.horizons,
        )
    )
    if not requested_targets:
        return 0
    return sum(
        1
        for relationship in page.relationships
        if relationship.target_id.strip().lower() in requested_targets
    )


def _verified_support_claims(
    page: JueWikiPageV3,
    evidence_refs: dict[str, EvidenceRefV1],
    reference_instant: datetime | None,
) -> tuple[WikiClaimV3, ...]:
    return tuple(
        claim
        for claim in page.claims
        if claim.status == "verified"
        and claim.claim_type in _POSITIVE_CLAIM_TYPES
        and _claim_validity(claim, reference_instant)[0]
        and claim.evidence
        and all(
            _evidence_resolves(evidence, evidence_refs)
            for evidence in claim.evidence
        )
    )


def _support_freshness(claims: tuple[WikiClaimV3, ...]) -> float:
    observed_instants = tuple(
        instant
        for claim in claims
        for evidence in claim.evidence
        if (instant := _canonical_instant(evidence.observed_at)) is not None
    )
    if not observed_instants:
        return float("-inf")
    return max(observed_instants).replace(tzinfo=timezone.utc).timestamp()


def _page_rank(
    page: JueWikiPageV3,
    request: WikiContextRequestV1,
    evidence_refs: dict[str, EvidenceRefV1],
    reference_instant: datetime | None,
) -> tuple[int | float | str, ...]:
    requested_page_types = _normalized(request.page_types)
    support = _verified_support_claims(
        page,
        evidence_refs,
        reference_instant,
    )
    confidence = max((claim.confidence for claim in support), default=0.0)
    return (
        -int(page.scope.strip().lower() == request.target_scope.strip().lower()),
        -_symbol_relation(page, request),
        -int(page.page_type.strip().lower() in requested_page_types),
        -_typed_claim_matches(page, request.regimes, "regimes"),
        -_typed_claim_matches(page, request.lanes, "strategies"),
        -_typed_claim_matches(page, request.horizons, "strategies"),
        -_support_freshness(support),
        -confidence,
        -_relationship_relevance(page, request),
        page.page_id,
    )


def _serialized_pages_char_count(pages: tuple[JueWikiPageV3, ...]) -> int:
    return len(
        json.dumps(
            [page.to_dict() for page in pages],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _finding_warning(finding: WikiLintFindingV1) -> str:
    location = finding.page_id
    if finding.claim_id:
        location = f"{location}:{finding.claim_id}" if location else finding.claim_id
    prefix = "lint_error" if finding.severity == "error" else "lint_warning"
    return f"{prefix}:{finding.finding_type}:{location}".rstrip(":")


def _support_status_warnings(
    selected_pages: tuple[JueWikiPageV3, ...],
    requested_symbols: tuple[str, ...],
    covered_symbols: set[str],
) -> set[str]:
    warnings: set[str] = set()
    claims = tuple(claim for page in selected_pages for claim in page.claims)
    for claim in claims:
        if claim.status in _NON_POSITIVE_STATUSES:
            warnings.add(f"{claim.status}_claim:{claim.claim_id}")
    for symbol in requested_symbols:
        if symbol in covered_symbols:
            continue
        statuses = {
            claim.status
            for claim in claims
            if symbol in claim.symbols and claim.status in _NON_POSITIVE_STATUSES
        }
        if len(statuses) == 1:
            warnings.add(f"{next(iter(statuses))}_only_support")
        warnings.add(f"symbol_coverage_missing:{symbol}")
    return warnings


class JueWikiContextService:
    def __init__(
        self,
        repository: _WikiContextRepository,
        *,
        eligibility_reader: _WikiEligibilityReader | None = None,
        health_reader: Callable[[], dict[str, Any]] | None = None,
        eligibility_db_path: str | Path | None = None,
        eligibility_max_age_seconds: int = 3600,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.eligibility_reader = eligibility_reader or (
            _StoredWikiEligibilityReader(eligibility_db_path)
            if eligibility_db_path is not None
            else None
        )
        self.eligibility_max_age_seconds = max(int(eligibility_max_age_seconds), 1)
        self.health_reader = health_reader
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _stored_health_warnings(
        self,
        *,
        venue: str,
        read_mode: ReadMode,
        snapshot_id: str,
    ) -> tuple[str, ...]:
        if self.health_reader is None:
            return (
                ("wiki_health_reader_missing",)
                if read_mode == "required"
                else ()
            )
        try:
            health = self.health_reader()
        except Exception:
            return ("wiki_health_unavailable",)
        if not isinstance(health, dict) or str(
            health.get("status") or ""
        ).lower() in {"error", "missing", "unavailable"}:
            return ("wiki_health_unavailable",)
        v3 = health.get("v3") if isinstance(health.get("v3"), dict) else None
        if v3 is None:
            return ("wiki_health_v3_missing",)
        warnings: list[str] = []
        active_read_mode = str(v3.get("active_read_mode") or "").lower()
        if read_mode == "required" and active_read_mode != "required":
            warnings.append("wiki_health_read_mode_mismatch")
        by_scope = v3.get("by_scope") if isinstance(v3.get("by_scope"), dict) else {}
        scope_health = (
            by_scope.get(venue) if isinstance(by_scope.get(venue), dict) else {}
        )
        if not scope_health:
            return (f"wiki_health_{venue}_scope_missing",)
        if str(scope_health.get("snapshot_id") or "") != snapshot_id:
            warnings.append("wiki_health_snapshot_mismatch")
        created_at_raw = str(scope_health.get("snapshot_created_at") or "").strip()
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
        if created_at is None or created_at.tzinfo is None:
            warnings.append("wiki_health_snapshot_stale")
        else:
            snapshot_age = (self._now() - created_at).total_seconds()
            if snapshot_age < 0 or snapshot_age > 3600:
                warnings.append("wiki_health_snapshot_stale")
        for field, label in (
            ("last_ingest_status", "ingest"),
            ("last_compile_status", "compile"),
            ("last_lint_status", "lint"),
            ("last_publish_status", "publish"),
            ("last_projection_status", "projection"),
        ):
            stage_status = str(scope_health.get(field) or "").lower()
            if (
                field == "last_projection_status"
                and stage_status == "warning"
                and scope_health.get("projection_warning_reason") == "cleanup_only"
            ):
                continue
            if stage_status != "ok":
                warnings.append(
                    f"wiki_health_{label}_{stage_status or 'missing'}"
                )
        for field, label in (
            ("stale_count", "stale_knowledge"),
            ("conflicted_count", "conflicted_knowledge"),
            ("orphan_page_count", "orphan_pages"),
            ("repair_backlog_count", "repair_backlog"),
        ):
            count = scope_health.get(field)
            if type(count) is not int or count < 0:
                warnings.append(f"wiki_health_{label}_invalid")
            elif count > 0:
                warnings.append(f"wiki_health_{label}")
        index_rebuild = (
            scope_health.get("index_rebuild")
            if isinstance(scope_health.get("index_rebuild"), dict)
            else {}
        )
        index_status = str(index_rebuild.get("status") or "").lower()
        if index_status != "ok":
            warnings.append(f"wiki_health_index_{index_status or 'missing'}")
        if read_mode == "required":
            eligibility = (
                v3.get("mode_eligibility")
                if isinstance(v3.get("mode_eligibility"), dict)
                else {}
            )
            venue_row = (
                eligibility.get(venue)
                if isinstance(eligibility.get(venue), dict)
                else {}
            )
            if not venue_row:
                warnings.append(f"wiki_health_{venue}_eligibility_missing")
            elif venue_row.get("required_eligible") is not True:
                warnings.append(f"wiki_health_{venue}_eligibility_degraded")
        return tuple(sorted(set(warnings)))

    def _required_eligible(self, venue: str) -> bool:
        if self.eligibility_reader is None or venue not in {"kis", "binance"}:
            return False
        try:
            result = self.eligibility_reader.eligibility(venue)
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            return False
        if not isinstance(result, dict):
            return False
        if result.get("version") != "wiki_shadow_eligibility_v1":
            return False
        if str(result.get("venue") or "").strip().lower() != venue:
            return False
        if result.get("required_eligible") is not True:
            return False
        sample_value = result.get("complete_sample_count")
        if type(sample_value) is not int or sample_value < 0:
            return False
        sample_count = sample_value
        if sample_count < 500:
            return False
        blockers = result.get("blockers")
        if not isinstance(blockers, list) or blockers:
            return False
        return not wiki_eligibility_freshness_reason(
            result,
            now=self._now(),
            max_age_seconds=self.eligibility_max_age_seconds,
        )

    def context_packet(
        self,
        request: WikiContextRequestV1,
        read_mode: ReadMode,
    ) -> WikiContextPacketV1:
        if read_mode not in {"shadow", "prefer", "required"}:
            raise ValueError(f"unsupported_wiki_read_mode:{read_mode}")
        snapshot = self.repository.current_snapshot(request.target_scope)
        if snapshot is None:
            return WikiContextPacketV1(
                status="missing",
                read_mode=read_mode,
                snapshot_id="",
                selected_pages=(),
                rejected_page_ids=(),
                coverage_status="insufficient",
                quality_warnings=("wiki_snapshot_missing",),
                repair_required=True,
                char_count=_serialized_pages_char_count(()),
            )

        reference_instant = _canonical_instant(snapshot.created_at)
        evidence_refs = self.repository.evidence_refs()
        known_evidence_ids = set(evidence_refs)
        lint_findings = lint_snapshot(
            snapshot,
            known_evidence_ids=known_evidence_ids,
        )
        global_errors = tuple(
            finding
            for finding in lint_findings
            if finding.severity == "error" and not finding.page_id
        )
        claim_owner_ids: dict[str, set[str]] = {}
        for page in snapshot.pages:
            for claim in page.claims:
                claim_owner_ids.setdefault(claim.claim_id, set()).add(page.page_id)
        page_ids = {page.page_id for page in snapshot.pages}
        error_rows_by_page: dict[str, list[WikiLintFindingV1]] = {
            page_id: [] for page_id in page_ids
        }
        for finding in lint_findings:
            if finding.severity != "error" or not finding.page_id:
                continue
            affected_page_ids = {
                finding.page_id,
                *claim_owner_ids.get(finding.claim_id, set()),
                *claim_owner_ids.get(finding.message, set()),
            }
            for page_id in sorted(affected_page_ids & page_ids):
                error_rows_by_page[page_id].append(finding)
        errors_by_page = {
            page_id: tuple(findings)
            for page_id, findings in error_rows_by_page.items()
        }
        ranked_pages = tuple(
            sorted(
                snapshot.pages,
                key=lambda page: _page_rank(
                    page,
                    request,
                    evidence_refs,
                    reference_instant,
                ),
            )
        )
        selected: list[JueWikiPageV3] = []
        rejected: list[str] = []
        warnings = {_finding_warning(finding) for finding in global_errors}
        if reference_instant is None:
            warnings.add("snapshot_created_at_invalid")
        evidence_rejections_by_page: dict[str, set[str]] = {
            page.page_id: set() for page in snapshot.pages
        }
        evidence_payloads: dict[
            str,
            dict[tuple[str | tuple[str, str], ...], set[str]],
        ] = {}
        for page in snapshot.pages:
            for claim in page.claims:
                for evidence in claim.evidence:
                    registry_ref = evidence_refs.get(evidence.evidence_id)
                    if claim.status == "verified" and registry_ref is not None:
                        for field in _evidence_mismatch_fields(
                            evidence,
                            registry_ref,
                        ):
                            evidence_rejections_by_page[page.page_id].add(
                                f"evidence_mismatch:{evidence.evidence_id}:{field}"
                            )
                    evidence_payloads.setdefault(evidence.evidence_id, {}).setdefault(
                        _evidence_payload(evidence),
                        set(),
                    ).add(page.page_id)
        for evidence_id, payload_owners in evidence_payloads.items():
            if len(payload_owners) <= 1:
                continue
            collision_warning = f"evidence_payload_collision:{evidence_id}"
            for owner_ids in payload_owners.values():
                for page_id in owner_ids:
                    evidence_rejections_by_page[page_id].add(collision_warning)
        char_count = _serialized_pages_char_count(())
        for page in ranked_pages:
            page_errors = (*global_errors, *errors_by_page[page.page_id])
            evidence_rejections = evidence_rejections_by_page[page.page_id]
            if page_errors or evidence_rejections:
                rejected.append(page.page_id)
                warnings.update(_finding_warning(finding) for finding in page_errors)
                warnings.update(evidence_rejections)
                continue
            prospective_pages = (*selected, page)
            prospective_char_count = _serialized_pages_char_count(prospective_pages)
            if prospective_char_count > request.max_chars:
                rejected.append(page.page_id)
                warnings.add(f"page_budget_exceeded:{page.page_id}")
                continue
            selected.append(page)
            char_count = prospective_char_count

        selected_pages = tuple(selected)
        selected_page_ids = {page.page_id for page in selected_pages}
        warnings.update(
            _finding_warning(finding)
            for finding in lint_findings
            if finding.severity == "warning" and finding.page_id in selected_page_ids
        )
        support_claims = tuple(
            claim
            for page in selected_pages
            for claim in _verified_support_claims(
                page,
                evidence_refs,
                reference_instant,
            )
        )
        warnings.update(
            warning
            for page in selected_pages
            for claim in page.claims
            for warning in _claim_validity(claim, reference_instant)[1]
        )
        covered_symbols = {
            symbol
            for claim in support_claims
            for symbol in claim.symbols
            if symbol in request.symbols
        }
        warnings.update(
            _support_status_warnings(
                selected_pages,
                request.symbols,
                covered_symbols,
            )
        )
        requested_symbols_covered = all(
            symbol in covered_symbols for symbol in request.symbols
        )
        coverage_sufficient = bool(support_claims) and requested_symbols_covered
        coverage_status = "sufficient" if coverage_sufficient else "insufficient"
        if not support_claims:
            warnings.add("current_verified_support_missing")
        health_warnings = self._stored_health_warnings(
            venue=request.target_scope.strip().lower(),
            read_mode=read_mode,
            snapshot_id=snapshot.snapshot_id,
        )
        warnings.update(health_warnings)

        return WikiContextPacketV1(
            status="ok",
            read_mode=read_mode,
            snapshot_id=snapshot.snapshot_id,
            selected_pages=selected_pages,
            rejected_page_ids=tuple(sorted(set(rejected))),
            coverage_status=coverage_status,
            quality_warnings=tuple(sorted(warnings)),
            repair_required=not coverage_sufficient,
            char_count=char_count,
            required_eligible=(
                self._required_eligible(request.target_scope.strip().lower())
                and not health_warnings
            ),
        )


def evaluate_wiki_promotion_gate(
    *,
    venue: str,
    playbook_type: str,
    promotion_thresholds: dict[str, dict[str, int]],
    fill_proven_closed_sample_count: int,
    cost_attribution_complete: bool,
    policy_review_approved: bool,
) -> dict[str, Any]:
    clean_venue = str(venue or "").strip().lower()
    clean_playbook = str(playbook_type or "").strip().lower()
    configured = promotion_thresholds.get(clean_venue, {})
    raw_threshold = configured.get(clean_playbook) if isinstance(configured, dict) else None
    threshold_configured = raw_threshold is not None
    threshold_valid = type(raw_threshold) is int and raw_threshold > 0
    threshold = raw_threshold if threshold_valid else 0
    sample_valid = (
        type(fill_proven_closed_sample_count) is int
        and fill_proven_closed_sample_count >= 0
    )
    sample_count = fill_proven_closed_sample_count if sample_valid else 0
    if not threshold_configured:
        reason = "promotion_threshold_unconfigured"
    elif not threshold_valid:
        reason = "promotion_threshold_invalid"
    elif not sample_valid:
        reason = "fill_proven_closed_sample_invalid"
    elif type(cost_attribution_complete) is not bool:
        reason = "cost_attribution_invalid"
    elif type(policy_review_approved) is not bool:
        reason = "policy_review_invalid"
    elif sample_count < threshold:
        reason = "fill_proven_closed_sample_insufficient"
    elif cost_attribution_complete is not True:
        reason = "cost_attribution_incomplete"
    elif policy_review_approved is not True:
        reason = "policy_review_required"
    else:
        reason = "promotion_acceptance_gates_passed"
    return {
        "version": "wiki_promotion_gate_v1",
        "venue": clean_venue,
        "playbook_type": clean_playbook,
        "automatic_promotion_allowed": reason == "promotion_acceptance_gates_passed",
        "reason": reason,
        "configured_threshold": threshold,
        "fill_proven_closed_sample_count": sample_count,
        "cost_attribution_complete": cost_attribution_complete is True,
        "policy_review_approved": policy_review_approved is True,
    }


def evaluate_wiki_decision_gate(packet: WikiContextPacketV1) -> WikiDecisionGateV1:
    if packet.read_mode != "required":
        return WikiDecisionGateV1(
            allow_new_risk=True,
            allow_exit_actions=True,
            reason="wiki_context_advisory",
            read_mode=packet.read_mode,
            snapshot_id=packet.snapshot_id,
        )
    if packet.coverage_status != "sufficient":
        return WikiDecisionGateV1(
            allow_new_risk=False,
            allow_exit_actions=True,
            reason="wiki_required_coverage_missing",
            read_mode=packet.read_mode,
            snapshot_id=packet.snapshot_id,
        )
    if not packet.required_eligible:
        return WikiDecisionGateV1(
            allow_new_risk=False,
            allow_exit_actions=True,
            reason="wiki_required_mode_ineligible",
            read_mode=packet.read_mode,
            snapshot_id=packet.snapshot_id,
        )
    return WikiDecisionGateV1(
        allow_new_risk=True,
        allow_exit_actions=True,
        reason="wiki_context_eligible",
        read_mode=packet.read_mode,
        snapshot_id=packet.snapshot_id,
    )


def strip_direct_raw_rag_context(
    prompt: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    removed_paths: list[str] = []

    def copy_value(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            copied: dict[Any, Any] = {}
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if (
                    isinstance(child, dict)
                    and child.get("source_contract") == "raw_rag"
                ):
                    removed_paths.append(child_path)
                    continue
                copied[key] = copy_value(child, child_path)
            return copied
        if isinstance(value, list):
            copied_list: list[Any] = []
            for index, child in enumerate(value):
                child_path = f"{path}[{index}]"
                if isinstance(child, dict) and child.get("source_contract") == "raw_rag":
                    removed_paths.append(child_path)
                    continue
                copied_list.append(copy_value(child, child_path))
            return copied_list
        if isinstance(value, tuple):
            copied_tuple: list[Any] = []
            for index, child in enumerate(value):
                child_path = f"{path}[{index}]"
                if isinstance(child, dict) and child.get("source_contract") == "raw_rag":
                    removed_paths.append(child_path)
                    continue
                copied_tuple.append(copy_value(child, child_path))
            return tuple(copied_tuple)
        return value

    copied_prompt: dict[str, Any] = {}
    for key, value in prompt.items():
        if key in _DIRECT_RAW_RAG_KEYS:
            removed_paths.append(key)
            continue
        if isinstance(value, dict) and value.get("source_contract") == "raw_rag":
            removed_paths.append(key)
            continue
        copied_prompt[key] = copy_value(value, key)
    return copied_prompt, tuple(sorted(removed_paths))
