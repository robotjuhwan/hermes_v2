from __future__ import annotations

from tradecraft.services.jue_research_spine import (
    build_research_spine,
    select_balanced_research_symbols,
)


def test_research_spine_builds_quality_packets_and_buckets() -> None:
    strategy_payload = {
        "status": "ok",
        "candidates": [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "asset_class": "etf",
                "horizon_bias": "core_etf",
                "score": 92,
                "confidence": 80,
                "sources": ["etf_research"],
                "reasons": ["거래대금과 코어 지수 노출이 충분하다."],
            },
            {
                "symbol": "005930",
                "name": "정보",
                "asset_class": "equity",
                "score": 74,
                "confidence": 63,
                "sources": ["naver_reports"],
                "identity_status": {
                    "status": "warning",
                    "label": "종목명 검증 필요",
                },
                "data_warnings": ["종목명 검증 필요", "밸류 미수집"],
                "valuation": {"status": "missing", "label": "unknown"},
                "suitability": {
                    "balanced": {"score": 74, "grade": "B"},
                    "short_term": {"score": 78, "grade": "B"},
                    "mid_term": {"score": 70, "grade": "B"},
                    "long_term": {"score": 48, "grade": "C"},
                },
                "reasons": ["HBM 리포트 근거"],
                "risks": ["밸류 최신성 부족"],
            },
        ],
    }
    daily_discovery = {
        "status": "ok",
        "items": [
            {
                "symbol": "000660",
                "name": "SK하이닉스",
                "score": 68,
                "analysis": {
                    "stance": "watch",
                    "summary": "메모리 업황 후보",
                    "reasons": ["디스커버리 점검"],
                    "risks": ["추격 부담"],
                },
            }
        ],
    }
    account = {
        "positions": [
            {"symbol": "005930", "name": "삼성전자", "qty": 2},
        ]
    }

    spine = build_research_spine(
        strategy_payload=strategy_payload,
        daily_discovery=daily_discovery,
        market_judgment={"status": "llm_error", "error_message": "quota"},
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account=account,
        blocks=[],
        quotes=[],
        max_packets=8,
    )

    assert spine["status"] == "ok"
    assert spine["version"] == "research_spine_v1"
    assert spine["quality_summary"]["identity_warning_count"] == 1
    assert spine["quality_summary"]["valuation_missing_count"] == 2
    assert spine["quality_summary"]["market_judgment_status"] == "llm_error"
    assert [row["symbol"] for row in spine["buckets"]["owned_symbols"]] == ["005930"]
    assert [row["symbol"] for row in spine["buckets"]["core_etf"]] == ["069500"]
    assert [row["symbol"] for row in spine["buckets"]["daily_discovery"]] == ["000660"]

    samsung = next(row for row in spine["packets"] if row["symbol"] == "005930")
    assert samsung["name"] == "삼성전자"
    assert samsung["quality"]["identity_confidence"] < 0.7
    assert samsung["quality"]["decision_use"] == "caution"
    assert "종목명 검증 필요" in samsung["quality"]["warnings"]


def test_select_balanced_research_symbols_keeps_equities_visible_when_etfs_dominate() -> None:
    strategy_payload = {
        "candidates": [
            {"symbol": "069500", "asset_class": "etf", "score": 100},
            {"symbol": "091160", "asset_class": "etf", "score": 98},
            {"symbol": "102110", "asset_class": "etf", "score": 97},
            {"symbol": "005930", "asset_class": "equity", "score": 72},
            {"symbol": "000660", "asset_class": "equity", "score": 70},
        ]
    }

    selected = select_balanced_research_symbols(
        strategy_payload,
        existing_symbols=[],
        limit=4,
    )

    assert selected[0] == "069500"
    assert "005930" in selected
    assert "000660" in selected
    assert len(selected) == 4


def test_research_spine_packet_selection_keeps_signal_buckets_visible() -> None:
    strategy_payload = {
        "status": "ok",
        "candidates": [
            *[
                {
                    "symbol": f"{69000 + index:06d}",
                    "name": f"ETF {index}",
                    "asset_class": "etf",
                    "horizon_bias": "core_etf",
                    "score": 95 - index,
                    "confidence": 80,
                    "sources": ["etf_research"],
                }
                for index in range(12)
            ],
            *[
                {
                    "symbol": f"{1000 + index:06d}",
                    "name": f"세시반 후보 {index}",
                    "asset_class": "equity",
                    "score": 78 - index,
                    "confidence": 66,
                    "sources": ["after_close_330"],
                    "reasons": ["세시반 독립 수급 후보"],
                }
                for index in range(8)
            ],
            *[
                {
                    "symbol": f"{2000 + index:06d}",
                    "name": f"고래 후보 {index}",
                    "asset_class": "equity",
                    "score": 74 - index,
                    "confidence": 65,
                    "sources": ["whale_insight"],
                    "reasons": ["고래 독립 포지션 후보"],
                }
                for index in range(6)
            ],
        ],
    }

    spine = build_research_spine(
        strategy_payload=strategy_payload,
        daily_discovery=None,
        market_judgment=None,
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=18,
    )

    packets = spine["packets"]
    sources_by_symbol = {
        row["symbol"]: set(row["evidence"]["sources"])
        for row in packets
    }
    assert len(packets) == 18
    assert sum(1 for row in packets if row["asset_class"] == "etf") <= 9
    assert any("after_close_330" in sources for sources in sources_by_symbol.values())
    assert any("whale_insight" in sources for sources in sources_by_symbol.values())


def test_research_spine_ingests_etf_research_items_as_trade_packets() -> None:
    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=None,
        market_judgment=None,
        etf_research={
            "status": "ok",
            "items": [
                {
                    "symbol": "360750",
                    "snapshot": {
                        "symbol": "360750",
                        "name": "TIGER 미국S&P500",
                        "status": "ok",
                        "change_pct": 0.82,
                        "turnover_krw": 12_000_000_000,
                    },
                    "score": {
                        "symbol": "360750",
                        "label": "core_fit",
                        "liquidity_score": 84,
                        "momentum_score": 61,
                        "core_fit_score": 92,
                        "risk_score": 18,
                        "reasons": ["미국 지수 코어 노출과 거래대금이 충분하다."],
                        "risks": ["환율 변동과 해외 지수 갭 리스크"],
                    },
                }
            ],
        },
        investment_memory={"status": "ok"},
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=6,
    )

    assert [row["symbol"] for row in spine["buckets"]["core_etf"]] == ["360750"]
    packet = next(row for row in spine["packets"] if row["symbol"] == "360750")
    assert packet["name"] == "TIGER 미국S&P500"
    assert packet["asset_class"] == "etf"
    assert packet["horizon_bias"] == "core_etf"
    assert packet["score"] >= 80
    assert packet["quality"]["valuation_status"] == "not_provided"
    assert "etf_research" in packet["evidence"]["sources"]
    assert "미국 지수 코어 노출" in packet["evidence"]["reasons"][0]
    assert "환율 변동" in packet["evidence"]["risks"][0]


def test_research_spine_ingests_symbol_analysis_memory_packets() -> None:
    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=None,
        market_judgment=None,
        etf_research={"status": "active", "items": []},
        investment_memory={
            "status": "ok",
            "symbol_analyses": {
                "033790": [
                    {
                        "stance": "mid_watch",
                        "confidence": 0.74,
                        "summary": "피노는 급락 후 수급 회복 시 중기 블록 후보로 재검토한다.",
                        "risks": ["재료 소멸 후 거래대금 감소"],
                        "data_gaps": ["기관 수급 연속성 확인 필요"],
                    }
                ]
            },
        },
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=6,
    )

    assert [row["symbol"] for row in spine["buckets"]["symbol_memory"]] == ["033790"]
    packet = next(row for row in spine["packets"] if row["symbol"] == "033790")
    assert packet["asset_class"] == "equity"
    assert "symbol_memory" in packet["buckets"]
    assert "symbol_analysis_memory" in packet["evidence"]["sources"]
    assert "중기 블록 후보" in packet["evidence"]["reasons"][0]
    assert "거래대금 감소" in packet["evidence"]["risks"][0]
    assert "기관 수급 연속성" in packet["evidence"]["checks"][0]


def test_research_spine_ingests_market_judgment_symbol_packets() -> None:
    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=None,
        market_judgment={
            "status": "ok",
            "judgments": [
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "stance": "watch",
                    "account_action": "new_watch",
                    "horizon": "mid_term",
                    "confidence": 0.71,
                    "reasons": ["장중 수급 회복과 HBM 테마 재점화 가능성"],
                    "risks": ["외국인 매도 전환 시 추격 부담"],
                    "triggers": ["전고점 돌파 후 거래대금 유지"],
                    "data_gaps": ["밸류 최신성 확인"],
                }
            ],
        },
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=6,
    )

    assert [row["symbol"] for row in spine["buckets"]["market_judgment"]] == [
        "000660"
    ]
    packet = next(row for row in spine["packets"] if row["symbol"] == "000660")
    assert packet["name"] == "SK하이닉스"
    assert packet["horizon_bias"] == "mid_term"
    assert "market_judgment" in packet["evidence"]["sources"]
    assert "HBM 테마" in packet["evidence"]["reasons"][0]
    assert "외국인 매도" in packet["evidence"]["risks"][0]
    assert "전고점 돌파" in packet["evidence"]["checks"][0]
    assert "밸류 최신성" in packet["evidence"]["checks"][1]


def test_research_spine_enriches_owned_block_packets_with_live_context() -> None:
    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=None,
        market_judgment=None,
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account={
            "positions": [
                {"symbol": "033790", "name": "피노", "qty": 3},
            ]
        },
        blocks=[
            {
                "block_id": "kis_033790_mid_1",
                "symbol": "033790",
                "name": "피노",
                "status": "open",
                "horizon": "mid",
                "qty_open": 3,
                "entry_price": 13660,
                "target_price": 15500,
                "stop_price": 12800,
                "unrealized_pnl_pct": 4.2,
                "metadata": {"thesis": "중기 회복 블록"},
            }
        ],
        quotes=[
            {
                "symbol": "033790",
                "name": "피노",
                "price": 14240,
                "change_pct": 2.1,
                "volume": 123456,
                "source": "kis",
            }
        ],
        max_packets=6,
    )

    packet = next(row for row in spine["packets"] if row["symbol"] == "033790")
    assert "owned_symbols" in packet["buckets"]
    assert {"account_or_block", "block_state", "quote"} <= set(
        packet["evidence"]["sources"]
    )
    assert packet["live_context"]["quote"]["price"] == 14240
    assert packet["live_context"]["quote"]["change_pct"] == 2.1
    assert packet["live_context"]["blocks"][0]["block_id"] == "kis_033790_mid_1"
    assert packet["live_context"]["blocks"][0]["target_price"] == 15500
    checks = " ".join(packet["evidence"]["checks"])
    assert "목표가=15500" in checks
    assert "현재가=14240" in checks


def test_research_spine_promotes_pre_surge_daily_discovery_lane() -> None:
    daily_discovery = {
        "status": "ok",
        "items": [
            {
                "symbol": "123450",
                "name": "선행후보",
                "score": 84,
                "pre_surge": {
                    "is_candidate": True,
                    "lane": "pre_surge",
                    "score": 88,
                    "entry_bias": "scout_or_waiting_block",
                    "preferred_horizon": "mid",
                    "reasons": ["저평가 눌림 후보", "섹터 순환매 전초"],
                },
                "analysis": {
                    "stance": "watch",
                    "confidence": 0.72,
                    "summary": "급등 전 선행 후보",
                    "reasons": ["저PER", "거래대금 증가 초입"],
                    "risks": ["유동성 확인"],
                },
            }
        ],
        "pre_surge_candidates": [
            {
                "symbol": "123450",
                "name": "선행후보",
                "score": 84,
            }
        ],
    }

    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=daily_discovery,
        market_judgment=None,
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=6,
    )

    assert [row["symbol"] for row in spine["buckets"]["pre_surge"]] == ["123450"]
    packet = next(row for row in spine["packets"] if row["symbol"] == "123450")
    assert "pre_surge" in packet["buckets"]
    assert "pre_surge_discovery" in packet["evidence"]["sources"]
    assert spine["quality_summary"]["pre_surge_count"] == 1


def test_research_spine_reconstructs_pre_surge_from_raw_daily_discovery() -> None:
    daily_discovery = {
        "status": "ok",
        "items": [
            {
                "symbol": "001390",
                "name": "KG케미칼",
                "score": 91.2,
                "analysis": {
                    "stance": "block_candidate",
                    "confidence": 0.82,
                    "summary": "저평가 눌림목에서 거래대금과 순환매 가능성이 붙었다.",
                    "reasons": ["저평가", "눌림목", "거래대금 증가"],
                    "risks": ["추격 매수 주의"],
                },
            }
        ],
        "pre_surge_candidates": [],
    }

    spine = build_research_spine(
        strategy_payload={"status": "ok", "candidates": []},
        daily_discovery=daily_discovery,
        market_judgment=None,
        etf_research={"status": "active", "items": []},
        investment_memory={"status": "ok"},
        account={"positions": []},
        blocks=[],
        quotes=[],
        max_packets=6,
    )

    assert [row["symbol"] for row in spine["buckets"]["pre_surge"]] == ["001390"]
    packet = next(row for row in spine["packets"] if row["symbol"] == "001390")
    assert packet["pre_surge"]["entry_bias"] == "scout_or_waiting_block"
