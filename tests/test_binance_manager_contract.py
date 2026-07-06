from __future__ import annotations

from tradecraft.services.binance_manager_contract import (
    apply_manager_contract_aliases,
    infer_manager_contract_side,
    manager_contract_action_key,
    manager_actions_from_contract_payload,
    manager_contract_close_payloads,
    manager_action_count,
    normalize_manager_hold_decision,
    raise_for_manager_llm_error_payload,
    validate_manager_actions,
)


def test_manager_contract_action_key_classifies_composite_actions() -> None:
    assert (
        manager_contract_action_key(
            {"decision": "create_blocks_and_close_stale", "symbol": "BTCUSDT"},
            response={},
        )
        == "create_blocks"
    )
    assert (
        manager_contract_action_key(
            {"decision": "close_blocks_and_hold", "block_id": "bnb_futures_ETH_1"},
            response={},
        )
        == "close_blocks"
    )
    assert (
        manager_contract_action_key(
            {"action": "pause block", "block_id": "bnb_spot_SOL_1"},
            response={},
        )
        == "pause_blocks"
    )
    assert (
        manager_contract_action_key(
            {
                "decision": "adopt_existing_block",
                "symbol": "BTCUSDT",
                "qty": 0.15,
            },
            response={},
        )
        == "adopt_existing_blocks"
    )


def test_manager_contract_close_payloads_collects_explicit_and_textual_block_ids() -> None:
    rows = manager_contract_close_payloads(
        {
            "close_blocks": [{"id": "bnb_spot_BTCUSDT_20260601010101000000"}],
            "close_block_ids": ["bnb_spot_BTCUSDT_20260601010101000000"],
            "stale_block_ids": ["bnb_futures_ETHUSDT_20260602020202000000"],
            "next_actions": [
                "close stale proposed block: bnb_futures_SOLUSDT_20260603030303000000",
                "watch only bnb_futures_XRPUSDT_20260604040404000000",
            ],
        }
    )

    assert rows == [
        {
            "block_id": "bnb_spot_BTCUSDT_20260601010101000000",
            "reason": "manager_close_requested",
        },
        {
            "block_id": "bnb_futures_ETHUSDT_20260602020202000000",
            "reason": "manager_stale_waiting_block",
        },
        {
            "block_id": "bnb_futures_SOLUSDT_20260603030303000000",
            "reason": "manager_stale_waiting_block",
        },
    ]


def test_infer_manager_contract_side_prefers_price_geometry_over_mixed_text() -> None:
    assert (
        infer_manager_contract_side(
            {
                "market": "futures",
                "entry_price": 100.0,
                "target_price": 94.0,
                "stop_price": 103.0,
                "claim": "mixed long and short wording",
            },
            candidate={},
            row={},
        )
        == "short"
    )
    assert (
        infer_manager_contract_side(
            {
                "market": "spot",
                "entry_price": 100.0,
                "target_price": 112.0,
                "stop_price": 96.0,
                "claim": "mixed long and short wording",
            },
            candidate={},
            row={},
        )
        == "long"
    )


def test_apply_manager_contract_aliases_maps_calculated_and_quote_budget_fields() -> None:
    row = {
        "market_or_account_scope": "upbit_spot",
        "quantity_or_quote_budget": 125000,
        "calculated_price_plan": {
            "entry_price": 15.2,
            "target_price": 18.4,
            "stop_price": 13.9,
            "entry_trigger_price": 15.1,
            "entry_trigger_operator": "<=",
        },
    }

    apply_manager_contract_aliases(row)

    assert row["market"] == "upbit_spot"
    assert row["venue"] == "upbit_spot"
    assert row["entry_price"] == 15.2
    assert row["target_price"] == 18.4
    assert row["stop_price"] == 13.9
    assert row["quote_budget_krw"] == 125000
    assert row["quote_currency"] == "KRW"
    assert row["entry_trigger_operator"] == "<="


def test_normalize_manager_hold_decision_enriches_sparse_note_from_payload() -> None:
    response = {
        "hold_decision": {
            "summary": "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다.",
            "reasons": ["이번 사이클에서는 실행할 블록 변화가 없습니다."],
        },
        "payload": {
            "symbol": "BTCUSDT",
            "market": "futures",
            "claim": "눌림 가격까지 기다린다.",
            "reasons": ["추격 진입은 피한다."],
            "entry_price": "61004.36",
            "entry_style": "pullback <= 61004.36",
            "risk_note": "숏 스퀴즈 위험",
            "watch_symbols": ["ETHUSDT", "BTCUSDT"],
            "next_actions": ["BTCUSDT 대기블록 후보 유지"],
        },
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": [], "pause_blocks": []},
        symbols=["SOLUSDT"],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["summary"] == "눌림 가격까지 기다린다."
    assert decision["reasons"] == ["추격 진입은 피한다."]
    assert decision["watch_symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert decision["next_triggers"] == [
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "condition": "pullback <= 61004.36",
            "price": 61004.36,
            "reason": "눌림 가격까지 기다린다.",
        }
    ]
    assert decision["planned_actions"] == ["BTCUSDT 대기블록 후보 유지"]
    assert decision["risk_notes"] == ["숏 스퀴즈 위험"]
    assert decision["action_count"] == 0


def test_manager_action_count_counts_allowed_action_rows_only() -> None:
    assert (
        manager_action_count(
            {
                "create_blocks": [{"symbol": "BTCUSDT"}],
                "update_blocks": [],
                "noise": [{"symbol": "ETHUSDT"}],
                "pause_blocks": [{"block_id": "a"}, {"block_id": "b"}],
            },
            allowed_actions=("create_blocks", "update_blocks", "pause_blocks"),
        )
        == 3
    )


def test_raise_for_manager_llm_error_payload_keeps_action_payloads_valid() -> None:
    try:
        raise_for_manager_llm_error_payload(
            {"error": "model warning", "create_blocks": [{"symbol": "BTCUSDT"}]},
            allowed_actions=("create_blocks", "update_blocks"),
        )
    except RuntimeError as exc:  # pragma: no cover - assertion message path
        raise AssertionError("action payload with error metadata should remain usable") from exc

    try:
        raise_for_manager_llm_error_payload(
            {"ok": False, "error": "native session failed"},
            allowed_actions=("create_blocks", "update_blocks"),
        )
    except RuntimeError as exc:
        assert str(exc) == "native session failed"
    else:  # pragma: no cover - assertion message path
        raise AssertionError("expected runtime error")


def test_manager_actions_from_contract_payload_normalizes_create_and_close_rows() -> None:
    def normalize_create_payload(row: dict[str, object]) -> dict[str, object]:
        return {"normalized": True, **row}

    actions = manager_actions_from_contract_payload(
        {
            "payload": {
                "decision": "create_block",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
            },
            "selected_contracts": [
                {
                    "decision": "close_block",
                    "block_id": "bnb_futures_ETHUSDT_20260602020202000000",
                }
            ],
            "next_actions": [
                "close stale proposed block: bnb_futures_SOLUSDT_20260603030303000000"
            ],
        },
        normalize_create_payload=normalize_create_payload,
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert actions["create_blocks"] == [
        {
            "normalized": True,
            "decision": "create_block",
            "symbol": "BTCUSDT",
            "market_or_account_scope": "futures",
        }
    ]
    assert actions["close_blocks"] == [
        {
            "decision": "close_block",
            "block_id": "bnb_futures_ETHUSDT_20260602020202000000",
        }
    ]


def test_validate_manager_actions_uses_contract_payload_when_explicit_actions_empty() -> None:
    def normalize_create_payload(row: dict[str, object]) -> dict[str, object]:
        return {"normalized": True, **row}

    actions = validate_manager_actions(
        {
            "payload": {
                "decision": "create_block",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
            }
        },
        normalize_create_payload=normalize_create_payload,
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert actions["create_blocks"][0]["normalized"] is True
    assert actions["create_blocks"][0]["symbol"] == "BTCUSDT"
    assert actions["update_blocks"] == []


def test_validate_manager_actions_infers_adopt_existing_blocks_from_contract_payload() -> None:
    actions = validate_manager_actions(
        {
            "payload": {
                "decision": "adopt_existing_block",
                "symbol": "BTCUSDT",
                "market": "spot",
                "qty": 0.15,
                "target_price": 110_000.0,
                "stop_price": 95_000.0,
                "adoption_note": "기존 현물 지갑 보유분을 신규 주문 없이 블록 원장에 흡수",
            }
        },
        normalize_create_payload=lambda row: {"normalized": True, **row},
        allowed_actions=(
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        ),
    )

    assert actions["adopt_existing_blocks"] == [
        {
            "decision": "adopt_existing_block",
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.15,
            "target_price": 110_000.0,
            "stop_price": 95_000.0,
            "adoption_note": "기존 현물 지갑 보유분을 신규 주문 없이 블록 원장에 흡수",
        }
    ]
    assert actions["create_blocks"] == []


def test_validate_manager_actions_rejects_non_list_action_fields() -> None:
    try:
        validate_manager_actions(
            {"create_blocks": {"symbol": "BTCUSDT"}},
            normalize_create_payload=lambda row: dict(row),
            allowed_actions=("create_blocks",),
        )
    except ValueError as exc:
        assert str(exc) == "create_blocks must be a list"
    else:  # pragma: no cover - assertion message path
        raise AssertionError("expected value error")
