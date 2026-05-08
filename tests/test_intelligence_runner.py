from __future__ import annotations

from tradecraft.runtime import intelligence_runner


def test_intelligence_runner_delegates_to_research_loop(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_run_research_loop(service_name: str = "") -> None:
        seen["service_name"] = service_name

    monkeypatch.setattr(
        intelligence_runner,
        "run_research_loop",
        fake_run_research_loop,
    )

    intelligence_runner.run()

    assert seen["service_name"] == "tradecraft-intelligence"
