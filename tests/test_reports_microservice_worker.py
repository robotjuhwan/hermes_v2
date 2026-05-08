from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

from tradecraft.config import AppSettings
from tradecraft.reports_api import worker as reports_worker


def test_is_symbol_directory_stale() -> None:
    assert reports_worker._is_symbol_directory_stale("", min_age_sec=60)

    fresh = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert not reports_worker._is_symbol_directory_stale(fresh, min_age_sec=60)

    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    assert reports_worker._is_symbol_directory_stale(old, min_age_sec=12 * 3600)


def test_run_cycle_executes_refresh_and_rag(monkeypatch) -> None:
    class _FakeCrawler:
        async def crawl_once(self) -> dict[str, object]:
            return {"status": "ok", "inserted": 2, "repository": {"total_reports": 20}}

    class _FakeRepo:
        def __init__(self) -> None:
            self.refresh_called = 0

        def status(self) -> dict[str, str]:
            return {"symbol_last_updated_at": ""}

        def refresh_symbol_directory_from_krx(self) -> dict[str, object]:
            self.refresh_called += 1
            return {"ok": True, "updated": 10, "as_of": "2026-02-27"}

        def list_chunks_for_rag(self, limit: int = 50000) -> list[dict[str, object]]:
            _ = limit
            return [{"content": "a"}, {"content": "b"}]

        def repair_metadata_quality(self) -> dict[str, object]:
            return {"status": "ok", "updated_reports": 0}

    class _FakeRAG:
        def sync_documents(
            self,
            docs: list[dict[str, object]],
            *,
            force_update: bool = False,
        ) -> dict[str, object]:
            _ = force_update
            return {"status": "ok", "synced": len(docs)}

    settings = AppSettings()
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "rag_sync_chunk_limit", 2)

    fake_repo = _FakeRepo()
    result = asyncio.run(
        reports_worker.run_cycle(
            crawler=_FakeCrawler(),
            repository=fake_repo,  # type: ignore[arg-type]
            rag_store=_FakeRAG(),  # type: ignore[arg-type]
            settings=settings,
        )
    )

    assert int((result["snapshot"] or {}).get("inserted") or 0) == 2
    assert bool((result["symbol_refresh"] or {}).get("ok")) is True
    assert int((result["rag_sync"] or {}).get("synced") or 0) == 2
    assert fake_repo.refresh_called == 1


def test_run_writes_disabled_worker_state(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "reports-worker.json"
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_ENABLED", "false")
    monkeypatch.setenv("TRADECRAFT_REPORTS_WORKER_STATE_PATH", str(state_path))

    reports_worker.run()

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["status"] == "disabled"
    assert payload["service"] == "reports_worker"
