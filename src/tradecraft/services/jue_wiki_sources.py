from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote

from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    WikiClaimV3,
    WikiRelationshipV1,
)
from tradecraft.services.jue_wiki_repository import JueWikiRepository
from tradecraft.services.kis_research_packet import (
    KisResearchRepository,
    build_kis_research_packet,
    kis_packet_candidate_claims,
)


NAVER_EXTRACTOR_VERSION = "jue_wiki_naver_v1"
CRYPTO_EXTRACTOR_VERSION = "jue_wiki_crypto_v1"
_SELF_ID = "__candidate_artifact_id__"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CRYPTO_TABLES = (
    "crypto_symbol_notes",
    "crypto_candidates",
    "crypto_features",
)
_CRYPTO_COLUMNS: dict[str, tuple[str, ...]] = {
    "crypto_symbol_notes": (
        "symbol",
        "stance",
        "horizon",
        "confidence",
        "summary_md",
        "reasons_json",
        "risks_json",
        "triggers_json",
        "updated_at",
        "content_hash",
        "sha256",
        "payload_sha256",
    ),
    "crypto_candidates": (
        "symbol",
        "market",
        "stance",
        "horizon",
        "score",
        "confidence",
        "reason_md",
        "block_template_json",
        "source_run_id",
        "updated_at",
        "content_hash",
        "sha256",
        "payload_sha256",
    ),
    "crypto_features": (
        "symbol",
        "feature_json",
        "score",
        "regime",
        "updated_at",
        "content_hash",
        "sha256",
        "payload_sha256",
    ),
}
_CRYPTO_IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "crypto_symbol_notes": ("symbol",),
    "crypto_candidates": ("symbol", "market", "stance", "horizon"),
    "crypto_features": ("symbol",),
}


class JueWikiBackfillError(RuntimeError):
    pass


class JueWikiSourceSchemaError(RuntimeError):
    pass


class JueWikiSourceDataError(RuntimeError):
    pass


def _normalize_finite_numbers(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {
            key: _normalize_finite_numbers(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_finite_numbers(item) for item in value]
    return value


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        _normalize_finite_numbers(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _source_hash(payload: dict[str, Any]) -> tuple[str, str]:
    for key in ("content_hash", "sha256", "pdf_sha256", "payload_sha256"):
        value = str(payload.get(key) or "").strip()
        if _SHA256_RE.fullmatch(value):
            return value.lower(), "source"
    return _sha256(payload), "normalized_payload"


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _replace_self_reference(value: str, artifact_id: str) -> str:
    return value.replace(_SELF_ID, artifact_id)


def _candidate_artifact(
    *,
    scope: str,
    extractor_version: str,
    input_payload: Any,
    source_refs: tuple[EvidenceRefV1, ...],
    claims: tuple[WikiClaimV3, ...],
    created_at: str,
    model: str,
    prompt_hash: str,
    config_hash: str,
    relationships: tuple[WikiRelationshipV1, ...] = (),
) -> CandidateArtifactV1:
    input_hash = _sha256({"scope": scope, "payload": input_payload})
    artifact_id = _sha256(
        {
            "scope": scope,
            "input_hash": input_hash,
            "source_refs": [row.to_dict() for row in source_refs],
            "claims": [row.to_dict() for row in claims],
            "relationships": [row.to_dict() for row in relationships],
            "extractor_version": extractor_version,
            "model": model,
            "prompt_hash": prompt_hash,
            "config_hash": config_hash,
        }
    )
    final_claims = tuple(
        replace(
            row,
            claim_id=_replace_self_reference(row.claim_id, artifact_id),
            provenance_id=_replace_self_reference(row.provenance_id, artifact_id),
        )
        for row in claims
    )
    final_relationships = tuple(
        replace(
            row,
            source_claim_id=_replace_self_reference(
                row.source_claim_id,
                artifact_id,
            ),
            target_id=_replace_self_reference(row.target_id, artifact_id),
        )
        for row in relationships
    )
    return CandidateArtifactV1(
        artifact_id=artifact_id,
        scope=scope,
        extractor_version=extractor_version,
        input_hash=input_hash,
        source_refs=source_refs,
        claims=final_claims,
        created_at=created_at,
        model=model,
        prompt_hash=prompt_hash,
        config_hash=config_hash,
        relationships=final_relationships,
    )


class NaverWikiSourceAdapter:
    def __init__(
        self,
        repository: KisResearchRepository,
        *,
        extractor_version: str = NAVER_EXTRACTOR_VERSION,
        model: str = "",
        prompt_hash: str = "",
        config_hash: str = "",
        report_limit: int = 12,
    ) -> None:
        self.repository = repository
        self.extractor_version = str(extractor_version)
        self.model = str(model)
        self.prompt_hash = str(prompt_hash)
        self.config_hash = str(config_hash)
        self.report_limit = max(int(report_limit), 1)

    def collect(
        self,
        symbols: Sequence[str],
        observed_at: str,
    ) -> tuple[CandidateArtifactV1, ...]:
        canonical_observed_at = _canonical_timestamp(observed_at)
        if canonical_observed_at is None:
            raise ValueError("invalid source observation timestamp")
        artifacts: list[CandidateArtifactV1] = []
        clean_symbols = sorted(
            {
                str(symbol or "").strip().upper()
                for symbol in symbols
                if str(symbol or "").strip()
            }
        )
        for symbol in clean_symbols:
            reports = self.repository.latest_symbol_linked_reports(
                symbol,
                limit=self.report_limit,
            )
            grouped_reports: dict[tuple[int, str], list[dict[str, Any]]] = {}
            for raw_report in reports:
                if not isinstance(raw_report, dict):
                    continue
                try:
                    report_id = int(raw_report.get("report_id") or 0)
                except (TypeError, ValueError):
                    continue
                linked_symbol = str(raw_report.get("symbol") or symbol).strip().upper()
                if report_id <= 0 or not linked_symbol:
                    continue
                report = dict(raw_report)
                published_at = _canonical_timestamp(
                    report.get("published_at") or canonical_observed_at
                )
                if published_at is None:
                    continue
                report["published_at"] = published_at
                report["link_confidence"] = _safe_float(
                    report.get("link_confidence")
                )
                key = (report_id, linked_symbol)
                grouped_reports.setdefault(key, []).append(report)

            unique_reports: dict[tuple[int, str], dict[str, Any]] = {}
            for key, candidates in grouped_reports.items():
                def rank(report: dict[str, Any]) -> tuple[float, str, str]:
                    return (
                        _safe_float(report.get("link_confidence")),
                        str(report["published_at"]),
                        _canonical_json(report),
                    )

                source_hashes = {
                    value.lower()
                    for report in candidates
                    for value in (str(report.get("pdf_sha256") or "").strip(),)
                    if _SHA256_RE.fullmatch(value)
                }
                if len(source_hashes) > 1:
                    raise JueWikiSourceDataError(
                        f"naver_source_hash_conflict:{key[0]}:{key[1]}"
                    )
                selected = dict(max(candidates, key=rank))
                if source_hashes:
                    agreed_hash = next(iter(source_hashes))
                    valid_hash_rows = [
                        report
                        for report in candidates
                        if str(report.get("pdf_sha256") or "").strip().lower()
                        == agreed_hash
                    ]
                    best_valid_hash_row = max(valid_hash_rows, key=rank)
                    selected["pdf_sha256"] = agreed_hash
                    for source_key in (
                        "pdf_archived_path",
                        "pdf_url",
                        "detail_url",
                    ):
                        if source_key in best_valid_hash_row:
                            selected[source_key] = best_valid_hash_row[source_key]
                        else:
                            selected.pop(source_key, None)
                unique_reports[key] = selected

            normalized_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
            facts_by_report: dict[int, dict[str, Any]] = {}
            for (report_id, _), report in sorted(unique_reports.items()):
                facts = self.repository.get_report_facts(report_id)
                if not isinstance(facts, dict):
                    continue
                normalized_facts = dict(facts)
                normalized_rows.append((report, normalized_facts))
                facts_by_report[report_id] = normalized_facts
            if not normalized_rows:
                continue
            packet = build_kis_research_packet(
                symbol=symbol,
                asset_class=str(normalized_rows[0][0].get("asset_class") or "stock"),
                reports=[row[0] for row in normalized_rows],
                facts_by_report=facts_by_report,
                now=canonical_observed_at,
            )
            source_refs = tuple(
                self._evidence_ref(report=report, facts=facts)
                for report, facts in normalized_rows
            )
            draft_claims = tuple(
                replace(
                    claim,
                    status="verified" if claim.claim_type == "fact" else "draft",
                    evidence=source_refs,
                )
                for claim in kis_packet_candidate_claims(packet, artifact_id=_SELF_ID)
            )
            input_payload = {
                "symbol": symbol,
                "reports": [
                    {"report": report, "facts": facts}
                    for report, facts in normalized_rows
                ],
            }
            artifacts.append(
                _candidate_artifact(
                    scope="kis",
                    extractor_version=self.extractor_version,
                    input_payload=input_payload,
                    source_refs=source_refs,
                    claims=draft_claims,
                    created_at=max(row.observed_at for row in source_refs),
                    model=self.model,
                    prompt_hash=self.prompt_hash,
                    config_hash=self.config_hash,
                )
            )
        return tuple(sorted(artifacts, key=lambda row: row.artifact_id))

    @staticmethod
    def _evidence_ref(
        *,
        report: dict[str, Any],
        facts: dict[str, Any],
    ) -> EvidenceRefV1:
        report_id = int(report.get("report_id") or 0)
        normalized_payload = {"report": report, "facts": facts}
        content_hash, hash_origin = _source_hash(report)
        if hash_origin == "normalized_payload":
            content_hash = _sha256(normalized_payload)
        return EvidenceRefV1(
            evidence_id=f"naver-report:{report_id}",
            source_type="naver_report",
            source_id=str(report_id),
            content_hash=content_hash,
            observed_at=str(report["published_at"]),
            source_path=str(report.get("pdf_archived_path") or ""),
            hash_origin=hash_origin,
        )


def _safe_json(value: Any, *, expected: type[list[Any]] | type[dict[str, Any]]) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


class CryptoWikiSourceAdapter:
    def __init__(
        self,
        db_paths: str | Path | Sequence[str | Path] | Mapping[str, str | Path],
        *,
        extractor_version: str = CRYPTO_EXTRACTOR_VERSION,
        model: str = "",
        prompt_hash: str = "",
        config_hash: str = "",
        max_rows: int = 100,
    ) -> None:
        if isinstance(db_paths, Mapping):
            values = db_paths.values()
        elif isinstance(db_paths, (str, Path)):
            values = (db_paths,)
        else:
            values = db_paths
        self.db_paths = tuple(
            sorted((Path(row).resolve() for row in values), key=str)
        )
        self.extractor_version = str(extractor_version)
        self.model = str(model)
        self.prompt_hash = str(prompt_hash)
        self.config_hash = str(config_hash)
        self.max_rows = min(max(int(max_rows), 1), 100)

    def collect(
        self,
        symbols: Sequence[str],
        observed_at: str,
    ) -> tuple[CandidateArtifactV1, ...]:
        canonical_observed_at = _canonical_timestamp(observed_at)
        if canonical_observed_at is None:
            raise ValueError("invalid source observation timestamp")
        clean_symbols = tuple(
            sorted(
                {
                    str(symbol or "").strip().upper()
                    for symbol in symbols
                    if str(symbol or "").strip()
                }
            )
        )
        artifacts_by_evidence_id: dict[str, CandidateArtifactV1] = {}
        version_hashes_by_evidence_id: dict[str, str] = {}
        remaining = self.max_rows
        for path in self.db_paths:
            if remaining <= 0:
                break
            rows, fetched_count = self._read_rows(path, clean_symbols, remaining)
            remaining -= fetched_count
            for row in rows:
                if not str(row.get("symbol") or "").strip():
                    continue
                built = self._artifact_from_row(
                    row,
                    source_path=path,
                    observed_at=canonical_observed_at,
                )
                if built is None:
                    continue
                artifact, version_hash = built
                evidence_id = artifact.source_refs[0].evidence_id
                existing = artifacts_by_evidence_id.get(evidence_id)
                if existing is not None:
                    if version_hashes_by_evidence_id[evidence_id] != version_hash:
                        raise JueWikiSourceDataError(
                            f"crypto_source_identity_collision:{evidence_id}"
                        )
                    continue
                artifacts_by_evidence_id[evidence_id] = artifact
                version_hashes_by_evidence_id[evidence_id] = version_hash
        return tuple(
            sorted(
                artifacts_by_evidence_id.values(),
                key=lambda row: row.artifact_id,
            )
        )

    def _artifact_from_row(
        self,
        row: dict[str, Any],
        *,
        source_path: Path,
        observed_at: str,
    ) -> tuple[CandidateArtifactV1, str] | None:
        payload = dict(row)
        source_type = str(payload.pop("_source_type"))
        source_id = str(payload.pop("_source_id"))
        timestamp_key = next(
            (
                key
                for key in ("updated_at", "captured_at", "promoted_at")
                if str(payload.get(key) or "").strip()
            ),
            "",
        )
        source_observed_at = _canonical_timestamp(
            payload.get(timestamp_key) if timestamp_key else observed_at
        )
        if source_observed_at is None:
            return None
        if timestamp_key:
            payload[timestamp_key] = source_observed_at
        symbol = str(payload.get("symbol") or "").strip().upper()
        content_hash, hash_origin = _source_hash(payload)
        version_hash = _sha256(
            {
                "source_type": source_type,
                "source_id": source_id,
                "content_hash": content_hash,
                "hash_origin": hash_origin,
                "observed_at": source_observed_at,
                "payload": payload,
            }
        )
        evidence = EvidenceRefV1(
            evidence_id=f"{source_type}:{source_id}:{version_hash[:16]}",
            source_type=source_type,
            source_id=source_id,
            content_hash=content_hash,
            observed_at=source_observed_at,
            source_path=str(source_path),
            hash_origin=hash_origin,
        )
        text = self._claim_text(source_type, payload)
        draft_claim = WikiClaimV3(
            claim_id=f"{_SELF_ID}:interpretation:0",
            claim_type="interpretation",
            text=text,
            status="draft",
            scope="binance",
            evidence=(evidence,),
            symbols=(symbol,) if symbol else (),
            venues=(str(payload.get("market") or "binance"),),
            valid_from=source_observed_at,
            confidence=_safe_float(payload.get("confidence")),
            provenance_id=_SELF_ID,
        )
        return (
            _candidate_artifact(
                scope="binance",
                extractor_version=self.extractor_version,
                input_payload=payload,
                source_refs=(evidence,),
                claims=(draft_claim,),
                created_at=source_observed_at,
                model=self.model,
                prompt_hash=self.prompt_hash,
                config_hash=self.config_hash,
            ),
            version_hash,
        )

    @staticmethod
    def _claim_text(source_type: str, row: dict[str, Any]) -> str:
        for key in ("summary_md", "reason_md", "name"):
            text = " ".join(str(row.get(key) or "").split()).strip()
            if text:
                return text
        return f"{source_type}:{_canonical_json(row)}"

    @classmethod
    def _read_rows(
        cls,
        path: Path,
        symbols: tuple[str, ...],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        uri = f"file:{quote(str(path))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            rows: list[dict[str, Any]] = []
            fetched_count = 0
            remaining = min(max(int(limit), 1), 100)
            for table in _CRYPTO_TABLES:
                if table not in tables or remaining <= 0:
                    continue
                table_rows = cls._read_table(
                    conn,
                    table=table,
                    symbols=symbols,
                    limit=remaining,
                )
                rows.extend(table_rows)
                remaining -= len(table_rows)
                fetched_count += len(table_rows)
            return rows, fetched_count
        finally:
            conn.close()

    @classmethod
    def _read_table(
        cls,
        conn: sqlite3.Connection,
        *,
        table: str,
        symbols: tuple[str, ...],
        limit: int,
    ) -> list[dict[str, Any]]:
        available = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        missing = [
            column
            for column in _CRYPTO_IDENTITY_COLUMNS[table]
            if column not in available
        ]
        if missing:
            raise JueWikiSourceSchemaError(
                f"crypto_source_identity_columns_missing:{table}:{','.join(missing)}"
            )
        selected = [column for column in _CRYPTO_COLUMNS[table] if column in available]
        where = ""
        parameters: list[Any] = []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            where = f" WHERE UPPER(symbol) IN ({placeholders})"
            parameters.extend(symbols)
        order_by = ", ".join(_CRYPTO_IDENTITY_COLUMNS[table])
        parameters.append(min(max(int(limit), 1), 100))
        sql = (
            f"SELECT {', '.join(selected)} FROM {table}{where} "
            f"ORDER BY {order_by} LIMIT ?"
        )
        return [cls._normalize_crypto_row(table, dict(row)) for row in conn.execute(sql, parameters)]

    @staticmethod
    def _normalize_crypto_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        symbol = str(normalized.get("symbol") or "").strip().upper()
        normalized["symbol"] = symbol
        for numeric_key in ("confidence", "score"):
            if numeric_key in normalized:
                normalized[numeric_key] = _safe_float(normalized[numeric_key])
        if table == "crypto_symbol_notes":
            normalized["reasons"] = _safe_json(
                normalized.pop("reasons_json", None),
                expected=list,
            )
            normalized["risks"] = _safe_json(
                normalized.pop("risks_json", None),
                expected=list,
            )
            normalized["triggers"] = _safe_json(
                normalized.pop("triggers_json", None),
                expected=list,
            )
            source_type = "crypto_symbol_note"
            source_id = symbol
        elif table == "crypto_candidates":
            normalized["market"] = str(normalized.get("market") or "spot")
            normalized["stance"] = str(normalized.get("stance") or "")
            normalized["horizon"] = str(normalized.get("horizon") or "")
            normalized["block_template"] = _safe_json(
                normalized.pop("block_template_json", None),
                expected=dict,
            )
            source_type = "crypto_candidate"
            source_id = ":".join(
                str(normalized[key])
                for key in ("symbol", "market", "stance", "horizon")
            )
        else:
            normalized["features"] = _safe_json(
                normalized.pop("feature_json", None),
                expected=dict,
            )
            source_type = "crypto_feature"
            source_id = symbol
        normalized["_source_type"] = source_type
        normalized["_source_id"] = source_id
        return normalized


@dataclass(frozen=True, slots=True)
class WikiBackfillBatchV1:
    scope: str
    input_cursor: str
    next_cursor: str
    artifact_ids: tuple[str, ...]
    source_count: int
    dry_run: bool


class WikiBackfillSource(Protocol):
    def read_after(
        self,
        *,
        scope: str,
        cursor: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str]: ...


class WikiBackfillCheckpointStore(Protocol):
    def read(self, *, scope: str) -> str: ...

    def store(self, *, scope: str, cursor: str) -> None: ...


class SQLiteWikiBackfillCheckpointStore:
    def __init__(
        self,
        repository: JueWikiRepository,
        *,
        forbidden_source_paths: Sequence[str | Path],
    ) -> None:
        self.db_path = Path(repository.db_path).resolve()
        forbidden = {Path(path).resolve() for path in forbidden_source_paths}
        if self.db_path in forbidden:
            raise JueWikiBackfillError("backfill_checkpoint_source_overlap")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_backfill_checkpoints_v1 (
                    scope TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def read(self, *, scope: str) -> str:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT cursor FROM wiki_backfill_checkpoints_v1 WHERE scope = ?",
                (scope,),
            ).fetchone()
        return str(row[0]) if row is not None else ""

    def store(self, *, scope: str, cursor: str) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT cursor FROM wiki_backfill_checkpoints_v1 WHERE scope = ?",
                (scope,),
            ).fetchone()
            if row is not None and cursor < str(row[0]):
                raise JueWikiBackfillError("backfill_checkpoint_regression")
            conn.execute(
                """
                INSERT INTO wiki_backfill_checkpoints_v1 (scope, cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (scope, cursor, updated_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _backfill_row_identity(row: dict[str, Any]) -> str:
    evidence_id = str(row.get("evidence_id") or "").strip()
    if evidence_id:
        return evidence_id
    source_type = str(row.get("source_type") or "").strip()
    source_id = str(
        row.get("source_id")
        or row.get("report_id")
        or row.get("id")
        or ""
    ).strip()
    if not source_id:
        raise JueWikiBackfillError("backfill_source_identity_required")
    return f"{source_type}:{source_id}" if source_type else source_id


class JueWikiBackfillService:
    def __init__(
        self,
        *,
        source: WikiBackfillSource,
        artifact_builder: Callable[[dict[str, Any]], CandidateArtifactV1],
        repository: JueWikiRepository,
        checkpoint_store: WikiBackfillCheckpointStore | None = None,
    ) -> None:
        self.source = source
        self.artifact_builder = artifact_builder
        self.repository = repository
        self.checkpoint_store = checkpoint_store

    def run(
        self,
        *,
        scope: str,
        cursor: str,
        limit: int = 100,
        dry_run: bool = True,
    ) -> WikiBackfillBatchV1:
        if not dry_run and self.checkpoint_store is None:
            raise JueWikiBackfillError("backfill_checkpoint_store_required")
        if not dry_run:
            if self.checkpoint_store is None:
                raise JueWikiBackfillError("backfill_checkpoint_store_required")
            if self.checkpoint_store.read(scope=scope) != cursor:
                raise JueWikiBackfillError("backfill_cursor_mismatch")
        bounded_limit = min(max(int(limit), 1), 100)
        rows, _provider_cursor = self.source.read_after(
            scope=scope,
            cursor=cursor,
            limit=bounded_limit,
        )
        identified_rows = [(_backfill_row_identity(row), row) for row in rows]
        identities = [identity for identity, _ in identified_rows]
        if identities != sorted(identities):
            raise JueWikiBackfillError("backfill_source_order_invalid")
        if any(identity <= cursor for identity, _ in identified_rows):
            raise JueWikiBackfillError("backfill_source_cursor_not_addressable")
        unique_rows: dict[str, dict[str, Any]] = {}
        for identity, row in identified_rows:
            existing = unique_rows.get(identity)
            if existing is not None:
                if _canonical_json(existing) != _canonical_json(row):
                    raise JueWikiBackfillError(
                        f"backfill_source_identity_collision:{identity}"
                    )
                continue
            unique_rows[identity] = row
        batch_rows = list(unique_rows.items())[:bounded_limit]
        artifacts = tuple(self.artifact_builder(row) for _, row in batch_rows)
        next_cursor = batch_rows[-1][0] if batch_rows else cursor
        if not dry_run:
            for artifact in artifacts:
                self.repository.store_candidate(artifact)
            if self.checkpoint_store is None:
                raise JueWikiBackfillError("backfill_checkpoint_store_required")
            self.checkpoint_store.store(scope=scope, cursor=next_cursor)
        return WikiBackfillBatchV1(
            scope=scope,
            input_cursor=cursor,
            next_cursor=next_cursor,
            artifact_ids=tuple(row.artifact_id for row in artifacts),
            source_count=len(batch_rows),
            dry_run=bool(dry_run),
        )
