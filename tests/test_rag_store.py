from __future__ import annotations

import hashlib
import json
import pickle
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.rag_store import RAGStore, RAGStoreConfig


class _FakeCollection:
    def __init__(
        self,
        existing_ids: set[str] | None = None,
        documents_by_id: dict[str, str] | None = None,
    ) -> None:
        self.existing_ids = set(existing_ids or set())
        self.documents_by_id = dict(documents_by_id or {})
        self.get_calls: list[list[str]] = []
        self.scan_calls: list[dict[str, int]] = []
        self.upserts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deletes: list[list[str]] = []
        self.last_query: dict[str, Any] = {}
        self.raise_on_delete = False
        self.raise_on_count = False

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
        for doc_id, document in zip(ids, documents):
            self.documents_by_id[doc_id] = document

    def update(
        self,
        *,
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.updates.append({"ids": ids, "metadatas": metadatas})

    def delete(self, *, ids: list[str]) -> None:
        if self.raise_on_delete:
            raise RuntimeError("delete unavailable")
        self.deletes.append(ids)
        self.existing_ids.difference_update(ids)

    def count(self) -> int:
        if self.raise_on_count:
            raise RuntimeError("count unavailable")
        return len(self.existing_ids)

    def get(
        self,
        *,
        ids: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        if ids is not None:
            self.get_calls.append(ids)
            found = [item for item in ids if item in self.existing_ids]
            result: dict[str, Any] = {"ids": found}
            if include and "documents" in include:
                result["documents"] = [self.documents_by_id.get(item, "") for item in found]
            return result

        self.scan_calls.append(
            {
                "limit": int(limit or 0),
                "offset": int(offset or 0),
            }
        )
        ordered = sorted(self.existing_ids)
        start = max(int(offset or 0), 0)
        end = start + max(int(limit or len(ordered)), 1)
        found = ordered[start:end]
        result = {"ids": found}
        if include and "documents" in include:
            result["documents"] = [self.documents_by_id.get(item, "") for item in found]
        return result

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
                        "linked_symbols": "069500",
                        "linked_names": "KODEX 200",
                        "linked_asset_classes": "etf",
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


class _SymbolAwareQueryCollection(_FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.query_calls: list[dict[str, Any]] = []

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
        self.query_calls.append(dict(self.last_query))
        if where == {"symbol": "000660"}:
            return {
                "documents": [["SK하이닉스 HBM 리포트"]],
                "metadatas": [
                    [
                        {
                            "report_id": 10,
                            "chunk_index": 0,
                            "symbol": "000660",
                            "broker": "테스트증권",
                            "published_at": "2026-05-27",
                            "title": "SK하이닉스",
                        }
                    ]
                ],
                "distances": [[0.11]],
            }
        return {
            "documents": [["SK 지주회사 리포트"]],
            "metadatas": [
                [
                    {
                        "report_id": 20,
                        "chunk_index": 0,
                        "symbol": "034730",
                        "broker": "테스트증권",
                        "published_at": "2026-05-27",
                        "title": "SK",
                    }
                ]
            ],
            "distances": [[0.2]],
        }


class _LexicalRerankCollection(_FakeCollection):
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
        docs = [
            "글로벌 브랜드가 된다면III",
            "화장품 유럽 수출 확대",
            "MP: 싼 반도체 & 비싼 코스닥",
            "HBM 수요와 메모리 반도체 업사이클",
        ][:n_results]
        metas = [
            {
                "report_id": 100,
                "chunk_index": 0,
                "symbol": "",
                "category": "industry_analysis",
                "title": "화장품 하반기 전망 유럽 확장과 브랜드 재평가",
                "section_title": "글로벌 브랜드가 된다면III",
                "published_at": "2026-06-02",
                "linked_names": "실리콘투,한국화장품",
            },
            {
                "report_id": 101,
                "chunk_index": 0,
                "symbol": "",
                "category": "industry_analysis",
                "title": "화장품 수출 점검",
                "section_title": "브랜드 재평가",
                "published_at": "2026-06-03",
                "linked_names": "실리콘투",
            },
            {
                "report_id": 102,
                "chunk_index": 0,
                "symbol": "",
                "category": "invest_info",
                "title": "싼 반도체 & 비싼 코스닥",
                "section_title": "MP: 싼 반도체 & 비싼 코스닥",
                "published_at": "2026-06-04",
                "linked_names": "삼성전자,SK하이닉스",
            },
            {
                "report_id": 103,
                "chunk_index": 0,
                "symbol": "000660",
                "category": "company_analysis",
                "title": "SK하이닉스 HBM 반도체 수요 점검",
                "section_title": "메모리 업사이클",
                "published_at": "2026-06-05",
                "linked_names": "SK하이닉스",
            },
        ][:n_results]
        return {
            "documents": [docs],
            "metadatas": [metas],
            "distances": [[0.05, 0.08, 0.18, 0.2][:n_results]],
        }


class _DuplicateReportQueryCollection(_FakeCollection):
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
        docs = [
            "SK하이닉스 HBM 실적 점검",
            "SK하이닉스 HBM 가격 전망",
            "SK하이닉스 HBM 투자포인트",
            "SK하이닉스 메모리 산업 비교",
        ][:n_results]
        metas = [
            {
                "report_id": 10,
                "chunk_index": 0,
                "symbol": "000660",
                "category": "company_analysis",
                "title": "SK하이닉스 HBM 리포트",
                "section_title": "실적",
                "published_at": "2026-06-05",
            },
            {
                "report_id": 10,
                "chunk_index": 1,
                "symbol": "000660",
                "category": "company_analysis",
                "title": "SK하이닉스 HBM 리포트",
                "section_title": "가격",
                "published_at": "2026-06-05",
            },
            {
                "report_id": 10,
                "chunk_index": 2,
                "symbol": "000660",
                "category": "company_analysis",
                "title": "SK하이닉스 HBM 리포트",
                "section_title": "투자포인트",
                "published_at": "2026-06-05",
            },
            {
                "report_id": 20,
                "chunk_index": 0,
                "symbol": "000660",
                "category": "company_analysis",
                "title": "SK하이닉스 메모리 산업 리포트",
                "section_title": "산업 비교",
                "published_at": "2026-06-04",
            },
        ][:n_results]
        return {
            "documents": [docs],
            "metadatas": [metas],
            "distances": [[0.1, 0.11, 0.12, 0.2][:n_results]],
        }


class _FlakyLargeBatchCollection(_FakeCollection):
    def __init__(self, *, min_fail_size: int) -> None:
        super().__init__()
        self.min_fail_size = min_fail_size
        self.failed_once = False

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not self.failed_once and len(ids) >= self.min_fail_size:
            self.failed_once = True
            raise RuntimeError("metadata segment compaction failed")
        super().upsert(ids=ids, documents=documents, metadatas=metadatas)


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
    store._repair_result = {}
    store._last_query_error = ""
    return store


class _FakeChromaClient:
    def __init__(self, path: str) -> None:
        self.path = path

    def get_collection(self, name: str) -> object:
        raise RuntimeError("missing")

    def create_collection(self, **_: Any) -> object:
        return _FakeCollection()


def test_rag_store_disables_chromadb_anonymized_telemetry(monkeypatch, tmp_path) -> None:
    seen: dict[str, Any] = {}

    class FakeSettings:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            seen["settings_kwargs"] = kwargs

    class FakeChromaClient:
        def __init__(self, path: str, settings: Any | None = None) -> None:
            self.path = path
            seen["client_path"] = path
            seen["client_settings"] = settings

        def get_collection(self, name: str) -> object:
            raise RuntimeError("missing")

        def create_collection(self, **_: Any) -> object:
            return _FakeCollection()

    fake_chromadb = types.SimpleNamespace(PersistentClient=FakeChromaClient)
    fake_config = types.SimpleNamespace(Settings=FakeSettings)
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setitem(sys.modules, "chromadb.config", fake_config)

    store = RAGStore(RAGStoreConfig(persist_path=str(tmp_path / "rag")))

    assert store.status()["available"] is True
    assert seen["settings_kwargs"] == {
        "anonymized_telemetry": False,
        "chroma_product_telemetry_impl": (
            "tradecraft.services.chroma_telemetry.NoOpProductTelemetry"
        ),
    }
    assert seen["client_settings"].kwargs == seen["settings_kwargs"]


def test_legacy_pickle_migration_is_off_by_default(monkeypatch, tmp_path) -> None:
    calls: list[Path] = []
    fake_chromadb = types.SimpleNamespace(PersistentClient=_FakeChromaClient)
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setattr(
        RAGStore,
        "_migrate_legacy_hnsw_metadata",
        staticmethod(lambda path: calls.append(path) or 0),
    )

    store = RAGStore(RAGStoreConfig(persist_path=str(tmp_path / "rag")))

    assert store.status()["available"] is True
    assert calls == []


def test_legacy_pickle_migration_can_be_explicitly_enabled(monkeypatch, tmp_path) -> None:
    calls: list[Path] = []
    fake_chromadb = types.SimpleNamespace(PersistentClient=_FakeChromaClient)
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setattr(
        RAGStore,
        "_migrate_legacy_hnsw_metadata",
        staticmethod(lambda path: calls.append(path) or 0),
    )

    RAGStore(
        RAGStoreConfig(
            persist_path=str(tmp_path / "rag"),
            allow_legacy_pickle_migration=True,
        )
    )

    assert calls == [tmp_path / "rag"]


def test_repairs_legacy_empty_chroma_collection_config(tmp_path: Path) -> None:
    db_dir = tmp_path / "rag"
    db_dir.mkdir()
    db_path = db_dir / "chroma.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                dimension INTEGER,
                database_id TEXT NOT NULL,
                config_json_str TEXT,
                schema_str TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE collection_metadata (
                collection_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO collections
                (id, name, dimension, database_id, config_json_str, schema_str)
            VALUES
                ('c1', 'naver_reports', 384, 'default', '{}', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO collection_metadata
                (collection_id, key, str_value)
            VALUES
                ('c1', 'hnsw:space', 'cosine')
            """
        )

    result = RAGStore._repair_legacy_collection_config(
        db_dir,
        collection_name="naver_reports",
    )

    assert result["status"] == "ok"
    assert result["repaired"] == 1
    assert (db_dir / "chroma.sqlite3.legacy-config.bak").exists()
    with sqlite3.connect(db_path) as conn:
        raw_config = conn.execute(
            "SELECT config_json_str FROM collections WHERE name = 'naver_reports'"
        ).fetchone()[0]
    parsed = json.loads(raw_config)
    assert parsed["_type"] == "CollectionConfigurationInternal"
    assert parsed["hnsw_configuration"]["_type"] == "HNSWConfigurationInternal"
    assert parsed["hnsw_configuration"]["space"] == "cosine"


def test_repair_legacy_chroma_collection_config_is_idempotent(tmp_path: Path) -> None:
    db_dir = tmp_path / "rag"
    db_dir.mkdir()
    db_path = db_dir / "chroma.sqlite3"
    config = RAGStore._default_collection_config(hnsw_space="ip")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                config_json_str TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO collections (id, name, config_json_str)
            VALUES ('c1', 'naver_reports', ?)
            """,
            (json.dumps(config),),
        )

    result = RAGStore._repair_legacy_collection_config(
        db_dir,
        collection_name="naver_reports",
    )

    assert result == {
        "status": "ok",
        "repaired": 0,
        "segment_metadata_repaired": 0,
    }
    assert not (db_dir / "chroma.sqlite3.legacy-config.bak").exists()


def test_repairs_missing_hnsw_segment_metadata(tmp_path: Path) -> None:
    db_dir = tmp_path / "rag"
    db_dir.mkdir()
    db_path = db_dir / "chroma.sqlite3"
    config = RAGStore._default_collection_config(hnsw_space="cosine")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                config_json_str TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE collection_metadata (
                collection_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                collection TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segment_metadata (
                segment_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO collections (id, name, config_json_str)
            VALUES ('c1', 'naver_reports', ?)
            """,
            (json.dumps(config),),
        )
        conn.execute(
            """
            INSERT INTO collection_metadata (collection_id, key, str_value)
            VALUES ('c1', 'hnsw:space', 'cosine')
            """
        )
        conn.execute(
            """
            INSERT INTO segments (id, type, scope, collection)
            VALUES (
                's1',
                'urn:chroma:segment/vector/hnsw-local-persisted',
                'VECTOR',
                'c1'
            )
            """
        )

    result = RAGStore._repair_legacy_collection_config(
        db_dir,
        collection_name="naver_reports",
    )

    assert result == {
        "status": "ok",
        "repaired": 0,
        "segment_metadata_repaired": 8,
    }
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT key, str_value, int_value, float_value
            FROM segment_metadata
            ORDER BY key
            """
        ).fetchall()
    assert len(rows) == 8
    assert ("hnsw:space", "cosine", None, None) in rows


def test_repair_hnsw_segment_metadata_ignores_concurrent_duplicate(
    tmp_path: Path,
) -> None:
    db_dir = tmp_path / "rag"
    db_dir.mkdir()
    db_path = db_dir / "chroma.sqlite3"
    config = RAGStore._default_collection_config(hnsw_space="cosine")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                config_json_str TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE collection_metadata (
                collection_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                collection TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segment_metadata (
                segment_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER,
                PRIMARY KEY (segment_id, key)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO collections (id, name, config_json_str)
            VALUES ('c1', 'naver_reports', ?)
            """,
            (json.dumps(config),),
        )
        conn.execute(
            """
            INSERT INTO collection_metadata (collection_id, key, str_value)
            VALUES ('c1', 'hnsw:space', 'cosine')
            """
        )
        conn.execute(
            """
            INSERT INTO segments (id, type, scope, collection)
            VALUES (
                's1',
                'urn:chroma:segment/vector/hnsw-local-persisted',
                'VECTOR',
                'c1'
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER race_hnsw_space BEFORE INSERT ON segment_metadata
            WHEN NEW.key = 'hnsw:space'
            BEGIN
                INSERT OR IGNORE INTO segment_metadata (
                    segment_id,
                    key,
                    str_value,
                    int_value,
                    float_value,
                    bool_value
                )
                VALUES (
                    NEW.segment_id,
                    NEW.key,
                    NEW.str_value,
                    NEW.int_value,
                    NEW.float_value,
                    NEW.bool_value
                );
            END
            """
        )

    result = RAGStore._repair_legacy_collection_config(
        db_dir,
        collection_name="naver_reports",
    )

    assert result["status"] == "ok"
    assert result["segment_metadata_repaired"] == 8


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


def test_sync_documents_splits_failed_large_upsert_batch() -> None:
    collection = _FlakyLargeBatchCollection(min_fail_size=4)
    store = _store(collection, sync_batch_size=4)
    docs = [
        {
            "report_id": idx,
            "chunk_index": 0,
            "content": f"문서 {idx}",
        }
        for idx in range(4)
    ]

    result = store.sync_documents(docs)

    assert result["status"] == "ok"
    assert result["synced"] == 4
    assert result["batches"] == 2
    assert result["upsert_retries"] == 1
    assert [len(row["ids"]) for row in collection.upserts] == [2, 2]


def test_sync_documents_persists_linked_report_metadata() -> None:
    collection = _FakeCollection()
    store = _store(collection)

    result = store.sync_documents(
        [
            {
                "report_id": 10,
                "chunk_index": 0,
                "content": "KODEX 200(069500) 비중 점검",
                "symbol": "",
                "linked_symbols": "069500",
                "linked_names": "KODEX 200",
                "linked_asset_classes": "etf",
            }
        ]
    )

    assert result["status"] == "ok"
    metadata = collection.upserts[0]["metadatas"][0]
    assert metadata["symbol"] == ""
    assert metadata["linked_symbols"] == "069500"
    assert metadata["linked_names"] == "KODEX 200"
    assert metadata["linked_asset_classes"] == "etf"


def test_sync_documents_skips_low_value_boilerplate_chunks() -> None:
    collection = _FakeCollection()
    store = _store(collection, sync_batch_size=2)

    result = store.sync_documents(
        [
            {
                "report_id": 1,
                "chunk_index": 0,
                "content": "삼성전자 메모리 가격 반등과 HBM 수요 개선으로 실적 추정치가 상향되고 있다.",
            },
            {
                "report_id": 2,
                "chunk_index": 0,
                "content": (
                    "[Compliance Notice] 본 조사분석자료는 금융투자분석사가 "
                    "타인의 부당한 압력 없이 작성했으며 당사는 동 자료를 "
                    "전문투자자에게 사전 제공한 사실이 없습니다."
                ),
            },
            {
                "report_id": 3,
                "chunk_index": 0,
                "content": "€ ▪ ▪ ▪ ▪",
            },
        ]
    )

    assert result["status"] == "ok"
    assert result["input_docs"] == 3
    assert result["skipped_low_value"] == 2
    assert result["synced"] == 1
    assert collection.upserts[0]["documents"] == [
        "삼성전자 메모리 가격 반등과 HBM 수요 개선으로 실적 추정치가 상향되고 있다."
    ]


def test_status_uses_sqlite_count_when_chroma_count_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "chroma.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                collection TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                segment_id TEXT NOT NULL,
                embedding_id TEXT NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO collections (id, name) VALUES ('c1', 'test')")
        conn.execute(
            """
            INSERT INTO segments (id, type, scope, collection)
            VALUES (
                's1',
                'urn:chroma:segment/vector/hnsw-local-persisted',
                'VECTOR',
                'c1'
            )
            """
        )
        conn.executemany(
            "INSERT INTO embeddings (id, segment_id, embedding_id) VALUES (?, 's1', ?)",
            [(1, "a"), (2, "b"), (3, "c")],
        )
    collection = _FakeCollection()
    collection.raise_on_count = True
    store = _store(collection)
    store.config = RAGStoreConfig(
        persist_path=str(tmp_path),
        collection_name="test",
    )

    status = store.status()

    assert status["status"] == "degraded"
    assert status["degradation_reason"] == "chroma_count_failed"
    assert status["count"] == 3
    assert status["count_source"] == "sqlite_fallback"
    assert "count unavailable" in status["count_error"]


def test_rebuild_persistent_store_backs_up_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_path = tmp_path / "rag"
    persist_path.mkdir()
    (persist_path / "chroma.sqlite3").write_text("old-store", encoding="utf-8")
    store = RAGStore.__new__(RAGStore)
    store.config = RAGStoreConfig(persist_path=str(persist_path))
    store._available = True
    store._client = object()
    store._collection = object()
    store._init_error = ""
    store._repair_result = {}
    store._last_query_error = ""
    initialize_calls: list[bool] = []

    def fake_initialize(self: RAGStore) -> None:
        initialize_calls.append(Path(self.config.persist_path).exists())
        self._available = True
        self._client = object()
        self._collection = object()
        self._init_error = ""

    monkeypatch.setattr(RAGStore, "_initialize", fake_initialize, raising=False)

    result = store.rebuild_persistent_store()

    assert result["status"] == "ok"
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert (backup_path / "chroma.sqlite3").read_text(encoding="utf-8") == "old-store"
    assert persist_path.exists()
    assert not (persist_path / "chroma.sqlite3").exists()
    assert initialize_calls == [True]


def test_prune_low_value_documents_supports_dry_run_and_delete() -> None:
    keep_id = hashlib.sha256("1:0".encode("utf-8")).hexdigest()
    compliance_id = hashlib.sha256("2:0".encode("utf-8")).hexdigest()
    symbol_noise_id = hashlib.sha256("3:0".encode("utf-8")).hexdigest()
    collection = _FakeCollection(
        existing_ids={keep_id, compliance_id, symbol_noise_id},
        documents_by_id={
            keep_id: "삼성전자 메모리 가격 반등과 HBM 수요 개선으로 실적 추정치가 상향되고 있다.",
            compliance_id: (
                "[Compliance Notice] 본 조사분석자료는 금융투자분석사가 "
                "타인의 부당한 압력 없이 작성했으며 당사는 동 자료를 "
                "전문투자자에게 사전 제공한 사실이 없습니다."
            ),
            symbol_noise_id: "€ ▪ ▪ ▪ ▪",
        },
    )
    store = _store(collection, sync_batch_size=2)

    dry_run = store.prune_low_value_documents(dry_run=True)

    assert dry_run["status"] == "ok"
    assert dry_run["dry_run"] is True
    assert dry_run["scanned_vectors"] == 3
    assert dry_run["low_value_candidates"] == 2
    assert dry_run["deleted_low_value"] == 0
    assert collection.deletes == []

    result = store.prune_low_value_documents(dry_run=False)

    assert result["status"] == "ok"
    assert result["dry_run"] is False
    assert result["low_value_candidates"] == 2
    assert result["deleted_low_value"] == 2
    assert collection.deletes == [[compliance_id, symbol_noise_id]]
    assert collection.existing_ids == {keep_id}


def test_prune_low_value_documents_reports_delete_failure() -> None:
    compliance_id = hashlib.sha256("2:0".encode("utf-8")).hexdigest()
    collection = _FakeCollection(
        existing_ids={compliance_id},
        documents_by_id={
            compliance_id: (
                "[Compliance Notice] 본 조사분석자료는 금융투자분석사가 "
                "기관투자자에게 사전 제공한 사실이 없습니다."
            ),
        },
    )
    collection.raise_on_delete = True
    store = _store(collection, sync_batch_size=2)

    result = store.prune_low_value_documents(dry_run=False)

    assert result["status"] == "error"
    assert result["reason"] == "rag_low_value_delete_failed"
    assert result["deleted_low_value"] == 0
    assert result["low_value_candidates"] == 1
    assert "delete unavailable" in result["detail"]
    assert collection.existing_ids == {compliance_id}


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


def test_sync_documents_prunes_orphan_vectors_when_requested() -> None:
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
        },
        {
            "report_id": 2,
            "chunk_index": 0,
            "content": "문서 2",
        },
    ]

    result = store.sync_documents(docs, prune_missing=True)

    assert result["status"] == "ok"
    assert result["deleted_orphans"] == 1
    assert result["scanned_vectors"] == 3
    assert stale_id not in collection.existing_ids
    assert collection.deletes == [[stale_id]]


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


def test_migrates_legacy_hnsw_metadata_backfills_missing_dimension(
    tmp_path: Path,
) -> None:
    mod = pytest.importorskip("chromadb.segment.impl.vector.local_persistent_hnsw")
    db_path = tmp_path / "chroma.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                dimension INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                collection TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO collections (id, name, dimension) VALUES ('c1', 'naver_reports', 384)"
        )
        conn.execute(
            """
            INSERT INTO segments (id, type, scope, collection)
            VALUES (
                'segment-1',
                'urn:chroma:segment/vector/hnsw-local-persisted',
                'VECTOR',
                'c1'
            )
            """
        )
    segment_dir = tmp_path / "segment-1"
    segment_dir.mkdir()
    metadata_path = segment_dir / "index_metadata.pickle"
    metadata_path.write_bytes(
        pickle.dumps(
            {
                "dimensionality": None,
                "total_elements_added": 2,
                "max_seq_id": None,
                "id_to_label": {"a": 1},
                "label_to_id": {1: "a"},
                "id_to_seq_id": {},
            }
        )
    )

    migrated = RAGStore._migrate_legacy_hnsw_metadata(tmp_path)
    restored = pickle.loads(metadata_path.read_bytes())

    assert migrated == 1
    assert isinstance(restored, mod.PersistentData)
    assert restored.dimensionality == 384


def test_query_uses_combined_filters_and_oversamples_for_date_filters() -> None:
    collection = _FakeCollection()
    store = _store(collection, query_oversample_factor=4)

    rows = store.query(
        query="반도체",
        symbol="069500",
        broker="테스트증권",
        limit=1,
        date_from="2026-01-01",
    )

    assert collection.last_query["n_results"] == 4
    assert collection.last_query["where"] == {"broker": "테스트증권"}
    assert len(rows) == 1
    assert rows[0]["report_id"] == 1
    assert rows[0]["symbol"] == "005930"
    assert rows[0]["linked_symbols"] == "069500"
    assert rows[0]["linked_names"] == "KODEX 200"
    assert rows[0]["linked_asset_classes"] == "etf"


def test_query_prefers_exact_symbol_metadata_before_broad_linked_search() -> None:
    collection = _SymbolAwareQueryCollection()
    store = _store(collection)

    rows = store.query("SK하이닉스 HBM", symbol="000660", limit=3)

    assert rows[0]["report_id"] == 10
    assert rows[0]["symbol"] == "000660"
    assert collection.query_calls[0]["where"] == {"symbol": "000660"}


def test_query_oversamples_and_reranks_direct_keyword_matches() -> None:
    collection = _LexicalRerankCollection()
    store = _store(collection, query_oversample_factor=4)

    rows = store.query("반도체", limit=3)

    assert collection.last_query["n_results"] == 12
    assert len(rows) == 2
    assert {row["report_id"] for row in rows} == {102, 103}
    assert all("화장품" not in row["title"] for row in rows)


def test_query_keeps_semantic_results_when_no_direct_keyword_matches() -> None:
    collection = _FakeCollection()
    store = _store(collection, query_oversample_factor=4)

    rows = store.query("AI capex", limit=2)

    assert len(rows) == 2


def test_query_limits_one_report_from_dominating_results() -> None:
    collection = _DuplicateReportQueryCollection()
    store = _store(collection, query_oversample_factor=4)

    rows = store.query("SK하이닉스 HBM", symbol="000660", limit=3)

    assert collection.last_query["n_results"] == 12
    assert [row["report_id"] for row in rows] == [10, 10, 20]
