from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.rag_store import RAGStore, RAGStoreConfig


class _FakeCollection:
    def __init__(self, existing_ids: set[str] | None = None) -> None:
        self.existing_ids = set(existing_ids or set())
        self.get_calls: list[list[str]] = []
        self.scan_calls: list[dict[str, int]] = []
        self.upserts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deletes: list[list[str]] = []
        self.last_query: dict[str, Any] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.upserts.append(
            {
                "ids": ids,
                "documents": documents,
                "metadatas": metadatas,
            }
        )
        self.existing_ids.update(ids)

    def update(
        self,
        *,
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.updates.append({"ids": ids, "metadatas": metadatas})

    def delete(self, *, ids: list[str]) -> None:
        self.deletes.append(ids)
        self.existing_ids.difference_update(ids)

    def get(
        self,
        *,
        ids: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        if ids is not None:
            self.get_calls.append(ids)
            return {"ids": [item for item in ids if item in self.existing_ids]}

        self.scan_calls.append(
            {
                "limit": int(limit or 0),
                "offset": int(offset or 0),
            }
        )
        ordered = sorted(self.existing_ids)
        start = max(int(offset or 0), 0)
        end = start + max(int(limit or len(ordered)), 1)
        return {"ids": ordered[start:end]}

    def query(
        self,
        *,
        query_texts: list[str],
        n_results: int,
        where: dict[str, Any] | None,
        include: list[str],
    ) -> dict[str, Any]:
        self.last_query = {
            "query_texts": query_texts,
            "n_results": n_results,
            "where": where,
            "include": include,
        }
        return {
            "documents": [["최근 반도체 리포트", "오래된 리포트", "추가 근거"]],
            "metadatas": [
                [
                    {
                        "report_id": 1,
                        "chunk_index": 0,
                        "symbol": "005930",
                        "broker": "테스트증권",
                        "published_at": "2026-01-03",
                        "title": "삼성전자",
                    },
                    {
                        "report_id": 2,
                        "chunk_index": 0,
                        "symbol": "005930",
                        "broker": "테스트증권",
                        "published_at": "2025-01-03",
                        "title": "삼성전자 과거",
                    },
                    {
                        "report_id": 3,
                        "chunk_index": 0,
                        "symbol": "005930",
                        "broker": "테스트증권",
                        "published_at": "2026-01-04",
                        "title": "삼성전자 추가",
                    },
                ]
            ],
            "distances": [[0.12, 0.2, 0.18]],
        }


def _store(collection: _FakeCollection, **kwargs: Any) -> RAGStore:
    store = RAGStore.__new__(RAGStore)
    store.config = RAGStoreConfig(
        persist_path=".runtime/test_rag",
        collection_name="test",
        **kwargs,
    )
    store._available = True
    store._client = None
    store._collection = collection
    store._init_error = ""
    return store


def test_sync_documents_batches_upserts() -> None:
    collection = _FakeCollection()
    store = _store(collection, sync_batch_size=2)
    docs = [
        {
            "report_id": idx,
            "chunk_index": 0,
            "content": f"문서 {idx}",
        }
        for idx in range(5)
    ]

    result = store.sync_documents(docs)

    assert result["status"] == "ok"
    assert result["synced"] == 5
    assert result["batches"] == 3
    assert [len(row["ids"]) for row in collection.upserts] == [2, 2, 1]


def test_sync_documents_skips_existing_vectors() -> None:
    existing_ids = {
        hashlib.sha256("0:0".encode("utf-8")).hexdigest(),
        hashlib.sha256("2:0".encode("utf-8")).hexdigest(),
    }
    collection = _FakeCollection(existing_ids=existing_ids)
    store = _store(collection, sync_batch_size=2, skip_existing=True)
    docs = [
        {
            "report_id": idx,
            "chunk_index": 0,
            "content": f"문서 {idx}",
        }
        for idx in range(5)
    ]

    result = store.sync_documents(docs)

    assert result["status"] == "ok"
    assert result["synced"] == 3
    assert result["skipped_existing"] == 2
    assert result["batches"] == 2
    assert len(collection.get_calls) == 3
    assert [len(row["ids"]) for row in collection.upserts] == [2, 1]
    assert existing_ids.isdisjoint(
        {item for row in collection.upserts for item in row["ids"]}
    )


def test_sync_metadata_updates_existing_vectors_without_reembedding() -> None:
    existing_ids = {
        hashlib.sha256("1:0".encode("utf-8")).hexdigest(),
        hashlib.sha256("2:0".encode("utf-8")).hexdigest(),
    }
    collection = _FakeCollection(existing_ids=existing_ids)
    store = _store(collection, sync_batch_size=1, skip_existing=True)
    docs = [
        {
            "report_id": 1,
            "chunk_index": 0,
            "content": "문서 1",
            "title": "정리된 제목",
        },
        {
            "report_id": 2,
            "chunk_index": 0,
            "content": "문서 2",
            "title": "다른 제목",
        },
        {
            "report_id": 3,
            "chunk_index": 0,
            "content": "문서 3",
            "title": "아직 없음",
        },
    ]

    result = store.sync_metadata(docs)

    assert result["status"] == "ok"
    assert result["metadata_updated"] == 2
    assert result["missing_vectors"] == 1
    assert len(collection.upserts) == 0
    assert [len(row["ids"]) for row in collection.updates] == [1, 1]


def test_sync_metadata_prunes_orphan_vectors() -> None:
    current_ids = {
        hashlib.sha256("1:0".encode("utf-8")).hexdigest(),
        hashlib.sha256("2:0".encode("utf-8")).hexdigest(),
    }
    stale_id = hashlib.sha256("99:0".encode("utf-8")).hexdigest()
    collection = _FakeCollection(existing_ids={*current_ids, stale_id})
    store = _store(collection, sync_batch_size=2, skip_existing=True)
    docs = [
        {
            "report_id": 1,
            "chunk_index": 0,
            "content": "문서 1",
            "title": "정리된 제목",
        },
        {
            "report_id": 2,
            "chunk_index": 0,
            "content": "문서 2",
            "title": "다른 제목",
        },
    ]

    result = store.sync_metadata(docs, prune_missing=True)

    assert result["status"] == "ok"
    assert result["metadata_updated"] == 2
    assert result["deleted_orphans"] == 1
    assert result["scanned_vectors"] == 3
    assert stale_id not in collection.existing_ids
    assert collection.deletes == [[stale_id]]
    assert [len(row["ids"]) for row in collection.updates] == [2]


def test_migrates_legacy_hnsw_metadata_pickle(tmp_path: Path) -> None:
    mod = pytest.importorskip("chromadb.segment.impl.vector.local_persistent_hnsw")
    segment_dir = tmp_path / "segment"
    segment_dir.mkdir()
    metadata_path = segment_dir / "index_metadata.pickle"
    legacy_payload = {
        "dimensionality": 384,
        "total_elements_added": 2,
        "max_seq_id": None,
        "id_to_label": {"a": 1},
        "label_to_id": {1: "a"},
        "id_to_seq_id": {"a": 1},
    }
    metadata_path.write_bytes(pickle.dumps(legacy_payload))

    migrated = RAGStore._migrate_legacy_hnsw_metadata(tmp_path)
    restored = pickle.loads(metadata_path.read_bytes())

    assert migrated == 1
    assert isinstance(restored, mod.PersistentData)
    assert restored.dimensionality == 384
    assert (segment_dir / "index_metadata.pickle.legacy-dict.bak").exists()


def test_query_uses_combined_filters_and_oversamples_for_date_filters() -> None:
    collection = _FakeCollection()
    store = _store(collection, query_oversample_factor=4)

    rows = store.query(
        query="반도체",
        symbol="005930",
        broker="테스트증권",
        limit=1,
        date_from="2026-01-01",
    )

    assert collection.last_query["n_results"] == 4
    assert collection.last_query["where"] == {
        "$and": [{"symbol": "005930"}, {"broker": "테스트증권"}]
    }
    assert len(rows) == 1
    assert rows[0]["report_id"] == 1
