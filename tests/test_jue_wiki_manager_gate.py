from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.config import AppSettings
from tradecraft.runtime import binance_block_trader_runner, kis_block_trader_runner
from tradecraft.services.binance_block_trader import apply_binance_wiki_decision_gate
from tradecraft.services.jue_wiki_contract import WikiDecisionGateV1
from tradecraft.services.kis_block_trader import apply_kis_wiki_decision_gate


def _blocked_gate(reason: str = "wiki_required_coverage_missing") -> WikiDecisionGateV1:
    return WikiDecisionGateV1(
        allow_new_risk=False,
        allow_exit_actions=True,
        reason=reason,
        read_mode="required",
        snapshot_id="snapshot:test:1",
    )


def test_kis_required_wiki_gap_blocks_create_but_keeps_close_actions() -> None:
    actions = {
        "create_blocks": [{"symbol": "005930"}],
        "close_blocks": [{"block_id": "kis-1"}],
    }

    filtered, audit = apply_kis_wiki_decision_gate(
        actions,
        _blocked_gate(),
        trusted_read_mode="required",
    )

    assert filtered["create_blocks"] == []
    assert filtered["close_blocks"] == [{"block_id": "kis-1"}]
    assert audit["suppressed_new_risk_count"] == 1
    assert audit["suppressed_actions"] == [
        {
            "venue": "kis",
            "action_kind": "create_blocks",
            "symbol": "005930",
            "block_id": "",
            "snapshot_id": "snapshot:test:1",
            "read_mode": "required",
            "reason": "wiki_required_coverage_missing",
        }
    ]


def test_kis_required_gate_blocks_only_explicit_or_ambiguous_size_increases() -> None:
    actions = {
        "update_blocks": [
            {"block_id": "increase", "qty": 12},
            {"block_id": "reduce", "qty": 5},
            {"block_id": "tighten", "stop_price": 105},
            {"block_id": "ambiguous", "qty": "unknown"},
        ],
        "close_blocks": [{"block_id": "close"}],
    }
    current = {
        "increase": {"block_id": "increase", "symbol": "005930", "qty_open": 10},
        "reduce": {"block_id": "reduce", "symbol": "000660", "qty_open": 10},
        "tighten": {"block_id": "tighten", "symbol": "035420", "qty_open": 4},
        "ambiguous": {"block_id": "ambiguous", "symbol": "051910", "qty_open": 2},
    }

    filtered, audit = apply_kis_wiki_decision_gate(
        actions,
        _blocked_gate("wiki_required_mode_ineligible"),
        trusted_read_mode="required",
        current_blocks=current,
    )

    assert [row["block_id"] for row in filtered["update_blocks"]] == [
        "reduce",
        "tighten",
    ]
    assert filtered["close_blocks"] == [{"block_id": "close"}]
    assert audit["suppressed_new_risk_count"] == 2
    assert {row["reason"] for row in audit["suppressed_actions"]} == {
        "wiki_required_mode_ineligible"
    }


def test_binance_required_wiki_gap_does_not_trust_reduce_only_without_enforcement() -> None:
    actions = {
        "create_blocks": [{"symbol": "BTCUSDT"}],
        "update_blocks": [
            {"block_id": "btc-1", "reduce_only": True, "quantity": 0.2},
        ],
    }

    filtered, audit = apply_binance_wiki_decision_gate(
        actions,
        _blocked_gate(),
        trusted_read_mode="required",
    )

    assert filtered["create_blocks"] == []
    assert filtered["update_blocks"] == []
    assert audit["suppressed_new_risk_count"] == 2


def test_binance_required_gate_blocks_leverage_and_non_reduce_only_increases() -> None:
    actions = {
        "update_blocks": [
            {"block_id": "lev", "leverage": 3},
            {"block_id": "grow", "quantity": 2, "reduce_only": False},
            {"block_id": "reduce", "quantity": 0.5, "reduce_only": False},
            {"block_id": "exit", "quantity": 2, "reduce_only": True},
            {"block_id": "tighten", "stop_price": 101},
        ],
        "pause_blocks": [{"block_id": "pause"}],
        "close_blocks": [{"block_id": "close"}],
    }
    current = {
        "lev": {"block_id": "lev", "symbol": "BTCUSDT", "leverage": 2},
        "grow": {"block_id": "grow", "symbol": "ETHUSDT", "qty_open": 1},
        "reduce": {"block_id": "reduce", "symbol": "SOLUSDT", "qty_open": 1},
        "exit": {"block_id": "exit", "symbol": "XRPUSDT", "qty_open": 1},
        "tighten": {"block_id": "tighten", "symbol": "BNBUSDT", "qty_open": 1},
    }

    filtered, audit = apply_binance_wiki_decision_gate(
        actions,
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks=current,
    )

    assert [row["block_id"] for row in filtered["update_blocks"]] == [
        "reduce",
        "tighten",
    ]
    assert filtered["pause_blocks"] == [{"block_id": "pause"}]
    assert filtered["close_blocks"] == [{"block_id": "close"}]
    assert audit["suppressed_new_risk_count"] == 3


def test_shadow_and_prefer_do_not_change_actions() -> None:
    actions = {
        "create_blocks": [{"symbol": "BTCUSDT"}],
        "update_blocks": [{"block_id": "btc-1", "leverage": 10}],
    }
    for mode in ("shadow", "prefer"):
        gate = WikiDecisionGateV1(
            allow_new_risk=False,
            allow_exit_actions=True,
            reason="wiki_required_coverage_missing",
            read_mode="required",
            snapshot_id="snapshot:test:advisory",
        )

        filtered, audit = apply_binance_wiki_decision_gate(
            actions,
            gate,
            trusted_read_mode=mode,
        )

        assert filtered == actions
        assert filtered is not actions
        assert audit["suppressed_new_risk_count"] == 0


@pytest.mark.parametrize(
    ("gate", "reason"),
    [
        ({}, "wiki_required_gate_missing"),
        (
            {
                "allow_new_risk": "true",
                "allow_exit_actions": True,
                "reason": "wiki_context_eligible",
                "read_mode": "required",
                "snapshot_id": "snapshot:test:eligible",
                "version": "wiki_decision_gate_v1",
            },
            "wiki_required_gate_invalid:allow_new_risk",
        ),
        (
            {
                "allow_new_risk": True,
                "allow_exit_actions": True,
                "reason": "wiki_context_eligible",
                "read_mode": "prefer",
                "snapshot_id": "snapshot:test:eligible",
                "version": "wiki_decision_gate_v1",
            },
            "wiki_required_gate_invalid:read_mode",
        ),
        (
            {
                "allow_new_risk": True,
                "allow_exit_actions": True,
                "reason": "wiki_required_coverage_missing",
                "read_mode": "required",
                "snapshot_id": "snapshot:test:eligible",
                "version": "wiki_decision_gate_v1",
            },
            "wiki_required_gate_invalid:reason",
        ),
    ],
)
def test_required_mode_fails_closed_for_malformed_or_mismatched_gate(
    gate: dict[str, object],
    reason: str,
) -> None:
    actions = {
        "create_blocks": [{"symbol": "005930"}],
        "close_blocks": [{"block_id": "close"}],
    }

    filtered, audit = apply_kis_wiki_decision_gate(
        actions,
        gate,
        trusted_read_mode="required",
    )

    assert filtered["create_blocks"] == []
    assert filtered["close_blocks"] == [{"block_id": "close"}]
    assert audit["reason"] == reason


def test_required_mode_allows_new_risk_only_for_exact_eligible_gate() -> None:
    actions = {"create_blocks": [{"symbol": "005930"}]}
    gate = {
        "allow_new_risk": True,
        "allow_exit_actions": True,
        "reason": "wiki_context_eligible",
        "read_mode": "required",
        "snapshot_id": "snapshot:test:eligible",
        "version": "wiki_decision_gate_v1",
    }

    filtered, audit = apply_kis_wiki_decision_gate(
        actions,
        gate,
        trusted_read_mode="required",
    )

    assert filtered == actions
    assert audit["suppressed_new_risk_count"] == 0


def test_claimed_reduction_and_conflicting_aliases_fail_closed() -> None:
    actions = {
        "update_blocks": [
            {
                "block_id": "claimed",
                "qty": 12,
                "intent": "reduce",
                "decision_class": "close",
            },
            {"block_id": "conflict", "qty": 5, "quantity": 12},
            {"block_id": "notional", "notional_usdt": 90, "quote_budget_usdt": 150},
            {"block_id": "string_bool", "quantity": 12, "reduce_only": "true"},
        ],
        "close_blocks": [{"block_id": "close"}],
        "pause_blocks": [{"block_id": "pause"}],
    }
    current = {
        "claimed": {"block_id": "claimed", "symbol": "BTCUSDT", "qty_open": 10},
        "conflict": {"block_id": "conflict", "symbol": "ETHUSDT", "qty_open": 10},
        "notional": {
            "block_id": "notional",
            "symbol": "SOLUSDT",
            "notional_usdt": 100,
        },
        "string_bool": {
            "block_id": "string_bool",
            "symbol": "XRPUSDT",
            "qty_open": 10,
        },
    }

    filtered, audit = apply_binance_wiki_decision_gate(
        actions,
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks=current,
    )

    assert filtered["update_blocks"] == []
    assert filtered["close_blocks"] == [{"block_id": "close"}]
    assert filtered["pause_blocks"] == [{"block_id": "pause"}]
    assert audit["suppressed_new_risk_count"] == 4


def test_binance_notional_aliases_compare_only_with_same_currency() -> None:
    actions = {
        "update_blocks": [
            {
                "block_id": "mixed_reduce",
                "notional_krw": 140_000,
                "notional_usdt": 200,
            },
            {"block_id": "upbit_opposite", "quote_budget_usdt": 200},
            {"block_id": "usdt_reduce", "quote_budget_usdt": 80},
            {"block_id": "krw_reduce", "quote_budget_krw": 90_000},
            {"block_id": "opposite_ambiguous", "notional_krw": 140_000},
        ]
    }
    current = {
        "mixed_reduce": {
            "block_id": "mixed_reduce",
            "market": "upbit_spot",
            "notional_krw": 150_000,
            "notional_usdt": 250,
        },
        "upbit_opposite": {
            "block_id": "upbit_opposite",
            "market": "upbit_spot",
            "qty_open": 1,
            "entry_price": 140_000,
        },
        "usdt_reduce": {
            "block_id": "usdt_reduce",
            "market": "spot",
            "notional_usdt": 100,
        },
        "krw_reduce": {
            "block_id": "krw_reduce",
            "market": "upbit_spot",
            "notional_krw": 100_000,
        },
        "opposite_ambiguous": {
            "block_id": "opposite_ambiguous",
            "market": "spot",
            "notional_usdt": 100,
        },
    }

    filtered, audit = apply_binance_wiki_decision_gate(
        actions,
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks=current,
    )

    assert [row["block_id"] for row in filtered["update_blocks"]] == [
        "mixed_reduce",
        "usdt_reduce",
        "krw_reduce",
    ]
    assert [row["block_id"] for row in audit["suppressed_actions"]] == [
        "upbit_opposite",
        "opposite_ambiguous",
    ]


@pytest.mark.parametrize(
    ("market", "current_notional", "update_notional"),
    [
        ("spot", {"notional": 100}, {"notional_usdt": 80}),
        ("futures", {"notional_usdt": 100}, {"notional": 80}),
        ("upbit_spot", {"notional": 100_000}, {"notional_krw": 80_000}),
        ("upbit_spot", {"notional_krw": 100_000}, {"notional": 80_000}),
    ],
)
def test_binance_generic_notional_uses_market_native_currency_for_reductions(
    market: str,
    current_notional: dict[str, int],
    update_notional: dict[str, int],
) -> None:
    current = {"block_id": "reduce", "market": market, **current_notional}
    action = {"block_id": "reduce", **update_notional}

    filtered, audit = apply_binance_wiki_decision_gate(
        {"update_blocks": [action]},
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks={"reduce": current},
    )

    assert filtered["update_blocks"] == [action]
    assert audit["suppressed_new_risk_count"] == 0


@pytest.mark.parametrize(
    ("market", "update"),
    [
        ("spot", {"notional_krw": 80}),
        ("upbit_spot", {"notional_usdt": 80}),
        ("unknown_market", {"notional": 80}),
    ],
)
def test_binance_generic_notional_cross_currency_or_unknown_market_fails_closed(
    market: str,
    update: dict[str, int],
) -> None:
    filtered, audit = apply_binance_wiki_decision_gate(
        {"update_blocks": [{"block_id": "blocked", **update}]},
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks={
            "blocked": {"block_id": "blocked", "market": market, "notional": 100}
        },
    )

    assert filtered["update_blocks"] == []
    assert audit["suppressed_new_risk_count"] == 1


def test_binance_generic_notional_conflicting_market_fails_closed() -> None:
    filtered, audit = apply_binance_wiki_decision_gate(
        {
            "update_blocks": [
                {"block_id": "blocked", "market": "upbit_spot", "notional": 80}
            ]
        },
        _blocked_gate(),
        trusted_read_mode="required",
        current_blocks={
            "blocked": {"block_id": "blocked", "market": "spot", "notional": 100}
        },
    )

    assert filtered["update_blocks"] == []
    assert audit["suppressed_new_risk_count"] == 1


@pytest.mark.parametrize(
    "apply_gate",
    [apply_kis_wiki_decision_gate, apply_binance_wiki_decision_gate],
)
def test_required_gate_identity_at_limit_round_trips_exactly(apply_gate) -> None:
    reason_prefix = "wiki_required_  coverage  "
    reason = reason_prefix + ("r" * (118 - len(reason_prefix))) + "  "
    snapshot_prefix = "  snapshot  with  spaces  "
    snapshot_id = snapshot_prefix + ("s" * (118 - len(snapshot_prefix))) + "  "
    _, audit = apply_gate(
        {"create_blocks": [{"symbol": "TEST"}]},
        {
            "allow_new_risk": False,
            "allow_exit_actions": True,
            "reason": reason,
            "read_mode": "required",
            "snapshot_id": snapshot_id,
            "version": "wiki_decision_gate_v1",
        },
        trusted_read_mode="required",
    )

    assert audit["reason"] == reason
    assert audit["snapshot_id"] == snapshot_id


@pytest.mark.parametrize(
    "apply_gate",
    [apply_kis_wiki_decision_gate, apply_binance_wiki_decision_gate],
)
@pytest.mark.parametrize("field", ["reason", "snapshot_id"])
def test_required_gate_identity_over_limit_fails_closed(apply_gate, field: str) -> None:
    gate = {
        "allow_new_risk": False,
        "allow_exit_actions": True,
        "reason": "wiki_required_closed",
        "read_mode": "required",
        "snapshot_id": "s" * 120,
        "version": "wiki_decision_gate_v1",
    }
    gate[field] = "wiki_required_" + ("x" * 108) if field == "reason" else "s" * 121

    _, audit = apply_gate(
        {"create_blocks": [{"symbol": "TEST"}]},
        gate,
        trusted_read_mode="required",
    )

    assert audit["reason"] == f"wiki_required_gate_invalid:{field}"
    assert audit["snapshot_id"] == ""


@pytest.mark.parametrize("field", ["reason", "snapshot_id"])
def test_required_gate_rejects_oversized_identity_fields(field: str) -> None:
    gate: dict[str, object] = {
        "allow_new_risk": True,
        "allow_exit_actions": True,
        "reason": "wiki_context_eligible",
        "read_mode": "required",
        "snapshot_id": "snapshot:test:eligible",
        "version": "wiki_decision_gate_v1",
    }
    gate[field] = "x" * 100_000

    filtered, audit = apply_kis_wiki_decision_gate(
        {"create_blocks": [{"symbol": "005930"}]},
        gate,
        trusted_read_mode="required",
    )

    assert filtered["create_blocks"] == []
    assert audit["reason"] == f"wiki_required_gate_invalid:{field}"
    assert len(audit["reason"]) < 100
    assert len(audit["snapshot_id"]) <= 120


@pytest.mark.parametrize(
    "runner_module",
    [kis_block_trader_runner, binance_block_trader_runner],
)
def test_required_disabled_wiki_builds_closed_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner_module: object,
) -> None:
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_ENABLED", "false")
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_READ_MODE", "required")
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_ROOT_PATH", str(tmp_path / "wiki"))
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_DB_PATH", str(tmp_path / "wiki.db"))
    settings = AppSettings(_env_file=None)

    provider = runner_module._build_jue_wiki_context_provider(settings)
    assert provider is not None

    payload = provider(target_scope="kis", symbols=["005930"], max_chars=2_000)

    assert payload["jue_wiki_decision_gate"]["allow_new_risk"] is False
    assert payload["jue_wiki_decision_gate"]["reason"] == "wiki_required_disabled"


@pytest.mark.parametrize(
    "runner_module",
    [kis_block_trader_runner, binance_block_trader_runner],
)
def test_required_selector_outage_overrides_eligible_v3_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner_module: object,
) -> None:
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_ENABLED", "true")
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_READ_MODE", "required")
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_ROOT_PATH", str(tmp_path / "wiki"))
    monkeypatch.setenv("TRADECRAFT_JUE_WIKI_DB_PATH", str(tmp_path / "wiki.db"))
    settings = AppSettings(_env_file=None)

    class EligibleContext:
        def __init__(self, _repository: object) -> None:
            pass

        def context_packet(self, _request: object, read_mode: str) -> object:
            assert read_mode == "required"
            return type(
                "Packet",
                (),
                {"to_dict": lambda self: {"status": "ok", "read_mode": read_mode}},
            )()

    monkeypatch.setattr(runner_module, "JueWikiContextService", EligibleContext)
    monkeypatch.setattr(
        runner_module,
        "evaluate_wiki_decision_gate",
        lambda _packet: type(
            "Gate",
            (),
            {
                "to_dict": lambda self: {
                    "allow_new_risk": True,
                    "allow_exit_actions": True,
                    "reason": "wiki_context_eligible",
                    "read_mode": "required",
                    "snapshot_id": "snapshot:test:eligible",
                    "version": "wiki_decision_gate_v1",
                }
            },
        )(),
    )
    monkeypatch.setattr(
        runner_module.JueWikiSelector,
        "select",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("selector down")),
    )
    provider = runner_module._build_jue_wiki_context_provider(settings)
    assert provider is not None

    payload = provider(target_scope="kis", symbols=["005930"], max_chars=2_000)

    assert payload["jue_wiki_decision_gate"]["allow_new_risk"] is False
    assert payload["jue_wiki_decision_gate"]["reason"] == (
        "wiki_required_selector_unavailable"
    )
