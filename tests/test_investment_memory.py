from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta, timezone
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryRepository,
    InvestmentMemoryService,
    KST,
    _compact_active_policies_for_ritual_prompt,
    _compact_jue_wiki_selection_memory,
    _compact_policy_summaries_for_budget,
    _compact_jue_workflow,
    _compact_ritual_context,
)
from tradecraft.services.jue_decision_packet import build_decision_lifecycle_packet
from tradecraft.services.trading_validation import DISCIPLINE_DEFINITIONS


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"
    resolved_reasoning_effort = "xhigh"

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}
        self.calls: list[dict] = []

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.calls.append(payload)
        return {"ok": True, "content": json.dumps(self.payload, ensure_ascii=False)}


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, parse_mode=None, chat_id=None) -> dict:
        _ = (parse_mode, chat_id)
        self.messages.append(text)
        return {"ok": True, "message_id": len(self.messages)}


class _FailingLLM:
    ready = True
    resolved_model = "gpt-5.5"
    resolved_reasoning_effort = "xhigh"

    def __init__(self, error: str) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.calls.append(payload)
        return {"ok": False, "error": self.error}


class _OpenCalendar:
    def is_open_day(self, value: date) -> bool:
        return value.weekday() < 5


class _HolidayCalendar:
    def __init__(self, holidays: set[date]) -> None:
        self.holidays = holidays

    def is_open_day(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays


def _service(
    tmp_path: Path,
    *,
    llm_payload: dict | None = None,
    codex_runtime: object | None = None,
    telegram: _FakeTelegram | None = None,
    wiki_context_provider: object | None = None,
) -> InvestmentMemoryService:
    strategy = tmp_path / "strategy_krx.md"
    strategy.write_text(
        "# 전략 노하우\n\n- 추격 매수보다 블록 손절 약속을 우선한다.\n\n본문 노이즈" * 20,
        encoding="utf-8",
    )
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy),
        ),
        codex_runtime=(
            codex_runtime
            if codex_runtime is not None
            else _FakeLLM(llm_payload) if llm_payload is not None else None
        ),  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        wiki_context_provider=wiki_context_provider,  # type: ignore[arg-type]
    )
    service.calendar = _OpenCalendar()  # type: ignore[assignment]
    return service


def test_investment_memory_repository_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with service.repository._connect() as conn:  # noqa: SLF001
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_repository_migrates_legacy_scope_tables_before_creating_scope_indexes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy_memory.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE period_reviews (
                period_key TEXT NOT NULL,
                period_type TEXT NOT NULL,
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                mode TEXT NOT NULL DEFAULT 'deterministic',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                review_md TEXT NOT NULL DEFAULT '',
                policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(period_key, period_type)
            );
            INSERT INTO period_reviews (
                period_key, period_type, start_date, end_date, status, mode,
                metrics_json, review_md, policy_revision_ids_json, created_at, updated_at
            )
            VALUES (
                '2026-W21', 'weekly', '2026-05-18', '2026-05-22', 'ok', 'llm',
                '{}', 'legacy review', '[]', '2026-05-22T07:00:00+00:00',
                '2026-05-22T07:00:00+00:00'
            );

            CREATE TABLE historical_replays (
                period_key TEXT NOT NULL,
                period_type TEXT NOT NULL,
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                mode TEXT NOT NULL DEFAULT 'deterministic',
                case_count INTEGER NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                replay_md TEXT NOT NULL DEFAULT '',
                case_reviews_json TEXT NOT NULL DEFAULT '[]',
                policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(period_key, period_type)
            );
            INSERT INTO historical_replays (
                period_key, period_type, start_date, end_date, status, mode,
                case_count, metrics_json, replay_md, case_reviews_json,
                policy_revision_ids_json, created_at, updated_at
            )
            VALUES (
                '2026-W21', 'weekly', '2026-05-18', '2026-05-22', 'ok', 'llm',
                1, '{}', 'legacy replay', '[]', '[]',
                '2026-05-22T07:00:00+00:00', '2026-05-22T07:00:00+00:00'
            );

            CREATE TABLE policy_revisions (
                revision_id TEXT PRIMARY KEY,
                period_key TEXT NOT NULL DEFAULT '',
                period_type TEXT NOT NULL DEFAULT '',
                policy_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT 'keep',
                status TEXT NOT NULL DEFAULT 'candidate',
                scope TEXT NOT NULL DEFAULT 'general',
                condition_json TEXT NOT NULL DEFAULT '{}',
                effect_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                reason_md TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                activated_at TEXT NOT NULL DEFAULT '',
                retired_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE policy_outcomes (
                policy_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                period_key TEXT NOT NULL,
                period_type TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                avg_pnl_pct REAL NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0,
                expectancy_pct REAL NOT NULL DEFAULT 0,
                max_drawdown_pct REAL NOT NULL DEFAULT 0,
                rule_follow_rate REAL NOT NULL DEFAULT 0,
                helped_count INTEGER NOT NULL DEFAULT 0,
                hurt_count INTEGER NOT NULL DEFAULT 0,
                notes_md TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(policy_id, rule_id, period_key, period_type)
            );
            """
        )

    repository = InvestmentMemoryRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        period_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(period_reviews)")
        }
        replay_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(historical_replays)")
        }
        revision_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(policy_revisions)")
        }
        outcome_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(policy_outcomes)")
        }

    assert "memory_scope" in period_columns
    assert "memory_scope" in replay_columns
    assert "memory_scope" in revision_columns
    assert "memory_scope" in outcome_columns
    assert repository.latest_period_review(
        "weekly",
        target_scope="core",
    )["period_key"] == "2026-W21"


def test_policy_scorecards_persist_scope_transferability_index_columns(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "kis.value.waiting",
                "scope": "kis",
                "transferability": "direct",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.31,
                "evidence_ids": ["kis-value-waiting"],
            },
            {
                "policy_id": "binance.breakout.probe",
                "scope": "binance",
                "transferability": "translated",
                "sample_count": 8,
                "confidence": 0.74,
                "expectancy_r": 0.42,
                "evidence_ids": ["binance-breakout-probe"],
            },
        ]
    )

    with sqlite3.connect(service.config.db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(policy_scorecards)").fetchall()
        }
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(policy_scorecards)").fetchall()
        }
        rows = conn.execute(
            """
            SELECT policy_id, memory_scope, transferability
            FROM policy_scorecards
            ORDER BY policy_id
            """
        ).fetchall()

    assert {"memory_scope", "transferability"}.issubset(columns)
    assert "idx_policy_scorecards_scope_status" in indexes
    assert rows == [
        ("binance.breakout.probe", "binance", "translated"),
        ("kis.value.waiting", "kis", "direct"),
    ]


def test_policy_rules_persist_scope_transferability_index_columns(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "kis.value.waiting",
                "scope": "kis",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.31,
                "evidence_ids": ["kis-value-waiting"],
            },
            {
                "policy_id": "binance.breakout.probe",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.74,
                "expectancy_r": 0.42,
                "evidence_ids": ["binance-breakout-probe"],
            },
        ]
    )
    service.sync_policy_rules()

    with sqlite3.connect(service.config.db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(policy_rules)").fetchall()
        }
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(policy_rules)").fetchall()
        }
        rows = conn.execute(
            """
            SELECT policy_id, memory_scope, transferability
            FROM policy_rules
            ORDER BY policy_id
            """
        ).fetchall()

    assert {"memory_scope", "transferability"}.issubset(columns)
    assert "idx_policy_rules_scope_status" in indexes
    assert rows == [
        ("binance.breakout.probe", "binance", "translated"),
        ("kis.value.waiting", "kis", "translated"),
    ]


def test_initialize_creates_structured_markdown_without_raw_legacy_copy(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.initialize()
    root = tmp_path / "memory"

    assert result["status"] == "ok"
    assert (root / "persona.md").exists()
    persona = (root / "persona.md").read_text(encoding="utf-8")
    assert "쥬" in persona
    assert "보수적" not in persona
    assert "적극" in persona
    assert "수익" in persona
    assert (root / "policies" / "trading.md").exists()
    trading_policy = (root / "policies" / "trading.md").read_text(encoding="utf-8")
    assert "실거래 수익" in trading_policy
    assert "probe" in trading_policy
    legacy = (root / "policies" / "legacy_strategy_extract.md").read_text(
        encoding="utf-8"
    )
    assert "Legacy Strategy Extract" in legacy
    assert "본문 노이즈본문 노이즈" not in legacy


def test_initialize_creates_decision_skill_files_and_context_pack_loads_them(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    result = service.initialize()
    root = tmp_path / "memory"
    pack = service.context_pack(symbols=["005930"], block_ids=["blk_005930_1"])

    assert result["status"] == "ok"
    expected = {
        "block_manager",
        "market_judge",
        "risk_manager",
        "reflection",
    }
    for skill_id in expected:
        path = root / "skills" / f"{skill_id}.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert f"skill_id: jue.{skill_id}.v1" in text
        assert "쥬" in text

    assert set(pack["decision_skills"]) == expected
    assert pack["decision_skills"]["block_manager"]["version"] == "jue.block_manager.v1"
    assert "블록" in pack["decision_skills"]["block_manager"]["content_md"]
    assert pack["decision_skill_status"]["count"] == 4
    assert pack["decision_skill_status"]["missing"] == []


def test_context_pack_trims_decision_skill_content_when_size_budget_is_small(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    pack = service.context_pack(max_chars=1000)
    serialized = json.dumps(pack, ensure_ascii=False, sort_keys=True)

    assert len(serialized) <= 1600
    assert set(pack["decision_skills"]) == {
        "block_manager",
        "market_judge",
        "risk_manager",
        "reflection",
    }
    assert pack["decision_skills"]["block_manager"]["skill_id"] == "block_manager"
    assert pack["decision_skills"]["block_manager"]["version"] == "jue.block_manager.v1"
    assert pack["decision_skill_status"]["count"] == 4
    assert pack["decision_skill_status"]["missing"] == []


def test_context_pack_filters_memory_by_target_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    repo = service.repository
    repo.save_insight(
        memory_type="block",
        key="blk_005930_scope_test",
        status="active",
        confidence=0.8,
        summary_md="KIS block memory",
        evidence=[{"memory_scope": "kis", "transferability": "direct"}],
    )
    repo.save_insight(
        memory_type="block",
        key="bnb_futures_BTCUSDT_scope_test",
        status="active",
        confidence=0.8,
        summary_md="Binance block memory",
        evidence=[{"memory_scope": "binance", "transferability": "direct"}],
    )
    repo.upsert_block_reflection(
        {
            "block_id": "blk_005930_scope_test",
            "symbol": "005930",
            "pnl_pct": 1.2,
            "exit_reason": "target_reached",
            "lesson_md": "KIS reflection",
            "metrics": {"memory_scope": "kis", "transferability": "direct"},
        }
    )
    repo.upsert_block_reflection(
        {
            "block_id": "bnb_futures_BTCUSDT_scope_test",
            "symbol": "BTCUSDT",
            "pnl_pct": -0.4,
            "exit_reason": "stop_reached",
            "lesson_md": "Binance reflection",
            "metrics": {"memory_scope": "binance", "transferability": "direct"},
        }
    )

    kis_pack = service.context_pack(target_scope="kis", max_chars=8000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=8000)

    assert {row["key"] for row in kis_pack["active_insights"]} == {
        "blk_005930_scope_test"
    }
    assert {row["key"] for row in binance_pack["active_insights"]} == {
        "bnb_futures_BTCUSDT_scope_test"
    }
    assert [row["block_id"] for row in kis_pack["recent_reflections"]] == [
        "blk_005930_scope_test"
    ]
    assert [row["block_id"] for row in binance_pack["recent_reflections"]] == [
        "bnb_futures_BTCUSDT_scope_test"
    ]
    assert kis_pack["memory_scope"] == "kis"
    assert binance_pack["memory_scope"] == "binance"


def test_context_pack_fetches_target_scope_active_insights_beyond_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_insights (
                memory_type, key, memory_scope, transferability, status,
                confidence, summary_md, evidence_json, source_run_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "block",
                "bnb_futures_ETHUSDT_slow_but_local",
                "binance",
                "direct",
                "active",
                0.76,
                "Binance local insight must not be crowded out by newer KIS insights.",
                "[]",
                None,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.executemany(
            """
            INSERT INTO memory_insights (
                memory_type, key, memory_scope, transferability, status,
                confidence, summary_md, evidence_json, source_run_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "block",
                    f"blk_005930_recent_kis_{idx:03d}",
                    "kis",
                    "direct",
                    "active",
                    0.9,
                    "Newer KIS insight.",
                    "[]",
                    None,
                    f"2026-01-02T00:{idx:02d}:00+00:00",
                )
                for idx in range(90)
            ],
        )

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["key"] for row in pack["active_insights"]
    } >= {"bnb_futures_ETHUSDT_slow_but_local"}


def test_context_pack_includes_jue_wiki_provider_context(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def wiki_context_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "ok",
            "target_scope": kwargs["target_scope"],
            "symbols": kwargs["symbols"],
            "budget": kwargs["max_chars"],
            "content": "삼성전자 wiki context",
        }

    service = _service(tmp_path, wiki_context_provider=wiki_context_provider)
    service.initialize()

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930", "", "BTCUSDT"],
        max_chars=18000,
    )

    assert calls == [
        {
            "target_scope": "kis",
            "symbols": ["005930", "BTCUSDT"],
            "max_chars": 6000,
        }
    ]
    assert pack["jue_wiki"] == {
        "status": "ok",
        "target_scope": "kis",
        "symbols": ["005930", "BTCUSDT"],
        "budget": 6000,
        "content": "삼성전자 wiki context",
    }


def test_context_pack_keeps_working_when_jue_wiki_provider_fails(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def wiki_context_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        raise RuntimeError("wiki unavailable")

    service = _service(tmp_path, wiki_context_provider=wiki_context_provider)
    service.initialize()

    pack = service.context_pack(
        target_scope="binance",
        symbols=["BTCUSDT"],
        max_chars=12000,
    )

    assert pack["status"] == "ok"
    assert calls == [
        {
            "target_scope": "binance",
            "symbols": ["BTCUSDT"],
            "max_chars": 4000,
        }
    ]
    assert pack["jue_wiki"]["status"] == "error"
    assert pack["jue_wiki"]["available"] is False
    assert pack["jue_wiki"]["reason"] == "wiki unavailable"


def test_today_payload_can_be_scoped_for_kis_and_binance_memory_tabs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    repo = service.repository
    repo.upsert_block_reflection(
        {
            "block_id": "blk_005930_today_scope_test",
            "symbol": "005930",
            "pnl_pct": 2.0,
            "lesson_md": "KIS today reflection",
            "metrics": {"memory_scope": "kis", "transferability": "direct"},
        }
    )
    repo.upsert_block_reflection(
        {
            "block_id": "bnb_futures_ETHUSDT_today_scope_test",
            "symbol": "ETHUSDT",
            "pnl_pct": -1.0,
            "lesson_md": "Binance today reflection",
            "metrics": {"memory_scope": "binance", "transferability": "direct"},
        }
    )

    kis_today = service.today(scope="kis")
    binance_today = service.today(scope="binance")

    assert kis_today["memory_scope"] == "kis"
    assert binance_today["memory_scope"] == "binance"
    assert [row["block_id"] for row in kis_today["recent_reflections"]] == [
        "blk_005930_today_scope_test"
    ]
    assert [row["block_id"] for row in binance_today["recent_reflections"]] == [
        "bnb_futures_ETHUSDT_today_scope_test"
    ]


def test_today_compact_payload_trims_memory_tab_bloat(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    trading_day = "2026-05-13"
    long_tail = "x" * 5000
    service.repository.upsert_journal(
        trading_day=trading_day,
        slot="post_close",
        title="KIS compact journal",
        message_md=f"{long_tail} JOURNAL_COMPACT_SHOULD_NOT_LEAK",
        file_path="journals/2026-05-13/post_close.md",
        context={"memory_scope": "kis"},
    )
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_005930_compact",
            "symbol": "005930",
            "pnl_pct": 2.0,
            "lesson_md": f"{long_tail} REFLECTION_COMPACT_SHOULD_NOT_LEAK",
            "metrics": {"memory_scope": "kis", "transferability": "direct"},
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.kis.cost_simulation",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 9,
            "avg_pnl_pct": -0.4,
            "confidence": 0.8,
            "reason": f"{long_tail} SCORECARD_COMPACT_SHOULD_NOT_LEAK",
        }
    )
    service.repository.upsert_policy_rule(
        {
            "policy_id": "validation.kis.cost_simulation",
            "version": 1,
            "rule_id": "validation.kis.cost_simulation@v1",
            "status": "active_caution",
            "action": "caution",
            "effect": {"entry_bias": "wait_for_price", "hard_filter": False},
            "reason": f"{long_tail} RULE_COMPACT_SHOULD_NOT_LEAK",
        }
    )

    compact = service.today(
        scope="kis",
        compact=True,
        now=datetime(2026, 5, 13, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    full = service.today(
        scope="kis",
        now=datetime(2026, 5, 13, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    compact_serialized = json.dumps(compact, ensure_ascii=False)

    assert compact["compact"] is True
    assert compact["memory_scope"] == "kis"
    assert "context_pack" in compact
    assert compact["context_pack"]["status"] == "compact"
    assert "decision_skills" not in compact["context_pack"]
    assert full["context_pack"]["status"] == "ok"
    assert "JOURNAL_COMPACT_SHOULD_NOT_LEAK" not in compact_serialized
    assert "REFLECTION_COMPACT_SHOULD_NOT_LEAK" not in compact_serialized
    assert "SCORECARD_COMPACT_SHOULD_NOT_LEAK" not in compact_serialized
    assert "RULE_COMPACT_SHOULD_NOT_LEAK" not in compact_serialized
    assert len(compact_serialized) < 20_000


def test_status_compact_payload_trims_memory_status_bloat(tmp_path: Path) -> None:
    service = _service(tmp_path, codex_runtime=_FakeLLM())
    service.initialize()
    long_tail = "x" * 5000
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_005930_status_compact",
            "symbol": "005930",
            "pnl_pct": 2.0,
            "lesson_md": f"{long_tail} STATUS_REFLECTION_SHOULD_NOT_LEAK",
            "metrics": {"memory_scope": "kis", "transferability": "direct"},
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.kis.status_compact",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 9,
            "avg_pnl_pct": -0.4,
            "confidence": 0.8,
            "reason": f"{long_tail} STATUS_SCORECARD_SHOULD_NOT_LEAK",
        }
    )
    service.repository.upsert_policy_rule(
        {
            "policy_id": "validation.kis.status_compact",
            "version": 1,
            "rule_id": "validation.kis.status_compact@v1",
            "status": "active_caution",
            "action": "caution",
            "effect": {"entry_bias": "wait_for_price", "hard_filter": False},
            "reason": f"{long_tail} STATUS_RULE_SHOULD_NOT_LEAK",
        }
    )

    compact = service.status(scope="kis", compact=True)
    full = service.status(scope="kis")
    compact_serialized = json.dumps(compact, ensure_ascii=False)

    assert compact["compact"] is True
    assert compact["memory_scope"] == "kis"
    assert compact["model"] == "gpt-5.5"
    assert compact["reasoning_effort"] == "xhigh"
    assert full["model"] == "gpt-5.5"
    assert full["reasoning_effort"] == "xhigh"
    assert compact["validation_recovery_summary"]["status"] in {
        "clear",
        "needs_repair",
    }
    assert "STATUS_REFLECTION_SHOULD_NOT_LEAK" not in compact_serialized
    assert "STATUS_SCORECARD_SHOULD_NOT_LEAK" not in compact_serialized
    assert "STATUS_RULE_SHOULD_NOT_LEAK" not in compact_serialized
    assert "compact" not in full
    assert len(compact_serialized) < 20_000


def test_status_payload_can_be_scoped_for_kis_and_binance_memory_tabs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    repo = service.repository
    repo.upsert_block_reflection(
        {
            "block_id": "blk_005930_status_scope_test",
            "symbol": "005930",
            "pnl_pct": 1.4,
            "lesson_md": "KIS status reflection",
            "metrics": {"memory_scope": "kis", "transferability": "direct"},
        }
    )
    repo.upsert_block_reflection(
        {
            "block_id": "bnb_futures_ETHUSDT_status_scope_test",
            "symbol": "ETHUSDT",
            "pnl_pct": -0.6,
            "lesson_md": "Binance status reflection",
            "metrics": {"memory_scope": "binance", "transferability": "direct"},
        }
    )
    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "kis.status.scope.policy",
                "scope": "kis",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.2,
            },
            {
                "policy_id": "binance.status.scope.policy",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.74,
                "expectancy_r": 0.3,
            },
        ]
    )
    repo.save_run(
        kind="ritual",
        slot="pre_open",
        status="ok",
        mode="llm",
        model="gpt-5.5",
        error_message="",
        input_payload={"context": {"memory_scope": "kis"}},
        output_payload={"title": "KIS scoped run"},
    )
    repo.save_run(
        kind="ritual",
        slot="morning",
        status="ok",
        mode="llm",
        model="gpt-5.5",
        error_message="",
        input_payload={"context": {"memory_scope": "binance"}},
        output_payload={"title": "Binance scoped run"},
    )
    repo.upsert_journal(
        trading_day="2026-07-01",
        slot="pre_open",
        title="KIS scoped journal",
        message_md="KIS scoped telegram",
        file_path=str(tmp_path / "memory" / "journals" / "2026-07-01" / "pre_open.md"),
        context={"memory_scope": "kis"},
        sent_telegram=True,
        telegram_result={"detail": {"result": {"text": "KIS scoped telegram"}}},
    )
    repo.upsert_journal(
        trading_day="2026-07-01",
        slot="morning",
        title="Binance scoped journal",
        message_md="Binance scoped telegram",
        file_path=str(tmp_path / "memory" / "journals" / "2026-07-01" / "morning.md"),
        context={"memory_scope": "binance"},
        sent_telegram=True,
        telegram_result={"detail": {"result": {"text": "Binance scoped telegram"}}},
    )

    kis_status = service.status(scope="kis")
    binance_status = service.status(scope="binance")

    assert kis_status["memory_scope"] == "kis"
    assert binance_status["memory_scope"] == "binance"
    assert [row["block_id"] for row in kis_status["recent_reflections"]] == [
        "blk_005930_status_scope_test"
    ]
    assert [row["block_id"] for row in binance_status["recent_reflections"]] == [
        "bnb_futures_ETHUSDT_status_scope_test"
    ]
    assert {row["policy_id"] for row in kis_status["policy_scorecards"]} == {
        "kis.status.scope.policy"
    }
    assert {row["policy_id"] for row in binance_status["policy_scorecards"]} == {
        "binance.status.scope.policy"
    }
    assert kis_status["latest_run"]["slot"] == "pre_open"
    assert binance_status["latest_run"]["slot"] == "morning"
    assert "KIS scoped telegram" in json.dumps(
        kis_status["latest_telegram_send"],
        ensure_ascii=False,
    )
    assert "Binance scoped telegram" in json.dumps(
        binance_status["latest_telegram_send"],
        ensure_ascii=False,
    )
    assert "KIS scoped telegram" not in json.dumps(
        binance_status["latest_telegram_send"],
        ensure_ascii=False,
    )


def test_status_exposes_period_memory_coverage_by_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    repo = service.repository
    repo.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "memory_scope": "kis",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"memory_scope": "kis"},
            "review_md": "KIS weekly complete",
            "policy_revision_ids": [],
        }
    )
    repo.upsert_period_review(
        {
            "period_key": "2026-05",
            "period_type": "monthly",
            "memory_scope": "kis",
            "start_date": "2026-05-01",
            "end_date": "2026-05-29",
            "status": "ok",
            "mode": "llm",
            "metrics": {"memory_scope": "kis"},
            "review_md": "KIS monthly complete",
            "policy_revision_ids": [],
        }
    )
    repo.upsert_historical_replay(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "memory_scope": "kis",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "case_count": 2,
            "metrics": {"memory_scope": "kis"},
            "replay_md": "KIS replay complete",
            "policy_revision_ids": [],
        }
    )

    all_status = service.status(compact=True)
    kis_status = service.status(scope="kis", compact=True)
    all_coverage = all_status["period_memory_coverage"]
    kis_coverage = kis_status["period_memory_coverage"]

    assert all_coverage["scopes"] == ["kis", "binance"]
    assert all_coverage["status"] == "needs_attention"
    assert all_coverage["weekly_reviews"]["kis"]["status"] == "ok"
    assert all_coverage["weekly_reviews"]["binance"]["status"] == "missing"
    assert all_coverage["weekly_replays"]["kis"]["status"] == "ok"
    assert all_coverage["weekly_replays"]["binance"]["status"] == "missing"
    assert all_coverage["monthly_reviews"]["kis"]["status"] == "ok"
    assert all_coverage["monthly_reviews"]["binance"]["status"] == "missing"
    assert set(all_coverage["missing"]) == {
        "binance:weekly_review",
        "binance:weekly_replay",
        "binance:monthly_review",
    }
    assert kis_coverage["scopes"] == ["kis"]
    assert kis_coverage["status"] == "ok"
    assert kis_coverage["missing"] == []


def test_scoped_status_does_not_fall_back_to_core_telegram_send(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_journal(
        trading_day="2026-07-01",
        slot="weekly",
        title="Core weekly journal",
        message_md="Core telegram should stay out of scoped tabs",
        file_path=str(tmp_path / "memory" / "journals" / "2026-07-01" / "weekly.md"),
        context={"memory_scope": "core"},
        sent_telegram=True,
        telegram_result={
            "detail": {"result": {"text": "Core telegram should not leak"}}
        },
    )

    binance_status = service.status(scope="binance")

    assert binance_status["memory_scope"] == "binance"
    assert binance_status["latest_telegram_send"] == {
        "status": "missing",
        "memory_scope": "binance",
    }


def test_context_pack_enforces_small_budget_with_large_memory_files(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    root = tmp_path / "memory"
    (root / "persona.md").write_text("쥬 페르소나\n" + ("긴문장 " * 700), encoding="utf-8")
    (root / "policies" / "trading.md").write_text(
        "거래 정책\n" + ("리스크 확인 " * 700),
        encoding="utf-8",
    )

    pack = service.context_pack(max_chars=1000)
    serialized = json.dumps(pack, ensure_ascii=False, sort_keys=True)

    assert len(serialized) <= 1600
    assert pack["status"] == "ok"
    assert "decision_skill_status" in pack
    assert set(pack["decision_skills"]) == {
        "block_manager",
        "market_judge",
        "risk_manager",
        "reflection",
    }
    assert pack["decision_skills"]["market_judge"]["version"] == "jue.market_judge.v1"


def test_memory_run_storage_is_hard_capped_for_large_context_payload(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    large_block = {
        "block_id": "blk_large",
        "symbol": "005930",
        "metadata": {"raw_dump": "X" * 200_000},
        "thesis": "Y" * 120_000,
    }

    run_id = service.repository.save_run(
        kind="reflection",
        slot="block_reflection",
        status="ok",
        mode="llm",
        model="gpt-5.5",
        error_message="",
        input_payload={
            "task": "large memory run",
            "context": {
                "blocks": {"active_blocks": [large_block for _ in range(8)]},
                "binance_blocks": {"active_blocks": [large_block for _ in range(8)]},
            },
        },
        output_payload={"message_md": "Z" * 200_000},
    )

    with sqlite3.connect(service.repository.path) as conn:
        row = conn.execute(
            """
            SELECT length(input_json), length(output_json), input_json, output_json
            FROM memory_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()

    assert row is not None
    assert row[0] <= 65_000
    assert row[1] <= 85_000
    stored_input = json.loads(row[2])
    stored_output = json.loads(row[3])
    assert stored_input["_storage_compaction"]["status"] == "compacted"
    assert stored_output["_storage_compaction"]["status"] == "compacted"


def test_daily_journal_context_storage_is_hard_capped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    large_block = {
        "block_id": "blk_large",
        "symbol": "005930",
        "metadata": {"raw_dump": "X" * 200_000},
        "thesis": "Y" * 120_000,
    }

    service.repository.upsert_journal(
        trading_day="2026-06-15",
        slot="block_reflection",
        title="large journal",
        message_md="M" * 200_000,
        file_path="",
        context={"blocks": {"active_blocks": [large_block for _ in range(8)]}},
    )

    with sqlite3.connect(service.repository.path) as conn:
        row = conn.execute(
            """
            SELECT length(message_md), length(context_json), context_json
            FROM daily_journals
            WHERE trading_day = '2026-06-15'
            """
        ).fetchone()

    assert row is not None
    assert row[0] <= 8_000
    assert row[1] <= 20_000
    stored_context = json.loads(row[2])
    assert stored_context["_storage_compaction"]["status"] == "compacted"


def test_context_pack_includes_recent_symbol_analyses(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "trigger": "user_request",
            "source": "instant",
            "model": "gpt-5.5",
            "status": "ok",
            "summary": "피노는 짧은 단기 블록만 허용한다.",
            "stance": "risk_check",
            "confidence": 0.7,
        }
    )

    pack = service.context_pack(symbols=["033790"], max_chars=4000)

    assert "symbol_analyses" in pack
    assert (
        pack["symbol_analyses"]["033790"][0]["summary"]
        == "피노는 짧은 단기 블록만 허용한다."
    )
    assert pack["lifecycle_artifacts"][0]["artifact_type"] == "symbol_analysis"
    assert pack["lifecycle_artifacts"][0]["symbol"] == "033790"
    lifecycle_packet = build_decision_lifecycle_packet(
        stage="manager_run",
        workflow_id="kis_intraday_manager",
        artifacts=pack["lifecycle_artifacts"],
    )
    assert lifecycle_packet["artifact_count"] == 1
    assert lifecycle_packet["rejected_artifacts"] == []


def test_context_pack_includes_symbol_filtered_lifecycle_artifacts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.lifecycle_repository.upsert_artifact(
        {
            "artifact_id": "life_005930_1",
            "artifact_type": "research_note",
            "workflow_id": "kis_daily_discovery",
            "symbol": "005930",
            "title": "삼성전자 수급 전환",
            "summary_md": "외국인 수급 전환이 블록 후보 판단에 필요하다.",
            "payload": {
                "block_implications": [
                    {"action": "watch_add", "horizon": "mid", "confidence": 0.71}
                ]
            },
            "evidence": [{"source": "daily_discovery"}, {"source": "price"}],
            "updated_at": "2026-05-20T01:02:03+00:00",
        }
    )
    service.lifecycle_repository.upsert_artifact(
        {
            "artifact_id": "life_000660_1",
            "artifact_type": "research_note",
            "workflow_id": "kis_daily_discovery",
            "symbol": "000660",
            "title": "SK하이닉스 별도 후보",
            "summary_md": "다른 심볼의 생애주기 산출물",
            "payload": {"block_implications": [{"action": "ignore"}]},
            "evidence": [{"source": "daily_discovery"}],
            "updated_at": "2026-05-20T01:02:04+00:00",
        }
    )

    pack = service.context_pack(symbols=["005930"], max_chars=7000)

    assert pack["lifecycle_artifacts"] == [
        {
            "artifact_id": "life_005930_1",
            "artifact_type": "research_note",
            "workflow_id": "kis_daily_discovery",
            "symbol": "005930",
            "title": "삼성전자 수급 전환",
            "summary_md": "외국인 수급 전환이 블록 후보 판단에 필요하다.",
            "updated_at": "2026-05-20T01:02:03+00:00",
            "evidence_count": 2,
            "evidence": [{"source": "daily_discovery"}, {"source": "price"}],
            "block_implications": [
                {"action": "watch_add", "horizon": "mid", "confidence": 0.71}
            ],
        }
    ]
    lifecycle_packet = build_decision_lifecycle_packet(
        stage="manager_run",
        workflow_id="kis_intraday_manager",
        artifacts=pack["lifecycle_artifacts"],
    )
    assert lifecycle_packet["artifact_count"] == 1
    assert lifecycle_packet["artifacts"][0]["symbol"] == "005930"
    assert lifecycle_packet["rejected_artifacts"] == []


def test_context_pack_scopes_kis_and_binance_memories(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.save_insight(
        memory_type="symbol",
        key="005930",
        status="active",
        confidence=0.8,
        summary_md="삼성전자 전용 국장 기억",
        evidence=[{"memory_scope": "kis", "transferability": "direct"}],
    )
    service.repository.save_insight(
        memory_type="symbol",
        key="BTCUSDT",
        status="active",
        confidence=0.75,
        summary_md="BTCUSDT 전용 바이낸스 기억",
        evidence=[{"memory_scope": "binance", "transferability": "direct"}],
    )
    service.repository.save_insight(
        memory_type="regime",
        key="regime:risk",
        status="active",
        confidence=0.7,
        summary_md="급등락장에서는 블록 수량을 보수적으로 검토한다.",
        evidence=[{"memory_scope": "core", "transferability": "direct"}],
    )
    service.repository.save_insight(
        memory_type="policy",
        key="respect_defined_stops",
        status="active",
        confidence=0.72,
        summary_md="KIS 손절 약속 준수 경험은 바이낸스에도 절차 교훈으로만 번역한다.",
        evidence=[{"memory_scope": "kis", "transferability": "translated"}],
    )

    pack = service.context_pack(target_scope="binance", max_chars=8000)
    scoped = pack["scoped_memory"]
    serialized = json.dumps(scoped, ensure_ascii=False)

    assert scoped["target_scope"] == "binance"
    assert "BTCUSDT 전용 바이낸스 기억" in serialized
    assert "급등락장에서는" in serialized
    assert "절차 교훈" in serialized
    assert "삼성전자 전용 국장 기억" not in serialized
    assert scoped["blocked_count"] >= 1


def test_context_pack_keeps_foreign_policy_rules_translated_not_top_level(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "binance.breakout.long",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.74,
                "expectancy_r": 0.42,
                "evidence_ids": ["ev-binance"],
            },
            {
                "policy_id": "kis.rebalance.mid",
                "scope": "kis",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.31,
                "evidence_ids": ["ev-kis"],
            },
        ]
    )

    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    top_rule_ids = {row["policy_id"] for row in kis_pack["policy_rules"]}
    top_scorecard_ids = {row["policy_id"] for row in kis_pack["policy_scorecards"]}
    active_ids = {row["policy_id"] for row in kis_pack["active_policies"]}
    translated = json.dumps(
        kis_pack["scoped_memory"].get("translated") or [],
        ensure_ascii=False,
    )
    translated_policy_context = kis_pack["translated_policy_context"]

    assert "kis.rebalance.mid" in top_rule_ids
    assert "kis.rebalance.mid" in top_scorecard_ids
    assert "kis.rebalance.mid" in active_ids
    assert "binance.breakout.long" not in top_rule_ids
    assert "binance.breakout.long" not in top_scorecard_ids
    assert "binance.breakout.long" not in active_ids
    assert "binance.breakout.long" in translated
    assert "binance" in translated
    assert translated_policy_context["status"] == "available"
    assert translated_policy_context["target_scope"] == "kis"
    assert translated_policy_context["available_count"] == 1
    assert translated_policy_context["selected_count"] == 1
    assert translated_policy_context["omitted_count"] == 0
    assert translated_policy_context["source_scope_counts"] == {"binance": 1}
    assert translated_policy_context["items"] == [
        {
            "policy_id": "binance.breakout.long",
            "source_scope": "binance",
            "transferability": "translated",
            "status": "active_preference",
            "action": "prefer",
            "sample_count": 8,
            "confidence": pytest.approx(0.74),
            "expectancy_pct": pytest.approx(0.42),
            "reason": "Evidence scorecard for binance: sample_count=8, confidence=0.74, expectancy_r=+0.42",
        }
    ]
    assert (
        translated_policy_context["instruction"]
        == "Use these only as translated lessons, never as direct venue rules."
    )


def test_translated_policy_context_reports_omitted_count_and_source_scope_counts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"binance.breakout.edge.{idx}",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.8,
            "expectancy_r": 0.2,
            "evidence_ids": [f"binance-edge-{idx}"],
        }
        for idx in range(6)
    ]
    scorecards.extend(
        [
            {
                "policy_id": "core.risk.check",
                "scope": "core",
                "sample_count": 5,
                "confidence": 0.7,
                "expectancy_r": 0.12,
                "evidence_ids": ["core-risk"],
            },
            {
                "policy_id": "kis.local.only",
                "scope": "kis",
                "sample_count": 5,
                "confidence": 0.7,
                "expectancy_r": 0.12,
                "evidence_ids": ["kis-local"],
            },
        ]
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="kis", max_chars=20000)
    translated_policy_context = pack["translated_policy_context"]

    assert translated_policy_context["status"] == "available"
    assert translated_policy_context["available_count"] == 6
    assert translated_policy_context["selected_count"] == 4
    assert translated_policy_context["omitted_count"] == 2
    assert translated_policy_context["source_scope_counts"] == {"binance": 6}
    assert translated_policy_context["selection_policy"] == {
        "order": "active status, then prompt order",
        "limit": 4,
    }


def test_context_pack_fetches_translated_policy_context_beyond_scorecard_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"binance.local.edge.{idx}",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.95,
            "expectancy_r": 0.4,
            "evidence_ids": [f"binance-local-{idx}"],
        }
        for idx in range(90)
    ]
    scorecards.append(
        {
            "policy_id": "kis.value.pullback",
            "scope": "kis",
            "sample_count": 8,
            "confidence": 0.66,
            "expectancy_r": 0.21,
            "evidence_ids": ["kis-translated-pullback"],
            "reason": "KIS 눌림목 교훈은 Binance에서 번역 참고로만 유지한다.",
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    translated_ids = {
        row["policy_id"]
        for row in pack["translated_policy_context"]["items"]
        if isinstance(row, dict)
    }
    assert "kis.value.pullback" in translated_ids
    assert "kis.value.pullback" not in {
        row["policy_id"] for row in pack["policy_scorecards"]
    }
    assert "kis.value.pullback" not in {
        row["policy_id"] for row in pack["policy_rules"]
    }


def test_context_pack_fetches_translated_policy_context_beyond_expanded_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"binance.local.scalp.{idx}",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.95,
            "expectancy_r": 0.35,
            "evidence_ids": [f"binance-scalp-{idx}"],
        }
        for idx in range(450)
    ]
    scorecards.append(
        {
            "policy_id": "kis.midterm.value.waiting_entry",
            "scope": "kis",
            "sample_count": 9,
            "confidence": 0.69,
            "expectancy_r": 0.24,
            "evidence_ids": ["kis-midterm-waiting-entry"],
            "reason": "KIS 중기 눌림 대기 블록은 Binance에서도 번역 참고로만 유지한다.",
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    translated_ids = {
        row["policy_id"]
        for row in pack["translated_policy_context"]["items"]
        if isinstance(row, dict)
    }
    assert "kis.midterm.value.waiting_entry" in translated_ids
    assert "kis.midterm.value.waiting_entry" not in {
        row["policy_id"] for row in pack["policy_scorecards"]
    }
    assert "kis.midterm.value.waiting_entry" not in {
        row["policy_id"] for row in pack["policy_rules"]
    }


def test_context_pack_fetches_target_scope_scorecards_before_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"kis.momentum.{idx}",
            "scope": "kis",
            "sample_count": 8,
            "confidence": 0.9,
            "expectancy_r": 0.5,
            "evidence_ids": [f"kis-{idx}"],
        }
        for idx in range(14)
    ]
    scorecards.append(
        {
            "policy_id": "binance.rebuild.waiting_entry",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.86,
            "expectancy_r": -0.2,
            "evidence_ids": ["binance-loss-pattern"],
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["policy_id"] for row in pack["policy_scorecards"]
    } >= {"binance.rebuild.waiting_entry"}
    assert {
        row["policy_id"] for row in pack["policy_rules"]
    } >= {"binance.rebuild.waiting_entry"}
    assert {
        row["policy_id"] for row in pack["active_policies"]
    } >= {"binance.rebuild.waiting_entry"}


def test_context_pack_fetches_target_scope_scorecards_beyond_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"kis.high_confidence.{idx}",
            "scope": "kis",
            "sample_count": 8,
            "confidence": 0.96,
            "expectancy_r": 0.4,
            "evidence_ids": [f"kis-high-confidence-{idx}"],
        }
        for idx in range(90)
    ]
    scorecards.append(
        {
            "policy_id": "binance.local.rebuild.waiting_entry",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.65,
            "expectancy_r": 0.18,
            "evidence_ids": ["binance-local-rebuild"],
            "reason": "Binance local recovery policy must not be crowded out by KIS scorecards.",
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["policy_id"] for row in pack["policy_scorecards"]
    } >= {"binance.local.rebuild.waiting_entry"}


def test_context_pack_fetches_target_scope_policy_rules_beyond_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"aaa.kis.high_confidence.{idx:03d}",
            "scope": "kis",
            "sample_count": 8,
            "confidence": 0.96,
            "expectancy_r": 0.4,
            "evidence_ids": [f"kis-rule-crowd-{idx}"],
        }
        for idx in range(90)
    ]
    scorecards.append(
        {
            "policy_id": "zzz.binance.local.rebuild.waiting_entry",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.65,
            "expectancy_r": 0.18,
            "evidence_ids": ["binance-local-rule"],
            "reason": "Binance local rule must not be crowded out by KIS policy rules.",
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["policy_id"] for row in pack["policy_rules"]
    } >= {"zzz.binance.local.rebuild.waiting_entry"}


def test_context_pack_fetches_target_scope_active_policies_beyond_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    scorecards = [
        {
            "policy_id": f"kis.active.preference.{idx:03d}",
            "scope": "kis",
            "sample_count": 8,
            "confidence": 0.96,
            "expectancy_r": 0.4,
            "evidence_ids": [f"kis-active-crowd-{idx}"],
        }
        for idx in range(90)
    ]
    scorecards.append(
        {
            "policy_id": "binance.active.local.rebuild.waiting_entry",
            "scope": "binance",
            "sample_count": 8,
            "confidence": 0.65,
            "expectancy_r": 0.18,
            "evidence_ids": ["binance-active-local"],
            "reason": "Binance active policy must not be crowded out by KIS active scorecards.",
        }
    )
    service.ingest_evidence_scorecards(scorecards)

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["policy_id"] for row in pack["active_policies"]
    } >= {"binance.active.local.rebuild.waiting_entry"}


def test_context_pack_fetches_target_scope_policy_changes_beyond_global_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO policy_changes (
                policy_id, memory_scope, transferability, action, strength,
                status, reason, confidence, source_run_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "binance.change.local.rebuild.waiting_entry",
                "binance",
                "translated",
                "caution",
                "caution",
                "active",
                "Binance policy change must not be crowded out by newer KIS changes.",
                0.72,
                None,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.executemany(
            """
            INSERT INTO policy_changes (
                policy_id, memory_scope, transferability, action, strength,
                status, reason, confidence, source_run_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"kis.change.active.preference.{idx:03d}",
                    "kis",
                    "translated",
                    "prefer",
                    "preference",
                    "active",
                    "Newer KIS policy change.",
                    0.9,
                    None,
                    f"2026-01-02T00:{idx:02d}:00+00:00",
                )
                for idx in range(90)
            ],
        )

    pack = service.context_pack(target_scope="binance", max_chars=20000)

    assert {
        row["policy_id"] for row in pack["active_policies"]
    } >= {"binance.change.local.rebuild.waiting_entry"}


def test_context_pack_budget_preserves_target_scope_policy_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    (tmp_path / "memory" / "persona.md").write_text(
        "# Jue\n\n" + ("큰 컨텍스트 " * 600),
        encoding="utf-8",
    )
    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "binance.rebuild.waiting_entry",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.86,
                "expectancy_r": -0.2,
                "evidence_ids": ["binance-loss-pattern"],
                "reason": "손실 lane은 가격 개선 대기 진입으로만 재시도한다.",
            }
        ]
    )

    pack = service.context_pack(target_scope="binance", max_chars=1000)

    assert {
        row["policy_id"] for row in pack["policy_scorecards"]
    } >= {"binance.rebuild.waiting_entry"}
    assert {
        row["policy_id"] for row in pack["policy_rules"]
    } >= {"binance.rebuild.waiting_entry"}
    assert {
        row["policy_id"] for row in pack["active_policies"]
    } >= {"binance.rebuild.waiting_entry"}


def test_context_pack_treats_validation_binance_policy_as_local(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.binance.cost_simulation",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 12,
            "confidence": 0.95,
            "reason": "바이낸스 비용 검증 실패가 반복되어 즉시진입보다 대기진입을 우선한다.",
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "protect_winning_blocks",
            "status": "active_preference",
            "action": "prefer",
            "sample_count": 20,
            "confidence": 0.95,
            "reason": "공통 수익 보호 정책",
        }
    )

    pack = service.context_pack(target_scope="binance", max_chars=3000)

    scorecards = {row["policy_id"]: row for row in pack["policy_scorecards"]}
    assert "validation.binance.cost_simulation" in scorecards
    assert scorecards["validation.binance.cost_simulation"]["scope"] == "binance"


def test_context_pack_budget_prioritizes_local_policy_over_core(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    for idx in range(5):
        service.repository.upsert_policy_scorecard(
            {
                "policy_id": f"core.preference.{idx}",
                "status": "active_preference",
                "action": "prefer",
                "sample_count": 20,
                "confidence": 0.95,
                "reason": "공통 정책이 많아도 대상 거래장 정책을 밀어내면 안 된다.",
            }
        )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.binance.cost_simulation",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 12,
            "confidence": 0.95,
            "reason": "바이낸스 비용 검증 실패가 반복되어 즉시진입보다 대기진입을 우선한다.",
        }
    )

    pack = service.context_pack(target_scope="binance", max_chars=1000)

    assert {
        row["policy_id"] for row in pack["policy_scorecards"]
    } >= {"validation.binance.cost_simulation"}


def test_context_pack_budget_keeps_compact_scoped_memory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    root = tmp_path / "memory"
    (root / "persona.md").write_text("쥬 페르소나\n" + ("긴문장 " * 700), encoding="utf-8")
    (root / "policies" / "trading.md").write_text(
        "거래 정책\n" + ("리스크 확인 " * 700),
        encoding="utf-8",
    )
    service.repository.save_insight(
        memory_type="symbol",
        key="BNBUSDT",
        status="active",
        confidence=0.78,
        summary_md=(
            "BNBUSDT 선물 숏 사후검증: 목표 미도달, 손절 도달. "
            "다음 숏은 microtrend/BTC/funding 확인."
        ),
        evidence=[{"memory_scope": "binance", "transferability": "direct"}],
    )

    pack = service.context_pack(
        symbols=["BNBUSDT"],
        target_scope="binance",
        max_chars=2000,
    )
    serialized = json.dumps(pack, ensure_ascii=False, sort_keys=True)

    assert len(serialized) <= 2600
    assert pack["scoped_memory"]["status"] == "trimmed"
    assert "BNBUSDT 선물 숏 사후검증" in json.dumps(
        pack["scoped_memory"],
        ensure_ascii=False,
    )


def test_context_pack_includes_decision_packet_outcome_summary(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    pack = service.context_pack(
        context={
            "decision_packet_v2": {
                "version": "decision_packet_v2",
                "recent_execution_summary": {
                    "sell_reasons": {"force_exit_requested": 1},
                    "exit_signals": {"stop_reached": 1},
                },
                "previous_decision_reviews": [
                    {
                        "run_id": 111,
                        "action_counts": {"close_blocks": 1},
                    }
                ],
                "blocks": [
                    {
                        "block_id": "blk_mid",
                        "symbol": "012330",
                        "horizon": "mid",
                        "status": "open",
                        "technical": {"price": 520000, "day_change_pct": -0.95},
                        "stop_policy": {
                            "touch_action": "manager_review",
                            "stop_touched_now": True,
                        },
                    }
                ],
            }
        },
        max_chars=5000,
    )

    packet = pack["decision_packet_v2"]
    assert packet["version"] == "decision_packet_v2"
    assert packet["recent_execution_summary"]["sell_reasons"]["force_exit_requested"] == 1
    assert packet["previous_decision_reviews"][0]["action_counts"]["close_blocks"] == 1
    assert packet["blocks"][0]["touch_action"] == "manager_review"


def test_context_pack_includes_daily_discovery_candidates(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    pack = service.context_pack(
        context={
            "daily_discovery": {
                "status": "ok",
                "trading_day": "2026-05-20",
                "summary": "아침 탐사에서 로봇/소부장 후보가 강했다.",
                "items": [
                    {
                        "symbol": f"10{idx:04d}",
                        "name": f"피코스피{idx}",
                        "market": "KOSPI",
                        "score": 70 + idx,
                        "stance": "watch",
                        "confidence": 0.6,
                        "summary": f"탐사 요약 {idx}",
                        "raw_payload": "drop me",
                    }
                    for idx in range(12)
                ],
                "block_candidates": [
                    {
                        "symbol": f"20{idx:04d}",
                        "name": f"피코스닥{idx}",
                        "market": "KOSDAQ",
                        "score": 80 + idx,
                        "analysis": {
                            "stance": "block_candidate",
                            "confidence": 0.7,
                            "summary": f"블록 후보 {idx}",
                            "verbose": "drop me",
                        },
                    }
                    for idx in range(7)
                ],
            }
        },
        max_chars=5000,
    )

    discovery = pack["daily_discovery"]
    assert discovery["status"] == "ok"
    assert discovery["trading_day"] == "2026-05-20"
    assert discovery["summary"] == "아침 탐사에서 로봇/소부장 후보가 강했다."
    assert len(discovery["items"]) == 10
    assert len(discovery["block_candidates"]) == 5
    assert discovery["block_candidates"][0] == {
        "symbol": "200000",
        "name": "피코스닥0",
        "market": "KOSDAQ",
        "score": 80,
        "stance": "block_candidate",
        "confidence": 0.7,
        "summary": "블록 후보 0",
    }
    assert "raw_payload" not in discovery["items"][0]
    assert "analysis" not in discovery["block_candidates"][0]


def test_context_pack_omits_empty_symbol_analyses_after_budget_trim(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    pack = service.context_pack(max_chars=1000)

    assert "symbol_analyses" not in pack


def test_memory_skills_describe_horizon_balanced_portfolio(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize(force=True)

    pack = service.context_pack(max_chars=5000)
    text = json.dumps(pack, ensure_ascii=False)

    assert "단기" in text
    assert "중기" in text
    assert "장기" in text
    assert "ETF" in text
    assert "현금" in text
    assert "모든 블록을 단기처럼 취급하지 않는다" in text
    assert "정규장 30분 매니저 루프" in text


def test_ritual_llm_unavailable_records_error_without_telegram(tmp_path: Path) -> None:
    telegram = _FakeTelegram()
    service = _service(tmp_path, telegram=telegram)
    context = {
        "account": {"cash_krw": 1_000_000, "position_count": 2},
        "blocks": {"blocks": [{"status": "open", "block_id": "blk_1"}]},
    }

    first = asyncio.run(
        service.run_ritual(slot="pre_open", context=context, send_telegram=True)
    )
    second = asyncio.run(
        service.run_ritual(slot="pre_open", context=context, send_telegram=True)
    )

    assert first["status"] == "error"
    assert second["status"] == "error"
    assert first["mode"] == "error"
    assert "codex_runtime_unavailable" in first["error_message"]
    assert telegram.messages == []


def test_ritual_telegram_message_prefers_symbol_names_from_context(tmp_path: Path) -> None:
    telegram = _FakeTelegram()
    payload = {
        "title": "장중 점검",
        "message_md": "005930은 유지하고 005930 (005930)은 추가 확인한다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload, telegram=telegram)
    context = {
        "account": {
            "positions": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "qty": 1,
                    "mark_price": 76_000,
                }
            ]
        }
    }

    result = asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=True,
            force=True,
        )
    )

    message = result["journal"]["message_md"]
    assert "삼성전자 (005930)은 유지" in message
    assert "삼성전자 (005930)은 추가 확인" in message
    assert "005930 (005930)" not in message
    assert telegram.messages == [message]


def test_ritual_prompt_compacts_large_context_before_llm(tmp_path: Path) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 압축된 근거만 보고 장중 판단을 정리했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    huge_context = {
        "account": {
            "cash_krw": 1_000_000,
            "orderable_cash_krw": 900_000,
            "position_count": 2,
            "positions": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "qty": 1,
                    "mark_price": 80_000,
                    "unrealized_pnl_krw": 1200,
                    "raw": "SHOULD_NOT_REACH_LLM" * 200,
                }
            ],
        },
        "blocks": {
            "blocks": [
                {
                    "block_id": f"blk_{idx}",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "open",
                    "qty_open": 1,
                    "entry_price": 80_000,
                    "target_price": 84_000,
                    "stop_price": 76_000,
                    "thesis": "장중 압축 테스트",
                    "raw": "SHOULD_NOT_REACH_LLM" * 200,
                }
                for idx in range(20)
            ],
            "events": [
                {"event_type": "quote", "payload": "SHOULD_NOT_REACH_LLM" * 200}
                for _ in range(20)
            ],
        },
        "market_pulse": {
            "status": "ok",
            "regime": "rotation",
            "raw_payload": "SHOULD_NOT_REACH_LLM" * 500,
        },
    }

    result = asyncio.run(
        service.run_ritual(slot="midday", context=huge_context, force=True)
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    assert result["status"] == "ok"
    assert len(prompt_text) < 20_000
    assert "SHOULD_NOT_REACH_LLM" not in prompt_text
    assert prompt["language_policy"]["internal_reasoning_language"] == "en-US"
    assert prompt["language_policy"]["operator_display_language"] == "ko-KR"
    assert prompt["language_policy"]["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    assert prompt["context"]["account"]["cash_krw"] == 1_000_000
    assert prompt["context"]["blocks"]["open_count"] == 10
    assert prompt["context"]["market_pulse"]["regime"] == "rotation"


def test_ritual_prompt_preserves_explicit_zero_position_availability(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 주문 가능 수량 0을 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "account": {
            "positions": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "qty": 3,
                    "available_qty": 0,
                    "avg_price": 71_000,
                    "mark_price": 72_000,
                    "unrealized_pnl_krw": 0,
                    "unrealized_pnl_pct": 0.0,
                    "weight": 0.0,
                }
            ],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    position = prompt["context"]["account"]["positions"][0]
    assert position["qty"] == 3
    assert position["available_qty"] == 0.0
    assert position["unrealized_pnl_krw"] == 0.0
    assert position["unrealized_pnl_pct"] == 0.0
    assert position["weight"] == 0.0


def test_ritual_prompt_honors_explicit_empty_positions_over_items(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 보유 없음 상태를 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "account": {
            "positions": [],
            "items": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "qty": 7,
                }
            ],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    account = prompt["context"]["account"]
    assert account["positions"] == []
    assert account["position_sample_count"] == 0
    assert account["position_total_count"] == 0


def test_ritual_prompt_honors_explicit_zero_open_count_over_active_blocks(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 열린 블록 없음 상태를 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "blocks": {
            "status": "ok",
            "open_count": 0,
            "active_blocks": [
                {
                    "block_id": "stale-open-block",
                    "symbol": "005930",
                    "status": "open",
                    "qty_open": 1,
                }
            ],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    blocks = prompt["context"]["blocks"]
    assert blocks["open_count"] == 0
    assert blocks["active_blocks"] == []


def test_ritual_prompt_omits_missing_block_counts_but_keeps_zero(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 블록 카운트 공백을 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "blocks": {
            "status": "ok",
            "active_blocks": [
                {
                    "block_id": "blk_missing_counts",
                    "symbol": "005930",
                    "status": "open",
                    "qty_open": 1,
                }
            ],
        },
        "binance_blocks": {
            "status": "ok",
            "total_count": 0,
            "open_total_count": 0,
            "closed_sample_count": 0,
            "active_blocks": [],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    missing_counts = prompt["context"]["blocks"]
    explicit_zero = prompt["context"]["binance_blocks"]
    assert missing_counts["open_count"] == 1
    assert "total_count" not in missing_counts
    assert "open_total_count" not in missing_counts
    assert "closed_sample_count" not in missing_counts
    assert explicit_zero["total_count"] == 0
    assert explicit_zero["open_total_count"] == 0
    assert explicit_zero["closed_sample_count"] == 0


def test_ritual_prompt_does_not_synthesize_missing_recent_order_numbers(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 최근 주문 숫자 필드를 정확히 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "blocks": {
            "blocks": [],
            "orders": [
                {
                    "block_id": "order-missing-numbers",
                    "symbol": "005930",
                    "side": "buy",
                    "status": "submitted",
                },
                {
                    "block_id": "order-explicit-zero",
                    "symbol": "000660",
                    "side": "sell",
                    "qty": 0,
                    "limit_price": 0,
                    "status": "rejected",
                },
            ],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    missing_numbers, explicit_zero = prompt["context"]["blocks"]["recent_orders"]
    assert missing_numbers["block_id"] == "order-missing-numbers"
    assert "qty" not in missing_numbers
    assert "limit_price" not in missing_numbers
    assert explicit_zero["block_id"] == "order-explicit-zero"
    assert explicit_zero["qty"] == 0
    assert explicit_zero["limit_price"] == 0.0


def test_ritual_prompt_omits_missing_strategy_candidate_metrics_but_keeps_zero(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 전략 후보 지표 공백을 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "strategy": {
            "status": "ok",
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "drivers": ["리포트 있음"],
                },
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "score": 0,
                    "confidence": 0.0,
                    "risk_score": 0,
                    "suitability": {},
                    "data_coverage": {},
                },
            ],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    missing_metrics, explicit_zero = prompt["context"]["strategy"]["candidates"]
    assert missing_metrics["symbol"] == "005930"
    assert "score" not in missing_metrics
    assert "confidence" not in missing_metrics
    assert "risk_score" not in missing_metrics
    assert "suitability" not in missing_metrics
    assert "data_coverage" not in missing_metrics
    assert explicit_zero["symbol"] == "000660"
    assert explicit_zero["score"] == 0
    assert explicit_zero["confidence"] == 0.0
    assert explicit_zero["risk_score"] == 0
    assert "suitability" not in explicit_zero
    assert "data_coverage" not in explicit_zero


def test_ritual_prompt_honors_explicit_empty_strategy_sources(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 전략 소스 공백을 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    context = {
        "strategy": {
            "status": "ok",
            "sources": [],
            "source_status": ["stale_whale", "stale_sesiban"],
            "candidates": [],
        },
    }

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=context,
            send_telegram=False,
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    strategy = prompt["context"]["strategy"]
    assert strategy["sources"] == []


def test_active_policy_compaction_omits_missing_confidence_but_keeps_zero() -> None:
    rows = [
        {
            "policy_id": "policy.missing.confidence",
            "action": "caution",
            "strength": "watch",
            "status": "active",
            "reason": "근거는 있으나 confidence 지표는 아직 없다.",
        },
        {
            "policy_id": "policy.explicit.zero",
            "action": "caution",
            "strength": "watch",
            "status": "active",
            "confidence": 0,
            "reason": "명시적 0 confidence는 그대로 전달되어야 한다.",
        },
    ]

    compact = _compact_active_policies_for_ritual_prompt(rows)

    missing_confidence, explicit_zero = compact
    assert missing_confidence["policy_id"] == "policy.missing.confidence"
    assert "confidence" not in missing_confidence
    assert explicit_zero["policy_id"] == "policy.explicit.zero"
    assert explicit_zero["confidence"] == 0.0


def test_jue_workflow_compaction_omits_missing_required_but_keeps_false() -> None:
    workflow = {
        "workflow_id": "kis_intraday_manager",
        "contracts": [
            {
                "contract_id": "contract.missing.required",
                "version": "v1",
            },
            {
                "contract_id": "contract.explicit.false",
                "version": "v1",
                "required": False,
            },
        ],
    }

    compact = _compact_jue_workflow(workflow)

    missing_required, explicit_false = compact["contracts"]
    assert missing_required["contract_id"] == "contract.missing.required"
    assert "required" not in missing_required
    assert explicit_false["contract_id"] == "contract.explicit.false"
    assert explicit_false["required"] is False


def test_ritual_prompt_enforces_final_payload_budget_with_many_active_policies(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "장중 점검",
        "message_md": "쥬가 최종 예산 안에서 메모리를 정리했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)
    for index in range(80):
        service.repository.upsert_policy_scorecard(
            {
                "policy_id": f"policy.large.{index}",
                "status": "active_caution",
                "action": "caution",
                "sample_count": 8,
                "win_rate": 0.45,
                "expectancy_pct": -0.1,
                "confidence": 0.7,
                "reason": "SHOULD_BE_COMPACTED" * 200,
                "raw_blob": "SHOULD_BE_COMPACTED" * 500,
            }
        )
    huge_context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": f"blk_{idx}",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "open",
                    "thesis": "SHOULD_BE_COMPACTED" * 200,
                }
                for idx in range(60)
            ],
            "events": [
                {"event_type": "raw", "payload": "SHOULD_BE_COMPACTED" * 200}
                for _ in range(60)
            ],
        },
        "research": {"raw": "SHOULD_BE_COMPACTED" * 1000},
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": f"bn_{idx}",
                    "symbol": "BTCUSDT",
                    "status": "open",
                    "llm_reason": "SHOULD_BE_COMPACTED" * 200,
                }
                for idx in range(60)
            ]
        },
    }

    result = asyncio.run(
        service.run_ritual(slot="midday", context=huge_context, force=True)
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    assert result["status"] == "ok"
    assert len(prompt_text) <= 60_000
    assert ("SHOULD_BE_COMPACTED" * 5) not in prompt_text
    assert prompt["prompt_budget"]["status"] == "ok"
    assert prompt["prompt_budget"]["max_chars"] == 60_000


def test_context_pack_includes_etf_core_research_allocation_and_policy_note(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    etf_research = {
        "status": "active",
        "provider_status": {"status": "active", "usable_research_count": 1},
        "configured_universe": [
            {"symbol": "069500", "name": "KODEX 200", "category": "core"},
        ],
        "items": [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "snapshot": {
                    "status": "ok",
                    "price": 41_500,
                    "change_pct": 0.7,
                    "turnover_krw": 49_800_000_000,
                    "raw_payload": "SHOULD_NOT_REACH_MEMORY" * 100,
                },
                "score": {
                    "label": "core_candidate",
                    "liquidity_score": 92,
                    "core_fit_score": 88,
                    "risk_score": 22,
                    "raw_payload": "SHOULD_NOT_REACH_MEMORY" * 100,
                },
            }
        ],
        "strategy_etf_candidates": [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "asset_class": "etf",
                "horizon_bias": "core_etf",
                "score": 82,
                "confidence": 74,
                "risk_score": 18,
            }
        ],
    }
    blocks = [
        {
            "block_id": "blk_core_069500",
            "symbol": "069500",
            "name": "KODEX 200",
            "status": "open",
            "qty_open": 3,
            "entry_price": 41_000,
            "target_price": 44_000,
            "stop_price": 39_000,
            "metadata": {"horizon": "core_etf", "allocation_reason": "core exposure"},
        }
    ]
    allocation = {
        "status": "ok",
        "targets": {"core_etf": 0.10},
        "items": [
            {
                "horizon": "core_etf",
                "current_value_krw": 124_500,
                "current_weight": 0.08,
                "target_weight": 0.10,
                "drift": -0.02,
            }
        ],
    }

    pack = service.context_pack(
        symbols=["069500"],
        block_ids=["blk_core_069500"],
        blocks=blocks,
        allocation=allocation,
        etf_research=etf_research,
    )
    serialized = json.dumps(pack, ensure_ascii=False)

    assert pack["etf_core"]["status"] == "active"
    assert pack["etf_core"]["research"]["items"][0]["latest"]["label"] == "core_candidate"
    assert pack["etf_core"]["research"]["configured_universe_sample"][0]["symbol"] == "069500"
    assert pack["etf_core"]["active_core_blocks"][0]["horizon"] == "core_etf"
    assert pack["etf_core"]["allocation"]["core_etf_target_weight"] == 0.10
    assert "not company valuation" in pack["etf_core"]["policy_note"]
    assert "SHOULD_NOT_REACH_MEMORY" not in serialized


def test_context_pack_omits_missing_etf_allocation_metrics_but_keeps_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    allocation = {
        "status": "ok",
        "targets": {"core_etf": 0.0},
        "items": [
            {"horizon": "core_etf"},
            {
                "horizon": "core_etf",
                "current_value_krw": 0,
                "current_weight": 0.0,
                "target_weight": 0.0,
                "drift": 0,
            },
        ],
    }

    pack = service.context_pack(
        symbols=["069500"],
        block_ids=[],
        blocks=[],
        allocation=allocation,
        etf_research={"status": "active", "items": []},
    )

    allocation_context = pack["etf_core"]["allocation"]
    assert allocation_context["core_etf_target_weight"] == 0.0
    missing_item, zero_item = allocation_context["core_etf_items"]
    assert missing_item == {"horizon": "core_etf"}
    assert zero_item["current_value_krw"] == 0.0
    assert zero_item["current_weight"] == 0.0
    assert zero_item["target_weight"] == 0.0
    assert zero_item["drift"] == 0.0


def test_ritual_prompt_compacts_etf_research_without_raw_payloads(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "ETF 점검",
        "message_md": "ETF/Core 리서치만 압축해서 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)

    result = asyncio.run(
        service.run_ritual(
            slot="midday",
            context=service.build_ritual_context(
                slot="midday",
                trading_day="2026-05-14",
                account={"cash_krw": 1_000_000},
                blocks={
                    "blocks": [
                        {
                            "block_id": "blk_core_069500",
                            "symbol": "069500",
                            "status": "open",
                            "horizon": "core_etf",
                        }
                    ]
                },
                etf_research={
                    "status": "active",
                    "configured_universe": [
                        {"symbol": "069500", "name": "KODEX 200", "category": "core"}
                    ],
                    "items": [
                        {
                            "symbol": "069500",
                            "name": "KODEX 200",
                            "snapshot": {
                                "status": "ok",
                                "price": 41_500,
                                "change_pct": 0.7,
                                "turnover_krw": 49_800_000_000,
                                "raw": "SHOULD_NOT_REACH_LLM" * 300,
                            },
                            "score": {
                                "label": "core_candidate",
                                "liquidity_score": 92,
                                "core_fit_score": 88,
                                "risk_score": 22,
                                "raw": "SHOULD_NOT_REACH_LLM" * 300,
                            },
                        }
                    ],
                },
            ),
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    assert result["status"] == "ok"
    assert prompt["context"]["etf_core"]["research"]["items"][0]["latest"]["price"] == 41_500
    assert prompt["context"]["etf_core"]["active_core_blocks"][0]["horizon"] == "core_etf"
    assert len(prompt_text) < 20_000
    assert "SHOULD_NOT_REACH_LLM" not in prompt_text


def test_ritual_prompt_omits_missing_etf_metrics_but_keeps_zero(
    tmp_path: Path,
) -> None:
    payload = {
        "title": "ETF 점검",
        "message_md": "ETF/Core 지표 공백을 확인했다.",
        "memory_updates": {},
        "policy_changes": [],
    }
    service = _service(tmp_path, llm_payload=payload)

    asyncio.run(
        service.run_ritual(
            slot="midday",
            context=service.build_ritual_context(
                slot="midday",
                trading_day="2026-05-14",
                account={"cash_krw": 1_000_000},
                blocks={"blocks": []},
                etf_research={
                    "status": "active",
                    "items": [
                        {
                            "symbol": "069500",
                            "name": "KODEX 200",
                            "snapshot": {"status": "ok"},
                            "score": {"label": "core_candidate"},
                        },
                        {
                            "symbol": "102110",
                            "name": "TIGER 200",
                            "snapshot": {
                                "status": "ok",
                                "price": 0,
                                "change_pct": 0.0,
                            },
                            "score": {
                                "label": "watch",
                                "liquidity_score": 0,
                                "core_fit_score": 0.0,
                                "risk_score": 0,
                            },
                        },
                    ],
                    "strategy_etf_candidates": [
                        {"symbol": "069500", "name": "KODEX 200"},
                        {
                            "symbol": "102110",
                            "name": "TIGER 200",
                            "score": 0,
                            "confidence": 0.0,
                            "risk_score": 0,
                        },
                    ],
                },
            ),
            force=True,
        )
    )
    prompt_text = service.codex_runtime.calls[0]["messages"][1]["content"]  # type: ignore[union-attr]
    prompt = json.loads(prompt_text)

    research = prompt["context"]["etf_core"]["research"]
    missing_latest, zero_latest = [row["latest"] for row in research["items"]]
    assert missing_latest == {"label": "core_candidate"}
    assert zero_latest["price"] == 0.0
    assert zero_latest["change_pct"] == 0.0
    assert zero_latest["liquidity_score"] == 0.0
    assert zero_latest["core_fit_score"] == 0.0
    assert zero_latest["risk_score"] == 0.0
    missing_candidate, zero_candidate = research["strategy_etf_candidates"]
    assert "score" not in missing_candidate
    assert "confidence" not in missing_candidate
    assert "risk_score" not in missing_candidate
    assert zero_candidate["score"] == 0
    assert zero_candidate["confidence"] == 0.0
    assert zero_candidate["risk_score"] == 0


def test_deterministic_fallback_mentions_etf_core_when_relevant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = service.build_ritual_context(
        slot="post_close",
        trading_day="2026-05-14",
        account={"cash_krw": 1_000_000},
        blocks={
            "blocks": [
                {
                    "block_id": "blk_core_069500",
                    "symbol": "069500",
                    "status": "open",
                    "horizon": "core_etf",
                }
            ]
        },
        etf_research={
            "status": "active",
            "items": [
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "snapshot": {"status": "stale", "stale": True},
                    "score": {"label": "core_candidate"},
                }
            ],
        },
    )
    fallback = service._deterministic_ritual(
        slot="post_close",
        trading_day="2026-05-14",
        context=context,
    )
    no_etf = service._deterministic_ritual(
        slot="post_close",
        trading_day="2026-05-14",
        context=service.build_ritual_context(
            slot="post_close",
            trading_day="2026-05-14",
            account={"cash_krw": 1_000_000},
            blocks={"blocks": []},
        ),
    )

    assert "ETF/Core" in fallback["message_md"]
    assert "노출·분산·리밸런스" in fallback["message_md"]
    assert "ETF/Core" not in no_etf["message_md"]


def test_post_close_context_can_include_llm_usage(tmp_path: Path) -> None:
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
        ),
        codex_runtime=_FakeLLM(),
    )

    context = service.build_ritual_context(
        slot="post_close",
        trading_day="2026-05-13",
        account={"cash_krw": 1_000_000},
        blocks={"blocks": []},
        llm_usage={
            "total": {"call_count": 3, "total_tokens": 1200},
            "by_component": [{"component": "kis_block_manager", "total_tokens": 900}],
        },
    )
    fallback = service._deterministic_ritual(
        slot="post_close",
        trading_day="2026-05-13",
        context=context,
    )

    assert context["llm_usage"]["total"]["total_tokens"] == 1200
    assert "오늘 LLM 호출/토큰: 3회 / 1,200 tokens." in fallback["message_md"]
    assert "가장 많이 쓴 컴포넌트: kis_block_manager." in fallback["message_md"]


def test_ritual_context_omits_missing_llm_usage_metrics_but_keeps_zero(
    tmp_path: Path,
) -> None:
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
        ),
        codex_runtime=_FakeLLM(),
    )

    context = service.build_ritual_context(
        slot="post_close",
        trading_day="2026-05-13",
        account={"cash_krw": 1_000_000},
        blocks={"blocks": []},
        llm_usage={
            "total": {"call_count": 0},
            "by_component": [
                {"component": "market_judge", "call_count": 0},
                {"component": "kis_block_manager"},
            ],
        },
    )

    usage = context["llm_usage"]
    assert usage["total"] == {"call_count": 0}
    assert usage["by_component"][0] == {
        "component": "market_judge",
        "call_count": 0,
    }
    assert usage["by_component"][1] == {"component": "kis_block_manager"}


def test_ritual_llm_failure_does_not_send_synthetic_telegram(
    tmp_path: Path,
) -> None:
    telegram = _FakeTelegram()
    strategy = tmp_path / "strategy_krx.md"
    strategy.write_text("# 전략 노하우\n", encoding="utf-8")
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy),
        ),
        codex_runtime=_FailingLLM(
            "codex exec failed code=1 USER: " + ("SHOULD_NOT_REACH_TELEGRAM" * 1000)
        ),  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        service.run_ritual(
            slot="post_close",
            context={"account": {"cash_krw": 1_000_000}, "blocks": {"blocks": []}},
            send_telegram=True,
            force=True,
        )
    )

    assert result["status"] == "error"
    assert result["mode"] == "error"
    assert "SHOULD_NOT_REACH_TELEGRAM" not in result["error_message"]
    assert telegram.messages == []


def test_status_compacts_legacy_raw_run_payloads(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    huge_prompt = {
        "slot": "post_close",
        "context": {
            "account": {"cash_krw": 1_000_000},
            "blocks": {
                "blocks": [
                    {
                        "block_id": "blk_legacy",
                        "symbol": "005930",
                        "status": "open",
                        "raw": "SHOULD_NOT_LEAK_FROM_STATUS" * 1000,
                    }
                ]
            },
        },
    }
    service.repository.save_run(
        kind="ritual",
        slot="post_close",
        status="llm_unavailable",
        mode="deterministic",
        model="gpt-5.5",
        error_message="codex failed USER: " + ("SHOULD_NOT_LEAK_FROM_STATUS" * 1000),
        input_payload=huge_prompt,
        output_payload={
            "title": "마감 리뷰",
            "message_md": "LLM 메모리 생성은 실패했습니다: USER: "
            + ("SHOULD_NOT_LEAK_FROM_STATUS" * 1000),
        },
    )
    service.repository.upsert_journal(
        trading_day="2026-05-13",
        slot="post_close",
        title="마감 리뷰",
        message_md=(
            "마감 요약\n\nLLM 메모리 생성은 실패했습니다: codex failed USER: "
            + ("SHOULD_NOT_LEAK_FROM_STATUS" * 1000)
        ),
        file_path=str(tmp_path / "memory" / "journals" / "2026-05-13" / "post_close.md"),
        context=huge_prompt["context"],
        sent_telegram=False,
        telegram_result={
            "detail": {
                "result": {
                    "text": "LLM 메모리 생성은 실패했습니다: USER: "
                    + ("SHOULD_NOT_LEAK_FROM_STATUS" * 1000)
                }
            }
        },
    )
    service.repository.record_telegram_send(
        trading_day="2026-05-13",
        slot="post_close",
        status="ok",
        result={
            "detail": {
                "result": {
                    "text": "LLM 메모리 생성은 실패했습니다: USER: "
                    + ("SHOULD_NOT_LEAK_FROM_STATUS" * 1000)
                }
            }
        },
    )

    payload = service.status()
    today = service.today(now=datetime(2026, 5, 13, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")))
    serialized = json.dumps(payload, ensure_ascii=False)
    today_serialized = json.dumps(today, ensure_ascii=False)

    assert len(serialized) < 30_000
    assert "SHOULD_NOT_LEAK_FROM_STATUS" not in serialized
    assert "SHOULD_NOT_LEAK_FROM_STATUS" not in today_serialized
    assert "input" not in payload["latest_run"]
    assert "output" not in payload["latest_run"]
    assert payload["latest_run"]["kind"] == "ritual"
    assert payload["latest_run"]["slot"] == "post_close"


def test_due_slots_do_not_backfill_missed_trading_day_windows(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kst = ZoneInfo("Asia/Seoul")

    late_friday = datetime(2026, 5, 8, 22, 30, tzinfo=kst)
    monday_pre_open = datetime(2026, 5, 11, 8, 35, tzinfo=kst)
    monday_between = datetime(2026, 5, 11, 10, 30, tzinfo=kst)

    assert service.due_slots(now=late_friday) == []
    assert service.due_slots(now=monday_pre_open) == ["pre_open"]
    assert service.due_slots(now=monday_between) == []


def test_due_slots_do_not_probe_next_open_day_outside_monthly_review_window(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    class _RecordingCalendar:
        def __init__(self) -> None:
            self.calls: list[date] = []

        def is_open_day(self, value: date) -> bool:
            self.calls.append(value)
            return value.weekday() < 5

    calendar = _RecordingCalendar()
    service.calendar = calendar  # type: ignore[assignment]

    slots = service.due_slots(now=datetime(2026, 5, 11, 10, 30, tzinfo=KST))

    assert slots == []
    assert calendar.calls == [date(2026, 5, 11)]


def test_llm_updates_symbol_block_and_soft_policy_memory(tmp_path: Path) -> None:
    payload = {
        "title": "마감 리뷰",
        "message_md": "오늘은 블록 약속을 잘 지켰다.",
        "memory_updates": {
            "symbols": [
                {
                    "symbol": "005930",
                    "summary_md": "삼성전자는 단기 수급보다 중기 밸류 확인이 중요했다.",
                    "confidence": 0.7,
                }
            ],
            "blocks": [
                {
                    "block_id": "blk_005930_1",
                    "summary_md": "목표가 도달 전 추격 조정을 피한 점이 좋았다.",
                    "confidence": 0.8,
                }
            ],
        },
        "policy_changes": [
            {
                "policy_id": "avoid_chasing_after_gap",
                "action": "caution",
                "strength": "soft",
                "reason": "갭상승 직후 블록 진입은 확인봉을 기다린다.",
                "confidence": 0.65,
            },
            {
                "policy_id": "ban_low_liquidity",
                "action": "ban",
                "strength": "hard",
                "reason": "유동성 부족 종목 금지 후보",
                "confidence": 0.6,
            },
        ],
    }
    service = _service(tmp_path, llm_payload=payload)

    result = asyncio.run(
        service.run_ritual(
            slot="post_close",
            context={"account": {}, "blocks": {"blocks": []}},
            force=True,
        )
    )
    context_pack = service.context_pack(symbols=["005930"], block_ids=["blk_005930_1"])

    assert result["status"] == "ok"
    assert "삼성전자" in (tmp_path / "memory" / "symbols" / "005930.md").read_text(
        encoding="utf-8"
    )
    assert "목표가" in (
        tmp_path / "memory" / "blocks" / "blk_005930_1.md"
    ).read_text(encoding="utf-8")
    candidate_policies = service.repository.list_policy_changes(
        status="candidate",
        limit=10,
    )

    assert service.active_policies() == []
    assert {row["policy_id"] for row in candidate_policies} == {
        "avoid_chasing_after_gap",
        "ban_low_liquidity",
    }
    assert all(row["status"] == "candidate" for row in candidate_policies)
    assert "avoid_chasing_after_gap" not in json.dumps(context_pack, ensure_ascii=False)


def test_seed_current_creates_initial_memory_once(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = {
        "account": {"cash_krw": 1_000_000, "position_count": 1},
        "blocks": {
            "blocks": [
                {
                    "block_id": "blk_005930_seed",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "open",
                    "qty_open": 1,
                    "entry_price": 80000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "메모리 seed 테스트",
                }
            ]
        },
        "reports_status": {"report_count": 10},
        "strategy": {"candidates": [{"symbol": "005930"}]},
        "valuation_status": {"total_count": 3},
    }

    first = service.seed_current(context=context)
    second = service.seed_current(context=context)
    pack = service.context_pack(symbols=["005930"], block_ids=["blk_005930_seed"])

    assert first["status"] == "ok"
    assert second["status"] == "skipped"
    assert service.status()["seeded"] is True
    assert "blk_005930_seed" in json.dumps(pack, ensure_ascii=False)
    assert "삼성전자" in (tmp_path / "memory" / "symbols" / "005930.md").read_text(
        encoding="utf-8"
    )


def test_due_reflections_are_idempotent_and_build_scorecards(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": "blk_005930_closed",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 84000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "목표가 도달 확인",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "quote": {"high_price": 84500, "low_price": 79000},
                }
            ],
            "orders": [
                {
                    "block_id": "blk_005930_closed",
                    "side": "sell",
                    "limit_price": 84000,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    first = service.run_due_reflections(context=context)
    second = service.run_due_reflections(context=context)
    memory = service.block_memory("blk_005930_closed")

    assert first["status"] == "ok"
    assert first["created_count"] == 1
    assert second["status"] == "skipped"
    assert memory["reflection"]["status"] == "closed"
    assert memory["reflection"]["metrics"]["outcome_date"] == "2026-05-08"
    assert memory["reflection"]["metrics"]["closed_at"] == "2026-05-08T06:30:00+00:00"
    assert service.policy_scorecards()["items"][0]["sample_count"] == 1


def test_due_reflections_audit_jue_wiki_execution_hint_compliance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_wiki_hint_violation"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 3,
                    "entry_price": 80000,
                    "current_price": 77600,
                    "target_price": 88000,
                    "stop_price": 76000,
                    "thesis": "위키 힌트 위반 감사 케이스",
                    "llm_reason": "live execution despite audit hint",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "jue_wiki": {
                            "decision_adjustments": [
                                {
                                    "action": "audit_or_repair_probe_only",
                                    "execution_hint": "cap_to_audit_or_repair_probe",
                                    "evidence_grade": {
                                        "status": "negative",
                                        "instruction": "audit_or_repair_probe_only",
                                        "basis": "decision_adjustment_audit_effectiveness",
                                    },
                                    "reason": "negative audit samples",
                                }
                            ]
                        },
                        "jue_wiki_decision_adjustment_resolution": {
                            "status": "live_execution",
                            "action": "create_block",
                            "reason": "treated as normal live block",
                        },
                    },
                    "quote": {"high_price": 80500, "low_price": 77400},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 77600,
                    "reason": "manager_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["metrics"]["jue_wiki_execution_hint_audit"] == {
        "execution_hint": "cap_to_audit_or_repair_probe",
        "status": "violated",
        "expected": "audit_or_repair_probe_only",
        "actual": "live_execution",
        "evidence_grade_status": "negative",
        "policy_id": "jue_wiki_execution_hint.cap_to_audit_or_repair_probe",
    }
    assert "위키 실행 힌트" in reflection["lesson_md"]
    assert scorecards["jue_wiki_execution_hint.cap_to_audit_or_repair_probe"][
        "source"
    ] == "jue_wiki_execution_hint_audit"
    assert scorecards["jue_wiki_execution_hint.cap_to_audit_or_repair_probe"][
        "hint_violation_count"
    ] == 1


def test_due_reflections_audit_period_memory_coverage_gap(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_period_memory_gap"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 84000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "메모리 공백 override 감사 케이스",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "period_memory_coverage_gap": "kis weekly replay missing",
                        "period_memory_override_reason": (
                            "current live evidence overrides the replay gap"
                        ),
                        "metadata_contract_repair_note": (
                            "metadata contract repair: "
                            "add_period_memory_override_reason_before_scaling; "
                            "resolution: override reason restored before scaling"
                        ),
                        "metadata_contract_audit_resolution": (
                            "override reason restored before scaling"
                        ),
                    },
                    "quote": {"high_price": 84500, "low_price": 79000},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 84000,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["metrics"]["period_memory_coverage_audit"] == {
        "status": "gap_overridden",
        "gap": "kis weekly replay missing",
        "override_reason": "current live evidence overrides the replay gap",
        "metadata_contract_repair_note": (
            "metadata contract repair: "
            "add_period_memory_override_reason_before_scaling; "
            "resolution: override reason restored before scaling"
        ),
        "metadata_contract_audit_resolution": (
            "override reason restored before scaling"
        ),
        "policy_id": "period_memory_coverage.gap_overridden",
    }
    assert "메모리 커버리지" in reflection["lesson_md"]
    assert "kis weekly replay missing" in reflection["lesson_md"]
    assert "override reason restored before scaling" in reflection["lesson_md"]
    assert scorecards["period_memory_coverage.gap_overridden"]["source"] == (
        "period_memory_coverage_audit"
    )
    assert scorecards["period_memory_coverage.gap_overridden"][
        "period_memory_gap_count"
    ] == 1
    assert scorecards["period_memory_coverage.gap_overridden"][
        "metadata_contract_repair_notes"
    ] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: override reason restored before scaling"
    ]


def test_due_reflections_audit_period_memory_contract_metadata_gap(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_period_memory_contract_gap"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 76000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "기간 메모리 계약 누락 감사 케이스",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "period_memory_contract_audit": {
                            "status": "missing_override_reason",
                            "policy_id": "period_memory_coverage.missing_override_reason",
                            "gap": "kis weekly replay missing",
                            "override_reason": "",
                            "missing_metadata": ["period_memory_override_reason"],
                            "required_metadata": [
                                "period_memory_coverage_gap",
                                "period_memory_override_reason",
                            ],
                            "repair_action": (
                                "add_period_memory_override_reason_before_scaling"
                            ),
                            "metadata_contract_repair_note": (
                                "metadata contract repair: "
                                "add_period_memory_override_reason_before_scaling; "
                                "resolution: kept micro probe until override reason is restored"
                            ),
                            "metadata_contract_audit_resolution": (
                                "kept micro probe until override reason is restored"
                            ),
                        }
                    },
                    "quote": {"high_price": 80500, "low_price": 75600},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 76000,
                    "reason": "stop_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=30)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["metrics"]["period_memory_coverage_audit"] == {
        "status": "missing_override_reason",
        "gap": "kis weekly replay missing",
        "override_reason": "",
        "missing_metadata": ["period_memory_override_reason"],
        "required_metadata": [
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ],
        "repair_action": "add_period_memory_override_reason_before_scaling",
        "metadata_contract_repair_note": (
            "metadata contract repair: "
            "add_period_memory_override_reason_before_scaling; "
            "resolution: kept micro probe until override reason is restored"
        ),
        "metadata_contract_audit_resolution": (
            "kept micro probe until override reason is restored"
        ),
        "policy_id": "period_memory_coverage.missing_override_reason",
    }
    assert "메모리 커버리지" in reflection["lesson_md"]
    assert "missing_override_reason" in reflection["lesson_md"]
    assert "add_period_memory_override_reason_before_scaling" in reflection["lesson_md"]
    assert "kept micro probe until override reason is restored" in reflection["lesson_md"]
    assert "metadata contract repair" in reflection["lesson_md"]
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "source"
    ] == "period_memory_coverage_audit"
    assert "우선 수리" in scorecards["period_memory_coverage.missing_override_reason"][
        "reason"
    ]
    assert "add_period_memory_override_reason_before_scaling" in scorecards[
        "period_memory_coverage.missing_override_reason"
    ]["reason"]
    assert "kept micro probe until override reason is restored" in scorecards[
        "period_memory_coverage.missing_override_reason"
    ]["reason"]
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "period_memory_gap_count"
    ] == 1
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "period_memory_override_count"
    ] == 0
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "period_memory_contract_gap_count"
    ] == 1
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "period_memory_missing_metadata"
    ] == ["period_memory_override_reason"]
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "period_memory_repair_actions"
    ] == ["add_period_memory_override_reason_before_scaling"]
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "metadata_contract_audit_resolutions"
    ] == ["kept micro probe until override reason is restored"]
    assert scorecards["period_memory_coverage.missing_override_reason"][
        "metadata_contract_repair_notes"
    ] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]


def test_due_reflections_audit_nested_jue_wiki_execution_hint_from_prompt_adjustment(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_wiki_hint_nested"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 2,
                    "entry_price": 80000,
                    "current_price": 88800,
                    "target_price": 88000,
                    "stop_price": 76000,
                    "thesis": "프롬프트 위키 힌트 자동 첨부 케이스",
                    "llm_reason": "live execution after wiki cross-check",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "jue_wiki_decision_adjustments": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "normal",
                                "decision_adjustment_effectiveness": {
                                    "execution_hint": (
                                        "allow_live_cross_checked_execution"
                                    ),
                                    "sample_count": 5,
                                    "avg_pnl_pct": 2.1,
                                },
                                "evidence_grade": {
                                    "status": "positive",
                                    "instruction": "usable_with_live_cross_check",
                                    "basis": "decision_adjustment_effectiveness",
                                },
                                "reason": "positive wiki-adjusted outcomes",
                            }
                        ],
                        "jue_wiki_decision_adjustment_resolution": {
                            "status": "live_cross_checked_execution",
                            "action": "create_block",
                            "reason": "cross checked against live data",
                        },
                    },
                    "quote": {"high_price": 88900, "low_price": 79900},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 88800,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["metrics"]["jue_wiki_execution_hint_audit"] == {
        "execution_hint": "allow_live_cross_checked_execution",
        "status": "followed",
        "expected": "live_cross_checked_execution",
        "actual": "live_cross_checked_execution",
        "evidence_grade_status": "positive",
        "policy_id": "jue_wiki_execution_hint.allow_live_cross_checked_execution",
    }
    assert scorecards["jue_wiki_execution_hint.allow_live_cross_checked_execution"][
        "source"
    ] == "jue_wiki_execution_hint_audit"
    assert scorecards["jue_wiki_execution_hint.allow_live_cross_checked_execution"][
        "hint_followed_count"
    ] == 1


def test_due_reflections_audit_jue_wiki_usage_contract_resolution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_wiki_usage_contract"
    resolution = (
        "위키는 단독 매매권한이 아니며 live_quote/account_state/risk_gate/"
        "current_price_structure 교차확인 후 1주 대기 블록으로 축소"
    )
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 82400,
                    "target_price": 82400,
                    "stop_price": 76800,
                    "thesis": "위키 사용계약 해소 감사 케이스",
                    "llm_reason": "wiki memory prior with live cross-check",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": resolution,
                    },
                    "quote": {"high_price": 82500, "low_price": 79800},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 82400,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=30)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["metrics"]["jue_wiki_usage_contract_audit"] == {
        "status": "resolved",
        "standalone_trade_authority": False,
        "cross_checks": [
            "live_quote",
            "account_state",
            "risk_gate",
            "current_price_structure",
        ],
        "resolution": resolution,
        "policy_id": "jue_wiki_usage_contract.resolved",
    }
    assert "위키 사용계약" in reflection["lesson_md"]
    assert "live_quote, account_state, risk_gate, current_price_structure" in (
        reflection["lesson_md"]
    )
    assert scorecards["jue_wiki_usage_contract.resolved"]["source"] == (
        "jue_wiki_usage_contract_audit"
    )
    assert scorecards["jue_wiki_usage_contract.resolved"][
        "usage_contract_resolved_count"
    ] == 1


def test_compact_status_keeps_jue_wiki_usage_contract_rule_required_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "blk_005930_wiki_usage_contract_context"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 82400,
                    "target_price": 82400,
                    "stop_price": 76800,
                    "thesis": "위키 사용계약 context pack 케이스",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate/"
                            "current_price_structure 교차확인 후 실행"
                        ),
                    },
                    "quote": {"high_price": 82500, "low_price": 79800},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "side": "sell",
                    "limit_price": 82400,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    service.run_due_reflections(context=context)
    pack = service.status(scope="kis", compact=True)
    compact_rules = {
        row["policy_id"]: row
        for row in pack["policy_rules"]
        if row.get("policy_id") == "jue_wiki_usage_contract.resolved"
    }

    rule = compact_rules["jue_wiki_usage_contract.resolved"]
    assert rule["effect"]["required_evidence"] == [
        "jue_wiki_usage_contract_resolution",
        "live_quote",
        "account_state",
        "risk_gate",
        "fresh_research_conflicts",
        "current_price_structure",
    ]
    assert rule["effect"]["sizing_policy"] == "do_not_scale_on_wiki_memory_alone"
    assert rule["effect"]["require_jue_wiki_usage_contract_audit"] is True


def test_due_reflections_refresh_when_block_closes_after_existing_error_reflection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "bnb_upbit_spot_KRW-JTO_1",
            "symbol": "KRW-JTO",
            "name": "KRW-JTO",
            "status": "error",
            "exit_reason": "waiting entry rejected",
            "pnl_krw": 0,
            "pnl_pct": 0,
            "lesson_md": "초기 미체결/에러 반성",
            "metrics": {"closed_at": ""},
        },
        source_run_id=1,
    )
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute(
            """
            UPDATE block_reflections
            SET created_at = ?, updated_at = ?
            WHERE block_id = ?
            """,
            (
                "2026-07-01T05:30:00+00:00",
                "2026-07-01T05:30:00+00:00",
                "bnb_upbit_spot_KRW-JTO_1",
            ),
        )

    result = service.run_due_reflections(
        context={
            "binance_blocks": {
                "blocks": [
                    {
                        "block_id": "bnb_upbit_spot_KRW-JTO_1",
                        "symbol": "KRW-JTO",
                        "name": "KRW-JTO",
                        "status": "closed",
                        "qty_initial": 1,
                        "entry_price": 100,
                        "current_price": 105,
                        "target_price": 105,
                        "stop_price": 95,
                        "thesis": "최종 청산 반성으로 갱신되어야 한다.",
                        "closed_at": "2026-07-01T06:36:12+00:00",
                        "created_at": "2026-07-01T05:00:00+00:00",
                    }
                ],
                "orders": [
                    {
                        "block_id": "bnb_upbit_spot_KRW-JTO_1",
                        "side": "sell",
                        "limit_price": 105,
                        "reason": "target_close",
                        "created_at": "2026-07-01T06:36:12+00:00",
                    }
                ],
            }
        }
    )
    reflection = service.repository.get_block_reflection("bnb_upbit_spot_KRW-JTO_1")

    assert result["status"] == "ok"
    assert result["created_count"] == 1
    assert reflection is not None
    assert reflection["status"] == "closed"
    assert reflection["metrics"]["closed_at"] == "2026-07-01T06:36:12+00:00"
    assert "최종 청산 반성" in reflection["lesson_md"]


def test_memory_status_uses_reflection_updated_at_for_latest_reflection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "bnb_upbit_spot_KRW-JTO_1",
            "symbol": "KRW-JTO",
            "name": "KRW-JTO",
            "status": "closed",
            "exit_reason": "target_reached",
            "pnl_krw": 1,
            "pnl_pct": 1,
            "lesson_md": "최신 반성",
            "metrics": {"closed_at": "2026-07-01T06:36:12+00:00"},
        },
        source_run_id=1,
    )
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute(
            """
            UPDATE block_reflections
            SET created_at = ?, updated_at = ?
            WHERE block_id = ?
            """,
            (
                "2026-06-29T03:58:34+00:00",
                "2026-07-01T06:57:02+00:00",
                "bnb_upbit_spot_KRW-JTO_1",
            ),
        )

    status = service.status()

    assert status["latest_reflection_at"] == "2026-07-01T06:57:02+00:00"


def test_due_reflections_ingest_rejected_create_events_as_policy_signals(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    rejected_event = {
        "id": 77,
        "block_id": "__system__",
        "event_type": "manager_create_rejected",
        "created_at": "2026-06-16T06:00:00+00:00",
        "payload": {
            "manager_run_id": 12,
            "symbol": "277810",
            "reason": "entry_quality_waiting_entry_required",
            "row": {
                "symbol": "277810",
                "horizon": "short",
                "entry_style": "aggressive_limit",
                "entry_quality_gate": {
                    "version": "kis_entry_quality_gate_v1",
                    "requires_waiting_entry": True,
                    "entry_quality": "extended_momentum",
                    "chase_risk": "high",
                    "price_location": "near_20d_high",
                    "reasons": [
                        "extended_momentum",
                        "chase_risk_high",
                        "price_location_near_20d_high",
                    ],
                },
            },
        },
    }

    first = service.run_due_reflections(
        context={"blocks": {"blocks": [], "orders": [], "events": [rejected_event]}}
    )
    second = service.run_due_reflections(
        context={"blocks": {"blocks": [], "orders": [], "events": [rejected_event]}}
    )
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    events = service.repository.list_memory_events(status="processed", limit=10)
    insights = service.repository.list_insights(
        memory_type="policy_signal",
        key="rejected_entry.entry_quality_waiting_entry_required",
        limit=5,
    )

    assert first["status"] == "ok"
    assert first["created_count"] == 0
    assert first["rejected_event_count"] == 1
    assert second["status"] == "skipped"
    assert second["reason"] == "no_due_reflections"
    assert scorecards["rejected_entry.entry_quality_waiting_entry_required"][
        "sample_count"
    ] == 1
    assert scorecards["rejected_entry.entry_quality_waiting_entry_required"][
        "status"
    ] == "candidate"
    assert scorecards["rejected_entry.entry_quality_waiting_entry_required"][
        "action"
    ] == "observe"
    assert any(
        event["event_key"] == "manager_create_rejected:77"
        for event in events
    )
    assert insights[0]["evidence"][0]["symbol"] == "277810"
    assert "고점 추격" in insights[0]["summary_md"]


def test_due_reflections_ingest_manager_contract_errors_as_memory_signals(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "latest_manager_run": {
            "id": 101,
            "run_at": "2026-07-04T02:00:00+00:00",
            "status": "error",
            "mode": "contract_error",
            "error_message": "research_spine_memory_resolution_missing_from_model",
            "response": {
                "contract_error": (
                    "research_spine_memory_resolution_missing_from_model"
                )
            },
            "latest_decision_input": {
                "memory_contract": {
                    "contract": "cite_or_reject_research_spine_memory",
                    "impacted_symbols": ["005930"],
                    "memory_packet_count": 1,
                    "resolution_status": "missing",
                }
            },
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 202,
                "run_at": "2026-07-04T03:00:00+00:00",
                "status": "error",
                "mode": "contract_error",
                "error_message": (
                    "candidate_memory_hint_resolution_missing_from_model"
                ),
                "response": {
                    "contract_error": (
                        "candidate_memory_hint_resolution_missing_from_model"
                    )
                },
                "latest_decision_input": {
                    "memory_contract": {
                        "contract": "cite_or_reject_candidate_memory_hint",
                        "impacted_symbols": ["BTCUSDT"],
                        "memory_packet_count": 1,
                        "resolution_status": "missing",
                    }
                },
            }
        },
    }

    first = service.run_due_reflections(context=context)
    second = service.run_due_reflections(context=context)
    events = service.repository.list_memory_events(status="processed", limit=10)
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }

    assert first["status"] == "ok"
    assert first["created_count"] == 0
    assert first["contract_error_event_count"] == 2
    assert second["status"] == "skipped"
    assert second["reason"] == "no_due_reflections"
    assert {
        row["event_key"]
        for row in events
        if row["event_type"] == "manager_contract_error"
    } == {
        "manager_contract_error:kis:101",
        "manager_contract_error:binance:202",
    }
    assert scorecards[
        "manager_contract_error.kis.research_spine_memory_resolution_missing_from_model"
    ]["sample_count"] == 1
    assert scorecards[
        "manager_contract_error.binance.candidate_memory_hint_resolution_missing_from_model"
    ]["sample_count"] == 1


def test_due_reflections_ingest_validation_repair_memory_contract_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 303,
                "run_at": "2026-07-04T04:00:00+00:00",
                "status": "error",
                "mode": "contract_error",
                "error_message": "memory_contract_resolution_missing_from_model",
                "response": {
                    "contract_error": (
                        "memory_contract_resolution_missing_from_model"
                    )
                },
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "error",
                        "source": "validation_repair",
                        "contract": "cite_or_reject_research_spine_memory",
                        "error": "memory_contract_resolution_missing_from_model",
                        "memory_contract_errors": [
                            "research_spine_memory_resolution_missing_from_model"
                        ],
                        "memory_packet_count": 1,
                        "impacted_symbols": ["005930"],
                        "resolution_status": "missing",
                    }
                },
            }
        }
    }

    result = service.run_due_reflections(context=context)
    scorecard = service.repository.get_policy_scorecard(
        "manager_contract_error.kis.memory_contract_resolution_missing_from_model"
    )

    assert result["contract_error_event_count"] == 1
    assert scorecard is not None
    assert scorecard["source"] == "manager_contract_error"
    assert scorecard["contract"] == "cite_or_reject_research_spine_memory"
    assert scorecard["latest_error"] == "memory_contract_resolution_missing_from_model"
    assert scorecard["impacted_symbols"] == ["005930"]


def test_due_reflections_ingest_manager_contract_resolutions_as_memory_signals(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 404,
                "run_at": "2026-07-04T05:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "error_message": "",
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "resolved",
                        "source": "validation_repair",
                        "contract": "cite_or_reject_research_spine_memory",
                        "memory_contract_errors": [
                            "research_spine_memory_resolution_missing_from_model"
                        ],
                        "memory_packet_count": 1,
                        "impacted_symbols": ["005930"],
                        "resolution_status": "resolved",
                        "resolved_candidates": [
                            {
                                "symbol": "005930",
                                "resolution": "candidate_rejected",
                                "memory_contract": (
                                    "cite_or_reject_research_spine_memory"
                                ),
                                "memory_contract_error": (
                                    "research_spine_memory_resolution_missing_from_model"
                                ),
                                "memory_contract_resolution": (
                                    "reject_memory_with_reason: 위키 기억을 확인했지만 "
                                    "현재 수급 근거가 약해 대기한다."
                                ),
                            }
                        ],
                    }
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 505,
                "run_at": "2026-07-04T05:10:00+00:00",
                "status": "ok",
                "mode": "llm",
                "error_message": "",
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "resolved",
                        "source": "validation_repair",
                        "contract": "cite_or_reject_candidate_memory_hint",
                        "memory_contract_errors": [
                            "candidate_memory_hint_resolution_missing_from_model"
                        ],
                        "memory_packet_count": 1,
                        "impacted_symbols": ["BTCUSDT"],
                        "resolution_status": "resolved",
                        "resolved_candidates": [
                            {
                                "symbol": "BTCUSDT",
                                "market": "futures",
                                "resolution": "probe_waiting_block",
                                "memory_contract": "cite_or_reject_candidate_memory_hint",
                                "memory_contract_error": (
                                    "candidate_memory_hint_resolution_missing_from_model"
                                ),
                                "memory_contract_resolution": (
                                    "cite_memory_and_apply: 추격 손실 메모리를 반영해 "
                                    "즉시 진입 대신 눌림 대기한다."
                                ),
                            }
                        ],
                    }
                },
            }
        },
    }

    first = service.run_due_reflections(context=context)
    second = service.run_due_reflections(context=context)
    events = service.repository.list_memory_events(status="processed", limit=20)
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }

    assert first["status"] == "ok"
    assert first["created_count"] == 0
    assert first["contract_resolution_event_count"] == 2
    assert second["status"] == "skipped"
    assert {
        row["event_key"]
        for row in events
        if row["event_type"] == "manager_contract_resolution"
    } == {
        "manager_contract_resolution:kis:404",
        "manager_contract_resolution:binance:505",
    }
    kis_scorecard = scorecards[
        "manager_contract_resolution.kis."
        "research_spine_memory_resolution_missing_from_model"
    ]
    binance_scorecard = scorecards[
        "manager_contract_resolution.binance."
        "candidate_memory_hint_resolution_missing_from_model"
    ]
    assert kis_scorecard["sample_count"] == 1
    assert kis_scorecard["source"] == "manager_contract_resolution"
    assert kis_scorecard["contract"] == "cite_or_reject_research_spine_memory"
    assert kis_scorecard["resolution_status"] == "resolved"
    assert kis_scorecard["latest_resolution"] == (
        "reject_memory_with_reason: 위키 기억을 확인했지만 현재 수급 근거가 약해 대기한다."
    )
    assert kis_scorecard["impacted_symbols"] == ["005930"]
    assert binance_scorecard["latest_symbol"] == "BTCUSDT"
    assert binance_scorecard["resolution_status"] == "resolved"


def test_due_reflections_resolution_closes_matching_manager_contract_error(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    error_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 403,
                "run_at": "2026-07-04T04:55:00+00:00",
                "status": "error",
                "mode": "contract_error",
                "error_message": "memory_contract_resolution_missing_from_model",
                "response": {
                    "contract_error": "memory_contract_resolution_missing_from_model"
                },
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "error",
                        "contract": "cite_or_reject_research_spine_memory",
                        "error": "memory_contract_resolution_missing_from_model",
                        "memory_packet_count": 1,
                        "impacted_symbols": ["005930"],
                        "resolution_status": "missing",
                    }
                },
            }
        }
    }
    resolution_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 404,
                "run_at": "2026-07-04T05:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "resolved",
                        "contract": "cite_or_reject_research_spine_memory",
                        "memory_contract_errors": [
                            "memory_contract_resolution_missing_from_model"
                        ],
                        "memory_packet_count": 1,
                        "impacted_symbols": ["005930"],
                        "resolution_status": "resolved",
                        "resolved_candidates": [
                            {
                                "symbol": "005930",
                                "memory_contract": (
                                    "cite_or_reject_research_spine_memory"
                                ),
                                "memory_contract_error": (
                                    "memory_contract_resolution_missing_from_model"
                                ),
                                "memory_contract_resolution": (
                                    "reject_memory_with_reason: 현재 수급이 약해 "
                                    "위키 기억을 보류한다."
                                ),
                            }
                        ],
                    }
                },
            }
        }
    }

    service.run_due_reflections(context=error_context)
    result = service.run_due_reflections(context=resolution_context)
    error_scorecard = service.repository.get_policy_scorecard(
        "manager_contract_error.kis.memory_contract_resolution_missing_from_model"
    )

    assert result["contract_resolution_event_count"] == 1
    assert error_scorecard is not None
    assert error_scorecard["status"] == "resolved"
    assert error_scorecard["action"] == "observe"
    assert error_scorecard["resolution_status"] == "resolved"
    assert error_scorecard["resolution_policy_id"] == (
        "manager_contract_resolution.kis."
        "memory_contract_resolution_missing_from_model"
    )
    assert error_scorecard["latest_resolution"] == (
        "reject_memory_with_reason: 현재 수급이 약해 위키 기억을 보류한다."
    )


def test_due_reflections_ingest_diagnostic_memory_contract_rows(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 515,
                "run_at": "2026-07-04T05:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "error_message": "",
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "memory_contract_status": "partial",
                            "memory_contract_rows": [
                                {
                                    "symbol": "000660",
                                    "status": "unresolved",
                                    "contracts": ["fresh_period_review_or_replay"],
                                    "errors": ["missing fresh period review"],
                                    "resolution_modes": [],
                                },
                                {
                                    "symbol": "005930",
                                    "status": "resolved",
                                    "contracts": ["period_memory_override_reason"],
                                    "errors": ["missing override reason"],
                                    "resolution_modes": ["action_metadata"],
                                },
                            ],
                        }
                    }
                },
            }
        }
    }

    first = service.run_due_reflections(context=context)
    second = service.run_due_reflections(context=context)
    error_scorecard = service.repository.get_policy_scorecard(
        "manager_contract_error.kis.missing_fresh_period_review"
    )
    resolution_scorecard = service.repository.get_policy_scorecard(
        "manager_contract_resolution.kis.missing_override_reason"
    )

    assert first["contract_error_event_count"] == 1
    assert first["contract_resolution_event_count"] == 1
    assert second["status"] == "skipped"
    assert error_scorecard is not None
    assert error_scorecard["source"] == "manager_contract_error"
    assert error_scorecard["contract"] == "fresh_period_review_or_replay"
    assert error_scorecard["impacted_symbols"] == ["000660"]
    assert error_scorecard["memory_contract_rows"] == [
        {
            "symbol": "000660",
            "status": "unresolved",
            "contracts": ["fresh_period_review_or_replay"],
            "errors": ["missing fresh period review"],
            "resolution_modes": [],
        }
    ]
    assert resolution_scorecard is not None
    assert resolution_scorecard["source"] == "manager_contract_resolution"
    assert resolution_scorecard["contract"] == "period_memory_override_reason"
    assert resolution_scorecard["latest_symbol"] == "005930"
    assert resolution_scorecard["latest_resolution"] == "action_metadata"
    assert resolution_scorecard["memory_contract_rows"] == [
        {
            "symbol": "005930",
            "status": "resolved",
            "contracts": ["period_memory_override_reason"],
            "errors": ["missing override reason"],
            "resolution_modes": ["action_metadata"],
        }
    ]


def test_due_reflections_handles_resolved_memory_contract_without_candidates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 516,
                "run_at": "2026-07-04T05:40:00+00:00",
                "status": "ok",
                "mode": "llm",
                "latest_decision_input": {
                    "memory_contract": {
                        "status": "resolved",
                        "source": "validation_repair",
                        "contract": "period_memory_override_reason",
                        "memory_packet_count": 1,
                        "impacted_symbols": ["005930"],
                        "resolution_status": "resolved",
                        "resolved_candidates": [],
                    }
                },
            }
        }
    }

    result = service.run_due_reflections(context=context)
    scorecard = service.repository.get_policy_scorecard(
        "manager_contract_resolution.kis.period_memory_override_reason"
    )

    assert result["contract_resolution_event_count"] == 1
    assert scorecard is not None
    assert scorecard["resolution_status"] == "resolved"
    assert scorecard["contract"] == "period_memory_override_reason"
    assert scorecard["impacted_symbols"] == ["005930"]


def test_due_reflections_ingest_jue_wiki_selection_audit_as_memory_signal(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 606,
                "run_at": "2026-07-04T06:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "jue_wiki_application": {
                        "status": "ok",
                        "selected_page_ids": ["kis.ops.manager_runs"],
                        "selection_audit": {
                            "selected_page_count": 2,
                            "reason_counts": {
                                "scope_match:kis": 2,
                                "operational_memory:manager_contract_recovery": 1,
                            },
                            "penalty_counts": {"freshness:stale": 1},
                            "top_pages": [
                                {
                                    "page_id": "kis.ops.manager_runs",
                                    "rank": 1,
                                    "selection_reasons": [
                                        "scope_match:kis",
                                        "operational_memory:manager_contract_recovery",
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
        }
    }

    first = service.run_due_reflections(context=context)
    second = service.run_due_reflections(context=context)
    events = service.repository.list_memory_events(status="processed", limit=10)
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_selection.kis.operational_memory_manager_contract_recovery"
    )
    insights = service.repository.list_insights(
        memory_type="policy_signal",
        key="jue_wiki_selection.kis.operational_memory_manager_contract_recovery",
        limit=5,
    )

    assert first["status"] == "ok"
    assert first["created_count"] == 0
    assert first["jue_wiki_selection_audit_event_count"] == 1
    assert second["status"] == "skipped"
    assert second["reason"] == "no_due_reflections"
    assert any(
        event["event_key"] == "jue_wiki_selection_audit:kis:606"
        and event["event_type"] == "jue_wiki_selection_audit"
        for event in events
    )
    assert scorecard is not None
    assert scorecard["sample_count"] == 1
    assert scorecard["status"] == "active_observation"
    assert scorecard["memory_scope"] == "kis"
    assert scorecard["transferability"] == "direct"
    assert scorecard["latest_reason"] == "operational_memory:manager_contract_recovery"
    assert scorecard["selected_page_ids"] == ["kis.ops.manager_runs"]
    event_payload = next(
        event["payload"]
        for event in events
        if event["event_key"] == "jue_wiki_selection_audit:kis:606"
    )
    assert event_payload["memory_scope"] == "kis"
    assert event_payload["transferability"] == "direct"
    assert event_payload["scope_evidence"] == [
        {
            "memory_scope": "kis",
            "transferability": "direct",
            "source": "jue_wiki_selection_audit",
        }
    ]
    assert insights[0]["memory_scope"] == "kis"
    assert insights[0]["transferability"] == "direct"
    assert insights[0]["evidence"][0]["memory_scope"] == "kis"
    assert insights[0]["evidence"][0]["transferability"] == "direct"
    assert insights[0]["evidence"][0]["top_pages"][0]["page_id"] == (
        "kis.ops.manager_runs"
    )


def test_due_reflections_ingest_compact_manager_context_jue_wiki_selection_audit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 609,
                "run_at": "2026-07-04T07:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "compact_manager_context": {
                        "jue_wiki_application": {
                            "status": "ok",
                            "selected_page_ids": ["kis.ops.manager_runs"],
                            "selection_audit": {
                                "selected_page_count": 1,
                                "reason_counts": {
                                    "operational_memory:manager_contract_recovery": 1,
                                },
                                "top_pages": [
                                    {
                                        "page_id": "kis.ops.manager_runs",
                                        "rank": 1,
                                        "selection_reasons": [
                                            "operational_memory:manager_contract_recovery"
                                        ],
                                    }
                                ],
                            },
                        }
                    }
                },
            }
        }
    }

    result = service.run_due_reflections(context=context)
    event = service.repository.get_memory_event("jue_wiki_selection_audit:kis:609")
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_selection.kis.operational_memory_manager_contract_recovery"
    )

    assert result["status"] == "ok"
    assert result["jue_wiki_selection_audit_event_count"] == 1
    assert event is not None
    assert event["payload"]["selected_page_ids"] == ["kis.ops.manager_runs"]
    assert scorecard is not None
    assert scorecard["selected_page_ids"] == ["kis.ops.manager_runs"]


def test_due_reflections_ingest_compact_diagnostics_wiki_selection_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 610,
                "run_at": "2026-07-04T08:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_intraday_manager",
                "workflow_version": 1,
                "skill_ids": ["jue-kis-trading"],
                "contract_ids": ["jue_wiki_usage_contract_resolution"],
                "prompt": {
                    "compact_manager_context": {
                        "jue_wiki_application": {
                            "status": "ok",
                            "selected_page_ids": ["kis.ops.manager_runs"],
                        },
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "jue_wiki_selection_guidance_status": "active",
                            "jue_wiki_selection_guidance_resolution_status": (
                                "unresolved"
                            ),
                            "blocker_tags": {
                                "unresolved_jue_wiki_selection_guidance": 2
                            },
                        },
                    }
                },
            }
        }
    }

    result = service.run_due_reflections(context=context)
    policy_id = "jue_wiki_selection.kis.diagnostics_selection_guidance"
    event = service.repository.get_memory_event("jue_wiki_selection_audit:kis:610")
    scorecard = service.repository.get_policy_scorecard(policy_id)
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    selection_memory = {
        row["policy_id"]: row
        for row in pack["jue_wiki_selection_memory"].get("items", [])
    }

    assert result["status"] == "ok"
    assert result["jue_wiki_selection_audit_event_count"] == 1
    assert event is not None
    assert event["payload"]["primary_reason"] == "diagnostics:selection_guidance"
    assert event["payload"]["workflow_id"] == "kis_intraday_manager"
    assert event["payload"]["contract_ids"] == [
        "jue_wiki_usage_contract_resolution"
    ]
    assert event["payload"]["penalty_counts"] == {
        "freshness:stale": 2,
        "selection_guidance:unresolved": 1,
    }
    assert scorecard is not None
    assert scorecard["status"] == "active_caution"
    assert scorecard["selected_page_ids"] == ["kis.ops.manager_runs"]
    assert scorecard["workflow_ids"] == ["kis_intraday_manager"]
    assert scorecard["contract_ids"] == ["jue_wiki_usage_contract_resolution"]
    assert selection_memory[policy_id]["workflow_ids"] == ["kis_intraday_manager"]
    assert selection_memory[policy_id]["contract_ids"] == [
        "jue_wiki_usage_contract_resolution"
    ]
    assert selection_memory[policy_id]["application_guidance"]["status"] == (
        "freshness_repair_required"
    )


def test_due_reflections_ingest_unresolved_jue_wiki_context_gap_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 611,
                "run_at": "2026-07-04T08:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_intraday_manager",
                "workflow_version": 1,
                "skill_ids": ["jue-kis-trading"],
                "contract_ids": ["jue_wiki_context_gap_resolution"],
                "prompt": {
                    "investment_memory": {
                        "jue_wiki": {
                            "status": "error",
                            "available": False,
                            "reason": "wiki_context_provider_timeout",
                        }
                    },
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "jue_wiki_context_gap_status": "active",
                            "jue_wiki_context_gap_resolution_status": (
                                "unresolved"
                            ),
                            "blocker_tags": {
                                "unresolved_jue_wiki_context_gap": 2
                            },
                        },
                    },
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 612,
                "run_at": "2026-07-04T08:40:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "binance_cycle",
                "workflow_version": 1,
                "skill_ids": ["jue-binance-trading"],
                "contract_ids": ["jue_wiki_context_gap_resolution"],
                "prompt": {
                    "memory": {
                        "jue_wiki": {
                            "status": "disabled",
                            "available": False,
                            "reason": "wiki_context_provider_unavailable",
                        }
                    },
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "binance_manager_diagnostics_v1",
                            "jue_wiki_context_gap_status": "active",
                            "jue_wiki_context_gap_resolution_status": (
                                "unresolved"
                            ),
                            "blocker_tags": {
                                "unresolved_jue_wiki_context_gap": 2
                            },
                        },
                    },
                },
            }
        },
    }

    result = service.run_due_reflections(context=context)
    kis_event = service.repository.get_memory_event("jue_wiki_context_gap:kis:611")
    binance_event = service.repository.get_memory_event(
        "jue_wiki_context_gap:binance:612"
    )
    kis_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_context_gap.kis.wiki_context_provider_timeout"
    )
    binance_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_context_gap.binance.wiki_context_provider_unavailable"
    )

    assert result["status"] == "ok"
    assert result["jue_wiki_context_gap_event_count"] == 2
    assert kis_event is not None
    assert kis_event["event_type"] == "jue_wiki_context_gap"
    assert kis_event["payload"]["memory_scope"] == "kis"
    assert kis_event["payload"]["workflow_id"] == "kis_intraday_manager"
    assert kis_event["payload"]["contract_ids"] == [
        "jue_wiki_context_gap_resolution"
    ]
    assert kis_event["payload"]["reason"] == "wiki_context_provider_timeout"
    assert binance_event is not None
    assert binance_event["payload"]["memory_scope"] == "binance"
    assert binance_event["payload"]["workflow_id"] == "binance_cycle"
    assert binance_event["payload"]["skill_ids"] == ["jue-binance-trading"]
    assert binance_event["payload"]["reason"] == "wiki_context_provider_unavailable"
    assert kis_scorecard is not None
    assert kis_scorecard["source"] == "jue_wiki_context_gap"
    assert kis_scorecard["status"] == "active_caution"
    assert kis_scorecard["memory_scope"] == "kis"
    assert kis_scorecard["transferability"] == "direct"
    assert kis_scorecard["workflow_ids"] == ["kis_intraday_manager"]
    assert kis_scorecard["contract_ids"] == ["jue_wiki_context_gap_resolution"]
    assert binance_scorecard is not None
    assert binance_scorecard["memory_scope"] == "binance"


def test_context_pack_exposes_jue_wiki_context_gap_memory_by_scope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 613,
                "run_at": "2026-07-04T08:45:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "investment_memory": {
                        "jue_wiki": {
                            "status": "error",
                            "available": False,
                            "reason": "wiki_context_provider_timeout",
                        }
                    },
                    "compact_manager_context": {
                        "diagnostics": {
                            "jue_wiki_context_gap_status": "active",
                            "jue_wiki_context_gap_resolution_status": "unresolved",
                            "blocker_tags": {
                                "unresolved_jue_wiki_context_gap": 2
                            },
                        },
                    },
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 614,
                "run_at": "2026-07-04T08:50:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "memory": {
                        "jue_wiki": {
                            "status": "disabled",
                            "available": False,
                            "reason": "wiki_context_provider_unavailable",
                        }
                    },
                    "compact_manager_context": {
                        "diagnostics": {
                            "jue_wiki_context_gap_status": "active",
                            "jue_wiki_context_gap_resolution_status": "unresolved",
                            "blocker_tags": {
                                "unresolved_jue_wiki_context_gap": 2
                            },
                        },
                    },
                },
            }
        },
    }

    service.run_due_reflections(context=context)
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)
    kis_items = {
        row["policy_id"]: row
        for row in kis_pack["jue_wiki_context_gap_memory"].get("items", [])
    }
    binance_items = {
        row["policy_id"]: row
        for row in binance_pack["jue_wiki_context_gap_memory"].get("items", [])
    }

    assert kis_pack["jue_wiki_context_gap_memory"]["status"] == "available"
    assert (
        "jue_wiki_context_gap.kis.wiki_context_provider_timeout" in kis_items
    )
    assert kis_items[
        "jue_wiki_context_gap.kis.wiki_context_provider_timeout"
    ]["application_guidance"] == {
        "status": "context_gap_repair_required",
        "manager_instruction": (
            "verify_wiki_context_or_record_jue_wiki_context_gap_before_action"
        ),
        "required_evidence": [
            "fresh_jue_wiki_context",
            "jue_wiki_context_gap",
            "live_cross_check",
        ],
        "gap_reason": "wiki_context_provider_timeout",
    }
    assert (
        "jue_wiki_context_gap.binance.wiki_context_provider_unavailable"
        not in kis_items
    )
    assert (
        "jue_wiki_context_gap.binance.wiki_context_provider_unavailable"
        in binance_items
    )
    assert (
        "jue_wiki_context_gap.kis.wiki_context_provider_timeout"
        not in binance_items
    )


def test_due_reflections_ingest_missing_jue_wiki_action_reference_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 615,
                "run_at": "2026-07-04T09:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_intraday_manager",
                "workflow_version": 1,
                "skill_ids": ["jue-kis-trading"],
                "contract_ids": ["jue_wiki_action_reference_contract"],
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "action_count": 2,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "jue_wiki_action_reference_missing_actions": [
                                {
                                    "section": "create_blocks",
                                    "symbol": "005930",
                                    "qty": 1,
                                    "horizon": "mid",
                                }
                            ],
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1
                            },
                        },
                    },
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 616,
                "run_at": "2026-07-04T09:10:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "binance_cycle",
                "workflow_version": 1,
                "skill_ids": ["jue-binance-trading"],
                "contract_ids": ["jue_wiki_action_reference_contract"],
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "binance_manager_diagnostics_v1",
                            "action_count": 3,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "jue_wiki_action_reference_missing_actions": [
                                {
                                    "section": "create_blocks",
                                    "symbol": "NEARUSDT",
                                    "market": "futures",
                                    "side": "long",
                                    "lane": "futures:long",
                                }
                            ],
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1
                            },
                        },
                    },
                },
            }
        },
    }

    result = service.run_due_reflections(context=context)
    kis_event = service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:kis:615"
    )
    binance_event = service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:binance:616"
    )
    kis_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.kis.missing"
    )
    binance_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.binance.missing"
    )
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)

    assert result["status"] == "ok"
    assert result["jue_wiki_action_reference_gap_event_count"] == 2
    assert kis_event is not None
    assert kis_event["event_type"] == "jue_wiki_action_reference_gap"
    assert kis_event["payload"]["memory_scope"] == "kis"
    assert kis_event["payload"]["workflow_id"] == "kis_intraday_manager"
    assert kis_event["payload"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]
    assert kis_event["payload"]["action_count"] == 2
    assert kis_event["payload"]["missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    assert binance_event is not None
    assert binance_event["payload"]["memory_scope"] == "binance"
    assert binance_event["payload"]["workflow_id"] == "binance_cycle"
    assert binance_event["payload"]["skill_ids"] == ["jue-binance-trading"]
    assert binance_event["payload"]["action_count"] == 3
    assert binance_event["payload"]["missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]
    assert kis_scorecard is not None
    assert kis_scorecard["source"] == "jue_wiki_action_reference_gap"
    assert kis_scorecard["status"] == "active_caution"
    assert kis_scorecard["memory_scope"] == "kis"
    assert kis_scorecard["workflow_ids"] == ["kis_intraday_manager"]
    assert kis_scorecard["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]
    assert kis_scorecard["missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    kis_memory_item = kis_pack["jue_wiki_action_reference_memory"]["items"][0]
    assert kis_memory_item["missing_actions"] == kis_scorecard["missing_actions"]
    assert (
        kis_memory_item["application_guidance"]["missing_actions"]
        == kis_scorecard["missing_actions"]
    )
    assert binance_scorecard is not None
    assert binance_scorecard["memory_scope"] == "binance"
    assert binance_scorecard["missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]
    binance_memory_item = binance_pack["jue_wiki_action_reference_memory"]["items"][0]
    assert binance_memory_item["missing_actions"] == (
        binance_scorecard["missing_actions"]
    )
    assert binance_memory_item["application_guidance"]["missing_actions"] == (
        binance_scorecard["missing_actions"]
    )


def test_due_reflections_ingest_missing_jue_wiki_usage_contract_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 735,
                "run_at": "2026-07-04T10:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_intraday_manager",
                "workflow_version": 1,
                "skill_ids": ["jue-kis-trading"],
                "contract_ids": ["jue_wiki_usage_contract_resolution"],
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "action_count": 2,
                            "jue_wiki_usage_contract_status": "missing",
                            "jue_wiki_usage_contract_resolution_count": 0,
                            "jue_wiki_usage_contract_resolution_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_usage_contract_resolution": 2
                            },
                        },
                    },
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 736,
                "run_at": "2026-07-04T10:10:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "binance_cycle",
                "workflow_version": 1,
                "skill_ids": ["jue-binance-trading"],
                "contract_ids": ["jue_wiki_usage_contract_resolution"],
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "version": "binance_manager_diagnostics_v1",
                            "action_count": 3,
                            "jue_wiki_usage_contract_status": "missing",
                            "jue_wiki_usage_contract_resolution_count": 0,
                            "jue_wiki_usage_contract_resolution_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_usage_contract_resolution": 3
                            },
                        },
                    },
                },
            }
        },
    }

    result = service.run_due_reflections(context=context)
    kis_event = service.repository.get_memory_event(
        "jue_wiki_usage_contract_gap:kis:735"
    )
    binance_event = service.repository.get_memory_event(
        "jue_wiki_usage_contract_gap:binance:736"
    )
    kis_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_usage_contract_gap.kis.missing"
    )
    binance_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_usage_contract_gap.binance.missing"
    )
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    kis_items = {
        row["policy_id"]: row
        for row in kis_pack["jue_wiki_usage_contract_memory"].get("items", [])
    }

    assert result["status"] == "ok"
    assert result["jue_wiki_usage_contract_gap_event_count"] == 2
    assert kis_event is not None
    assert kis_event["event_type"] == "jue_wiki_usage_contract_gap"
    assert kis_event["payload"]["memory_scope"] == "kis"
    assert kis_event["payload"]["workflow_id"] == "kis_intraday_manager"
    assert kis_event["payload"]["workflow_version"] == 1
    assert kis_event["payload"]["skill_ids"] == ["jue-kis-trading"]
    assert kis_event["payload"]["contract_ids"] == [
        "jue_wiki_usage_contract_resolution"
    ]
    assert kis_event["payload"]["action_count"] == 2
    assert kis_event["payload"]["resolution_count"] == 0
    assert binance_event is not None
    assert binance_event["payload"]["memory_scope"] == "binance"
    assert binance_event["payload"]["workflow_id"] == "binance_cycle"
    assert binance_event["payload"]["skill_ids"] == ["jue-binance-trading"]
    assert binance_event["payload"]["contract_ids"] == [
        "jue_wiki_usage_contract_resolution"
    ]
    assert binance_event["payload"]["action_count"] == 3
    assert kis_scorecard is not None
    assert kis_scorecard["source"] == "jue_wiki_usage_contract_gap"
    assert kis_scorecard["status"] == "active_caution"
    assert kis_scorecard["memory_scope"] == "kis"
    assert kis_scorecard["workflow_ids"] == ["kis_intraday_manager"]
    assert kis_scorecard["contract_ids"] == ["jue_wiki_usage_contract_resolution"]
    assert binance_scorecard is not None
    assert binance_scorecard["memory_scope"] == "binance"
    assert "jue_wiki_usage_contract_gap.kis.missing" in kis_items
    assert kis_items["jue_wiki_usage_contract_gap.kis.missing"]["workflow_ids"] == [
        "kis_intraday_manager"
    ]
    assert kis_items["jue_wiki_usage_contract_gap.kis.missing"]["contract_ids"] == [
        "jue_wiki_usage_contract_resolution"
    ]
    assert kis_items[
        "jue_wiki_usage_contract_gap.kis.missing"
    ]["application_guidance"] == {
        "status": "wiki_usage_contract_resolution_required",
        "manager_instruction": (
            "record_jue_wiki_usage_contract_resolution_on_wiki_influenced_actions"
        ),
        "required_evidence": [
            "jue_wiki_usage_contract_resolution",
            "live_quote_or_spread",
            "account_or_margin_state",
            "risk_gate",
            "fresh_research_or_quant_conflicts",
            "current_price_structure_or_orderbook_depth",
        ],
        "latest_status": "missing",
    }


def test_due_reflections_ingests_wiki_action_gap_from_manager_runs_decision_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "manager_runs": [
                {
                    "id": 715,
                    "run_at": "2026-07-04T10:00:00+00:00",
                    "status": "ok",
                    "mode": "llm",
                    "decision_context": {
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "action_count": 1,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1,
                            },
                        },
                    },
                }
            ],
        },
        "binance_blocks": {
            "manager_runs": [
                {
                    "id": 716,
                    "run_at": "2026-07-04T10:10:00+00:00",
                    "status": "ok",
                    "mode": "llm",
                    "decision_context": {
                        "diagnostics": {
                            "version": "binance_manager_diagnostics_v1",
                            "action_count": 4,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1,
                            },
                        },
                    },
                }
            ],
        },
    }

    result = service.run_due_reflections(context=context)

    assert result["status"] == "ok"
    assert result["jue_wiki_action_reference_gap_event_count"] == 2
    assert service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:kis:715"
    ) is not None
    binance_event = service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:binance:716"
    )
    assert binance_event is not None
    assert binance_event["payload"]["memory_scope"] == "binance"
    assert binance_event["payload"]["action_count"] == 4


def test_due_reflections_ingests_unresolved_wiki_action_reference_memory_without_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 717,
                "run_at": "2026-07-04T10:20:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_memory": 2,
                    },
                },
            },
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 718,
                "run_at": "2026-07-04T10:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_memory": 2,
                    },
                },
            },
        },
    }

    result = service.run_due_reflections(context=context)
    kis_event = service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:kis:717"
    )
    binance_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.binance.unresolved_memory"
    )

    assert result["status"] == "ok"
    assert result["jue_wiki_action_reference_gap_event_count"] == 2
    assert kis_event is not None
    assert kis_event["payload"]["status"] == "no_actions"
    assert kis_event["payload"]["resolution_status"] == "unresolved"
    assert kis_event["payload"]["unresolved_memory_blocker_count"] == 2
    assert binance_scorecard is not None
    assert binance_scorecard["memory_scope"] == "binance"


def test_due_reflections_ingests_unresolved_wiki_action_reference_recovery(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 722,
                "run_at": "2026-07-04T10:40:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "unresolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 3,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "missing",
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_recovery": 3,
                    },
                },
            },
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 723,
                "run_at": "2026-07-04T10:45:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "open_gaps",
                    "jue_wiki_action_reference_recovery_open_gap_count": 4,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "missing",
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_recovery": 4,
                    },
                },
            },
        },
    }

    result = service.run_due_reflections(context=context)
    kis_event = service.repository.get_memory_event(
        "jue_wiki_action_reference_gap:kis:722"
    )
    binance_scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.binance.unresolved_recovery"
    )

    assert result["status"] == "ok"
    assert result["jue_wiki_action_reference_gap_event_count"] == 2
    assert kis_event is not None
    assert kis_event["payload"]["recovery_blocker_count"] == 3
    assert kis_event["payload"]["recovery_status"] == "unresolved"
    assert kis_event["payload"]["recovery_latest_resolution_status"] == "unresolved"
    assert binance_scorecard is not None
    assert binance_scorecard["memory_scope"] == "binance"
    assert binance_scorecard["policy_id"] == (
        "jue_wiki_action_reference_gap.binance.unresolved_recovery"
    )
    assert binance_scorecard["recovery_blocker_count"] == 4
    assert binance_scorecard["recovery_status"] == "open_gaps"
    assert binance_scorecard["recovery_open_gap_count"] == 4
    assert binance_scorecard["recovery_latest_resolution_status"] == "unresolved"


def test_context_pack_turns_unresolved_wiki_action_reference_recovery_into_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 724,
                "run_at": "2026-07-04T10:50:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "unresolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 3,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "missing",
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_recovery": 3,
                    },
                },
            },
        },
    }

    service.run_due_reflections(context=context)
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    items = {
        row["policy_id"]: row
        for row in pack["jue_wiki_action_reference_memory"].get("items", [])
    }
    item = items["jue_wiki_action_reference_gap.kis.unresolved_recovery"]

    assert item["recovery_blocker_count"] == 3
    assert item["recovery_status"] == "unresolved"
    assert item["recovery_open_gap_count"] == 3
    assert item["recovery_latest_resolution_status"] == "unresolved"
    assert item["application_guidance"] == {
        "status": "wiki_reference_recovery_required",
        "manager_instruction": (
            "resolve_action_reference_recovery_before_next_decision"
        ),
        "required_evidence": [
            "jue_wiki_action_reference_recovery",
            "jue_wiki_reference_basis",
            "jue_wiki_freshness_cross_check",
            "jue_wiki_selection_resolution",
            "explicit_non_wiki_basis_if_no_action",
            "live_cross_check",
        ],
        "latest_status": "no_actions",
        "recovery_status": "unresolved",
        "recovery_latest_resolution_status": "unresolved",
        "recovery_open_gap_count": 3,
    }


def test_due_reflections_resolves_wiki_action_reference_recovery_policy(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    unresolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 725,
                "run_at": "2026-07-04T11:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_intraday_manager",
                "workflow_version": 1,
                "skill_ids": ["jue-kis-trading"],
                "contract_ids": ["jue_wiki_action_reference_contract"],
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "unresolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 2,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "missing",
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_recovery": 2,
                    },
                },
            },
        },
    }
    resolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 726,
                "run_at": "2026-07-04T11:10:00+00:00",
                "status": "ok",
                "mode": "llm",
                "workflow_id": "kis_recovery_manager",
                "workflow_version": 2,
                "skill_ids": ["jue-kis-trading", "jue-memory-reflection"],
                "contract_ids": [
                    "jue_wiki_action_reference_contract",
                    "jue_wiki_action_reference_recovery",
                ],
                "prompt": {
                    "investment_memory": {
                        "jue_wiki_action_reference_memory": {
                            "status": "available",
                            "items": [
                                {
                                    "policy_id": (
                                        "jue_wiki_action_reference_gap."
                                        "kis.unresolved_recovery"
                                    ),
                                    "application_guidance": {
                                        "manager_instruction": (
                                            "resolve_action_reference_recovery_"
                                            "before_next_decision"
                                        )
                                    },
                                }
                            ],
                        }
                    }
                },
                "diagnostics": {
                    "action_count": 1,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "action_metadata"
                    ),
                    "jue_wiki_action_reference_status": "referenced",
                    "jue_wiki_action_reference_count": 1,
                    "jue_wiki_action_reference_ratio": 1.0,
                    "jue_wiki_action_reference_recovery_status": "resolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 0,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "action_metadata"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "referenced",
                    "blocker_tags": {},
                },
            },
        },
    }

    first = service.run_due_reflections(context=unresolved_context)
    second = service.run_due_reflections(context=resolved_context)
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.kis.unresolved_recovery"
    )
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    status = service.status(scope="kis", compact=True)

    assert first["jue_wiki_action_reference_gap_event_count"] == 1
    assert second["jue_wiki_action_reference_resolution_event_count"] == 1
    assert scorecard is not None
    assert scorecard["status"] == "resolved"
    assert scorecard["resolution_status"] == "action_metadata"
    assert scorecard["recovery_resolution_status"] == "action_metadata"
    assert scorecard["recovery_latest_status"] == "referenced"
    assert scorecard["workflow_ids"] == [
        "kis_intraday_manager",
        "kis_recovery_manager",
    ]
    assert scorecard["skill_ids"] == [
        "jue-kis-trading",
        "jue-memory-reflection",
    ]
    assert scorecard["contract_ids"] == [
        "jue_wiki_action_reference_contract",
        "jue_wiki_action_reference_recovery",
    ]
    assert pack["jue_wiki_action_reference_memory"]["status"] == "missing"
    assert status["jue_wiki_action_reference_recovery"]["status"] == "resolved"


def test_due_reflections_records_hold_trigger_wiki_action_reference_recovery(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    unresolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 727,
                "run_at": "2026-07-04T11:20:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "unresolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 1,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "missing",
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_recovery": 1,
                    },
                },
            },
        },
    }
    resolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 728,
                "run_at": "2026-07-04T11:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "investment_memory": {
                        "jue_wiki_action_reference_memory": {
                            "status": "available",
                            "items": [
                                {
                                    "policy_id": (
                                        "jue_wiki_action_reference_gap."
                                        "kis.unresolved_recovery"
                                    ),
                                    "application_guidance": {
                                        "manager_instruction": (
                                            "resolve_action_reference_recovery_"
                                            "before_next_decision"
                                        )
                                    },
                                }
                            ],
                        }
                    }
                },
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "hold_trigger"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "jue_wiki_action_reference_recovery_status": "resolved",
                    "jue_wiki_action_reference_recovery_open_gap_count": 0,
                    "jue_wiki_action_reference_recovery_latest_resolution_status": (
                        "hold_trigger"
                    ),
                    "jue_wiki_action_reference_recovery_latest_status": "no_actions",
                    "blocker_tags": {},
                },
            },
        },
    }

    service.run_due_reflections(context=unresolved_context)
    result = service.run_due_reflections(context=resolved_context)
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.kis.unresolved_recovery"
    )

    assert result["jue_wiki_action_reference_resolution_event_count"] == 1
    assert scorecard is not None
    assert scorecard["status"] == "resolved"
    assert scorecard["resolution_status"] == "hold_trigger"
    assert scorecard["recovery_resolution_status"] == "hold_trigger"
    assert "관망" in scorecard["reason"]


def test_context_pack_exposes_jue_wiki_action_reference_memory_by_scope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 617,
                "run_at": "2026-07-04T09:20:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "action_count": 2,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1
                            },
                        },
                    },
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 618,
                "run_at": "2026-07-04T09:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "compact_manager_context": {
                        "diagnostics": {
                            "action_count": 3,
                            "jue_wiki_action_reference_status": "missing",
                            "jue_wiki_action_reference_count": 0,
                            "jue_wiki_action_reference_ratio": 0.0,
                            "blocker_tags": {
                                "missing_jue_wiki_action_reference": 1
                            },
                        },
                    },
                },
            }
        },
    }

    service.run_due_reflections(context=context)
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)
    kis_items = {
        row["policy_id"]: row
        for row in kis_pack["jue_wiki_action_reference_memory"].get("items", [])
    }
    binance_items = {
        row["policy_id"]: row
        for row in binance_pack["jue_wiki_action_reference_memory"].get("items", [])
    }

    assert kis_pack["jue_wiki_action_reference_memory"]["status"] == "available"
    assert "jue_wiki_action_reference_gap.kis.missing" in kis_items
    assert kis_items[
        "jue_wiki_action_reference_gap.kis.missing"
    ]["application_guidance"] == {
        "status": "wiki_reference_repair_required",
        "manager_instruction": (
            "attach_jue_wiki_reference_or_explicitly_record_non_wiki_basis"
        ),
        "required_evidence": [
            "jue_wiki_freshness_cross_check",
            "jue_wiki_selection_resolution",
            "live_cross_check",
        ],
        "latest_status": "missing",
    }
    assert "jue_wiki_action_reference_gap.binance.missing" not in kis_items
    assert "jue_wiki_action_reference_gap.binance.missing" in binance_items
    assert "jue_wiki_action_reference_gap.kis.missing" not in binance_items


def test_context_pack_turns_unresolved_wiki_action_reference_memory_into_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 719,
                "run_at": "2026-07-04T10:40:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_memory": 3,
                    },
                },
            },
        },
    }

    service.run_due_reflections(context=context)
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    items = {
        row["policy_id"]: row
        for row in pack["jue_wiki_action_reference_memory"].get("items", [])
    }
    item = items["jue_wiki_action_reference_gap.kis.unresolved_memory"]

    assert item["memory_status"] == "active"
    assert item["resolution_status"] == "unresolved"
    assert item["unresolved_memory_blocker_count"] == 3
    assert item["application_guidance"] == {
        "status": "wiki_reference_repair_required",
        "manager_instruction": (
            "resolve_action_reference_memory_before_next_decision"
        ),
        "required_evidence": [
            "jue_wiki_reference_basis",
            "jue_wiki_freshness_cross_check",
            "jue_wiki_selection_resolution",
            "explicit_non_wiki_basis_if_no_action",
            "live_cross_check",
        ],
        "latest_status": "no_actions",
        "memory_status": "active",
        "resolution_status": "unresolved",
    }


def test_due_reflections_resolves_wiki_action_reference_memory_policy(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    unresolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 720,
                "run_at": "2026-07-04T10:50:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_memory": 2,
                    },
                },
            },
        },
    }
    resolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 721,
                "run_at": "2026-07-04T11:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "investment_memory": {
                        "jue_wiki_action_reference_memory": {
                            "status": "available",
                            "items": [
                                {
                                    "policy_id": (
                                        "jue_wiki_action_reference_gap."
                                        "kis.unresolved_memory"
                                    ),
                                    "application_guidance": {
                                        "manager_instruction": (
                                            "resolve_action_reference_memory_"
                                            "before_next_decision"
                                        )
                                    },
                                }
                            ],
                        }
                    }
                },
                "diagnostics": {
                    "action_count": 1,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "action_metadata"
                    ),
                    "jue_wiki_action_reference_status": "referenced",
                    "jue_wiki_action_reference_count": 1,
                    "jue_wiki_action_reference_ratio": 1.0,
                    "blocker_tags": {},
                },
            },
        },
    }

    first = service.run_due_reflections(context=unresolved_context)
    second = service.run_due_reflections(context=resolved_context)
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.kis.unresolved_memory"
    )
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    status = service.status(scope="kis", compact=True)
    item_ids = {
        row["policy_id"]
        for row in pack["jue_wiki_action_reference_memory"].get("items", [])
    }

    assert first["jue_wiki_action_reference_gap_event_count"] == 1
    resolution_events = [
        event
        for event in service.repository.list_memory_events(
            status="processed",
            limit=20,
        )
        if event["event_type"] == "jue_wiki_action_reference_resolution"
    ]

    assert second["jue_wiki_action_reference_gap_event_count"] == 0
    assert second["jue_wiki_action_reference_resolution_event_count"] == 1
    assert second["jue_wiki_action_reference_resolution_events"][0][
        "event_key"
    ] == "jue_wiki_action_reference_resolution:kis:721"
    assert {
        event["event_key"]
        for event in resolution_events
    } == {"jue_wiki_action_reference_resolution:kis:721"}
    assert scorecard is not None
    assert scorecard["status"] == "resolved"
    assert scorecard["action"] == "observe"
    assert scorecard["resolution_status"] == "action_metadata"
    assert scorecard["latest_status"] == "referenced"
    assert status["jue_wiki_action_reference_recovery"] == {
        "status": "resolved",
        "memory_scope": "kis",
        "open_gap_count": 0,
        "resolved_count": 1,
        "total_count": 1,
        "recovery_ratio": 1.0,
        "latest_resolution_status": "action_metadata",
        "latest_status": "referenced",
    }
    assert pack["jue_wiki_action_reference_recovery"] == (
        status["jue_wiki_action_reference_recovery"]
    )
    assert (
        "jue_wiki_action_reference_gap.kis.unresolved_memory"
        not in item_ids
    )


def test_due_reflections_resolves_wiki_action_reference_memory_without_policy_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    unresolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 722,
                "run_at": "2026-07-04T11:10:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 0,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "unresolved"
                    ),
                    "jue_wiki_action_reference_status": "no_actions",
                    "jue_wiki_action_reference_count": 0,
                    "jue_wiki_action_reference_ratio": 0.0,
                    "blocker_tags": {
                        "unresolved_jue_wiki_action_reference_memory": 2,
                    },
                },
            },
        },
    }
    resolved_context = {
        "blocks": {
            "latest_manager_run": {
                "id": 723,
                "run_at": "2026-07-04T11:20:00+00:00",
                "status": "ok",
                "mode": "llm",
                "diagnostics": {
                    "action_count": 1,
                    "jue_wiki_action_reference_memory_status": "active",
                    "jue_wiki_action_reference_memory_resolution_status": (
                        "action_metadata"
                    ),
                    "jue_wiki_action_reference_status": "referenced",
                    "jue_wiki_action_reference_count": 1,
                    "jue_wiki_action_reference_ratio": 1.0,
                    "blocker_tags": {},
                },
            },
        },
    }

    service.run_due_reflections(context=unresolved_context)
    result = service.run_due_reflections(context=resolved_context)
    scorecard = service.repository.get_policy_scorecard(
        "jue_wiki_action_reference_gap.kis.unresolved_memory"
    )
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    status = service.status(scope="kis", compact=True)

    assert result["jue_wiki_action_reference_gap_event_count"] == 0
    assert result["jue_wiki_action_reference_resolution_event_count"] == 1
    assert scorecard is not None
    assert scorecard["status"] == "resolved"
    assert scorecard["resolution_status"] == "action_metadata"
    assert pack["jue_wiki_action_reference_memory"]["status"] == "missing"
    assert status["jue_wiki_action_reference_recovery"]["recovery_ratio"] == 1.0
    assert pack["jue_wiki_action_reference_recovery"]["recovery_ratio"] == 1.0


def test_context_pack_exposes_jue_wiki_selection_memory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 607,
                "run_at": "2026-07-04T06:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "jue_wiki_application": {
                        "status": "ok",
                        "selected_page_ids": ["kis.ops.manager_runs"],
                        "selection_audit": {
                            "selected_page_count": 2,
                            "reason_counts": {
                                "scope_match:kis": 2,
                                "operational_memory:manager_contract_recovery": 1,
                            },
                            "penalty_counts": {"freshness:stale": 1},
                            "top_pages": [
                                {
                                    "page_id": "kis.ops.manager_runs",
                                    "rank": 1,
                                    "selection_reasons": [
                                        "scope_match:kis",
                                        "operational_memory:manager_contract_recovery",
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
        }
    }

    service.run_due_reflections(context=context)
    pack = service.context_pack(target_scope="kis", max_chars=5000)

    selection_memory = pack["jue_wiki_selection_memory"]
    assert selection_memory["status"] == "available"
    assert selection_memory["target_scope"] == "kis"
    assert selection_memory["item_count"] == 1
    assert selection_memory["items"][0]["policy_id"] == (
        "jue_wiki_selection.kis.operational_memory_manager_contract_recovery"
    )
    assert selection_memory["items"][0]["primary_reason"] == (
        "operational_memory:manager_contract_recovery"
    )
    assert selection_memory["items"][0]["selected_page_ids"] == [
        "kis.ops.manager_runs"
    ]
    assert selection_memory["items"][0]["top_pages"][0]["page_id"] == (
        "kis.ops.manager_runs"
    )


def test_context_pack_keeps_jue_wiki_selection_memory_scoped_by_venue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 611,
                "run_at": "2026-07-04T08:30:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "jue_wiki_application": {
                        "status": "ok",
                        "selected_page_ids": ["kis.symbol.005930"],
                        "selection_audit": {
                            "selected_page_count": 1,
                            "reason_counts": {"scope_match:kis": 1},
                            "top_pages": [
                                {
                                    "page_id": "kis.symbol.005930",
                                    "rank": 1,
                                    "selection_reasons": ["scope_match:kis"],
                                }
                            ],
                        },
                    }
                },
            }
        },
        "binance_blocks": {
            "latest_manager_run": {
                "id": 612,
                "run_at": "2026-07-04T08:31:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "jue_wiki_application": {
                        "status": "ok",
                        "selected_page_ids": ["binance.symbol.BTCUSDT"],
                        "selection_audit": {
                            "selected_page_count": 1,
                            "reason_counts": {"scope_match:binance": 1},
                            "top_pages": [
                                {
                                    "page_id": "binance.symbol.BTCUSDT",
                                    "rank": 1,
                                    "selection_reasons": ["scope_match:binance"],
                                }
                            ],
                        },
                    }
                },
            }
        },
    }

    service.run_due_reflections(context=context)
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)
    kis_policy_ids = {
        row["policy_id"]
        for row in kis_pack["jue_wiki_selection_memory"].get("items", [])
    }
    binance_policy_ids = {
        row["policy_id"]
        for row in binance_pack["jue_wiki_selection_memory"].get("items", [])
    }
    kis_scorecard_ids = {row["policy_id"] for row in kis_pack["policy_scorecards"]}
    binance_scorecard_ids = {
        row["policy_id"] for row in binance_pack["policy_scorecards"]
    }

    assert kis_policy_ids == {"jue_wiki_selection.kis.scope_match_kis"}
    assert binance_policy_ids == {
        "jue_wiki_selection.binance.scope_match_binance"
    }
    assert "jue_wiki_selection.kis.scope_match_kis" in kis_scorecard_ids
    assert "jue_wiki_selection.binance.scope_match_binance" not in kis_scorecard_ids
    assert "jue_wiki_selection.binance.scope_match_binance" in binance_scorecard_ids
    assert "jue_wiki_selection.kis.scope_match_kis" not in binance_scorecard_ids


def test_compact_jue_wiki_selection_memory_filters_unscoped_rows_by_target() -> None:
    compact = _compact_jue_wiki_selection_memory(
        [
            {
                "source": "jue_wiki_selection_audit",
                "policy_id": "jue_wiki_selection.binance.scope_match_binance",
                "memory_scope": "binance",
                "transferability": "direct",
                "latest_reason": "scope_match:binance",
                "selected_page_ids": ["binance.symbol.BTCUSDT"],
            },
            {
                "source": "jue_wiki_selection_audit",
                "policy_id": "jue_wiki_selection.kis.scope_match_kis",
                "memory_scope": "kis",
                "transferability": "direct",
                "latest_reason": "scope_match:kis",
                "selected_page_ids": ["kis.symbol.005930"],
            },
        ],
        target_scope="kis",
        limit=4,
    )

    assert [row["policy_id"] for row in compact["items"]] == [
        "jue_wiki_selection.kis.scope_match_kis"
    ]
    assert compact["items"][0]["selected_page_ids"] == ["kis.symbol.005930"]


def test_context_pack_blocks_legacy_translated_jue_wiki_selection_cross_venue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    raw = {
        "policy_id": "jue_wiki_selection.kis.scope_match_kis",
        "source": "jue_wiki_selection_audit",
        "memory_scope": "kis",
        "transferability": "translated",
        "selected_page_ids": ["kis.symbol.005930"],
        "latest_reason": "scope_match:kis",
    }
    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO policy_scorecards (
                policy_id, memory_scope, transferability, action, status,
                sample_count, win_rate, avg_pnl_pct, expectancy_pct,
                rule_follow_rate, confidence, reason, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw["policy_id"],
                "kis",
                "translated",
                "caution",
                "active_caution",
                2,
                0.0,
                0.0,
                0.0,
                1.0,
                0.61,
                "legacy translated KIS wiki selection row",
                json.dumps(raw, ensure_ascii=False),
                "2026-07-04T09:00:00+00:00",
            ),
        )

    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)
    kis_selection_ids = {
        row["policy_id"]
        for row in kis_pack["jue_wiki_selection_memory"].get("items", [])
    }
    translated_ids = {
        row["policy_id"]
        for row in binance_pack["translated_policy_context"].get("items", [])
    }

    assert "jue_wiki_selection.kis.scope_match_kis" in kis_selection_ids
    assert "jue_wiki_selection.kis.scope_match_kis" not in translated_ids
    assert binance_pack["translated_policy_context"]["source_scope_counts"] == {}


def test_context_pack_blocks_legacy_translated_jue_wiki_selection_insight(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    evidence = [
        {
            "event_key": "jue_wiki_selection_audit:kis:legacy",
            "venue": "kis",
            "selected_page_ids": ["kis.symbol.005930"],
        }
    ]
    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_insights (
                memory_type, key, memory_scope, transferability, status,
                confidence, summary_md, evidence_json, source_run_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy_signal",
                "jue_wiki_selection.kis.scope_match_kis",
                "kis",
                "translated",
                "active",
                0.62,
                "legacy translated KIS wiki selection insight",
                json.dumps(evidence, ensure_ascii=False),
                None,
                "2026-07-04T09:10:00+00:00",
            ),
        )

    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)
    kis_active_keys = {row["key"] for row in kis_pack["active_insights"]}
    binance_active_keys = {row["key"] for row in binance_pack["active_insights"]}
    binance_scoped_translated_keys = {
        row["key"] for row in binance_pack["scoped_memory"].get("translated", [])
    }

    assert "jue_wiki_selection.kis.scope_match_kis" in kis_active_keys
    assert "jue_wiki_selection.kis.scope_match_kis" not in binance_active_keys
    assert (
        "jue_wiki_selection.kis.scope_match_kis"
        not in binance_scoped_translated_keys
    )


def test_repository_repairs_legacy_jue_wiki_selection_transferability_on_open(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    raw_scorecard = {
        "policy_id": "jue_wiki_selection.binance.scope_match_binance",
        "source": "jue_wiki_selection_audit",
        "memory_scope": "binance",
        "transferability": "translated",
        "selected_page_ids": ["binance.symbol.BTCUSDT"],
    }
    insight_evidence = [
        {
            "event_key": "jue_wiki_selection_audit:binance:legacy",
            "venue": "binance",
            "selected_page_ids": ["binance.symbol.BTCUSDT"],
        }
    ]
    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO policy_scorecards (
                policy_id, memory_scope, transferability, action, status,
                sample_count, win_rate, avg_pnl_pct, expectancy_pct,
                rule_follow_rate, confidence, reason, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_scorecard["policy_id"],
                "binance",
                "translated",
                "caution",
                "active_caution",
                2,
                0.0,
                0.0,
                0.0,
                1.0,
                0.61,
                "legacy translated Binance wiki selection row",
                json.dumps(raw_scorecard, ensure_ascii=False),
                "2026-07-04T09:20:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_insights (
                memory_type, key, memory_scope, transferability, status,
                confidence, summary_md, evidence_json, source_run_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy_signal",
                raw_scorecard["policy_id"],
                "binance",
                "translated",
                "active",
                0.62,
                "legacy translated Binance wiki selection insight",
                json.dumps(insight_evidence, ensure_ascii=False),
                None,
                "2026-07-04T09:21:00+00:00",
            ),
        )

    InvestmentMemoryRepository(service.config.db_path)

    with sqlite3.connect(service.config.db_path) as conn:
        scorecard_row = conn.execute(
            """
            SELECT memory_scope, transferability, raw_json
            FROM policy_scorecards
            WHERE policy_id = ?
            """,
            (raw_scorecard["policy_id"],),
        ).fetchone()
        insight_row = conn.execute(
            """
            SELECT memory_scope, transferability, evidence_json
            FROM memory_insights
            WHERE key = ?
            """,
            (raw_scorecard["policy_id"],),
        ).fetchone()

    scorecard_raw = json.loads(scorecard_row[2])
    repaired_evidence = json.loads(insight_row[2])
    assert scorecard_row[:2] == ("binance", "direct")
    assert scorecard_raw["memory_scope"] == "binance"
    assert scorecard_raw["transferability"] == "direct"
    assert scorecard_raw["scope_evidence"] == [
        {
            "memory_scope": "binance",
            "transferability": "direct",
            "source": "jue_wiki_selection_audit",
        }
    ]
    assert insight_row[:2] == ("binance", "direct")
    assert repaired_evidence[0]["memory_scope"] == "binance"
    assert repaired_evidence[0]["transferability"] == "direct"
    assert repaired_evidence[0]["source"] == "jue_wiki_selection_audit"


def test_repository_status_reports_jue_wiki_selection_provenance_health(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    policy_id = "jue_wiki_selection.kis.scope_match_kis"
    with sqlite3.connect(service.config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO policy_scorecards (
                policy_id, memory_scope, transferability, action, status,
                sample_count, win_rate, avg_pnl_pct, expectancy_pct,
                rule_follow_rate, confidence, reason, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_id,
                "kis",
                "translated",
                "caution",
                "active_caution",
                1,
                0.0,
                0.0,
                0.0,
                1.0,
                0.51,
                "dirty provenance row",
                json.dumps({"policy_id": policy_id}, ensure_ascii=False),
                "2026-07-04T09:30:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_insights (
                memory_type, key, memory_scope, transferability, status,
                confidence, summary_md, evidence_json, source_run_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy_signal",
                policy_id,
                "kis",
                "translated",
                "active",
                0.52,
                "dirty provenance insight",
                json.dumps([{"venue": "kis"}], ensure_ascii=False),
                None,
                "2026-07-04T09:31:00+00:00",
            ),
        )

    dirty_status = service.repository.status()["jue_wiki_selection_provenance"]
    repaired_status = InvestmentMemoryRepository(
        service.config.db_path
    ).status()["jue_wiki_selection_provenance"]

    assert dirty_status["status"] == "dirty"
    assert dirty_status["dirty_scorecard_count"] == 1
    assert dirty_status["dirty_insight_count"] == 1
    assert dirty_status["dirty_scorecard_policy_ids"] == [policy_id]
    assert dirty_status["dirty_insight_keys"] == [policy_id]
    assert repaired_status["status"] == "clean"
    assert repaired_status["dirty_scorecard_count"] == 0
    assert repaired_status["dirty_insight_count"] == 0
    assert repaired_status["dirty_scorecard_policy_ids"] == []
    assert repaired_status["dirty_insight_keys"] == []
    assert repaired_status["scorecard_count"] == 1
    assert repaired_status["insight_count"] == 1


def test_context_pack_turns_stale_jue_wiki_selection_into_repair_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "latest_manager_run": {
                "id": 608,
                "run_at": "2026-07-04T07:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "prompt": {
                    "jue_wiki_application": {
                        "status": "ok",
                        "selected_page_ids": ["kis.ops.manager_runs"],
                        "selection_audit": {
                            "selected_page_count": 2,
                            "reason_counts": {
                                "scope_match:kis": 2,
                                "operational_memory:manager_contract_recovery": 1,
                            },
                            "penalty_counts": {"freshness:stale": 2},
                            "top_pages": [
                                {
                                    "page_id": "kis.ops.manager_runs",
                                    "rank": 1,
                                    "selection_reasons": [
                                        "scope_match:kis",
                                        "operational_memory:manager_contract_recovery",
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
        }
    }

    service.run_due_reflections(context=context)
    pack = service.context_pack(target_scope="kis", max_chars=12000)
    policy_id = "jue_wiki_selection.kis.operational_memory_manager_contract_recovery"
    scorecard = service.repository.get_policy_scorecard(policy_id)
    backlog = {
        row["policy_id"]: row
        for row in pack["validation_repair_backlog"].get("items", [])
    }
    constraints = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"].get("items", [])
    }
    selection_memory = {
        row["policy_id"]: row
        for row in pack["jue_wiki_selection_memory"].get("items", [])
    }

    assert scorecard is not None
    assert scorecard["status"] == "active_caution"
    assert selection_memory[policy_id]["application_guidance"] == {
        "status": "freshness_repair_required",
        "manager_instruction": (
            "refresh_or_cross_check_selected_wiki_before_size_increase"
        ),
        "required_evidence": [
            "fresh_jue_wiki_context",
            "selection_audit_resolution",
            "live_cross_check",
        ],
        "cross_check_page_ids": ["kis.ops.manager_runs"],
    }
    assert backlog[policy_id]["discipline_id"] == "wiki_freshness"
    assert backlog[policy_id]["repair_action_id"] == (
        "jue_wiki_refresh.kis.operational_memory_manager_contract_recovery"
    )
    assert backlog[policy_id]["required_checks"] == ["require_fresh_wiki_context"]
    assert backlog[policy_id]["required_evidence"] == [
        "fresh_jue_wiki_context",
        "selection_audit_resolution",
        "live_cross_check",
    ]
    assert backlog[policy_id]["selected_page_ids"] == ["kis.ops.manager_runs"]
    assert constraints[policy_id]["discipline_id"] == "wiki_freshness"
    assert constraints[policy_id]["entry_bias"] == (
        "fresh_wiki_cross_checked_probe_or_wait"
    )
    assert constraints[policy_id]["sizing_policy"] == (
        "no_size_increase_until_wiki_freshness_repaired"
    )
    assert "require_fresh_wiki_context" in constraints[policy_id]["required_checks"]


def test_context_pack_turns_manager_contract_error_scorecard_into_repair_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": (
                "manager_contract_error.kis."
                "research_spine_memory_resolution_missing_from_model"
            ),
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "expectancy_pct": 0.0,
            "rule_follow_rate": 0.0,
            "confidence": 0.72,
            "reason": "KIS manager repeatedly ignored research spine memory.",
            "source": "manager_contract_error",
            "venue": "kis",
            "memory_scope": "kis",
            "transferability": "direct",
            "contract": "cite_or_reject_research_spine_memory",
            "latest_error": "research_spine_memory_resolution_missing_from_model",
            "impacted_symbols": ["005930"],
            "memory_contract_rows": [
                {
                    "symbol": "005930",
                    "status": "unresolved",
                    "contracts": ["cite_or_reject_research_spine_memory"],
                    "errors": [
                        "research_spine_memory_resolution_missing_from_model"
                    ],
                    "resolution_modes": [],
                }
            ],
        }
    )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        max_chars=12000,
    )

    backlog = {
        row["policy_id"]: row
        for row in pack["validation_repair_backlog"].get("items", [])
    }
    constraints = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"].get("items", [])
    }
    policy_id = (
        "manager_contract_error.kis."
        "research_spine_memory_resolution_missing_from_model"
    )

    assert backlog[policy_id]["discipline_id"] == "memory_contract"
    assert backlog[policy_id]["repair_action_id"] == (
        "memory_contract_repair.kis."
        "research_spine_memory_resolution_missing_from_model"
    )
    assert backlog[policy_id]["required_checks"] == [
        "require_memory_contract_resolution"
    ]
    assert backlog[policy_id]["blocks_new_entries"] == (
        "cite_or_reject_memory_before_new_entries"
    )
    assert backlog[policy_id]["memory_contract_rows"] == [
        {
            "symbol": "005930",
            "status": "unresolved",
            "contracts": ["cite_or_reject_research_spine_memory"],
            "errors": ["research_spine_memory_resolution_missing_from_model"],
            "resolution_modes": [],
        }
    ]
    assert constraints[policy_id]["discipline_id"] == "memory_contract"
    assert constraints[policy_id]["entry_bias"] == (
        "memory_contract_resolved_probe_or_wait"
    )
    assert constraints[policy_id]["sizing_policy"] == (
        "no_size_increase_until_memory_contract_repaired"
    )
    assert constraints[policy_id]["required_checks"] == [
        "require_memory_contract_resolution"
    ]
    assert constraints[policy_id]["memory_contract_rows"] == [
        {
            "symbol": "005930",
            "status": "unresolved",
            "contracts": ["cite_or_reject_research_spine_memory"],
            "errors": ["research_spine_memory_resolution_missing_from_model"],
            "resolution_modes": [],
        }
    ]


def test_context_pack_marks_manager_contract_error_recovered_after_resolution_scorecard(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    error_policy_id = (
        "manager_contract_error.kis."
        "research_spine_memory_resolution_missing_from_model"
    )
    resolution_policy_id = (
        "manager_contract_resolution.kis."
        "research_spine_memory_resolution_missing_from_model"
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": error_policy_id,
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "expectancy_pct": 0.0,
            "rule_follow_rate": 0.0,
            "confidence": 0.72,
            "reason": "KIS manager repeatedly ignored research spine memory.",
            "source": "manager_contract_error",
            "venue": "kis",
            "memory_scope": "kis",
            "transferability": "direct",
            "contract": "cite_or_reject_research_spine_memory",
            "latest_error": "research_spine_memory_resolution_missing_from_model",
            "impacted_symbols": ["005930"],
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": resolution_policy_id,
            "status": "active_observation",
            "action": "observe",
            "sample_count": 1,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "expectancy_pct": 0.0,
            "rule_follow_rate": 1.0,
            "confidence": 0.5,
            "reason": "KIS manager recorded a memory contract resolution.",
            "source": "manager_contract_resolution",
            "venue": "kis",
            "memory_scope": "kis",
            "transferability": "direct",
            "contract": "cite_or_reject_research_spine_memory",
            "memory_contract_errors": [
                "research_spine_memory_resolution_missing_from_model"
            ],
            "resolution_status": "resolved",
            "latest_resolution": (
                "reject_memory_with_reason: 위키 기억을 확인했지만 현재 수급이 약해 대기한다."
            ),
            "impacted_symbols": ["005930"],
        }
    )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        max_chars=12000,
    )

    backlog = {
        row["policy_id"]: row
        for row in pack["validation_repair_backlog"].get("items", [])
    }
    constraints = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"].get("items", [])
    }

    assert error_policy_id not in backlog
    assert error_policy_id not in constraints
    assert pack["validation_recovery_summary"]["manager_contract_recovered"] == [
        {
            "policy_id": error_policy_id,
            "resolution_policy_id": resolution_policy_id,
            "contract": "cite_or_reject_research_spine_memory",
            "error": "research_spine_memory_resolution_missing_from_model",
            "impacted_symbols": ["005930"],
            "latest_resolution": (
                "reject_memory_with_reason: 위키 기억을 확인했지만 현재 수급이 약해 대기한다."
            ),
        }
    ]


def test_due_reflections_preserve_live_authority_validation_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": "blk_023810_capacity_closed",
                    "symbol": "023810",
                    "name": "인팩",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 50000,
                    "current_price": 48000,
                    "target_price": 56000,
                    "stop_price": 47000,
                    "thesis": "검증 게이트가 용량 병목을 지적한 축소 블록",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "live_authority": {
                            "validation_gate_status": "blocked_by_validation",
                            "validation_readiness": "blocked_by_validation",
                            "validation_gate_reason": (
                                "readiness=blocked_by_validation, fail_count=3"
                            ),
                            "validation_passport": {
                                "version": "trading_validation_passport_v1",
                                "status": "blocked_by_validation",
                                "readiness": "blocked_by_validation",
                                "score": 42.5,
                                "expected_count": 19,
                                "actual_count": 3,
                                "is_complete": False,
                                "pass_count": 1,
                                "warn_count": 0,
                                "fail_count": 2,
                                "missing_count": 0,
                                "failed_ids": [
                                    "capacity_analysis",
                                    "monte_carlo",
                                ],
                                "weak_ids": [
                                    "capacity_analysis",
                                    "monte_carlo",
                                ],
                                "requires_revalidation": True,
                                "risk_governor_action": "halt_new_risk",
                            },
                            "validation_pressure": {
                                "version": "validation_pressure_v1",
                                "severity": "blocked",
                                "entry_posture": "no_new_entry",
                                "sizing_posture": "halt_new_risk",
                                "risk_governor_action": "halt_new_risk",
                                "scale_up_allowed": False,
                                "fail_ids": [
                                    "capacity_analysis",
                                    "monte_carlo",
                                ],
                                "warn_ids": ["cost_simulation"],
                                "missing_ids": ["walk_forward_analysis"],
                                "block_design_requirements": [
                                    "cap_concentration_and_confirm_capacity_before_scaling",
                                    "use_fractional_kelly_with_drawdown_and_ruin_caps",
                                ],
                                "discipline_actions": [
                                    {
                                        "id": "capacity_analysis",
                                        "status": "fail",
                                        "entry_constraint": (
                                            "entry_qty_must_fit_depth_turnover_and_exit_capacity"
                                        ),
                                        "sizing_constraint": (
                                            "cap_qty_to_practical_capacity_until_verified"
                                        ),
                                        "repair_action": (
                                            "attach_orderbook_depth_or_turnover_capacity_evidence"
                                        ),
                                        "block_design_focus": (
                                            "liquid_symbols_and_exit_capacity_before_size"
                                        ),
                                    },
                                    {
                                        "id": "monte_carlo",
                                        "status": "fail",
                                        "entry_constraint": (
                                            "patient_entry_only_when_sequence_risk_is_reduced"
                                        ),
                                        "sizing_constraint": (
                                            "fractional_small_until_loss_streak_risk_repairs"
                                        ),
                                        "repair_action": (
                                            "reduce_loss_clustering_and_retest_sequence_tail_risk"
                                        ),
                                    },
                                ],
                            },
                            "discipline_matrix": {
                                "expected_count": 19,
                                "actual_count": 3,
                                "summary": {
                                    "readiness": "blocked_by_validation",
                                    "pass_count": 1,
                                    "warn_count": 0,
                                    "fail_count": 2,
                                    "missing_count": 0,
                                },
                                "statuses": [
                                    {
                                        "id": "data_validation",
                                        "label": "데이터 검증",
                                        "status": "pass",
                                    },
                                    {
                                        "id": "capacity_analysis",
                                        "label": "용량 분석",
                                        "status": "fail",
                                    },
                                    {
                                        "id": "monte_carlo",
                                        "label": "몬테카를로 시뮬레이션",
                                        "status": "fail",
                                    },
                                ],
                            },
                            "failed_disciplines": [
                                {
                                    "id": "capacity_analysis",
                                    "label": "용량 분석",
                                    "status": "fail",
                                },
                                {
                                    "id": "monte_carlo",
                                    "label": "몬테카를로 시뮬레이션",
                                    "status": "fail",
                                },
                            ],
                            "capacity_bottleneck": {
                                "tightest_symbol": "023810",
                                "min_capacity_ratio": 0.79563,
                                "capacity_method": "metadata_capacity_ratio",
                            },
                            "failure_attribution": {
                                "recovery_focus": [
                                    "symbol=023810 net -120000.00, PF 0.00, expectancy -4.00%"
                                ],
                                "worst_groups": [
                                    {
                                        "group_type": "symbol",
                                        "group": "023810",
                                        "risk_score": 55.0,
                                    }
                                ],
                            },
                            "operator_guidance": [
                                "용량 분석: 023810 체결 크기 축소",
                            ],
                            "remediation_plan": {
                                "status": "blocked",
                                "primary_next_action": "KIS capacity/OOS 복구",
                                "weak_count": 4,
                                "failed_count": 2,
                                "categories": [
                                    {
                                        "id": "immediate_ops_controls",
                                        "label": "운영 즉시조치",
                                        "weak_count": 1,
                                        "fail_count": 1,
                                        "items": [
                                            {
                                                "discipline_id": "capacity_analysis",
                                                "label": "용량 분석",
                                                "status": "fail",
                                                "action": "용량 병목 종목 sizing 축소",
                                            }
                                        ],
                                    }
                                ],
                            },
                        },
                        "validation_repair_enforcement": {
                            "version": "validation_repair_enforcement_v1",
                            "repair_action_ids": [
                                "validation_repair.depth_capacity.capacity_analysis"
                            ],
                            "scale_up_blocked": True,
                            "waiting_entry_required": True,
                            "budget_multiplier": 0.5,
                            "last_repair_statuses": ["queued_capacity_repair"],
                            "adjustments": [
                                {
                                    "field": "qty",
                                    "from": 3,
                                    "to": 1,
                                    "reason": "validation_repair_scale_up_blocked_probe_only",
                                },
                                {
                                    "field": "entry_style",
                                    "from": "aggressive_limit",
                                    "to": "wait_for_price",
                                    "entry_trigger_price": 50000,
                                    "entry_trigger_operator": "lte",
                                    "reason": "validation_repair_requires_waiting_entry",
                                },
                            ],
                        },
                        "lane_authority_gate": {
                            "source": "lane_authority",
                            "matched_lane": "mid:value_pullback",
                            "action": "validation_evidence_repair_waiting_probe",
                            "reason": "lane_authority_waiting_entry_required",
                            "grade": "C",
                            "scale_decision": "capped_until_repairs",
                            "scale_up_allowed": False,
                            "requires_waiting_entry": True,
                            "applied_max_budget_multiplier": 0.5,
                            "max_budget_multiplier": 0.5,
                            "scale_blockers": [
                                "cost_evidence_repair",
                                "entry_quality_repair",
                            ],
                            "scale_repair_targets": [
                                "record_missing_cost_component:spread",
                                "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
                            ],
                            "cost_repair_targets": [
                                "record_missing_cost_component:spread",
                            ],
                            "entry_repair_targets": [
                                "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
                            ],
                            "risk_budget_passport": {
                                "lane_key": "kis:mid:value_pullback",
                                "status": "capped",
                                "scale_decision": "capped_until_repairs",
                                "scale_up_allowed": False,
                                "applied_risk_budget_multiplier": 0.5,
                                "scale_blockers": [
                                    "cost_evidence_repair",
                                    "entry_quality_repair",
                                ],
                                "scale_repair_targets": [
                                    "record_missing_cost_component:spread",
                                    "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
                                ],
                            },
                        },
                    },
                }
            ],
            "orders": [
                {
                    "block_id": "blk_023810_capacity_closed",
                    "side": "sell",
                    "limit_price": 48000,
                    "reason": "manual_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory("blk_023810_capacity_closed")["reflection"]

    assert result["status"] == "ok"
    assert reflection["metrics"]["live_authority"]["validation_gate_status"] == (
        "blocked_by_validation"
    )
    assert reflection["metrics"]["live_authority"]["validation_passport"] == {
        "version": "trading_validation_passport_v1",
        "status": "blocked_by_validation",
        "readiness": "blocked_by_validation",
        "score": 42.5,
        "expected_count": 19,
        "actual_count": 3,
        "is_complete": False,
        "pass_count": 1,
        "warn_count": 0,
        "fail_count": 2,
        "missing_count": 0,
        "failed_ids": ["capacity_analysis", "monte_carlo"],
        "weak_ids": ["capacity_analysis", "monte_carlo"],
        "requires_revalidation": True,
        "risk_governor_action": "halt_new_risk",
    }
    pressure = reflection["metrics"]["live_authority"]["validation_pressure"]
    assert pressure["severity"] == "blocked"
    assert pressure["entry_posture"] == "no_new_entry"
    assert pressure["sizing_posture"] == "halt_new_risk"
    assert pressure["fail_ids"] == ["capacity_analysis", "monte_carlo"]
    assert pressure["warn_ids"] == ["cost_simulation"]
    assert pressure["missing_ids"] == ["walk_forward_analysis"]
    assert pressure["discipline_actions"][0] == {
        "id": "capacity_analysis",
        "status": "fail",
        "entry_constraint": "entry_qty_must_fit_depth_turnover_and_exit_capacity",
        "sizing_constraint": "cap_qty_to_practical_capacity_until_verified",
        "repair_action": "attach_orderbook_depth_or_turnover_capacity_evidence",
        "block_design_focus": "liquid_symbols_and_exit_capacity_before_size",
    }
    assert reflection["metrics"]["live_authority"]["discipline_matrix"][
        "expected_count"
    ] == 19
    assert reflection["metrics"]["live_authority"]["discipline_matrix"]["summary"][
        "fail_count"
    ] == 2
    assert reflection["metrics"]["live_authority"]["failed_disciplines"][0]["id"] == (
        "capacity_analysis"
    )
    assert reflection["metrics"]["live_authority"]["capacity_bottleneck"][
        "tightest_symbol"
    ] == "023810"
    assert reflection["metrics"]["live_authority"]["failure_attribution"][
        "worst_groups"
    ][0]["group"] == "023810"
    assert reflection["metrics"]["live_authority"]["remediation_plan"][
        "primary_next_action"
    ] == "KIS capacity/OOS 복구"
    enforcement = reflection["metrics"]["validation_repair_enforcement"]
    assert enforcement["scale_up_blocked"] is True
    assert enforcement["waiting_entry_required"] is True
    assert enforcement["repair_action_ids"] == [
        "validation_repair.depth_capacity.capacity_analysis"
    ]
    assert enforcement["adjustments"][0]["field"] == "qty"
    lane_gate = reflection["metrics"]["lane_authority_gate"]
    assert lane_gate["scale_decision"] == "capped_until_repairs"
    assert lane_gate["scale_up_allowed"] is False
    assert lane_gate["scale_blockers"] == [
        "cost_evidence_repair",
        "entry_quality_repair",
    ]
    assert lane_gate["scale_repair_targets"] == [
        "record_missing_cost_component:spread",
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
    ]
    assert lane_gate["risk_budget_passport"]["scale_blockers"] == [
        "cost_evidence_repair",
        "entry_quality_repair",
    ]
    assert "용량 분석" in reflection["lesson_md"]
    assert "19검증" in reflection["lesson_md"]
    assert "검증 여권" in reflection["lesson_md"]
    assert "검증 압력" in reflection["lesson_md"]
    assert "entry_qty_must_fit_depth_turnover_and_exit_capacity" in reflection[
        "lesson_md"
    ]
    assert "재검증" in reflection["lesson_md"]
    assert "42.5" in reflection["lesson_md"]
    assert "실패 귀속" in reflection["lesson_md"]
    assert "검증 수리 강제" in reflection["lesson_md"]
    assert "대기진입 요구" in reflection["lesson_md"]
    assert "Lane 권한" in reflection["lesson_md"]
    assert "cost_evidence_repair" in reflection["lesson_md"]
    assert "023810" in reflection["lesson_md"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=30)["items"]
    }
    assert scorecards["lane_scale.cost_evidence_repair"]["source"] == (
        "lane_authority_scale_blocker"
    )
    assert scorecards["lane_scale.cost_evidence_repair"]["scale_blocker"] == (
        "cost_evidence_repair"
    )
    assert scorecards["lane_scale.cost_evidence_repair"]["lane_scale_evidence"][
        "scale_repair_target_counts"
    ]["record_missing_cost_component:spread"] == 1


def test_due_reflections_preserve_validation_recovery_focus_and_build_soft_policy(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_eth_validation_recovery_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "ETHUSDT",
                "name": "Ethereum",
                "status": "closed",
                "qty_initial": 0.05,
                "entry_price": 3500,
                "current_price": 3475,
                "target_price": 3650,
                "stop_price": 3420,
                "thesis": "패턴랩 검증 증거가 복구 대상인 상태에서 생성된 블록",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "live_authority": {
                        "validation_gate_status": "blocked_by_validation",
                        "validation_readiness": "blocked_by_validation",
                        "validation_recovery_focus": [
                            {
                                "source": "pattern_lab",
                                "reason": "active_walk_forward_windows_missing",
                                "action": "Re-run rolling WFA windows for active optimized sets.",
                                "active_set_count": 2,
                                "active_wfa_coverage_rate_pct": 0.0,
                            }
                        ],
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 3475,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    reflection = service.block_memory("blk_eth_validation_recovery_0")["reflection"]
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }

    policy_id = "validation_recovery.pattern_lab.active_walk_forward_windows_missing"
    assert result["created_count"] == 3
    assert reflection["metrics"]["live_authority"]["validation_recovery_focus"][0][
        "reason"
    ] == "active_walk_forward_windows_missing"
    assert "검증 복구" in reflection["lesson_md"]
    assert "active_walk_forward_windows_missing" in reflection["lesson_md"]
    assert scorecards[policy_id]["status"] == "active_caution"
    assert scorecards[policy_id]["source"] == "live_authority_validation_recovery"
    assert active_rules[policy_id]["condition"]["validation_recovery_focus"] == {
        "source": "pattern_lab",
        "reason": "active_walk_forward_windows_missing",
    }
    assert active_rules[policy_id]["effect"]["hard_filter"] is False


def test_due_reflections_build_policy_from_validation_repair_enforcement(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_repair_enforcement_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "BTCUSDT",
                "name": "Bitcoin",
                "status": "closed",
                "qty_initial": 0.02,
                "entry_price": 100.0,
                "current_price": 99.0,
                "target_price": 106.0,
                "stop_price": 97.0,
                "thesis": "비용 검증 수리 중이라 대기진입과 소액 probe로 축소된 블록",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "validation_repair_enforcement": {
                        "version": "validation_repair_enforcement_v1",
                        "repair_action_ids": [
                            "validation_repair.cost_evidence_repair.cost_simulation"
                        ],
                        "scale_up_blocked": True,
                        "waiting_entry_required": True,
                        "budget_multiplier": 0.25,
                        "last_repair_statuses": ["queued_cost_repair"],
                        "adjustments": [
                            {
                                "field": "quote_budget_usdt",
                                "from": 400.0,
                                "to": 100.0,
                                "reason": (
                                    "validation_repair_scale_up_blocked_probe_budget"
                                ),
                            },
                            {
                                "field": "entry_style",
                                "from": "immediate",
                                "to": "wait_for_price",
                                "entry_trigger_price": 100.0,
                                "entry_trigger_operator": "<=",
                                "reason": "validation_repair_requires_waiting_entry",
                            },
                        ],
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 99.0,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    reflection = service.block_memory("blk_repair_enforcement_0")["reflection"]
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=30)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }
    policy_id = (
        "validation_repair_enforcement."
        "validation_repair_cost_evidence_repair_cost_simulation"
    )

    assert result["created_count"] == 3
    assert reflection["metrics"]["validation_repair_enforcement"][
        "budget_multiplier"
    ] == pytest.approx(0.25)
    assert "검증 수리 강제" in reflection["lesson_md"]
    assert scorecards[policy_id]["status"] == "active_caution"
    assert scorecards[policy_id]["source"] == "validation_repair_enforcement"
    assert active_rules[policy_id]["effect"]["hard_filter"] is False
    assert active_rules[policy_id]["effect"]["entry_bias"] == (
        "respect_repair_waiting_or_probe_mode"
    )
    assert active_rules[policy_id]["effect"]["sizing_policy"] == (
        "keep_probe_until_repair_passes"
    )


def test_due_reflections_build_lane_policy_from_weak_lane_sources(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_entry_quality_source_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "BTCUSDT",
                "name": "Bitcoin",
                "status": "closed",
                "qty_initial": 0.02,
                "entry_price": 100.0,
                "current_price": 98.0,
                "target_price": 108.0,
                "stop_price": 96.0,
                "thesis": "진입 품질 weak source 때문에 대기진입으로 축소된 블록",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "lane_authority_gate": {
                        "source": "lane_authority",
                        "matched_lane": "spot:long:late_chase",
                        "action": "entry_quality_repair_waiting_entry",
                        "grade": "qualified",
                        "scale_decision": "capped_until_repairs",
                        "scale_up_allowed": False,
                        "requires_waiting_entry": True,
                        "applied_max_budget_multiplier": 0.5,
                        "weak_lane_sources": ["entry_quality_weak_lanes"],
                        "entry_repair_targets": [
                            "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
                            "require_entry_quality_score_above_60_before_size_increase",
                        ],
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 98.0,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(
        context={"binance_blocks": {"blocks": blocks, "orders": orders}}
    )
    reflection = service.block_memory("blk_entry_quality_source_0")["reflection"]
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=30)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }
    policy_id = "lane_scale.entry_quality_repair"

    assert result["created_count"] == 3
    assert reflection["metrics"]["lane_authority_gate"]["weak_lane_sources"] == [
        "entry_quality_weak_lanes"
    ]
    assert scorecards[policy_id]["status"] == "active_caution"
    assert scorecards[policy_id]["sample_count"] == 3
    assert scorecards[policy_id]["scale_blocker"] == "entry_quality_repair"
    assert scorecards[policy_id]["lane_scale_evidence"][
        "weak_lane_source_counts"
    ] == {"entry_quality_weak_lanes": 3}
    assert scorecards[policy_id]["lane_scale_evidence"][
        "scale_blocker_counts"
    ] == {"entry_quality_repair": 3}
    assert active_rules[policy_id]["effect"]["hard_filter"] is False
    assert active_rules[policy_id]["effect"]["entry_bias"] == (
        "pullback_reclaim_or_value_waiting_entry"
    )
    assert active_rules[policy_id]["effect"]["sizing_policy"] == (
        "probe_only_until_entry_quality_repairs"
    )


def test_validation_failures_build_discipline_policy_scorecards(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_023810_validation_fail_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "023810",
                "name": "인팩",
                "status": "closed",
                "qty_initial": 1,
                "entry_price": 50000,
                "current_price": 48500,
                "target_price": 56000,
                "stop_price": 47000,
                "thesis": "검증 실패 누적 테스트",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "live_authority": {
                        "validation_gate_status": "blocked_by_validation",
                        "validation_readiness": "blocked_by_validation",
                        "validation_gate_reason": "readiness=blocked_by_validation, fail_count=2",
                        "validation_pressure": {
                            "version": "validation_pressure_v1",
                            "severity": "de_risk",
                            "entry_posture": "waiting_entry_required",
                            "sizing_posture": "probe_or_cap",
                            "risk_governor_action": "reduce_new_risk",
                            "scale_up_allowed": False,
                            "fail_ids": ["capacity_analysis", "monte_carlo"],
                            "block_design_requirements": [
                                "cap_concentration_and_confirm_capacity_before_scaling",
                                "use_fractional_kelly_with_drawdown_and_ruin_caps",
                            ],
                            "discipline_actions": [
                                {
                                    "id": "capacity_analysis",
                                    "status": "fail",
                                    "entry_constraint": (
                                        "entry_qty_must_fit_depth_turnover_and_exit_capacity"
                                    ),
                                    "sizing_constraint": (
                                        "cap_qty_to_practical_capacity_until_verified"
                                    ),
                                    "repair_action": (
                                        "attach_orderbook_depth_or_turnover_capacity_evidence"
                                    ),
                                    "block_design_focus": (
                                        "liquid_symbols_and_exit_capacity_before_size"
                                    ),
                                },
                                {
                                    "id": "monte_carlo",
                                    "status": "fail",
                                    "entry_constraint": (
                                        "patient_entry_only_when_sequence_risk_is_reduced"
                                    ),
                                    "sizing_constraint": (
                                        "fractional_small_until_loss_streak_risk_repairs"
                                    ),
                                    "repair_action": (
                                        "reduce_loss_clustering_and_retest_sequence_tail_risk"
                                    ),
                                    "block_design_focus": (
                                        "loss_streak_tail_risk_before_size"
                                    ),
                                },
                            ],
                        },
                        "failed_disciplines": [
                            {
                                "id": "capacity_analysis",
                                "label": "용량 분석",
                                "status": "fail",
                            },
                            {
                                "id": "monte_carlo",
                                "label": "몬테카를로 시뮬레이션",
                                "status": "fail",
                            },
                        ],
                        "capacity_bottleneck": {
                            "tightest_symbol": "023810",
                            "min_capacity_ratio": 0.79,
                            "capacity_method": "metadata_capacity_ratio",
                        },
                        "failure_attribution": {
                            "recovery_focus": [
                                "strategy_family=late_chase net -9000.00, PF 0.20"
                            ],
                            "worst_groups": [
                                {
                                    "group_type": "strategy_family",
                                    "group": "late_chase",
                                    "risk_score": 58.0,
                                }
                            ],
                        },
                        "operator_guidance": [
                            "용량 분석: 023810 체결 크기 축소",
                            "몬테카를로: 연속 손실 위험 축소",
                        ],
                    }
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 48500,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }

    assert result["created_count"] == 3
    assert scorecards["validation.capacity_analysis"]["status"] == "active_caution"
    assert scorecards["validation.capacity_analysis"]["sample_count"] == 3
    assert scorecards["validation.capacity_analysis"]["discipline_id"] == "capacity_analysis"
    assert scorecards["validation.capacity_analysis"]["validation_pressure_action"][
        "entry_constraint"
    ] == "entry_qty_must_fit_depth_turnover_and_exit_capacity"
    assert scorecards["validation.capacity_analysis"]["validation_pressure_action"][
        "sizing_constraint"
    ] == "cap_qty_to_practical_capacity_until_verified"
    assert scorecards["validation.capacity_analysis"]["validation_pressure_action"][
        "repair_action"
    ] == "attach_orderbook_depth_or_turnover_capacity_evidence"
    assert scorecards["validation.capacity_analysis"]["validation_pressure_evidence"][
        "entry_constraint_counts"
    ] == {"entry_qty_must_fit_depth_turnover_and_exit_capacity": 3}
    assert scorecards["validation.monte_carlo"]["status"] == "active_caution"
    assert scorecards["validation_attribution.strategy_family.late_chase"][
        "status"
    ] == "active_caution"
    assert scorecards["validation_attribution.strategy_family.late_chase"][
        "attribution_group_type"
    ] == "strategy_family"
    assert scorecards["validation_attribution.strategy_family.late_chase"][
        "attribution_group"
    ] == "late_chase"
    assert active_rules["validation.capacity_analysis"]["condition"][
        "live_authority_failed_discipline"
    ] == "capacity_analysis"
    assert active_rules["validation_attribution.strategy_family.late_chase"][
        "condition"
    ]["live_authority_failure_attribution"] == {
        "group_type": "strategy_family",
        "group": "late_chase",
    }
    assert active_rules["validation_attribution.strategy_family.late_chase"][
        "effect"
    ]["hard_filter"] is False
    capacity_effect = active_rules["validation.capacity_analysis"]["effect"]
    assert capacity_effect["entry_bias"] == "depth_checked_waiting_entry"
    assert capacity_effect["require_capacity_check"] is True
    assert capacity_effect["sizing_policy"] == "cap_by_capacity_until_depth_verified"
    assert capacity_effect["validation_pressure_entry_constraint"] == (
        "entry_qty_must_fit_depth_turnover_and_exit_capacity"
    )
    assert capacity_effect["validation_pressure_sizing_constraint"] == (
        "cap_qty_to_practical_capacity_until_verified"
    )
    assert capacity_effect["validation_pressure_repair_action"] == (
        "attach_orderbook_depth_or_turnover_capacity_evidence"
    )
    assert capacity_effect["validation_pressure_block_design_focus"] == (
        "liquid_symbols_and_exit_capacity_before_size"
    )
    pack = service.context_pack(max_chars=9000)
    constraints = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"]["items"]
    }
    assert constraints["validation.capacity_analysis"][
        "validation_pressure_entry_constraint"
    ] == "entry_qty_must_fit_depth_turnover_and_exit_capacity"
    assert constraints["validation.capacity_analysis"][
        "validation_pressure_repair_action"
    ] == "attach_orderbook_depth_or_turnover_capacity_evidence"


def test_negative_validation_attribution_does_not_become_preference(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(5):
        block_id = f"blk_negative_late_chase_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "WLDUSDT",
                "name": "WLDUSDT",
                "status": "closed",
                "qty_initial": 10,
                "entry_price": 1.0,
                "current_price": 0.96,
                "target_price": 1.08,
                "stop_price": 0.95,
                "thesis": "반복 손실 late chase 테스트",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "live_authority": {
                        "validation_gate_status": "blocked_by_validation",
                        "failure_attribution": {
                            "worst_groups": [
                                {
                                    "group_type": "strategy_family",
                                    "group": "late_chase",
                                    "risk_score": 71.0,
                                }
                            ]
                        },
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 0.96,
                "reason": "stop_reached",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    service.run_due_reflections(context={"binance_blocks": {"blocks": blocks, "orders": orders}})
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }

    policy_id = "validation_attribution.strategy_family.late_chase"
    assert scorecards[policy_id]["avg_pnl_pct"] < 0
    assert scorecards[policy_id]["status"] == "active_caution"
    assert scorecards[policy_id]["action"] == "caution"
    assert rules[policy_id]["action"] == "caution"


def test_trading_validation_signals_build_venue_scoped_policy_scorecards(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def payload(run_id: str, venue: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": run_id,
            "venue": venue,
            "computed_at": f"2026-06-17T00:0{run_id[-1]}:00+00:00",
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "fail",
                    "action": "실제 수수료/슬리피지 근거를 보강한다.",
                },
                {
                    "id": "profit_factor",
                    "label": "수익팩터",
                    "status": "pass",
                },
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 1,
                "fail_count": 1,
                "warn_count": 0,
                "missing_count": 0,
            },
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": "cost_simulation",
                        "lane_policy_hint": "cost_verified_waiting_entry",
                        "blocks_scaling": "reduce_cost_weak_lanes",
                        "pass_path": {
                            "version": "validation_pass_path_v1",
                            "current_gap": "evidence_failed_threshold",
                            "collection_hook": "sync_live_performance_and_edges",
                            "collection_cadence": "before_next_manager_run",
                            "pass_criteria": (
                                "cost_simulation returns to pass with recorded "
                                "fee/spread/slippage evidence"
                            ),
                            "required_evidence": {
                                "min_recorded_cost_coverage_pct": 60.0,
                                "required_cost_components": [
                                    "fees",
                                    "spread",
                                    "slippage",
                                    "tax_or_funding",
                                ],
                            },
                            "jue_behavior_until_pass": {
                                "allowed_entry_posture": "cost_verified_waiting_entry",
                                "blocks_scaling": "reduce_cost_weak_lanes",
                                "blocks_new_entries": "cost_weak_immediate_entries",
                                "scale_up_blocked": True,
                            },
                            "m1_runtime_profile": {
                                "execution_weight": "lightweight",
                                "prefer_incremental_refresh": True,
                            },
                        },
                    }
                ]
            },
        }

    first = service.ingest_trading_validation_signals(
        venue="binance",
        validation=payload("validation-binance-1", "binance"),
    )
    duplicate = service.ingest_trading_validation_signals(
        venue="binance",
        validation=payload("validation-binance-1", "binance"),
    )
    service.ingest_trading_validation_signals(
        venue="binance",
        validation=payload("validation-binance-2", "binance"),
    )
    third = service.ingest_trading_validation_signals(
        venue="binance",
        validation=payload("validation-binance-3", "binance"),
    )
    service.ingest_trading_validation_signals(
        venue="kis",
        validation=payload("validation-kis-1", "kis"),
    )

    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }

    assert first["processed_count"] == 1
    assert duplicate["status"] == "skipped"
    assert duplicate["reason"] == "validation_run_already_ingested"
    assert third["processed_count"] == 1
    assert scorecards["validation.binance.cost_simulation"]["sample_count"] == 3
    assert scorecards["validation.binance.cost_simulation"]["status"] == (
        "active_caution"
    )
    assert scorecards["validation.binance.cost_simulation"]["memory_scope"] == (
        "binance"
    )
    assert scorecards["validation.binance.cost_simulation"]["lane_policy_hint"] == (
        "cost_verified_waiting_entry"
    )
    assert scorecards["validation.binance.cost_simulation"]["pass_current_gap"] == (
        "evidence_failed_threshold"
    )
    assert scorecards["validation.binance.cost_simulation"][
        "pass_collection_hook"
    ] == "sync_live_performance_and_edges"
    assert scorecards["validation.binance.cost_simulation"][
        "pass_required_evidence"
    ]["min_recorded_cost_coverage_pct"] == pytest.approx(60.0)
    assert scorecards["validation.kis.cost_simulation"]["sample_count"] == 1
    assert active_rules["validation.binance.cost_simulation"]["condition"] == {
        "new_entry": True,
        "live_authority_failed_discipline": "cost_simulation",
        "live_authority_venue": "binance",
        "min_sample_count": 3,
        "min_confidence": 0.66,
    }
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "hard_filter"
    ] is False
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "entry_bias"
    ] == "cost_verified_waiting_entry"
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "require_positive_net_edge"
    ] is True
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "sizing_policy"
    ] == "reduce_cost_weak_lane"
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "min_reward_risk"
    ] == pytest.approx(1.8)
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "risk_budget_multiplier"
    ] == pytest.approx(0.5)
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "max_budget_multiplier"
    ] == pytest.approx(0.5)
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "validation_pass_current_gap"
    ] == "evidence_failed_threshold"
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "pass_collection_hook"
    ] == "sync_live_performance_and_edges"
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "scale_up_blocked_until_pass_path"
    ] is True
    assert active_rules["validation.binance.cost_simulation"]["effect"][
        "pass_required_evidence"
    ]["min_recorded_cost_coverage_pct"] == pytest.approx(60.0)
    pack = service.context_pack(target_scope="binance", max_chars=9000)
    constraints = pack["block_design_constraints"]
    playbook = pack["next_block_design_playbook"]
    assert constraints["status"] == "active_constraints"
    assert constraints["items"][0]["policy_id"] == "validation.binance.cost_simulation"
    assert constraints["items"][0]["entry_bias"] == "cost_verified_waiting_entry"
    assert constraints["items"][0]["sizing_policy"] == "reduce_cost_weak_lane"
    assert constraints["items"][0]["min_reward_risk"] == pytest.approx(1.8)
    assert constraints["items"][0]["risk_budget_multiplier"] == pytest.approx(0.5)
    assert constraints["items"][0]["max_budget_multiplier"] == pytest.approx(0.5)
    assert constraints["items"][0]["pass_current_gap"] == (
        "evidence_failed_threshold"
    )
    assert constraints["items"][0]["pass_collection_hook"] == (
        "sync_live_performance_and_edges"
    )
    assert constraints["items"][0]["pass_required_evidence"][
        "min_recorded_cost_coverage_pct"
    ] == pytest.approx(60.0)
    assert "fee" in constraints["items"][0]["required_evidence"]
    assert playbook["status"] == "active"
    assert playbook["hard_filter"] is False
    assert playbook["entry"]["posture"] == "waiting_or_probe_preferred"
    assert "cost_verified_waiting_entry" in playbook["entry"]["biases"]
    assert playbook["sizing"]["policy"] == "reduced_or_capped"
    assert playbook["sizing"]["risk_budget_multiplier"] == pytest.approx(0.5)
    assert playbook["sizing"]["max_budget_multiplier"] == pytest.approx(0.5)
    assert playbook["target_stop"]["review_required"] is True
    assert playbook["target_stop"]["min_reward_risk"] == pytest.approx(1.8)
    assert "fee" in playbook["evidence"]["required_evidence"]
    assert "require_positive_net_edge" in playbook["evidence"]["required_checks"]


def test_validation_repair_ops_summary_exposes_actionable_constraints(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.ingest_trading_validation_signals(
        venue="binance",
        validation={
            "status": "blocked",
            "run_id": "validation-binance-cost-fail",
            "venue": "binance",
            "computed_at": "2026-06-17T00:20:00+00:00",
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "fail",
                    "action": "수수료/스프레드/슬리피지 반영 순엣지 재검증",
                }
            ],
            "summary": {"readiness": "blocked_by_validation", "fail_count": 1},
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": "cost_simulation",
                        "status": "fail",
                        "priority": "p0",
                        "owner": "live_evaluator",
                        "automation_hook": "sync_live_performance_and_edges",
                        "execution_weight": "lightweight",
                        "validation_mode": "cost_evidence_repair",
                        "scale_up_blocked": True,
                        "allowed_entry_posture": "cost_verified_waiting_entry",
                    }
                ]
            },
        },
    )

    summary = service.validation_repair_ops_summary(
        target_scope="binance",
        limit=4,
    )

    assert summary["version"] == "validation_repair_ops_summary_v1"
    assert summary["scope"] == "binance"
    assert summary["target_scope"] == "binance"
    assert summary["status"] == "needs_repair"
    assert summary["backlog_count"] == 1
    assert summary["top_backlog"][0]["discipline_id"] == "cost_simulation"
    assert summary["top_backlog"][0]["entry_bias"] == "cost_verified_waiting_entry"
    assert summary["top_backlog"][0]["sizing_policy"] == "reduce_cost_weak_lane"
    assert summary["top_backlog"][0]["risk_budget_multiplier"] == pytest.approx(0.5)
    assert summary["top_backlog"][0]["min_reward_risk"] == pytest.approx(1.8)
    assert "require_positive_net_edge" in summary["top_backlog"][0]["required_checks"]


def test_validation_repair_ops_summary_cache_invalidates_on_new_validation_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service.config.ops_summary_cache_ttl_sec = 60
    service.ingest_trading_validation_signals(
        venue="binance",
        validation={
            "status": "blocked",
            "run_id": "validation-binance-cost-cache",
            "venue": "binance",
            "computed_at": "2026-06-17T00:20:00+00:00",
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "fail",
                    "action": "순엣지 재검증",
                }
            ],
            "summary": {"readiness": "blocked_by_validation", "fail_count": 1},
        },
    )
    call_count = 0
    original_evaluate_policy_rules = service.evaluate_policy_rules

    def counting_evaluate_policy_rules(**kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return original_evaluate_policy_rules(**kwargs)

    monkeypatch.setattr(
        service,
        "evaluate_policy_rules",
        counting_evaluate_policy_rules,
    )

    first = service.validation_repair_ops_summary(target_scope="binance", limit=4)
    second = service.validation_repair_ops_summary(target_scope="binance", limit=4)

    assert call_count == 1
    assert second == first
    assert second is not first

    service.ingest_trading_validation_signals(
        venue="binance",
        validation={
            "status": "blocked",
            "run_id": "validation-binance-data-cache",
            "venue": "binance",
            "computed_at": "2026-06-17T00:25:00+00:00",
            "disciplines": [
                {
                    "id": "data_validation",
                    "label": "데이터 검증",
                    "status": "fail",
                    "action": "quote/orderbook freshness 확인",
                }
            ],
            "summary": {"readiness": "blocked_by_validation", "fail_count": 1},
        },
    )
    third = service.validation_repair_ops_summary(target_scope="binance", limit=4)

    assert call_count == 2
    assert third["backlog_count"] == 2
    assert {
        item["discipline_id"] for item in third["top_backlog"]
    } >= {"cost_simulation", "data_validation"}


def test_partial_trading_validation_payload_builds_missing_discipline_scorecards(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    result = service.ingest_trading_validation_signals(
        venue="binance",
        validation={
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": "validation-binance-partial",
            "venue": "binance",
            "computed_at": "2026-06-17T00:10:00+00:00",
            "disciplines": [
                {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                {
                    "id": "walk_forward_analysis",
                    "label": "Walk Forward Analysis",
                    "status": "pass",
                },
                {"id": "monte_carlo", "label": "몬테카를로", "status": "pass"},
            ],
            "summary": {
                "readiness": "probe",
                "pass_count": 3,
                "fail_count": 0,
                "warn_count": 0,
                "missing_count": len(DISCIPLINE_DEFINITIONS) - 3,
            },
        },
    )
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=30)["items"]
    }
    events = service.repository.list_memory_events(status="processed", limit=5)
    event = next(
        row
        for row in events
        if row["event_key"] == "trading_validation:binance:validation-binance-partial"
    )
    payload = event["payload"]

    assert result["processed_count"] == len(DISCIPLINE_DEFINITIONS) - 3
    assert "validation.binance.out_of_sample_test" in scorecards
    assert "validation.binance.profit_factor" in scorecards
    assert scorecards["validation.binance.out_of_sample_test"][
        "discipline_status"
    ] == "missing"
    assert scorecards["validation.binance.out_of_sample_test"][
        "lane_policy_hint"
    ] == ""
    assert payload["weak_disciplines"][0]["status"] == "missing"
    assert len(payload["weak_disciplines"]) == len(DISCIPLINE_DEFINITIONS) - 3


def test_core_missing_validation_signals_become_executable_policy_effects(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def payload(index: int) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": f"validation-binance-core-missing-{index}",
            "venue": "binance",
            "computed_at": f"2026-06-17T00:1{index}:00+00:00",
            "disciplines": [
                {
                    "id": "data_validation",
                    "label": "데이터 검증",
                    "status": "missing",
                    "action": "실거래 데이터 품질 표본을 채운다.",
                },
                {
                    "id": "capacity_analysis",
                    "label": "용량 분석",
                    "status": "missing",
                    "action": "호가/거래대금 기반 용량 증거를 채운다.",
                },
                {
                    "id": "mdd_limit",
                    "label": "MDD 제한",
                    "status": "missing",
                    "action": "lane별 drawdown budget 증거를 채운다.",
                },
            ],
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 0,
                "fail_count": 0,
                "warn_count": 0,
                "missing_count": 3,
            },
            "remediation_plan": {
                "lane_policy_hints": {
                    "version": "validation_lane_policy_hints_v2",
                    "scale_up_allowed": False,
                    "entry_mode": "verified_waiting_probe",
                    "requires_verified_quotes": True,
                    "requires_capacity_check": True,
                    "risk_budget_mode": "probe",
                },
                "work_queue": [
                    {
                        "discipline_id": "data_validation",
                        "lane_policy_hint": "quote_verified_only",
                        "blocks_scaling": "no_scale_up_until_data_clean",
                    },
                    {
                        "discipline_id": "capacity_analysis",
                        "lane_policy_hint": "depth_checked_only",
                        "blocks_scaling": "cap_by_depth_and_turnover",
                    },
                    {
                        "discipline_id": "mdd_limit",
                        "lane_policy_hint": (
                            "risk_budget_probe_until_ratios_recover"
                        ),
                        "blocks_scaling": "fractional_kelly_probe_only",
                    },
                ],
            },
        }

    for index in range(3):
        service.ingest_trading_validation_signals(
            venue="binance",
            validation=payload(index),
        )

    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }

    assert scorecards["validation.binance.data_validation"]["status"] == (
        "active_caution"
    )
    assert scorecards["validation.binance.capacity_analysis"]["status"] == (
        "active_caution"
    )
    assert scorecards["validation.binance.mdd_limit"]["status"] == (
        "active_caution"
    )
    data_effect = active_rules["validation.binance.data_validation"]["effect"]
    capacity_effect = active_rules["validation.binance.capacity_analysis"]["effect"]
    mdd_effect = active_rules["validation.binance.mdd_limit"]["effect"]
    assert data_effect["entry_bias"] == "quote_verified_waiting_entry"
    assert data_effect["max_budget_multiplier"] == pytest.approx(0.5)
    assert capacity_effect["require_capacity_check"] is True
    assert capacity_effect["max_budget_multiplier"] == pytest.approx(0.5)
    assert mdd_effect["entry_bias"] == "fractional_kelly_probe_entry"
    assert mdd_effect["min_reward_risk"] == pytest.approx(2.0)
    assert mdd_effect["max_budget_multiplier"] == pytest.approx(0.25)


def test_validation_policy_rules_evaluate_as_symbol_impacts_for_candidates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.binance.cost_simulation",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "confidence": 0.72,
            "reason": "Binance 비용 검증 fail 반복",
            "source": "trading_validation_signal",
            "discipline_id": "cost_simulation",
            "venue": "binance",
            "memory_scope": "binance",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="binance",
        strategy={
            "candidates": [
                {"symbol": "BTCUSDT", "market": "futures"},
                {"symbol": "ETHUSDT", "market": "spot"},
            ]
        },
        max_chars=12000,
    )

    global_impact = pack["policy_rule_evaluation"]["global"][0]
    btc_impact = pack["policy_rule_evaluation"]["by_symbol"]["BTCUSDT"][0]
    eth_impact = pack["policy_rule_evaluation"]["by_symbol"]["ETHUSDT"][0]
    recovery = pack["validation_recovery_summary"]
    status = service.status()

    assert global_impact["policy_id"] == "validation.binance.cost_simulation"
    assert global_impact["matched_metric"] == {
        "discipline_id": "cost_simulation",
        "venue": "binance",
        "target_scope": "binance",
        "match_scope": "venue_validation",
    }
    assert btc_impact["matched_metric"] == {
        "discipline_id": "cost_simulation",
        "venue": "binance",
        "target_scope": "binance",
        "match_scope": "candidate_under_validation_policy",
        "symbol": "BTCUSDT",
    }
    assert eth_impact["effect"]["entry_bias"] == "cost_verified_waiting_entry"
    assert eth_impact["effect"]["max_budget_multiplier"] == pytest.approx(0.5)
    assert recovery["status"] == "active_repair"
    assert recovery["hard_filter"] is False
    assert recovery["scale_up_allowed"] is False
    assert recovery["jue_response_summary"] == {
        "new_entries": "waiting_or_probe_preferred",
        "sizing": "reduced_or_capped",
        "target_stop": "review_required",
        "evidence": "repair_required",
    }
    assert recovery["items"][0]["policy_id"] == "validation.binance.cost_simulation"
    assert recovery["items"][0]["current_jue_response"] == [
        "prefer_waiting_or_probe_entry",
        "reduce_or_cap_sizing",
        "review_target_stop_before_entry",
        "require_evidence_repair",
    ]
    assert status["validation_recovery_summary"]["status"] == "active_repair"
    assert status["next_block_design_playbook"]["status"] == "active"
    assert status["next_block_design_playbook"]["entry"]["posture"] == (
        "waiting_or_probe_preferred"
    )


def test_jue_wiki_execution_hint_scorecard_becomes_probe_policy_rule(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "jue_wiki_execution_hint.cap_to_audit_or_repair_probe",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 2,
            "win_rate": 0.0,
            "avg_pnl_pct": -1.1,
            "expectancy_pct": -1.1,
            "rule_follow_rate": 0.0,
            "confidence": 0.62,
            "reason": "위키 힌트를 live 실행으로 위반한 케이스가 반복됨",
            "source": "jue_wiki_execution_hint_audit",
            "execution_hint": "cap_to_audit_or_repair_probe",
            "hint_violation_count": 2,
            "hint_followed_count": 0,
            "hint_status_counts": {"violated": 2},
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=12000,
    )
    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["jue_wiki_execution_hint.cap_to_audit_or_repair_probe"]
    constraint = pack["block_design_constraints"]["items"][0]

    assert rule["effect"]["entry_bias"] == "audit_or_repair_probe_only"
    assert rule["effect"]["sizing_policy"] == "micro_probe_until_wiki_hint_compliance_recovers"
    assert rule["effect"]["max_budget_multiplier"] == pytest.approx(0.25)
    assert rule["effect"]["require_jue_wiki_execution_hint_audit"] is True
    assert rule["effect"]["hard_filter"] is False
    assert constraint["policy_id"] == "jue_wiki_execution_hint.cap_to_audit_or_repair_probe"
    assert constraint["venue"] == "kis"
    assert constraint["entry_bias"] == "audit_or_repair_probe_only"
    assert constraint["execution_hint"] == "cap_to_audit_or_repair_probe"
    assert constraint["sizing_policy"] == "micro_probe_until_wiki_hint_compliance_recovers"


def test_context_pack_budget_preserves_jue_wiki_execution_hint_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, discipline_id in enumerate(
        [
            "data_validation",
            "cost_simulation",
            "mdd_limit",
            "regime_test",
            "factor_exposure",
        ]
    ):
        service.repository.upsert_policy_scorecard(
            {
                "policy_id": f"validation.kis.{discipline_id}",
                "status": "active_caution",
                "action": "caution",
                "sample_count": 4 + idx,
                "confidence": 0.95 - (idx * 0.01),
                "reason": f"validation pressure {discipline_id}",
                "source": "trading_validation_signal",
                "discipline_id": discipline_id,
                "venue": "kis",
                "memory_scope": "kis",
                "transferability": "direct",
            }
        )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "jue_wiki_execution_hint.cap_to_audit_or_repair_probe",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.0,
            "avg_pnl_pct": -1.2,
            "expectancy_pct": -1.2,
            "rule_follow_rate": 0.0,
            "confidence": 0.62,
            "reason": "위키 힌트를 live 실행으로 위반한 케이스가 반복됨",
            "source": "jue_wiki_execution_hint_audit",
            "execution_hint": "cap_to_audit_or_repair_probe",
            "hint_violation_count": 3,
            "hint_followed_count": 0,
            "hint_status_counts": {"violated": 3},
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=1000,
    )

    constraint_ids = [
        row["policy_id"]
        for row in pack["block_design_constraints"]["items"]
        if isinstance(row, dict)
    ]
    assert "jue_wiki_execution_hint.cap_to_audit_or_repair_probe" in constraint_ids


def test_period_memory_coverage_scorecard_becomes_soft_block_design_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "period_memory_coverage.gap_overridden",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 4,
            "win_rate": 0.25,
            "avg_pnl_pct": -0.9,
            "expectancy_pct": -0.9,
            "rule_follow_rate": 1.0,
            "confidence": 0.71,
            "reason": "기간 메모리 공백을 live 근거로 override한 블록 성과가 불안정함",
            "source": "period_memory_coverage_audit",
            "period_memory_status": "gap_overridden",
            "period_memory_gap_count": 4,
            "period_memory_override_count": 4,
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["period_memory_coverage.gap_overridden"]
    constraint = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"]["items"]
    }["period_memory_coverage.gap_overridden"]

    assert rule["effect"]["hard_filter"] is False
    assert rule["effect"]["period_memory_status"] == "gap_overridden"
    assert rule["effect"]["entry_bias"] == "cross_checked_probe_or_wait_on_memory_gap"
    assert rule["effect"]["sizing_policy"] == "reduce_without_fresh_period_review_or_replay"
    assert rule["effect"]["require_period_memory_override_audit"] is True
    assert "period_memory_coverage_gap" in rule["effect"]["required_evidence"]
    assert "period_memory_override_reason" in rule["effect"]["required_evidence"]
    assert constraint["policy_id"] == "period_memory_coverage.gap_overridden"
    assert constraint["venue"] == "kis"
    assert constraint["period_memory_status"] == "gap_overridden"
    assert constraint["entry_bias"] == "cross_checked_probe_or_wait_on_memory_gap"
    assert constraint["sizing_policy"] == "reduce_without_fresh_period_review_or_replay"
    assert "require_period_memory_override_audit" in constraint["required_checks"]
    assert "period_memory_coverage_gap" in constraint["required_evidence"]


def test_period_memory_successful_repair_scorecard_allows_full_size_review(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "period_memory_coverage.gap_overridden",
            "status": "active_preference",
            "action": "prefer",
            "sample_count": 6,
            "win_rate": 0.75,
            "avg_pnl_pct": 1.4,
            "expectancy_pct": 1.4,
            "rule_follow_rate": 1.0,
            "confidence": 0.78,
            "reason": "기간 메모리 공백을 수리한 뒤 live override 성과가 안정적임",
            "source": "period_memory_coverage_audit",
            "period_memory_status": "gap_overridden",
            "period_memory_gap_count": 6,
            "period_memory_override_count": 6,
            "metadata_contract_audit_resolutions": [
                "override reason restored before scaling"
            ],
            "metadata_contract_repair_notes": [
                "metadata contract repair: "
                "add_period_memory_override_reason_before_scaling; "
                "resolution: override reason restored before scaling"
            ],
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["period_memory_coverage.gap_overridden"]
    constraint = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"]["items"]
    }["period_memory_coverage.gap_overridden"]

    assert rule["effect"]["period_memory_repair_quality"] == "successful_repair"
    assert rule["effect"]["entry_bias"] == "repaired_gap_cross_checked_execution"
    assert rule["effect"]["sizing_policy"] == (
        "normal_size_after_successful_period_memory_repair"
    )
    assert rule["effect"]["risk_budget_multiplier"] == 1.0
    assert rule["effect"]["max_budget_multiplier"] == 1.0
    assert "period_memory_override_reason" in rule["effect"]["required_evidence"]
    assert "current_live_cross_check" in rule["effect"]["required_evidence"]
    assert constraint["period_memory_repair_quality"] == "successful_repair"
    assert constraint["sizing_policy"] == (
        "normal_size_after_successful_period_memory_repair"
    )


def test_period_memory_contract_gap_scorecard_becomes_metadata_repair_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "period_memory_coverage.missing_override_reason",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.34,
            "avg_pnl_pct": -0.4,
            "expectancy_pct": -0.4,
            "rule_follow_rate": 1.0,
            "confidence": 0.68,
            "reason": "기간 메모리 공백 override 사유가 metadata에 남지 않음",
            "source": "period_memory_coverage_audit",
            "period_memory_status": "missing_override_reason",
            "period_memory_gap_count": 3,
            "period_memory_override_count": 0,
            "period_memory_contract_gap_count": 3,
            "period_memory_missing_metadata": ["period_memory_override_reason"],
            "period_memory_repair_actions": [
                "add_period_memory_override_reason_before_scaling"
            ],
            "metadata_contract_audit_resolutions": [
                "kept micro probe until override reason is restored"
            ],
            "metadata_contract_repair_notes": [
                "metadata contract repair: "
                "add_period_memory_override_reason_before_scaling; "
                "resolution: kept micro probe until override reason is restored"
            ],
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["period_memory_coverage.missing_override_reason"]
    constraint = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"]["items"]
    }["period_memory_coverage.missing_override_reason"]

    assert rule["effect"]["hard_filter"] is False
    assert rule["effect"]["period_memory_status"] == "missing_override_reason"
    assert (
        rule["effect"]["entry_bias"]
        == "metadata_repair_probe_or_wait_until_override_reason_present"
    )
    assert rule["effect"]["require_period_memory_metadata_contract_repair"] is True
    assert rule["effect"]["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert rule["effect"]["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert rule["effect"]["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert rule["effect"]["metadata_contract_repair_notes"] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]
    assert "period_memory_coverage_gap" in rule["effect"]["required_evidence"]
    assert "period_memory_override_reason" in rule["effect"]["required_evidence"]
    assert "metadata_contract_audit_resolution" in rule["effect"]["required_evidence"]
    assert constraint["policy_id"] == "period_memory_coverage.missing_override_reason"
    assert constraint["period_memory_status"] == "missing_override_reason"
    assert constraint["period_memory_contract_gap_count"] == 3
    assert constraint["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert constraint["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert constraint["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert constraint["metadata_contract_repair_notes"] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]
    assert (
        "require_period_memory_metadata_contract_repair"
        in constraint["required_checks"]
    )


def test_period_memory_missing_gap_scorecard_requires_gap_naming_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "period_memory_coverage.missing_coverage_gap",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.34,
            "avg_pnl_pct": -0.4,
            "expectancy_pct": -0.4,
            "rule_follow_rate": 1.0,
            "confidence": 0.68,
            "reason": "기간 메모리 공백 이름 없이 override 처리됨",
            "source": "period_memory_coverage_audit",
            "period_memory_status": "missing_coverage_gap",
            "period_memory_gap_count": 0,
            "period_memory_override_count": 3,
            "period_memory_contract_gap_count": 3,
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="kis",
        strategy={"candidates": [{"symbol": "005930", "name": "삼성전자"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["period_memory_coverage.missing_coverage_gap"]
    constraint = {
        row["policy_id"]: row
        for row in pack["block_design_constraints"]["items"]
    }["period_memory_coverage.missing_coverage_gap"]

    assert rule["effect"]["hard_filter"] is False
    assert rule["effect"]["period_memory_status"] == "missing_coverage_gap"
    assert (
        rule["effect"]["entry_bias"]
        == "metadata_repair_probe_or_wait_until_coverage_gap_named"
    )
    assert rule["effect"]["target_stop_review"] == (
        "name_period_memory_gap_before_using_override"
    )
    assert rule["effect"]["require_period_memory_metadata_contract_repair"] is True
    assert "period_memory_coverage_gap" in rule["effect"]["required_evidence"]
    assert "metadata_contract_audit_resolution" in rule["effect"]["required_evidence"]
    assert constraint["period_memory_status"] == "missing_coverage_gap"
    assert constraint["period_memory_contract_gap_count"] == 3
    assert (
        "require_period_memory_metadata_contract_repair"
        in constraint["required_checks"]
    )


def test_context_pack_budget_preserves_period_memory_scorecard_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(8):
        service.repository.upsert_policy_scorecard(
            {
                "policy_id": f"validation.kis.data_quality_noise_{idx}",
                "status": "active_caution",
                "action": "caution",
                "sample_count": 4,
                "confidence": 0.72,
                "reason": "budget filler",
                "source": "trading_validation_signal",
                "memory_scope": "kis",
                "transferability": "direct",
            }
        )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "period_memory_coverage.gap_overridden",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 4,
            "win_rate": 0.25,
            "avg_pnl_pct": -0.9,
            "expectancy_pct": -0.9,
            "rule_follow_rate": 1.0,
            "confidence": 0.71,
            "reason": "기간 메모리 공백을 live 근거로 override한 블록 성과가 불안정함",
            "source": "period_memory_coverage_audit",
            "period_memory_status": "gap_overridden",
            "period_memory_gap_count": 4,
            "period_memory_override_count": 4,
            "period_memory_contract_gap_count": 2,
            "period_memory_missing_metadata": ["period_memory_override_reason"],
            "period_memory_repair_actions": [
                "add_period_memory_override_reason_before_scaling"
            ],
            "metadata_contract_audit_resolutions": [
                "kept micro probe until override reason is restored"
            ],
            "metadata_contract_repair_notes": [
                "metadata contract repair: "
                "add_period_memory_override_reason_before_scaling; "
                "resolution: kept micro probe until override reason is restored"
            ],
            "memory_scope": "kis",
            "transferability": "direct",
        }
    )

    pack = service.context_pack(target_scope="kis", max_chars=1000)
    scorecards = {
        row["policy_id"]: row
        for row in pack["policy_scorecards"]
        if isinstance(row, dict)
    }
    period_scorecard = scorecards["period_memory_coverage.gap_overridden"]

    assert period_scorecard["source"] == "period_memory_coverage_audit"
    assert period_scorecard["period_memory_status"] == "gap_overridden"
    assert period_scorecard["period_memory_gap_count"] == 4
    assert period_scorecard["period_memory_override_count"] == 4
    assert period_scorecard["period_memory_contract_gap_count"] == 2
    assert period_scorecard["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert period_scorecard["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert period_scorecard["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert period_scorecard["metadata_contract_repair_notes"] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]
    period_rule = {
        row["policy_id"]: row
        for row in pack["policy_rules"]
        if isinstance(row, dict)
    }["period_memory_coverage.gap_overridden"]
    assert period_rule["effect"]["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert period_rule["source_scorecard"]["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert period_rule["source_scorecard"]["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert period_rule["source_scorecard"]["metadata_contract_repair_notes"] == [
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]


def test_lane_scale_verified_alpha_gap_becomes_block_design_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "lane_scale.verified_edge_sample_cap",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 5,
            "win_rate": 0.4,
            "avg_pnl_pct": -0.8,
            "expectancy_pct": -0.8,
            "rule_follow_rate": 1.0,
            "confidence": 0.72,
            "reason": "검증된 비용 차감 알파 샘플이 부족한 lane",
            "source": "lane_authority_scale_blocker",
            "scale_blocker": "verified_edge_sample_cap",
            "venue": "binance",
            "memory_scope": "binance",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="binance",
        strategy={"candidates": [{"symbol": "BTCUSDT", "market": "futures"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["lane_scale.verified_edge_sample_cap"]
    constraint = pack["block_design_constraints"]["items"][0]
    recovery = pack["validation_recovery_summary"]
    playbook = pack["next_block_design_playbook"]

    assert rule["effect"]["entry_bias"] == "recorded_cost_alpha_waiting_probe"
    assert rule["effect"]["target_stop_review"] == (
        "require_net_edge_after_recorded_costs_before_pressing"
    )
    assert rule["effect"]["risk_budget_multiplier"] == pytest.approx(0.25)
    assert rule["effect"]["max_budget_multiplier"] == pytest.approx(0.5)
    assert rule["effect"]["min_reward_risk"] == pytest.approx(2.0)
    assert rule["effect"]["hard_filter"] is False
    assert constraint["policy_id"] == "lane_scale.verified_edge_sample_cap"
    assert constraint["scale_blocker"] == "verified_edge_sample_cap"
    assert constraint["entry_bias"] == "recorded_cost_alpha_waiting_probe"
    assert constraint["sizing_policy"] == "recorded_alpha_probe_until_min_samples"
    assert constraint["target_stop_review"] == (
        "require_net_edge_after_recorded_costs_before_pressing"
    )
    assert constraint["risk_budget_multiplier"] == pytest.approx(0.25)
    assert constraint["max_budget_multiplier"] == pytest.approx(0.5)
    assert constraint["min_reward_risk"] == pytest.approx(2.0)
    assert "recorded_entry_fill" in constraint["required_evidence"]
    assert "recorded_exit_fill" in constraint["required_evidence"]
    assert "positive_net_edge" in constraint["required_evidence"]
    assert "require_positive_net_edge" in constraint["required_checks"]
    assert "require_scale_repair_review" in constraint["required_checks"]
    assert recovery["status"] == "active_repair"
    assert recovery["scale_up_allowed"] is False
    assert recovery["jue_response_summary"] == {
        "new_entries": "waiting_or_probe_preferred",
        "sizing": "reduced_or_capped",
        "target_stop": "review_required",
        "evidence": "repair_required",
    }
    assert playbook["entry"]["posture"] == "waiting_or_probe_preferred"
    assert "recorded_cost_alpha_waiting_probe" in playbook["entry"]["biases"]
    assert playbook["sizing"]["risk_budget_multiplier"] == pytest.approx(0.25)
    assert playbook["sizing"]["max_budget_multiplier"] == pytest.approx(0.5)
    assert playbook["target_stop"]["min_reward_risk"] == pytest.approx(2.0)
    assert "positive_net_edge" in playbook["evidence"]["required_evidence"]


def test_scope_only_scorecard_builds_binance_block_design_constraint_venue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "lane_scale.verified_edge_sample_cap",
                "scope": "binance",
                "sample_count": 7,
                "confidence": 0.72,
                "expectancy_r": -0.3,
                "evidence_ids": ["binance-scope-only-lane"],
                "reason": "scope 필드만 있는 Binance lane scale 정책",
            }
        ]
    )

    pack = service.context_pack(
        target_scope="binance",
        strategy={"candidates": [{"symbol": "BTCUSDT", "market": "futures"}]},
        max_chars=12000,
    )

    constraint = pack["block_design_constraints"]["items"][0]
    assert constraint["policy_id"] == "lane_scale.verified_edge_sample_cap"
    assert constraint["venue"] == "binance"


def test_lane_scale_verified_net_loss_becomes_positive_edge_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "lane_scale.verified_edge_net_pnl_cap",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 7,
            "win_rate": 0.36,
            "avg_pnl_pct": -1.1,
            "expectancy_pct": -1.1,
            "rule_follow_rate": 1.0,
            "confidence": 0.76,
            "reason": "기록비용 기준 순알파가 음수인 lane",
            "source": "lane_authority_scale_blocker",
            "scale_blocker": "verified_edge_net_pnl_cap",
            "venue": "binance",
            "memory_scope": "binance",
            "transferability": "direct",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        target_scope="binance",
        strategy={"candidates": [{"symbol": "ETHUSDT", "market": "futures"}]},
        max_chars=12000,
    )

    rule = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }["lane_scale.verified_edge_net_pnl_cap"]
    constraint = pack["block_design_constraints"]["items"][0]
    recovery = pack["validation_recovery_summary"]

    assert rule["effect"]["entry_bias"] == "positive_recorded_edge_waiting_probe"
    assert rule["effect"]["target_stop_review"] == (
        "reprice_or_wait_until_recorded_cost_alpha_positive"
    )
    assert rule["effect"]["sizing_policy"] == (
        "micro_probe_until_recorded_alpha_positive"
    )
    assert rule["effect"]["risk_budget_multiplier"] == pytest.approx(0.25)
    assert rule["effect"]["max_budget_multiplier"] == pytest.approx(0.25)
    assert rule["effect"]["min_reward_risk"] == pytest.approx(2.2)
    assert rule["effect"]["hard_filter"] is False
    assert constraint["policy_id"] == "lane_scale.verified_edge_net_pnl_cap"
    assert constraint["scale_blocker"] == "verified_edge_net_pnl_cap"
    assert constraint["entry_bias"] == "positive_recorded_edge_waiting_probe"
    assert constraint["sizing_policy"] == (
        "micro_probe_until_recorded_alpha_positive"
    )
    assert constraint["target_stop_review"] == (
        "reprice_or_wait_until_recorded_cost_alpha_positive"
    )
    assert constraint["risk_budget_multiplier"] == pytest.approx(0.25)
    assert constraint["max_budget_multiplier"] == pytest.approx(0.25)
    assert constraint["min_reward_risk"] == pytest.approx(2.2)
    assert "positive_recorded_cost_alpha_net_pnl" in constraint["required_evidence"]
    assert "require_positive_net_edge" in constraint["required_checks"]
    assert "require_scale_repair_review" in constraint["required_checks"]
    assert recovery["status"] == "active_repair"
    assert recovery["scale_up_allowed"] is False
    assert recovery["items"][0]["current_jue_response"] == [
        "prefer_waiting_or_probe_entry",
        "reduce_or_cap_sizing",
        "review_target_stop_before_entry",
        "require_evidence_repair",
    ]


def test_context_pack_includes_scoped_validation_repair_backlog(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def payload(run_id: str, venue: str, discipline_id: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": run_id,
            "venue": venue,
            "computed_at": f"2026-06-17T00:0{run_id[-1]}:00+00:00",
            "disciplines": [
                {
                    "id": discipline_id,
                    "label": discipline_id,
                    "status": "fail",
                    "action": "복구 전 증액 보류",
                }
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 18,
                "fail_count": 1,
                "warn_count": 0,
                "missing_count": 0,
            },
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": discipline_id,
                        "repair_action_id": (
                            f"validation_repair.portfolio_exposure_check.{discipline_id}"
                        ),
                        "status": "fail",
                        "priority": "p0",
                        "owner": "portfolio_risk",
                        "cadence": "before_new_correlated_block",
                        "automation_hook": "refresh_portfolio_exposure_snapshot",
                        "execution_weight": "lightweight",
                        "lane_policy_hint": "avoid_unpriced_concentration",
                        "blocks_scaling": "cap_correlated_exposure",
                        "blocks_new_entries": (
                            "correlated_or_factor_concentrated_entries"
                        ),
                        "runner_hint": (
                            "refresh portfolio exposure snapshots, then "
                            "refresh_trading_validation"
                        ),
                        "verification_artifact": (
                            "correlation metric includes active exposure buckets"
                        ),
                        "exit_criteria": "상관/팩터 쏠림이 pass로 회복될 때까지 증액 보류",
                        "validation_mode": "portfolio_exposure_check",
                        "allowed_entry_posture": "exposure_capped_probe",
                        "live_shadow_required": False,
                        "scale_up_blocked": True,
                        "evidence_targets": {
                            "max_top_cluster_share_pct": 60.0,
                            "requires_active_block_exposure_snapshot": True,
                        },
                    }
                ]
            },
        }

    service.ingest_trading_validation_signals(
        venue="binance",
        validation=payload("validation-binance-1", "binance", "correlation"),
    )
    service.ingest_trading_validation_signals(
        venue="kis",
        validation=payload("validation-kis-1", "kis", "cost_simulation"),
    )
    service.ingest_validation_repair_execution(
        {
            "status": "queued",
            "actions": [
                {
                    "venue": "binance",
                    "discipline_id": "correlation",
                    "repair_action_id": (
                        "validation_repair.portfolio_exposure_check.correlation"
                    ),
                    "status": "queued_external_runner",
                    "automation_hook": "refresh_portfolio_exposure_snapshot",
                    "execution_weight": "lightweight",
                    "validation_mode": "portfolio_exposure_check",
                    "artifact": "portfolio_exposure_snapshot",
                    "scale_up_blocked": True,
                    "reason": "exposure snapshot repair is still queued",
                }
            ],
        }
    )

    pack = service.context_pack(target_scope="binance", max_chars=8000)
    backlog = pack["validation_repair_backlog"]

    assert backlog["status"] == "needs_repair"
    assert backlog["item_count"] == 1
    assert backlog["items"][0]["venue"] == "binance"
    assert backlog["items"][0]["discipline_id"] == "correlation"
    assert backlog["items"][0]["repair_action_id"] == (
        "validation_repair.portfolio_exposure_check.correlation"
    )
    assert backlog["items"][0]["priority"] == "p0"
    assert backlog["items"][0]["owner"] == "portfolio_risk"
    assert backlog["items"][0]["automation_hook"] == (
        "refresh_portfolio_exposure_snapshot"
    )
    assert backlog["items"][0]["execution_weight"] == "lightweight"
    assert backlog["items"][0]["lane_policy_hint"] == "avoid_unpriced_concentration"
    assert backlog["items"][0]["blocks_scaling"] == "cap_correlated_exposure"
    assert backlog["items"][0]["blocks_new_entries"] == (
        "correlated_or_factor_concentrated_entries"
    )
    assert "refresh_trading_validation" in backlog["items"][0]["runner_hint"]
    assert "active exposure buckets" in backlog["items"][0][
        "verification_artifact"
    ]
    assert backlog["items"][0]["validation_mode"] == "portfolio_exposure_check"
    assert backlog["items"][0]["allowed_entry_posture"] == "exposure_capped_probe"
    assert backlog["items"][0]["scale_up_blocked"] is True
    assert backlog["items"][0]["evidence_targets"]["max_top_cluster_share_pct"] == 60.0
    assert backlog["items"][0]["scale_blocker"] == "validation_correlation_repair"
    assert backlog["items"][0]["validation_effect_profile"] == (
        "portfolio_concentration"
    )
    assert backlog["items"][0]["entry_bias"] == (
        "concentration_checked_waiting_entry"
    )
    assert backlog["items"][0]["sizing_policy"] == "cap_correlated_exposure"
    assert backlog["items"][0]["target_stop_review"] == (
        "review_regime_correlation_factor_exposure"
    )
    assert backlog["items"][0]["risk_budget_multiplier"] == pytest.approx(0.5)
    assert backlog["items"][0]["max_budget_multiplier"] == pytest.approx(0.5)
    assert "correlation_cluster" in backlog["items"][0]["required_evidence"]
    assert "require_exposure_review" in backlog["items"][0]["required_checks"]
    assert backlog["items"][0]["policy_id"] == "validation.binance.correlation"
    assert backlog["items"][0]["repair_policy_id"] == (
        "validation_repair.binance.correlation"
    )
    assert backlog["items"][0]["last_repair_status"] == "queued_external_runner"
    assert backlog["items"][0]["last_repair_policy_status"] == "active_caution"
    assert backlog["items"][0]["last_repair_action"] == "caution"
    assert backlog["items"][0]["last_repair_confidence"] == pytest.approx(0.5)
    assert backlog["items"][0]["last_repair_automation_hook"] == (
        "refresh_portfolio_exposure_snapshot"
    )
    assert backlog["items"][0]["last_repair_execution_weight"] == "lightweight"
    assert "복구 대기 중" in backlog["items"][0]["last_repair_reason"]
    assert "cost_simulation" not in json.dumps(backlog, ensure_ascii=False)

    today = service.today()
    status = service.status()
    assert today["validation_repair_backlog"]["status"] == "needs_repair"
    assert status["validation_repair_backlog_status"] == "needs_repair"
    assert status["validation_repair_backlog_count"] == 2


def test_repair_execution_updates_memory_scorecards_and_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    result = service.ingest_validation_repair_execution(
        {
            "status": "queued",
            "status_counts": {"queued_external_runner": 1, "executed": 1},
            "actions": [
                {
                    "venue": "binance",
                    "discipline_id": "walk_forward_analysis",
                    "repair_action_id": (
                        "validation_repair.backtest_wfa_oos_rebuild."
                        "walk_forward_analysis"
                    ),
                    "status": "queued_external_runner",
                    "automation_hook": "pattern_lab_rebuild_wfa_oos",
                    "execution_weight": "external_runner",
                    "validation_mode": "backtest_wfa_oos_rebuild",
                    "artifact": "crypto_pattern_lab_runner",
                    "scale_up_blocked": True,
                    "live_shadow_required": True,
                },
                {
                    "venue": "kis",
                    "discipline_id": "data_validation",
                    "status": "executed",
                    "validation_mode": "data_repair_before_trade",
                    "artifact": "sync_live_performance_and_edges",
                    "scale_up_blocked": True,
                },
            ],
        }
    )
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    events = {
        row["event_key"]: row
        for row in service.repository.list_memory_events(status="processed", limit=20)
    }
    pack = service.context_pack(target_scope="binance", max_chars=8000)

    assert result["status"] == "ok"
    assert result["processed_count"] == 2
    binance_policy = "validation_repair.binance.walk_forward_analysis"
    kis_policy = "validation_repair.kis.data_validation"
    assert scorecards[binance_policy]["status"] == "active_caution"
    assert scorecards[binance_policy]["action"] == "caution"
    assert scorecards[binance_policy]["repair_status"] == "queued_external_runner"
    assert scorecards[binance_policy]["repair_action_id"] == (
        "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis"
    )
    assert scorecards[binance_policy]["automation_hook"] == (
        "pattern_lab_rebuild_wfa_oos"
    )
    assert scorecards[binance_policy]["execution_weight"] == "external_runner"
    assert scorecards[binance_policy]["live_shadow_required"] is True
    assert scorecards[kis_policy]["status"] == "candidate"
    assert scorecards[kis_policy]["action"] == "observe"
    assert "validation_repair_execution:binance:walk_forward_analysis" in events
    scoped_local_keys = {
        row["key"]
        for row in pack["scoped_memory"]["local"]
        if row["memory_type"] in {"policy_signal", "policy_scorecard", "policy_rule"}
    }
    scoped_translated_keys = {
        row["key"]
        for row in pack["scoped_memory"]["translated"]
        if row["memory_type"] in {"policy_scorecard", "policy_rule"}
    }
    assert binance_policy in scoped_local_keys
    assert kis_policy not in scoped_local_keys
    assert kis_policy in scoped_translated_keys


def test_validation_correlation_policy_caps_concentration_without_hard_filter(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def payload(index: int) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": f"validation-binance-correlation-{index}",
            "venue": "binance",
            "computed_at": f"2026-06-17T00:0{index}:00+00:00",
            "disciplines": [
                {
                    "id": "correlation",
                    "label": "상관관계",
                    "status": "fail",
                    "action": "상관 노출 축소",
                }
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 18,
                "fail_count": 1,
                "warn_count": 0,
                "missing_count": 0,
            },
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": "correlation",
                        "status": "fail",
                        "priority": "p1",
                        "owner": "portfolio_risk",
                        "cadence": "before_new_correlated_block",
                        "lane_policy_hint": "avoid_unpriced_concentration",
                        "blocks_scaling": "cap_correlated_exposure",
                    }
                ]
            },
        }

    for index in range(1, 4):
        service.ingest_trading_validation_signals(
            venue="binance",
            validation=payload(index),
        )

    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }
    effect = active_rules["validation.binance.correlation"]["effect"]

    assert effect["hard_filter"] is False
    assert effect["entry_bias"] == "concentration_checked_waiting_entry"
    assert effect["require_exposure_review"] is True
    assert effect["sizing_policy"] == "cap_correlated_exposure"
    assert effect["risk_budget_multiplier"] == pytest.approx(0.5)
    assert effect["max_budget_multiplier"] == pytest.approx(0.5)
    assert "correlation_cluster" in effect["required_evidence"]

    pack = service.context_pack(target_scope="binance", max_chars=9000)
    constraint = pack["block_design_constraints"]["items"][0]
    assert constraint["policy_id"] == "validation.binance.correlation"
    assert constraint["validation_effect_profile"] == "portfolio_concentration"
    assert constraint["entry_bias"] == "concentration_checked_waiting_entry"
    assert constraint["sizing_policy"] == "cap_correlated_exposure"
    assert constraint["risk_budget_multiplier"] == pytest.approx(0.5)
    assert constraint["max_budget_multiplier"] == pytest.approx(0.5)
    assert "require_exposure_review" in constraint["required_checks"]


def test_validation_matrix_failures_build_policy_scorecards_without_failed_list(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_matrix_kelly_fail_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "BTCUSDT",
                "name": "Bitcoin",
                "status": "closed",
                "qty_initial": 0.01,
                "entry_price": 65000,
                "current_price": 64200,
                "target_price": 67000,
                "stop_price": 64000,
                "thesis": "matrix fail 정책 생성 테스트",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "live_authority": {
                        "validation_gate_status": "blocked_by_validation",
                        "validation_readiness": "blocked_by_validation",
                        "validation_gate_reason": "readiness=blocked_by_validation, fail_count=1",
                        "discipline_matrix": {
                            "expected_count": 19,
                            "actual_count": 2,
                            "summary": {
                                "readiness": "blocked_by_validation",
                                "pass_count": 1,
                                "warn_count": 0,
                                "fail_count": 1,
                                "missing_count": 0,
                            },
                            "statuses": [
                                {
                                    "id": "data_validation",
                                    "label": "데이터 검증",
                                    "status": "pass",
                                },
                                {
                                    "id": "kelly_sizing",
                                    "label": "켈리 공식",
                                    "status": "fail",
                                    "action": "fractional sizing 재계산",
                                },
                            ],
                        },
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 64200,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }
    reflection = service.block_memory("blk_matrix_kelly_fail_0")["reflection"]

    assert result["created_count"] == 3
    assert reflection["metrics"]["live_authority"]["discipline_matrix"]["summary"][
        "fail_count"
    ] == 1
    assert scorecards["validation.kelly_sizing"]["status"] == "active_caution"
    assert scorecards["validation.kelly_sizing"]["discipline_id"] == "kelly_sizing"
    assert active_rules["validation.kelly_sizing"]["condition"][
        "live_authority_failed_discipline"
    ] == "kelly_sizing"
    assert active_rules["validation.kelly_sizing"]["effect"]["hard_filter"] is False
    assert active_rules["validation.kelly_sizing"]["effect"]["entry_bias"] == (
        "fractional_kelly_probe_entry"
    )
    assert active_rules["validation.kelly_sizing"]["effect"]["sizing_policy"] == (
        "fractional_kelly_probe_only"
    )
    assert active_rules["validation.kelly_sizing"]["effect"][
        "min_reward_risk"
    ] == pytest.approx(2.0)
    assert active_rules["validation.kelly_sizing"]["effect"][
        "max_stop_risk_pct"
    ] == pytest.approx(6.0)
    assert active_rules["validation.kelly_sizing"]["effect"][
        "risk_budget_multiplier"
    ] == pytest.approx(0.25)
    assert active_rules["validation.kelly_sizing"]["effect"][
        "max_budget_multiplier"
    ] == pytest.approx(0.25)
    assert "fractional_kelly" in active_rules["validation.kelly_sizing"]["effect"][
        "required_evidence"
    ]


def test_validation_passport_failures_build_policy_scorecards_without_matrix(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_passport_monte_fail_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "BTCUSDT",
                "name": "Bitcoin",
                "status": "closed",
                "qty_initial": 0.01,
                "entry_price": 65000,
                "current_price": 64100,
                "target_price": 67000,
                "stop_price": 64000,
                "thesis": "passport fail 정책 생성 테스트",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "scope": "binance",
                    "live_authority": {
                        "validation_gate_status": "blocked_by_validation",
                        "validation_readiness": "blocked_by_validation",
                        "validation_gate_reason": "readiness=blocked_by_validation, fail_count=1",
                        "validation_passport": {
                            "version": "trading_validation_passport_v1",
                            "status": "blocked_by_validation",
                            "readiness": "blocked_by_validation",
                            "score": 51.0,
                            "expected_count": 19,
                            "actual_count": 19,
                            "is_complete": True,
                            "failed_ids": ["monte_carlo"],
                            "weak_ids": ["monte_carlo"],
                            "requires_revalidation": True,
                        },
                    },
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 64100,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    scorecards = {
        row["policy_id"]: row
        for row in service.policy_scorecards(limit=20)["items"]
    }
    active_rules = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=True)["items"]
    }
    reflection = service.block_memory("blk_passport_monte_fail_0")["reflection"]

    assert result["created_count"] == 3
    assert reflection["metrics"]["live_authority"]["validation_passport"][
        "failed_ids"
    ] == ["monte_carlo"]
    assert "검증 여권" in reflection["lesson_md"]
    assert scorecards["validation.monte_carlo"]["status"] == "active_caution"
    assert scorecards["validation.monte_carlo"]["discipline_id"] == "monte_carlo"
    assert active_rules["validation.monte_carlo"]["condition"][
        "live_authority_failed_discipline"
    ] == "monte_carlo"
    assert active_rules["validation.monte_carlo"]["effect"]["hard_filter"] is False


def test_policy_scorecards_prioritize_active_rules_before_candidates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "candidate.high_confidence",
            "status": "candidate",
            "action": "observe",
            "sample_count": 8,
            "confidence": 0.99,
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation.capacity_analysis",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "confidence": 0.66,
        }
    )
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "protect_winning_blocks",
            "status": "active_preference",
            "action": "prefer",
            "sample_count": 5,
            "confidence": 0.7,
        }
    )

    items = service.policy_scorecards(limit=3)["items"]

    assert [row["policy_id"] for row in items] == [
        "protect_winning_blocks",
        "validation.capacity_analysis",
        "candidate.high_confidence",
    ]


def test_compact_policy_summaries_demotes_resolved_contract_errors() -> None:
    rows = [
        {
            "policy_id": "manager_contract_error.kis.old_gap",
            "status": "resolved",
            "action": "observe",
            "confidence": 0.99,
            "reason": "Old manager contract gap was already repaired.",
        },
        {
            "policy_id": "validation.capacity_analysis",
            "status": "active_caution",
            "action": "caution",
            "confidence": 0.66,
            "reason": "Capacity evidence is still weak.",
        },
    ]

    compact = _compact_policy_summaries_for_budget(
        rows,
        limit=1,
        summary_limit=120,
    )

    assert [row["policy_id"] for row in compact] == ["validation.capacity_analysis"]


def test_due_reflections_read_compact_kis_block_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = {
        "blocks": {
            "recent_closed_blocks": [
                {
                    "block_id": "blk_005930_compact_closed",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "current_price": 84000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "compact snapshot reflection",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                }
            ],
            "recent_orders": [
                {
                    "block_id": "blk_005930_compact_closed",
                    "side": "sell",
                    "limit_price": 84000,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory("blk_005930_compact_closed")["reflection"]

    assert result["status"] == "ok"
    assert result["checked"] == 1
    assert reflection["metrics"]["memory_scope"] == "kis"
    assert reflection["exit_reason"] == "target_close"


def test_due_reflections_include_binance_blocks_with_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    block_id = "bnb_spot_BTCUSDT_closed"
    context = {
        "blocks": {"blocks": [], "orders": []},
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "status": "closed",
                    "qty_initial": 0.5,
                    "entry_price": 100.0,
                    "current_price": 110.0,
                    "target_price": 110.0,
                    "stop_price": 92.0,
                    "thesis": "BTC 반등 블록",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "quote": {"high_price": 112.0, "low_price": 98.0},
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "sell",
                    "limit_price": 110.0,
                    "reason": "target_close",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        },
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    pack = service.context_pack(target_scope="kis", max_chars=8000)

    assert result["status"] == "ok"
    assert reflection["metrics"]["memory_scope"] == "binance"
    assert reflection["metrics"]["transferability"] == "translated"
    assert reflection["metrics"]["qty"] == pytest.approx(0.5)
    assert "BTC 반등 블록" in reflection["lesson_md"]
    assert block_id in json.dumps(pack["scoped_memory"]["translated"], ensure_ascii=False)


def test_due_reflections_read_compact_binance_block_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    block_id = "bnb_futures_ETHUSDT_short_compact_closed"
    context = {
        "binance_blocks": {
            "block_history": [
                {
                    "block_id": block_id,
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "status": "closed",
                    "qty_initial": 0.2,
                    "entry_price": 3000.0,
                    "current_price": 2910.0,
                    "target_price": 2910.0,
                    "stop_price": 3090.0,
                    "thesis": "compact binance history reflection",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "buy",
                    "limit_price": 2910.0,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    assert result["checked"] == 1
    assert reflection["metrics"]["memory_scope"] == "binance"
    assert reflection["exit_reason"] == "target_reached"


def test_binance_short_reflection_uses_exit_buy_order_and_short_pnl(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "bnb_futures_BNBUSDT_short_closed"
    context = {
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "status": "closed",
                    "qty_initial": 2.0,
                    "entry_price": 100.0,
                    "current_price": 90.0,
                    "target_price": 90.0,
                    "stop_price": 104.0,
                    "thesis": "BNB 약세 숏 블록",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "quote": {"high_price": 105.0, "low_price": 88.0},
                    "metadata": {
                        "fees_usdt": 0.10,
                        "funding_usdt": 0.02,
                        "slippage_usdt": 0.03,
                    },
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "sell",
                    "limit_price": 100.0,
                    "reason": "entry_order",
                    "created_at": "2026-05-08T00:30:00+00:00",
                },
                {
                    "block_id": block_id,
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "buy",
                    "limit_price": 90.0,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                },
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    assert reflection["exit_reason"] == "target_reached"
    assert reflection["pnl_pct"] == pytest.approx(9.925)
    assert reflection["pnl_krw"] == pytest.approx(19.85)
    assert reflection["mfe_pct"] == pytest.approx(12.0)
    assert reflection["mae_pct"] == pytest.approx(-5.0)
    assert reflection["metrics"]["gross_pnl"] == pytest.approx(20.0)
    assert reflection["metrics"]["gross_pnl_pct"] == pytest.approx(10.0)
    assert reflection["metrics"]["net_pnl"] == pytest.approx(19.85)
    assert reflection["metrics"]["costs"]["total"] == pytest.approx(0.15)
    assert reflection["metrics"]["costs"]["source"] == "explicit"
    assert "결과: +9.93%" in reflection["lesson_md"]


def test_due_reflections_use_precise_kis_performance_cost_components(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "kis_277810_precise_cost_closed"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "277810",
                    "name": "레인보우로보틱스",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 100_500,
                    "current_price": 110_500,
                    "target_price": 110_000,
                    "stop_price": 95_000,
                    "thesis": "정밀 비용이 확인된 목표가 청산 블록",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "performance": {
                            "version": "kis_closed_block_performance_v1",
                            "entry_price": 100_500,
                            "exit_price": 110_500,
                            "qty": 1,
                            "gross_pnl_krw": 10_000,
                            "net_pnl_krw": 9_724.5,
                            "total_cost_krw": 275.5,
                            "cost_source": "kis_order_payload",
                            "cost_components": {
                                "fees": 20.0,
                                "taxes": 150.0,
                                "slippage": 105.5,
                                "spread": 0.0,
                                "funding": 0.0,
                            },
                        }
                    },
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "277810",
                    "side": "sell",
                    "limit_price": 110_000,
                    "avg_fill_price": 110_500,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    assert reflection["metrics"]["gross_pnl"] == pytest.approx(10_000)
    assert reflection["metrics"]["net_pnl"] == pytest.approx(9_724.5)
    assert reflection["pnl_krw"] == pytest.approx(9_724.5)
    assert reflection["metrics"]["costs"]["fee"] == pytest.approx(20.0)
    assert reflection["metrics"]["costs"]["taxes"] == pytest.approx(150.0)
    assert reflection["metrics"]["costs"]["slippage"] == pytest.approx(105.5)
    assert reflection["metrics"]["costs"]["spread"] == pytest.approx(0.0)
    assert reflection["metrics"]["costs"]["total"] == pytest.approx(275.5)
    assert reflection["metrics"]["costs"]["source"] == "kis_order_payload"
    assert reflection["metrics"]["costs"]["cost_precision"] == "recorded"
    assert reflection["metrics"]["costs"]["required_cost_components"] == [
        "fees",
        "slippage",
        "spread",
        "taxes",
    ]
    assert reflection["metrics"]["costs"]["present_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
        "taxes",
    ]
    assert reflection["metrics"]["costs"]["missing_cost_components"] == []


def test_due_reflections_do_not_score_unexecuted_upbit_error_blocks(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "bnb_upbit_spot_KRW-JTO_unexecuted_error"
    context = {
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "KRW-JTO",
                    "market": "upbit_spot",
                    "side": "long",
                    "status": "error",
                    "qty_initial": 20299.513540311087,
                    "qty_open": 0.0,
                    "entry_price": 0.80685,
                    "current_price": 1280.0,
                    "target_price": 0.81976,
                    "stop_price": 0.8004,
                    "thesis": "업비트 대기 진입 블록",
                    "llm_reason": "Create one small waiting-entry Upbit spot long block.",
                    "risk_note": (
                        "frozen: upbit KRW/USDT price-scale mismatch repaired in code; "
                        "legacy proposed block disabled"
                    ),
                    "created_at": "2026-06-28T11:15:36+00:00",
                    "updated_at": "2026-06-29T03:43:11+00:00",
                    "opened_at": "",
                    "closed_at": "",
                }
            ],
            "orders": [],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert reflection["pnl_krw"] == pytest.approx(0)
    assert reflection["pnl_pct"] == pytest.approx(0)
    assert reflection["mfe_pct"] == pytest.approx(0)
    assert reflection["mae_pct"] == pytest.approx(0)
    assert reflection["metrics"]["pnl_status"] == "not_executed"
    assert reflection["metrics"]["pnl_status_reason"] == "error_without_open_or_exit_fill"
    assert reflection["metrics"]["realized_pnl_available"] is False
    assert reflection["metrics"]["exit_price_source"] == "none"
    assert "미체결/에러" in reflection["lesson_md"]
    assert scorecards["review_order_and_data_failures"]["status"] == "active_caution"
    assert scorecards["review_order_and_data_failures"]["action"] == "caution"


def test_due_reflections_use_precise_binance_performance_cost_components(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "binance_btc_precise_cost_closed"
    context = {
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "status": "closed",
                    "qty_initial": 0.1,
                    "entry_price": 100.0,
                    "current_price": 108.0,
                    "target_price": 110.0,
                    "stop_price": 95.0,
                    "thesis": "펀딩과 시장 비용이 확인된 선물 블록",
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "performance_reflection": {
                        "entry_price": 100.0,
                        "exit_price": 108.0,
                        "gross_pnl_usdt": 0.8,
                        "net_pnl_usdt": 0.61,
                        "fee_usdt": 0.10,
                        "funding_usdt": 0.02,
                        "slippage_usdt": 0.03,
                        "spread_usdt": 0.04,
                        "total_cost_usdt": 0.19,
                        "cost_source": "explicit",
                    },
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "sell",
                    "limit_price": 108.0,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    assert reflection["metrics"]["gross_pnl"] == pytest.approx(0.8)
    assert reflection["metrics"]["net_pnl"] == pytest.approx(0.61)
    assert reflection["pnl_krw"] == pytest.approx(0.61)
    assert reflection["pnl_pct"] == pytest.approx(6.1)
    assert reflection["metrics"]["costs"]["fee"] == pytest.approx(0.10)
    assert reflection["metrics"]["costs"]["funding"] == pytest.approx(0.02)
    assert reflection["metrics"]["costs"]["slippage"] == pytest.approx(0.03)
    assert reflection["metrics"]["costs"]["spread"] == pytest.approx(0.04)
    assert reflection["metrics"]["costs"]["total"] == pytest.approx(0.19)
    assert reflection["metrics"]["costs"]["source"] == "explicit"
    assert reflection["metrics"]["costs"]["cost_precision"] == "recorded"
    assert reflection["metrics"]["costs"]["required_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
    ]
    assert reflection["metrics"]["costs"]["present_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
    ]
    assert reflection["metrics"]["costs"]["missing_cost_components"] == []


def test_due_reflections_preserve_zero_cost_component_audit_for_binance_futures(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "binance_eth_zero_component_audit_closed"
    context = {
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "status": "closed",
                    "qty_initial": 0.2,
                    "entry_price": 100.0,
                    "current_price": 103.0,
                    "target_price": 103.0,
                    "stop_price": 97.0,
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "performance_reflection": {
                        "entry_price": 100.0,
                        "exit_price": 103.0,
                        "gross_pnl_usdt": 0.6,
                        "net_pnl_usdt": 0.58,
                        "total_cost_usdt": 0.02,
                        "cost_source": "binance_fills",
                        "cost_components": {
                            "fees": 0.02,
                            "funding": 0.0,
                            "slippage": 0.0,
                            "spread": 0.0,
                        },
                    },
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "sell",
                    "limit_price": 103.0,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    costs = reflection["metrics"]["costs"]
    assert costs["total"] == pytest.approx(0.02)
    assert costs["cost_precision"] == "recorded"
    assert costs["cost_precision_reason"] == "cost_components_audited"
    assert costs["present_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
    ]
    assert costs["missing_cost_components"] == []


def test_due_reflections_downgrade_missing_required_cost_components(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    block_id = "kis_005930_missing_spread_closed"
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80_000,
                    "current_price": 82_000,
                    "target_price": 82_000,
                    "stop_price": 77_000,
                    "closed_at": "2026-05-08T06:30:00+00:00",
                    "created_at": "2026-05-08T00:30:00+00:00",
                    "metadata": {
                        "performance": {
                            "entry_price": 80_000,
                            "exit_price": 82_000,
                            "gross_pnl_krw": 2_000,
                            "net_pnl_krw": 1_850,
                            "total_cost_krw": 150,
                            "cost_source": "kis_order_payload",
                            "cost_components": {
                                "fees": 20,
                                "taxes": 100,
                                "slippage": 30,
                            },
                        }
                    },
                }
            ],
            "orders": [
                {
                    "block_id": block_id,
                    "symbol": "005930",
                    "side": "sell",
                    "limit_price": 82_000,
                    "reason": "target_reached",
                    "created_at": "2026-05-08T06:30:00+00:00",
                }
            ],
        }
    }

    result = service.run_due_reflections(context=context)
    reflection = service.block_memory(block_id)["reflection"]

    assert result["status"] == "ok"
    costs = reflection["metrics"]["costs"]
    assert costs["cost_precision"] == "partial"
    assert costs["cost_precision_reason"] == (
        "recorded_cost_missing_required_components"
    )
    assert costs["required_cost_components"] == [
        "fees",
        "slippage",
        "spread",
        "taxes",
    ]
    assert costs["present_cost_components"] == ["fees", "slippage", "taxes"]
    assert costs["missing_cost_components"] == ["spread"]
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=30)["items"]
    }
    assert scorecards["lane_scale.cost_evidence_repair"]["source"] == (
        "cost_component_audit"
    )
    assert scorecards["lane_scale.cost_evidence_repair"]["lane_scale_evidence"][
        "scale_repair_target_counts"
    ] == {"record_missing_cost_component:spread": 1}


def test_due_reflections_promote_repeated_cost_audit_gaps_to_soft_lane_policy(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"kis_cost_gap_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "005930",
                "name": "삼성전자",
                "status": "closed",
                "qty_initial": 1,
                "entry_price": 80_000,
                "current_price": 79_000,
                "target_price": 82_000,
                "stop_price": 78_000,
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "metadata": {
                    "performance": {
                        "entry_price": 80_000,
                        "exit_price": 79_000,
                        "gross_pnl_krw": -1_000,
                        "net_pnl_krw": -1_150,
                        "total_cost_krw": 150,
                        "cost_source": "kis_order_payload",
                        "cost_components": {
                            "fees": 20,
                            "taxes": 100,
                            "slippage": 30,
                        },
                    }
                },
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "symbol": "005930",
                "side": "sell",
                "limit_price": 79_000,
                "reason": "manual_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )
    context = {"blocks": {"blocks": blocks, "orders": orders}}

    result = service.run_due_reflections(context=context)
    scorecards = {
        row["policy_id"]: row for row in service.policy_scorecards(limit=30)["items"]
    }
    rules = {
        row["policy_id"]: row for row in service.policy_rules(active_only=True)["items"]
    }

    assert result["status"] == "ok"
    scorecard = scorecards["lane_scale.cost_evidence_repair"]
    assert scorecard["status"] == "active_caution"
    assert scorecard["source"] == "cost_component_audit"
    assert scorecard["lane_scale_evidence"]["scale_blocker_counts"] == {
        "cost_evidence_repair": 3
    }
    assert scorecard["lane_scale_evidence"]["scale_repair_target_counts"] == {
        "record_missing_cost_component:spread": 3
    }
    assert scorecard["lane_scale_evidence"]["representative_gate"]["source"] == (
        "reflection_cost_audit"
    )
    rule = rules["lane_scale.cost_evidence_repair"]
    assert rule["effect"]["hard_filter"] is False
    assert rule["effect"]["entry_bias"] == "cost_verified_waiting_entry"
    assert rule["effect"]["sizing_policy"] == "reduce_cost_weak_lane"
    assert rule["effect"]["risk_budget_multiplier"] == 0.5


def test_memory_run_storage_compacts_large_reflection_context(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    giant_prompt = "manager prompt " * 40_000
    context = {
        "blocks": {
            "blocks": [
                {
                    "block_id": "blk_closed",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "closed",
                    "qty_initial": 1,
                    "entry_price": 80000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "thesis": "x" * 20_000,
                }
            ],
            "orders": [],
            "manager_runs": [{"prompt": {"huge": giant_prompt}}],
        },
        "binance_blocks": {
            "blocks": [
                {
                    "block_id": "bnb_futures_BTCUSDT_closed",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "status": "closed",
                    "qty_initial": 0.01,
                    "entry_price": 100000.0,
                    "target_price": 98000.0,
                    "stop_price": 101000.0,
                    "risk_note": "y" * 20_000,
                }
            ],
            "orders": [],
            "manager_runs": [{"prompt_json": giant_prompt}],
        },
        "jue_workflow": {"workflow_id": "block_reflection"},
    }

    run_id = service.repository.save_run(
        kind="reflection",
        slot="block_reflection",
        status="ok",
        mode="deterministic",
        model="gpt-5.5",
        error_message="",
        input_payload=context,
        output_payload={"reflections": [{"block_id": "blk_closed"}]},
    )

    with service.repository._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT input_json FROM memory_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    stored = row["input_json"]
    assert len(stored) < 60_000
    assert giant_prompt not in stored
    assert "manager_runs" not in stored
    assert "blk_closed" in stored
    assert "bnb_futures_BTCUSDT_closed" in stored


def test_repository_status_does_not_load_latest_run_payload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    giant_payload = {"context": {"raw": "x" * 500_000}}
    service.repository.save_run(
        kind="reflection",
        slot="block_reflection",
        status="ok",
        mode="deterministic",
        model="gpt-5.5",
        error_message="",
        input_payload=giant_payload,
        output_payload=giant_payload,
    )

    status = service.repository.status()
    serialized = json.dumps(status["latest_run"], ensure_ascii=False)

    assert "input" not in status["latest_run"]
    assert "output" not in status["latest_run"]
    assert len(serialized) < 1000


def test_scorecards_generate_versioned_policy_rules_and_evaluation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    blocks = []
    orders = []
    for idx in range(3):
        block_id = f"blk_005930_loss_{idx}"
        blocks.append(
            {
                "block_id": block_id,
                "symbol": "005930",
                "name": "삼성전자",
                "status": "closed",
                "qty_initial": 1,
                "entry_price": 80000,
                "current_price": 76000,
                "target_price": 84000,
                "stop_price": 76000,
                "thesis": "손절 약속 검증",
                "closed_at": "2026-05-08T06:30:00+00:00",
                "created_at": "2026-05-08T00:30:00+00:00",
                "quote": {"high_price": 80500, "low_price": 75600},
            }
        )
        orders.append(
            {
                "block_id": block_id,
                "side": "sell",
                "limit_price": 76000,
                "reason": "stop_close",
                "created_at": "2026-05-08T06:30:00+00:00",
            }
        )

    result = service.run_due_reflections(context={"blocks": {"blocks": blocks, "orders": orders}})
    rules = service.policy_rules(active_only=True)["items"]
    pack = service.context_pack(
        symbols=["005930"],
        blocks=[
            {
                "block_id": "blk_005930_open",
                "symbol": "005930",
                "status": "open",
                "entry_price": 80000,
                "stop_price": 0,
            }
        ],
        block_ids=["blk_005930_open"],
    )

    assert result["created_count"] == 3
    assert rules[0]["policy_id"] == "respect_defined_stops"
    assert rules[0]["version"] == 1
    assert rules[0]["status"] == "active_caution"
    assert Path(rules[0]["file_path"]).exists()
    assert pack["policy_rule_evaluation"]["active_rule_count"] == 1
    assert pack["policy_rule_evaluation"]["by_block"]["blk_005930_open"][0]["rule_id"].endswith("@v1")

    service.repository.upsert_policy_scorecard(
        {
            **rules[0]["source_scorecard"],
            "policy_id": "respect_defined_stops",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 4,
            "confidence": 0.7,
            "reason": "표본 4건으로 손절 약속을 더 강하게 점검한다.",
        }
    )
    service.sync_policy_rules()
    active_rules = service.policy_rules(active_only=True)["items"]
    all_rules = service.policy_rules()["items"]

    assert len(active_rules) == 1
    assert active_rules[0]["rule_id"] == "respect_defined_stops@v2"
    assert {row["status"] for row in all_rules} >= {"active_caution", "retired"}


def test_validation_attribution_policy_rules_evaluate_as_candidate_soft_caution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "validation_attribution.strategy_family.late_chase",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "win_rate": 0.0,
            "avg_pnl_pct": -2.8,
            "expectancy_pct": -2.8,
            "rule_follow_rate": 1.0,
            "confidence": 0.72,
            "reason": "late_chase 손실 귀속 3건",
            "source": "live_authority_failure_attribution",
            "attribution_group_type": "strategy_family",
            "attribution_group": "late_chase",
        }
    )
    service.sync_policy_rules()

    pack = service.context_pack(
        strategy={
            "candidates": [
                {
                    "symbol": "005930",
                    "strategy_family": "late_chase",
                }
            ]
        },
        target_scope="kis",
        max_chars=8000,
    )
    impact = next(
        row
        for row in pack["policy_rule_evaluation"]["by_symbol"]["005930"]
        if row["policy_id"] == "validation_attribution.strategy_family.late_chase"
    )

    assert impact["effect"]["entry_bias"] == "reduce_or_wait_on_repeated_attribution"
    assert impact["matched_metric"] == {
        "group_type": "strategy_family",
        "group": "late_chase",
    }


def test_memory_ingests_evidence_scorecard_as_versioned_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.ingest_evidence_scorecards(
        [
            "not-a-scorecard",  # type: ignore[list-item]
            {"scope": "binance", "confidence": 0.9},
            {
                "policy_id": "binance.breakout.observe",
                "scope": "binance",
                "fresh_count": 4,
                "confidence": 0.62,
                "expectancy_r": 0.35,
                "evidence_ids": ["ev-observe"],
            },
            {
                "policy_id": "binance.breakout.short",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": -0.25,
                "evidence_ids": ["ev-caution"],
            },
            {
                "policy_id": "binance.breakout.long",
                "scope": "binance",
                "status": "candidate",
                "fresh_count": 4,
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.35,
                "evidence_ids": ["ev1", "ev2"],
                "updated_at": "2026-05-25T00:00:00+00:00",
            }
        ]
    )
    rules_by_policy = {
        row["policy_id"]: row
        for row in service.policy_rules(active_only=False)["items"]
        if row["status"] != "retired"
    }
    active_policy_ids = {
        row["policy_id"] for row in service.policy_rules(active_only=True)["items"]
    }

    assert result["ingested"] == 3
    assert result["skipped_count"] == 2
    assert [row["reason"] for row in result["skipped"]] == [
        "invalid_scorecard",
        "invalid_policy_id",
    ]
    assert rules_by_policy["binance.breakout.observe"]["action"] == "observe"
    assert rules_by_policy["binance.breakout.observe"]["status"] == "candidate"
    assert rules_by_policy["binance.breakout.short"]["action"] == "caution"
    assert rules_by_policy["binance.breakout.short"]["status"] == "active_caution"
    assert rules_by_policy["binance.breakout.long"]["action"] == "prefer"
    assert rules_by_policy["binance.breakout.long"]["status"] == "active_preference"
    assert rules_by_policy["binance.breakout.long"]["scope"] == "binance"
    assert rules_by_policy["binance.breakout.long"]["source_scorecard"]["scope"] == "binance"
    assert rules_by_policy["binance.breakout.long"]["source_scorecard"]["evidence_ids"] == [
        "ev1",
        "ev2",
    ]
    assert active_policy_ids == {
        "binance.breakout.long",
        "binance.breakout.short",
    }

    second_result = service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "binance.breakout.long",
                "scope": "binance",
                "status": "candidate",
                "fresh_count": 4,
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.35,
                "evidence_ids": ["ev1", "ev2"],
                "updated_at": "2026-05-25T01:00:00+00:00",
            }
        ]
    )
    unchanged_long_rules = [
        row
        for row in service.policy_rules(active_only=False)["items"]
        if row["policy_id"] == "binance.breakout.long"
    ]

    assert second_result["ingested"] == 1
    assert {row["version"] for row in unchanged_long_rules} == {1}

    third_result = service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "binance.breakout.long",
                "scope": "binance",
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.35,
                "evidence_ids": ["ev3"],
                "updated_at": "2026-05-25T02:00:00+00:00",
            }
        ]
    )
    long_rules = [
        row
        for row in service.policy_rules(active_only=False)["items"]
        if row["policy_id"] == "binance.breakout.long"
    ]
    latest_long_rule = max(long_rules, key=lambda row: row["version"])

    assert third_result["ingested"] == 1
    assert {row["version"] for row in long_rules} == {1, 2}
    assert latest_long_rule["status"] == "active_preference"
    assert latest_long_rule["source_scorecard"]["evidence_ids"] == ["ev3"]


def test_sync_policy_rules_retires_resolved_scorecard_rule(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    policy_id = "manager_contract_error.kis.memory_contract_resolution_missing"
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": policy_id,
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "confidence": 0.72,
            "memory_scope": "kis",
            "transferability": "direct",
            "latest_error": "memory_contract_resolution_missing",
        }
    )
    service.sync_policy_rules()
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": policy_id,
            "status": "resolved",
            "action": "observe",
            "sample_count": 3,
            "confidence": 0.8,
            "memory_scope": "kis",
            "transferability": "direct",
            "latest_error": "memory_contract_resolution_missing",
            "resolution_status": "resolved",
        }
    )

    result = service.sync_policy_rules()
    rules = [
        row
        for row in service.policy_rules(active_only=False)["items"]
        if row["policy_id"] == policy_id
    ]
    active_rules = [
        row
        for row in service.policy_rules(active_only=True)["items"]
        if row["policy_id"] == policy_id
    ]
    non_retired_rules = [row for row in rules if row["status"] != "retired"]

    assert result["created_count"] == 0
    assert active_rules == []
    assert non_retired_rules == []
    assert {row["status"] for row in rules} == {"retired"}


def test_policy_rule_evidence_preserves_manager_workflow_provenance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    policy_id = "jue_wiki_action_reference_gap.kis.missing"
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": policy_id,
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "confidence": 0.76,
            "memory_scope": "kis",
            "transferability": "direct",
            "source": "jue_wiki_action_reference_gap",
            "reason": "KIS 매니저가 선택된 위키를 액션 근거로 남기지 않았다.",
            "workflow_ids": ["kis_intraday_manager"],
            "skill_ids": ["jue-kis-trading"],
            "contract_ids": ["jue_wiki_action_reference_contract"],
        }
    )

    service.sync_policy_rules()
    rule = service.repository.latest_policy_rule(policy_id)

    assert rule is not None
    assert rule["evidence"]["workflow_ids"] == ["kis_intraday_manager"]
    assert rule["evidence"]["skill_ids"] == ["jue-kis-trading"]
    assert rule["evidence"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]
    assert rule["source_scorecard"]["workflow_ids"] == ["kis_intraday_manager"]
    file_payload = json.loads(Path(rule["file_path"]).read_text(encoding="utf-8"))
    assert file_payload["evidence"]["workflow_ids"] == ["kis_intraday_manager"]
    assert file_payload["evidence"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]


def test_policy_rule_evaluation_exposes_workflow_provenance_in_impacts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    policy_id = "jue_wiki_action_reference_gap.kis.missing"
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": policy_id,
            "status": "active_caution",
            "action": "caution",
            "sample_count": 3,
            "confidence": 0.76,
            "memory_scope": "kis",
            "transferability": "direct",
            "source": "jue_wiki_action_reference_gap",
            "reason": "KIS 매니저가 선택된 위키를 액션 근거로 남기지 않았다.",
            "workflow_ids": ["kis_intraday_manager"],
            "skill_ids": ["jue-kis-trading"],
            "contract_ids": ["jue_wiki_action_reference_contract"],
        }
    )

    pack = service.context_pack(target_scope="kis", max_chars=12000)
    impact = next(
        row
        for row in pack["policy_rule_evaluation"]["global"]
        if row["policy_id"] == policy_id
    )

    assert impact["evidence"]["workflow_ids"] == ["kis_intraday_manager"]
    assert impact["evidence"]["skill_ids"] == ["jue-kis-trading"]
    assert impact["evidence"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]


def test_period_review_repository_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    review = service.repository.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"closed_blocks": 4, "win_rate": 0.5},
            "review_md": "이번 주는 손절 속도가 빨랐다.",
            "policy_revision_ids": ["rev_1"],
        }
    )
    revision = service.repository.upsert_policy_revision(
        {
            "revision_id": "rev_1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "policy_id": "slow_down_mid_term_stops",
            "action": "create",
            "status": "candidate",
            "scope": "mid",
            "condition": {"horizon": "mid"},
            "effect": {"stop_review": "less_intraday_noise"},
            "evidence": {"sample_count": 4},
            "reason_md": "중기 블록이 일중 노이즈에 너무 빨리 닫혔다.",
            "confidence": 0.71,
        }
    )
    outcome = service.repository.upsert_policy_outcome(
        {
            "policy_id": "slow_down_mid_term_stops",
            "rule_id": "slow_down_mid_term_stops@v1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "sample_count": 4,
            "avg_pnl_pct": 1.2,
            "win_rate": 0.5,
            "expectancy_pct": 1.2,
            "max_drawdown_pct": -2.1,
            "rule_follow_rate": 0.75,
            "helped_count": 2,
            "hurt_count": 1,
            "notes_md": "손절 완화가 일부 도움이 됐다.",
        }
    )
    service.repository.upsert_policy_outcome(
        {
            "policy_id": "other_policy",
            "rule_id": "other_policy@v1",
            "period_key": "2026-W20",
            "period_type": "weekly",
            "sample_count": 1,
        }
    )
    fetched_outcome = service.repository.get_policy_outcome(
        policy_id="slow_down_mid_term_stops",
        rule_id="slow_down_mid_term_stops@v1",
        period_key="2026-W21",
        period_type="weekly",
    )

    assert review["period_key"] == "2026-W21"
    assert revision["policy_id"] == "slow_down_mid_term_stops"
    assert outcome["helped_count"] == 2
    assert fetched_outcome is not None
    assert fetched_outcome["policy_id"] == "slow_down_mid_term_stops"
    assert service.repository.latest_period_review("weekly")["period_key"] == "2026-W21"
    assert service.repository.list_period_reviews(period_type="weekly", limit=5)[0]["review_md"].startswith("이번 주")
    assert service.repository.list_policy_revisions(limit=5)[0]["revision_id"] == "rev_1"
    assert {row["policy_id"] for row in service.repository.list_policy_outcomes(limit=5)} >= {
        "slow_down_mid_term_stops",
        "other_policy",
    }


def test_period_review_repository_rejects_missing_primary_keys(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    with pytest.raises(ValueError, match="period_key and period_type required"):
        service.repository.upsert_period_review({"period_key": "2026-W21"})
    with pytest.raises(ValueError, match="period_key and period_type required"):
        service.repository.upsert_period_review(
            {"period_key": "   ", "period_type": "weekly"}
        )
    with pytest.raises(ValueError, match="revision_id required"):
        service.repository.upsert_policy_revision({"policy_id": "missing_revision_id"})
    with pytest.raises(ValueError, match="revision_id required"):
        service.repository.upsert_policy_revision({"revision_id": "   "})
    with pytest.raises(ValueError, match="policy_id, rule_id, period_key, and period_type required"):
        service.repository.upsert_policy_outcome(
            {
                "policy_id": "slow_down_mid_term_stops",
                "rule_id": "slow_down_mid_term_stops@v1",
                "period_key": "2026-W21",
            }
        )
    with pytest.raises(ValueError, match="policy_id, rule_id, period_key, and period_type required"):
        service.repository.upsert_policy_outcome(
            {
                "policy_id": "slow_down_mid_term_stops",
                "rule_id": "   ",
                "period_key": "2026-W21",
                "period_type": "weekly",
            }
        )


def test_period_review_metrics_split_source_and_horizon(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_short_win",
            "symbol": "005930",
            "name": "삼성전자",
            "status": "closed",
            "exit_reason": "target_reached",
            "pnl_pct": 2.0,
            "rule_followed": True,
            "lesson_md": "단기 목표가 준수",
            "metrics": {
                "horizon": "short",
                "created_by": "jue",
                "holding_minutes": 80,
                "outcome_date": "2026-05-19",
                "policy_id": "protect_winning_blocks",
            },
        },
        source_run_id=1,
    )
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_mid_loss",
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -1.5,
            "rule_followed": True,
            "lesson_md": "중기 블록 손절이 빨랐다",
            "metrics": {
                "horizon": "mid",
                "created_by": "user",
                "closed_at": "2026-05-22T00:30:00+00:00",
                "holding_minutes": 35,
                "policy_id": "respect_defined_stops",
            },
        },
        source_run_id=1,
    )
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_next_week_win",
            "symbol": "000660",
            "name": "SK하이닉스",
            "status": "closed",
            "exit_reason": "target_reached",
            "pnl_pct": 9.0,
            "rule_followed": True,
            "lesson_md": "다음 주 성과",
            "metrics": {
                "horizon": "short",
                "created_by": "jue",
                "outcome_date": "2026-05-25",
                "policy_id": "protect_winning_blocks",
            },
        },
        source_run_id=1,
    )

    metrics = service.build_period_metrics(
        period_type="weekly",
        period_key="2026-W21",
        start_date="2026-05-18",
        end_date="2026-05-22",
    )

    assert metrics["closed_blocks"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["avg_pnl_pct"] == 0.25
    assert metrics["by_horizon"]["short"]["sample_count"] == 1
    assert metrics["by_horizon"]["mid"]["avg_pnl_pct"] == -1.5
    assert metrics["by_source"]["user"]["sample_count"] == 1
    assert metrics["policy_impacts"]["respect_defined_stops"]["sample_count"] == 1


def test_weekly_and_monthly_review_run_once_creates_review_and_revision(
    tmp_path: Path,
) -> None:
    class _ReviewLLM:
        resolved_model = "gpt-5.5"
        ready = True

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def complete(self, payload, timeout_ms=None) -> dict:
            _ = timeout_ms
            self.calls.append(payload)
            return {
                "ok": True,
                "content": json.dumps(
                    {
                        "review_title": "주간 반성",
                        "review_md": "중기 블록 손절이 빠르게 나갔다.",
                        "observations": ["중기 블록은 일중 노이즈와 분리해서 본다."],
                        "policy_revisions": [
                            {
                                "policy_id": "slow_down_mid_term_stops",
                                "action": "create",
                                "scope": "mid",
                                "condition": {"horizon": "mid"},
                                "effect": {"stop_review": "confirm_with_daily_context"},
                                "reason_md": "중기 블록의 손절 판단은 장중 흔들림만으로 결정하지 않는다.",
                                "confidence": 0.72,
                            }
                        ],
                        "memory_updates": {"notes": []},
                    },
                    ensure_ascii=False,
                ),
                "model": "gpt-5.5",
            }

    llm = _ReviewLLM()
    service = _service(tmp_path, codex_runtime=llm)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_mid_loss",
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -1.5,
            "rule_followed": True,
            "lesson_md": "중기 블록 손절이 빠름",
            "metrics": {
                "horizon": "mid",
                "created_by": "user",
                "outcome_date": "2026-05-22",
                "policy_id": "respect_defined_stops",
            },
        },
        source_run_id=1,
    )
    for index in range(2):
        service.repository.upsert_block_reflection(
            {
                "block_id": f"blk_short_win_{index}",
                "symbol": "005930",
                "name": "삼성전자",
                "status": "closed",
                "exit_reason": "target_reached",
                "pnl_pct": 1.1 + index,
                "rule_followed": True,
                "lesson_md": "단기 목표 준수",
                "metrics": {
                    "horizon": "short",
                    "created_by": "jue",
                    "outcome_date": "2026-05-21",
                    "policy_id": "protect_winning_blocks",
                },
            },
            source_run_id=1,
        )

    result = asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
            force=True,
        )
    )
    skipped = asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
        )
    )
    monthly_window = service.period_window(
        period_type="monthly",
        now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
    )

    assert result["status"] == "ok"
    assert result["period_type"] == "weekly"
    assert result["review"]["period_key"] == "2026-W21"
    assert result["review"]["mode"] == "llm"
    assert result["revision_count"] == 1
    assert result["revisions"][0]["policy_id"] == "slow_down_mid_term_stops"
    assert result["revisions"][0]["status"] == "active_caution"
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "period_review_already_exists"
    assert monthly_window == {
        "period_type": "monthly",
        "period_key": "2026-05",
        "start_date": "2026-05-01",
        "end_date": "2026-05-31",
    }
    assert llm.calls


def test_weekly_historical_replay_creates_replay_and_memory_revision(
    tmp_path: Path,
) -> None:
    class _ReplayLLM:
        resolved_model = "gpt-5.5"
        ready = True

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def complete(self, payload, timeout_ms=None) -> dict:
            _ = timeout_ms
            self.calls.append(payload)
            return {
                "ok": True,
                "content": json.dumps(
                    {
                        "replay_title": "주간 의사결정 리플레이",
                        "replay_md": "as_of 기준으로 보면 중기 블록 손절폭이 너무 좁았다.",
                        "case_reviews": [
                            {
                                "case_id": "blk_mid_loss",
                                "as_of": "2026-05-20T00:00:00+09:00",
                                "replay_decision": "중기 유지, 당일 저가 손절은 보류",
                                "outcome_review": "이후 반등이 있었으므로 손절 기준 재검토",
                                "lesson": "중기 블록은 장중 노이즈와 종가 훼손을 구분한다.",
                            }
                        ],
                        "policy_revisions": [
                            {
                                "policy_id": "replay_mid_stop_review",
                                "action": "create",
                                "scope": "mid",
                                "condition": {"horizon": "mid"},
                                "effect": {"stop_review": "confirm_close_damage"},
                                "reason_md": "리플레이에서 중기 블록의 장중 노이즈 손절 문제가 반복 확인됐다.",
                                "confidence": 0.7,
                            }
                        ],
                        "memory_updates": {
                            "notes": [
                                {
                                    "key": "regime:weekly_replay",
                                    "summary": "as_of 리플레이 결과 중기 손절 기준을 더 넓게 검토한다.",
                                    "confidence": 0.7,
                                }
                            ],
                            "blocks": [
                                {
                                    "block_id": "blk_mid_loss",
                                    "summary": "과거 시점 리플레이에서 중기 블록으로 유지했어야 할 가능성이 확인됐다.",
                                    "confidence": 0.7,
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                "model": "gpt-5.5",
            }

    llm = _ReplayLLM()
    service = _service(tmp_path, codex_runtime=llm)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_mid_loss",
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -1.5,
            "rule_followed": True,
            "lesson_md": "중기 블록 손절이 빨랐다.",
            "metrics": {
                "horizon": "mid",
                "created_by": "jue",
                "outcome_date": "2026-05-22",
                "as_of": "2026-05-20T00:00:00+09:00",
                "entry_thesis": "로봇 섹터 중기 눌림목",
                "research_snapshot": {"summary": "로봇 수급 개선"},
                "outcome_summary": "손절 뒤 반등",
                "policy_id": "respect_defined_stops",
            },
        },
        source_run_id=1,
    )

    result = asyncio.run(
        service.run_historical_replay(
            period_type="weekly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
            force=True,
        )
    )
    skipped = asyncio.run(
        service.run_historical_replay(
            period_type="weekly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
            force=False,
        )
    )
    latest = service.latest_historical_replay("weekly")
    pack = service.context_pack(block_ids=["blk_mid_loss"], max_chars=6000)

    assert result["status"] == "ok"
    assert result["period_key"] == "2026-W21"
    assert result["case_count"] == 1
    assert result["revision_count"] == 1
    assert result["revisions"][0]["policy_id"] == "replay_mid_stop_review"
    assert latest["replay_md"].startswith("as_of 기준")
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "historical_replay_already_exists"
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])
    assert prompt["future_data_guard"].startswith("decision_context_as_of")
    assert prompt["cases"][0]["decision_context_as_of"]["research_snapshot"] == {
        "summary": "로봇 수급 개선"
    }
    assert "outcome_after_as_of" in prompt["cases"][0]
    assert "weekly_replay" in json.dumps(pack, ensure_ascii=False)


def test_historical_replay_cases_are_filtered_by_memory_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_005930_kis_replay_case",
            "symbol": "005930",
            "name": "삼성전자",
            "status": "closed",
            "exit_reason": "target_reached",
            "pnl_pct": 2.2,
            "rule_followed": True,
            "lesson_md": "KIS 눌림목 성공",
            "metrics": {
                "memory_scope": "kis",
                "transferability": "direct",
                "outcome_date": "2026-05-22",
                "as_of": "2026-05-20T09:30:00+09:00",
            },
        },
        source_run_id=1,
    )
    service.repository.upsert_block_reflection(
        {
            "block_id": "bnb_futures_BTCUSDT_replay_case",
            "symbol": "BTCUSDT",
            "name": "BTCUSDT",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -3.4,
            "rule_followed": True,
            "lesson_md": "Binance 변동성 손절",
            "metrics": {
                "memory_scope": "binance",
                "transferability": "direct",
                "outcome_date": "2026-05-22",
                "as_of": "2026-05-20T00:30:00+00:00",
            },
        },
        source_run_id=1,
    )
    window = {
        "period_type": "weekly",
        "period_key": "2026-W21",
        "start_date": "2026-05-18",
        "end_date": "2026-05-22",
    }

    kis_cases = service.build_historical_replay_cases(
        window=window,
        context={"memory_scope": "kis"},
        limit=8,
    )
    binance_cases = service.build_historical_replay_cases(
        window=window,
        context={"memory_scope": "binance"},
        limit=8,
    )

    assert [row["case_id"] for row in kis_cases] == ["blk_005930_kis_replay_case"]
    assert [row["case_id"] for row in binance_cases] == [
        "bnb_futures_BTCUSDT_replay_case"
    ]


def test_period_metrics_are_filtered_by_memory_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    common = {
        "status": "closed",
        "exit_reason": "target_reached",
        "rule_followed": True,
        "metrics": {"outcome_date": "2026-05-22"},
    }
    service.repository.upsert_block_reflection(
        {
            **common,
            "block_id": "blk_005930_kis_metric_case",
            "symbol": "005930",
            "pnl_pct": 2.0,
            "lesson_md": "KIS metric",
            "metrics": {
                **common["metrics"],
                "memory_scope": "kis",
                "transferability": "direct",
            },
        },
        source_run_id=1,
    )
    service.repository.upsert_block_reflection(
        {
            **common,
            "block_id": "bnb_futures_BTCUSDT_metric_case",
            "symbol": "BTCUSDT",
            "pnl_pct": -4.0,
            "lesson_md": "Binance metric",
            "metrics": {
                **common["metrics"],
                "memory_scope": "binance",
                "transferability": "direct",
            },
        },
        source_run_id=1,
    )

    kis_metrics = service.build_period_metrics(
        period_type="weekly",
        period_key="2026-W21",
        start_date="2026-05-18",
        end_date="2026-05-22",
        target_scope="kis",
    )
    binance_metrics = service.build_period_metrics(
        period_type="weekly",
        period_key="2026-W21",
        start_date="2026-05-18",
        end_date="2026-05-22",
        target_scope="binance",
    )

    assert kis_metrics["memory_scope"] == "kis"
    assert kis_metrics["closed_blocks"] == 1
    assert kis_metrics["avg_pnl_pct"] == pytest.approx(2.0)
    assert binance_metrics["memory_scope"] == "binance"
    assert binance_metrics["closed_blocks"] == 1
    assert binance_metrics["avg_pnl_pct"] == pytest.approx(-4.0)


def test_period_review_records_error_when_llm_unavailable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    result = asyncio.run(
        service.run_period_review(
            period_type="monthly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
            force=True,
        )
    )

    assert result["status"] == "error"
    assert result["period_key"] == "2026-05"
    assert result["review"]["mode"] == "error"
    assert result["review"]["review_md"] == ""
    assert result["review"]["policy_revision_ids"] == []


def test_period_review_retries_after_llm_unavailable_without_force(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, codex_runtime=_FailingLLM("temporary outage"))
    service.initialize()
    now = datetime(2026, 5, 24, 20, 30, tzinfo=KST)

    first = asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=now,
            force=True,
        )
    )
    service.codex_runtime = _FakeLLM(
        {
            "review_title": "주간 재시도",
            "review_md": "LLM 재시도 후 주간 리뷰를 정상 생성했다.",
            "observations": [],
            "policy_revisions": [],
            "memory_updates": {"notes": []},
        }
    )  # type: ignore[assignment]
    retry = asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=now,
            force=False,
        )
    )

    assert first["status"] == "error"
    assert retry["status"] == "ok"
    assert retry["review"]["period_key"] == "2026-W21"
    assert retry["review"]["mode"] == "llm"
    assert retry["review"]["review_md"] == "LLM 재시도 후 주간 리뷰를 정상 생성했다."


def test_policy_revision_rejects_hard_filter_and_syncs_active_rule(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    safe = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_mid_user_positions",
                "action": "create",
                "scope": "user_position",
                "condition": {"created_by": "user"},
                "effect": {"horizon_bias": "mid", "position_sizing": "review"},
                "reason_md": "사용자가 직접 산 보유분은 단기 노이즈보다 중기 thesis를 먼저 확인한다.",
                "confidence": 0.72,
            },
            {
                "policy_id": "ban_all_loss_after_open",
                "action": "create",
                "scope": "short",
                "condition": {"time": "09:00"},
                "effect": {"hard_filter": True, "ban": True},
                "reason_md": "하드 필터는 허용하지 않는다.",
                "confidence": 0.9,
            },
        ],
        period_type="weekly",
        period_key="2026-W21",
        metrics={"closed_blocks": 5},
    )

    by_id = {row["policy_id"]: row for row in safe}
    result = service._sync_revisions_to_policy_rules()
    rules = service.policy_rules(active_only=True)["items"]
    pack = service.context_pack(max_chars=5000)

    assert len(safe) == 2
    assert by_id["prefer_mid_user_positions"]["status"] == "active_caution"
    assert by_id["ban_all_loss_after_open"]["status"] == "rejected"
    assert by_id["ban_all_loss_after_open"]["effect"]["hard_filter"] is False
    assert result["created_count"] == 1
    assert rules[0]["policy_id"] == "prefer_mid_user_positions"
    assert rules[0]["source_scorecard"]["source"] == "policy_revision"
    assert rules[0]["effect"]["hard_filter"] is False
    assert rules[0]["effect"]["safety_gate_override"] is False
    assert pack["policy_rules"][0]["policy_id"] == "prefer_mid_user_positions"
    assert pack["safety_note"] == (
        "Memory guides live trading decisions. Kill switch, cash limits, "
        "position limits, and duplicate-order guards always override memory policies."
    )


def test_policy_revision_sync_collapses_duplicate_active_revisions_by_policy(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    first = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_mid_user_positions",
                "action": "create",
                "scope": "user_position",
                "condition": {"created_by": "user"},
                "effect": {"horizon_bias": "mid"},
                "reason_md": "첫 번째 후보",
                "confidence": 0.67,
            }
        ],
        period_type="weekly",
        period_key="2026-W20",
        metrics={"closed_blocks": 4},
    )[0]
    second = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_mid_user_positions",
                "action": "strengthen",
                "scope": "user_position",
                "condition": {"created_by": "user"},
                "effect": {"horizon_bias": "mid", "position_sizing": "review"},
                "reason_md": "두 번째 후보가 더 최근이고 더 강한 근거다.",
                "confidence": 0.78,
            }
        ],
        period_type="weekly",
        period_key="2026-W21",
        metrics={"closed_blocks": 5},
    )[0]

    first_result = service._sync_revisions_to_policy_rules()
    second_result = service._sync_revisions_to_policy_rules()
    service.policy_rules(active_only=True)
    third_result = service._sync_revisions_to_policy_rules()
    all_rules = service.policy_rules()["items"]
    active_rules = service.policy_rules(active_only=True)["items"]
    revisions = service.repository.list_policy_revisions(status="active_caution", limit=5)

    assert first["status"] == "active_caution"
    assert second["status"] == "active_caution"
    assert first_result["created_count"] == 1
    assert second_result["created_count"] == 0
    assert third_result["created_count"] == 0
    assert len(active_rules) == 1
    assert active_rules[0]["version"] == 1
    assert active_rules[0]["source_scorecard"]["revision_id"] == second["revision_id"]
    assert active_rules[0]["reason"] == "두 번째 후보가 더 최근이고 더 강한 근거다."
    assert [row["rule_id"] for row in all_rules] == ["prefer_mid_user_positions@v1"]
    assert revisions[0]["revision_id"] == second["revision_id"]


def test_policy_revisions_and_rules_are_scoped_by_trading_venue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    kis_revision = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_patient_entries",
                "action": "create",
                "scope": "mid",
                "condition": {"entry_style": "waiting"},
                "effect": {"entry_bias": "patient_value_wait"},
                "reason_md": "KIS 중기 블록은 급등 추격보다 눌림 대기가 나았다.",
                "confidence": 0.78,
            }
        ],
        period_type="weekly",
        period_key="2026-W31",
        metrics={"closed_blocks": 6, "memory_scope": "kis"},
    )[0]
    binance_revision = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_patient_entries",
                "action": "create",
                "scope": "mid",
                "condition": {"entry_style": "waiting"},
                "effect": {"entry_bias": "crypto_pullback_wait"},
                "reason_md": "Binance 변동성 lane은 오더북 확인 후 눌림 대기가 나았다.",
                "confidence": 0.78,
            }
        ],
        period_type="weekly",
        period_key="2026-W31",
        metrics={"closed_blocks": 6, "memory_scope": "binance"},
    )[0]

    sync = service.sync_policy_rules()
    active_rules = service.policy_rules(active_only=True)["items"]
    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)

    assert kis_revision["memory_scope"] == "kis"
    assert binance_revision["memory_scope"] == "binance"
    assert kis_revision["revision_id"] != binance_revision["revision_id"]
    assert sync["created_count"] == 2
    assert {row["memory_scope"] for row in active_rules} >= {"kis", "binance"}
    assert {row["source_scorecard"]["memory_scope"] for row in active_rules} >= {
        "kis",
        "binance",
    }
    assert {row["memory_scope"] for row in kis_pack["policy_revisions"]} == {"kis"}
    assert {row["memory_scope"] for row in binance_pack["policy_revisions"]} == {
        "binance"
    }


def test_policy_outcomes_are_scoped_by_trading_venue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_policy_outcome(
        {
            "policy_id": "shared_patient_entries",
            "rule_id": "shared_patient_entries@v1",
            "memory_scope": "kis",
            "period_key": "2026-W31",
            "period_type": "weekly",
            "sample_count": 4,
            "avg_pnl_pct": 1.4,
            "win_rate": 0.5,
            "expectancy_pct": 1.4,
            "notes_md": "KIS 성과",
        }
    )
    service.repository.upsert_policy_outcome(
        {
            "policy_id": "shared_patient_entries",
            "rule_id": "shared_patient_entries@v1",
            "memory_scope": "binance",
            "period_key": "2026-W31",
            "period_type": "weekly",
            "sample_count": 7,
            "avg_pnl_pct": -0.8,
            "win_rate": 0.2,
            "expectancy_pct": -0.8,
            "notes_md": "Binance 성과",
        }
    )

    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)

    assert {row["memory_scope"] for row in kis_pack["policy_outcomes"]} == {"kis"}
    assert kis_pack["policy_outcomes"][0]["notes_md"] == "KIS 성과"
    assert {row["memory_scope"] for row in binance_pack["policy_outcomes"]} == {
        "binance"
    }
    assert binance_pack["policy_outcomes"][0]["notes_md"] == "Binance 성과"


def test_policy_rule_sync_does_not_ping_pong_between_scorecard_and_revision(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_policy_scorecard(
        {
            "policy_id": "respect_defined_stops",
            "status": "active_caution",
            "action": "caution",
            "sample_count": 9,
            "avg_pnl_pct": -1.2,
            "win_rate": 0.0,
            "expectancy_pct": -1.2,
            "rule_follow_rate": 0.5,
            "confidence": 0.9,
            "reason": "scorecard 기반 손절 점검",
        }
    )
    revision = service._save_policy_revisions(
        [
            {
                "policy_id": "respect_defined_stops",
                "action": "weaken",
                "scope": "core_etf",
                "condition": {"stop_context": "review"},
                "effect": {"on_stop_hit": "manager_review"},
                "reason_md": "리플레이 기반 손절 정책 보정",
                "confidence": 0.82,
            }
        ],
        period_type="weekly_replay",
        period_key="2026-W23:weekly_replay",
        metrics={"closed_blocks": 8},
    )[0]

    first = service.sync_policy_rules()
    second = service.sync_policy_rules()
    service.policy_rules(active_only=True)
    third = service.sync_policy_rules()
    all_rules = service.policy_rules()["items"]
    active_rules = service.policy_rules(active_only=True)["items"]

    assert revision["status"] == "active_caution"
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert third["created_count"] == 0
    assert len(active_rules) == 1
    assert len(all_rules) == 1
    assert active_rules[0]["rule_id"] == "respect_defined_stops@v1"
    assert active_rules[0]["source_scorecard"]["source"] == "policy_revision"
    assert active_rules[0]["source_scorecard"]["revision_id"] == revision["revision_id"]


def test_runtime_storage_compaction_prunes_old_retired_policy_rules(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    rules_dir = tmp_path / "memory" / "policies" / "rules"
    for version in range(1, 7):
        file_path = rules_dir / f"respect_defined_stops_v{version}.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("{}", encoding="utf-8")
        service.repository.upsert_policy_rule(
            {
                "policy_id": "respect_defined_stops",
                "version": version,
                "rule_id": f"respect_defined_stops@v{version}",
                "status": "active_caution" if version == 6 else "retired",
                "action": "caution",
                "condition": {"version": version},
                "effect": {"policy_mode": "soft"},
                "reason": f"v{version}",
                "file_path": str(file_path),
            }
        )

    result = service.compact_runtime_storage(
        policy_retired_keep=2,
        vacuum=False,
    )
    rows = service.repository.list_policy_rules(limit=10)
    versions = sorted(
        [
            row["version"]
            for row in rows
            if row["policy_id"] == "respect_defined_stops"
        ],
        reverse=True,
    )

    assert result["status"] == "ok"
    assert result["policy_rules"]["deleted_count"] == 3
    assert versions == [6, 5, 4]
    assert not (rules_dir / "respect_defined_stops_v1.json").exists()
    assert not (rules_dir / "respect_defined_stops_v2.json").exists()
    assert not (rules_dir / "respect_defined_stops_v3.json").exists()
    assert (rules_dir / "respect_defined_stops_v6.json").exists()


def test_runtime_storage_compaction_compacts_processed_validation_events(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    payload = {
        "venue": "binance",
        "run_id": "validation-binance-large",
        "computed_at": "2026-06-18T00:00:00+00:00",
        "summary": {"fail_count": 1, "warn_count": 0, "pass_count": 18},
        "weak_disciplines": [
            {
                "id": "cost_simulation",
                "label": "거래비용",
                "status": "fail",
                "action": "비용 근거 보강",
            }
        ],
        "remediation_plan": {
            "status": "needs_repair",
            "primary_next_action": "cost evidence",
            "active_revision_evidence": {
                "active_sample_count": 2,
                "all_revision_sample_count": 10,
                "authority_posture": "probe",
                "active_revision_sample_building_failed_discipline_ids": [
                    "cost_simulation"
                ],
                "huge_blob": "x" * 12000,
            },
            "work_queue": [
                {
                    "repair_action_id": "repair-cost",
                    "discipline_id": "cost_simulation",
                    "status": "fail",
                    "lane_policy_hint": "cost_verified_waiting_entry",
                    "pass_path": {
                        "current_gap": "missing_cost",
                        "collection_hook": "sync_costs",
                        "pass_criteria": "fee evidence captured",
                        "huge_blob": "y" * 12000,
                    },
                }
            ],
        },
    }
    event = service.repository.save_memory_event(
        event_key="trading_validation:binance:large",
        event_type="trading_validation_signal",
        block_id="__system__",
        status="pending",
        payload=payload,
    )
    service.repository.mark_memory_event_processed(event["event_key"])
    before = service.repository.get_memory_event(event["event_key"])
    before_len = len(json.dumps(before["payload"], ensure_ascii=False))

    result = service.compact_runtime_storage(
        validation_event_min_payload_chars=1000,
        validation_event_max_rows=10,
        vacuum=False,
    )
    after = service.repository.get_memory_event(event["event_key"])
    after_len = len(json.dumps(after["payload"], ensure_ascii=False))
    backlog = service.validation_repair_backlog(target_scope="binance", limit=5)

    assert result["validation_events"]["compacted_count"] == 1
    assert after["payload"]["compaction_version"] == "memory_event_validation_v2"
    assert after_len < before_len
    assert after["payload"]["weak_disciplines"][0]["id"] == "cost_simulation"
    assert (
        after["payload"]["remediation_plan"]["work_queue"][0]["pass_path"][
            "collection_hook"
        ]
        == "sync_costs"
    )
    assert any(
        row["discipline_id"] == "cost_simulation"
        for row in backlog.get("items", [])
    )


def test_runtime_storage_compaction_prunes_old_processed_validation_events_by_venue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    for venue in ("kis", "binance"):
        for index in range(5):
            event_key = f"trading_validation:{venue}:run-{index}"
            service.repository.save_memory_event(
                event_key=event_key,
                event_type="trading_validation_signal",
                block_id="__system__",
                status="pending",
                payload={
                    "venue": venue,
                    "run_id": f"run-{index}",
                    "summary": {"fail_count": index},
                    "weak_disciplines": [
                        {
                            "id": "cost_simulation",
                            "label": "거래비용",
                            "status": "fail",
                            "action": "비용 근거 보강",
                        }
                    ],
                },
            )
            service.repository.mark_memory_event_processed(event_key)
            with service.repository._connect() as conn:  # noqa: SLF001
                conn.execute(
                    """
                    UPDATE memory_events
                    SET created_at = ?
                    WHERE event_key = ?
                    """,
                    (f"2026-06-18T00:0{index}:00+00:00", event_key),
                )

    result = service.compact_runtime_storage(
        validation_event_retained_rows_per_venue=2,
        vacuum=False,
    )
    events = service.repository.list_memory_events(status="processed", limit=20)
    remaining_keys = {row["event_key"] for row in events}

    assert result["validation_event_retention"]["deleted_count"] == 6
    assert result["validation_event_retention"]["venues"]["kis"] == {
        "kept_count": 2,
        "deleted_count": 3,
    }
    assert result["validation_event_retention"]["venues"]["binance"] == {
        "kept_count": 2,
        "deleted_count": 3,
    }
    assert remaining_keys == {
        "trading_validation:kis:run-3",
        "trading_validation:kis:run-4",
        "trading_validation:binance:run-3",
        "trading_validation:binance:run-4",
    }


def test_runtime_storage_compaction_compacts_old_memory_run_payloads_by_group(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    run_ids: list[int] = []

    for index in range(4):
        run_id = service.repository.save_run(
            kind="reflection",
            slot="block_reflection",
            status="ok",
            mode="llm",
            model="gpt-5.5",
            error_message="",
            input_payload={
                "task": "reflect block",
                "context": {"giant": "x" * 60_000, "index": index},
            },
            output_payload={
                "reflection": "y" * 50_000,
                "index": index,
            },
        )
        run_ids.append(run_id)
        with service.repository._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE memory_runs SET run_at = ? WHERE id = ?",
                (f"2026-06-18T00:0{index}:00+00:00", run_id),
            )
    with service.repository._connect() as conn:  # noqa: SLF001
        before_rows = conn.execute(
            """
            SELECT id, input_json, output_json
            FROM memory_runs
            WHERE id IN (?, ?, ?, ?)
            ORDER BY id
            """,
            tuple(run_ids),
        ).fetchall()
    before_recent = [
        (len(row["input_json"]), len(row["output_json"]))
        for row in before_rows[2:]
    ]

    result = service.compact_runtime_storage(
        memory_run_recent_rows_per_group=2,
        memory_run_min_payload_chars=1000,
        vacuum=False,
    )
    with service.repository._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            """
            SELECT id, input_json, output_json
            FROM memory_runs
            WHERE id IN (?, ?, ?, ?)
            ORDER BY id
            """,
            tuple(run_ids),
        ).fetchall()

    compacted_old = [
        (len(row["input_json"]), len(row["output_json"]))
        for row in rows[:2]
    ]
    detailed_recent = [
        (len(row["input_json"]), len(row["output_json"]))
        for row in rows[2:]
    ]

    assert result["memory_runs"]["compacted_count"] == 2
    assert all(
        input_len < 8_000 and output_len < 10_000
        for input_len, output_len in compacted_old
    )
    assert detailed_recent == before_recent


def test_runtime_storage_compaction_compacts_old_symbol_analysis_raw_payloads(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    saved_ids: list[int] = []

    for index in range(5):
        saved = service.repository.save_symbol_analysis(
            {
                "symbol": "033790",
                "name": "피노",
                "trigger": "daily_random_deep_research",
                "source": "instant",
                "model": "gpt-5.5",
                "status": "ok",
                "summary": f"분석 {index}",
                "reasons": ["수급", "밸류"],
                "risks": ["변동성"],
                "snapshot": {"quote": {"price": 13660}, "raw": "s" * 40_000},
                "prompt": {"instructions": "p" * 60_000, "index": index},
                "raw_response": {"content": "r" * 50_000, "index": index},
                "created_at": f"2026-06-18T00:0{index}:00+00:00",
            }
        )
        saved_ids.append(int(saved["id"]))

    result = service.compact_runtime_storage(
        symbol_analysis_recent_rows_per_symbol=2,
        symbol_analysis_min_payload_chars=1000,
        vacuum=False,
    )
    history = service.repository.list_symbol_analyses("033790", limit=5)
    with service.repository._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            """
            SELECT id, prompt_json, snapshot_json, raw_response_json
            FROM symbol_analyses
            WHERE id IN (?, ?, ?, ?, ?)
            ORDER BY id
            """,
            tuple(saved_ids),
        ).fetchall()

    compacted_old = [
        (
            len(row["prompt_json"]),
            len(row["snapshot_json"]),
            len(row["raw_response_json"]),
        )
        for row in rows[:3]
    ]
    detailed_recent = [
        (
            len(row["prompt_json"]),
            len(row["snapshot_json"]),
            len(row["raw_response_json"]),
        )
        for row in rows[3:]
    ]

    assert result["symbol_analyses"]["compacted_count"] == 0
    assert history["items"][0]["summary"] == "분석 4"
    assert history["items"][-1]["reasons"] == ["수급", "밸류"]
    assert all(max(lengths) < 8_000 for lengths in compacted_old)
    assert all(max(lengths) < 8_000 for lengths in detailed_recent)


def test_symbol_analysis_storage_caps_payloads_on_save(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    saved = service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "trigger": "daily_random_deep_research",
            "source": "instant",
            "model": "gpt-5.5",
            "status": "ok",
            "summary": "거대한 근거를 저장해도 원문 비만은 만들지 않는다.",
            "snapshot": {
                "quote": {"price": 13660},
                "recent_history": [
                    {
                        "summary": "이전 분석",
                        "snapshot": {"nested": "OLD_SNAPSHOT_CONTEXT " * 50_000},
                    }
                ],
                "raw": "RAW_SNAPSHOT_CONTEXT " * 50_000,
            },
            "prompt": {"instructions": "PROMPT_CONTEXT " * 50_000},
            "raw_response": {"content": "RESPONSE_CONTEXT " * 50_000},
        }
    )

    with service.repository._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            """
            SELECT prompt_json, snapshot_json, raw_response_json
            FROM symbol_analyses
            WHERE id = ?
            """,
            (saved["id"],),
        ).fetchone()

    assert len(row["prompt_json"]) < 8_000
    assert len(row["snapshot_json"]) < 8_000
    assert len(row["raw_response_json"]) < 8_000
    stored_snapshot = json.loads(row["snapshot_json"])
    assert stored_snapshot["quote"]["price"] == 13660
    assert "RAW_SNAPSHOT_CONTEXT" not in row["snapshot_json"]
    assert "OLD_SNAPSHOT_CONTEXT" not in row["snapshot_json"]
    assert "PROMPT_CONTEXT" not in row["prompt_json"]
    assert "RESPONSE_CONTEXT" not in row["raw_response_json"]


def test_runtime_storage_compaction_compacts_oversized_recent_symbol_analyses(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    saved_ids: list[int] = []
    for index in range(3):
        saved = service.repository.save_symbol_analysis(
            {
                "symbol": "033790",
                "name": "피노",
                "summary": f"분석 {index}",
                "snapshot": {"quote": {"price": 13000 + index}},
                "prompt": {"index": index},
                "raw_response": {"index": index},
                "created_at": f"2026-06-18T00:0{index}:00+00:00",
            }
        )
        saved_ids.append(int(saved["id"]))
    newest_id = saved_ids[-1]
    huge_snapshot = json.dumps(
        {
            "quote": {"price": 13660},
            "recent_history": [{"snapshot": {"raw": "R" * 300_000}}],
        },
        ensure_ascii=False,
    )
    with service.repository._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE symbol_analyses SET snapshot_json = ? WHERE id = ?",
            (huge_snapshot, newest_id),
        )

    result = service.compact_runtime_storage(
        symbol_analysis_recent_rows_per_symbol=3,
        symbol_analysis_min_payload_chars=1000,
        vacuum=False,
    )
    with service.repository._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT snapshot_json FROM symbol_analyses WHERE id = ?",
            (newest_id,),
        ).fetchone()

    assert result["symbol_analyses"]["compacted_count"] == 1
    assert result["symbol_analyses"]["forced_recent_count"] == 1
    assert len(row["snapshot_json"]) < 8_000
    assert "R" * 1000 not in row["snapshot_json"]


def test_runtime_storage_compaction_compacts_recent_symbol_analyses_above_soft_cap(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    saved = service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "summary": "최근 분석이어도 과한 snapshot은 위키형 요약으로 압축한다.",
            "snapshot": {"quote": {"price": 13660}},
            "prompt": {"index": 1},
            "raw_response": {"index": 1},
        }
    )
    oversized_snapshot = json.dumps(
        {
            "quote": {"price": 13660},
            "research_snapshot": {"raw": "RECENT_CONTEXT " * 6_000},
        },
        ensure_ascii=False,
    )
    with service.repository._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE symbol_analyses SET snapshot_json = ? WHERE id = ?",
            (oversized_snapshot, int(saved["id"])),
        )

    result = service.compact_runtime_storage(
        symbol_analysis_recent_rows_per_symbol=3,
        symbol_analysis_min_payload_chars=1000,
        vacuum=False,
    )
    with service.repository._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT snapshot_json FROM symbol_analyses WHERE id = ?",
            (int(saved["id"]),),
        ).fetchone()

    assert result["symbol_analyses"]["forced_recent_count"] == 1
    assert result["symbol_analyses"]["recent_hard_limit_chars"] == 50_000
    assert len(row["snapshot_json"]) < 8_000
    assert "RECENT_CONTEXT" not in row["snapshot_json"]


def test_period_metrics_group_reflections_by_pattern_and_venue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "bnb_short_loss",
            "symbol": "BNBUSDT",
            "name": "BNB",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_krw": -0.2,
            "pnl_pct": -1.0,
            "mfe_pct": 0.2,
            "mae_pct": -1.1,
            "hold_seconds": 600,
            "rule_followed": True,
            "lesson_md": "BNB 숏 반복 손실",
            "metrics": {
                "outcome_date": "2026-06-05",
                "memory_scope": "binance",
                "market": "futures",
                "side": "short",
                "pattern_key": "binance:futures:short:microtrend",
                "policy_id": "validate_crypto_futures_short_microtrend",
            },
        }
    )

    metrics = service.build_period_metrics(
        period_type="weekly",
        period_key="2026-W23",
        start_date="2026-06-01",
        end_date="2026-06-05",
    )

    assert metrics["by_venue"]["binance"]["sample_count"] == 1
    assert metrics["by_pattern"]["binance:futures:short:microtrend"]["avg_pnl_pct"] == pytest.approx(-1.0)
    assert metrics["by_lane"]["futures:short"]["win_rate"] == 0.0


def test_due_slots_include_weekly_and_monthly_reviews(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    friday_close = datetime(2026, 5, 22, 16, 5, tzinfo=KST)
    month_end_close = datetime(2026, 5, 29, 16, 5, tzinfo=KST)

    assert "weekly_review" in service.due_slots(now=friday_close)
    assert "weekly_replay" in service.due_slots(now=friday_close)
    assert "monthly_review" in service.due_slots(now=month_end_close)

    asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=friday_close,
            force=True,
        )
    )
    service.repository.upsert_historical_replay(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "case_count": 1,
            "metrics": {},
            "replay_md": "done",
            "policy_revision_ids": [],
        }
    )

    assert "weekly_review" not in service.due_slots(now=friday_close)
    assert "weekly_replay" not in service.due_slots(now=friday_close)


def test_due_slots_keep_weekly_work_due_until_all_memory_scopes_complete(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    friday_close = datetime(2026, 5, 22, 16, 5, tzinfo=KST)
    weekly_window = service.period_window(period_type="weekly", now=friday_close)

    service.repository.upsert_period_review(
        {
            "period_key": weekly_window["period_key"],
            "period_type": "weekly",
            "memory_scope": "kis",
            "start_date": weekly_window["start_date"],
            "end_date": weekly_window["end_date"],
            "status": "ok",
            "mode": "llm",
            "metrics": {"memory_scope": "kis"},
            "review_md": "KIS review done",
            "policy_revision_ids": [],
        }
    )
    service.repository.upsert_historical_replay(
        {
            "period_key": weekly_window["period_key"],
            "period_type": "weekly",
            "memory_scope": "kis",
            "start_date": weekly_window["start_date"],
            "end_date": weekly_window["end_date"],
            "status": "ok",
            "mode": "llm",
            "case_count": 1,
            "metrics": {"memory_scope": "kis"},
            "replay_md": "KIS replay done",
            "policy_revision_ids": [],
        }
    )

    partial_slots = service.due_slots(
        now=friday_close,
        memory_scopes=["kis", "binance"],
    )

    assert "weekly_review" in partial_slots
    assert "weekly_replay" in partial_slots

    service.repository.upsert_period_review(
        {
            "period_key": weekly_window["period_key"],
            "period_type": "weekly",
            "memory_scope": "binance",
            "start_date": weekly_window["start_date"],
            "end_date": weekly_window["end_date"],
            "status": "ok",
            "mode": "llm",
            "metrics": {"memory_scope": "binance"},
            "review_md": "Binance review done",
            "policy_revision_ids": [],
        }
    )
    service.repository.upsert_historical_replay(
        {
            "period_key": weekly_window["period_key"],
            "period_type": "weekly",
            "memory_scope": "binance",
            "start_date": weekly_window["start_date"],
            "end_date": weekly_window["end_date"],
            "status": "ok",
            "mode": "llm",
            "case_count": 1,
            "metrics": {"memory_scope": "binance"},
            "replay_md": "Binance replay done",
            "policy_revision_ids": [],
        }
    )

    complete_slots = service.due_slots(
        now=friday_close,
        memory_scopes=["kis", "binance"],
    )

    assert "weekly_review" not in complete_slots
    assert "weekly_replay" not in complete_slots


def test_due_slots_skip_krx_holidays_and_use_last_open_day_for_monthly(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.calendar = _HolidayCalendar({date(2026, 5, 29)})  # type: ignore[assignment]
    service.initialize()

    holiday_pre_open = datetime(2026, 5, 29, 8, 40, tzinfo=KST)
    holiday_close = datetime(2026, 5, 29, 16, 5, tzinfo=KST)
    last_open_close = datetime(2026, 5, 28, 16, 5, tzinfo=KST)

    assert service.due_slots(now=holiday_pre_open) == []
    assert service.due_slots(now=holiday_close) == []
    assert "monthly_review" in service.due_slots(now=last_open_close)


def test_context_pack_includes_period_reviews_and_policy_revisions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"closed_blocks": 3},
            "review_md": "중기 블록 손절 기준을 조정한다.",
            "policy_revision_ids": ["2026-W21:prefer_mid_user_positions:1"],
        }
    )
    service.repository.upsert_policy_revision(
        {
            "revision_id": "2026-W21:prefer_mid_user_positions:1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "policy_id": "prefer_mid_user_positions",
            "action": "create",
            "status": "active_caution",
            "scope": "user_position",
            "condition": {"created_by": "user"},
            "effect": {"horizon_bias": "mid", "hard_filter": False},
            "evidence": {"sample_count": 3},
            "reason_md": "사용자 직접 매수분은 중기 관리 우선.",
            "confidence": 0.7,
        }
    )

    pack = service.context_pack(max_chars=6000)

    assert pack["period_reviews"]["weekly"]["period_key"] == "2026-W21"
    assert pack["period_reviews"]["weekly"]["review_md"] == "중기 블록 손절 기준을 조정한다."
    assert pack["policy_revisions"][0]["policy_id"] == "prefer_mid_user_positions"
    assert pack["policy_revisions"][0]["effect"]["horizon_bias"] == "mid"


def test_context_pack_keeps_scoped_period_reviews_and_replays_separate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    base = {
        "period_key": "2026-W21",
        "period_type": "weekly",
        "start_date": "2026-05-18",
        "end_date": "2026-05-22",
        "status": "ok",
        "mode": "llm",
        "policy_revision_ids": [],
    }
    service.repository.upsert_period_review(
        {
            **base,
            "memory_scope": "kis",
            "metrics": {"memory_scope": "kis", "closed_blocks": 2},
            "review_md": "KIS 주간 리뷰",
        }
    )
    service.repository.upsert_period_review(
        {
            **base,
            "memory_scope": "binance",
            "metrics": {"memory_scope": "binance", "closed_blocks": 9},
            "review_md": "Binance 주간 리뷰",
        }
    )
    service.repository.upsert_historical_replay(
        {
            **base,
            "memory_scope": "kis",
            "case_count": 2,
            "metrics": {"memory_scope": "kis", "case_count": 2},
            "replay_md": "KIS 리플레이",
            "case_reviews": [],
        }
    )
    service.repository.upsert_historical_replay(
        {
            **base,
            "memory_scope": "binance",
            "case_count": 9,
            "metrics": {"memory_scope": "binance", "case_count": 9},
            "replay_md": "Binance 리플레이",
            "case_reviews": [],
        }
    )

    kis_pack = service.context_pack(target_scope="kis", max_chars=12000)
    binance_pack = service.context_pack(target_scope="binance", max_chars=12000)

    assert kis_pack["period_reviews"]["weekly"]["review_md"] == "KIS 주간 리뷰"
    assert kis_pack["period_reviews"]["weekly"]["memory_scope"] == "kis"
    assert kis_pack["historical_replays"]["weekly"]["replay_md"] == "KIS 리플레이"
    assert kis_pack["historical_replays"]["weekly"]["memory_scope"] == "kis"
    assert binance_pack["period_reviews"]["weekly"]["review_md"] == "Binance 주간 리뷰"
    assert binance_pack["period_reviews"]["weekly"]["memory_scope"] == "binance"
    assert binance_pack["historical_replays"]["weekly"]["replay_md"] == (
        "Binance 리플레이"
    )
    assert binance_pack["historical_replays"]["weekly"]["memory_scope"] == "binance"


def test_context_pack_includes_period_memory_coverage_for_target_scope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "memory_scope": "kis",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"memory_scope": "kis"},
            "review_md": "KIS 주간 리뷰 완료",
            "policy_revision_ids": [],
        }
    )

    pack = service.context_pack(target_scope="kis", max_chars=12000)
    coverage = pack["period_memory_coverage"]

    assert coverage["scopes"] == ["kis"]
    assert coverage["status"] == "needs_attention"
    assert coverage["weekly_reviews"]["kis"]["status"] == "ok"
    assert coverage["weekly_replays"]["kis"]["status"] == "missing"
    assert coverage["monthly_reviews"]["kis"]["status"] == "missing"
    assert coverage["missing"] == [
        "kis:weekly_replay",
        "kis:monthly_review",
    ]


def test_compact_ritual_context_includes_daily_discovery_candidates() -> None:
    compact = _compact_ritual_context(
        {
            "daily_discovery": {
                "status": "ok",
                "trading_day": "2026-05-21",
                "summary": "장전 탐사 후보",
                "items": [
                    {
                        "symbol": "277810",
                        "name": "레인보우로보틱스",
                        "market": "KOSDAQ",
                        "score": 91,
                        "analysis": {
                            "stance": "block_candidate",
                            "summary": "로봇 모멘텀 후보",
                        },
                        "raw_payload": "SHOULD_NOT_REACH_RITUAL",
                    }
                ],
                "block_candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "market": "KOSPI",
                        "score": 76,
                        "summary": "반도체 대형주 후보",
                    }
                ],
            },
            "account": {"cash_krw": 1_000_000},
            "blocks": {"blocks": []},
        },
        limit=5000,
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert compact["daily_discovery"]["trading_day"] == "2026-05-21"
    assert compact["daily_discovery"]["items"][0]["symbol"] == "277810"
    assert compact["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
    assert "SHOULD_NOT_REACH_RITUAL" not in serialized


def test_build_ritual_context_contains_jue_workflow(tmp_path: Path) -> None:
    service = _service(tmp_path)

    context = service.build_ritual_context(
        slot="pre_open",
        trading_day="2026-05-31",
        account={"cash_krw": 1_000_000},
        blocks={"blocks": []},
    )
    workflow = context["jue_workflow"]
    skill_ids = {row["skill_id"] for row in workflow["skills"]}

    assert workflow["workflow_id"] == "kis_pre_open"
    assert "portfolio_balance" in skill_ids


def test_build_block_reflection_context_contains_jue_workflow(tmp_path: Path) -> None:
    service = _service(tmp_path)

    context = service.build_block_reflection_context(
        block={"block_id": "blk_1", "symbol": "005930", "status": "closed"},
        orders=[],
        events=[],
    )
    workflow = context["jue_workflow"]

    assert workflow["workflow_id"] == "block_reflection"
    assert context["blocks"]["recent_closed_blocks"][0]["block_id"] == "blk_1"


def test_runner_handles_period_reviews_without_skipping_daily_rituals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def __init__(self) -> None:
            self.review_calls: list[tuple[str, str]] = []
            self.replay_calls: list[tuple[str, str]] = []
            self.ritual_calls: list[str] = []

        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return ["post_close", "weekly_review", "weekly_replay", "monthly_review"]

        def status(self) -> dict[str, object]:
            return {"seeded": True}

        def seed_current(self, *, context: dict[str, object]) -> dict[str, object]:
            _ = context
            raise AssertionError("seed_current should not run for seeded memory")

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            return {"status": "skipped"}

        async def run_period_review(
            self,
            *,
            period_type: str,
            context: dict[str, object],
            force: bool = False,
        ) -> dict[str, object]:
            _ = force
            self.review_calls.append(
                (period_type, str(context.get("memory_scope") or ""))
            )
            return {
                "status": "ok",
                "period_key": f"2026-{period_type}",
                "revision_count": 1,
            }

        async def run_historical_replay(
            self,
            *,
            period_type: str,
            context: dict[str, object],
            force: bool = False,
        ) -> dict[str, object]:
            _ = force
            self.replay_calls.append(
                (period_type, str(context.get("memory_scope") or ""))
            )
            return {
                "status": "ok",
                "period_key": f"2026-{period_type}",
                "revision_count": 1,
                "case_count": 2,
            }

        async def run_ritual(
            self,
            *,
            slot: str,
            context: dict[str, object],
            send_telegram: bool = False,
        ) -> dict[str, object]:
            _ = (context, send_telegram)
            self.ritual_calls.append(slot)
            return {"status": "ok", "slot": slot}

    async def fake_build_context(settings: object) -> dict[str, object]:
        _ = settings
        return {"account": {"cash_krw": 1_000_000}}

    service = _RunnerService()
    settings = SimpleNamespace(
        investment_memory_state_path=str(tmp_path / "state.json"),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        fake_build_context,
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=service,  # type: ignore[arg-type]
        )
    )

    assert service.review_calls == [
        ("weekly", "kis"),
        ("weekly", "binance"),
        ("monthly", "kis"),
        ("monthly", "binance"),
    ]
    assert service.replay_calls == [("weekly", "kis"), ("weekly", "binance")]
    assert service.ritual_calls == ["post_close"]


def test_runner_accepts_custom_memory_scopes_for_period_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def __init__(self) -> None:
            self.review_contexts: list[dict[str, object]] = []
            self.due_slot_scope_calls: list[list[str]] = []

        def initialize(self) -> None:
            return None

        def due_slots(self, *, memory_scopes: list[str] | None = None) -> list[str]:
            self.due_slot_scope_calls.append(list(memory_scopes or []))
            return ["weekly_review"]

        def status(self) -> dict[str, object]:
            return {"seeded": True}

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            return {"status": "skipped"}

        async def run_period_review(
            self,
            *,
            period_type: str,
            context: dict[str, object],
            force: bool = False,
        ) -> dict[str, object]:
            _ = (period_type, force)
            self.review_contexts.append(context)
            return {"status": "ok", "period_key": "2026-W21"}

        async def run_historical_replay(
            self,
            *,
            period_type: str,
            context: dict[str, object],
            force: bool = False,
        ) -> dict[str, object]:
            _ = (period_type, context, force)
            raise AssertionError("weekly_replay is not due")

        async def run_ritual(
            self,
            *,
            slot: str,
            context: dict[str, object],
            send_telegram: bool = False,
        ) -> dict[str, object]:
            _ = (slot, context, send_telegram)
            return {"status": "ok"}

    async def fake_build_context(settings: object) -> dict[str, object]:
        _ = settings
        return {"base": "context"}

    service = _RunnerService()
    settings = SimpleNamespace(
        investment_memory_state_path=str(tmp_path / "state.json"),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        investment_memory_scopes="kis",
        daily_discovery_enabled=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        fake_build_context,
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=service,  # type: ignore[arg-type]
        )
    )

    assert [row["memory_scope"] for row in service.review_contexts] == ["kis"]
    assert [row["target_scope"] for row in service.review_contexts] == ["kis"]
    assert service.due_slot_scope_calls == [["kis"]]


def test_runner_idle_cycle_skips_heavy_context_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        reflection_calls = 0

        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return []

        def status(self) -> dict[str, object]:
            return {
                "seeded": True,
                "pending_event_count": 0,
                "latest_reflection_at": datetime.now(timezone.utc).isoformat(),
            }

        def _is_open_day(self, value: date) -> bool:
            _ = value
            return False

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            self.reflection_calls += 1
            return {"status": "skipped"}

    async def heavy_build_context(settings: object) -> dict[str, object]:
        _ = settings
        raise AssertionError("idle cycle must not build trading context")

    service = _RunnerService()
    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        heavy_build_context,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_current_kst_date",
        lambda: date(2026, 6, 14),
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=service,  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "idle"
    assert snapshot["include_trading_context"] is False
    assert snapshot["reflection_result"]["reason"] == "idle_no_due_reflections"
    assert service.reflection_calls == 0


def test_runner_open_day_without_due_work_skips_heavy_context_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        reflection_calls = 0

        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return []

        def status(self) -> dict[str, object]:
            return {
                "seeded": True,
                "pending_event_count": 0,
                "latest_reflection_at": datetime.now(timezone.utc).isoformat(),
            }

        def _is_open_day(self, value: date) -> bool:
            _ = value
            return True

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            self.reflection_calls += 1
            return {"status": "skipped"}

    async def heavy_build_context(settings: object) -> dict[str, object]:
        _ = settings
        raise AssertionError("open-day idle cycle must not build trading context")

    service = _RunnerService()
    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        heavy_build_context,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_current_kst_date",
        lambda: date(2026, 6, 16),
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=service,  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "idle"
    assert snapshot["include_trading_context"] is False
    assert snapshot["reflection_result"]["reason"] == "idle_no_due_reflections"
    assert service.reflection_calls == 0


def test_runner_skips_daily_discovery_before_morning_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return []

        def status(self) -> dict[str, object]:
            return {
                "seeded": True,
                "pending_event_count": 0,
                "latest_reflection_at": datetime.now(timezone.utc).isoformat(),
            }

        def _is_open_day(self, value: date) -> bool:
            _ = value
            return True

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            raise AssertionError("daily discovery closed window must stay idle")

    async def heavy_build_context(settings: object) -> dict[str, object]:
        _ = settings
        raise AssertionError("daily discovery closed window must not build context")

    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=True,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        heavy_build_context,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_current_kst_date",
        lambda: date(2026, 6, 16),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_daily_discovery_window_open",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_daily_discovery_service",
        lambda settings: (_ for _ in ()).throw(
            AssertionError("daily discovery service should not be built yet")
        ),
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=_RunnerService(),  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "idle"
    assert snapshot["include_trading_context"] is False
    assert snapshot["daily_discovery_due"] is False


def test_runner_detects_reflection_catchup_due_from_block_dbs(tmp_path: Path) -> None:
    from tradecraft.runtime import investment_memory_runner

    kis_db = tmp_path / "kis_blocks.db"
    binance_db = tmp_path / "binance_blocks.db"
    terminal_at = datetime.now(timezone.utc) - timedelta(hours=2)
    for db_path in (kis_db, binance_db):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE blocks (
                    block_id TEXT,
                    status TEXT,
                    closed_at TEXT,
                    updated_at TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO blocks VALUES (?, ?, ?, ?, ?)",
                (
                    "closed_block",
                    "closed",
                    terminal_at.isoformat(),
                    terminal_at.isoformat(),
                    (terminal_at - timedelta(hours=1)).isoformat(),
                ),
            )
    settings = SimpleNamespace(
        kis_block_trader_db_path=str(kis_db),
        binance_block_trader_db_path=str(binance_db),
    )

    assert investment_memory_runner._reflection_catchup_due(
        settings,  # type: ignore[arg-type]
        {"latest_reflection_at": (terminal_at - timedelta(hours=1)).isoformat()},
    )
    assert not investment_memory_runner._reflection_catchup_due(
        settings,  # type: ignore[arg-type]
        {"latest_reflection_at": datetime.now(timezone.utc).isoformat()},
    )


def test_runner_triggers_daily_discovery_after_missed_pre_open_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return []

        def status(self) -> dict[str, object]:
            return {"seeded": True}

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            return {"status": "skipped"}

        def _is_open_day(self, value: date) -> bool:
            return value == date(2026, 5, 22)

    class _DiscoveryService:
        def __init__(self) -> None:
            self.calls: list[date] = []

        def should_run_for_day(self, trading_day: date) -> bool:
            return trading_day == date(2026, 5, 22)

        async def run_once(
            self,
            *,
            trading_day: date,
            force: bool = False,
        ) -> dict[str, object]:
            _ = force
            self.calls.append(trading_day)
            return {"status": "ok", "analyzed_count": 10}

        def latest_context(self, *, limit: int = 10) -> dict[str, object]:
            _ = limit
            return {"status": "ok", "trading_day": "2026-05-22"}

    async def fake_build_context(settings: object) -> dict[str, object]:
        _ = settings
        return {"account": {"cash_krw": 1_000_000}}

    discovery = _DiscoveryService()
    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        investment_memory_run_daily_discovery=True,
        daily_discovery_enabled=True,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        fake_build_context,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_daily_discovery_service",
        lambda settings: discovery,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_current_kst_date",
        lambda: date(2026, 5, 22),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_daily_discovery_window_open",
        lambda: True,
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=_RunnerService(),  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert discovery.calls == [date(2026, 5, 22)]
    assert snapshot["results"][0] == {
        "status": "ok",
        "slot": "daily_discovery",
        "analyzed_count": 10,
    }


def test_runner_does_not_build_daily_discovery_by_default_after_morning_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return []

        def status(self) -> dict[str, object]:
            return {
                "seeded": True,
                "pending_event_count": 0,
                "latest_reflection_at": datetime.now(timezone.utc).isoformat(),
            }

        def _is_open_day(self, value: date) -> bool:
            return value == date(2026, 5, 22)

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            raise AssertionError("idle memory runner should not build context")

    async def heavy_build_context(settings: object) -> dict[str, object]:
        _ = settings
        raise AssertionError("idle memory runner should not build trading context")

    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=True,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        heavy_build_context,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_daily_discovery_service",
        lambda settings: (_ for _ in ()).throw(
            AssertionError("daily discovery service should be opt-in for memory")
        ),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_current_kst_date",
        lambda: date(2026, 5, 22),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_daily_discovery_window_open",
        lambda: True,
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=_RunnerService(),  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "idle"
    assert snapshot["daily_discovery_due"] is False
    assert snapshot["daily_discovery_runner_enabled"] is False


@pytest.mark.parametrize(
    ("result_status", "expected_status"),
    [("llm_unavailable", "degraded"), ("error", "error")],
)
def test_runner_snapshot_status_reflects_result_severity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_status: str,
    expected_status: str,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _RunnerService:
        def initialize(self) -> None:
            return None

        def due_slots(self) -> list[str]:
            return ["post_close"]

        def status(self) -> dict[str, object]:
            return {"seeded": True}

        def run_due_reflections(
            self,
            *,
            context: dict[str, object],
        ) -> dict[str, object]:
            _ = context
            return {"status": "skipped"}

        async def run_ritual(
            self,
            *,
            slot: str,
            context: dict[str, object],
            send_telegram: bool = False,
        ) -> dict[str, object]:
            _ = (slot, context, send_telegram)
            return {"status": result_status, "slot": "post_close"}

    async def fake_build_context(settings: object) -> dict[str, object]:
        _ = settings
        return {"account": {"cash_krw": 1_000_000}}

    state_path = tmp_path / "state.json"
    settings = SimpleNamespace(
        investment_memory_state_path=str(state_path),
        investment_memory_poll_interval_sec=10,
        investment_memory_once=True,
        investment_memory_send_telegram=False,
        daily_discovery_enabled=False,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_context",
        fake_build_context,
    )

    asyncio.run(
        investment_memory_runner.run_investment_memory_loop(
            settings=settings,  # type: ignore[arg-type]
            service=_RunnerService(),  # type: ignore[arg-type]
        )
    )

    snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == expected_status


def test_symbol_analysis_history_is_persisted_and_listed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    saved = service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "trigger": "user_request",
            "source": "instant",
            "model": "gpt-5.5",
            "status": "ok",
            "summary": "피노는 단기 반등 블록만 허용한다.",
            "short_view": "단기 변동성 대응",
            "mid_view": "재무 공백 확인",
            "long_view": "중장기 확신 부족",
            "stance": "risk_check",
            "confidence": 0.72,
            "reasons": ["목표가 회복 확인"],
            "risks": ["밸류 공백"],
            "data_gaps": ["리포트 없음"],
            "triggers": ["13800원 회복"],
            "target_candidates": [13800],
            "stop_candidates": [13340],
            "snapshot": {"quote": {"price": 13660}},
        }
    )

    history = service.repository.list_symbol_analyses("033790", limit=5)

    assert saved["id"] > 0
    assert history["status"] == "ok"
    assert history["symbol"] == "033790"
    assert history["count"] == 1
    assert history["items"][0]["summary"] == "피노는 단기 반등 블록만 허용한다."
    assert history["items"][0]["reasons"] == ["목표가 회복 확인"]
    assert history["items"][0]["snapshot"]["quote"]["price"] == 13660


def test_symbol_analysis_updates_symbol_markdown(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.record_symbol_analysis_memory(
        {
            "symbol": "033790",
            "name": "피노",
            "summary": "피노는 데이터 공백이 커서 짧은 블록만 다룬다.",
            "stance": "risk_check",
            "confidence": 0.68,
            "created_at": "2026-05-19T11:30:00+00:00",
        }
    )

    text = (tmp_path / "memory" / "symbols" / "033790.md").read_text(encoding="utf-8")

    assert "피노" in text
    assert "데이터 공백" in text
    assert "risk_check" in text


def test_runner_build_context_prefers_compact_trading_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    class _Trader:
        def __init__(self, name: str) -> None:
            self.name = name

        async def snapshot(self) -> dict[str, object]:
            return {
                "status": "ok",
                "kind": "full",
                "blocks": [{"block_id": f"{self.name}_full", "raw": "x" * 200_000}],
                "manager_runs": [{"prompt_json": "y" * 200_000}],
                "account": {"cash_krw": 1_000_000},
                "summary": {"clock": {"session": "regular"}},
            }

        async def snapshot_compact(self) -> dict[str, object]:
            return {
                "status": "ok",
                "kind": "compact",
                "active_blocks": [{"block_id": f"{self.name}_compact"}],
                "manager_runs": [{"status": "ok", "action_count": 1}],
                "account": {"cash_krw": 1_000_000},
                "summary": {"clock": {"session": "regular"}},
            }

    class _UsageRepository:
        def __init__(self, path: str) -> None:
            self.path = path

        def daily_summary(self, day: str) -> dict[str, object]:
            return {"trading_day": day, "total": {"call_count": 0}}

    class _Discovery:
        def latest_context(self, *, limit: int = 10) -> dict[str, object]:
            return {"status": "ok", "limit": limit}

    monkeypatch.setattr(
        investment_memory_runner,
        "_build_block_trader",
        lambda settings: _Trader("kis"),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_binance_block_trader",
        lambda settings: _Trader("binance"),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "read_active_research_feed",
        lambda settings: ({"status": "ok"}, "ok"),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "LLMUsageRepository",
        _UsageRepository,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_daily_discovery_service",
        lambda settings: _Discovery(),
    )

    context = asyncio.run(
        investment_memory_runner._build_context(  # noqa: SLF001
            SimpleNamespace(llm_usage_db_path=str(tmp_path / "usage.db"))
        )
    )
    serialized = json.dumps(context, ensure_ascii=False)

    assert context["blocks"]["kind"] == "compact"
    assert context["binance_blocks"]["kind"] == "compact"
    assert "kis_compact" in serialized
    assert "binance_compact" in serialized
    assert "prompt_json" not in serialized
    assert "x" * 1000 not in serialized


def test_runner_build_context_can_skip_trading_snapshots_for_idle_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.runtime import investment_memory_runner

    def fail_build_trader(settings: object) -> object:
        _ = settings
        raise AssertionError("trading snapshot should not be built")

    class _UsageRepository:
        def __init__(self, path: str) -> None:
            self.path = path

        def daily_summary(self, day: str) -> dict[str, object]:
            return {"trading_day": day, "total": {"call_count": 0}}

    monkeypatch.setattr(
        investment_memory_runner,
        "_build_block_trader",
        fail_build_trader,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "_build_binance_block_trader",
        fail_build_trader,
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "read_active_research_feed",
        lambda settings: ({"status": "ok"}, "ok"),
    )
    monkeypatch.setattr(
        investment_memory_runner,
        "LLMUsageRepository",
        _UsageRepository,
    )

    context = asyncio.run(
        investment_memory_runner._build_context(  # noqa: SLF001
            SimpleNamespace(
                llm_usage_db_path=str(tmp_path / "usage.db"),
                daily_discovery_db_path=str(tmp_path / "daily.db"),
            ),
            include_trading=False,
        )
    )

    assert context["blocks"]["status"] == "skipped"
    assert context["binance_blocks"]["status"] == "skipped"
    assert context["research"]["status"] == "ok"
