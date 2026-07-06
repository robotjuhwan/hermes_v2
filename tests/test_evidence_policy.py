from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradecraft.services.evidence_policy import (
    EvidenceItem,
    build_decision_packet,
    evidence_from_signal,
    normalize_scope,
    normalize_symbol,
    scorecard_from_evidence,
    utc_now_iso,
)


def test_utc_now_iso_returns_timezone_aware_datetime() -> None:
    parsed = datetime.fromisoformat(utc_now_iso())

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def test_evidence_from_signal_has_stable_identity_and_ttl() -> None:
    captured_at = datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc).isoformat()
    item = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="btcusdt",
        scope="binance",
        confidence=0.72,
        ttl_sec=900,
        captured_at=captured_at,
        payload={"bias": "long", "long_score": 82},
    )
    same_item = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="btcusdt",
        scope="binance",
        confidence=0.72,
        ttl_sec=900,
        captured_at=captured_at,
        payload={"bias": "long", "long_score": 82},
    )
    assert item.evidence_id.startswith("ev_")
    assert item.evidence_id == same_item.evidence_id
    assert item.symbol == "BTCUSDT"
    assert item.scope == "binance"
    assert item.signal_type == "directional_quant"
    assert item.expires_at > captured_at
    assert item.payload["bias"] == "long"


def test_evidence_item_marks_expired() -> None:
    item = EvidenceItem(
        evidence_id="ev_old",
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="ETHUSDT",
        scope="binance",
        confidence=0.5,
        captured_at="2026-05-25T00:00:00+00:00",
        expires_at="2026-05-25T00:10:00+00:00",
        payload={},
    )
    assert item.is_expired("2026-05-25T00:11:00+00:00") is True
    assert item.is_expired("2026-05-25T00:09:00+00:00") is False


def test_evidence_item_to_dict_uses_list_for_default_block_ids() -> None:
    item = EvidenceItem(
        evidence_id="ev_default",
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="ETHUSDT",
        scope="binance",
        confidence=0.5,
        captured_at="2026-05-25T00:00:00+00:00",
        expires_at="2026-05-25T00:10:00+00:00",
        payload={},
    )

    assert item.to_dict()["used_by_block_ids"] == []


def test_evidence_from_signal_clamps_confidence_and_ttl() -> None:
    low = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="BTCUSDT",
        scope="crypto",
        confidence=-0.5,
        ttl_sec=0,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={},
    )
    high = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="BTCUSDT",
        scope="crypto",
        confidence=1.5,
        ttl_sec=-30,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={"side": "long"},
    )

    assert low.confidence == 0.0
    assert low.expires_at == "2026-05-25T01:00:01+00:00"
    assert high.confidence == 1.0
    assert high.expires_at == "2026-05-25T01:00:01+00:00"


def test_scorecard_and_decision_packet_compact_evidence() -> None:
    fresh = evidence_from_signal(
        source="crypto_pattern_lab",
        signal_type="pattern_scorecard",
        symbol="BTCUSDT",
        scope="binance",
        confidence=0.8,
        ttl_sec=3600,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={"pattern_key": "breakout:long", "expectancy_r": 0.6, "sample_count": 8},
    )
    expired = evidence_from_signal(
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="BTCUSDT",
        scope="binance",
        confidence=0.4,
        ttl_sec=60,
        captured_at="2026-05-25T00:00:00+00:00",
        payload={"event_type": "listing"},
    )
    scorecard = scorecard_from_evidence(
        policy_id="binance.breakout.long",
        evidence=[fresh, expired],
        now="2026-05-25T01:01:00+00:00",
    )
    packet = build_decision_packet(
        target_scope="binance",
        symbols=["BTCUSDT"],
        evidence=[fresh, expired],
        scorecards=[scorecard],
        active_policies=[{"policy_id": "binance.breakout.long", "action": "prefer"}],
        max_items=5,
        now="2026-05-25T01:01:00+00:00",
    )
    assert scorecard["fresh_count"] == 1
    assert scorecard["expired_count"] == 1
    assert packet["target_scope"] == "binance"
    assert packet["evidence"][0]["evidence_id"] == fresh.evidence_id
    assert expired.evidence_id not in {item["evidence_id"] for item in packet["evidence"]}
    assert packet["scorecards"][0]["policy_id"] == "binance.breakout.long"
    assert packet["active_policies"][0]["action"] == "prefer"


def test_decision_packet_filters_sorts_caps_and_exposes_advisory_metadata() -> None:
    weaker = evidence_from_signal(
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="btc/usdt",
        scope="global",
        confidence=0.3,
        ttl_sec=3600,
        captured_at="2026-05-25T01:02:00+00:00",
        payload={"event_type": "macro"},
    )
    strongest_old = evidence_from_signal(
        source="crypto_pattern_lab",
        signal_type="pattern_scorecard",
        symbol="btc-usdt",
        scope="crypto",
        confidence=0.9,
        ttl_sec=3600,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={"pattern_key": "breakout:long"},
    )
    strongest_fresh = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="btc_usdt",
        scope="binance",
        confidence=0.9,
        ttl_sec=3600,
        captured_at="2026-05-25T01:03:00+00:00",
        payload={"bias": "long"},
    )
    other_scope = evidence_from_signal(
        source="kis_research",
        signal_type="domestic_signal",
        symbol="BTCUSDT",
        scope="kis",
        confidence=1.0,
        ttl_sec=3600,
        captured_at="2026-05-25T01:04:00+00:00",
        payload={},
    )
    other_symbol = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="ETHUSDT",
        scope="binance",
        confidence=1.0,
        ttl_sec=3600,
        captured_at="2026-05-25T01:05:00+00:00",
        payload={},
    )

    packet = build_decision_packet(
        target_scope="crypto",
        symbols=["btc/usdt"],
        evidence=[weaker, strongest_old, strongest_fresh, other_scope, other_symbol],
        scorecards=[
            {"policy_id": "first"},
            {"policy_id": "second"},
            {"policy_id": "third"},
        ],
        active_policies=[
            {"policy_id": "first", "action": "prefer"},
            {"policy_id": "second", "action": "caution"},
            {"policy_id": "third", "action": "observe"},
        ],
        max_items=3,
        now="2026-05-25T01:06:00+00:00",
    )

    assert packet["hard_filters_enabled"] is False
    assert packet["policy_mode"] == "advisory_preference_caution"
    assert "not trade bans" in packet["policy_note"]
    assert packet["symbols"] == ["BTCUSDT"]
    assert [item["evidence_id"] for item in packet["evidence"]] == [
        strongest_fresh.evidence_id,
        strongest_old.evidence_id,
        weaker.evidence_id,
    ]
    assert weaker.evidence_id in {item["evidence_id"] for item in packet["evidence"]}
    assert other_scope.evidence_id not in {item["evidence_id"] for item in packet["evidence"]}
    assert other_symbol.evidence_id not in {item["evidence_id"] for item in packet["evidence"]}
    assert len(packet["evidence"]) == 3
    assert len(packet["scorecards"]) == 3
    assert len(packet["active_policies"]) == 3


def test_decision_packet_caps_evidence_with_max_items() -> None:
    weaker = evidence_from_signal(
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="BTCUSDT",
        scope="global",
        confidence=0.3,
        ttl_sec=3600,
        captured_at="2026-05-25T01:02:00+00:00",
        payload={"event_type": "macro"},
    )
    strongest_old = evidence_from_signal(
        source="crypto_pattern_lab",
        signal_type="pattern_scorecard",
        symbol="BTCUSDT",
        scope="crypto",
        confidence=0.9,
        ttl_sec=3600,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={"pattern_key": "breakout:long"},
    )
    strongest_fresh = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="BTCUSDT",
        scope="binance",
        confidence=0.9,
        ttl_sec=3600,
        captured_at="2026-05-25T01:03:00+00:00",
        payload={"bias": "long"},
    )

    packet = build_decision_packet(
        target_scope="binance",
        symbols=["BTCUSDT"],
        evidence=[weaker, strongest_old, strongest_fresh],
        scorecards=[{"policy_id": "first"}, {"policy_id": "second"}, {"policy_id": "third"}],
        active_policies=[
            {"policy_id": "first", "action": "prefer"},
            {"policy_id": "second", "action": "caution"},
            {"policy_id": "third", "action": "observe"},
        ],
        max_items=2,
        now="2026-05-25T01:06:00+00:00",
    )

    assert [item["evidence_id"] for item in packet["evidence"]] == [
        strongest_fresh.evidence_id,
        strongest_old.evidence_id,
    ]
    assert weaker.evidence_id not in {item["evidence_id"] for item in packet["evidence"]}
    assert len(packet["evidence"]) == 2
    assert len(packet["scorecards"]) == 2
    assert len(packet["active_policies"]) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BINANCE", "binance"),
        ("crypto", "binance"),
        ("kis", "kis"),
        ("krx", "kis"),
        ("domestic", "kis"),
        ("global", "global"),
        ("anything else", "global"),
    ],
)
def test_normalize_scope(raw: str, expected: str) -> None:
    assert normalize_scope(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" btc/usdt ", "BTCUSDT"),
        ("eth-usdt", "ETHUSDT"),
        ("sol_usdt", "SOLUSDT"),
        (None, ""),
    ],
)
def test_normalize_symbol(raw: object, expected: str) -> None:
    assert normalize_symbol(raw) == expected
