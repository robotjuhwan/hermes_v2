from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_playbooks import JueWikiPlaybookCompiler


def _service(tmp_path: Path) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )


def test_compile_all_writes_kis_and_binance_reflection_playbooks(
    tmp_path: Path,
) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                scope TEXT,
                symbol TEXT,
                lesson TEXT,
                summary TEXT,
                created_at TEXT,
                pnl_krw REAL,
                pnl_usdt REAL
            );
            INSERT INTO block_reflections VALUES (
                'kis_blk_1', 'kis', '005930', '저평가 눌림목은 분할 진입한다.',
                'KIS fallback summary', '2026-06-27T00:00:00+00:00', 12000, NULL
            );
            INSERT INTO block_reflections VALUES (
                'bn_blk_1', 'binance', 'BTCUSDT', NULL,
                'Breakout entries need tighter invalidation.',
                '2026-06-28T00:00:00+00:00', NULL, 18.5
            );
            """
        )
    service = _service(tmp_path)

    result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=memory_db,
    ).compile_all()

    assert result == {"status": "ok", "updated_count": 2}
    kis_page = service.read_page("kis.playbook.reflection_lessons")
    binance_page = service.read_page("binance.playbook.reflection_lessons")
    assert kis_page["status"] == "ok"
    assert binance_page["status"] == "ok"
    assert "# KIS Reflection Lessons" in kis_page["content"]
    assert "저평가 눌림목은 분할 진입한다." in kis_page["content"]
    assert "## Entry Conditions" in kis_page["content"]
    assert "## Performance Evidence" in kis_page["content"]
    assert "# BINANCE Reflection Lessons" in binance_page["content"]
    assert "Breakout entries need tighter invalidation." in binance_page["content"]

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        refs = conn.execute(
            """
            SELECT page_id, source_type, source_id
            FROM wiki_source_refs
            ORDER BY page_id, source_id
            """
        ).fetchall()

    assert refs == [
        ("binance.playbook.reflection_lessons", "block_reflections", "bn_blk_1"),
        ("kis.playbook.reflection_lessons", "block_reflections", "kis_blk_1"),
    ]


def test_compile_all_missing_db_or_table_returns_ok_zero_updates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    missing_db_result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=tmp_path / "missing.db",
    ).compile_all()
    assert missing_db_result["status"] == "ok"
    assert missing_db_result["updated_count"] == 0

    empty_db = tmp_path / "empty_memory.db"
    with sqlite3.connect(empty_db):
        pass

    missing_table_result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=empty_db,
    ).compile_all()
    assert missing_table_result["status"] == "ok"
    assert missing_table_result["updated_count"] == 0


def test_compile_all_malformed_reflections_table_returns_error_without_pages(
    tmp_path: Path,
) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                scope TEXT,
                symbol TEXT
            );
            INSERT INTO block_reflections VALUES ('kis', '005930');
            """
        )
    service = _service(tmp_path)

    result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=memory_db,
    ).compile_all()

    assert result["status"] == "error"
    assert result["updated_count"] == 0
    assert "lesson/text column" in result["error_message"]
    assert service.read_page("kis.playbook.reflection_lessons")["status"] == "not_found"
    assert (
        service.read_page("binance.playbook.reflection_lessons")["status"]
        == "not_found"
    )


def test_compile_all_skips_empty_lesson_rows_without_writing_fresh_pages(
    tmp_path: Path,
) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                scope TEXT,
                symbol TEXT,
                lesson TEXT,
                summary TEXT,
                reflection_md TEXT,
                notes_md TEXT,
                created_at TEXT
            );
            INSERT INTO block_reflections VALUES (
                'kis_empty_1', 'kis', '005930', '', NULL, '   ', '',
                '2026-06-28T00:00:00+00:00'
            );
            INSERT INTO block_reflections VALUES (
                'bn_empty_1', 'binance', 'BTCUSDT', NULL, '', NULL, '   ',
                '2026-06-28T00:00:00+00:00'
            );
            """
        )
    service = _service(tmp_path)

    result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=memory_db,
    ).compile_all()

    assert result == {"status": "ok", "updated_count": 0, "skipped_count": 2}
    assert service.read_page("kis.playbook.reflection_lessons")["status"] == "not_found"
    assert (
        service.read_page("binance.playbook.reflection_lessons")["status"]
        == "not_found"
    )


def test_compile_all_uses_lesson_md_when_plain_lesson_is_absent(
    tmp_path: Path,
) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                scope TEXT,
                symbol TEXT,
                lesson_md TEXT,
                created_at TEXT
            );
            INSERT INTO block_reflections VALUES (
                'kis_md_1', 'kis', '000660',
                'lesson_md 기반으로 손절선을 먼저 고정한다.',
                '2026-06-28T00:00:00+00:00'
            );
            """
        )
    service = _service(tmp_path)

    result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=memory_db,
    ).compile_all()

    assert result == {"status": "ok", "updated_count": 1}
    page = service.read_page("kis.playbook.reflection_lessons")
    assert "lesson_md 기반으로 손절선을 먼저 고정한다." in page["content"]


def test_compile_all_limits_reflection_load_to_recent_200(
    tmp_path: Path,
) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.execute(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                scope TEXT,
                symbol TEXT,
                lesson TEXT,
                created_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO block_reflections VALUES (?, 'kis', '005930', ?, ?)
            """,
            [
                (
                    f"kis_blk_{index:03d}",
                    f"lesson {index:03d}",
                    "2026-06-28T00:00:00+00:00",
                )
                for index in range(205)
            ],
        )
    service = _service(tmp_path)

    result = JueWikiPlaybookCompiler(
        service,
        investment_memory_db_path=memory_db,
    ).compile_all()

    assert result == {"status": "ok", "updated_count": 1}
    page = service.read_page("kis.playbook.reflection_lessons")
    assert "Reflections reviewed: 200." in page["content"]
    assert "kis_blk_204" in page["content"]
    assert "kis_blk_005" in page["content"]
    assert "kis_blk_004" not in page["content"]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        ref_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM wiki_source_refs
            WHERE page_id = 'kis.playbook.reflection_lessons'
            """
        ).fetchone()[0]
    assert ref_count == 200
