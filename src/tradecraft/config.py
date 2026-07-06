from __future__ import annotations

import json
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MARKET_INTELLIGENCE_SOURCES_JSON = json.dumps(
    [
        {
            "source_id": "whale_insight",
            "label": "Whale Insight",
            "role": "고래 포지션, 5% 이상 대량보유 변동, 전설 투자자 포트폴리오를 참고 신호로 추적",
            "coverage": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
            "signal_types": [
                "large_holder_change",
                "institutional_position",
                "legend_portfolio",
            ],
            "caution": "자동화 수집 기반 참고 데이터로 오류 가능성을 전제로 교차검증",
        },
        {
            "source_id": "after_close_330",
            "label": "세시반",
            "role": "장마감 후 수급, 섹터 트리맵, 종가 후보를 5분 브리프용 참고 신호로 추적",
            "coverage": ["KOSPI", "KOSDAQ"],
            "signal_types": [
                "after_close_flow",
                "sector_treemap",
                "closing_candidate",
            ],
            "caution": "다음 거래일 블록 매매 후보를 좁히는 당일 수급/섹터 운영 신호",
        },
    ],
    ensure_ascii=False,
)

DEFAULT_STRATEGY_INSIGHT_SOURCES_JSON = json.dumps(
    [
        {
            "source_id": "whale_insight",
            "label": "Whale Insight 공개 데이터",
            "kind": "whale_insight_static",
            "url": "https://whale-insight.com/major_stock",
            "cache_path": ".runtime/cache/whale_insight_public_signals.json",
            "symbol_cache_path": ".runtime/cache/strategy_insight_symbol_cache.json",
            "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
            "limit": 40,
            "enabled": True,
        },
        {
            "source_id": "after_close_330",
            "label": "세시반 공개 JSON",
            "kind": "sesiban_leading",
            "url": "https://api.lefthanders-new.xyz/api/v1/rankings/leading?market=KR",
            "cache_path": ".runtime/cache/sesiban_public_signals.json",
            "limit": 40,
            "enabled": True,
        },
    ],
    ensure_ascii=False,
)

DEFAULT_CRYPTO_MARKET_RESEARCH_UNIVERSE = (
    "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,"
    "LINKUSDT,DOTUSDT,LTCUSDT,BCHUSDT,TONUSDT,TRXUSDT,NEARUSDT,ATOMUSDT,"
    "APTUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,FILUSDT,ETCUSDT,UNIUSDT,"
    "AAVEUSDT,MATICUSDT,SEIUSDT,RUNEUSDT,IMXUSDT,RENDERUSDT"
)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("TRADECRAFT_TELEGRAM_BOT_TOKEN", "TELE_TOKEN"),
    )
    telegram_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("TRADECRAFT_TELEGRAM_CHAT_ID", "TELE_CHAT_ID"),
    )
    telegram_webhook_secret: str = Field(
        default="",
        alias="TRADECRAFT_TELEGRAM_WEBHOOK_SECRET",
    )
    admin_token: str = Field(default="", alias="TRADECRAFT_ADMIN_TOKEN")
    admin_tokens: str = Field(default="", alias="TRADECRAFT_ADMIN_TOKENS")
    upbit_access_key: str = Field(default="", alias="UPBIT_ACCESS_KEY")
    upbit_secret_key: str = Field(default="", alias="UPBIT_SECRET_KEY")
    upbit_base_url: str = Field(default="https://api.upbit.com", alias="UPBIT_BASE_URL")
    bithumb_access_key: str = Field(default="", alias="BITHUMB_ACCESS_KEY")
    bithumb_secret_key: str = Field(default="", alias="BITHUMB_SECRET_KEY")
    bithumb_base_url: str = Field(
        default="https://api.bithumb.com", alias="BITHUMB_BASE_URL"
    )
    binance_spot_api_key: str = Field(default="", alias="BINANCE_SPOT_API_KEY")
    binance_spot_api_secret: str = Field(default="", alias="BINANCE_SPOT_API_SECRET")
    binance_spot_base_url: str = Field(
        default="https://api.binance.com", alias="BINANCE_SPOT_BASE_URL"
    )
    binance_futures_api_key: str = Field(default="", alias="BINANCE_FUTURES_API_KEY")
    binance_futures_api_secret: str = Field(
        default="", alias="BINANCE_FUTURES_API_SECRET"
    )
    binance_futures_base_url: str = Field(
        default="https://fapi.binance.com", alias="BINANCE_FUTURES_BASE_URL"
    )
    binance_usdt_krw: float = Field(default=1387.0, alias="BINANCE_USDT_KRW")
    usd_krw: float = Field(default=1387.0, alias="USD_KRW")
    fx_cache_ttl_sec: int = Field(default=30, alias="FX_CACHE_TTL_SEC")
    dashboard_kis_balance_cache_ttl_sec: int = Field(
        default=180,
        alias="TRADECRAFT_DASHBOARD_KIS_BALANCE_CACHE_TTL_SEC",
    )
    dashboard_crypto_balance_cache_ttl_sec: int = Field(
        default=180,
        alias="TRADECRAFT_DASHBOARD_CRYPTO_BALANCE_CACHE_TTL_SEC",
    )
    dashboard_stale_balance_cache_ttl_sec: int = Field(
        default=7200,
        alias="TRADECRAFT_DASHBOARD_STALE_BALANCE_CACHE_TTL_SEC",
    )
    dashboard_kis_balance_error_cooldown_sec: int = Field(
        default=180,
        alias="TRADECRAFT_DASHBOARD_KIS_BALANCE_ERROR_COOLDOWN_SEC",
    )
    dashboard_balance_fetch_timeout_sec: float = Field(
        default=25.0,
        alias="TRADECRAFT_DASHBOARD_BALANCE_FETCH_TIMEOUT_SEC",
    )
    dashboard_kis_us_balance_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_DASHBOARD_KIS_US_BALANCE_ENABLED",
    )
    dashboard_payload_disk_cache_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_DASHBOARD_PAYLOAD_DISK_CACHE_ENABLED",
    )
    dashboard_payload_cache_path: str = Field(
        default=".runtime/dashboard_payload_cache.json",
        alias="TRADECRAFT_DASHBOARD_PAYLOAD_CACHE_PATH",
    )
    kis_base_url: str = Field(
        default="https://openapi.koreainvestment.com:9443",
        alias="KIS_BASE_URL",
    )
    kis_primary_app_key: str = Field(default="", alias="KIS_PRIMARY_APP_KEY")
    kis_primary_app_secret: str = Field(default="", alias="KIS_PRIMARY_APP_SECRET")
    kis_primary_account_no: str = Field(default="", alias="KIS_PRIMARY_ACCOUNT_NO")
    kis_primary_product_code: str = Field(default="", alias="KIS_PRIMARY_PRODUCT_CODE")
    kis_secondary_app_key: str = Field(default="", alias="KIS_SECONDARY_APP_KEY")
    kis_secondary_app_secret: str = Field(default="", alias="KIS_SECONDARY_APP_SECRET")
    kis_secondary_account_no: str = Field(default="", alias="KIS_SECONDARY_ACCOUNT_NO")
    kis_secondary_product_code: str = Field(
        default="", alias="KIS_SECONDARY_PRODUCT_CODE"
    )
    kis_rate_limit_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_KIS_RATE_LIMIT_ENABLED",
    )
    kis_rest_rate_limit_per_sec: float = Field(
        default=8.0,
        alias="TRADECRAFT_KIS_REST_RATE_LIMIT_PER_SEC",
    )
    kis_account_min_interval_sec: float = Field(
        default=8.0,
        alias="TRADECRAFT_KIS_ACCOUNT_MIN_INTERVAL_SEC",
    )
    kis_token_min_interval_sec: float = Field(
        default=65.0,
        alias="TRADECRAFT_KIS_TOKEN_MIN_INTERVAL_SEC",
    )
    kis_rate_limit_db_path: str = Field(
        default=".runtime/kis_rate_limit.db",
        alias="TRADECRAFT_KIS_RATE_LIMIT_DB_PATH",
    )
    runtime_state_path: str = Field(
        default=".runtime/state.json", alias="TRADECRAFT_RUNTIME_STATE_PATH"
    )
    runtime_sessions_path: str = Field(
        default="", alias="TRADECRAFT_RUNTIME_SESSIONS_PATH"
    )
    backtest_cycles: int = Field(default=720, alias="TRADECRAFT_BACKTEST_CYCLES")
    backtest_step_sec: int = Field(default=60, alias="TRADECRAFT_BACKTEST_STEP_SEC")
    backtest_speed: float = Field(default=120.0, alias="TRADECRAFT_BACKTEST_SPEED")
    backtest_initial_price: float = Field(
        default=100_000_000.0,
        alias="TRADECRAFT_BACKTEST_INITIAL_PRICE",
    )
    backtest_volatility_bps: float = Field(
        default=18.0,
        alias="TRADECRAFT_BACKTEST_VOLATILITY_BPS",
    )
    backtest_drift_bps: float = Field(
        default=0.2,
        alias="TRADECRAFT_BACKTEST_DRIFT_BPS",
    )
    backtest_fee_rate: float = Field(
        default=0.0005,
        alias="TRADECRAFT_BACKTEST_FEE_RATE",
    )
    backtest_slippage_bps: float = Field(
        default=1.0,
        alias="TRADECRAFT_BACKTEST_SLIPPAGE_BPS",
    )
    backtest_seed: int = Field(default=7, alias="TRADECRAFT_BACKTEST_SEED")
    backtest_state_path: str = Field(
        default=".runtime/backtest_live.json",
        alias="TRADECRAFT_BACKTEST_STATE_PATH",
    )
    backtest_result_path: str = Field(
        default=".runtime/backtest_result.json",
        alias="TRADECRAFT_BACKTEST_RESULT_PATH",
    )
    backtest_data_registry_path: str = Field(
        default=".runtime/backtest_data_registry.json",
        alias="TRADECRAFT_BACKTEST_DATA_REGISTRY_PATH",
    )
    backtest_max_curve_points: int = Field(
        default=4000,
        alias="TRADECRAFT_BACKTEST_MAX_CURVE_POINTS",
    )
    backtest_emit_interval: int = Field(
        default=1,
        alias="TRADECRAFT_BACKTEST_EMIT_INTERVAL",
    )
    runtime_max_age_sec: int = Field(default=90, alias="TRADECRAFT_RUNTIME_MAX_AGE_SEC")
    runtime_write_interval_sec: int = Field(
        default=5, alias="TRADECRAFT_RUNTIME_WRITE_INTERVAL_SEC"
    )
    runtime_storage_large_file_threshold_mb: int = Field(
        default=10,
        alias="TRADECRAFT_RUNTIME_STORAGE_LARGE_FILE_THRESHOLD_MB",
    )
    runtime_storage_prune_unreferenced_pdfs: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_UNREFERENCED_PDFS",
    )
    runtime_storage_prune_extracted_report_pdfs: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_EXTRACTED_REPORT_PDFS",
    )
    runtime_storage_extracted_report_pdf_retention_days: int = Field(
        default=14,
        alias="TRADECRAFT_RUNTIME_STORAGE_EXTRACTED_REPORT_PDF_RETENTION_DAYS",
    )
    runtime_storage_prune_rag_repair_artifacts: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_RAG_REPAIR_ARTIFACTS",
    )
    runtime_storage_rag_repair_artifact_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_RUNTIME_STORAGE_RAG_REPAIR_ARTIFACT_RETENTION_DAYS",
    )
    runtime_storage_prune_rag_rebuild_backups: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_RAG_REBUILD_BACKUPS",
    )
    runtime_storage_rag_rebuild_backup_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_RUNTIME_STORAGE_RAG_REBUILD_BACKUP_RETENTION_DAYS",
    )
    runtime_storage_prune_old_runtime_logs: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_RUNTIME_LOGS",
    )
    runtime_storage_log_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_RUNTIME_STORAGE_LOG_RETENTION_DAYS",
    )
    runtime_storage_rotate_large_active_logs: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_ROTATE_LARGE_ACTIVE_LOGS",
    )
    runtime_storage_active_log_max_mb: int = Field(
        default=16,
        alias="TRADECRAFT_RUNTIME_STORAGE_ACTIVE_LOG_MAX_MB",
    )
    runtime_storage_active_log_tail_kb: int = Field(
        default=2048,
        alias="TRADECRAFT_RUNTIME_STORAGE_ACTIVE_LOG_TAIL_KB",
    )
    runtime_storage_prune_scratch_artifacts: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_SCRATCH_ARTIFACTS",
    )
    runtime_storage_scratch_artifact_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_RUNTIME_STORAGE_SCRATCH_ARTIFACT_RETENTION_DAYS",
    )
    runtime_storage_prune_old_backtest_artifacts: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_BACKTEST_ARTIFACTS",
    )
    runtime_storage_backtest_artifact_retention_days: int = Field(
        default=30,
        alias="TRADECRAFT_RUNTIME_STORAGE_BACKTEST_ARTIFACT_RETENTION_DAYS",
    )
    runtime_storage_prune_old_ui_check_artifacts: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_UI_CHECK_ARTIFACTS",
    )
    runtime_storage_ui_check_artifact_retention_days: int = Field(
        default=30,
        alias="TRADECRAFT_RUNTIME_STORAGE_UI_CHECK_ARTIFACT_RETENTION_DAYS",
    )
    runtime_storage_prune_zero_byte_runtime_markers: bool = Field(
        default=True,
        alias="TRADECRAFT_RUNTIME_STORAGE_PRUNE_ZERO_BYTE_RUNTIME_MARKERS",
    )
    runtime_storage_zero_byte_marker_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_RUNTIME_STORAGE_ZERO_BYTE_MARKER_RETENTION_DAYS",
    )
    runtime_storage_database_compact_min_free_mb: int = Field(
        default=4,
        alias="TRADECRAFT_RUNTIME_STORAGE_DATABASE_COMPACT_MIN_FREE_MB",
    )
    runtime_storage_database_compact_min_free_ratio_pct: float = Field(
        default=10.0,
        alias="TRADECRAFT_RUNTIME_STORAGE_DATABASE_COMPACT_MIN_FREE_RATIO_PCT",
    )
    research_state_path: str = Field(
        default=".runtime/research.json", alias="TRADECRAFT_RESEARCH_STATE_PATH"
    )
    research_max_age_sec: int = Field(
        default=3600, alias="TRADECRAFT_RESEARCH_MAX_AGE_SEC"
    )
    research_enabled: bool = Field(default=True, alias="TRADECRAFT_RESEARCH_ENABLED")
    research_runner_collect_reports: bool = Field(
        default=False,
        alias="TRADECRAFT_RESEARCH_RUNNER_COLLECT_REPORTS",
    )
    research_run_interval_sec: int = Field(
        default=1800, alias="TRADECRAFT_RESEARCH_RUN_INTERVAL_SEC"
    )
    intelligence_once: bool = Field(
        default=False,
        alias="TRADECRAFT_INTELLIGENCE_ONCE",
    )
    research_max_items: int = Field(default=20, alias="TRADECRAFT_RESEARCH_MAX_ITEMS")
    research_knowledge_max_chars: int = Field(
        default=28000,
        alias="TRADECRAFT_RESEARCH_KNOWLEDGE_MAX_CHARS",
    )
    research_advice_context_max_chars: int = Field(
        default=2200,
        alias="TRADECRAFT_RESEARCH_ADVICE_CONTEXT_MAX_CHARS",
    )
    research_db_reference_top_k: int = Field(
        default=16,
        alias="TRADECRAFT_RESEARCH_DB_REFERENCE_TOP_K",
    )
    research_market_scope: str = Field(
        default="KRX", alias="TRADECRAFT_RESEARCH_MARKET_SCOPE"
    )
    research_codex_command: str = Field(
        default="", alias="TRADECRAFT_RESEARCH_CODEX_COMMAND"
    )
    research_codex_query: str = Field(
        default="국장 스윙/단타 후보 종목 리서치",
        alias="TRADECRAFT_RESEARCH_CODEX_QUERY",
    )
    research_codex_timeout_sec: int = Field(
        default=120, alias="TRADECRAFT_RESEARCH_CODEX_TIMEOUT_SEC"
    )
    codex_runtime_mode_preference: str = Field(
        default="sdk",
        alias="TRADECRAFT_CODEX_NATIVE_MODE",
    )
    codex_runtime_sdk_codex_bin: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRADECRAFT_CODEX_NATIVE_BIN",
            "TRADECRAFT_CODEX_SDK_BIN",
        ),
    )
    codex_runtime_timeout_ms: int = Field(
        default=600000,
        alias="TRADECRAFT_CODEX_NATIVE_TIMEOUT_MS",
    )
    codex_native_thread_mode: str = Field(
        default="daily",
        alias="TRADECRAFT_CODEX_NATIVE_THREAD_MODE",
    )
    codex_native_thread_db_path: str = Field(
        default=".runtime/codex_native_threads.db",
        alias="TRADECRAFT_CODEX_NATIVE_THREAD_DB_PATH",
    )
    codex_native_compact_after_turns: int = Field(
        default=8,
        alias="TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS",
    )
    codex_native_read_turns: bool = Field(
        default=False,
        alias="TRADECRAFT_CODEX_NATIVE_READ_TURNS",
    )
    codex_native_account_check_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_CODEX_NATIVE_ACCOUNT_CHECK_INTERVAL_SEC",
    )
    codex_native_model_check_interval_sec: int = Field(
        default=900,
        alias="TRADECRAFT_CODEX_NATIVE_MODEL_CHECK_INTERVAL_SEC",
    )
    codex_native_developer_instructions_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CODEX_NATIVE_DEVELOPER_INSTRUCTIONS_ENABLED",
    )
    jue_codex_lab_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_JUE_CODEX_LAB_ENABLED",
    )
    jue_codex_lab_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_JUE_CODEX_LAB_INTERVAL_SEC",
    )
    jue_codex_lab_autonomy_mode: str = Field(
        default="auto_apply_verified",
        alias="TRADECRAFT_JUE_CODEX_LAB_AUTONOMY_MODE",
    )
    jue_codex_lab_db_path: str = Field(
        default=".runtime/jue_codex_lab.db",
        alias="TRADECRAFT_JUE_CODEX_LAB_DB_PATH",
    )
    jue_codex_lab_max_patch_bytes: int = Field(
        default=120_000,
        alias="TRADECRAFT_JUE_CODEX_LAB_MAX_PATCH_BYTES",
    )
    jue_codex_lab_allowed_paths: str = Field(
        default="src/tradecraft,tests,docs/superpowers/plans,docs/spec",
        alias="TRADECRAFT_JUE_CODEX_LAB_ALLOWED_PATHS",
    )
    jue_codex_lab_blocked_paths: str = Field(
        default=".env,.runtime,secrets,credentials,private_key",
        alias="TRADECRAFT_JUE_CODEX_LAB_BLOCKED_PATHS",
    )
    jue_codex_lab_max_tasks_per_cycle: int = Field(
        default=1,
        alias="TRADECRAFT_JUE_CODEX_LAB_MAX_TASKS_PER_CYCLE",
    )
    jue_codex_lab_market_hours_hot_deploy: bool = Field(
        default=False,
        alias="TRADECRAFT_JUE_CODEX_LAB_MARKET_HOURS_HOT_DEPLOY",
    )
    llm_model: str = Field(
        default="gpt-5.5",
        alias="TRADECRAFT_LLM_MODEL",
    )
    llm_reasoning_effort: str = Field(
        default="xhigh",
        alias="TRADECRAFT_LLM_REASONING_EFFORT",
    )
    jue_strategy_revision_id: str = Field(
        default="jue_edge_repair_v1",
        alias="TRADECRAFT_JUE_STRATEGY_REVISION_ID",
    )
    llm_usage_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_LLM_USAGE_ENABLED",
    )
    llm_usage_db_path: str = Field(
        default=".runtime/llm_usage.db",
        alias="TRADECRAFT_LLM_USAGE_DB_PATH",
    )
    jue_wiki_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_ENABLED",
            "jue_wiki_enabled",
        ),
    )
    jue_wiki_root_path: str = Field(
        default=".runtime/jue_wiki",
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_ROOT_PATH",
            "jue_wiki_root_path",
        ),
    )
    jue_wiki_db_path: str = Field(
        default=".runtime/jue_wiki/wiki.db",
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_DB_PATH",
            "jue_wiki_db_path",
        ),
    )
    jue_wiki_context_max_chars: int = Field(
        default=24000,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_CONTEXT_MAX_CHARS",
            "jue_wiki_context_max_chars",
        ),
    )
    jue_wiki_runner_interval_sec: int = Field(
        default=1800,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_RUNNER_INTERVAL_SEC",
            "jue_wiki_runner_interval_sec",
        ),
    )
    jue_wiki_page_max_chars: int = Field(
        default=12000,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_PAGE_MAX_CHARS",
            "jue_wiki_page_max_chars",
        ),
    )
    jue_wiki_context_page_limit: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_CONTEXT_PAGE_LIMIT",
            "jue_wiki_context_page_limit",
        ),
    )
    jue_wiki_prompt_mode: str = Field(
        default="assist",
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_PROMPT_MODE",
            "jue_wiki_prompt_mode",
        ),
    )
    jue_wiki_selector_max_pages: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_SELECTOR_MAX_PAGES",
            "jue_wiki_selector_max_pages",
        ),
    )
    jue_wiki_selector_min_confidence: float = Field(
        default=0.15,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_SELECTOR_MIN_CONFIDENCE",
            "jue_wiki_selector_min_confidence",
        ),
    )
    jue_wiki_exclude_lint_warnings: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_EXCLUDE_LINT_WARNINGS",
            "jue_wiki_exclude_lint_warnings",
        ),
    )
    jue_wiki_repair_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_REPAIR_ENABLED",
            "jue_wiki_repair_enabled",
        ),
    )
    jue_wiki_full_prompt_max_chars: int = Field(
        default=190_000,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_FULL_PROMPT_MAX_CHARS",
            "jue_wiki_full_prompt_max_chars",
        ),
    )
    jue_wiki_application_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED",
            "jue_wiki_application_enabled",
        ),
    )
    jue_wiki_effectiveness_weight: float = Field(
        default=0.12,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT",
            "jue_wiki_effectiveness_weight",
        ),
    )
    jue_wiki_effectiveness_max_adjustment: float = Field(
        default=8.0,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT",
            "jue_wiki_effectiveness_max_adjustment",
        ),
    )
    jue_wiki_effectiveness_min_samples: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES",
            "jue_wiki_effectiveness_min_samples",
        ),
    )
    jue_wiki_mode_recommendation_min_samples: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES",
            "jue_wiki_mode_recommendation_min_samples",
        ),
    )
    research_report_urls: str = Field(
        default="", alias="TRADECRAFT_RESEARCH_REPORT_URLS"
    )
    research_strategy_md_path: str = Field(
        default=".runtime/strategy_krx.md",
        alias="TRADECRAFT_RESEARCH_STRATEGY_MD_PATH",
    )
    portfolio_coach_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_PORTFOLIO_COACH_ENABLED",
    )
    portfolio_coach_user_id: str = Field(
        default="default",
        alias="TRADECRAFT_PORTFOLIO_COACH_USER_ID",
    )
    portfolio_coach_db_path: str = Field(
        default=".runtime/portfolio_coach.db",
        alias="TRADECRAFT_PORTFOLIO_COACH_DB_PATH",
    )
    portfolio_coach_lookback_days: int = Field(
        default=60,
        alias="TRADECRAFT_PORTFOLIO_COACH_LOOKBACK_DAYS",
    )
    portfolio_coach_concentration_threshold: float = Field(
        default=0.30,
        alias="TRADECRAFT_PORTFOLIO_COACH_CONCENTRATION_THRESHOLD",
    )
    portfolio_coach_max_candidates: int = Field(
        default=12,
        alias="TRADECRAFT_PORTFOLIO_COACH_MAX_CANDIDATES",
    )
    portfolio_coach_top_n: int = Field(
        default=5,
        alias="TRADECRAFT_PORTFOLIO_COACH_TOP_N",
    )
    portfolio_coach_option_count: int = Field(
        default=3,
        alias="TRADECRAFT_PORTFOLIO_COACH_OPTION_COUNT",
    )
    portfolio_coach_trigger_count: int = Field(
        default=3,
        alias="TRADECRAFT_PORTFOLIO_COACH_TRIGGER_COUNT",
    )
    portfolio_coach_time_horizon: str = Field(
        default="중기",
        alias="TRADECRAFT_PORTFOLIO_COACH_TIME_HORIZON",
    )
    portfolio_coach_max_single_position_weight: float = Field(
        default=0.20,
        alias="TRADECRAFT_PORTFOLIO_COACH_MAX_SINGLE_POSITION_WEIGHT",
    )
    portfolio_coach_max_sector_weight: float = Field(
        default=0.35,
        alias="TRADECRAFT_PORTFOLIO_COACH_MAX_SECTOR_WEIGHT",
    )
    portfolio_coach_rebalance_frequency: str = Field(
        default="weekly",
        alias="TRADECRAFT_PORTFOLIO_COACH_REBALANCE_FREQUENCY",
    )
    portfolio_coach_risk_budget: str = Field(
        default="중간",
        alias="TRADECRAFT_PORTFOLIO_COACH_RISK_BUDGET",
    )
    portfolio_coach_idea_filters: str = Field(
        default="최근 리포트 존재",
        alias="TRADECRAFT_PORTFOLIO_COACH_IDEA_FILTERS",
    )
    portfolio_coach_factor_weights_json: str = Field(
        default="",
        alias="TRADECRAFT_PORTFOLIO_COACH_FACTOR_WEIGHTS_JSON",
    )
    portfolio_coach_ticker_name_map_json: str = Field(
        default="",
        alias="TRADECRAFT_PORTFOLIO_COACH_TICKER_NAME_MAP_JSON",
    )
    portfolio_coach_review_queue_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_PORTFOLIO_COACH_REVIEW_QUEUE_ENABLED",
    )
    kis_block_trader_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_ENABLED",
    )
    kis_block_trader_once: bool = Field(
        default=False,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_ONCE",
    )
    kis_block_trader_execute_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS",
    )
    kis_block_trader_db_path: str = Field(
        default=".runtime/kis_blocks.db",
        alias="TRADECRAFT_KIS_BLOCK_TRADER_DB_PATH",
    )
    kis_block_trader_state_path: str = Field(
        default=".runtime/kis_block_trader.json",
        alias="TRADECRAFT_KIS_BLOCK_TRADER_STATE_PATH",
    )
    kis_block_trader_rule_interval_sec: int = Field(
        default=10,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_RULE_INTERVAL_SEC",
    )
    kis_block_trader_manager_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_INTERVAL_SEC",
    )
    kis_block_trader_manager_error_retry_sec: int = Field(
        default=300,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_ERROR_RETRY_SEC",
    )
    kis_block_trader_retention_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_RETENTION_INTERVAL_SEC",
    )
    kis_block_trader_quote_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_QUOTE_RETENTION_DAYS",
    )
    kis_block_trader_reconciliation_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_RECONCILIATION_RETENTION_DAYS",
    )
    kis_block_trader_manager_run_retention_days: int = Field(
        default=14,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_RUN_RETENTION_DAYS",
    )
    kis_block_trader_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_ARCHIVE_RETENTION_DAYS",
    )
    kis_block_trader_aggressive_limit_bps: float = Field(
        default=30.0,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_AGGRESSIVE_LIMIT_BPS",
    )
    kis_block_trader_pending_reconcile_timeout_sec: int = Field(
        default=300,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_PENDING_RECONCILE_TIMEOUT_SEC",
    )
    kis_block_trader_failed_exit_retry_cooldown_sec: int = Field(
        default=60,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_FAILED_EXIT_RETRY_COOLDOWN_SEC",
    )
    kis_block_trader_max_manager_symbols: int = Field(
        default=80,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MAX_MANAGER_SYMBOLS",
    )
    kis_block_trader_prompt_target_chars: int = Field(
        default=100_000,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_PROMPT_TARGET_CHARS",
    )
    kis_block_trader_prompt_warn_chars: int = Field(
        default=150_000,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_PROMPT_WARN_CHARS",
    )
    kis_block_trader_prompt_max_chars: int = Field(
        default=190_000,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_PROMPT_MAX_CHARS",
    )
    kis_block_trader_manager_query: str = Field(
        default="국장1 계좌와 전략 지식을 바탕으로 블록 매매 계획을 관리해줘",
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_QUERY",
    )
    block_horizon_targets: str = Field(
        default="cash:0.30,short:0.15,mid:0.30,long:0.15,core_etf:0.10",
        validation_alias=AliasChoices("TRADECRAFT_KIS_BLOCK_HORIZON_TARGETS"),
    )
    kis_block_trader_etf_universe: str = Field(
        default=(
            "069500:KODEX 200,102110:TIGER 200,091160:KODEX 반도체,"
            "122630:KODEX 레버리지,229200:KODEX 코스닥150,"
            "360750:TIGER 미국S&P500,379800:KODEX 미국S&P500,"
            "133690:TIGER 미국나스닥100,379810:KODEX 미국나스닥100,"
            "396500:TIGER 반도체TOP10,459580:KODEX CD금리액티브(합성)"
        ),
        validation_alias=AliasChoices("TRADECRAFT_KIS_BLOCK_TRADER_ETF_UNIVERSE"),
    )
    binance_block_trader_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED",
    )
    binance_block_trader_execute_spot_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS",
    )
    binance_block_trader_execute_futures_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS",
    )
    binance_block_trader_execute_upbit_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_UPBIT_ORDERS",
    )
    binance_block_trader_once: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ONCE",
    )
    binance_block_trader_db_path: str = Field(
        default=".runtime/binance_blocks.db",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DB_PATH",
    )
    binance_block_trader_state_path: str = Field(
        default=".runtime/binance_block_trader.json",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_STATE_PATH",
    )
    binance_block_trader_quote_interval_sec: int = Field(
        default=15,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_QUOTE_INTERVAL_SEC",
    )
    binance_block_trader_rule_interval_sec: int = Field(
        default=15,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_RULE_INTERVAL_SEC",
    )
    binance_block_trader_manager_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_INTERVAL_SEC",
    )
    binance_block_trader_manager_error_retry_sec: int = Field(
        default=300,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_ERROR_RETRY_SEC",
    )
    binance_block_trader_performance_feedback_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PERFORMANCE_FEEDBACK_INTERVAL_SEC",
    )
    binance_block_trader_telegram_reports_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_TELEGRAM_REPORTS_ENABLED",
    )
    binance_block_trader_telegram_report_slots: str = Field(
        default="morning:06:00,noon:12:00,night:20:00",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_TELEGRAM_REPORT_SLOTS",
    )
    binance_block_trader_llm_model: str = Field(
        default="gpt-5.5",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL",
    )
    binance_block_trader_llm_reasoning_effort: str = Field(
        default="xhigh",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT",
    )
    binance_block_trader_llm_timeout_ms: int = Field(
        default=420_000,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_TIMEOUT_MS",
    )
    binance_block_trader_max_manager_symbols: int = Field(
        default=60,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_MANAGER_SYMBOLS",
    )
    binance_block_trader_prompt_target_chars: int = Field(
        default=70_000,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROMPT_TARGET_CHARS",
    )
    binance_block_trader_prompt_warn_chars: int = Field(
        default=90_000,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROMPT_WARN_CHARS",
    )
    binance_block_trader_prompt_max_chars: int = Field(
        default=190_000,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROMPT_MAX_CHARS",
    )
    binance_block_trader_jue_wiki_context_max_chars: int = Field(
        default=18_000,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_JUE_WIKI_CONTEXT_MAX_CHARS",
    )
    binance_block_trader_spot_universe: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_UNIVERSE",
    )
    binance_block_trader_futures_universe: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_UNIVERSE",
    )
    binance_block_trader_upbit_universe: str = Field(
        default="KRW-BTC,KRW-ETH,KRW-SOL,KRW-XRP,KRW-DOGE",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_UPBIT_UNIVERSE",
    )
    binance_block_trader_max_futures_leverage: int = Field(
        default=2,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_FUTURES_LEVERAGE",
    )
    binance_block_trader_min_liquidation_distance_pct: float = Field(
        default=12.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_LIQUIDATION_DISTANCE_PCT",
    )
    binance_block_trader_aggressive_limit_bps: float = Field(
        default=20.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_AGGRESSIVE_LIMIT_BPS",
    )
    binance_block_trader_failed_exit_retry_cooldown_sec: int = Field(
        default=60,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FAILED_EXIT_RETRY_COOLDOWN_SEC",
    )
    binance_block_trader_min_entry_confidence: float = Field(
        default=0.58,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_ENTRY_CONFIDENCE",
    )
    binance_block_trader_min_entry_expected_r: float = Field(
        default=0.55,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_ENTRY_EXPECTED_R",
    )
    binance_block_trader_min_entry_directional_score: float = Field(
        default=62.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_ENTRY_DIRECTIONAL_SCORE",
    )
    binance_block_trader_min_candidate_stop_pct: float = Field(
        default=1.2,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_CANDIDATE_STOP_PCT",
    )
    binance_block_trader_profit_lock_trigger_r: float = Field(
        default=1.2,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROFIT_LOCK_TRIGGER_R",
    )
    binance_block_trader_weak_lane_profit_lock_trigger_r: float = Field(
        default=0.8,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_WEAK_LANE_PROFIT_LOCK_TRIGGER_R",
    )
    binance_block_trader_distressed_lane_profit_lock_trigger_r: float = Field(
        default=0.55,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DISTRESSED_LANE_PROFIT_LOCK_TRIGGER_R",
    )
    binance_block_trader_entry_quality_loss_tighten_trigger_r: float = Field(
        default=0.5,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ENTRY_QUALITY_LOSS_TIGHTEN_TRIGGER_R",
    )
    binance_block_trader_distressed_lane_min_samples: int = Field(
        default=5,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DISTRESSED_LANE_MIN_SAMPLES",
    )
    binance_block_trader_distressed_lane_max_win_rate_pct: float = Field(
        default=20.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DISTRESSED_LANE_MAX_WIN_RATE_PCT",
    )
    binance_block_trader_distressed_lane_max_profit_factor: float = Field(
        default=0.5,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DISTRESSED_LANE_MAX_PROFIT_FACTOR",
    )
    binance_block_trader_distressed_entry_quality_partial_profit_fraction: float = Field(
        default=0.75,
        alias=(
            "TRADECRAFT_BINANCE_BLOCK_TRADER_DISTRESSED_ENTRY_QUALITY_"
            "PARTIAL_PROFIT_FRACTION"
        ),
    )
    binance_block_trader_profit_lock_stop_r: float = Field(
        default=0.25,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROFIT_LOCK_STOP_R",
    )
    binance_block_trader_profit_lock_min_net_buffer_pct: float = Field(
        default=0.12,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PROFIT_LOCK_MIN_NET_BUFFER_PCT",
    )
    binance_block_trader_spot_quote_budget_pct: float = Field(
        default=5.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_QUOTE_BUDGET_PCT",
    )
    binance_block_trader_spot_min_quote_budget_usdt: float = Field(
        default=50.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_MIN_QUOTE_BUDGET_USDT",
    )
    binance_block_trader_spot_max_quote_budget_usdt: float = Field(
        default=300.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_MAX_QUOTE_BUDGET_USDT",
    )
    binance_block_trader_upbit_quote_budget_pct: float = Field(
        default=5.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_UPBIT_QUOTE_BUDGET_PCT",
    )
    binance_block_trader_upbit_min_quote_budget_krw: float = Field(
        default=10_000.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_UPBIT_MIN_QUOTE_BUDGET_KRW",
    )
    binance_block_trader_upbit_max_quote_budget_krw: float = Field(
        default=150_000.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_UPBIT_MAX_QUOTE_BUDGET_KRW",
    )
    binance_block_trader_futures_quote_budget_pct: float = Field(
        default=10.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_QUOTE_BUDGET_PCT",
    )
    binance_block_trader_futures_min_quote_budget_usdt: float = Field(
        default=25.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_MIN_QUOTE_BUDGET_USDT",
    )
    binance_block_trader_futures_max_quote_budget_usdt: float = Field(
        default=150.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_MAX_QUOTE_BUDGET_USDT",
    )
    binance_block_trader_budget_performance_scale_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_BUDGET_PERFORMANCE_SCALE_ENABLED",
    )
    binance_block_trader_budget_performance_scale_min_samples: int = Field(
        default=10,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_BUDGET_PERFORMANCE_SCALE_MIN_SAMPLES",
    )
    binance_block_trader_budget_performance_scale_win_rate_pct: float = Field(
        default=55.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_BUDGET_PERFORMANCE_SCALE_WIN_RATE_PCT",
    )
    binance_block_trader_budget_performance_scale_multiplier: float = Field(
        default=1.5,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_BUDGET_PERFORMANCE_SCALE_MULTIPLIER",
    )
    binance_block_trader_execution_defect_loss_multiplier: float = Field(
        default=0.5,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTION_DEFECT_LOSS_MULTIPLIER",
    )
    binance_block_trader_performance_scorecard_feedback_limit: int = Field(
        default=120,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_PERFORMANCE_SCORECARD_FEEDBACK_LIMIT",
    )
    binance_block_trader_account_risk_pct: float = Field(
        default=0.25,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ACCOUNT_RISK_PCT",
    )
    binance_block_trader_max_total_exposure_usdt: float = Field(
        default=0.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_TOTAL_EXPOSURE_USDT",
    )
    binance_block_trader_max_symbol_exposure_pct: float = Field(
        default=25.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_SYMBOL_EXPOSURE_PCT",
    )
    binance_block_trader_min_reward_risk: float = Field(
        default=1.3,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_REWARD_RISK",
    )
    binance_block_trader_volatile_attack_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_ENABLED",
    )
    binance_block_trader_volatile_attack_candidate_limit: int = Field(
        default=12,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_CANDIDATE_LIMIT",
    )
    binance_block_trader_volatile_attack_budget_multiplier: float = Field(
        default=0.35,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_BUDGET_MULTIPLIER",
    )
    binance_block_trader_volatile_attack_min_change_pct: float = Field(
        default=8.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_MIN_CHANGE_PCT",
    )
    binance_block_trader_volatile_attack_min_volume_expansion: float = Field(
        default=1.8,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_MIN_VOLUME_EXPANSION",
    )
    binance_block_trader_volatile_attack_min_reward_risk: float = Field(
        default=2.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_MIN_REWARD_RISK",
    )
    binance_block_trader_volatile_attack_stop_multiplier: float = Field(
        default=1.35,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_VOLATILE_ATTACK_STOP_MULTIPLIER",
    )
    binance_block_trader_daily_loss_stop_pct: float = Field(
        default=7.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DAILY_LOSS_STOP_PCT",
    )
    binance_block_trader_monthly_loss_stop_pct: float = Field(
        default=20.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MONTHLY_LOSS_STOP_PCT",
    )
    binance_block_trader_quote_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_QUOTE_RETENTION_DAYS",
    )
    binance_block_trader_manager_run_retention_days: int = Field(
        default=14,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_RUN_RETENTION_DAYS",
    )
    binance_block_trader_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ARCHIVE_RETENTION_DAYS",
    )
    binance_block_trader_retention_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_RETENTION_INTERVAL_SEC",
    )
    crypto_market_research_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_ENABLED",
    )
    crypto_market_research_once: bool = Field(
        default=False,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_ONCE",
    )
    crypto_market_research_db_path: str = Field(
        default=".runtime/crypto_market_research.db",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_DB_PATH",
    )
    crypto_market_research_state_path: str = Field(
        default=".runtime/crypto_market_research.json",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_STATE_PATH",
    )
    crypto_market_research_universe: str = Field(
        default=DEFAULT_CRYPTO_MARKET_RESEARCH_UNIVERSE,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_UNIVERSE",
    )
    crypto_market_research_max_symbols: int = Field(
        default=300,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_MAX_SYMBOLS",
    )
    crypto_market_research_auto_universe_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_AUTO_UNIVERSE_ENABLED",
    )
    crypto_market_research_auto_universe_limit: int = Field(
        default=300,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_AUTO_UNIVERSE_LIMIT",
    )
    crypto_market_research_research_universe_limit: int = Field(
        default=80,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_RESEARCH_UNIVERSE_LIMIT",
    )
    crypto_market_research_llm_top_symbols: int = Field(
        default=30,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_TOP_SYMBOLS",
    )
    crypto_market_research_min_quote_volume_usdt: float = Field(
        default=100_000.0,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_MIN_QUOTE_VOLUME_USDT",
    )
    crypto_market_research_kline_intervals: str = Field(
        default="1m:120,5m:96,15m:96,1h:168,4h:180",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_INTERVALS",
    )
    crypto_market_research_kline_hot_window_rows: int = Field(
        default=720,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_HOT_WINDOW_ROWS",
    )
    crypto_market_research_market_hot_window_rows: int = Field(
        default=720,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_MARKET_HOT_WINDOW_ROWS",
    )
    crypto_market_research_regime_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_REGIME_ENABLED",
    )
    crypto_market_research_squeeze_guard_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_SQUEEZE_GUARD_ENABLED",
    )
    crypto_market_research_collect_symbol_timeout_sec: float = Field(
        default=20.0,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_COLLECT_SYMBOL_TIMEOUT_SEC",
    )
    crypto_market_research_collect_cycle_timeout_sec: float = Field(
        default=240.0,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_COLLECT_CYCLE_TIMEOUT_SEC",
    )
    crypto_market_research_collect_concurrency: int = Field(
        default=4,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_COLLECT_CONCURRENCY",
    )
    crypto_market_research_feature_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_FEATURE_INTERVAL_SEC",
    )
    crypto_market_research_llm_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_INTERVAL_SEC",
    )
    crypto_market_research_llm_model: str = Field(
        default="gpt-5.5",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_MODEL",
    )
    crypto_market_research_llm_reasoning_effort: str = Field(
        default="xhigh",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_REASONING_EFFORT",
    )
    crypto_market_research_external_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_EXTERNAL_ENABLED",
    )
    crypto_market_research_external_sources: str = Field(
        default="coingecko,defillama,fear_greed",
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_EXTERNAL_SOURCES",
    )
    crypto_market_research_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_RETENTION_DAYS",
    )
    crypto_market_research_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_ARCHIVE_RETENTION_DAYS",
    )
    crypto_quant_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_QUANT_ENABLED",
            "CRYPTO_QUANT_ENABLED",
        ),
    )
    crypto_quant_db_path: str = Field(
        default=".runtime/crypto_quant.db",
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_QUANT_DB_PATH",
            "CRYPTO_QUANT_DB_PATH",
        ),
    )
    crypto_quant_context_limit: int = Field(
        default=18,
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_QUANT_CONTEXT_LIMIT",
            "CRYPTO_QUANT_CONTEXT_LIMIT",
        ),
    )
    crypto_quant_hot_window_rows: int = Field(
        default=360,
        alias="TRADECRAFT_CRYPTO_QUANT_HOT_WINDOW_ROWS",
    )
    crypto_quant_archive_window_rows: int = Field(
        default=360,
        alias="TRADECRAFT_CRYPTO_QUANT_ARCHIVE_WINDOW_ROWS",
    )
    crypto_quant_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_CRYPTO_QUANT_RETENTION_DAYS",
    )
    crypto_quant_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_CRYPTO_QUANT_ARCHIVE_RETENTION_DAYS",
    )
    crypto_pattern_lab_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_PATTERN_LAB_ENABLED",
            "CRYPTO_PATTERN_LAB_ENABLED",
        ),
    )
    crypto_pattern_lab_once: bool = Field(
        default=False,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_ONCE",
    )
    crypto_pattern_lab_state_path: str = Field(
        default=".runtime/crypto_pattern_lab.json",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_STATE_PATH",
    )
    crypto_pattern_lab_db_path: str = Field(
        default=".runtime/crypto_pattern_lab.db",
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_PATTERN_LAB_DB_PATH",
            "CRYPTO_PATTERN_LAB_DB_PATH",
        ),
    )
    kr_equity_pattern_lab_db_path: str = Field(
        default=".runtime/kr_equity_pattern_lab.db",
        alias="TRADECRAFT_KR_EQUITY_PATTERN_LAB_DB_PATH",
    )
    kr_equity_pattern_lab_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_KR_EQUITY_PATTERN_LAB_ENABLED",
    )
    kr_equity_pattern_lab_min_samples: int = Field(
        default=3,
        alias="TRADECRAFT_KR_EQUITY_PATTERN_LAB_MIN_SAMPLES",
    )
    crypto_pattern_lab_strategy_paths: str = Field(
        default="",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_STRATEGY_PATHS",
    )
    crypto_pattern_lab_freqtrade_data_paths: str = Field(
        default="",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_FREQTRADE_DATA_PATHS",
    )
    crypto_pattern_lab_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_INTERVAL_SEC",
    )
    crypto_pattern_lab_max_symbols: int = Field(
        default=30,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_MAX_SYMBOLS",
    )
    crypto_pattern_lab_intervals: str = Field(
        default="5m,15m,1h",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_INTERVALS",
    )
    crypto_pattern_lab_lookback_bars: int = Field(
        default=500,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_LOOKBACK_BARS",
    )
    crypto_pattern_lab_context_limit: int = Field(
        default=12,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_CONTEXT_LIMIT",
    )
    crypto_pattern_lab_retention_days: int = Field(
        default=90,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_RETENTION_DAYS",
    )
    crypto_pattern_lab_backtests_per_tuple_retention: int = Field(
        default=4,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_BACKTESTS_PER_TUPLE_RETENTION",
    )
    crypto_pattern_lab_optimizer_runs_per_tuple_retention: int = Field(
        default=4,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_OPTIMIZER_RUNS_PER_TUPLE_RETENTION",
    )
    crypto_pattern_lab_optimizer_trials_per_run_retention: int = Field(
        default=8,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_OPTIMIZER_TRIALS_PER_RUN_RETENTION",
    )
    crypto_pattern_lab_max_backtest_rows: int = Field(
        default=80_000,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_MAX_BACKTEST_ROWS",
    )
    crypto_pattern_lab_max_optimizer_runs: int = Field(
        default=2_500,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_MAX_OPTIMIZER_RUNS",
    )
    crypto_pattern_lab_max_optimizer_trials: int = Field(
        default=24_000,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_MAX_OPTIMIZER_TRIALS",
    )
    crypto_pattern_lab_optimizer_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_OPTIMIZER_ENABLED",
    )
    crypto_pattern_lab_optimizer_max_scorecards: int = Field(
        default=60,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_OPTIMIZER_MAX_SCORECARDS",
    )
    crypto_pattern_lab_optimizer_max_trials_per_scorecard: int = Field(
        default=24,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_OPTIMIZER_MAX_TRIALS_PER_SCORECARD",
    )
    crypto_alpha_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CRYPTO_ALPHA_ENABLED",
    )
    crypto_alpha_once: bool = Field(
        default=False,
        alias="TRADECRAFT_CRYPTO_ALPHA_ONCE",
    )
    crypto_alpha_db_path: str = Field(
        default=".runtime/crypto_alpha.db",
        alias="TRADECRAFT_CRYPTO_ALPHA_DB_PATH",
    )
    crypto_alpha_state_path: str = Field(
        default=".runtime/crypto_alpha.json",
        alias="TRADECRAFT_CRYPTO_ALPHA_STATE_PATH",
    )
    crypto_alpha_source_ids: str = Field(
        default="binance_announcements,coinbase_blog,kraken_blog",
        alias="TRADECRAFT_CRYPTO_ALPHA_SOURCE_IDS",
    )
    crypto_alpha_crawl_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_CRYPTO_ALPHA_CRAWL_INTERVAL_SEC",
    )
    crypto_alpha_outcome_interval_sec: int = Field(
        default=900,
        alias="TRADECRAFT_CRYPTO_ALPHA_OUTCOME_INTERVAL_SEC",
    )
    crypto_alpha_rate_limit_sec: float = Field(
        default=2.0,
        alias="TRADECRAFT_CRYPTO_ALPHA_RATE_LIMIT_SEC",
    )
    crypto_alpha_context_limit: int = Field(
        default=12,
        alias="TRADECRAFT_CRYPTO_ALPHA_CONTEXT_LIMIT",
    )
    crypto_alpha_llm_model: str = Field(
        default="gpt-5.5",
        alias="TRADECRAFT_CRYPTO_ALPHA_LLM_MODEL",
    )
    crypto_alpha_llm_reasoning_effort: str = Field(
        default="xhigh",
        alias="TRADECRAFT_CRYPTO_ALPHA_LLM_REASONING_EFFORT",
    )
    etf_research_db_path: str = Field(
        default=".runtime/etf_research.db",
        alias="TRADECRAFT_ETF_RESEARCH_DB_PATH",
    )
    etf_research_universe: str = Field(
        default=(
            "069500:KODEX 200,102110:TIGER 200,091160:KODEX 반도체,"
            "122630:KODEX 레버리지,229200:KODEX 코스닥150,"
            "360750:TIGER 미국S&P500,379800:KODEX 미국S&P500,"
            "133690:TIGER 미국나스닥100,379810:KODEX 미국나스닥100,"
            "396500:TIGER 반도체TOP10,459580:KODEX CD금리액티브(합성)"
        ),
        alias="TRADECRAFT_ETF_RESEARCH_UNIVERSE",
    )
    etf_research_max_symbols: int = Field(
        default=30,
        alias="TRADECRAFT_ETF_RESEARCH_MAX_SYMBOLS",
    )
    etf_research_auto_collect: bool = Field(
        default=True,
        alias="TRADECRAFT_ETF_RESEARCH_AUTO_COLLECT",
    )
    etf_research_stale_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_ETF_RESEARCH_STALE_SEC",
    )
    etf_research_auto_min_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_ETF_RESEARCH_AUTO_MIN_INTERVAL_SEC",
    )
    etf_research_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_ETF_RESEARCH_RETENTION_DAYS",
    )
    etf_research_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_ETF_RESEARCH_ARCHIVE_RETENTION_DAYS",
    )
    investment_memory_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_INVESTMENT_MEMORY_ENABLED",
    )
    investment_memory_once: bool = Field(
        default=False,
        alias="TRADECRAFT_INVESTMENT_MEMORY_ONCE",
    )
    investment_memory_root_path: str = Field(
        default=".runtime/investment_memory",
        alias="TRADECRAFT_INVESTMENT_MEMORY_ROOT_PATH",
    )
    investment_memory_db_path: str = Field(
        default=".runtime/investment_memory.db",
        alias="TRADECRAFT_INVESTMENT_MEMORY_DB_PATH",
    )
    investment_memory_state_path: str = Field(
        default=".runtime/investment_memory_runner.json",
        alias="TRADECRAFT_INVESTMENT_MEMORY_STATE_PATH",
    )
    investment_memory_poll_interval_sec: int = Field(
        default=60,
        alias="TRADECRAFT_INVESTMENT_MEMORY_POLL_INTERVAL_SEC",
    )
    investment_memory_send_telegram: bool = Field(
        default=True,
        alias="TRADECRAFT_INVESTMENT_MEMORY_SEND_TELEGRAM",
    )
    investment_memory_run_daily_discovery: bool = Field(
        default=False,
        alias="TRADECRAFT_INVESTMENT_MEMORY_RUN_DAILY_DISCOVERY",
    )
    investment_memory_policy_mode: str = Field(
        default="soft_auto",
        alias="TRADECRAFT_INVESTMENT_MEMORY_POLICY_MODE",
    )
    investment_memory_persona_tone: str = Field(
        default="friendly_partner",
        alias="TRADECRAFT_INVESTMENT_MEMORY_PERSONA_TONE",
    )
    investment_memory_context_max_chars: int = Field(
        default=8000,
        alias="TRADECRAFT_INVESTMENT_MEMORY_CONTEXT_MAX_CHARS",
    )
    investment_memory_ops_summary_cache_ttl_sec: int = Field(
        default=10,
        alias="TRADECRAFT_INVESTMENT_MEMORY_OPS_SUMMARY_CACHE_TTL_SEC",
    )
    investment_memory_compaction_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_INVESTMENT_MEMORY_COMPACTION_INTERVAL_SEC",
    )
    investment_memory_policy_retired_keep: int = Field(
        default=2,
        alias="TRADECRAFT_INVESTMENT_MEMORY_POLICY_RETIRED_KEEP",
    )
    investment_memory_validation_event_retained_rows_per_venue: int = Field(
        default=720,
        alias="TRADECRAFT_INVESTMENT_MEMORY_VALIDATION_EVENT_RETAINED_ROWS_PER_VENUE",
    )
    investment_memory_run_recent_rows_per_group: int = Field(
        default=24,
        alias="TRADECRAFT_INVESTMENT_MEMORY_RUN_RECENT_ROWS_PER_GROUP",
    )
    investment_memory_symbol_analysis_recent_rows_per_symbol: int = Field(
        default=3,
        alias="TRADECRAFT_INVESTMENT_MEMORY_SYMBOL_ANALYSIS_RECENT_ROWS_PER_SYMBOL",
    )
    live_evaluator_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_LIVE_EVALUATOR_ENABLED",
    )
    live_evaluator_once: bool = Field(
        default=False,
        alias="TRADECRAFT_LIVE_EVALUATOR_ONCE",
    )
    live_evaluator_db_path: str = Field(
        default=".runtime/live_edge.db",
        alias="TRADECRAFT_LIVE_EVALUATOR_DB_PATH",
    )
    live_performance_db_path: str = Field(
        default=".runtime/live_performance.db",
        alias="TRADECRAFT_LIVE_PERFORMANCE_DB_PATH",
    )
    trading_validation_db_path: str = Field(
        default=".runtime/trading_validation.db",
        alias="TRADECRAFT_TRADING_VALIDATION_DB_PATH",
    )
    trading_validation_max_age_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_TRADING_VALIDATION_MAX_AGE_SEC",
    )
    trading_validation_payload_compaction_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACTION_ENABLED",
    )
    trading_validation_payload_recent_rows_per_group: int = Field(
        default=48,
        alias="TRADECRAFT_TRADING_VALIDATION_PAYLOAD_RECENT_ROWS_PER_GROUP",
    )
    trading_validation_payload_max_rows_per_group: int = Field(
        default=720,
        alias="TRADECRAFT_TRADING_VALIDATION_PAYLOAD_MAX_ROWS_PER_GROUP",
    )
    trading_validation_payload_compact_min_chars: int = Field(
        default=20_000,
        alias="TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACT_MIN_CHARS",
    )
    live_evaluator_state_path: str = Field(
        default=".runtime/live_evaluator.json",
        alias="TRADECRAFT_LIVE_EVALUATOR_STATE_PATH",
    )
    live_evaluator_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_LIVE_EVALUATOR_INTERVAL_SEC",
    )
    live_authority_max_scale_multiplier: float = Field(
        default=1.5,
        alias="TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER",
    )
    live_authority_min_samples_to_scale: int = Field(
        default=10,
        alias="TRADECRAFT_LIVE_AUTHORITY_MIN_SAMPLES_TO_SCALE",
    )
    binance_validation_spot_fee_rate: float = Field(
        default=0.001,
        alias="TRADECRAFT_BINANCE_VALIDATION_SPOT_FEE_RATE",
    )
    binance_validation_futures_fee_rate: float = Field(
        default=0.0005,
        alias="TRADECRAFT_BINANCE_VALIDATION_FUTURES_FEE_RATE",
    )
    binance_validation_slippage_bps: float = Field(
        default=2.0,
        alias="TRADECRAFT_BINANCE_VALIDATION_SLIPPAGE_BPS",
    )
    binance_validation_initial_equity_usdt: float = Field(
        default=1_000.0,
        alias="TRADECRAFT_BINANCE_VALIDATION_INITIAL_EQUITY_USDT",
    )
    kis_validation_buy_fee_rate: float = Field(
        default=0.00015,
        alias="TRADECRAFT_KIS_VALIDATION_BUY_FEE_RATE",
    )
    kis_validation_sell_fee_rate: float = Field(
        default=0.00015,
        alias="TRADECRAFT_KIS_VALIDATION_SELL_FEE_RATE",
    )
    kis_validation_sell_tax_rate: float = Field(
        default=0.002,
        alias="TRADECRAFT_KIS_VALIDATION_SELL_TAX_RATE",
    )
    kis_validation_slippage_bps: float = Field(
        default=5.0,
        alias="TRADECRAFT_KIS_VALIDATION_SLIPPAGE_BPS",
    )
    kis_validation_spread_bps: float = Field(
        default=0.0,
        alias="TRADECRAFT_KIS_VALIDATION_SPREAD_BPS",
    )
    kis_validation_initial_equity_krw: float = Field(
        default=4_000_000.0,
        alias="TRADECRAFT_KIS_VALIDATION_INITIAL_EQUITY_KRW",
    )
    watchdog_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_WATCHDOG_ENABLED",
    )
    watchdog_once: bool = Field(
        default=False,
        alias="TRADECRAFT_WATCHDOG_ONCE",
    )
    watchdog_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_WATCHDOG_INTERVAL_SEC",
    )
    watchdog_cooldown_sec: int = Field(
        default=300,
        alias="TRADECRAFT_WATCHDOG_COOLDOWN_SEC",
    )
    watchdog_flap_window_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_WATCHDOG_FLAP_WINDOW_SEC",
    )
    watchdog_max_restarts_per_window: int = Field(
        default=3,
        alias="TRADECRAFT_WATCHDOG_MAX_RESTARTS_PER_WINDOW",
    )
    watchdog_state_path: str = Field(
        default=".runtime/watchdog.json",
        alias="TRADECRAFT_WATCHDOG_STATE_PATH",
    )
    watchdog_db_path: str = Field(
        default=".runtime/watchdog_events.db",
        alias="TRADECRAFT_WATCHDOG_DB_PATH",
    )
    watchdog_runner_keys: str = Field(
        default="",
        alias="TRADECRAFT_WATCHDOG_RUNNER_KEYS",
    )
    daily_discovery_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_DAILY_DISCOVERY_ENABLED",
    )
    daily_discovery_db_path: str = Field(
        default=".runtime/jue_daily_discovery.db",
        alias="TRADECRAFT_DAILY_DISCOVERY_DB_PATH",
    )
    daily_discovery_kospi_count: int = Field(
        default=30,
        alias="TRADECRAFT_DAILY_DISCOVERY_KOSPI_COUNT",
    )
    daily_discovery_kosdaq_count: int = Field(
        default=30,
        alias="TRADECRAFT_DAILY_DISCOVERY_KOSDAQ_COUNT",
    )
    daily_discovery_exclude_recent_days: int = Field(
        default=10,
        alias="TRADECRAFT_DAILY_DISCOVERY_EXCLUDE_RECENT_DAYS",
    )
    daily_discovery_candidate_limit_per_market: int = Field(
        default=500,
        alias="TRADECRAFT_DAILY_DISCOVERY_CANDIDATE_LIMIT_PER_MARKET",
    )
    naver_reports_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_NAVER_REPORTS_ENABLED",
    )
    naver_reports_db_path: str = Field(
        default=".runtime/naver_reports.db",
        alias="TRADECRAFT_NAVER_REPORTS_DB_PATH",
    )
    naver_reports_seed_url: str = Field(
        default="https://finance.naver.com/research/company_list.naver",
        alias="TRADECRAFT_NAVER_REPORTS_SEED_URL",
    )
    naver_reports_seed_urls: str = Field(
        default=(
            "https://finance.naver.com/research/market_info_list.naver,"
            "https://finance.naver.com/research/invest_list.naver,"
            "https://finance.naver.com/research/company_list.naver,"
            "https://finance.naver.com/research/industry_list.naver,"
            "https://finance.naver.com/research/economy_list.naver,"
            "https://finance.naver.com/research/debenture_list.naver"
        ),
        alias="TRADECRAFT_NAVER_REPORTS_SEED_URLS",
    )
    naver_reports_interval_sec: int = Field(
        default=21600,
        alias="TRADECRAFT_NAVER_REPORTS_INTERVAL_SEC",
    )
    naver_reports_cycle_timeout_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_NAVER_REPORTS_CYCLE_TIMEOUT_SEC",
    )
    naver_reports_max_pages: int = Field(
        default=5,
        alias="TRADECRAFT_NAVER_REPORTS_MAX_PAGES",
    )
    naver_reports_since_date: str = Field(
        default="2025-06-04",
        alias="TRADECRAFT_NAVER_REPORTS_SINCE_DATE",
    )
    naver_reports_request_delay_sec: float = Field(
        default=1.8,
        alias="TRADECRAFT_NAVER_REPORTS_REQUEST_DELAY_SEC",
    )
    naver_reports_pdf_archive_dir: str = Field(
        default=".runtime/naver_reports/pdfs",
        alias="TRADECRAFT_NAVER_REPORTS_PDF_ARCHIVE_DIR",
    )
    naver_reports_min_pdf_text_chars: int = Field(
        default=240,
        alias="TRADECRAFT_NAVER_REPORTS_MIN_PDF_TEXT_CHARS",
    )
    naver_reports_llm_facts_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED",
    )
    rag_enabled: bool = Field(default=True, alias="TRADECRAFT_RAG_ENABLED")
    rag_persist_path: str = Field(
        default=".runtime/rag_chroma",
        alias="TRADECRAFT_RAG_PERSIST_PATH",
    )
    rag_collection_name: str = Field(
        default="naver_reports",
        alias="TRADECRAFT_RAG_COLLECTION_NAME",
    )
    rag_sync_chunk_limit: int = Field(
        default=50000,
        alias="TRADECRAFT_RAG_SYNC_CHUNK_LIMIT",
    )
    rag_sync_batch_size: int = Field(
        default=512,
        alias="TRADECRAFT_RAG_SYNC_BATCH_SIZE",
    )
    rag_skip_existing: bool = Field(
        default=True,
        alias="TRADECRAFT_RAG_SKIP_EXISTING",
    )
    rag_query_top_k: int = Field(
        default=8,
        alias="TRADECRAFT_RAG_QUERY_TOP_K",
    )
    rag_query_oversample_factor: int = Field(
        default=4,
        alias="TRADECRAFT_RAG_QUERY_OVERSAMPLE_FACTOR",
    )
    rag_allow_legacy_pickle_migration: bool = Field(
        default=False,
        alias="TRADECRAFT_RAG_ALLOW_LEGACY_PICKLE_MIGRATION",
    )
    market_intelligence_sources_json: str = Field(
        default=DEFAULT_MARKET_INTELLIGENCE_SOURCES_JSON,
        alias="TRADECRAFT_MARKET_INTELLIGENCE_SOURCES_JSON",
    )
    strategy_insight_sources_json: str = Field(
        default=DEFAULT_STRATEGY_INSIGHT_SOURCES_JSON,
        alias="TRADECRAFT_STRATEGY_INSIGHT_SOURCES_JSON",
    )
    strategy_insight_collect_interval_sec: int = Field(
        default=900,
        alias="TRADECRAFT_STRATEGY_INSIGHT_COLLECT_INTERVAL_SEC",
    )
    strategy_insight_error_backoff_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_STRATEGY_INSIGHT_ERROR_BACKOFF_SEC",
    )
    strategy_insight_request_timeout_sec: float = Field(
        default=10.0,
        alias="TRADECRAFT_STRATEGY_INSIGHT_REQUEST_TIMEOUT_SEC",
    )
    strategy_insight_once: bool = Field(
        default=False,
        alias="TRADECRAFT_STRATEGY_INSIGHT_ONCE",
    )
    strategy_insight_state_path: str = Field(
        default=".runtime/strategy_insights_runner.json",
        alias="TRADECRAFT_STRATEGY_INSIGHT_STATE_PATH",
    )
    strategy_insight_db_path: str = Field(
        default=".runtime/strategy_insights.db",
        alias="TRADECRAFT_STRATEGY_INSIGHT_DB_PATH",
    )
    strategy_insight_retention_days: int = Field(
        default=45,
        alias="TRADECRAFT_STRATEGY_INSIGHT_RETENTION_DAYS",
    )
    strategy_insight_signal_row_cap_per_symbol: int = Field(
        default=96,
        alias="TRADECRAFT_STRATEGY_INSIGHT_SIGNAL_ROW_CAP_PER_SYMBOL",
    )
    strategy_insight_sidecar_max_lines: int = Field(
        default=500,
        alias="TRADECRAFT_STRATEGY_INSIGHT_SIDECAR_MAX_LINES",
    )
    strategy_insight_migrate_legacy_jsonl: bool = Field(
        default=False,
        alias="TRADECRAFT_STRATEGY_INSIGHT_MIGRATE_LEGACY_JSONL",
    )
    valuation_db_path: str = Field(
        default=".runtime/symbol_fundamentals.db",
        alias="TRADECRAFT_VALUATION_DB_PATH",
    )
    valuation_watchlist: str = Field(
        default="005930,000660,402340,178920",
        alias="TRADECRAFT_VALUATION_WATCHLIST",
    )
    valuation_timeout_sec: float = Field(
        default=8.0,
        alias="TRADECRAFT_VALUATION_TIMEOUT_SEC",
    )
    valuation_min_refresh_hours: int = Field(
        default=12,
        alias="TRADECRAFT_VALUATION_MIN_REFRESH_HOURS",
    )
    valuation_max_symbols_per_collect: int = Field(
        default=80,
        alias="TRADECRAFT_VALUATION_MAX_SYMBOLS_PER_COLLECT",
    )
    valuation_auto_collect: bool = Field(
        default=True,
        alias="TRADECRAFT_VALUATION_AUTO_COLLECT",
    )
    valuation_auto_min_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_VALUATION_AUTO_MIN_INTERVAL_SEC",
    )
    valuation_auto_max_symbols: int = Field(
        default=8,
        alias="TRADECRAFT_VALUATION_AUTO_MAX_SYMBOLS",
    )
    market_judge_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_MARKET_JUDGE_ENABLED",
    )
    market_judge_once: bool = Field(
        default=False,
        alias="TRADECRAFT_MARKET_JUDGE_ONCE",
    )
    market_judge_db_path: str = Field(
        default=".runtime/market_judgment.db",
        alias="TRADECRAFT_MARKET_JUDGE_DB_PATH",
    )
    market_judge_state_path: str = Field(
        default=".runtime/market_judge.json",
        alias="TRADECRAFT_MARKET_JUDGE_STATE_PATH",
    )
    market_quote_interval_sec: int = Field(
        default=60,
        alias="TRADECRAFT_MARKET_QUOTE_INTERVAL_SEC",
    )
    market_judge_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_MARKET_JUDGE_INTERVAL_SEC",
    )
    market_judge_quote_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_MARKET_JUDGE_QUOTE_RETENTION_DAYS",
    )
    market_judge_quote_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_MARKET_JUDGE_QUOTE_ARCHIVE_RETENTION_DAYS",
    )
    market_judge_account_retention_days: int = Field(
        default=30,
        alias="TRADECRAFT_MARKET_JUDGE_ACCOUNT_RETENTION_DAYS",
    )
    market_judge_judgment_retention_days: int = Field(
        default=30,
        alias="TRADECRAFT_MARKET_JUDGE_JUDGMENT_RETENTION_DAYS",
    )
    market_judge_judgment_archive_retention_days: int = Field(
        default=30,
        alias="TRADECRAFT_MARKET_JUDGE_JUDGMENT_ARCHIVE_RETENTION_DAYS",
    )
    market_judge_compact_recent_run_count: int = Field(
        default=48,
        alias="TRADECRAFT_MARKET_JUDGE_COMPACT_RECENT_RUN_COUNT",
    )
    market_judge_compact_min_chars: int = Field(
        default=20_000,
        alias="TRADECRAFT_MARKET_JUDGE_COMPACT_MIN_CHARS",
    )
    market_judge_compact_symbol_min_chars: int = Field(
        default=2_000,
        alias="TRADECRAFT_MARKET_JUDGE_COMPACT_SYMBOL_MIN_CHARS",
    )
    market_judge_max_symbols: int = Field(
        default=60,
        alias="TRADECRAFT_MARKET_JUDGE_MAX_SYMBOLS",
    )
    market_judge_llm_max_symbols: int = Field(
        default=60,
        alias="TRADECRAFT_MARKET_JUDGE_LLM_MAX_SYMBOLS",
    )
    market_judge_use_naver_fallback: bool = Field(
        default=False,
        alias="TRADECRAFT_MARKET_JUDGE_USE_NAVER_FALLBACK",
    )
    market_judge_query: str = Field(
        default="장중 현재 움직임과 내 국장1 계좌를 반영해 관심/보류 판단을 정리해줘",
        alias="TRADECRAFT_MARKET_JUDGE_QUERY",
    )
    market_pulse_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_MARKET_PULSE_ENABLED",
    )
    market_pulse_once: bool = Field(
        default=False,
        alias="TRADECRAFT_MARKET_PULSE_ONCE",
    )
    market_pulse_db_path: str = Field(
        default=".runtime/market_pulse.db",
        alias="TRADECRAFT_MARKET_PULSE_DB_PATH",
    )
    market_pulse_state_path: str = Field(
        default=".runtime/market_pulse.json",
        alias="TRADECRAFT_MARKET_PULSE_STATE_PATH",
    )
    market_pulse_interval_sec: int = Field(
        default=60,
        alias="TRADECRAFT_MARKET_PULSE_INTERVAL_SEC",
    )
    market_pulse_closed_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_MARKET_PULSE_CLOSED_INTERVAL_SEC",
    )
    market_pulse_retention_days: int = Field(
        default=3,
        alias="TRADECRAFT_MARKET_PULSE_RETENTION_DAYS",
    )
    market_pulse_archive_retention_days: int = Field(
        default=7,
        alias="TRADECRAFT_MARKET_PULSE_ARCHIVE_RETENTION_DAYS",
    )
    market_pulse_timeout_sec: float = Field(
        default=8.0,
        alias="TRADECRAFT_MARKET_PULSE_TIMEOUT_SEC",
    )
    market_pulse_index_codes: str = Field(
        default="KOSPI,KOSDAQ,KPI200,FUT",
        alias="TRADECRAFT_MARKET_PULSE_INDEX_CODES",
    )
    market_pulse_sector_signal_limit: int = Field(
        default=240,
        alias="TRADECRAFT_MARKET_PULSE_SECTOR_SIGNAL_LIMIT",
    )
    market_pulse_investor_flow_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_MARKET_PULSE_INVESTOR_FLOW_ENABLED",
    )
    market_pulse_investor_flow_markets: str = Field(
        default="KOSPI,KOSDAQ,FUT",
        alias="TRADECRAFT_MARKET_PULSE_INVESTOR_FLOW_MARKETS",
    )
    market_pulse_program_trading_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_MARKET_PULSE_PROGRAM_TRADING_ENABLED",
    )
    market_pulse_program_trading_markets: str = Field(
        default="KOSPI,KOSDAQ",
        alias="TRADECRAFT_MARKET_PULSE_PROGRAM_TRADING_MARKETS",
    )
    market_pulse_fx_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_MARKET_PULSE_FX_ENABLED",
    )
    host: str = Field(default="127.0.0.1", alias="TRADECRAFT_HOST")
    port: int = Field(default=8000, alias="TRADECRAFT_PORT")
    allow_origins: str = Field(
        default=(
            "http://127.0.0.1:8000,http://localhost:8000,"
            "http://127.0.0.1:18080,http://localhost:18080,"
            "http://127.0.0.1:8010,http://localhost:8010"
        ),
        alias="TRADECRAFT_ALLOW_ORIGINS",
    )
    reports_api_host: str = Field(
        default="127.0.0.1",
        alias="TRADECRAFT_REPORTS_API_HOST",
    )
    reports_api_port: int = Field(
        default=8010,
        alias="TRADECRAFT_REPORTS_API_PORT",
    )
    reports_api_token: str = Field(
        default="",
        alias="TRADECRAFT_REPORTS_API_TOKEN",
    )
    reports_api_tokens: str = Field(
        default="",
        alias="TRADECRAFT_REPORTS_API_TOKENS",
    )
    reports_ui_allowed_cidrs: str = Field(
        default=(
            "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,"
            "192.168.0.0/16,::1/128"
        ),
        alias="TRADECRAFT_REPORTS_UI_ALLOWED_CIDRS",
    )
    reports_ui_trust_proxy: bool = Field(
        default=False,
        alias="TRADECRAFT_REPORTS_UI_TRUST_PROXY",
    )
    reports_worker_state_path: str = Field(
        default=".runtime/reports_worker_state.json",
        alias="TRADECRAFT_REPORTS_WORKER_STATE_PATH",
    )

    @property
    def cors_origins(self) -> list[str]:
        value = self.allow_origins.strip()
        if value == "*":
            return ["*"]
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def admin_token_list(self) -> list[str]:
        out: list[str] = []
        for item in str(self.admin_tokens or "").split(","):
            token = item.strip()
            if token:
                out.append(token)
        legacy = str(self.admin_token or "").strip()
        if legacy:
            out.append(legacy)
        return list(dict.fromkeys(out))

    @property
    def upbit_ready(self) -> bool:
        return bool(self.upbit_access_key and self.upbit_secret_key)

    @property
    def bithumb_ready(self) -> bool:
        return bool(self.bithumb_access_key and self.bithumb_secret_key)

    @property
    def binance_spot_ready(self) -> bool:
        return bool(self.binance_spot_api_key and self.binance_spot_api_secret)

    @property
    def binance_futures_key_resolved(self) -> str:
        return self.binance_futures_api_key or self.binance_spot_api_key

    @property
    def binance_futures_secret_resolved(self) -> str:
        return self.binance_futures_api_secret or self.binance_spot_api_secret

    @property
    def binance_futures_ready(self) -> bool:
        return bool(
            self.binance_futures_key_resolved and self.binance_futures_secret_resolved
        )

    @property
    def kis_primary_ready(self) -> bool:
        return bool(
            self.kis_primary_app_key
            and self.kis_primary_app_secret
            and self.kis_primary_account_no
            and self.kis_primary_product_code
        )

    @property
    def kis_secondary_ready(self) -> bool:
        return bool(
            self.kis_secondary_app_key
            and self.kis_secondary_app_secret
            and self.kis_secondary_account_no
            and self.kis_secondary_product_code
        )

    @property
    def research_report_url_list(self) -> list[str]:
        return [
            item.strip()
            for item in self.research_report_urls.split(",")
            if item.strip()
        ]

    @property
    def codex_runtime_mode(self) -> str:
        requested = str(self.codex_runtime_mode_preference or "auto").strip().lower()
        if requested in {"none", "off", "disabled"}:
            return "none"
        return "sdk"

    @property
    def codex_runtime_ready(self) -> bool:
        return self.codex_runtime_mode == "sdk"

    @property
    def naver_reports_llm_facts_active(self) -> bool:
        return bool(self.naver_reports_llm_facts_enabled and self.codex_runtime_ready)

    @property
    def naver_reports_seed_url_list(self) -> list[str]:
        value = self.naver_reports_seed_urls.strip()
        if value:
            return [item.strip() for item in value.split(",") if item.strip()]
        single = self.naver_reports_seed_url.strip()
        return [single] if single else []

    @property
    def market_intelligence_source_list(self) -> list[dict[str, str | list[str]]]:
        raw = str(self.market_intelligence_sources_json or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []

        out: list[dict[str, str | list[str]]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or "").strip()
            label = str(row.get("label") or source_id).strip()
            if not source_id or not label:
                continue
            coverage = [
                str(item).strip()
                for item in list(row.get("coverage") or [])
                if str(item).strip()
            ]
            signal_types = [
                str(item).strip()
                for item in list(row.get("signal_types") or [])
                if str(item).strip()
            ]
            out.append(
                {
                    "source_id": source_id,
                    "label": label,
                    "role": str(row.get("role") or "").strip(),
                    "coverage": coverage,
                    "signal_types": signal_types,
                    "caution": str(row.get("caution") or "").strip(),
                }
            )
        return out

    @property
    def strategy_insight_source_list(self) -> list[dict[str, Any]]:
        raw = str(self.strategy_insight_sources_json or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            return []

        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or row.get("id") or "").strip()
            if not source_id:
                continue
            item: dict[str, Any] = {
                "source_id": source_id,
                "label": str(row.get("label") or source_id).strip(),
                "enabled": row.get("enabled", True),
                "dedupe": row.get("dedupe", True),
            }
            for key in (
                "path",
                "url",
                "text",
                "payload",
                "signals",
                "kind",
                "cache_path",
                "symbol_cache_path",
                "symbol_search_url",
                "limit",
            ):
                if key in row:
                    item[key] = row[key]
            if any(
                key in item
                for key in (
                    "path",
                    "url",
                    "text",
                    "payload",
                    "signals",
                    "kind",
                )
            ):
                out.append(item)
        return out

    @property
    def reports_ui_allowed_cidr_list(self) -> list[str]:
        return [
            item.strip()
            for item in str(self.reports_ui_allowed_cidrs or "").split(",")
            if item.strip()
        ]

    @property
    def reports_api_token_list(self) -> list[str]:
        out: list[str] = []
        for item in str(self.reports_api_tokens or "").split(","):
            token = item.strip()
            if token and token not in out:
                out.append(token)
        legacy = str(self.reports_api_token or "").strip()
        if legacy and legacy not in out:
            out.append(legacy)
        return out
