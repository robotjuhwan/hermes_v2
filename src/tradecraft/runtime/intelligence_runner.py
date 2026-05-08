from __future__ import annotations

from tradecraft.runtime.research_runner import run as run_research_loop


def run() -> None:
    run_research_loop(service_name="tradecraft-intelligence")


if __name__ == "__main__":
    run()
