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
    bithumb_base_url: str = Field(default="https://api.bithumb.com", alias="BITHUMB_BASE_URL")
    binance_spot_api_key: str = Field(default="", alias="BINANCE_SPOT_API_KEY")
    binance_spot_api_secret: str = Field(default="", alias="BINANCE_SPOT_API_SECRET")
    binance_spot_base_url: str = Field(default="https://api.binance.com", alias="BINANCE_SPOT_BASE_URL")
    binance_futures_api_key: str = Field(default="", alias="BINANCE_FUTURES_API_KEY")
    binance_futures_api_secret: str = Field(default="", alias="BINANCE_FUTURES_API_SECRET")
    binance_futures_base_url: str = Field(default="https://fapi.binance.com", alias="BINANCE_FUTURES_BASE_URL")
    binance_usdt_krw: float = Field(default=1387.0, alias="BINANCE_USDT_KRW")
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
    kis_secondary_product_code: str = Field(default="", alias="KIS_SECONDARY_PRODUCT_CODE")
    runtime_state_path: str = Field(default=".runtime/state.json", alias="TRADECRAFT_RUNTIME_STATE_PATH")
    runtime_max_age_sec: int = Field(default=90, alias="TRADECRAFT_RUNTIME_MAX_AGE_SEC")
    runtime_write_interval_sec: int = Field(default=5, alias="TRADECRAFT_RUNTIME_WRITE_INTERVAL_SEC")
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
        return bool(self.binance_futures_key_resolved and self.binance_futures_secret_resolved)

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
