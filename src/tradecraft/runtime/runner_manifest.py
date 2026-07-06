from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerSpec:
    key: str
    pid_file: str
    pattern: str
    label: str
    session_names: tuple[str, ...]
    command: str
    log_path: str

    @property
    def primary_session(self) -> str:
        return self.session_names[0]


RUNNER_SPECS: dict[str, RunnerSpec] = {
    "control": RunnerSpec(
        key="control",
        pid_file="tradecraft-control.pid",
        pattern=(
            r"tradecraft-control|tradecraft\.main:app|"
            r"uvicorn .*tradecraft\.main:app"
        ),
        label="control API",
        session_names=("tradecraft-control", "hermes-control"),
        command=".venv/bin/tradecraft-control --host 127.0.0.1 --port 18080",
        log_path=".runtime/control.log",
    ),
    "runtime": RunnerSpec(
        key="runtime",
        pid_file="tradecraft-runtime.pid",
        pattern=(
            r"tradecraft-runtime|tradecraft\.runtime\.runner|runtime/runner\.py"
        ),
        label="runtime runner",
        session_names=("tradecraft-runtime", "hermes-runtime"),
        command=".venv/bin/tradecraft-runtime",
        log_path=".runtime/logs/runtime.log",
    ),
    "intelligence": RunnerSpec(
        key="intelligence",
        pid_file="tradecraft-intelligence.pid",
        pattern=(
            r"tradecraft-intelligence|tradecraft\.runtime\.intelligence_runner|"
            r"intelligence_runner\.py"
        ),
        label="intelligence runner",
        session_names=("tradecraft-intelligence", "hermes-intelligence"),
        command=".venv/bin/tradecraft-intelligence",
        log_path=".runtime/tradecraft-intelligence.log",
    ),
    "research": RunnerSpec(
        key="research",
        pid_file="tradecraft-research.pid",
        pattern=(
            r"tradecraft-research|tradecraft\.runtime\.research_runner|"
            r"research_runner\.py"
        ),
        label="research runner",
        session_names=("tradecraft-research", "hermes-research"),
        command=".venv/bin/tradecraft-research",
        log_path=".runtime/tradecraft-research.log",
    ),
    "kis_block_trader": RunnerSpec(
        key="kis_block_trader",
        pid_file="tradecraft-kis-block-trader.pid",
        pattern=(
            r"tradecraft-kis-block-trader|"
            r"tradecraft\.runtime\.kis_block_trader_runner|"
            r"kis_block_trader_runner\.py"
        ),
        label="KIS block trader runner",
        session_names=("tradecraft-kis-block-trader", "hermes-kis-block-trader"),
        command=".venv/bin/tradecraft-kis-block-trader",
        log_path=".runtime/kis_block_trader.log",
    ),
    "binance_block_trader": RunnerSpec(
        key="binance_block_trader",
        pid_file="tradecraft-binance-block-trader.pid",
        pattern=(
            r"tradecraft-binance-block-trader|"
            r"tradecraft\.runtime\.binance_block_trader_runner|"
            r"binance_block_trader_runner\.py"
        ),
        label="Binance block trader runner",
        session_names=("tradecraft-binance-block-trader", "hermes-binance-block-trader"),
        command=".venv/bin/tradecraft-binance-block-trader",
        log_path=".runtime/binance_block_trader.log",
    ),
    "crypto_market_research": RunnerSpec(
        key="crypto_market_research",
        pid_file="tradecraft-crypto-market-research.pid",
        pattern=(
            r"tradecraft-crypto-market-research|"
            r"tradecraft\.runtime\.crypto_market_research_runner|"
            r"crypto_market_research_runner\.py"
        ),
        label="crypto market research runner",
        session_names=(
            "tradecraft-crypto-market-research",
            "hermes-crypto-market-research",
        ),
        command=".venv/bin/tradecraft-crypto-market-research",
        log_path=".runtime/crypto_market_research.log",
    ),
    "crypto_pattern_lab": RunnerSpec(
        key="crypto_pattern_lab",
        pid_file="tradecraft-crypto-pattern-lab.pid",
        pattern=(
            r"tradecraft-crypto-pattern-lab|"
            r"tradecraft\.runtime\.crypto_pattern_lab_runner|"
            r"crypto_pattern_lab_runner\.py"
        ),
        label="crypto pattern lab runner",
        session_names=("tradecraft-crypto-pattern-lab", "hermes-crypto-pattern-lab"),
        command=".venv/bin/tradecraft-crypto-pattern-lab",
        log_path=".runtime/crypto_pattern_lab.log",
    ),
    "crypto_alpha": RunnerSpec(
        key="crypto_alpha",
        pid_file="tradecraft-crypto-alpha.pid",
        pattern=(
            r"tradecraft-crypto-alpha|tradecraft\.runtime\.crypto_alpha_runner|"
            r"crypto_alpha_runner\.py"
        ),
        label="crypto alpha runner",
        session_names=("tradecraft-crypto-alpha", "hermes-crypto-alpha"),
        command=".venv/bin/tradecraft-crypto-alpha",
        log_path=".runtime/crypto_alpha.log",
    ),
    "jue_wiki": RunnerSpec(
        key="jue_wiki",
        pid_file="tradecraft-jue-wiki.pid",
        pattern=(
            r"tradecraft-jue-wiki|tradecraft\.runtime\.jue_wiki_runner|"
            r"jue_wiki_runner\.py"
        ),
        label="Jue wiki runner",
        session_names=("tradecraft-jue-wiki", "hermes-jue-wiki"),
        command=".venv/bin/tradecraft-jue-wiki",
        log_path=".runtime/jue_wiki_runner.log",
    ),
    "investment_memory": RunnerSpec(
        key="investment_memory",
        pid_file="tradecraft-investment-memory.pid",
        pattern=(
            r"tradecraft-investment-memory|"
            r"tradecraft\.runtime\.investment_memory_runner|"
            r"investment_memory_runner\.py"
        ),
        label="investment memory runner",
        session_names=("tradecraft-investment-memory", "hermes-investment-memory"),
        command=".venv/bin/tradecraft-investment-memory",
        log_path=".runtime/investment_memory.log",
    ),
    "live_evaluator": RunnerSpec(
        key="live_evaluator",
        pid_file="tradecraft-live-evaluator.pid",
        pattern=(
            r"tradecraft-live-evaluator|"
            r"tradecraft\.runtime\.live_evaluator_runner|"
            r"live_evaluator_runner\.py"
        ),
        label="live evaluator runner",
        session_names=("tradecraft-live-evaluator", "hermes-live-evaluator"),
        command=".venv/bin/tradecraft-live-evaluator",
        log_path=".runtime/live_evaluator.log",
    ),
    "naver_reports": RunnerSpec(
        key="naver_reports",
        pid_file="tradecraft-naver-reports.pid",
        pattern=(
            r"tradecraft-naver-reports|tradecraft\.runtime\.naver_reports_runner|"
            r"naver_reports_runner\.py"
        ),
        label="reports crawler",
        session_names=("tradecraft-naver-reports", "hermes-naver-reports"),
        command=".venv/bin/tradecraft-naver-reports",
        log_path=".runtime/naver_reports.log",
    ),
    "strategy_insights": RunnerSpec(
        key="strategy_insights",
        pid_file="tradecraft-strategy-insights.pid",
        pattern=(
            r"tradecraft-strategy-insights|"
            r"tradecraft\.runtime\.strategy_insights_runner|"
            r"strategy_insights_runner\.py"
        ),
        label="strategy insight runner",
        session_names=("tradecraft-strategy-insights", "hermes-strategy-insights"),
        command=".venv/bin/tradecraft-strategy-insights",
        log_path=".runtime/strategy_insights.log",
    ),
    "market_judge": RunnerSpec(
        key="market_judge",
        pid_file="tradecraft-market-judge.pid",
        pattern=(
            r"tradecraft-market-judge|tradecraft\.runtime\.market_judge_runner|"
            r"market_judge_runner\.py"
        ),
        label="market judge runner",
        session_names=("tradecraft-market-judge", "hermes-market-judge"),
        command=".venv/bin/tradecraft-market-judge",
        log_path=".runtime/market_judge.log",
    ),
    "market_pulse": RunnerSpec(
        key="market_pulse",
        pid_file="tradecraft-market-pulse.pid",
        pattern=(
            r"tradecraft-market-pulse|tradecraft\.runtime\.market_pulse_runner|"
            r"market_pulse_runner\.py"
        ),
        label="market pulse runner",
        session_names=("tradecraft-market-pulse", "hermes-market-pulse"),
        command=".venv/bin/tradecraft-market-pulse",
        log_path=".runtime/market_pulse.log",
    ),
    "watchdog": RunnerSpec(
        key="watchdog",
        pid_file="tradecraft-watchdog.pid",
        pattern=(
            r"tradecraft-watchdog|tradecraft\.runtime\.watchdog_runner|"
            r"watchdog_runner\.py"
        ),
        label="watchdog runner",
        session_names=("tradecraft-watchdog", "hermes-watchdog"),
        command=".venv/bin/tradecraft-watchdog",
        log_path=".runtime/watchdog.log",
    ),
}

DEFAULT_RESTART_RUNNER_KEYS: tuple[str, ...] = (
    "control",
    "runtime",
    "kis_block_trader",
    "investment_memory",
    "live_evaluator",
    "market_judge",
    "market_pulse",
    "binance_block_trader",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
    "jue_wiki",
    "strategy_insights",
    "watchdog",
)
