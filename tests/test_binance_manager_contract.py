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


def test_normalize_manager_hold_decision_merges_payload_and_validation_repair_context() -> None:
    response = {
        "hold_decision": {
            "summary": "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다.",
            "reasons": ["이번 사이클에서는 실행할 블록 변화가 없습니다."],
        },
        "payload": {
            "symbol": "BTCUSDT",
            "market": "futures",
            "claim": "BTC는 눌림 가격까지 기다린다.",
            "reasons": ["BTC 추격 진입은 피한다."],
            "entry_price": "61004.36",
            "entry_style": "BTCUSDT <= 61004.36",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["BTC funding refresh needed"],
        },
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "pattern prior missing",
                    "next_trigger": "ESPUSDT >= 0.07156 and stop risk <= 3%",
                }
            ],
        },
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["summary"] == "BTC는 눌림 가격까지 기다린다."
    assert decision["reasons"] == [
        "BTC 추격 진입은 피한다.",
        "pattern prior missing",
    ]
    assert decision["watch_symbols"] == ["BTCUSDT", "ETHUSDT", "ESPUSDT"]
    assert decision["next_triggers"] == [
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "condition": "BTCUSDT <= 61004.36",
            "price": 61004.36,
            "reason": "BTC는 눌림 가격까지 기다린다.",
        },
        {
            "symbol": "ESPUSDT",
            "market": "futures",
            "condition": "ESPUSDT >= 0.07156 and stop risk <= 3%",
            "price": 0.07156,
            "reason": "pattern prior missing",
        },
    ]
    assert decision["data_gaps"] == [
        "BTC funding refresh needed",
        "pattern prior missing",
    ]


def test_normalize_manager_hold_decision_merges_repair_context_with_non_sparse_raw_hold() -> None:
    response = {
        "hold_decision": {
            "summary": "기존 관망 사유를 유지한다.",
            "reasons": ["raw reason"],
            "watch_symbols": ["SOLUSDT"],
            "next_triggers": [
                {
                    "symbol": "SOLUSDT",
                    "market": "futures",
                    "condition": "SOLUSDT <= 76.5",
                    "reason": "raw trigger",
                }
            ],
            "data_gaps": ["raw gap"],
        },
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "evidence_gap": "pattern prior missing",
                    "next_trigger": "ESPUSDT >= 0.07156",
                }
            ],
        },
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["summary"] == "기존 관망 사유를 유지한다."
    assert decision["reasons"] == ["raw reason", "pattern prior missing"]
    assert decision["watch_symbols"] == ["SOLUSDT", "ESPUSDT"]
    assert decision["next_triggers"] == [
        {
            "symbol": "SOLUSDT",
            "market": "futures",
            "condition": "SOLUSDT <= 76.5",
            "price": 76.5,
            "reason": "raw trigger",
        },
        {
            "symbol": "ESPUSDT",
            "market": "futures",
            "condition": "ESPUSDT >= 0.07156",
            "price": 0.07156,
            "reason": "pattern prior missing",
        },
    ]
    assert decision["data_gaps"] == ["raw gap", "pattern prior missing"]


def test_normalize_manager_hold_decision_merges_raw_triggers_alias_with_repair_context() -> None:
    response = {
        "hold_decision": {
            "summary": "기존 triggers alias를 유지한다.",
            "triggers": [
                {
                    "symbol": "SOLUSDT",
                    "market": "futures",
                    "condition": "SOLUSDT <= 76.5",
                    "reason": "raw trigger alias",
                }
            ],
        },
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "evidence_gap": "pattern prior missing",
                    "next_trigger": "ESPUSDT >= 0.07156",
                }
            ],
        },
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["next_triggers"] == [
        {
            "symbol": "SOLUSDT",
            "market": "futures",
            "condition": "SOLUSDT <= 76.5",
            "price": 76.5,
            "reason": "raw trigger alias",
        },
        {
            "symbol": "ESPUSDT",
            "market": "futures",
            "condition": "ESPUSDT >= 0.07156",
            "price": 0.07156,
            "reason": "pattern prior missing",
        },
    ]


def test_normalize_manager_hold_decision_preserves_structured_entry_trigger_price() -> None:
    response = {
        "hold_decision": {
            "summary": "조건만 대기한다.",
            "next_triggers": [
                {
                    "symbol": "ADAUSDT",
                    "market": "futures",
                    "condition": "fresh support retest",
                    "entry_trigger_price": 0.52,
                    "reason": "separate structured trigger price",
                }
            ],
        }
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["next_triggers"] == [
        {
            "symbol": "ADAUSDT",
            "market": "futures",
            "condition": "fresh support retest",
            "price": 0.52,
            "reason": "separate structured trigger price",
        }
    ]


def test_normalize_manager_hold_decision_preserves_payload_entry_trigger_price() -> None:
    response = {
        "payload": {
            "symbol": "ADAUSDT",
            "market": "futures",
            "claim": "지지 재확인을 기다린다.",
            "entry_style": "fresh support retest",
            "entry_trigger_price": 0.52,
        }
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["next_triggers"] == [
        {
            "symbol": "ADAUSDT",
            "market": "futures",
            "condition": "fresh support retest",
            "price": 0.52,
            "reason": "지지 재확인을 기다린다.",
        }
    ]


def test_normalize_manager_hold_decision_promotes_validation_repair_triggers() -> None:
    response = {
        "hold_decision": {
            "summary": "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다.",
            "reasons": ["이번 사이클에서는 실행할 블록 변화가 없습니다."],
            "next_triggers": [],
        },
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "pattern prior is missing and stop risk exceeds cap",
                    "next_trigger": "ESPUSDT >= 0.07156 and stop risk <= 3%",
                    "entry_trigger_price": 0.07156,
                },
                {
                    "symbol": "CHIPUSDT",
                    "market": "spot",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "confidence below waiting-entry threshold",
                    "next_trigger": "confidence >= 0.58 with RR >= 2.0",
                },
            ],
        },
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": [], "pause_blocks": []},
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["watch_symbols"] == ["ESPUSDT", "CHIPUSDT"]
    assert decision["next_triggers"] == [
        {
            "symbol": "ESPUSDT",
            "market": "futures",
            "condition": "ESPUSDT >= 0.07156 and stop risk <= 3%",
            "price": 0.07156,
            "reason": "pattern prior is missing and stop risk exceeds cap",
        },
        {
            "symbol": "CHIPUSDT",
            "market": "spot",
            "condition": "confidence >= 0.58 with RR >= 2.0",
            "price": 0.0,
            "reason": "confidence below waiting-entry threshold",
        },
    ]
    assert decision["data_gaps"] == [
        "pattern prior is missing and stop risk exceeds cap",
        "confidence below waiting-entry threshold",
    ]


def test_normalize_manager_hold_decision_infers_symbol_anchored_trigger_price() -> None:
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "evidence_gap": "pattern prior missing",
                    "next_trigger": "ESPUSDT >= 0.07156 and stop risk <= 3%",
                },
                {
                    "symbol": "CHIPUSDT",
                    "market": "spot",
                    "evidence_gap": "confidence below threshold",
                    "next_trigger": "confidence >= 0.58 with RR >= 2.0",
                },
                {
                    "symbol": "SOLUSDT",
                    "market": "futures",
                    "evidence_gap": "waiting for pullback",
                    "next_trigger": "76.553 이하 눌림과 동시에 confidence >= 0.58",
                },
                {
                    "symbol": "TUTUSDT",
                    "market": "futures",
                    "evidence_gap": "confidence still low",
                    "next_trigger": "0.032926 이하 대기 가격에서 신뢰도 0.58 이상",
                },
                {
                    "symbol": "KRW-PENGU",
                    "market": "upbit_spot",
                    "evidence_gap": "confidence still low",
                    "next_trigger": "9.1183 이하 진입가, 8.9552 손절, 9.371 목표",
                },
                {
                    "symbol": "XPLUSDT",
                    "market": "futures",
                    "evidence_gap": "stop risk too wide",
                    "next_trigger": "stop risk <= 3%",
                },
                {
                    "symbol": "CONFUSDT",
                    "market": "futures",
                    "evidence_gap": "confidence still low",
                    "next_trigger": "신뢰도 0.58 이상이면 재검토",
                },
                {
                    "symbol": "RRUSDT",
                    "market": "futures",
                    "evidence_gap": "reward risk still low",
                    "next_trigger": "RR 2.0 이상이면 재검토",
                },
            ]
        }
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["next_triggers"] == [
        {
            "symbol": "ESPUSDT",
            "market": "futures",
            "condition": "ESPUSDT >= 0.07156 and stop risk <= 3%",
            "price": 0.07156,
            "reason": "pattern prior missing",
        },
        {
            "symbol": "CHIPUSDT",
            "market": "spot",
            "condition": "confidence >= 0.58 with RR >= 2.0",
            "price": 0.0,
            "reason": "confidence below threshold",
        },
        {
            "symbol": "SOLUSDT",
            "market": "futures",
            "condition": "76.553 이하 눌림과 동시에 confidence >= 0.58",
            "price": 76.553,
            "reason": "waiting for pullback",
        },
        {
            "symbol": "TUTUSDT",
            "market": "futures",
            "condition": "0.032926 이하 대기 가격에서 신뢰도 0.58 이상",
            "price": 0.032926,
            "reason": "confidence still low",
        },
        {
            "symbol": "KRW-PENGU",
            "market": "upbit_spot",
            "condition": "9.1183 이하 진입가, 8.9552 손절, 9.371 목표",
            "price": 9.1183,
            "reason": "confidence still low",
        },
        {
            "symbol": "XPLUSDT",
            "market": "futures",
            "condition": "stop risk <= 3%",
            "price": 0.0,
            "reason": "stop risk too wide",
        },
        {
            "symbol": "CONFUSDT",
            "market": "futures",
            "condition": "신뢰도 0.58 이상이면 재검토",
            "price": 0.0,
            "reason": "confidence still low",
        },
        {
            "symbol": "RRUSDT",
            "market": "futures",
            "condition": "RR 2.0 이상이면 재검토",
            "price": 0.0,
            "reason": "reward risk still low",
        },
    ]


def test_normalize_manager_hold_decision_ignores_metric_particles_as_prices() -> None:
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "KCONFUSDT",
                    "market": "futures",
                    "evidence_gap": "confidence still low",
                    "next_trigger": "신뢰도는 0.58 이상이면 재검토",
                },
                {
                    "symbol": "KRRUSDT",
                    "market": "futures",
                    "evidence_gap": "reward risk still low",
                    "next_trigger": "RR은 2.0 이상이면 재검토",
                },
                {
                    "symbol": "RISKUSDT",
                    "market": "futures",
                    "evidence_gap": "risk still wide",
                    "next_trigger": "손절 위험은 3 이상이면 보류",
                },
            ]
        }
    }

    decision = normalize_manager_hold_decision(
        response=response,
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        symbols=[],
        allowed_actions=("create_blocks", "update_blocks", "close_blocks", "pause_blocks"),
    )

    assert decision["next_triggers"] == [
        {
            "symbol": "KCONFUSDT",
            "market": "futures",
            "condition": "신뢰도는 0.58 이상이면 재검토",
            "price": 0.0,
            "reason": "confidence still low",
        },
        {
            "symbol": "KRRUSDT",
            "market": "futures",
            "condition": "RR은 2.0 이상이면 재검토",
            "price": 0.0,
            "reason": "reward risk still low",
        },
        {
            "symbol": "RISKUSDT",
            "market": "futures",
            "condition": "손절 위험은 3 이상이면 보류",
            "price": 0.0,
            "reason": "risk still wide",
        },
    ]


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
