from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
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


class RAGStore:
    def __init__(self, config: RAGStoreConfig) -> None:
        self.config = config
        self._available = False
        self._client = None
        self._collection = None
        self._init_error = ""

        try:
            import chromadb  # type: ignore
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            return

        try:
            path = Path(config.persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._migrate_legacy_hnsw_metadata(path)
            self._client = chromadb.PersistentClient(path=str(path))
            self._collection = self._resolve_collection()
            self._available = True
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _migrate_legacy_hnsw_metadata(path: Path) -> int:
        try:
            from chromadb.segment.impl.vector.local_persistent_hnsw import (  # type: ignore
                PersistentData,
            )
        except Exception:
            return 0

        migrated = 0
        for metadata_path in path.glob("*/index_metadata.pickle"):
            try:
                payload = metadata_path.read_bytes()
                raw = pickle.loads(payload)
            except Exception:
                continue
            if not isinstance(raw, dict) or "dimensionality" not in raw:
                continue
            try:
                persistent = PersistentData(
                    dimensionality=raw.get("dimensionality"),
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
            }
        try:
            count = int(self._collection.count())
            count_error = ""
        except Exception as exc:
            count = 0
            count_error = f"{type(exc).__name__}: {exc}"
        payload = {
            "available": True,
            "persist_path": self.config.persist_path,
            "collection_name": self.config.collection_name,
            "count": count,
            "sync_batch_size": max(int(self.config.sync_batch_size), 1),
            "skip_existing": bool(self.config.skip_existing),
            "query_oversample_factor": max(int(self.config.query_oversample_factor), 1),
        }
        if count_error:
            payload["count_error"] = count_error[:500]
        return payload

    def _build_document_payloads(
        self,
        docs: list[dict[str, Any]],
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for row in docs:
            content = str(row.get("content") or "").strip()
            if not content:
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
                }
            )
        return ids, documents, metadatas

    def sync_documents(
        self,
        docs: list[dict[str, Any]],
        *,
        force_update: bool = False,
    ) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "status": "skipped",
                "reason": "rag_store_unavailable",
                "input_docs": len(docs),
            }

        ids, documents, metadatas = self._build_document_payloads(docs)
        batch_size = max(min(int(self.config.sync_batch_size), 5000), 1)
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
                "batches": 0,
                "batch_size": batch_size,
                "force_update": bool(force_update),
            }

        batch_count = 0
        synced = 0
        for start in range(0, len(ids), batch_size):
            end = start + batch_size
            try:
                self._collection.upsert(
                    ids=ids[start:end],
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "reason": "rag_upsert_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "synced": synced,
                    "input_docs": len(docs),
                    "skipped_existing": skipped_existing,
                    "batches": batch_count,
                    "batch_size": batch_size,
                    "force_update": bool(force_update),
                }
            batch_count += 1
            synced += len(ids[start:end])
        return {
            "status": "ok",
            "synced": synced,
            "input_docs": len(docs),
            "skipped_existing": skipped_existing,
            "batches": batch_count,
            "batch_size": batch_size,
            "force_update": bool(force_update),
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

        ids, _, metadatas = self._build_document_payloads(docs)
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
        n_results = resolved_limit
        if date_from or date_to:
            oversample_factor = max(int(self.config.query_oversample_factor), 1)
            n_results = max(resolved_limit, min(50, resolved_limit * oversample_factor))
        where = self._build_where(
            symbol=str(symbol or "").strip(),
            broker=str(broker or "").strip(),
            doc_id=str(doc_id or "").strip(),
        )
        try:
            results = self._collection.query(
                query_texts=[text],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return []

        out: list[dict[str, Any]] = []
        docs = list((results.get("documents") or [[]])[0])
        metas = list((results.get("metadatas") or [[]])[0])
        distances = list((results.get("distances") or [[]])[0])
        for idx, content in enumerate(docs):
            meta = (
                metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
            )
            distance = distances[idx] if idx < len(distances) else None
            out.append(
                {
                    "content": str(content or ""),
                    "distance": float(distance) if distance is not None else None,
                    "doc_id": str(meta.get("doc_id") or ""),
                    "report_id": int(meta.get("report_id") or 0),
                    "chunk_index": int(meta.get("chunk_index") or 0),
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
                }
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
            return filtered[:resolved_limit]
        return out[:resolved_limit]
