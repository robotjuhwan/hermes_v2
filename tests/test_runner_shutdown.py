from __future__ import annotations

from types import SimpleNamespace


def test_async_runners_handle_keyboard_interrupt_without_traceback(monkeypatch) -> None:
    from tradecraft.runtime import (
        binance_block_trader_runner,
        crypto_alpha_runner,
        crypto_market_research_runner,
        crypto_pattern_lab_runner,
        investment_memory_runner,
        kis_block_trader_runner,
        live_evaluator_runner,
        market_judge_runner,
        market_pulse_runner,
        strategy_insights_runner,
    )

    def raise_keyboard_interrupt(_payload) -> None:
        raise KeyboardInterrupt

    for module, loop_name, settings in [
        (
            binance_block_trader_runner,
            "run_binance_block_trader_loop",
            SimpleNamespace(binance_block_trader_enabled=True),
        ),
        (
            crypto_alpha_runner,
            "run_crypto_alpha_loop",
            SimpleNamespace(crypto_alpha_enabled=True),
        ),
        (
            crypto_market_research_runner,
            "run_crypto_market_research_loop",
            SimpleNamespace(crypto_market_research_enabled=True),
        ),
        (
            crypto_pattern_lab_runner,
            "run_crypto_pattern_lab_loop",
            None,
        ),
        (
            investment_memory_runner,
            "run_investment_memory_loop",
            SimpleNamespace(investment_memory_enabled=True),
        ),
        (
            kis_block_trader_runner,
            "run_kis_block_trader_loop",
            SimpleNamespace(kis_block_trader_enabled=True, kis_primary_ready=True),
        ),
        (
            live_evaluator_runner,
            "run_live_evaluator_loop",
            None,
        ),
        (
            market_judge_runner,
            "run_market_judge_loop",
            SimpleNamespace(market_judge_enabled=True),
        ),
        (
            market_pulse_runner,
            "run_market_pulse_loop",
            SimpleNamespace(market_pulse_enabled=True),
        ),
        (
            strategy_insights_runner,
            "run_strategy_insight_loop",
            None,
        ),
    ]:
        monkeypatch.setattr(module, "write_current_runner_pid", lambda _key: None)
        if hasattr(module, "clear_current_runner_pid"):
            monkeypatch.setattr(module, "clear_current_runner_pid", lambda _key: None)
        if settings is not None:
            monkeypatch.setattr(module, "AppSettings", lambda: settings)
        monkeypatch.setattr(module, loop_name, lambda **_kwargs: object())
        monkeypatch.setattr(module.asyncio, "run", raise_keyboard_interrupt)

        module.run()


def test_live_evaluator_run_configures_runner_logging(monkeypatch) -> None:
    from tradecraft.runtime import live_evaluator_runner

    calls: list[dict] = []

    def capture_basic_config(**kwargs) -> None:
        calls.append(kwargs)

    def raise_keyboard_interrupt(_payload) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(live_evaluator_runner, "write_current_runner_pid", lambda _key: None)
    monkeypatch.setattr(live_evaluator_runner.logging, "basicConfig", capture_basic_config)
    monkeypatch.setattr(
        live_evaluator_runner,
        "run_live_evaluator_loop",
        lambda: object(),
    )
    monkeypatch.setattr(live_evaluator_runner.asyncio, "run", raise_keyboard_interrupt)

    live_evaluator_runner.run()

    assert calls
    assert calls[0]["level"] == live_evaluator_runner.logging.INFO
    assert "%(asctime)s %(levelname)s %(name)s - %(message)s" == calls[0]["format"]


def test_sleeping_runners_handle_keyboard_interrupt_without_traceback(monkeypatch) -> None:
    from tradecraft.runtime import jue_wiki_runner, watchdog_runner

    monkeypatch.setattr(jue_wiki_runner, "write_current_runner_pid", lambda _key: None)
    monkeypatch.setattr(
        jue_wiki_runner,
        "AppSettings",
        lambda: SimpleNamespace(
            jue_wiki_runner_interval_sec=60,
            jue_wiki_repair_enabled=False,
            jue_wiki_application_enabled=False,
            jue_wiki_effectiveness_min_samples=3,
            jue_wiki_mode_recommendation_min_samples=3,
        ),
    )
    monkeypatch.setattr(jue_wiki_runner, "build_service", lambda _settings: object())
    monkeypatch.setattr(
        jue_wiki_runner,
        "run_once",
        lambda **_kwargs: {
            "status": "ok",
            "rebuild": {"updated_count": 0},
            "lint": {"status": "ok"},
        },
    )
    monkeypatch.setattr(
        jue_wiki_runner.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    jue_wiki_runner.run()

    monkeypatch.setattr(watchdog_runner, "write_current_runner_pid", lambda _key: None)
    monkeypatch.setattr(
        watchdog_runner,
        "AppSettings",
        lambda: SimpleNamespace(watchdog_interval_sec=60, watchdog_enabled=True),
    )
    monkeypatch.setattr(
        watchdog_runner,
        "run_watchdog_once",
        lambda _settings: {"status": "ok", "restart_keys": []},
    )
    monkeypatch.setattr(
        watchdog_runner.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    watchdog_runner.run()
