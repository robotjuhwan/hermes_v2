from __future__ import annotations

from datetime import datetime, date
from zoneinfo import ZoneInfo

from tradecraft.runtime.research_runner import (
    _build_advice_message,
    _next_advice_slot,
    _sync_kis_trader_targets_from_morning_advice,
    _should_run_learning,
)
from tradecraft.runtime.state_store import RuntimeStateStore


KST = ZoneInfo("Asia/Seoul")


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


def test_sync_kis_trader_targets_from_morning_advice_writes_target_symbols(
    tmp_path,
) -> None:
    state_path = tmp_path / "kis_trader.json"
    codes = _sync_kis_trader_targets_from_morning_advice(
        snapshot={
            "items": [
                {"picks": ["005930", "000660"]},
                {"summary": "관심 종목 005930, 035420"},
            ]
        },
        label="장전",
        scheduled_at=datetime(2026, 2, 19, 8, 0, tzinfo=KST),
        trader_state_path=str(state_path),
        max_symbols=3,
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    assert codes == ["005930", "000660", "035420"]
    assert saved.get("target_symbols") == ["005930", "000660", "035420"]


def test_sync_kis_trader_targets_from_morning_advice_skips_non_morning(
    tmp_path,
) -> None:
    state_path = tmp_path / "kis_trader.json"
    codes = _sync_kis_trader_targets_from_morning_advice(
        snapshot={"items": [{"picks": ["005930"]}]},
        label="장마감",
        scheduled_at=datetime(2026, 2, 19, 15, 40, tzinfo=KST),
        trader_state_path=str(state_path),
        max_symbols=5,
    )
    assert codes == []
    assert RuntimeStateStore(state_path).read_snapshot() is None
