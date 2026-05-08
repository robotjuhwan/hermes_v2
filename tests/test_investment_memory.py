from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}
        self.calls: list[dict] = []

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.calls.append(payload)
        return {"ok": True, "content": json.dumps(self.payload, ensure_ascii=False)}


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, parse_mode=None, chat_id=None) -> dict:
        _ = (parse_mode, chat_id)
        self.messages.append(text)
        return {"ok": True, "message_id": len(self.messages)}


def _service(
    tmp_path: Path,
    *,
    llm_payload: dict | None = None,
    telegram: _FakeTelegram | None = None,
) -> InvestmentMemoryService:
    strategy = tmp_path / "strategy_krx.md"
    strategy.write_text(
        "# 전략 노하우\n\n- 추격 매수보다 블록 손절 약속을 우선한다.\n\n본문 노이즈" * 20,
        encoding="utf-8",
    )
    return InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy),
        ),
        llm_bridge=_FakeLLM(llm_payload) if llm_payload is not None else None,  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
    )


def test_initialize_creates_structured_markdown_without_raw_legacy_copy(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.initialize()
    root = tmp_path / "memory"

    assert result["status"] == "ok"
    assert (root / "persona.md").exists()
    assert "쥬" in (root / "persona.md").read_text(encoding="utf-8")
    assert (root / "policies" / "trading.md").exists()
    legacy = (root / "policies" / "legacy_strategy_extract.md").read_text(
        encoding="utf-8"
    )
    assert "Legacy Strategy Extract" in legacy
    assert "본문 노이즈본문 노이즈" not in legacy


def test_ritual_is_idempotent_and_sends_telegram_once(tmp_path: Path) -> None:
    telegram = _FakeTelegram()
    service = _service(tmp_path, telegram=telegram)
    context = {
        "account": {"cash_krw": 1_000_000, "position_count": 2},
        "blocks": {"blocks": [{"status": "open", "block_id": "blk_1"}]},
    }

    first = asyncio.run(
        service.run_ritual(slot="pre_open", context=context, send_telegram=True)
    )
    second = asyncio.run(
        service.run_ritual(slot="pre_open", context=context, send_telegram=True)
    )

    assert first["status"] == "llm_unavailable"
    assert second["status"] == "skipped"
    assert len(telegram.messages) == 1
    assert "장전 마음가짐" in telegram.messages[0]


def test_due_slots_do_not_backfill_missed_trading_day_windows(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kst = ZoneInfo("Asia/Seoul")

    late_friday = datetime(2026, 5, 8, 22, 30, tzinfo=kst)
    monday_pre_open = datetime(2026, 5, 11, 8, 35, tzinfo=kst)
    monday_between = datetime(2026, 5, 11, 10, 30, tzinfo=kst)

    assert service.due_slots(now=late_friday) == []
    assert service.due_slots(now=monday_pre_open) == ["pre_open"]
    assert service.due_slots(now=monday_between) == []


def test_llm_updates_symbol_block_and_soft_policy_memory(tmp_path: Path) -> None:
    payload = {
        "title": "마감 리뷰",
        "message_md": "오늘은 블록 약속을 잘 지켰다.",
        "memory_updates": {
            "symbols": [
                {
                    "symbol": "005930",
                    "summary_md": "삼성전자는 단기 수급보다 중기 밸류 확인이 중요했다.",
                    "confidence": 0.7,
                }
            ],
            "blocks": [
                {
                    "block_id": "blk_005930_1",
                    "summary_md": "목표가 도달 전 추격 조정을 피한 점이 좋았다.",
                    "confidence": 0.8,
                }
            ],
        },
        "policy_changes": [
            {
                "policy_id": "avoid_chasing_after_gap",
                "action": "caution",
                "strength": "soft",
                "reason": "갭상승 직후 블록 진입은 확인봉을 기다린다.",
                "confidence": 0.65,
            },
            {
                "policy_id": "ban_low_liquidity",
                "action": "ban",
                "strength": "hard",
                "reason": "유동성 부족 종목 금지 후보",
                "confidence": 0.6,
            },
        ],
    }
    service = _service(tmp_path, llm_payload=payload)

    result = asyncio.run(
        service.run_ritual(
            slot="post_close",
            context={"account": {}, "blocks": {"blocks": []}},
            force=True,
        )
    )
    context_pack = service.context_pack(symbols=["005930"], block_ids=["blk_005930_1"])

    assert result["status"] == "ok"
    assert "삼성전자" in (tmp_path / "memory" / "symbols" / "005930.md").read_text(
        encoding="utf-8"
    )
    assert "목표가" in (
        tmp_path / "memory" / "blocks" / "blk_005930_1.md"
    ).read_text(encoding="utf-8")
    assert [row["policy_id"] for row in service.active_policies()] == [
        "avoid_chasing_after_gap"
    ]
    assert "avoid_chasing_after_gap" in json.dumps(context_pack, ensure_ascii=False)
