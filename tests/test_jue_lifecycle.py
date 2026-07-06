from __future__ import annotations

from pathlib import Path

from tradecraft.services.jue_lifecycle import JueLifecycleRepository


def test_lifecycle_repository_upserts_and_lists_artifacts(tmp_path: Path) -> None:
    repo = JueLifecycleRepository(tmp_path / "memory.db")

    saved = repo.upsert_artifact(
        {
            "artifact_id": "art_1",
            "artifact_type": "morning_note",
            "workflow_id": "kis_morning_note",
            "symbol": "005930",
            "title": "삼성전자 장전 점검",
            "summary_md": "메모리 업황과 수급을 함께 점검한다.",
            "payload": {"block_implications": [{"action": "watch_add"}]},
            "evidence": [{"source_type": "report", "source_id": "r1"}],
            "status": "active",
        }
    )
    rows = repo.list_artifacts(symbols=["005930"], limit=5)

    assert saved["artifact_id"] == "art_1"
    assert rows[0]["workflow_id"] == "kis_morning_note"
    assert rows[0]["payload"]["block_implications"][0]["action"] == "watch_add"


def test_lifecycle_repository_filters_by_workflow(tmp_path: Path) -> None:
    repo = JueLifecycleRepository(tmp_path / "memory.db")
    repo.upsert_artifact(
        {
            "artifact_id": "art_1",
            "artifact_type": "morning_note",
            "workflow_id": "kis_morning_note",
            "symbol": "005930",
            "title": "장전",
            "summary_md": "장전 점검",
        }
    )
    repo.upsert_artifact(
        {
            "artifact_id": "art_2",
            "artifact_type": "idea_screen",
            "workflow_id": "kis_idea_screen",
            "symbol": "000660",
            "title": "아이디어",
            "summary_md": "아이디어 점검",
        }
    )

    rows = repo.list_artifacts(workflow_id="kis_idea_screen", limit=5)

    assert [row["artifact_id"] for row in rows] == ["art_2"]
