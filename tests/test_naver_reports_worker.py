from __future__ import annotations

import json
from types import SimpleNamespace

from tradecraft.runtime import naver_reports_worker


def test_naver_reports_worker_writes_progress_and_result(tmp_path, monkeypatch) -> None:
    class Crawler:
        async def crawl_once(self) -> dict[str, object]:
            return {"inserted": 1}

    class Repository:
        def status(self) -> dict[str, object]:
            return {"symbol_last_updated_at": ""}

        def refresh_symbol_directory_from_krx(self) -> dict[str, object]:
            return {"ok": True, "updated": 2}

        def repair_metadata_quality(self) -> dict[str, object]:
            return {"updated_reports": 0}

    stack = SimpleNamespace(
        crawler=Crawler(),
        repository=Repository(),
        rag_store=None,
    )
    monkeypatch.setattr(
        naver_reports_worker,
        "build_report_intelligence_stack",
        lambda _settings: stack,
    )
    result_path = tmp_path / "result.json"
    progress_path = tmp_path / "progress.json"

    result = naver_reports_worker.run_worker(
        result_path=result_path,
        progress_path=progress_path,
        settings=SimpleNamespace(rag_enabled=False, rag_sync_chunk_limit=100),
    )

    persisted_result = json.loads(result_path.read_text(encoding="utf-8"))
    persisted_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert persisted_result["snapshot"] == {"inserted": 1}
    assert persisted_progress["stage"] == "cycle_completed"
