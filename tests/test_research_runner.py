from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from tradecraft.runtime.research_runner import (
    _build_advice_message,
    _extract_rebalance_target_weights_from_payload,
    _extract_total_value_krw_from_payload,
    _extract_target_cash_weight_from_payload,
    _resolve_symbol_names_for_codes,
    _next_advice_slot,
    _parse_krw_amount,
    _sync_kis_rebalance_targets_to_trader_state,
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


def test_extract_rebalance_target_weights_from_payload() -> None:
    payload = {
        "pack": {
            "advice_seed_json": {
                "model_portfolio": {
                    "targets": [
                        {"ticker": "005930", "target_weight": 0.2},
                        {"ticker": "000660", "target_weight": 0.15},
                    ]
                }
            }
        }
    }
    out = _extract_rebalance_target_weights_from_payload(payload, max_symbols=5)
    assert out == {"005930": 0.2, "000660": 0.15}


def test_extract_target_cash_weight_from_payload() -> None:
    payload = {
        "pack": {
            "advice_seed_json": {
                "model_portfolio": {
                    "targets": [{"ticker": "005930", "target_weight": 0.2}],
                    "target_cash_weight": 0.12,
                }
            }
        }
    }
    out = _extract_target_cash_weight_from_payload(payload)
    assert out == 0.12


def test_extract_target_cash_weight_prefers_strategy_spec() -> None:
    payload = {
        "pack": {
            "advice_seed_json": {
                "strategy_spec": {"target_cash_weight": 0.2},
                "model_portfolio": {"target_cash_weight": 0.1},
            }
        }
    }
    out = _extract_target_cash_weight_from_payload(payload)
    assert out == 0.2


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


def test_parse_krw_amount_parses_currency_text() -> None:
    assert _parse_krw_amount("2,330,000원") == 2330000.0


def test_extract_total_value_krw_from_payload_uses_total_krw_text() -> None:
    payload = {
        "pack": {
            "advice_seed_json": {
                "portfolio": {
                    "total_krw": "2,330,000원",
                }
            }
        }
    }
    assert _extract_total_value_krw_from_payload(payload) == 2330000.0


def test_sync_kis_rebalance_targets_to_trader_state(tmp_path) -> None:
    state_path = tmp_path / "kis_trader.json"
    out = _sync_kis_rebalance_targets_to_trader_state(
        payload={
            "pack": {
                "advice_seed_json": {
                    "model_portfolio": {
                        "targets": [
                            {"ticker": "005930", "target_weight": 0.2},
                            {"ticker": "000660", "target_weight": 0.15},
                        ]
                    }
                }
            }
        },
        trader_state_path=str(state_path),
        max_symbols=6,
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    assert out == {"005930": 0.2, "000660": 0.15}
    assert saved.get("target_weights") == {
        "005930": 0.2,
        "000660": 0.15,
    }
    assert saved.get("target_symbols") == ["005930", "000660"]
    assert saved.get("target_weights_source") == "research_runner_portfolio_coach"


def test_sync_kis_rebalance_targets_to_trader_state_writes_cash_weight(
    tmp_path,
) -> None:
    state_path = tmp_path / "kis_trader.json"
    out = _sync_kis_rebalance_targets_to_trader_state(
        payload={
            "pack": {
                "advice_seed_json": {
                    "strategy_spec": {"target_cash_weight": 0.25},
                    "model_portfolio": {
                        "targets": [
                            {"ticker": "005930", "target_weight": 0.6},
                            {"ticker": "000660", "target_weight": 0.4},
                        ]
                    },
                }
            }
        },
        trader_state_path=str(state_path),
        max_symbols=6,
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    assert round(sum(out.values()), 6) == 0.75
    assert saved.get("target_cash_weight") == 0.25
    assert round(sum(saved.get("target_weights", {}).values()), 6) == 0.75


def test_sync_kis_rebalance_targets_to_trader_state_adds_snapshot_picks(
    tmp_path,
) -> None:
    state_path = tmp_path / "kis_trader.json"
    out = _sync_kis_rebalance_targets_to_trader_state(
        payload={
            "pack": {
                "advice_seed_json": {
                    "model_portfolio": {
                        "targets": [
                            {"ticker": "005930", "target_weight": 0.2},
                        ]
                    }
                }
            }
        },
        snapshot={
            "items": [
                {"picks": ["005930", "000660", "012450"]},
            ]
        },
        trader_state_path=str(state_path),
        max_symbols=6,
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    assert out.get("005930") == 0.2
    assert out.get("000660") == 0.08
    assert out.get("012450") == 0.08
    assert saved.get("target_symbols") == ["005930", "000660", "012450"]


def test_sync_kis_rebalance_targets_to_trader_state_uses_snapshot_when_no_targets(
    tmp_path,
) -> None:
    state_path = tmp_path / "kis_trader.json"
    out = _sync_kis_rebalance_targets_to_trader_state(
        payload={"pack": {"advice_seed_json": {"model_portfolio": {"targets": []}}}},
        snapshot={
            "items": [
                {"picks": ["005930", "000660"]},
            ]
        },
        trader_state_path=str(state_path),
        max_symbols=6,
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    assert out == {"005930": 0.5, "000660": 0.5}
    assert saved.get("target_weights") == {"005930": 0.5, "000660": 0.5}
