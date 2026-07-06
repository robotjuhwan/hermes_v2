from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from tradecraft.runtime.research_runner import (
    _build_advice_message,
    _resolve_symbol_names_for_codes,
    _next_advice_slot,
    _service_reports_enabled,
    _should_run_learning,
    _write_research_disabled_snapshot,
)
from tradecraft.runtime.state_store import RuntimeStateStore


KST = ZoneInfo("Asia/Seoul")


def test_intelligence_service_does_not_duplicate_report_collection() -> None:
    assert not _service_reports_enabled("tradecraft-research", True)
    assert _service_reports_enabled(
        "tradecraft-research",
        True,
        research_runner_collect_reports=True,
    )
    assert not _service_reports_enabled("tradecraft-intelligence", True)
    assert not _service_reports_enabled("tradecraft-research", False)


def test_write_research_disabled_snapshot_clears_stale_running_state(
    tmp_path,
) -> None:
    state_path = tmp_path / "research.json"
    store = RuntimeStateStore(state_path)
    store.write_snapshot(
        {
            "updated_at": "2026-06-28T15:05:42+00:00",
            "service": "tradecraft-research",
            "status": "report_collection_running",
        }
    )

    snapshot = _write_research_disabled_snapshot(
        state_store=store,
        service_name="tradecraft-research",
        research_enabled=False,
        reports_enabled=False,
    )
    saved = store.read_snapshot() or {}

    assert snapshot["status"] == "disabled"
    assert saved["status"] == "disabled"
    assert saved["research_enabled"] is False
    assert saved["reports_enabled"] is False
    assert saved["reason"] == "research_and_reports_disabled"


def test_next_advice_slot_same_open_day() -> None:
    now = datetime(2026, 2, 18, 7, 10, tzinfo=KST)

    next_at, label = _next_advice_slot(now, is_open_day=lambda _: True)

    assert next_at == datetime(2026, 2, 18, 8, 0, tzinfo=KST)
    assert label == "장전"


def test_next_advice_slot_skips_holiday() -> None:
    holiday = date(2026, 2, 18)
    now = datetime(2026, 2, 18, 7, 10, tzinfo=KST)

    next_at, label = _next_advice_slot(
        now,
        is_open_day=lambda value: value != holiday and value.weekday() < 5,
    )

    assert next_at == datetime(2026, 2, 19, 8, 0, tzinfo=KST)
    assert label == "장전"


def test_build_advice_message_includes_balance_and_knowledge() -> None:
    snapshot = {
        "updated_at": "2026-02-18T00:00:00Z",
        "query": "KRX momentum",
        "items": [
            {
                "picks": ["005930", "000660"],
                "summary": "Semiconductor momentum remains resilient.",
            }
        ],
    }
    message = _build_advice_message(
        snapshot=snapshot,
        label="장전",
        scheduled_at=datetime(2026, 2, 19, 8, 0, tzinfo=KST),
        balance={
            "total": "1.20B KRW",
            "cash": "120.0W KRW",
            "count": "2",
            "top": "005930, 000660",
        },
        knowledge_excerpt="# KRX Knowledge Memory\nCore setup in English.",
        pick_name_map={"005930": "삼성전자", "000660": "SK하이닉스"},
    )

    assert "계좌현황" in message
    assert "후보종목: 삼성전자(005930), SK하이닉스(000660)" in message
    assert "Knowledge:" in message


def test_should_run_learning_when_db_changed() -> None:
    assert _should_run_learning(
        current_db_updated_at="2026-02-17T10:00:00+00:00",
        previous_db_updated_at="2026-02-17T09:00:00+00:00",
        has_snapshot=True,
        snapshot_updated_at="2026-02-17T09:59:00+00:00",
        max_snapshot_age_sec=3600,
    )


def test_should_skip_learning_when_db_unchanged_and_snapshot_exists() -> None:
    recent = datetime.now(KST).isoformat()
    assert not _should_run_learning(
        current_db_updated_at="2026-02-17T10:00:00+00:00",
        previous_db_updated_at="2026-02-17T10:00:00+00:00",
        has_snapshot=True,
        snapshot_updated_at=recent,
        max_snapshot_age_sec=3600,
    )


def test_should_run_learning_without_snapshot_even_if_db_unchanged() -> None:
    assert _should_run_learning(
        current_db_updated_at="2026-02-17T10:00:00+00:00",
        previous_db_updated_at="2026-02-17T10:00:00+00:00",
        has_snapshot=False,
        snapshot_updated_at="",
        max_snapshot_age_sec=3600,
    )


def test_resolve_symbol_names_for_codes_uses_pykrx_fallback(monkeypatch) -> None:
    class _Repo:
        def resolve_symbol_names(self, symbols):
            return {}

        def search(self, query: str, symbol: str, category: str, limit: int = 3):
            return []

        def upsert_symbol_directory(self, **kwargs):
            return None

        def refresh_symbol_directory_from_krx(self):
            return {"ok": True}

    monkeypatch.setattr(
        "tradecraft.runtime.research_runner._fetch_company_name_from_pykrx",
        lambda symbol: "테스트기업" if str(symbol) == "123456" else "",
    )
    monkeypatch.setattr(
        "tradecraft.runtime.research_runner._fetch_company_name_from_naver",
        lambda symbol: "",
    )

    out = _resolve_symbol_names_for_codes(
        codes=["123456"],
        report_repository=cast(Any, _Repo()),
        kis=None,
        initial_map={},
    )

    assert out == {"123456": "테스트기업"}
