from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import pickle
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RAGStoreConfig:
    persist_path: str
    collection_name: str = "naver_reports"
    sync_batch_size: int = 512
    skip_existing: bool = True
    query_oversample_factor: int = 4
    hnsw_space: str = "cosine"
    allow_legacy_pickle_migration: bool = False


_LOW_VALUE_RAG_MARKERS = (
    "compliance notice",
    "본 조사분석자료",
    "금융투자분석사",
    "재산적 이해관계",
    "사전 제공한 사실",
    "보장할 수 없",
    "고객 불편사항",
    "family center",
)


def _is_low_value_rag_chunk(content: str) -> bool:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return True
    informative_chars = re.findall(r"[0-9A-Za-z가-힣]", text)
    if not informative_chars:
        return True
    lowered = text.lower()
    marker_count = sum(1 for marker in _LOW_VALUE_RAG_MARKERS if marker in lowered)
    return marker_count >= 2


class RAGStore:
    def __init__(self, config: RAGStoreConfig) -> None:
        self.config = config
        self._reset_runtime_state()
        self._initialize()

    def _reset_runtime_state(self) -> None:
        self._available = False
        self._client = None
        self._collection = None
        self._init_error = ""
        self._repair_result: dict[str, Any] = {}
        self._last_query_error = ""

    def _initialize(self) -> None:
        try:
            import chromadb  # type: ignore
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            return

        try:
            path = Path(self.config.persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.config.allow_legacy_pickle_migration:
                self._migrate_legacy_hnsw_metadata(path)
            self._repair_result = self._repair_legacy_collection_config(
                path,
                collection_name=self.config.collection_name,
            )
            client_kwargs: dict[str, Any] = {"path": str(path)}
            settings = self._chromadb_settings_for_client(chromadb.PersistentClient)
            if settings is not None:
                client_kwargs["settings"] = settings
            self._client = chromadb.PersistentClient(**client_kwargs)
            self._collection = self._resolve_collection()
            self._available = True
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _next_rebuild_backup_path(path: Path) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = path.with_name(f"{path.name}.rebuild-backup-{timestamp}")
        if not base.exists():
            return base
        for index in range(1, 1000):
            candidate = path.with_name(f"{base.name}-{index}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"could not allocate backup path for {path}")

    def rebuild_persistent_store(self) -> dict[str, Any]:
        path = Path(self.config.persist_path)
        backup_path: Path | None = None
        moved_existing = False
        self._client = None
        self._collection = None
        self._available = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup_path = self._next_rebuild_backup_path(path)
                shutil.move(str(path), str(backup_path))
                moved_existing = True
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            return {
                "status": "error",
                "reason": "rag_rebuild_backup_failed",
                "detail": self._init_error,
                "persist_path": str(path),
                "backup_path": str(backup_path or ""),
                "moved_existing": moved_existing,
            }

        self._reset_runtime_state()
        self._initialize()
        status = self.status()
        ok = bool(status.get("available")) and str(status.get("status") or "ok") != "error"
        return {
            "status": "ok" if ok else "error",
            "reason": "" if ok else "rag_rebuild_init_failed",
            "persist_path": str(path),
            "backup_path": str(backup_path or ""),
            "moved_existing": moved_existing,
            "store_status": status,
        }

    @staticmethod
    def _chromadb_settings_for_client(client_factory: Any) -> Any | None:
        if not RAGStore._persistent_client_accepts_settings(client_factory):
            return None
        try:
            chroma_config = importlib.import_module("chromadb.config")
            settings_cls = getattr(chroma_config, "Settings")
            return settings_cls(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl=(
                    "tradecraft.services.chroma_telemetry.NoOpProductTelemetry"
                ),
            )
        except Exception:
            return None

    @staticmethod
    def _persistent_client_accepts_settings(client_factory: Any) -> bool:
        try:
            signature = inspect.signature(client_factory)
        except (TypeError, ValueError):
            return False
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            or name == "settings"
            for name, parameter in signature.parameters.items()
        )

    @staticmethod
    def _migrate_legacy_hnsw_metadata(path: Path) -> int:
        try:
            from chromadb.segment.impl.vector.local_persistent_hnsw import (  # type: ignore
                PersistentData,
            )
        except Exception:
            return 0

        segment_dimensions = RAGStore._hnsw_segment_dimensions(path)
        migrated = 0
        for metadata_path in path.glob("*/index_metadata.pickle"):
            try:
                payload = metadata_path.read_bytes()
                raw = pickle.loads(payload)
            except Exception:
                continue
            segment_dimension = segment_dimensions.get(metadata_path.parent.name)
            if not isinstance(raw, dict):
                if (
                    segment_dimension is not None
                    and isinstance(raw, PersistentData)
                    and getattr(raw, "dimensionality", None) is None
                ):
                    try:
                        raw.dimensionality = segment_dimension
                        metadata_path.write_bytes(pickle.dumps(raw))
                        migrated += 1
                    except Exception:
                        continue
                continue
            if "dimensionality" not in raw:
                continue
            try:
                dimensionality = raw.get("dimensionality")
                if dimensionality is None:
                    dimensionality = segment_dimension
                if dimensionality is not None:
                    dimensionality = int(dimensionality)
                persistent = PersistentData(
                    dimensionality=dimensionality,
                    total_elements_added=int(raw.get("total_elements_added") or 0),
                    id_to_label=dict(raw.get("id_to_label") or {}),
                    label_to_id=dict(raw.get("label_to_id") or {}),
                    id_to_seq_id=dict(raw.get("id_to_seq_id") or {}),
                )
                persistent.max_seq_id = raw.get("max_seq_id")
                backup_path = metadata_path.with_name(
                    f"{metadata_path.name}.legacy-dict.bak"
                )
                if not backup_path.exists():
                    backup_path.write_bytes(payload)
                metadata_path.write_bytes(pickle.dumps(persistent))
                migrated += 1
            except Exception:
                continue
        return migrated

    @staticmethod
    def _hnsw_segment_dimensions(path: Path) -> dict[str, int]:
        db_path = path / "chroma.sqlite3"
        if not db_path.exists():
            return {}
        try:
            with sqlite3.connect(db_path) as conn:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                if "segments" not in tables or "collections" not in tables:
                    return {}
                rows = conn.execute(
                    """
                    SELECT s.id, c.dimension
                    FROM segments s
                    JOIN collections c ON c.id = s.collection
                    WHERE s.scope = 'VECTOR'
                      AND s.type = 'urn:chroma:segment/vector/hnsw-local-persisted'
                      AND c.dimension IS NOT NULL
                    """
                ).fetchall()
        except sqlite3.Error:
            return {}
        dimensions: dict[str, int] = {}
        for segment_id, dimension in rows:
            try:
                dimensions[str(segment_id)] = int(dimension)
            except (TypeError, ValueError):
                continue
        return dimensions

    @staticmethod
    def _default_collection_config(*, hnsw_space: str) -> dict[str, Any]:
        space = str(hnsw_space or "l2").strip().lower()
        if space not in {"l2", "cosine", "ip"}:
            space = "l2"
        return {
            "hnsw_configuration": {
                "space": space,
                "ef_construction": 100,
                "ef_search": 10,
                "num_threads": 8,
                "M": 16,
                "resize_factor": 1.2,
                "batch_size": 100,
                "sync_threshold": 1000,
                "_type": "HNSWConfigurationInternal",
            },
            "_type": "CollectionConfigurationInternal",
        }

    @classmethod
    def _repair_legacy_collection_config(
        cls,
        path: Path,
        *,
        collection_name: str,
    ) -> dict[str, Any]:
        db_path = path / "chroma.sqlite3"
        if not db_path.exists():
            return {"status": "skipped", "reason": "sqlite_missing"}

        try:
            with sqlite3.connect(db_path) as conn:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(collections)")
                }
                if "config_json_str" not in columns:
                    return {"status": "skipped", "reason": "config_column_missing"}

                rows = conn.execute(
                    """
                    SELECT id, name, config_json_str
                    FROM collections
                    WHERE name = ?
                    """,
                    (collection_name,),
                ).fetchall()
                if not rows:
                    return {"status": "skipped", "reason": "collection_missing"}

                repairs: list[tuple[str, str]] = []
                segment_metadata_repaired = 0
                for collection_id, name, raw_config in rows:
                    try:
                        parsed = json.loads(str(raw_config or "{}"))
                    except json.JSONDecodeError:
                        parsed = {}
                    hnsw_space = (
                        conn.execute(
                            """
                            SELECT str_value
                            FROM collection_metadata
                            WHERE collection_id = ? AND key = 'hnsw:space'
                            """,
                            (collection_id,),
                        ).fetchone()
                        if "collection_metadata" in tables
                        else None
                    )
                    segment_metadata_repaired += (
                        cls._repair_legacy_hnsw_segment_metadata(
                            conn,
                            collection_id=str(collection_id),
                            hnsw_space=str(hnsw_space[0] if hnsw_space else "l2"),
                        )
                    )
                    if isinstance(parsed, dict) and parsed.get("_type"):
                        continue
                    config_json = json.dumps(
                        cls._default_collection_config(
                            hnsw_space=str(hnsw_space[0] if hnsw_space else "l2"),
                        ),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    repairs.append((config_json, str(name)))

                if not repairs:
                    return {
                        "status": "ok",
                        "repaired": 0,
                        "segment_metadata_repaired": segment_metadata_repaired,
                    }

                backup_path = db_path.with_suffix(
                    f"{db_path.suffix}.legacy-config.bak"
                )
                if not backup_path.exists():
                    shutil.copy2(db_path, backup_path)

                for config_json, name in repairs:
                    conn.execute(
                        """
                        UPDATE collections
                        SET config_json_str = ?
                        WHERE name = ?
                        """,
                        (config_json, name),
                    )
                conn.commit()
                return {
                    "status": "ok",
                    "repaired": len(repairs),
                    "segment_metadata_repaired": segment_metadata_repaired,
                    "backup_path": str(backup_path),
                }
        except Exception as exc:
            return {
                "status": "error",
                "reason": "legacy_collection_config_repair_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _repair_legacy_hnsw_segment_metadata(
        conn: sqlite3.Connection,
        *,
        collection_id: str,
        hnsw_space: str,
    ) -> int:
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(segment_metadata)")
            }
        except sqlite3.Error:
            return 0
        if not columns:
            return 0
        has_bool = "bool_value" in columns
        value_columns = (
            "segment_id, key, str_value, int_value, float_value, bool_value"
            if has_bool
            else "segment_id, key, str_value, int_value, float_value"
        )
        placeholders = "?, ?, ?, ?, ?, ?" if has_bool else "?, ?, ?, ?, ?"
        segments = conn.execute(
            """
            SELECT id
            FROM segments
            WHERE collection = ?
              AND scope = 'VECTOR'
              AND type = 'urn:chroma:segment/vector/hnsw-local-persisted'
            """,
            (collection_id,),
        ).fetchall()
        if not segments:
            return 0

        metadata: list[tuple[Any, str, Any, Any, Any, Any | None]] = []
        space = str(hnsw_space or "l2").strip().lower()
        if space not in {"l2", "cosine", "ip"}:
            space = "l2"
        base_rows: list[tuple[str, Any, Any, Any]] = [
            ("hnsw:space", space, None, None),
            ("hnsw:construction_ef", None, 100, None),
            ("hnsw:search_ef", None, 10, None),
            ("hnsw:M", None, 16, None),
            ("hnsw:num_threads", None, 8, None),
            ("hnsw:resize_factor", None, None, 1.2),
            ("hnsw:batch_size", None, 100, None),
            ("hnsw:sync_threshold", None, 1000, None),
        ]
        for (segment_id,) in segments:
            for key, str_value, int_value, float_value in base_rows:
                metadata.append(
                    (segment_id, key, str_value, int_value, float_value, None)
                )
        repaired = 0
        for row in metadata:
            existing = conn.execute(
                """
                SELECT 1
                FROM segment_metadata
                WHERE segment_id = ? AND key = ?
                """,
                (row[0], row[1]),
            ).fetchone()
            if existing:
                continue
            values = row if has_bool else row[:5]
            before_changes = conn.total_changes
            conn.execute(
                f"""
                INSERT OR IGNORE INTO segment_metadata ({value_columns})
                VALUES ({placeholders})
                """,
                values,
            )
            if conn.total_changes > before_changes:
                repaired += 1
        return repaired

    def _resolve_collection(self) -> Any:
        if self._client is None:
            raise RuntimeError("chromadb client is not initialized")
        name = self.config.collection_name
        try:
            return self._client.get_collection(name)
        except Exception:
            return self._client.create_collection(
                name=name,
                metadata={"hnsw:space": self.config.hnsw_space},
            )

    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "available": False,
                "reason": "chromadb_not_installed_or_init_failed",
                "detail": self._init_error,
                "persist_path": self.config.persist_path,
                "collection_name": self.config.collection_name,
                "repair": self._repair_result,
            }
        try:
            count = int(self._collection.count())
            count_error = ""
            count_source = "chroma"
        except Exception as exc:
            fallback_count = self._sqlite_embedding_count()
            count = int(fallback_count or 0)
            count_error = f"{type(exc).__name__}: {exc}"
            count_source = "sqlite_fallback" if fallback_count is not None else "error"
        health_status = "ok" if not count_error else "degraded"
        payload = {
            "status": health_status,
            "available": True,
            "persist_path": self.config.persist_path,
            "collection_name": self.config.collection_name,
            "count": count,
            "count_source": count_source,
            "sync_batch_size": max(int(self.config.sync_batch_size), 1),
            "skip_existing": bool(self.config.skip_existing),
            "query_oversample_factor": max(int(self.config.query_oversample_factor), 1),
        }
        if self._repair_result:
            payload["repair"] = self._repair_result
        if self._last_query_error:
            payload["last_query_error"] = self._last_query_error[:500]
        if count_error:
            payload["degradation_reason"] = "chroma_count_failed"
            payload["count_error"] = count_error[:500]
        return payload

    def _sqlite_embedding_count(self) -> int | None:
        db_path = Path(self.config.persist_path) / "chroma.sqlite3"
        if not db_path.exists():
            return None
        try:
            with sqlite3.connect(db_path) as conn:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                if "embeddings" not in tables:
                    return None
                if "segments" in tables and "collections" in tables:
                    row = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM embeddings e
                        JOIN segments s ON s.id = e.segment_id
                        JOIN collections c ON c.id = s.collection
                        WHERE c.name = ?
                        """,
                        (self.config.collection_name,),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None

    def _build_document_payloads(
        self,
        docs: list[dict[str, Any]],
    ) -> tuple[list[str], list[str], list[dict[str, Any]], int]:
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        skipped_low_value = 0
        for row in docs:
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if _is_low_value_rag_chunk(content):
                skipped_low_value += 1
                continue
            report_id = int(row.get("report_id") or 0)
            chunk_index = int(row.get("chunk_index") or 0)
            raw_id = f"{report_id}:{chunk_index}"
            doc_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
            ids.append(doc_id)
            documents.append(content)
            metadatas.append(
                {
                    "doc_id": str(row.get("doc_id") or row.get("pdf_sha256") or ""),
                    "report_id": report_id,
                    "chunk_index": chunk_index,
                    "symbol": str(row.get("symbol") or ""),
                    "category": str(row.get("category") or "unknown"),
                    "title": str(row.get("title") or ""),
                    "broker": str(row.get("broker") or ""),
                    "published_at": str(row.get("published_at") or ""),
                    "page_start": int(row.get("page_start") or 0),
                    "page_end": int(row.get("page_end") or 0),
                    "section_title": str(row.get("section_title") or "unknown"),
                    "pdf_url": str(row.get("pdf_url") or ""),
                    "detail_url": str(row.get("detail_url") or ""),
                    "linked_symbols": str(row.get("linked_symbols") or ""),
                    "linked_names": str(row.get("linked_names") or ""),
                    "linked_asset_classes": str(
                        row.get("linked_asset_classes") or ""
                    ),
                }
            )
        return ids, documents, metadatas, skipped_low_value

    def sync_documents(
        self,
        docs: list[dict[str, Any]],
        *,
        force_update: bool = False,
        prune_missing: bool = False,
    ) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "status": "skipped",
                "reason": "rag_store_unavailable",
                "input_docs": len(docs),
            }

        ids, documents, metadatas, skipped_low_value = self._build_document_payloads(
            docs
        )
        batch_size = max(min(int(self.config.sync_batch_size), 5000), 1)
        prune_result = self._delete_orphan_ids(
            current_ids=set(ids),
            batch_size=batch_size,
        ) if prune_missing and ids else {
            "deleted_orphans": 0,
            "scanned_vectors": 0,
        }
        if prune_missing and not ids:
            prune_result["prune_skipped_reason"] = "empty_input_docs"
        skipped_existing = 0
        if ids and self.config.skip_existing and not force_update:
            existing_ids = self._existing_ids(ids, batch_size=batch_size)
            if existing_ids:
                filtered_ids: list[str] = []
                filtered_documents: list[str] = []
                filtered_metadatas: list[dict[str, Any]] = []
                for idx, doc_id in enumerate(ids):
                    if doc_id in existing_ids:
                        skipped_existing += 1
                        continue
                    filtered_ids.append(doc_id)
                    filtered_documents.append(documents[idx])
                    filtered_metadatas.append(metadatas[idx])
                ids = filtered_ids
                documents = filtered_documents
                metadatas = filtered_metadatas

        if not ids:
            return {
                "status": "ok",
                "synced": 0,
                "input_docs": len(docs),
                "skipped_existing": skipped_existing,
                "skipped_low_value": skipped_low_value,
                "batches": 0,
                "batch_size": batch_size,
                "force_update": bool(force_update),
                **prune_result,
            }

        batch_count = 0
        synced = 0
        upsert_retries = 0
        upsert_retry_errors: list[str] = []
        for start in range(0, len(ids), batch_size):
            end = start + batch_size
            upsert_result = self._upsert_documents_resilient(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )
            synced += int(upsert_result.get("synced") or 0)
            batch_count += int(upsert_result.get("batches") or 0)
            upsert_retries += int(upsert_result.get("retries") or 0)
            upsert_retry_errors.extend(
                str(item)
                for item in list(upsert_result.get("retry_errors") or [])
                if str(item)
            )
            if str(upsert_result.get("status") or "") != "ok":
                return {
                    "status": "error",
                    "reason": "rag_upsert_failed",
                    "detail": str(upsert_result.get("detail") or "upsert failed"),
                    "failed_ids": list(upsert_result.get("failed_ids") or []),
                    "synced": synced,
                    "input_docs": len(docs),
                    "skipped_existing": skipped_existing,
                    "skipped_low_value": skipped_low_value,
                    "batches": batch_count,
                    "batch_size": batch_size,
                    "force_update": bool(force_update),
                    "upsert_retries": upsert_retries,
                    "upsert_retry_errors": upsert_retry_errors[:5],
                    **prune_result,
                }
        result = {
            "status": "ok",
            "synced": synced,
            "input_docs": len(docs),
            "skipped_existing": skipped_existing,
            "skipped_low_value": skipped_low_value,
            "batches": batch_count,
            "batch_size": batch_size,
            "force_update": bool(force_update),
            **prune_result,
        }
        if upsert_retries:
            result["upsert_retries"] = upsert_retries
            result["upsert_retry_errors"] = upsert_retry_errors[:5]
        return result

    def _upsert_documents_resilient(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._collection is None:
            return {
                "status": "error",
                "detail": "collection missing",
                "failed_ids": ids,
                "synced": 0,
                "batches": 0,
                "retries": 0,
                "retry_errors": [],
            }
        try:
            self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            return {
                "status": "ok",
                "synced": len(ids),
                "batches": 1,
                "retries": 0,
                "retry_errors": [],
            }
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if len(ids) <= 1:
                return {
                    "status": "error",
                    "detail": detail,
                    "failed_ids": ids,
                    "synced": 0,
                    "batches": 0,
                    "retries": 1,
                    "retry_errors": [detail],
                }
            midpoint = max(len(ids) // 2, 1)
            left = self._upsert_documents_resilient(
                ids=ids[:midpoint],
                documents=documents[:midpoint],
                metadatas=metadatas[:midpoint],
            )
            right = self._upsert_documents_resilient(
                ids=ids[midpoint:],
                documents=documents[midpoint:],
                metadatas=metadatas[midpoint:],
            )
            retry_errors = [detail]
            retry_errors.extend(list(left.get("retry_errors") or []))
            retry_errors.extend(list(right.get("retry_errors") or []))
            synced = int(left.get("synced") or 0) + int(right.get("synced") or 0)
            batches = int(left.get("batches") or 0) + int(right.get("batches") or 0)
            retries = 1 + int(left.get("retries") or 0) + int(right.get("retries") or 0)
            if str(left.get("status") or "") == "ok" and str(right.get("status") or "") == "ok":
                return {
                    "status": "ok",
                    "synced": synced,
                    "batches": batches,
                    "retries": retries,
                    "retry_errors": retry_errors,
                }
            failed_ids = list(left.get("failed_ids") or []) + list(
                right.get("failed_ids") or []
            )
            return {
                "status": "error",
                "detail": detail,
                "failed_ids": failed_ids,
                "synced": synced,
                "batches": batches,
                "retries": retries,
                "retry_errors": retry_errors,
            }

    def sync_metadata(
        self,
        docs: list[dict[str, Any]],
        *,
        prune_missing: bool = False,
    ) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "status": "skipped",
                "reason": "rag_store_unavailable",
                "input_docs": len(docs),
            }

        ids, _, metadatas, skipped_low_value = self._build_document_payloads(docs)
        batch_size = max(min(int(self.config.sync_batch_size), 5000), 1)
        prune_result = self._delete_orphan_ids(
            current_ids=set(ids),
            batch_size=batch_size,
        ) if prune_missing and ids else {
            "deleted_orphans": 0,
            "scanned_vectors": 0,
        }
        if prune_missing and not ids:
            prune_result["prune_skipped_reason"] = "empty_input_docs"

        existing_ids = self._existing_ids(ids, batch_size=batch_size)
        filtered_ids: list[str] = []
        filtered_metadatas: list[dict[str, Any]] = []
        missing = 0
        for idx, doc_id in enumerate(ids):
            if doc_id not in existing_ids:
                missing += 1
                continue
            filtered_ids.append(doc_id)
            filtered_metadatas.append(metadatas[idx])

        if not filtered_ids:
            return {
                "status": "ok",
                "metadata_updated": 0,
                "input_docs": len(docs),
                "missing_vectors": missing,
                "skipped_low_value": skipped_low_value,
                "batches": 0,
                "batch_size": batch_size,
                **prune_result,
            }

        batch_count = 0
        metadata_updated = 0
        for start in range(0, len(filtered_ids), batch_size):
            end = start + batch_size
            try:
                self._collection.update(
                    ids=filtered_ids[start:end],
                    metadatas=filtered_metadatas[start:end],
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "reason": "rag_metadata_update_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "metadata_updated": metadata_updated,
                    "input_docs": len(docs),
                    "missing_vectors": missing,
                    "skipped_low_value": skipped_low_value,
                    "batches": batch_count,
                    "batch_size": batch_size,
                    **prune_result,
                }
            batch_count += 1
            metadata_updated += len(filtered_ids[start:end])
        return {
            "status": "ok",
            "metadata_updated": metadata_updated,
            "input_docs": len(docs),
            "missing_vectors": missing,
            "skipped_low_value": skipped_low_value,
            "batches": batch_count,
            "batch_size": batch_size,
            **prune_result,
        }

    def _existing_ids(self, ids: list[str], *, batch_size: int) -> set[str]:
        if not ids or self._collection is None:
            return set()
        existing: set[str] = set()
        for start in range(0, len(ids), max(int(batch_size), 1)):
            batch_ids = ids[start : start + batch_size]
            try:
                result = self._collection.get(ids=batch_ids)
            except Exception:
                return set()
            found_ids = result.get("ids") if isinstance(result, dict) else []
            existing.update(str(item) for item in found_ids or [] if str(item))
        return existing

    def _all_collection_ids(self, *, batch_size: int) -> tuple[list[str], str]:
        if self._collection is None:
            return [], ""
        page_size = max(int(batch_size), 1)
        offset = 0
        all_ids: list[str] = []

        while True:
            try:
                result = self._collection.get(limit=page_size, offset=offset)
                paginated = True
            except TypeError:
                try:
                    result = self._collection.get()
                    paginated = False
                except Exception as exc:
                    return [], f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                return [], f"{type(exc).__name__}: {exc}"

            found_ids = result.get("ids") if isinstance(result, dict) else []
            normalized = [str(item) for item in found_ids or [] if str(item)]
            if not normalized:
                break
            all_ids.extend(normalized)
            if not paginated or len(normalized) < page_size:
                break
            offset += len(normalized)

        return all_ids, ""

    def _delete_orphan_ids(
        self,
        *,
        current_ids: set[str],
        batch_size: int,
    ) -> dict[str, Any]:
        if not current_ids or self._collection is None:
            return {
                "deleted_orphans": 0,
                "scanned_vectors": 0,
                "prune_skipped_reason": "empty_current_ids",
            }

        collection_ids, error = self._all_collection_ids(batch_size=batch_size)
        if error:
            return {
                "deleted_orphans": 0,
                "scanned_vectors": 0,
                "prune_skipped_reason": "collection_scan_failed",
                "prune_error": error,
            }

        stale_ids = [item for item in collection_ids if item not in current_ids]
        for start in range(0, len(stale_ids), max(int(batch_size), 1)):
            self._collection.delete(ids=stale_ids[start : start + batch_size])

        return {
            "deleted_orphans": len(stale_ids),
            "scanned_vectors": len(collection_ids),
        }

    def prune_low_value_documents(
        self,
        *,
        dry_run: bool = True,
        batch_size: int | None = None,
        max_scan: int | None = None,
    ) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "status": "skipped",
                "reason": "rag_store_unavailable",
                "dry_run": bool(dry_run),
            }
        resolved_batch_size = max(
            min(int(batch_size or self.config.sync_batch_size), 5000),
            1,
        )
        scan_limit = max(int(max_scan or 0), 0)
        offset = 0
        scanned = 0
        candidates: list[str] = []
        scan_error = ""

        while True:
            if scan_limit and scanned >= scan_limit:
                break
            limit = resolved_batch_size
            if scan_limit:
                limit = min(limit, scan_limit - scanned)
            try:
                result = self._collection.get(
                    limit=limit,
                    offset=offset,
                    include=["documents"],
                )
                paginated = True
            except TypeError:
                try:
                    result = self._collection.get(include=["documents"])
                    paginated = False
                except Exception as exc:
                    scan_error = f"{type(exc).__name__}: {exc}"
                    break
            except Exception as exc:
                scan_error = f"{type(exc).__name__}: {exc}"
                break

            ids = result.get("ids") if isinstance(result, dict) else []
            documents = result.get("documents") if isinstance(result, dict) else []
            normalized_ids = [str(item) for item in ids or [] if str(item)]
            if not normalized_ids:
                break
            for index, doc_id in enumerate(normalized_ids):
                document = ""
                if isinstance(documents, list) and index < len(documents):
                    document = str(documents[index] or "")
                if _is_low_value_rag_chunk(document):
                    candidates.append(doc_id)
            scanned += len(normalized_ids)
            if not paginated or len(normalized_ids) < limit:
                break
            offset += len(normalized_ids)

        deleted = 0
        if candidates and not dry_run:
            for start in range(0, len(candidates), resolved_batch_size):
                batch = candidates[start : start + resolved_batch_size]
                try:
                    self._collection.delete(ids=batch)
                except Exception as exc:
                    return {
                        "status": "error",
                        "reason": "rag_low_value_delete_failed",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                        "dry_run": False,
                        "scanned_vectors": scanned,
                        "low_value_candidates": len(candidates),
                        "deleted_low_value": deleted,
                        "batch_size": resolved_batch_size,
                        "max_scan": scan_limit,
                    }
                deleted += len(batch)
        result_payload: dict[str, Any] = {
            "status": "ok" if not scan_error else "error",
            "dry_run": bool(dry_run),
            "scanned_vectors": scanned,
            "low_value_candidates": len(candidates),
            "deleted_low_value": deleted,
            "batch_size": resolved_batch_size,
            "max_scan": scan_limit,
        }
        if scan_error:
            result_payload["reason"] = "collection_scan_failed"
            result_payload["detail"] = scan_error[:500]
        return result_payload

    def _build_where(
        self,
        *,
        symbol: str,
        broker: str,
        doc_id: str,
    ) -> dict[str, Any] | None:
        filters: list[dict[str, Any]] = []
        if symbol:
            filters.append({"symbol": symbol})
        if broker:
            filters.append({"broker": broker})
        if doc_id:
            filters.append({"doc_id": doc_id})
        if not filters:
            return None
        if len(filters) == 1:
            return filters[0]
        return {"$and": filters}

    @staticmethod
    def _metadata_symbol_matches(meta: dict[str, Any], symbol: str) -> bool:
        target = str(symbol or "").strip()
        if not target:
            return True
        if str(meta.get("symbol") or "").strip() == target:
            return True
        linked_symbols = {
            item.strip()
            for item in str(meta.get("linked_symbols") or "").split(",")
            if item.strip()
        }
        return target in linked_symbols

    @staticmethod
    def _metadata_symbol_exact(meta: dict[str, Any], symbol: str) -> bool:
        target = str(symbol or "").strip()
        if not target:
            return True
        return str(meta.get("symbol") or "").strip() == target

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        normalized = str(query or "").strip().lower()
        if not normalized:
            return []
        terms = [normalized]
        for token in re.findall(r"[0-9a-zA-Z가-힣]{2,}", normalized):
            if token not in terms:
                terms.append(token)
        return terms

    @staticmethod
    def _term_score(text: str, terms: list[str], *, weight: float) -> float:
        haystack = str(text or "").lower()
        if not haystack:
            return 0.0
        score = 0.0
        for term in terms:
            if term and term in haystack:
                score += weight
        return score

    @classmethod
    def _lexical_score(
        cls,
        row: dict[str, Any],
        *,
        terms: list[str],
        symbol_text: str,
    ) -> float:
        if not terms and not symbol_text:
            return 0.0
        score = 0.0
        score += cls._term_score(str(row.get("title") or ""), terms, weight=4.0)
        score += cls._term_score(
            str(row.get("section_title") or ""), terms, weight=3.0
        )
        score += cls._term_score(str(row.get("content") or ""), terms, weight=2.5)
        score += cls._term_score(
            str(row.get("linked_names") or ""), terms, weight=1.5
        )
        score += cls._term_score(str(row.get("broker") or ""), terms, weight=0.5)
        if symbol_text:
            if str(row.get("symbol") or "").strip() == symbol_text:
                score += 8.0
            linked_symbols = {
                item.strip()
                for item in str(row.get("linked_symbols") or "").split(",")
                if item.strip()
            }
            if symbol_text in linked_symbols:
                score += 3.0
        category = str(row.get("category") or "")
        if score > 0 and category == "company_analysis" and (row.get("symbol") or ""):
            score += 0.75
        return score

    @classmethod
    def _rerank_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        query: str,
        symbol_text: str,
    ) -> list[dict[str, Any]]:
        terms = cls._query_terms(query)

        def sort_key(row: dict[str, Any]) -> tuple[float, float, int, int, int]:
            score = cls._lexical_score(row, terms=terms, symbol_text=symbol_text)
            row["lexical_score"] = round(score, 6)
            distance_raw = row.get("distance")
            try:
                distance = float(distance_raw)
            except (TypeError, ValueError):
                distance = 9999.0
            published_at = str(row.get("published_at") or "")
            published_rank = int(re.sub(r"\D", "", published_at[:10]) or 0)
            return (
                -score,
                distance,
                -published_rank,
                int(row.get("report_id") or 0),
                int(row.get("chunk_index") or 0),
            )

        ranked = sorted(rows, key=sort_key)
        if terms or symbol_text:
            positive = [
                row
                for row in ranked
                if float(row.get("lexical_score") or 0.0) > 0.0
            ]
            if positive:
                return positive
        return ranked

    @staticmethod
    def _diversify_report_chunks(
        rows: list[dict[str, Any]],
        *,
        max_chunks_per_report: int = 2,
    ) -> list[dict[str, Any]]:
        max_chunks = max(int(max_chunks_per_report), 1)
        counts: dict[int, int] = {}
        out: list[dict[str, Any]] = []
        for row in rows:
            report_id = int(row.get("report_id") or 0)
            current = counts.get(report_id, 0)
            if current >= max_chunks:
                continue
            counts[report_id] = current + 1
            out.append(row)
        return out

    def _rows_from_query_results(
        self,
        results: dict[str, Any],
        *,
        symbol_text: str,
        exact_symbol_only: bool,
        seen: set[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        docs = list((results.get("documents") or [[]])[0])
        metas = list((results.get("metadatas") or [[]])[0])
        distances = list((results.get("distances") or [[]])[0])
        for idx, content in enumerate(docs):
            meta = (
                metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
            )
            if exact_symbol_only:
                if not self._metadata_symbol_exact(meta, symbol_text):
                    continue
            elif not self._metadata_symbol_matches(meta, symbol_text):
                continue
            report_id = int(meta.get("report_id") or 0)
            chunk_index = int(meta.get("chunk_index") or 0)
            key = (report_id, chunk_index)
            if key in seen:
                continue
            seen.add(key)
            distance = distances[idx] if idx < len(distances) else None
            out.append(
                {
                    "content": str(content or ""),
                    "distance": float(distance) if distance is not None else None,
                    "doc_id": str(meta.get("doc_id") or ""),
                    "report_id": report_id,
                    "chunk_index": chunk_index,
                    "symbol": str(meta.get("symbol") or ""),
                    "category": str(meta.get("category") or "unknown"),
                    "title": str(meta.get("title") or ""),
                    "broker": str(meta.get("broker") or ""),
                    "published_at": str(meta.get("published_at") or ""),
                    "page_start": int(meta.get("page_start") or 0),
                    "page_end": int(meta.get("page_end") or 0),
                    "section_title": str(meta.get("section_title") or "unknown"),
                    "pdf_url": str(meta.get("pdf_url") or ""),
                    "detail_url": str(meta.get("detail_url") or ""),
                    "linked_symbols": str(meta.get("linked_symbols") or ""),
                    "linked_names": str(meta.get("linked_names") or ""),
                    "linked_asset_classes": str(
                        meta.get("linked_asset_classes") or ""
                    ),
                }
            )
        return out

    def query(
        self,
        query: str,
        symbol: str = "",
        limit: int = 8,
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        if not self._available or self._collection is None:
            return []
        text = str(query or "").strip()
        if not text:
            return []
        resolved_limit = max(min(int(limit), 50), 1)
        symbol_text = str(symbol or "").strip()
        broker_text = str(broker or "").strip()
        doc_id_text = str(doc_id or "").strip()
        oversample_factor = max(int(self.config.query_oversample_factor), 1)
        n_results = max(resolved_limit, min(50, resolved_limit * oversample_factor))

        out: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()

        def run_query(
            *,
            where: dict[str, Any] | None,
            limit_count: int,
            exact_symbol_only: bool,
        ) -> None:
            try:
                results = self._collection.query(
                    query_texts=[text],
                    n_results=limit_count,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                self._last_query_error = ""
            except Exception as exc:
                self._last_query_error = f"{type(exc).__name__}: {exc}"
                return
            out.extend(
                self._rows_from_query_results(
                    results,
                    symbol_text=symbol_text,
                    exact_symbol_only=exact_symbol_only,
                    seen=seen,
                )
            )

        if symbol_text:
            exact_where = self._build_where(
                symbol=symbol_text,
                broker=broker_text,
                doc_id=doc_id_text,
            )
            run_query(
                where=exact_where,
                limit_count=n_results,
                exact_symbol_only=True,
            )
        if len(out) < resolved_limit:
            broad_where = self._build_where(
                symbol="",
                broker=broker_text,
                doc_id=doc_id_text,
            )
            run_query(
                where=broad_where,
                limit_count=n_results,
                exact_symbol_only=False,
            )
        if date_from or date_to:
            filtered: list[dict[str, Any]] = []
            for row in out:
                published = str(row.get("published_at") or "")
                if date_from and published and published < date_from:
                    continue
                if date_to and published and published > date_to:
                    continue
                filtered.append(row)
            ranked = self._rerank_rows(
                filtered,
                query=text,
                symbol_text=symbol_text,
            )
            return self._diversify_report_chunks(ranked)[:resolved_limit]
        ranked = self._rerank_rows(out, query=text, symbol_text=symbol_text)
        return self._diversify_report_chunks(ranked)[:resolved_limit]
