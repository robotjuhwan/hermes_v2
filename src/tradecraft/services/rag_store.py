from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RAGStoreConfig:
    persist_path: str
    collection_name: str = "naver_reports"


class RAGStore:
    def __init__(self, config: RAGStoreConfig) -> None:
        self.config = config
        self._available = False
        self._client = None
        self._collection = None

        try:
            import chromadb  # type: ignore
        except Exception:
            return

        path = Path(config.persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(config.collection_name)
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "available": False,
                "reason": "chromadb_not_installed_or_init_failed",
                "persist_path": self.config.persist_path,
                "collection_name": self.config.collection_name,
            }
        try:
            count = int(self._collection.count())
        except Exception:
            count = 0
        return {
            "available": True,
            "persist_path": self.config.persist_path,
            "collection_name": self.config.collection_name,
            "count": count,
        }

    def sync_documents(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        if not self._available or self._collection is None:
            return {
                "status": "skipped",
                "reason": "rag_store_unavailable",
                "input_docs": len(docs),
            }

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

        if not ids:
            return {
                "status": "ok",
                "synced": 0,
                "input_docs": len(docs),
            }

        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return {
            "status": "ok",
            "synced": len(ids),
            "input_docs": len(docs),
        }

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
        where: dict[str, Any] | None = None
        if symbol:
            where = {"symbol": symbol}
        if broker:
            where = {**(where or {}), "broker": broker}
        if doc_id:
            where = {**(where or {}), "doc_id": doc_id}
        results = self._collection.query(
            query_texts=[text],
            n_results=max(min(int(limit), 50), 1),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

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
            return filtered
        return out
