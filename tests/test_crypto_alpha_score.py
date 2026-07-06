from __future__ import annotations

from tradecraft.services.crypto_alpha_score import score_crypto_candidate


def test_alpha_score_rewards_volume_oi_squeeze_and_penalizes_bad_book() -> None:
    score = score_crypto_candidate(
        {
            "change_pct_24h": 14.0,
            "volume_expansion_ratio": 3.0,
            "spread_bps": 18.0,
            "orderbook_depth_usdt": 120_000.0,
            "wick_risk_score": 35.0,
            "funding_rate": -0.0002,
            "open_interest": 50_000_000,
            "squeeze_risk_score": 72.0,
            "alpha_event_score": 70.0,
        }
    )

    assert score["total_score"] >= 75
    assert "volume_expansion" in score["drivers"]
    assert "squeeze_setup" in score["drivers"]
    assert not score["reject"]


def test_alpha_score_rejects_thin_wide_books() -> None:
    score = score_crypto_candidate(
        {
            "change_pct_24h": 25.0,
            "volume_expansion_ratio": 4.0,
            "spread_bps": 95.0,
            "orderbook_depth_usdt": 4_000.0,
            "wick_risk_score": 80.0,
        }
    )

    assert score["reject"] is True
    assert "spread_too_wide" in score["risks"]
    assert "depth_too_thin" in score["risks"]
