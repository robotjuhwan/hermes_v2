from __future__ import annotations

import json
from datetime import datetime, date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tradecraft.runtime.research_runner import (
    _build_advice_message,
    _extract_balance_totals,
    _extract_reduce_tickers_from_payload,
    _extract_rebalance_target_weights_from_payload,
    _extract_total_value_krw_from_payload,
    _extract_target_cash_weight_from_payload,
    _next_advice_slot,
    _parse_krw_amount,
    _restart_kis_freqtrade_if_running,
    _sum_open_stake_amount,
    _sync_kis_rebalance_targets_to_freqtrade_override,
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
    runtime_dir = tmp_path / "freqtrade"
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
        freqtrade_runtime_dir=str(runtime_dir),
    )
    saved = RuntimeStateStore(state_path).read_snapshot() or {}
    override = json.loads(
        (runtime_dir / "kis.override.json").read_text(encoding="utf-8")
    )
    assert codes == ["005930", "000660", "035420"]
    assert saved.get("target_symbols") == ["005930", "000660", "035420"]
    assert override.get("exchange", {}).get("pair_whitelist") == [
        "005930/KRW",
        "000660/KRW",
        "035420/KRW",
    ]


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


def test_restart_kis_freqtrade_if_running_restarts_bot(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class DummyManager:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def list_statuses(self):
            return [{"running": True}]

        def stop(self, bot_id: str):
            calls.append(("stop", bot_id))
            return {"action": "stopped"}

        def start(self, bot_id: str):
            calls.append(("start", bot_id))
            return {"action": "started", "pid": 10101}

    monkeypatch.setattr(
        "tradecraft.runtime.research_runner.FreqtradeProcessManager",
        DummyManager,
    )

    settings = SimpleNamespace(
        freqtrade_executable_path="freqtrade",
        freqtrade_workdir="third_party/freqtrade",
        freqtrade_runtime_dir=".runtime/freqtrade",
        freqtrade_stop_timeout_sec=8.0,
    )
    out = _restart_kis_freqtrade_if_running(settings)  # type: ignore[arg-type]

    assert out.get("status") == "ok"
    assert out.get("stop_action") == "stopped"
    assert out.get("start_action") == "started"
    assert out.get("pid") == 10101
    assert calls == [("stop", "kis"), ("start", "kis")]


def test_restart_kis_freqtrade_if_running_skips_when_stopped(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class DummyManager:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def list_statuses(self):
            return [{"running": False}]

        def stop(self, bot_id: str):
            calls.append(("stop", bot_id))
            return {"action": "stopped"}

        def start(self, bot_id: str):
            calls.append(("start", bot_id))
            return {"action": "started", "pid": 10101}

    monkeypatch.setattr(
        "tradecraft.runtime.research_runner.FreqtradeProcessManager",
        DummyManager,
    )

    settings = SimpleNamespace(
        freqtrade_executable_path="freqtrade",
        freqtrade_workdir="third_party/freqtrade",
        freqtrade_runtime_dir=".runtime/freqtrade",
        freqtrade_stop_timeout_sec=8.0,
    )
    out = _restart_kis_freqtrade_if_running(settings)  # type: ignore[arg-type]

    assert out.get("status") == "ok"
    assert out.get("stop_action") == "stopped"
    assert out.get("start_action") == "started"
    assert out.get("pid") == 10101
    assert calls == [("stop", "kis"), ("start", "kis")]


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


def test_extract_balance_totals_and_sum_open_stake() -> None:
    balance_payload = {
        "total": 1200000,
        "currencies": [
            {"currency": "KRW", "free": 900000, "balance": 900000},
            {"currency": "005930", "free": 1, "balance": 1},
        ],
    }
    status_rows = [
        {"pair": "005930/KRW", "is_open": True, "stake_amount": 120000},
        {"pair": "000660/KRW", "is_open": True, "stake_amount": 80000},
    ]
    total_krw, krw_free = _extract_balance_totals(balance_payload)
    open_stake = _sum_open_stake_amount(status_rows)

    assert total_krw == 1200000.0
    assert krw_free == 900000.0
    assert open_stake == 200000.0


def test_sync_kis_rebalance_targets_to_freqtrade_override(tmp_path) -> None:
    runtime_dir = tmp_path / "freqtrade"
    out = _sync_kis_rebalance_targets_to_freqtrade_override(
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
        runtime_dir=str(runtime_dir),
        max_symbols=6,
    )
    saved = json.loads((runtime_dir / "kis.override.json").read_text(encoding="utf-8"))
    assert out == {"005930": 0.2, "000660": 0.15}
    assert saved.get("tradecraft_target_weights") == {
        "005930": 0.2,
        "000660": 0.15,
    }
    assert saved.get("force_entry_enable") is True
    assert saved.get("exchange", {}).get("pair_whitelist") == [
        "005930/KRW",
        "000660/KRW",
    ]


def test_sync_kis_rebalance_targets_to_freqtrade_override_writes_cash_weight(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / "freqtrade"
    out = _sync_kis_rebalance_targets_to_freqtrade_override(
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
        runtime_dir=str(runtime_dir),
        max_symbols=6,
    )
    saved = json.loads((runtime_dir / "kis.override.json").read_text(encoding="utf-8"))
    assert round(sum(out.values()), 6) == 0.75
    assert saved.get("tradecraft_target_cash_weight") == 0.25
    assert round(sum(saved.get("tradecraft_target_weights", {}).values()), 6) == 0.75


def test_sync_kis_rebalance_targets_to_freqtrade_override_adds_snapshot_picks(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / "freqtrade"
    out = _sync_kis_rebalance_targets_to_freqtrade_override(
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
        runtime_dir=str(runtime_dir),
        max_symbols=6,
    )
    saved = json.loads((runtime_dir / "kis.override.json").read_text(encoding="utf-8"))
    assert out.get("005930") == 0.2
    assert out.get("000660") == 0.08
    assert out.get("012450") == 0.08
    assert saved.get("exchange", {}).get("pair_whitelist") == [
        "005930/KRW",
        "000660/KRW",
        "012450/KRW",
    ]


def test_sync_kis_rebalance_targets_to_freqtrade_override_uses_snapshot_when_no_targets(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / "freqtrade"
    out = _sync_kis_rebalance_targets_to_freqtrade_override(
        payload={"pack": {"advice_seed_json": {"model_portfolio": {"targets": []}}}},
        snapshot={
            "items": [
                {"picks": ["005930", "000660"]},
            ]
        },
        runtime_dir=str(runtime_dir),
        max_symbols=6,
    )
    saved = json.loads((runtime_dir / "kis.override.json").read_text(encoding="utf-8"))
    assert out == {"005930": 0.5, "000660": 0.5}
    assert saved.get("tradecraft_target_weights") == {"005930": 0.5, "000660": 0.5}


def test_extract_reduce_tickers_from_payload() -> None:
    payload = {
        "pack": {
            "advice_seed_json": {
                "action_plan": {
                    "rebalance_table_rows": [
                        {"ticker": "033790", "action": "REDUCE"},
                        {"ticker": "005930", "action": "BUY"},
                        {"ticker": "000660", "action": "SELL"},
                        {"ticker": "bad", "action": "REDUCE"},
                    ]
                }
            }
        }
    }
    out = _extract_reduce_tickers_from_payload(payload)
    assert out == {"033790", "000660"}
