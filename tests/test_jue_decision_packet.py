from __future__ import annotations

from tradecraft.services.jue_decision_packet import (
    CANONICAL_DECISION_PACKET_PROMPT_VERSION,
    DECISION_PACKET_REQUIRED_SECTIONS,
    DECISION_PACKET_VERSION,
    build_canonical_decision_packet_prompt_section,
    build_canonical_decision_prompt_bundle,
    build_decision_lifecycle_packet,
    build_decision_packet,
    decision_packet_contract,
    validate_decision_packet_contract,
)


def test_decision_packet_contract_defines_shared_canonical_schema() -> None:
    contract = decision_packet_contract(target_scope="binance")

    assert contract["version"] == DECISION_PACKET_VERSION
    assert contract["target_scope"] == "binance"
    assert contract["producer"] == "tradecraft.services.jue_decision_packet.build_decision_packet"
    assert contract["required_sections"] == list(DECISION_PACKET_REQUIRED_SECTIONS)
    assert "account_pressure" in contract["required_sections"]
    assert "risk_budget" in contract["required_sections"]


def test_decision_packet_contract_catalogs_sections_and_prompt_fields() -> None:
    contract = decision_packet_contract(target_scope="kis")

    assert set(contract["section_catalog"]) == set(DECISION_PACKET_REQUIRED_SECTIONS)
    assert contract["section_catalog"]["source_coverage"] == {
        "role": "source_freshness_and_gap_map",
        "shape": "aggregate_with_by_source",
        "required": True,
    }
    assert contract["venue_profiles"]["kis"]["base_currency"] == "KRW"
    assert contract["venue_profiles"]["binance"]["supports_short"] is True
    assert contract["prompt_fields"] == {
        "canonical_packet": "decision_packet_v2",
        "legacy_packet": "decision_packet",
        "prompt_section": "canonical_decision_packet",
    }


def test_build_decision_packet_embeds_canonical_schema_contract() -> None:
    packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
    )

    assert packet["schema"]["version"] == DECISION_PACKET_VERSION
    assert packet["schema"]["target_scope"] == "shared"
    assert packet["schema"]["required_sections"] == list(DECISION_PACKET_REQUIRED_SECTIONS)
    assert packet["contract_validation"]["status"] == "ok"
    assert packet["contract_validation"]["missing_required_sections"] == []


def test_build_decision_packet_embeds_explicit_target_scope() -> None:
    packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
        target_scope="kis",
    )

    assert packet["schema"]["target_scope"] == "kis"
    assert packet["contract_validation"]["target_scope"] == "kis"


def test_validate_decision_packet_contract_reports_missing_sections_and_scope_drift() -> None:
    validation = validate_decision_packet_contract(
        {
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "version": DECISION_PACKET_VERSION,
                "target_scope": "kis",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "venue_profile": {"target_scope": "binance"},
            "account_pressure": {},
        },
        expected_target_scope="kis",
    )

    assert validation["status"] == "invalid"
    assert validation["version"] == DECISION_PACKET_VERSION
    assert validation["target_scope"] == "kis"
    assert validation["expected_target_scope"] == "kis"
    assert validation["scope_mismatch"] is True
    assert "blocks" in validation["missing_required_sections"]
    assert "account_pressure" in validation["present_required_sections"]


def test_build_decision_packet_embeds_canonical_venue_profile() -> None:
    kis_packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
        target_scope="kis",
    )
    binance_packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
        target_scope="binance",
    )

    assert "venue_profile" in DECISION_PACKET_REQUIRED_SECTIONS
    assert kis_packet["venue_profile"] == {
        "target_scope": "kis",
        "venue": "kis",
        "asset_class": "kr_equity",
        "base_currency": "KRW",
        "price_unit": "KRW",
        "quantity_unit": "shares",
        "trading_session": "krx_regular",
        "supports_short": False,
    }
    assert binance_packet["venue_profile"] == {
        "target_scope": "binance",
        "venue": "binance",
        "asset_class": "crypto",
        "base_currency": "USDT",
        "price_unit": "USDT",
        "quantity_unit": "asset_units",
        "trading_session": "24h",
        "supports_short": True,
    }


def test_build_decision_packet_normalizes_source_coverage() -> None:
    packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
        target_scope="kis",
        source_context={
            "reports": {"status": "ok", "item_count": "42", "as_of": "2026-06-21"},
            "whale": {"status": "error", "error_message": "timeout"},
            "three_thirty": {"status": "stale", "age_minutes": "180"},
            "ignored_empty": {},
            "crypto_research": {"status": "ok", "symbol_count": 300},
        },
    )

    coverage = packet["source_coverage"]

    assert coverage["source_count"] == 4
    assert coverage["ok_count"] == 2
    assert coverage["warning_count"] == 1
    assert coverage["error_count"] == 1
    assert coverage["by_source"]["reports"] == {
        "source_id": "reports",
        "status": "ok",
        "item_count": 42,
        "symbol_count": None,
        "age_minutes": None,
        "as_of": "2026-06-21",
        "error_message": "",
    }
    assert coverage["by_source"]["whale"]["error_message"] == "timeout"
    assert "source_coverage" in packet["schema"]["required_sections"]
    assert (
        "Treat stale or failed source_coverage entries as explicit decision gaps."
        in packet["llm_focus_questions"]
    )


def test_canonical_decision_packet_prompt_section_marks_primary_packet_and_gaps() -> None:
    section = build_canonical_decision_packet_prompt_section(
        target_scope="binance",
        decision_packet_v2={
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "target_scope": "binance",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "account_pressure": {"cash": 100},
            "risk_budget": {"status": "ok"},
        },
        legacy_decision_packet={"target_scope": "legacy-binance"},
    )

    assert section["version"] == CANONICAL_DECISION_PACKET_PROMPT_VERSION
    assert section["target_scope"] == "binance"
    assert section["schema_version"] == DECISION_PACKET_VERSION
    assert section["primary_prompt_key"] == "decision_packet_v2"
    assert section["legacy_prompt_key"] == "decision_packet"
    assert section["present_sections"] == ["account_pressure", "risk_budget"]
    assert "blocks" in section["missing_required_sections"]
    assert section["legacy_context_mode"] == "policy_metadata_only"
    assert section["section_catalog"]["source_coverage"]["role"] == (
        "source_freshness_and_gap_map"
    )
    assert section["prompt_fields"]["canonical_packet"] == "decision_packet_v2"


def test_canonical_decision_packet_prompt_section_exposes_packet_identity() -> None:
    section = build_canonical_decision_packet_prompt_section(
        target_scope="kis",
        decision_packet_v2={
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "version": DECISION_PACKET_VERSION,
                "target_scope": "kis",
                "producer": "tradecraft.services.jue_decision_packet.build_decision_packet",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "venue_profile": {"target_scope": "kis"},
            "account_pressure": {"cash": 100},
            "risk_budget": {"status": "ok"},
        },
        legacy_decision_packet={"target_scope": "kis"},
    )

    assert section["packet_identity"] == {
        "version": CANONICAL_DECISION_PACKET_PROMPT_VERSION,
        "schema_version": DECISION_PACKET_VERSION,
        "target_scope": "kis",
        "producer": "tradecraft.services.jue_decision_packet.build_decision_packet",
        "primary_prompt_key": "decision_packet_v2",
        "legacy_prompt_key": "decision_packet",
        "required_section_count": len(DECISION_PACKET_REQUIRED_SECTIONS),
        "present_section_count": 3,
        "legacy_context_mode": "policy_metadata_only",
    }


def test_canonical_decision_packet_prompt_section_exposes_contract_validation() -> None:
    section = build_canonical_decision_packet_prompt_section(
        target_scope="kis",
        decision_packet_v2={
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "target_scope": "kis",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "venue_profile": {"target_scope": "binance"},
            "account_pressure": {"cash": 100},
        },
    )

    validation = section["contract_validation"]

    assert validation["status"] == "invalid"
    assert validation["expected_target_scope"] == "kis"
    assert validation["scope_mismatch"] is True
    assert "blocks" in validation["missing_required_sections"]
    assert section["decision_packet_status"] == "invalid"
    assert "decision_packet_contract_invalid" in section["decision_warnings"]
    assert "decision_packet_scope_mismatch" in section["decision_warnings"]
    assert "decision_packet_missing_sections" in section["decision_warnings"]


def test_canonical_decision_prompt_bundle_standardizes_input_order_and_policy() -> None:
    bundle = build_canonical_decision_prompt_bundle(
        target_scope="binance",
        decision_packet_v2={
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "target_scope": "binance",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "account_pressure": {"cash": 100},
            "risk_budget": {"status": "ok"},
        },
        legacy_decision_packet={"target_scope": "binance"},
        base_inputs=["account", "memory", "account"],
        extra_inputs=["risk_guard", "blocks", "decision_packet_v2"],
    )

    assert bundle["canonical_decision_packet"]["target_scope"] == "binance"
    assert bundle["decision_packet_policy"].startswith(
        "decision_packet_v2 is the canonical normalized decision context."
    )
    assert bundle["decision_inputs"] == [
        "account",
        "memory",
        "canonical_decision_packet",
        "decision_packet_v2",
        "decision_packet",
        "risk_guard",
        "blocks",
    ]


def test_canonical_decision_prompt_bundle_can_insert_lifecycle_before_legacy_packet() -> None:
    bundle = build_canonical_decision_prompt_bundle(
        target_scope="kis",
        decision_packet_v2={
            "version": DECISION_PACKET_VERSION,
            "schema": {
                "target_scope": "kis",
                "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
            },
            "account_pressure": {"cash": 100},
            "risk_budget": {"status": "ok"},
        },
        legacy_decision_packet={"target_scope": "kis"},
        base_inputs=["account", "blocks"],
        lifecycle_packet_key="decision_lifecycle_v3",
        extra_inputs=["candidate_policy_impacts"],
    )

    assert bundle["decision_inputs"] == [
        "account",
        "blocks",
        "canonical_decision_packet",
        "decision_packet_v2",
        "decision_lifecycle_v3",
        "decision_packet",
        "candidate_policy_impacts",
    ]
    assert "Crypto-specific policies are not KIS asset evidence" in bundle[
        "decision_packet_policy"
    ]


def test_cash_deployment_ratio_and_exposure_aggregation() -> None:
    packet = build_decision_packet(
        account={
            "cash": "250,000",
            "total_equity": 1_000_000,
            "positions": [
                {"symbol": "005930", "market_value": 400_000},
                {"symbol": "000660", "value_krw": 350_000},
            ],
        },
        blocks=[
            {
                "block_id": "b1",
                "symbol": "005930",
                "status": "open",
                "metadata": {"horizon": "short"},
                "qty_open": 3,
                "entry_price": "100000",
            },
            {
                "block_id": "b2",
                "symbol": "005930",
                "status": "entry_pending",
                "horizon": "mid",
                "qty_open": 2,
                "entry_price": "200000",
            },
            {
                "block_id": "b3",
                "symbol": "069500",
                "status": "exit_pending",
                "horizon": "core_etf",
                "qty_open": 5,
                "entry_price": "10000",
            },
            {
                "block_id": "b4",
                "symbol": "000660",
                "status": "closed",
                "horizon": "long",
                "qty_open": 99,
                "entry_price": "1",
            },
        ],
        quotes={},
        recent_events=[],
        previous_manager_runs=[],
    )

    assert packet["version"] == "decision_packet_v2"
    assert packet["account_pressure"]["cash"] == 250_000
    assert packet["account_pressure"]["total_equity"] == 1_000_000
    assert packet["account_pressure"]["deployment_ratio"] == 0.75
    assert packet["position_pressure"]["by_symbol"]["005930"]["exposure"] == 700_000
    assert packet["position_pressure"]["by_horizon"]["short"]["exposure"] == 300_000
    assert packet["position_pressure"]["by_horizon"]["mid"]["exposure"] == 400_000
    assert packet["position_pressure"]["by_horizon"]["core_etf"]["exposure"] == 50_000
    assert packet["block_state"]["counts_by_status"]["open"] == 1
    assert packet["block_state"]["counts_by_horizon"]["core_etf"] == 1
    assert packet["risk_budget"]["pending_exit_count"] == 1


def test_stale_and_error_quote_flags_are_surfaced() -> None:
    packet = build_decision_packet(
        account={"cash": 100, "total_equity": 1000},
        blocks=[],
        quotes={
            "005930": {
                "symbol": "005930",
                "price": "75,000",
                "raw": {
                    "stck_prdy_ctrt": "+2.5%",
                    "stck_hgpr": "76000",
                    "stck_lwpr": "73000",
                    "acml_vol": "1,234",
                    "acml_tr_pbmn": "987654321",
                },
            },
            "000660": {
                "symbol": "000660",
                "status": "error",
                "error_message": "quote failed",
                "raw": {},
            },
            "069500": {
                "symbol": "069500",
                "stale": True,
                "raw": {"prdy_ctrt": "-1.2"},
            },
        },
        recent_events=[],
        previous_manager_runs=[],
    )

    quote = packet["quote_regime"]["by_symbol"]["005930"]
    assert quote["day_change_pct"] == 2.5
    assert quote["intraday_range_pct"] == 4.0
    assert quote["volume"] == 1234
    assert quote["value_proxy"] == 987654321
    assert packet["quote_regime"]["by_symbol"]["000660"]["has_error"] is True
    assert packet["quote_regime"]["by_symbol"]["069500"]["is_stale"] is True
    assert packet["quote_regime"]["stale_or_error_count"] == 2
    assert "Review symbols with stale or failed quote data before sizing new actions." in packet["llm_focus_questions"]


def test_mid_block_stop_touch_requires_manager_review_not_immediate_exit() -> None:
    packet = build_decision_packet(
        account={"cash_krw": 1_000_000, "positions": []},
        blocks=[
            {
                "block_id": "blk_mid",
                "symbol": "012330",
                "name": "현대모비스",
                "status": "open",
                "qty_open": 1,
                "entry_price": 546000,
                "target_price": 563000,
                "stop_price": 520000,
                "metadata": {"horizon": "mid"},
            }
        ],
        quotes=[
            {
                "symbol": "012330",
                "price": 520000,
                "raw": {
                    "stck_oprc": "532000",
                    "stck_hgpr": "561000",
                    "stck_lwpr": "515000",
                    "acml_vol": "787096",
                    "prdy_ctrt": "-0.95",
                    "pgtr_ntby_qty": "-236120",
                },
            }
        ],
        recent_events=[
            {
                "block_id": "blk_mid",
                "event_type": "exit_signal",
                "message": "mid block touched stop_reached; manager review will decide action",
                "payload": {"reason": "stop_reached", "price": 520000},
                "created_at": "2026-05-20T04:55:05+00:00",
            }
        ],
        previous_manager_runs=[],
        market_pulse={"status": "ok", "regime": "risk_off"},
    )

    block = packet["blocks"][0]
    assert block["horizon"] == "mid"
    assert block["stop_policy"]["touch_action"] == "manager_review"
    assert block["stop_policy"]["latest_signal"]["reason"] == "stop_reached"
    assert block["technical"]["intraday_position_pct"] == 10.87
    assert block["technical"]["program_net_qty"] == -236120


def test_short_block_stop_touch_is_rule_exit() -> None:
    packet = build_decision_packet(
        account={"cash_krw": 1_000_000, "positions": []},
        blocks=[
            {
                "block_id": "blk_short",
                "symbol": "277810",
                "name": "레인보우로보틱스",
                "status": "open",
                "qty_open": 2,
                "entry_price": 624000,
                "target_price": 650000,
                "stop_price": 622000,
                "metadata": {"horizon": "short"},
            }
        ],
        quotes=[
            {
                "symbol": "277810",
                "price": 621000,
                "raw": {"stck_hgpr": "635000", "stck_lwpr": "620000"},
            }
        ],
        recent_events=[],
        previous_manager_runs=[],
        market_pulse={},
    )

    assert packet["blocks"][0]["stop_policy"]["touch_action"] == "rule_exit"


def test_recent_rule_events_are_grouped_by_block_and_symbol() -> None:
    packet = build_decision_packet(
        account={},
        blocks=[{"block_id": "b1", "symbol": "005930", "status": "open"}],
        quotes={},
        recent_events=[
            {
                "block_id": "b1",
                "event_type": "target_reached",
                "message": "target touched",
                "created_at": "2026-05-19T01:00:00+00:00",
            },
            {
                "block_id": "b1",
                "symbol": "005930",
                "event_type": "stop_reached",
                "message": "stop touched",
            },
            {
                "block_id": "b2",
                "symbol": "000660",
                "event_type": "note",
            },
        ],
        previous_manager_runs=[],
    )

    assert packet["recent_rule_events"]["event_count"] == 2
    assert packet["recent_rule_events"]["by_block"]["b1"]["event_types"] == [
        "target_reached",
        "stop_reached",
    ]
    assert packet["recent_rule_events"]["by_symbol"]["005930"]["event_count"] == 2
    assert "note" not in packet["recent_rule_events"]["by_block"].get("b2", {})


def test_previous_manager_outcomes_are_summarized() -> None:
    packet = build_decision_packet(
        account={},
        blocks=[],
        quotes={},
        recent_events=[],
        previous_manager_runs=[
            {
                "id": 10,
                "run_at": "2026-05-19T00:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "actions": {
                    "create_blocks": [{"symbol": "005930"}],
                    "adopt_existing_blocks": [{"symbol": "000660"}],
                    "update_blocks": [{"block_id": "b1"}],
                    "close_blocks": [{"block_id": "b2"}],
                },
                "applied": {
                    "created": [{"block_id": "new"}],
                    "adopted": [{"block_id": "adopted"}],
                    "updated": [{"block_id": "b1"}],
                    "rejected": [{"reason": "create_not_allowed"}],
                },
            },
            {
                "run_id": 11,
                "status": "llm_unavailable",
                "mode": "deterministic",
                "error_message": "bridge down",
                "actions": {},
            },
        ],
    )

    assert packet["previous_decision_outcomes"]["run_count"] == 2
    assert packet["previous_decision_outcomes"]["action_totals"]["create_blocks"] == 1
    assert packet["previous_decision_outcomes"]["action_totals"]["adopt_existing_blocks"] == 1
    assert packet["previous_decision_outcomes"]["applied_totals"]["created"] == 1
    assert packet["previous_decision_outcomes"]["applied_totals"]["rejected"] == 1
    assert packet["previous_decision_outcomes"]["last_run"]["run_id"] == 11
    assert packet["previous_decision_outcomes"]["last_error"] == "bridge down"


def test_recent_execution_summary_counts_sell_reasons_and_exit_signals() -> None:
    packet = build_decision_packet(
        account={},
        blocks=[],
        quotes=[],
        recent_events=[
            {
                "block_id": "blk_mid",
                "event_type": "order",
                "message": "sell 1 @ 525000 sent",
                "payload": {"reason": "force_exit_requested", "side": "sell"},
                "created_at": "2026-05-20T05:04:46+00:00",
            },
            {
                "block_id": "blk_mid",
                "event_type": "exit_signal",
                "payload": {"reason": "stop_reached", "price": 520000},
            },
        ],
        previous_manager_runs=[
            {
                "id": 111,
                "run_at": "2026-05-20T05:04:20+00:00",
                "status": "ok",
                "model": "gpt-5.5",
                "actions": {"close_blocks": [{"block_id": "blk_mid"}]},
            }
        ],
        market_pulse={},
    )

    review = packet["previous_decision_reviews"][0]
    assert review["run_id"] == 111
    assert review["action_counts"]["close_blocks"] == 1
    assert packet["recent_execution_summary"]["sell_reasons"]["force_exit_requested"] == 1
    assert packet["recent_execution_summary"]["exit_signals"]["stop_reached"] == 1


def test_core_etf_alias_and_error_messages_are_compacted() -> None:
    packet = build_decision_packet(
        account={"cash": 100_000, "total_equity": 200_000},
        blocks=[
            {
                "block_id": "blk_etf",
                "symbol": "069500",
                "status": "open",
                "qty_open": 2,
                "entry_price": 50_000,
                "metadata": {"horizon": "etf_core"},
            }
        ],
        quotes={},
        recent_events=[],
        previous_manager_runs=[
            {
                "id": 1,
                "status": "llm_unavailable",
                "error_message": "ERR " + ("x" * 5000),
            }
        ],
    )

    assert packet["position_pressure"]["by_horizon"]["core_etf"]["exposure"] == 100_000
    assert packet["position_pressure"]["active_core_etf_exposure"] == 100_000
    assert len(packet["previous_decision_reviews"][0]["error_message"]) == 600
    assert len(packet["previous_decision_outcomes"]["last_error"]) == 600


def test_missing_fields_have_deterministic_fallbacks() -> None:
    first = build_decision_packet(
        account={"cash": "bad"},
        blocks=[{"block_id": "b1", "symbol": "005930", "status": "open"}],
        quotes={"005930": {"raw": {"stck_prdy_ctrt": "--"}}},
        recent_events=[{"event_type": "exit_signal", "payload": {"symbol": "005930"}}],
        previous_manager_runs=[{"actions": None}],
    )
    second = build_decision_packet(
        account={"cash": "bad"},
        blocks=[{"block_id": "b1", "symbol": "005930", "status": "open"}],
        quotes={"005930": {"raw": {"stck_prdy_ctrt": "--"}}},
        recent_events=[{"event_type": "exit_signal", "payload": {"symbol": "005930"}}],
        previous_manager_runs=[{"actions": None}],
    )

    assert first == second
    assert first["generated_at"] == ""
    assert first["account_pressure"]["cash"] is None
    assert first["account_pressure"]["deployment_ratio"] is None
    assert first["quote_regime"]["by_symbol"]["005930"]["day_change_pct"] is None
    assert first["recent_rule_events"]["by_symbol"]["005930"]["event_count"] == 1


def test_decision_lifecycle_packet_links_research_to_block_implications() -> None:
    packet = build_decision_lifecycle_packet(
        stage="idea_screen",
        workflow_id="kis_idea_screen",
        artifacts=[
            {
                "artifact_id": "art_1",
                "artifact_type": "idea_screen",
                "symbol": "005930",
                "thesis": {"summary": "메모리 업사이클 기대"},
                "evidence": [{"source_type": "report", "source_id": "r1"}],
                "block_implications": [{"action": "watch_add", "horizon": "mid"}],
                "rejected_actions": [],
            }
        ],
    )

    assert packet["version"] == "decision_lifecycle_v3"
    assert packet["workflow_id"] == "kis_idea_screen"
    assert packet["artifact_count"] == 1
    assert packet["symbols"] == ["005930"]
    assert packet["block_implications"][0]["action"] == "watch_add"
    assert packet["block_implications"][0]["symbol"] == "005930"
    assert packet["block_implications"][0]["artifact_id"] == "art_1"


def test_decision_lifecycle_packet_rejects_vague_artifacts() -> None:
    packet = build_decision_lifecycle_packet(
        stage="idea_screen",
        workflow_id="kis_idea_screen",
        artifacts=[{"artifact_id": "bad", "symbol": "005930", "thesis": {}}],
    )

    assert packet["artifact_count"] == 0
    assert packet["rejected_artifacts"][0]["reason"] == "missing_evidence"
