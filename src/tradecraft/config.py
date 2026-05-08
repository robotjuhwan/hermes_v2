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
            "caution": "매매 추천이 아니라 당일 시장 분위기와 후보군 보조 신호",
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
            "symbol_search_url": "https://www.sesiban.site/api/v1/assets",
            "limit": 40,
            "enabled": True,
        },
        {
            "source_id": "after_close_330",
            "label": "세시반 공개 JSON",
            "kind": "sesiban_leading",
            "url": "https://www.sesiban.site/api/v1/rankings/leading?market=KR",
            "cache_path": ".runtime/cache/sesiban_public_signals.json",
            "limit": 40,
            "enabled": True,
        },
    ],
    ensure_ascii=False,
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
    runtime_state_path: str = Field(
        default=".runtime/state.json", alias="TRADECRAFT_RUNTIME_STATE_PATH"
    )
    runtime_sessions_path: str = Field(
        default="", alias="TRADECRAFT_RUNTIME_SESSIONS_PATH"
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
    research_state_path: str = Field(
        default=".runtime/research.json", alias="TRADECRAFT_RESEARCH_STATE_PATH"
    )
    research_max_age_sec: int = Field(
        default=3600, alias="TRADECRAFT_RESEARCH_MAX_AGE_SEC"
    )
    research_enabled: bool = Field(default=True, alias="TRADECRAFT_RESEARCH_ENABLED")
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
    llm_bridge_command: str = Field(
        default="",
        alias="TRADECRAFT_LLM_BRIDGE_COMMAND",
    )
    llm_bridge_args: str = Field(
        default="",
        alias="TRADECRAFT_LLM_BRIDGE_ARGS",
    )
    llm_bridge_url: str = Field(
        default="",
        alias="TRADECRAFT_LLM_BRIDGE_URL",
    )
    llm_bridge_token: str = Field(
        default="",
        alias="TRADECRAFT_LLM_BRIDGE_TOKEN",
    )
    llm_bridge_timeout_ms: int = Field(
        default=60000,
        alias="TRADECRAFT_LLM_BRIDGE_TIMEOUT_MS",
    )
    llm_model: str = Field(
        default="gpt-5.5",
        alias="TRADECRAFT_LLM_MODEL",
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
    kis_trader_enabled: bool = Field(
        default=False, alias="TRADECRAFT_KIS_TRADER_ENABLED"
    )
    kis_trader_execute_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_KIS_TRADER_EXECUTE_ORDERS",
    )
    kis_trader_state_path: str = Field(
        default=".runtime/kis_trader.json",
        alias="TRADECRAFT_KIS_TRADER_STATE_PATH",
    )
    kis_trader_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_KIS_TRADER_INTERVAL_SEC",
    )
    kis_trader_llm_command: str = Field(
        default="",
        alias="TRADECRAFT_KIS_TRADER_LLM_COMMAND",
    )
    kis_trader_persona: str = Field(
        default="너는 한국 주식시장 초고수 트레이더다. 거시/수급/이슈를 종합해 보수적으로 판단한다.",
        alias="TRADECRAFT_KIS_TRADER_PERSONA",
    )
    kis_trader_max_orders_per_cycle: int = Field(
        default=2,
        alias="TRADECRAFT_KIS_TRADER_MAX_ORDERS_PER_CYCLE",
    )
    kis_trader_max_budget_per_order_krw: float = Field(
        default=500000.0,
        alias="TRADECRAFT_KIS_TRADER_MAX_BUDGET_PER_ORDER_KRW",
    )
    kis_trader_min_confidence: float = Field(
        default=0.7,
        alias="TRADECRAFT_KIS_TRADER_MIN_CONFIDENCE",
    )
    kis_trader_default_order_type: str = Field(
        default="01",
        alias="TRADECRAFT_KIS_TRADER_DEFAULT_ORDER_TYPE",
    )
    kis_trader_allow_sell: bool = Field(
        default=True,
        alias="TRADECRAFT_KIS_TRADER_ALLOW_SELL",
    )
    kis_trader_max_candidate_codes: int = Field(
        default=10,
        alias="TRADECRAFT_KIS_TRADER_MAX_CANDIDATE_CODES",
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
        default=5,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_RULE_INTERVAL_SEC",
    )
    kis_block_trader_manager_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_INTERVAL_SEC",
    )
    kis_block_trader_aggressive_limit_bps: float = Field(
        default=30.0,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_AGGRESSIVE_LIMIT_BPS",
    )
    kis_block_trader_pending_reconcile_timeout_sec: int = Field(
        default=300,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_PENDING_RECONCILE_TIMEOUT_SEC",
    )
    kis_block_trader_max_manager_symbols: int = Field(
        default=12,
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MAX_MANAGER_SYMBOLS",
    )
    kis_block_trader_manager_query: str = Field(
        default="국장1 계좌와 전략 지식을 바탕으로 블록 매매 계획을 관리해줘",
        alias="TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_QUERY",
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
    kis_trader_report_context_top_k: int = Field(
        default=6,
        alias="TRADECRAFT_KIS_TRADER_REPORT_CONTEXT_TOP_K",
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
        default=600,
        alias="TRADECRAFT_MARKET_JUDGE_INTERVAL_SEC",
    )
    market_judge_max_symbols: int = Field(
        default=60,
        alias="TRADECRAFT_MARKET_JUDGE_MAX_SYMBOLS",
    )
    market_judge_llm_max_symbols: int = Field(
        default=12,
        alias="TRADECRAFT_MARKET_JUDGE_LLM_MAX_SYMBOLS",
    )
    market_judge_use_naver_fallback: bool = Field(
        default=True,
        alias="TRADECRAFT_MARKET_JUDGE_USE_NAVER_FALLBACK",
    )
    market_judge_query: str = Field(
        default="장중 현재 움직임과 내 국장1 계좌를 반영해 관심/보류 판단을 정리해줘",
        alias="TRADECRAFT_MARKET_JUDGE_QUERY",
    )
    host: str = Field(default="127.0.0.1", alias="TRADECRAFT_HOST")
    port: int = Field(default=8000, alias="TRADECRAFT_PORT")
    allow_origins: str = Field(default="*", alias="TRADECRAFT_ALLOW_ORIGINS")
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
    def llm_bridge_mode(self) -> str:
        if self.llm_bridge_command.strip():
            return "command"
        if self.llm_bridge_url.strip():
            return "url"
        return "none"

    @property
    def llm_bridge_ready(self) -> bool:
        return self.llm_bridge_mode in {"command", "url"}

    @property
    def naver_reports_llm_facts_active(self) -> bool:
        return bool(self.naver_reports_llm_facts_enabled and self.llm_bridge_ready)

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
