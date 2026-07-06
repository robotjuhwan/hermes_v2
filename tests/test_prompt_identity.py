from __future__ import annotations

from pathlib import Path

from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)


MEMORY_ROOT = Path(".runtime/investment_memory")
DECISION_SKILL_FILES = {
    "block_manager.md": "블록",
    "market_judge.md": "장중",
    "risk_manager.md": "리스크",
    "reflection.md": "반성",
}


def _ensure_default_investment_memory_files() -> None:
    InvestmentMemoryService(
        config=InvestmentMemoryConfig(root_path=str(MEMORY_ROOT)),
    ).initialize()


def _identity_scan_paths() -> list[Path]:
    _ensure_default_investment_memory_files()

    paths = [
        Path("src/tradecraft/services/investment_memory.py"),
        MEMORY_ROOT / "persona.md",
        MEMORY_ROOT / "policies" / "trading.md",
        *[
            MEMORY_ROOT / "skills" / filename
            for filename in DECISION_SKILL_FILES
        ],
    ]
    return list(dict.fromkeys(paths))


def test_new_prompts_and_static_ui_keep_live_block_trading_identity() -> None:
    roots = [Path("src/tradecraft")]
    forbidden = [
        "정보 제공용",
        "매매 추천 아님",
        "financial advice",
        "not recommendations",
        "not recommendation",
        "Do not give direct buy",
        "매수 지시가 아니라",
        "사용자 책임",
        "투자 책임",
        "본인 책임",
    ]
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".css", ".md"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in forbidden:
                if term in text:
                    hits.append(f"{path}:{term}")
    for path in _identity_scan_paths():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in forbidden:
            if term in text:
                hits.append(f"{path}:{term}")

    assert hits == []


def test_jue_decision_skill_files_keep_live_trading_identity() -> None:
    _ensure_default_investment_memory_files()

    root = MEMORY_ROOT / "skills"
    for filename, keyword in DECISION_SKILL_FILES.items():
        text = (root / filename).read_text(encoding="utf-8")
        assert "쥬" in text
        assert keyword in text
        assert "skill_id: jue." in text
