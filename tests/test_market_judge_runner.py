from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.runtime.market_judge_runner import _should_use_llm, run_market_judge_loop


def test_market_judge_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "market_judge.json"

    class _Settings:
        market_judge_state_path = str(state_path)
        market_quote_interval_sec = 15
        market_judge_interval_sec = 60
        market_judge_once = True

    class _Engine:
        def clock(self) -> dict:
            return {"session": "regular"}

        async def run_once(self, *, use_llm: bool = True) -> dict:
            return {
                "status": "ok",
                "llm_used": use_llm,
                "judgments": [{"symbol": "005930"}],
            }

    asyncio.run(
        run_market_judge_loop(
            settings=_Settings(),  # type: ignore[arg-type]
            engine=_Engine(),  # type: ignore[arg-type]
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["service"] == "tradecraft-market-judge"
    assert payload["status"] == "ok"
    assert payload["result"]["llm_used"] is True


def test_market_judge_runner_passes_split_retention_settings(tmp_path: Path) -> None:
    state_path = tmp_path / "market_judge.json"

    class _Settings:
        market_judge_state_path = str(state_path)
        market_quote_interval_sec = 15
        market_judge_interval_sec = 60
        market_judge_once = True
        market_judge_quote_retention_days = 3
        market_judge_quote_archive_retention_days = 7
        market_judge_account_retention_days = 11
        market_judge_judgment_retention_days = 31
        market_judge_judgment_archive_retention_days = 13
        market_judge_compact_recent_run_count = 17
        market_judge_compact_min_chars = 12_345
        market_judge_compact_symbol_min_chars = 678

    class _Engine:
        prune_kwargs: dict | None = None

        def clock(self) -> dict:
            return {"session": "regular"}

        async def run_once(self, *, use_llm: bool = True) -> dict:
            return {
                "status": "ok",
                "llm_used": use_llm,
                "judgments": [{"symbol": "005930"}],
            }

        def prune_history(self, **kwargs) -> dict:
            self.prune_kwargs = kwargs
            return {"status": "ok"}

    engine = _Engine()

    asyncio.run(
        run_market_judge_loop(
            settings=_Settings(),  # type: ignore[arg-type]
            engine=engine,  # type: ignore[arg-type]
        )
    )

    assert engine.prune_kwargs == {
        "retention_days": 3,
        "quote_archive_retention_days": 7,
        "account_retention_days": 11,
        "judgment_retention_days": 31,
        "judgment_archive_retention_days": 13,
        "compact_recent_run_count": 17,
        "compact_min_chars": 12_345,
        "compact_symbol_min_chars": 678,
    }


def test_market_judge_runner_skips_heavy_run_when_market_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "market_judge.json"

    class _Settings:
        market_judge_state_path = str(state_path)
        market_quote_interval_sec = 15
        market_judge_interval_sec = 60
        market_judge_once = True

    class _Engine:
        run_calls = 0

        def clock(self) -> dict:
            return {"session": "closed", "date": "2026-06-15"}

        def has_llm_run_for_session_date(self, *, session: str, trading_day: str) -> bool:
            _ = (session, trading_day)
            return False

        def schedule(self, *, last_judged_at=None) -> dict:
            _ = last_judged_at
            return {"clock": self.clock()}

        async def run_once(self, *, use_llm: bool = True) -> dict:
            _ = use_llm
            self.run_calls += 1
            raise AssertionError("closed market must not collect account/quotes/strategy")

    engine = _Engine()

    asyncio.run(
        run_market_judge_loop(
            settings=_Settings(),  # type: ignore[arg-type]
            engine=engine,  # type: ignore[arg-type]
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert engine.run_calls == 0
    assert payload["status"] == "closed"
    assert payload["result"]["status"] == "closed"
    assert payload["result"]["reason"] == "market_closed"


def test_market_judge_opening_quote_only_and_slot_idempotency() -> None:
    assert (
        _should_use_llm(
            last_judged_at=None,
            interval_sec=1800,
            clock={"session": "regular", "now": "2026-05-11T09:03:00+09:00"},
        )
        is False
    )
    assert (
        _should_use_llm(
            last_judged_at=None,
            interval_sec=1800,
            clock={"session": "pre_open", "date": "2026-05-11"},
            has_session_run_today=True,
        )
        is False
    )
    assert (
        _should_use_llm(
            last_judged_at=None,
            interval_sec=1800,
            clock={"session": "pre_open", "date": "2026-05-11"},
            has_session_run_today=False,
        )
        is True
    )


def test_market_judge_runner_writes_llm_in_progress_heartbeat(tmp_path: Path) -> None:
    state_path = tmp_path / "market_judge.json"

    class _Settings:
        market_judge_state_path = str(state_path)
        market_quote_interval_sec = 15
        market_judge_interval_sec = 60
        market_judge_once = True

    class _Engine:
        started: asyncio.Event
        release: asyncio.Event

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        def latest_llm_run_at(self) -> datetime:
            return datetime.now(timezone.utc) - timedelta(seconds=120)

        def clock(self) -> dict:
            return {"session": "regular", "now": "2026-05-11T10:00:00+09:00"}

        def schedule(self, *, last_judged_at=None) -> dict:
            return {
                "status": "ok",
                "latest_llm_run_at": last_judged_at.isoformat() if last_judged_at else "",
            }

        async def run_once(self, *, use_llm: bool = True) -> dict:
            assert use_llm is True
            self.started.set()
            await self.release.wait()
            return {
                "status": "ok",
                "llm_used": True,
                "judgments": [{"symbol": "005930"}],
            }

    async def scenario() -> dict:
        engine = _Engine()
        task = asyncio.create_task(
            run_market_judge_loop(
                settings=_Settings(),  # type: ignore[arg-type]
                engine=engine,  # type: ignore[arg-type]
                sleep=lambda _: asyncio.sleep(0),
            )
        )
        await asyncio.wait_for(engine.started.wait(), timeout=1)
        for _ in range(20):
            if state_path.exists():
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                if payload.get("status") == "llm_in_progress":
                    engine.release.set()
                    await asyncio.wait_for(task, timeout=1)
                    return payload
            await asyncio.sleep(0.01)
        engine.release.set()
        await asyncio.wait_for(task, timeout=1)
        return json.loads(state_path.read_text(encoding="utf-8"))

    payload = asyncio.run(scenario())

    assert payload["status"] == "llm_in_progress"
    assert payload["llm_used"] is True
    assert payload["result"]["status"] == "llm_in_progress"
    assert payload["result"]["mode"] == "llm"


def test_market_judge_jue_wiki_provider_preserves_selected_page_quality_metadata(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.market_judge_runner import _selector_context_provider
    from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService

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
        title="삼성전자 market quality",
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


def test_market_judge_jue_wiki_provider_defaults_to_effectiveness_learning(
    tmp_path: Path,
) -> None:
    from tradecraft.runtime.market_judge_runner import _selector_context_provider
    from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="market",
        key="regime",
        title="KIS regime memory",
        symbols=[],
        content_sections={
            "Current Stance": "장중 판단에서 검증된 시장 국면 기억",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "market_judgment", "source_id": "judge-1"}],
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
    )(target_scope="kis")

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == page_id
    assert "effectiveness_adjustment:3.0000" in payload["pages"][0][
        "selection_reasons"
    ]
