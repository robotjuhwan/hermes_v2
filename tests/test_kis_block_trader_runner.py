from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradecraft.runtime.kis_block_trader_runner import (
    _cycle_log_level,
    _latest_manager_run_at,
    run_kis_block_trader_loop,
)
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.kis_block_trader import run_due_manager


class _Settings:
    def __init__(self, state_path: Path) -> None:
        self.kis_block_trader_state_path = str(state_path)
        self.kis_block_trader_rule_interval_sec = 1
        self.kis_block_trader_manager_interval_sec = 60
        self.kis_block_trader_retention_interval_sec = 3600
        self.kis_block_trader_quote_retention_days = 3
        self.kis_block_trader_reconciliation_retention_days = 7
        self.kis_block_trader_manager_run_retention_days = 14
        self.kis_block_trader_archive_retention_days = 7
        self.kis_block_trader_once = True
        self.daily_discovery_enabled = True
        self.valuation_auto_collect = True
        self.valuation_auto_min_interval_sec = 1800
        self.valuation_auto_max_symbols = 8
        self.valuation_watchlist = "005930,000660"
        self.valuation_db_path = str(state_path.with_name("valuation.db"))
        self.valuation_timeout_sec = 0.1
        self.valuation_min_refresh_hours = 12
        self.valuation_max_symbols_per_collect = 8


class _Trader:
    def __init__(self) -> None:
        self.executor_ticks = 0
        self.retention_runs = 0
        self.repository = None
        self.config = SimpleNamespace(manager_interval_sec=60)

    def clock(self) -> dict:
        return {"session": "closed"}

    async def executor_tick(self) -> dict:
        self.executor_ticks += 1
        return {"status": "skipped", "actions": []}

    def prune_operational_history(self, **kwargs) -> dict:
        self.retention_runs += 1
        return {"status": "ok", "kwargs": kwargs}


class _FundamentalsCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    async def collect_symbols(self, symbols: list[str], *, force: bool = False) -> dict:
        self.calls.append((list(symbols), force))
        return {"status": "ok", "target_count": len(symbols), "collected": len(symbols)}


class _BlockRepository:
    def __init__(self, account: dict | None = None) -> None:
        self.account = account

    def list_blocks(self, *, include_closed: bool = True) -> list[dict]:
        assert include_closed is False
        return [
            {"block_id": "open-1", "symbol": "005930", "status": "open"},
            {"block_id": "entry-1", "symbol": "035420", "status": "entry_waiting"},
            {"block_id": "bad", "symbol": "ABC", "status": "open"},
        ]

    def latest_reconciliation_account(self) -> dict | None:
        return self.account


class _ManagerRunRepository:
    def __init__(self, run_at: str) -> None:
        self.run_at = run_at

    def latest_manager_run(self, *, public: bool = True) -> dict:
        assert public is False
        return {"run_at": self.run_at}


class _LatestManagerRepository:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def latest_manager_run(
        self,
        *,
        public: bool = True,
        include_payload: bool = True,
    ) -> dict:
        assert public is False
        return dict(self.payload)


class _ManagerRunTrader(_Trader):
    def __init__(self, run_at: str) -> None:
        self.repository = _ManagerRunRepository(run_at)


def test_latest_manager_run_at_reads_repository_timestamp() -> None:
    parsed = _latest_manager_run_at(
        _ManagerRunTrader("2026-06-04T01:53:54.338552+00:00")  # type: ignore[arg-type]
    )

    assert parsed == datetime(2026, 6, 4, 1, 53, 54, 338552, tzinfo=timezone.utc)


def test_latest_manager_run_at_ignores_invalid_timestamp() -> None:
    parsed = _latest_manager_run_at(_ManagerRunTrader("not-a-time"))  # type: ignore[arg-type]

    assert parsed is None


def test_kis_block_trader_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "kis_block_trader.json"

    async def no_sleep(seconds: float) -> None:
        _ = seconds

    asyncio.run(
        run_kis_block_trader_loop(
            settings=_Settings(state_path),  # type: ignore[arg-type]
            trader=_Trader(),  # type: ignore[arg-type]
            sleep=no_sleep,
        )
    )

    assert state_path.exists()
    assert "tradecraft-kis-block-trader" in state_path.read_text(encoding="utf-8")


def test_kis_block_trader_runner_collects_small_fundamentals_batch_after_tick(
    tmp_path: Path,
) -> None:
    class FundamentalsSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.valuation_auto_max_symbols = 3

    class FundamentalsTrader(_Trader):
        def __init__(self, collector: _FundamentalsCollector) -> None:
            super().__init__()
            self.repository = _BlockRepository()
            self.collector = collector
            self.events: list[str] = []

        async def executor_tick(self) -> dict:
            self.events.append("tick")
            return await super().executor_tick()

    state_path = tmp_path / "kis_block_trader.json"
    collector = _FundamentalsCollector()
    trader = FundamentalsTrader(collector)

    async def no_sleep(seconds: float) -> None:
        _ = seconds

    asyncio.run(
        run_kis_block_trader_loop(
            settings=FundamentalsSettings(state_path),  # type: ignore[arg-type]
            trader=trader,  # type: ignore[arg-type]
            fundamentals_service=collector,
            sleep=no_sleep,
        )
    )

    assert trader.events == ["tick"]
    assert collector.calls == [(["005930", "000660", "035420"], False)]
    state_text = state_path.read_text(encoding="utf-8")
    assert "fundamentals_collect_result" in state_text
    assert "035420" in state_text


def test_kis_block_trader_runner_prioritizes_account_positions_for_fundamentals(
    tmp_path: Path,
) -> None:
    class FundamentalsSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.valuation_auto_max_symbols = 4

    class HoldingsTrader(_Trader):
        def __init__(self, collector: _FundamentalsCollector) -> None:
            super().__init__()
            self.repository = _BlockRepository(
                account={
                    "positions": [
                        {"symbol": "064350", "name": "현대로템", "qty": 1},
                        {"symbol": "005930", "name": "삼성전자", "qty": 2},
                        {"symbol": "BAD", "name": "bad", "qty": 1},
                    ]
                }
            )
            self.collector = collector

    state_path = tmp_path / "kis_block_trader.json"
    collector = _FundamentalsCollector()

    async def no_sleep(seconds: float) -> None:
        _ = seconds

    asyncio.run(
        run_kis_block_trader_loop(
            settings=FundamentalsSettings(state_path),  # type: ignore[arg-type]
            trader=HoldingsTrader(collector),  # type: ignore[arg-type]
            fundamentals_service=collector,
            sleep=no_sleep,
        )
    )

    assert collector.calls == [(["005930", "000660", "064350", "035420"], False)]
    state_text = state_path.read_text(encoding="utf-8")
    assert "064350" in state_text


def test_kis_jue_wiki_provider_applies_effectiveness_weight(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.kis_block_trader_runner import _selector_context_provider

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "효과성이 검증된 삼성전자 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk-1"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "sample_count": 8,
            "helpful_score": 25.0,
            "status": "active",
        }
    )
    degraded_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="000660",
        title="SK하이닉스 degraded",
        symbols=["000660"],
        content_sections={
            "Current Stance": "수리해야 할 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk-loss"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": degraded_id,
            "decision_scope": "kis",
            "sample_count": 7,
            "win_rate": 0.28,
            "expectancy": -0.6,
            "drawdown_pressure": 1.2,
            "helpful_score": -7.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": ["late chase loss"],
        }
    )

    class WikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"
        jue_wiki_effectiveness_weight = 1.0
        jue_wiki_effectiveness_max_adjustment = 8.0

    payload = _selector_context_provider(
        service,
        WikiSettings(),  # type: ignore[arg-type]
    )(target_scope="kis", symbols=["005930"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert "effectiveness:active" in payload["pages"][0]["selection_reasons"]
    assert "effectiveness_adjustment:8.0000" in payload["pages"][0][
        "selection_reasons"
    ]
    assert payload["pages"][0]["effectiveness"]["status"] == "active"
    assert payload["pages"][0]["effectiveness"]["sample_count"] == 8
    assert "degraded pages as repair evidence" in payload["effectiveness_policy"][
        "degraded"
    ]
    assert payload["budget_report"]["effectiveness_status_counts"]["active"] == 1
    assert payload["budget_report"]["repair_priority_count"] == 1
    assert payload["repair_priorities"][0]["page_id"] == degraded_id
    assert payload["repair_priorities"][0]["reasons"] == ["late chase loss"]


def test_kis_jue_wiki_provider_defaults_to_effectiveness_learning(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.kis_block_trader_runner import _selector_context_provider

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "효과성 기본값으로도 선택돼야 하는 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk-default"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "sample_count": 8,
            "helpful_score": 25.0,
            "status": "active",
            "confidence": 1.0,
        }
    )

    class MinimalWikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"

    payload = _selector_context_provider(
        service,
        MinimalWikiSettings(),  # type: ignore[arg-type]
    )(target_scope="kis", symbols=["005930"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert "effectiveness_adjustment:3.0000" in payload["pages"][0][
        "selection_reasons"
    ]


def test_kis_jue_wiki_provider_passes_requested_horizons_to_selector(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.kis_block_trader_runner import _selector_context_provider

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 short horizon memory",
        symbols=["005930"],
        content_sections={
            "Current Stance": "단기 판단에서 검증된 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "kis_blocks", "source_id": "short-win"}],
        confidence=0.5,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "short",
            "sample_count": 8,
            "helpful_score": 8.0,
            "status": "active",
            "confidence": 1.0,
            "reasons": ["short horizon worked"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 8,
            "helpful_score": -8.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": ["mid horizon failed"],
        }
    )

    class MinimalWikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"

    payload = _selector_context_provider(
        service,
        MinimalWikiSettings(),  # type: ignore[arg-type]
    )(target_scope="kis", symbols=["005930"], horizons=["short"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert payload["pages"][0]["effectiveness"]["horizon"] == "short"
    assert payload["pages"][0]["effectiveness"]["status"] == "active"
    assert payload["pages"][0]["effectiveness"]["reasons"] == [
        "short horizon worked"
    ]


def test_kis_jue_wiki_provider_preserves_selected_page_quality_metadata(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.kis_block_trader_runner import _selector_context_provider

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "품질 메타 전달 테스트",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d", "price_missing"],
            }
        ],
        confidence=0.8,
        freshness="stale",
    )["page_id"]

    class WikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"
        jue_wiki_effectiveness_weight = 0.0
        jue_wiki_effectiveness_max_adjustment = 0.0

    payload = _selector_context_provider(
        service,
        WikiSettings(),  # type: ignore[arg-type]
    )(target_scope="kis", symbols=["005930"])

    page = payload["pages"][0]
    assert page["page_id"] == page_id
    assert page["quality_status"] == "weak"
    assert set(page["quality_warnings"]) == {
        "valuation_stale_gt_30d",
        "price_missing",
    }


def test_kis_jue_wiki_provider_applies_scope_mode_recommendation(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.kis_block_trader_runner import _selector_context_provider

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="playbook",
        key="primary_mode",
        title="KIS primary mode",
        symbols=["005930"],
        content_sections={
            "Current Stance": "KIS scope has enough validated wiki evidence.",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "policy_scorecard", "source_id": "kis"}],
        confidence=0.8,
        freshness="fresh",
    )["page_id"]
    service.initialize()
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:kis-primary",
                "kis",
                "primary",
                "assist",
                45,
                0.75,
                json.dumps(["samples:45", "active:4", "avg_helpful:7.2000"]),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    class WikiSettings:
        jue_wiki_full_prompt_max_chars = 20_000
        jue_wiki_context_max_chars = 20_000
        jue_wiki_selector_max_pages = 24
        jue_wiki_selector_min_confidence = 0.15
        jue_wiki_exclude_lint_warnings = False
        jue_wiki_prompt_mode = "assist"
        jue_wiki_effectiveness_weight = 0.0
        jue_wiki_effectiveness_max_adjustment = 0.0

    payload = _selector_context_provider(
        service,
        WikiSettings(),  # type: ignore[arg-type]
    )(target_scope="kis", symbols=["005930"])

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert payload["configured_prompt_mode"] == "assist"
    assert payload["prompt_mode"] == "primary"
    assert payload["mode_recommendation"]["recommendation_id"] == (
        "wiki-mode:kis-primary"
    )
    assert payload["prompt_mode_policy"]["source"] == "mode_recommendation"
    assert "trust_profile_effectiveness" in payload


def test_kis_block_trader_runner_throttles_retention_cleanup_between_ticks(
    tmp_path: Path,
) -> None:
    class ContinuousSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.kis_block_trader_once = False
            self.kis_block_trader_manager_interval_sec = 999
            self.kis_block_trader_retention_interval_sec = 3600

    class StopLoop(Exception):
        pass

    state_path = tmp_path / "kis_block_trader.json"
    trader = _Trader()

    async def stop_after_third_tick(seconds: float) -> None:
        _ = seconds
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_kis_block_trader_loop(
                settings=ContinuousSettings(state_path),  # type: ignore[arg-type]
                trader=trader,  # type: ignore[arg-type]
                sleep=stop_after_third_tick,
            )
        )

    assert trader.executor_ticks >= 3
    assert trader.retention_runs == 1


def test_kis_block_trader_runner_keeps_ticking_while_manager_runs(
    tmp_path: Path,
) -> None:
    class ContinuousSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.kis_block_trader_once = False
            self.kis_block_trader_manager_interval_sec = 60

    class SlowManagerTrader(_Trader):
        def __init__(self) -> None:
            super().__init__()
            self.manager_calls = 0

        def clock(self) -> dict:
            return {"session": "regular", "date": "2026-07-02"}

        async def run_manager_once(self) -> dict:
            self.manager_calls += 1
            await asyncio.sleep(3600)
            return {"status": "ok", "actions": []}

    class StopLoop(Exception):
        pass

    state_path = tmp_path / "kis_block_trader.json"
    trader = SlowManagerTrader()

    async def stop_after_third_tick(seconds: float) -> None:
        _ = seconds
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            asyncio.wait_for(
                run_kis_block_trader_loop(
                    settings=ContinuousSettings(state_path),  # type: ignore[arg-type]
                    trader=trader,  # type: ignore[arg-type]
                    sleep=stop_after_third_tick,
                ),
                timeout=0.2,
            )
        )

    assert trader.manager_calls == 1
    assert trader.executor_ticks >= 3
    state_text = state_path.read_text(encoding="utf-8")
    assert '"manager_result": {"status": "running"' in state_text


def test_kis_block_trader_runner_keeps_ticking_while_discovery_runs(
    tmp_path: Path,
) -> None:
    class ContinuousSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.kis_block_trader_once = False
            self.kis_block_trader_manager_interval_sec = 999

    class SlowDiscoveryTrader(_Trader):
        def __init__(self) -> None:
            super().__init__()
            self.discovery_calls = 0

        def clock(self) -> dict:
            return {"session": "regular", "date": "2026-07-02"}

        def daily_discovery_should_run(self, trading_day) -> bool:
            assert str(trading_day) == "2026-07-02"
            return True

        async def daily_discovery_run_once(self, *, trading_day, force=False) -> dict:
            _ = trading_day, force
            self.discovery_calls += 1
            await asyncio.sleep(3600)
            return {"status": "ok"}

    class StopLoop(Exception):
        pass

    state_path = tmp_path / "kis_block_trader.json"
    trader = SlowDiscoveryTrader()

    async def stop_after_third_tick(seconds: float) -> None:
        _ = seconds
        if trader.executor_ticks >= 3:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            asyncio.wait_for(
                run_kis_block_trader_loop(
                    settings=ContinuousSettings(state_path),  # type: ignore[arg-type]
                    trader=trader,  # type: ignore[arg-type]
                    sleep=stop_after_third_tick,
                ),
                timeout=0.2,
            )
        )

    assert trader.discovery_calls == 1
    assert trader.executor_ticks >= 3
    state_text = state_path.read_text(encoding="utf-8")
    assert '"daily_discovery_result": {"status": "running"' in state_text


def test_kis_block_trader_runner_recovers_last_manager_result_from_repository(
    tmp_path: Path,
) -> None:
    class ContinuousSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.kis_block_trader_once = False
            self.kis_block_trader_manager_interval_sec = 999

    class TraderWithLatestRun(_Trader):
        def __init__(self) -> None:
            super().__init__()
            self.repository = _LatestManagerRepository(
                {
                    "id": 77,
                    "run_at": "2026-07-02T00:00:00+00:00",
                    "status": "error",
                    "mode": "llm",
                    "model": "gpt-5.5",
                    "error_message": "prompt_budget_exceeded",
                }
            )

        def clock(self) -> dict:
            return {"session": "regular", "date": "2026-07-02"}

    class StopLoop(Exception):
        pass

    state_path = tmp_path / "kis_block_trader.json"
    trader = TraderWithLatestRun()

    async def stop_after_first_tick(seconds: float) -> None:
        _ = seconds
        raise StopLoop()

    with pytest.raises(StopLoop):
        asyncio.run(
            run_kis_block_trader_loop(
                settings=ContinuousSettings(state_path),  # type: ignore[arg-type]
                trader=trader,  # type: ignore[arg-type]
                sleep=stop_after_first_tick,
                now_provider=lambda: datetime(
                    2026, 7, 2, 0, 0, 1, tzinfo=timezone.utc
                ),
            )
        )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["last_manager_result"] == {
        "status": "error",
        "run_id": 77,
        "run_at": "2026-07-02T00:00:00+00:00",
        "mode": "llm",
        "model": "gpt-5.5",
        "error_message": "prompt_budget_exceeded",
    }


def test_kis_block_trader_runner_retries_recent_manager_error_after_cooldown(
    tmp_path: Path,
) -> None:
    class ContinuousSettings(_Settings):
        def __init__(self, state_path: Path) -> None:
            super().__init__(state_path)
            self.kis_block_trader_once = False
            self.kis_block_trader_manager_interval_sec = 9_999
            self.kis_block_trader_manager_error_retry_sec = 300

    class TraderWithRecentError(_Trader):
        def __init__(self) -> None:
            super().__init__()
            self.manager_calls = 0
            self.config = SimpleNamespace(manager_interval_sec=9_999)
            self.repository = _LatestManagerRepository(
                {
                    "id": 78,
                    "run_at": "2026-07-02T00:00:00+00:00",
                    "status": "error",
                    "mode": "llm",
                    "model": "gpt-5.5",
                    "error_message": "prompt_budget_exceeded",
                }
            )

        def clock(self) -> dict:
            return {"session": "regular", "date": "2026-07-02"}

        async def run_manager_once(self) -> dict:
            self.manager_calls += 1
            await asyncio.sleep(3600)
            return {"status": "ok", "actions": []}

    class StopLoop(Exception):
        pass

    state_path = tmp_path / "kis_block_trader.json"
    trader = TraderWithRecentError()

    async def stop_after_second_tick(seconds: float) -> None:
        _ = seconds
        if trader.executor_ticks >= 2:
            raise StopLoop()
        await asyncio.sleep(0)

    with pytest.raises(StopLoop):
        asyncio.run(
            run_kis_block_trader_loop(
                settings=ContinuousSettings(state_path),  # type: ignore[arg-type]
                trader=trader,  # type: ignore[arg-type]
                sleep=stop_after_second_tick,
                now_provider=lambda: datetime(2026, 7, 2, 0, 5, 1, tzinfo=timezone.utc),
            )
        )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert trader.manager_calls == 1
    assert payload["manager_result"]["status"] == "running"
    assert payload["manager_result"]["elapsed_sec"] == 0.0
    assert payload["manager_result"]["timeout_sec"] >= 60.0
    assert payload["manager_due_reason"] == "retry_after_manager_error"
    assert payload["manager_error_retry_sec"] == 300


def test_kis_block_trader_runner_logs_noop_ticks_at_debug_level() -> None:
    assert _cycle_log_level(status="ok", manager_used=False, action_count=0) == 10
    assert _cycle_log_level(status="skipped", manager_used=False, action_count=0) == 10
    assert _cycle_log_level(status="ok", manager_used=True, action_count=0) == 20
    assert _cycle_log_level(status="ok", manager_used=False, action_count=1) == 20
    assert _cycle_log_level(status="error", manager_used=False, action_count=0) == 20


class _DiscoveryTrader(_Trader):
    def __init__(self) -> None:
        super().__init__()
        self.discovery_calls = 0

    def clock(self) -> dict:
        return {"session": "pre_open", "date": "2026-05-20"}

    def daily_discovery_should_run(self, trading_day) -> bool:
        assert str(trading_day) == "2026-05-20"
        return True

    async def daily_discovery_run_once(self, *, trading_day, force=False) -> dict:
        assert str(trading_day) == "2026-05-20"
        assert force is False
        self.discovery_calls += 1
        return {"status": "ok", "trading_day": str(trading_day)}

    async def run_manager_once(self) -> dict:
        return {"status": "ok", "actions": []}


def test_kis_block_trader_runner_runs_daily_discovery_when_due(tmp_path: Path) -> None:
    state_path = tmp_path / "kis_block_trader.json"
    trader = _DiscoveryTrader()

    async def no_sleep(seconds: float) -> None:
        _ = seconds

    asyncio.run(
        run_kis_block_trader_loop(
            settings=_Settings(state_path),  # type: ignore[arg-type]
            trader=trader,  # type: ignore[arg-type]
            sleep=no_sleep,
        )
    )

    assert trader.discovery_calls == 1
    state_text = state_path.read_text(encoding="utf-8")
    assert "daily_discovery_result" in state_text
    assert "2026-05-20" in state_text


class _PreOpenManagerTrader(_Trader):
    def __init__(self, trading_day: str) -> None:
        self.trading_day = trading_day
        self.manager_calls = 0

    def clock(self) -> dict:
        return {"session": "pre_open", "date": self.trading_day}

    async def run_manager_once(self) -> dict:
        self.manager_calls += 1
        return {"status": "ok", "actions": []}


def test_run_due_manager_uses_kst_trading_day_for_pre_open() -> None:
    trader = _PreOpenManagerTrader("2026-06-05")
    previous_regular_run = datetime(2026, 6, 4, 6, 0, tzinfo=timezone.utc)

    used, result = asyncio.run(
        run_due_manager(
            trader,  # type: ignore[arg-type]
            last_manager_at=previous_regular_run,
        )
    )

    assert used is True
    assert result == {"status": "ok", "actions": []}
    assert trader.manager_calls == 1


def test_run_due_manager_skips_duplicate_pre_open_same_kst_day() -> None:
    trader = _PreOpenManagerTrader("2026-06-05")
    previous_pre_open_run = datetime(2026, 6, 4, 23, 40, tzinfo=timezone.utc)

    used, result = asyncio.run(
        run_due_manager(
            trader,  # type: ignore[arg-type]
            last_manager_at=previous_pre_open_run,
        )
    )

    assert used is False
    assert result is None
    assert trader.manager_calls == 0
