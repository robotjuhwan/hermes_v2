from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.llm_model_policy import llm_model_config_kwargs

try:
    from tradecraft.runtime.process_status import (
        clear_current_runner_pid,
        write_current_runner_pid,
    )
except ImportError:
    clear_current_runner_pid = None  # type: ignore[assignment]
    write_current_runner_pid = None  # type: ignore[assignment]

try:
    from tradecraft.services.crypto_market_research import (
        CryptoMarketResearchConfig,
        CryptoMarketResearchService,
    )
except ImportError:
    CryptoMarketResearchConfig = None  # type: ignore[assignment]
    CryptoMarketResearchService = None  # type: ignore[assignment]

try:
    from tradecraft.services.crypto_quant import CryptoQuantRepository
except ImportError:
    CryptoQuantRepository = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]
STATE_TEXT_LIMIT = 220
STATE_SAMPLE_LIMIT = 3
STATE_DICT_KEY_LIMIT = 18


def _setting(settings: AppSettings, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _timestamp_from_iso(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_crypto_universe(value: Any) -> list[str]:
    symbols = [
        str(part or "").strip().upper()
        for part in re.split(r"[\s,;]+", str(value or ""))
        if str(part or "").strip()
    ]
    return list(dict.fromkeys(symbols))


def parse_kline_intervals(value: Any) -> dict[str, int]:
    intervals: dict[str, int] = {}
    for raw_part in re.split(r"[\s,;]+", str(value or "")):
        part = raw_part.strip()
        if ":" not in part:
            continue
        interval, raw_limit = part.split(":", 1)
        interval = interval.strip()
        try:
            limit = int(raw_limit.strip())
        except ValueError:
            continue
        if interval and limit > 0:
            intervals[interval] = limit
    return intervals


def _compact_text_for_state(value: str, *, limit: int = STATE_TEXT_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"[omitted {len(text)} chars]"


def _compact_value_for_state(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text_for_state(value)
    if isinstance(value, list):
        return [
            _compact_value_for_state(item, depth=depth + 1)
            for item in value[:STATE_SAMPLE_LIMIT]
        ]
    if isinstance(value, tuple):
        return _compact_value_for_state(list(value), depth=depth)
    if isinstance(value, dict):
        if depth >= 3:
            return {
                "key_count": len(value),
                "keys": [str(key) for key in list(value)[:STATE_DICT_KEY_LIMIT]],
            }
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= STATE_DICT_KEY_LIMIT:
                compact["truncated"] = True
                compact["key_count"] = len(value)
                break
            compact[str(key)] = _compact_value_for_state(item, depth=depth + 1)
        return compact
    return _compact_text_for_state(str(value))


def _compact_collect_for_state(collect: Any) -> Any:
    if not isinstance(collect, dict):
        return _compact_value_for_state(collect)
    items = collect.get("items") if isinstance(collect.get("items"), list) else []
    errors = collect.get("errors") if isinstance(collect.get("errors"), list) else []
    compact = {
        key: _compact_value_for_state(value)
        for key, value in collect.items()
        if key not in {"items", "errors"}
    }
    compact["item_count"] = len(items)
    compact["items"] = [
        _compact_value_for_state(item, depth=1)
        for item in items[:STATE_SAMPLE_LIMIT]
    ]
    compact["items_truncated"] = len(items) > STATE_SAMPLE_LIMIT
    compact["error_count"] = collect.get("error_count", len(errors))
    compact["errors"] = [
        _compact_value_for_state(error, depth=1)
        for error in errors[:STATE_SAMPLE_LIMIT]
    ]
    compact["errors_truncated"] = len(errors) > STATE_SAMPLE_LIMIT
    return compact


def _compact_universe_for_state(universe: Any) -> Any:
    if not isinstance(universe, dict):
        return _compact_value_for_state(universe)
    dynamic_candidates = (
        universe.get("dynamic_candidates")
        if isinstance(universe.get("dynamic_candidates"), list)
        else []
    )
    excluded_symbols = (
        universe.get("excluded_symbols")
        if isinstance(universe.get("excluded_symbols"), list)
        else []
    )
    compact = {
        key: _compact_value_for_state(value)
        for key, value in universe.items()
        if key not in {"dynamic_candidates", "excluded_symbols"}
    }
    compact["dynamic_candidate_count"] = len(dynamic_candidates)
    compact["dynamic_candidates"] = [
        _compact_value_for_state(item, depth=1)
        for item in dynamic_candidates[:STATE_SAMPLE_LIMIT]
    ]
    compact["dynamic_candidates_truncated"] = (
        len(dynamic_candidates) > STATE_SAMPLE_LIMIT
    )
    compact["excluded_symbol_count"] = len(excluded_symbols)
    compact["excluded_symbols"] = excluded_symbols[:STATE_SAMPLE_LIMIT]
    compact["excluded_symbols_truncated"] = len(excluded_symbols) > STATE_SAMPLE_LIMIT
    return compact


def _compact_crypto_research_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    compact = dict(snapshot)
    compact["state_compacted"] = True
    if "collect" in compact:
        compact["collect"] = _compact_collect_for_state(compact.get("collect"))
    if "universe" in compact:
        compact["universe"] = _compact_universe_for_state(compact.get("universe"))
    if "research" in compact:
        compact["research"] = _compact_value_for_state(compact.get("research"))
    return compact


def _build_binance_adapter(settings: AppSettings) -> BinanceAdapter:
    return BinanceAdapter(
        BinanceConfig(
            spot_api_key=settings.binance_spot_api_key,
            spot_api_secret=settings.binance_spot_api_secret,
            spot_base_url=settings.binance_spot_base_url,
            futures_api_key=settings.binance_futures_api_key,
            futures_api_secret=settings.binance_futures_api_secret,
            futures_base_url=settings.binance_futures_base_url,
            usdt_krw_rate=settings.binance_usdt_krw,
        )
    )


def _build_memory(settings: AppSettings, *, codex_runtime: CodexNativeRuntime) -> InvestmentMemoryService:
    return InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=settings.investment_memory_root_path,
            db_path=settings.investment_memory_db_path,
            strategy_md_path=settings.research_strategy_md_path,
            policy_mode=settings.investment_memory_policy_mode,
            persona_tone=settings.investment_memory_persona_tone,
            telegram_enabled=False,
            context_max_chars=settings.investment_memory_context_max_chars,
            ops_summary_cache_ttl_sec=int(
                getattr(settings, "investment_memory_ops_summary_cache_ttl_sec", 10)
            ),
        ),
        codex_runtime=codex_runtime,
    )


def build_crypto_market_research_service(settings: AppSettings) -> Any:
    if CryptoMarketResearchConfig is None or CryptoMarketResearchService is None:
        raise RuntimeError("CryptoMarketResearchService is not available")

    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            **llm_model_config_kwargs(settings, component="crypto_market_research"),
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="crypto_market_research",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    config = CryptoMarketResearchConfig(
        db_path=str(
            _setting(
                settings,
                "crypto_market_research_db_path",
                ".runtime/crypto_market_research.db",
            )
        ),
        max_symbols=int(_setting(settings, "crypto_market_research_max_symbols", 300)),
        llm_model=str(
            _setting(
                settings,
                "crypto_market_research_llm_model",
                "gpt-5.6-terra",
            )
        ),
        llm_reasoning_effort=str(
            _setting(settings, "crypto_market_research_llm_reasoning_effort", "xhigh")
        ),
        external_enabled=bool(
            _setting(settings, "crypto_market_research_external_enabled", True)
        ),
        external_sources=str(
            _setting(
                settings,
                "crypto_market_research_external_sources",
                "coingecko,defillama,fear_greed",
            )
        ),
        auto_universe_enabled=bool(
            _setting(settings, "crypto_market_research_auto_universe_enabled", True)
        ),
        auto_universe_limit=int(
            _setting(settings, "crypto_market_research_auto_universe_limit", 300)
        ),
        research_universe_limit=int(
            _setting(settings, "crypto_market_research_research_universe_limit", 80)
        ),
        llm_top_symbols=int(_setting(settings, "crypto_market_research_llm_top_symbols", 30)),
        min_quote_volume_usdt=float(
            _setting(settings, "crypto_market_research_min_quote_volume_usdt", 100_000.0)
        ),
        kline_intervals=parse_kline_intervals(
            _setting(
                settings,
                "crypto_market_research_kline_intervals",
                "1m:120,5m:96,15m:96,1h:168,4h:180",
            )
        ),
        kline_hot_window_rows=int(
            _setting(settings, "crypto_market_research_kline_hot_window_rows", 720)
        ),
        market_hot_window_rows=int(
            _setting(settings, "crypto_market_research_market_hot_window_rows", 720)
        ),
        regime_enabled=bool(
            _setting(settings, "crypto_market_research_regime_enabled", True)
        ),
        squeeze_guard_enabled=bool(
            _setting(settings, "crypto_market_research_squeeze_guard_enabled", True)
        ),
        collect_symbol_timeout_sec=float(
            _setting(settings, "crypto_market_research_collect_symbol_timeout_sec", 20.0)
        ),
        collect_cycle_timeout_sec=float(
            _setting(settings, "crypto_market_research_collect_cycle_timeout_sec", 240.0)
        ),
        collect_concurrency=int(
            _setting(settings, "crypto_market_research_collect_concurrency", 4)
        ),
    )
    memory = _build_memory(settings, codex_runtime=bridge)
    quant_repository = None
    if bool(_setting(settings, "crypto_quant_enabled", True)) and CryptoQuantRepository is not None:
        quant_repository = CryptoQuantRepository(
            str(_setting(settings, "crypto_quant_db_path", ".runtime/crypto_quant.db"))
        )
    return CryptoMarketResearchService(
        config=config,
        binance=_build_binance_adapter(settings),
        codex_runtime=bridge,
        memory_provider=memory.context_pack,
        quant_repository=quant_repository,
    )


async def run_crypto_market_research_loop(
    *,
    settings: AppSettings,
    service: Any | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    store = RuntimeStateStore(
        str(
            _setting(
                settings,
                "crypto_market_research_state_path",
                ".runtime/crypto_market_research.json",
            )
        )
    )
    if not bool(_setting(settings, "crypto_market_research_enabled", True)):
        store.write_snapshot(
            {
                "service": "tradecraft-crypto-market-research",
                "status": "disabled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    try:
        resolved = service or build_crypto_market_research_service(settings)
    except Exception as exc:
        logger.exception("crypto market research service build failed")
        store.write_snapshot(
            {
                "service": "tradecraft-crypto-market-research",
                "status": "error",
                "error_message": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    feature_interval = max(
        int(_setting(settings, "crypto_market_research_feature_interval_sec", 300)),
        60,
    )
    llm_interval = max(
        int(_setting(settings, "crypto_market_research_llm_interval_sec", 3600)),
        300,
    )
    max_symbols = max(int(_setting(settings, "crypto_market_research_max_symbols", 300)), 1)
    research_universe_limit = max(
        int(_setting(settings, "crypto_market_research_research_universe_limit", 80)),
        1,
    )
    auto_universe_enabled = bool(
        _setting(settings, "crypto_market_research_auto_universe_enabled", True)
    )
    auto_universe_limit = max(
        int(_setting(settings, "crypto_market_research_auto_universe_limit", 300)),
        0,
    )
    collect_symbol_timeout = max(
        float(_setting(settings, "crypto_market_research_collect_symbol_timeout_sec", 20.0)),
        1.0,
    )
    collect_cycle_timeout = max(
        float(_setting(settings, "crypto_market_research_collect_cycle_timeout_sec", 240.0)),
        1.0,
    )
    collect_concurrency = max(
        int(_setting(settings, "crypto_market_research_collect_concurrency", 4)),
        1,
    )
    once = bool(_setting(settings, "crypto_market_research_once", False))
    cycle = 0
    previous_snapshot = store.read_snapshot() or {}
    last_llm_run_at = str(previous_snapshot.get("last_llm_run_at") or "")
    last_llm_at = _timestamp_from_iso(last_llm_run_at)

    while True:
        cycle += 1
        seed_symbols = parse_crypto_universe(
            _setting(
                settings,
                "crypto_market_research_universe",
                "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
            )
        )
        universe = {
            "status": "ok",
            "symbols": seed_symbols[:max_symbols],
            "static_count": min(len(seed_symbols), max_symbols),
            "dynamic_count": 0,
        }
        resolve_universe = getattr(resolved, "resolve_universe", None)
        if resolve_universe is not None:
            try:
                universe_result = resolve_universe(
                    seed_symbols,
                    auto_enabled=auto_universe_enabled,
                    auto_limit=auto_universe_limit,
                    max_symbols=max_symbols,
                )
                if asyncio.iscoroutine(universe_result):
                    universe_result = await universe_result
                if isinstance(universe_result, dict) and universe_result.get("symbols"):
                    universe = universe_result
            except Exception as exc:
                logger.warning("crypto universe resolve failed: %s", exc)
                universe = {
                    **universe,
                    "status": "partial",
                    "error_message": str(exc),
                }
        observed_symbols = [str(symbol) for symbol in universe.get("symbols", [])][:max_symbols]
        symbols = observed_symbols[: min(research_universe_limit, len(observed_symbols))]
        store.write_snapshot(
            _compact_crypto_research_snapshot(
                {
                    "service": "tradecraft-crypto-market-research",
                    "status": "collecting",
                    "cycle": cycle,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "feature_interval_sec": feature_interval,
                    "llm_interval_sec": llm_interval,
                    "symbols": symbols,
                    "symbol_count": len(symbols),
                    "observed_symbols": observed_symbols,
                    "observed_symbol_count": len(observed_symbols),
                    "research_universe_limit": research_universe_limit,
                    "collect_symbol_timeout_sec": collect_symbol_timeout,
                    "collect_cycle_timeout_sec": collect_cycle_timeout,
                    "collect_concurrency": collect_concurrency,
                    "last_llm_run_at": last_llm_run_at,
                    "universe": universe,
                }
            )
        )
        logger.info(
            "crypto market research cycle=%s collecting observed=%s symbols=%s",
            cycle,
            len(observed_symbols),
            len(symbols),
        )
        now = datetime.now(timezone.utc).timestamp()
        research: dict[str, Any] = {"status": "skipped", "reason": "cadence"}
        try:
            collect = await resolved.collect_market_structure(symbols)
            prune_history = getattr(resolved, "prune_history", None)
            if prune_history is not None:
                try:
                    prune_history(
                        market_retention_days=int(
                            _setting(settings, "crypto_market_research_retention_days", 3)
                        ),
                        quant_retention_days=int(
                            _setting(settings, "crypto_quant_retention_days", 3)
                        ),
                        market_archive_retention_days=int(
                            _setting(
                                settings,
                                "crypto_market_research_archive_retention_days",
                                7,
                            )
                        ),
                        quant_archive_retention_days=int(
                            _setting(settings, "crypto_quant_archive_retention_days", 7)
                        ),
                        quant_hot_window_rows=int(
                            _setting(settings, "crypto_quant_hot_window_rows", 360)
                        ),
                        quant_archive_window_rows=int(
                            _setting(settings, "crypto_quant_archive_window_rows", 360)
                        ),
                    )
                except Exception as exc:
                    logger.warning("crypto market research retention cleanup failed: %s", exc)
            if now - last_llm_at >= llm_interval:
                last_llm_run_at = datetime.now(timezone.utc).isoformat()
                last_llm_at = _timestamp_from_iso(last_llm_run_at)
                store.write_snapshot(
                    _compact_crypto_research_snapshot(
                        {
                            "service": "tradecraft-crypto-market-research",
                            "status": "researching",
                            "cycle": cycle,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "feature_interval_sec": feature_interval,
                            "llm_interval_sec": llm_interval,
                            "symbols": symbols,
                            "symbol_count": len(symbols),
                            "observed_symbols": observed_symbols,
                            "observed_symbol_count": len(observed_symbols),
                            "research_universe_limit": research_universe_limit,
                            "collect_symbol_timeout_sec": collect_symbol_timeout,
                            "collect_cycle_timeout_sec": collect_cycle_timeout,
                            "collect_concurrency": collect_concurrency,
                            "last_llm_run_at": last_llm_run_at,
                            "universe": universe,
                            "collect": collect,
                        }
                    )
                )
                collect_external_context = getattr(
                    resolved,
                    "collect_external_context",
                    None,
                )
                if collect_external_context is None:
                    external = {"status": "skipped", "reason": "not_supported"}
                else:
                    external = await collect_external_context(symbols=symbols)
                research = await resolved.run_research_once(symbols=symbols)
                research["external_context_collect"] = external
            status = str(collect.get("status") or "ok")
        except Exception as exc:
            logger.exception("crypto market research cycle failed")
            status = "error"
            collect = {"status": "error", "error_message": str(exc)}

        focus_symbols: list[str] = []
        select_focus = getattr(resolved, "select_llm_focus_symbols", None)
        if select_focus is not None:
            try:
                focus_symbols = select_focus(symbols=symbols)
            except Exception:
                focus_symbols = []
        snapshot = {
            "service": "tradecraft-crypto-market-research",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "feature_interval_sec": feature_interval,
            "llm_interval_sec": llm_interval,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "observed_symbols": observed_symbols,
            "observed_symbol_count": len(observed_symbols),
            "research_universe_limit": research_universe_limit,
            "collect_symbol_timeout_sec": collect_symbol_timeout,
            "collect_cycle_timeout_sec": collect_cycle_timeout,
            "collect_concurrency": collect_concurrency,
            "last_llm_run_at": last_llm_run_at,
            "focus_symbols": focus_symbols,
            "focus_symbol_count": len(focus_symbols),
            "universe": universe,
            "collect": collect,
            "research": research,
        }
        store.write_snapshot(_compact_crypto_research_snapshot(snapshot))
        logger.info(
            "crypto market research cycle=%s status=%s observed=%s symbols=%s research=%s",
            cycle,
            status,
            len(observed_symbols),
            len(symbols),
            research.get("status"),
        )
        if once:
            return
        await sleep(float(feature_interval))


def run() -> None:
    if write_current_runner_pid is not None:
        write_current_runner_pid("crypto_market_research")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if not bool(_setting(settings, "crypto_market_research_enabled", True)):
            logger.info(
                "crypto market research disabled: "
                "TRADECRAFT_CRYPTO_MARKET_RESEARCH_ENABLED=false"
            )
            return
        asyncio.run(run_crypto_market_research_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("crypto market research runner interrupted; stopping")
    finally:
        if clear_current_runner_pid is not None:
            clear_current_runner_pid("crypto_market_research")


if __name__ == "__main__":
    run()
