from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tradecraft.runtime.crypto_alpha_runner import run_crypto_alpha_loop


def test_crypto_alpha_runner_writes_state_once(tmp_path: Path) -> None:
    class Settings:
        crypto_alpha_enabled = True
        crypto_alpha_once = True
        crypto_alpha_state_path = str(tmp_path / "crypto_alpha.json")
        crypto_alpha_crawl_interval_sec = 300
        crypto_alpha_outcome_interval_sec = 300

    class Service:
        async def collect_once(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "sources": ["binance_announcements"],
                "created_snapshots": 1,
                "created_events": 1,
                "errors": [],
            }

        async def label_due_outcomes(self) -> dict[str, Any]:
            return {"status": "ok", "labeled": 0, "skipped_recent": 1}

        def status(self) -> dict[str, Any]:
            return {"status": "ok", "events": 1}

    asyncio.run(
        run_crypto_alpha_loop(
            settings=Settings(),  # type: ignore[arg-type]
            service=Service(),  # type: ignore[arg-type]
        )
    )

    assert Path(Settings.crypto_alpha_state_path).exists()
    assert "tradecraft-crypto-alpha" in Path(Settings.crypto_alpha_state_path).read_text()


def test_crypto_alpha_runner_disabled_writes_state(tmp_path: Path) -> None:
    class Settings:
        crypto_alpha_enabled = False
        crypto_alpha_state_path = str(tmp_path / "crypto_alpha.json")

    asyncio.run(run_crypto_alpha_loop(settings=Settings()))  # type: ignore[arg-type]

    payload = Path(Settings.crypto_alpha_state_path).read_text()
    assert '"status": "disabled"' in payload
