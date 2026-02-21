from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    runtime_max_age_sec: int = Field(default=90, alias="TRADECRAFT_RUNTIME_MAX_AGE_SEC")
    runtime_write_interval_sec: int = Field(
        default=5, alias="TRADECRAFT_RUNTIME_WRITE_INTERVAL_SEC"
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
        default="gpt-5.3-codex",
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
    portfolio_coach_review_queue_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_PORTFOLIO_COACH_REVIEW_QUEUE_ENABLED",
    )
    kis_trader_enabled: bool = Field(
        default=False, alias="TRADECRAFT_KIS_TRADER_ENABLED"
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
    rag_query_top_k: int = Field(
        default=8,
        alias="TRADECRAFT_RAG_QUERY_TOP_K",
    )
    kis_trader_report_context_top_k: int = Field(
        default=6,
        alias="TRADECRAFT_KIS_TRADER_REPORT_CONTEXT_TOP_K",
    )
    freqtrade_spot_api_url: str = Field(default="", alias="FREQTRADE_SPOT_API_URL")
    freqtrade_spot_username: str = Field(default="", alias="FREQTRADE_SPOT_USERNAME")
    freqtrade_spot_password: str = Field(default="", alias="FREQTRADE_SPOT_PASSWORD")
    freqtrade_spot_config_path: str = Field(
        default="third_party/freqtrade/user_data/config_jurobot.json",
        alias="FREQTRADE_SPOT_CONFIG_PATH",
    )
    freqtrade_futures_api_url: str = Field(
        default="", alias="FREQTRADE_FUTURES_API_URL"
    )
    freqtrade_futures_username: str = Field(
        default="", alias="FREQTRADE_FUTURES_USERNAME"
    )
    freqtrade_futures_password: str = Field(
        default="", alias="FREQTRADE_FUTURES_PASSWORD"
    )
    freqtrade_futures_config_path: str = Field(
        default="third_party/freqtrade/user_data/config_jurobot_futures.json",
        alias="FREQTRADE_FUTURES_CONFIG_PATH",
    )
    freqtrade_bot_api_urls: str = Field(default="", alias="FREQTRADE_BOT_API_URLS")
    freqtrade_executable_path: str = Field(
        default="third_party/freqtrade/.venv-ft/bin/freqtrade",
        alias="FREQTRADE_EXECUTABLE_PATH",
    )
    freqtrade_workdir: str = Field(
        default="third_party/freqtrade",
        alias="FREQTRADE_WORKDIR",
    )
    freqtrade_runtime_dir: str = Field(
        default=".runtime/freqtrade",
        alias="FREQTRADE_RUNTIME_DIR",
    )
    freqtrade_stop_timeout_sec: float = Field(
        default=8.0,
        alias="FREQTRADE_STOP_TIMEOUT_SEC",
    )
    freqtrade_timeout_sec: float = Field(default=3.5, alias="FREQTRADE_TIMEOUT_SEC")
    host: str = Field(default="0.0.0.0", alias="TRADECRAFT_HOST")
    port: int = Field(default=8000, alias="TRADECRAFT_PORT")
    allow_origins: str = Field(default="*", alias="TRADECRAFT_ALLOW_ORIGINS")

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
    def freqtrade_bot_api_url_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        raw = self.freqtrade_bot_api_urls.strip()
        if not raw:
            return out

        for chunk in raw.split(","):
            item = chunk.strip()
            if not item or "=" not in item:
                continue
            bot_id, api_url = item.split("=", 1)
            key = bot_id.strip()
            value = api_url.strip().rstrip("/")
            if key and value:
                out[key] = value
        return out

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
    def naver_reports_seed_url_list(self) -> list[str]:
        value = self.naver_reports_seed_urls.strip()
        if value:
            return [item.strip() for item in value.split(",") if item.strip()]
        single = self.naver_reports_seed_url.strip()
        return [single] if single else []
