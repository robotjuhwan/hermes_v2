import json
from pathlib import Path

import pytest

from tradecraft.config import AppSettings


JUE_CODEX_LAB_ENV_KEYS = (
    "TRADECRAFT_JUE_CODEX_LAB_ENABLED",
    "TRADECRAFT_JUE_CODEX_LAB_INTERVAL_SEC",
    "TRADECRAFT_JUE_CODEX_LAB_AUTONOMY_MODE",
    "TRADECRAFT_JUE_CODEX_LAB_DB_PATH",
    "TRADECRAFT_JUE_CODEX_LAB_MAX_PATCH_BYTES",
    "TRADECRAFT_JUE_CODEX_LAB_ALLOWED_PATHS",
    "TRADECRAFT_JUE_CODEX_LAB_BLOCKED_PATHS",
    "TRADECRAFT_JUE_CODEX_LAB_MAX_TASKS_PER_CYCLE",
    "TRADECRAFT_JUE_CODEX_LAB_MARKET_HOURS_HOT_DEPLOY",
)


def _csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _accepted_tradecraft_env_keys() -> set[str]:
    keys: set[str] = set()
    for field in AppSettings.model_fields.values():
        for source in (field.alias, field.validation_alias):
            if source is None:
                continue
            if isinstance(source, str):
                keys.add(source)
                continue
            for choice in getattr(source, "choices", []) or []:
                if isinstance(choice, str):
                    keys.add(choice)
    return keys


def _normalize_env_default(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return ",".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _env_defaults_match(example_value: str, actual_value: object, *, alias: str) -> bool:
    actual = _normalize_env_default(actual_value)
    if alias == "TRADECRAFT_ALLOW_ORIGINS":
        return {
            item.strip() for item in example_value.split(",") if item.strip()
        } == {item.strip() for item in actual.split(",") if item.strip()}
    if isinstance(actual_value, (int, float)) and not isinstance(actual_value, bool):
        try:
            return float(example_value) == pytest.approx(float(actual_value))
        except ValueError:
            return False
    return example_value == actual


def test_env_example_values_match_app_settings_defaults(monkeypatch) -> None:
    for field in AppSettings.model_fields.values():
        alias = str(field.alias or "")
        if alias.startswith("TRADECRAFT_"):
            monkeypatch.delenv(alias, raising=False)

    settings = AppSettings(_env_file=None)
    examples = _env_example_values()
    mismatches: list[str] = []

    for name, field in AppSettings.model_fields.items():
        alias = str(field.alias or "")
        if not alias.startswith("TRADECRAFT_") or alias not in examples:
            continue
        if not _env_defaults_match(examples[alias], getattr(settings, name), alias=alias):
            mismatches.append(
                f"{alias}: example={examples[alias]!r} default={getattr(settings, name)!r}"
            )

    assert not mismatches


def test_env_example_tradecraft_keys_are_known_and_unique() -> None:
    accepted = _accepted_tradecraft_env_keys()
    seen: set[str] = set()
    duplicates: list[str] = []
    unknown: list[str] = []

    for raw_line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        if key.startswith("TRADECRAFT_") and key not in accepted:
            unknown.append(key)

    assert not duplicates
    assert not unknown


def test_naver_reports_seed_url_list_parses_csv(monkeypatch) -> None:
    monkeypatch.setenv(
        "TRADECRAFT_NAVER_REPORTS_SEED_URLS",
        "https://finance.naver.com/research/market_info_list.naver, https://finance.naver.com/research/company_list.naver",
    )

    settings = AppSettings(_env_file=None)

    assert settings.naver_reports_seed_url_list == [
        "https://finance.naver.com/research/market_info_list.naver",
        "https://finance.naver.com/research/company_list.naver",
    ]


def test_runtime_sessions_path_defaults_to_bundled_sessions(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_RUNTIME_SESSIONS_PATH", "")

    settings = AppSettings(_env_file=None)

    assert settings.runtime_sessions_path == ""


def test_backtest_runtime_defaults_and_env_aliases(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_BACKTEST_CYCLES", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_STEP_SEC", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_SPEED", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_INITIAL_PRICE", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_VOLATILITY_BPS", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_DRIFT_BPS", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_FEE_RATE", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_SLIPPAGE_BPS", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_SEED", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_STATE_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_RESULT_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_DATA_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_MAX_CURVE_POINTS", raising=False)
    monkeypatch.delenv("TRADECRAFT_BACKTEST_EMIT_INTERVAL", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.backtest_cycles == 720
    assert settings.backtest_step_sec == 60
    assert settings.backtest_speed == 120.0
    assert settings.backtest_initial_price == 100_000_000.0
    assert settings.backtest_volatility_bps == 18.0
    assert settings.backtest_drift_bps == 0.2
    assert settings.backtest_fee_rate == 0.0005
    assert settings.backtest_slippage_bps == 1.0
    assert settings.backtest_seed == 7
    assert settings.backtest_state_path == ".runtime/backtest_live.json"
    assert settings.backtest_result_path == ".runtime/backtest_result.json"
    assert settings.backtest_data_registry_path == ".runtime/backtest_data_registry.json"
    assert settings.backtest_max_curve_points == 4000
    assert settings.backtest_emit_interval == 1

    monkeypatch.setenv("TRADECRAFT_BACKTEST_CYCLES", "12")
    monkeypatch.setenv("TRADECRAFT_BACKTEST_RESULT_PATH", ".runtime/custom_bt.json")
    env_settings = AppSettings(_env_file=None)

    assert env_settings.backtest_cycles == 12
    assert env_settings.backtest_result_path == ".runtime/custom_bt.json"


def test_runtime_storage_pdf_cleanup_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_PRUNE_EXTRACTED_REPORT_PDFS", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_EXTRACTED_REPORT_PDF_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_PRUNE_RAG_REPAIR_ARTIFACTS", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_RAG_REPAIR_ARTIFACT_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_PRUNE_RAG_REBUILD_BACKUPS", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_RAG_REBUILD_BACKUP_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_RUNTIME_LOGS", raising=False)
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_ROTATE_LARGE_ACTIVE_LOGS", raising=False)
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_ACTIVE_LOG_MAX_MB", raising=False)
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_ACTIVE_LOG_TAIL_KB", raising=False)
    monkeypatch.delenv("TRADECRAFT_RUNTIME_STORAGE_PRUNE_SCRATCH_ARTIFACTS", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_SCRATCH_ARTIFACT_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_BACKTEST_ARTIFACTS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_BACKTEST_ARTIFACT_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_PRUNE_OLD_UI_CHECK_ARTIFACTS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_UI_CHECK_ARTIFACT_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_PRUNE_ZERO_BYTE_RUNTIME_MARKERS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_ZERO_BYTE_MARKER_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_DATABASE_COMPACT_MIN_FREE_MB",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_RUNTIME_STORAGE_DATABASE_COMPACT_MIN_FREE_RATIO_PCT",
        raising=False,
    )

    settings = AppSettings(_env_file=None)

    assert settings.runtime_storage_prune_unreferenced_pdfs is True
    assert settings.runtime_storage_prune_extracted_report_pdfs is True
    assert settings.runtime_storage_extracted_report_pdf_retention_days == 14
    assert settings.runtime_storage_prune_rag_repair_artifacts is True
    assert settings.runtime_storage_rag_repair_artifact_retention_days == 7
    assert settings.runtime_storage_prune_rag_rebuild_backups is True
    assert settings.runtime_storage_rag_rebuild_backup_retention_days == 7
    assert settings.runtime_storage_prune_old_runtime_logs is True
    assert settings.runtime_storage_log_retention_days == 7
    assert settings.runtime_storage_rotate_large_active_logs is True
    assert settings.runtime_storage_active_log_max_mb == 16
    assert settings.runtime_storage_active_log_tail_kb == 2048
    assert settings.runtime_storage_prune_scratch_artifacts is True
    assert settings.runtime_storage_scratch_artifact_retention_days == 7
    assert settings.runtime_storage_prune_old_backtest_artifacts is True
    assert settings.runtime_storage_backtest_artifact_retention_days == 30
    assert settings.runtime_storage_prune_old_ui_check_artifacts is True
    assert settings.runtime_storage_ui_check_artifact_retention_days == 30
    assert settings.runtime_storage_prune_zero_byte_runtime_markers is True
    assert settings.runtime_storage_zero_byte_marker_retention_days == 7
    assert settings.runtime_storage_database_compact_min_free_mb == 4
    assert settings.runtime_storage_database_compact_min_free_ratio_pct == 10.0


def test_crypto_archive_retention_defaults(monkeypatch) -> None:
    monkeypatch.delenv(
        "TRADECRAFT_CRYPTO_MARKET_RESEARCH_ARCHIVE_RETENTION_DAYS",
        raising=False,
    )
    monkeypatch.delenv("TRADECRAFT_CRYPTO_QUANT_ARCHIVE_RETENTION_DAYS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.crypto_market_research_archive_retention_days == 7
    assert settings.crypto_quant_archive_retention_days == 7


def test_etf_research_retention_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_ETF_RESEARCH_RETENTION_DAYS", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_ETF_RESEARCH_ARCHIVE_RETENTION_DAYS",
        raising=False,
    )

    settings = AppSettings(_env_file=None)

    assert settings.etf_research_retention_days == 3
    assert settings.etf_research_archive_retention_days == 7


def test_codex_runtime_mode_defaults_to_native_sdk(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CODEX_NATIVE_MODE", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.codex_runtime_mode == "sdk"
    assert settings.codex_runtime_ready is True


def test_codex_native_timeout_default_allows_xhigh_judgments(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CODEX_NATIVE_TIMEOUT_MS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.codex_runtime_timeout_ms == 600000


def test_codex_runtime_mode_can_select_native_sdk(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODE", "sdk")
    monkeypatch.setenv(
        "TRADECRAFT_CODEX_NATIVE_BIN",
        "/Applications/Codex.app/Contents/Resources/codex",
    )

    settings = AppSettings(_env_file=None)

    assert settings.codex_runtime_mode == "sdk"
    assert settings.codex_runtime_ready is True
    assert settings.codex_runtime_sdk_codex_bin == "/Applications/Codex.app/Contents/Resources/codex"


def test_codex_runtime_mode_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODE", "none")

    settings = AppSettings(_env_file=None)

    assert settings.codex_runtime_mode == "none"
    assert settings.codex_runtime_ready is False


def test_codex_native_thread_settings(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_THREAD_MODE", "daily")
    monkeypatch.setenv(
        "TRADECRAFT_CODEX_NATIVE_THREAD_DB_PATH",
        ".runtime/test_threads.db",
    )
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS", "7")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_READ_TURNS", "true")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_ACCOUNT_CHECK_INTERVAL_SEC", "120")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODEL_CHECK_INTERVAL_SEC", "300")

    settings = AppSettings(_env_file=None)

    assert settings.codex_native_thread_mode == "daily"
    assert settings.codex_native_thread_db_path == ".runtime/test_threads.db"
    assert settings.codex_native_compact_after_turns == 7
    assert settings.codex_native_read_turns is True
    assert settings.codex_native_account_check_interval_sec == 120
    assert settings.codex_native_model_check_interval_sec == 300


def test_jue_codex_lab_settings_defaults(monkeypatch) -> None:
    for key in JUE_CODEX_LAB_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.jue_codex_lab_enabled is False
    assert settings.jue_codex_lab_interval_sec == 1800
    assert settings.jue_codex_lab_autonomy_mode == "auto_apply_verified"
    assert settings.jue_codex_lab_db_path == ".runtime/jue_codex_lab.db"
    assert settings.jue_codex_lab_max_patch_bytes == 120_000
    assert "src/tradecraft" in _csv_items(settings.jue_codex_lab_allowed_paths)
    assert "tests" in _csv_items(settings.jue_codex_lab_allowed_paths)
    assert "docs/superpowers/plans" in _csv_items(settings.jue_codex_lab_allowed_paths)
    assert "docs/spec" in _csv_items(settings.jue_codex_lab_allowed_paths)
    assert ".env" in _csv_items(settings.jue_codex_lab_blocked_paths)
    assert ".runtime" in _csv_items(settings.jue_codex_lab_blocked_paths)
    assert "secrets" in _csv_items(settings.jue_codex_lab_blocked_paths)
    assert "credentials" in _csv_items(settings.jue_codex_lab_blocked_paths)
    assert "private_key" in _csv_items(settings.jue_codex_lab_blocked_paths)
    assert settings.jue_codex_lab_max_tasks_per_cycle == 1
    assert settings.jue_codex_lab_market_hours_hot_deploy is False


def test_jue_codex_lab_settings_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_ENABLED", "false")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_INTERVAL_SEC", "900")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_AUTONOMY_MODE", "proposal_only")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_DB_PATH", ".runtime/custom_codex_lab.db")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_MAX_PATCH_BYTES", "4096")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_ALLOWED_PATHS", "src/tradecraft/config.py,tests/test_config.py")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_BLOCKED_PATHS", ".env,private")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_MAX_TASKS_PER_CYCLE", "3")
    monkeypatch.setenv("TRADECRAFT_JUE_CODEX_LAB_MARKET_HOURS_HOT_DEPLOY", "true")

    settings = AppSettings(_env_file=None)

    assert settings.jue_codex_lab_enabled is False
    assert settings.jue_codex_lab_interval_sec == 900
    assert settings.jue_codex_lab_autonomy_mode == "proposal_only"
    assert settings.jue_codex_lab_db_path == ".runtime/custom_codex_lab.db"
    assert settings.jue_codex_lab_max_patch_bytes == 4096
    assert _csv_items(settings.jue_codex_lab_allowed_paths) == [
        "src/tradecraft/config.py",
        "tests/test_config.py",
    ]
    assert _csv_items(settings.jue_codex_lab_blocked_paths) == [".env", "private"]
    assert settings.jue_codex_lab_max_tasks_per_cycle == 3
    assert settings.jue_codex_lab_market_hours_hot_deploy is True


def test_llm_usage_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_LLM_USAGE_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_LLM_USAGE_DB_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_LLM_REASONING_EFFORT", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.llm_usage_enabled is True
    assert settings.llm_usage_db_path == ".runtime/llm_usage.db"
    assert settings.llm_reasoning_effort == "xhigh"


def test_live_authority_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_LIVE_EVALUATOR_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_LIVE_EVALUATOR_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER", raising=False)
    monkeypatch.delenv("TRADECRAFT_KIS_VALIDATION_INITIAL_EQUITY_KRW", raising=False)
    monkeypatch.delenv("TRADECRAFT_BINANCE_VALIDATION_INITIAL_EQUITY_USDT", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.live_evaluator_enabled is True
    assert settings.live_evaluator_interval_sec == 300
    assert settings.trading_validation_max_age_sec == 1800
    assert settings.trading_validation_payload_compaction_enabled is True
    assert settings.trading_validation_payload_recent_rows_per_group == 48
    assert settings.trading_validation_payload_max_rows_per_group == 720
    assert settings.trading_validation_payload_compact_min_chars == 20_000
    assert settings.live_authority_max_scale_multiplier == 1.5
    assert settings.live_authority_min_samples_to_scale == 10
    assert settings.jue_strategy_revision_id == "jue_edge_repair_v1"
    assert settings.binance_validation_spot_fee_rate == 0.001
    assert settings.binance_validation_futures_fee_rate == 0.0005
    assert settings.binance_validation_slippage_bps == 2.0
    assert settings.binance_validation_initial_equity_usdt == 1_000
    assert settings.kis_validation_buy_fee_rate == 0.00015
    assert settings.kis_validation_sell_fee_rate == 0.00015
    assert settings.kis_validation_sell_tax_rate == 0.002
    assert settings.kis_validation_slippage_bps == 5.0
    assert settings.kis_validation_spread_bps == 0.0
    assert settings.kis_validation_initial_equity_krw == 4_000_000


def test_kis_validation_spread_bps_env_alias(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_KIS_VALIDATION_SPREAD_BPS", "1.75")

    settings = AppSettings(_env_file=None)

    assert settings.kis_validation_spread_bps == 1.75


def test_watchdog_defaults_to_thirty_minute_check_interval(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_WATCHDOG_INTERVAL_SEC", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.watchdog_enabled is True
    assert settings.watchdog_interval_sec == 1800
    assert settings.watchdog_cooldown_sec == 300
    assert settings.watchdog_max_restarts_per_window == 3


def test_jue_wiki_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_ROOT_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_DB_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_CONTEXT_MAX_CHARS", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_RUNNER_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_PAGE_MAX_CHARS", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_CONTEXT_PAGE_LIMIT", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.jue_wiki_enabled is True
    assert settings.jue_wiki_root_path == ".runtime/jue_wiki"
    assert settings.jue_wiki_db_path == ".runtime/jue_wiki/wiki.db"
    assert settings.jue_wiki_context_max_chars == 24000
    assert settings.jue_wiki_runner_interval_sec == 1800
    assert settings.jue_wiki_page_max_chars == 12000
    assert settings.jue_wiki_context_page_limit == 8


def test_jue_wiki_phase2_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_PROMPT_MODE", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_SELECTOR_MAX_PAGES", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_SELECTOR_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EXCLUDE_LINT_WARNINGS", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_REPAIR_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_FULL_PROMPT_MAX_CHARS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.jue_wiki_prompt_mode == "assist"
    assert settings.jue_wiki_selector_max_pages == 24
    assert settings.jue_wiki_selector_min_confidence == 0.15
    assert settings.jue_wiki_exclude_lint_warnings is True
    assert settings.jue_wiki_repair_enabled is True
    assert settings.jue_wiki_full_prompt_max_chars == 190_000


def test_jue_wiki_phase3_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT",
        raising=False,
    )
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES", raising=False)
    monkeypatch.delenv(
        "TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES",
        raising=False,
    )

    settings = AppSettings(_env_file=None)

    assert settings.jue_wiki_application_enabled is True
    assert settings.jue_wiki_effectiveness_weight == 0.12
    assert settings.jue_wiki_effectiveness_max_adjustment == 8.0
    assert settings.jue_wiki_effectiveness_min_samples == 5
    assert settings.jue_wiki_mode_recommendation_min_samples == 20


def test_llm_reasoning_effort_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_REASONING_EFFORT", "high")

    settings = AppSettings(_env_file=None)

    assert settings.llm_reasoning_effort == "high"


def test_reports_ui_allowed_cidr_list_parses_csv(monkeypatch) -> None:
    monkeypatch.setenv(
        "TRADECRAFT_REPORTS_UI_ALLOWED_CIDRS",
        "127.0.0.1/32, 10.0.0.0/8 ,",
    )

    settings = AppSettings(_env_file=None)

    assert settings.reports_ui_allowed_cidr_list == [
        "127.0.0.1/32",
        "10.0.0.0/8",
    ]


def test_reports_api_token_list_combines_rotating_and_legacy_values(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_REPORTS_API_TOKENS", "next-token, current-token")
    monkeypatch.setenv("TRADECRAFT_REPORTS_API_TOKEN", "legacy-token")

    settings = AppSettings(_env_file=None)

    assert settings.reports_api_token_list == [
        "next-token",
        "current-token",
        "legacy-token",
    ]


def test_admin_token_list_combines_rotating_and_legacy_values(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKENS", "next-admin, current-admin")
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "legacy-admin")

    settings = AppSettings(_env_file=None)

    assert settings.admin_token_list == [
        "next-admin",
        "current-admin",
        "legacy-admin",
    ]


def test_security_defaults_are_locked_down(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("TRADECRAFT_RAG_ALLOW_LEGACY_PICKLE_MIGRATION", raising=False)

    settings = AppSettings(_env_file=None)

    assert "*" not in settings.cors_origins
    assert "http://127.0.0.1:8000" in settings.cors_origins
    assert settings.rag_allow_legacy_pickle_migration is False


def test_env_example_does_not_reopen_cors_to_wildcard() -> None:
    env_example = Path(".env.example").read_text()

    assert "TRADECRAFT_ALLOW_ORIGINS=*" not in env_example
    assert "TRADECRAFT_ALLOW_ORIGINS=http://127.0.0.1:18080" in env_example


def test_market_intelligence_source_list_has_default_playbook() -> None:
    settings = AppSettings(_env_file=None)

    source_ids = {
        str(row.get("source_id"))
        for row in settings.market_intelligence_source_list
    }

    assert "whale_insight" in source_ids
    assert "after_close_330" in source_ids


def test_strategy_insight_source_list_parses_import_sources(monkeypatch) -> None:
    monkeypatch.setenv(
        "TRADECRAFT_STRATEGY_INSIGHT_SOURCES_JSON",
        json.dumps(
            [
                {
                    "source_id": "whale_insight",
                    "label": "Whale file",
                    "path": "/tmp/whale.jsonl",
                    "enabled": True,
                },
                {"source_id": "after_close_330", "signals": [{"symbol": "005930"}]},
                {"source_id": "", "path": "/tmp/broken.jsonl"},
            ],
            ensure_ascii=False,
        ),
    )

    settings = AppSettings(_env_file=None)

    assert settings.strategy_insight_source_list == [
        {
            "source_id": "whale_insight",
            "label": "Whale file",
            "enabled": True,
            "dedupe": True,
            "path": "/tmp/whale.jsonl",
        },
        {
            "source_id": "after_close_330",
            "label": "after_close_330",
            "enabled": True,
            "dedupe": True,
            "signals": [{"symbol": "005930"}],
        },
    ]


def test_strategy_insight_source_list_has_public_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_STRATEGY_INSIGHT_SOURCES_JSON", raising=False)

    settings = AppSettings()
    sources = settings.strategy_insight_source_list

    assert settings.strategy_insight_retention_days == 45
    assert settings.strategy_insight_signal_row_cap_per_symbol == 96
    assert settings.strategy_insight_sidecar_max_lines == 500
    assert settings.strategy_insight_migrate_legacy_jsonl is False
    assert [row["source_id"] for row in sources] == [
        "whale_insight",
        "after_close_330",
    ]
    assert sources[0]["kind"] == "whale_insight_static"
    assert sources[0]["symbol_search_url"].startswith("https://api.lefthanders-new.xyz/")
    assert sources[1]["kind"] == "sesiban_leading"

    from tradecraft.services.settings_catalog import META

    assert META["strategy_insight_retention_days"].category == "signals"
    assert META["strategy_insight_signal_row_cap_per_symbol"].category == "signals"
    assert META["strategy_insight_sidecar_max_lines"].category == "signals"
    assert META["strategy_insight_migrate_legacy_jsonl"].category == "signals"
    assert sources[1]["url"].startswith("https://api.lefthanders-new.xyz/")


def test_jue_kis_default_focus_limits_are_ai_scale(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_KIS_BLOCK_TRADER_MAX_MANAGER_SYMBOLS", raising=False)
    monkeypatch.delenv("TRADECRAFT_MARKET_JUDGE_LLM_MAX_SYMBOLS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.kis_block_trader_max_manager_symbols == 80
    assert settings.market_judge_llm_max_symbols == 60


def test_naver_reports_llm_facts_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODE", "sdk")
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED", "false")

    settings = AppSettings()

    assert settings.codex_runtime_ready is True
    assert settings.naver_reports_llm_facts_enabled is False
    assert settings.naver_reports_llm_facts_active is False


def test_naver_reports_llm_facts_active_requires_native(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED", "true")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODE", "none")

    settings = AppSettings()

    assert settings.naver_reports_llm_facts_enabled is True
    assert settings.codex_runtime_ready is False
    assert settings.naver_reports_llm_facts_active is False


def test_rag_skip_existing_defaults_to_incremental_sync(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_RAG_SKIP_EXISTING", raising=False)

    settings = AppSettings()

    assert settings.rag_skip_existing is True


def test_market_judge_defaults_are_trading_judgment_only(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_MARKET_JUDGE_ENABLED", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.market_judge_enabled is False
    assert settings.market_judge_db_path == ".runtime/market_judgment.db"
    assert settings.market_judge_interval_sec == 1800
    assert settings.market_judge_llm_max_symbols == 60
    assert settings.market_judge_use_naver_fallback is False
    assert settings.market_judge_quote_retention_days == 3
    assert settings.market_judge_quote_archive_retention_days == 7
    assert settings.market_judge_account_retention_days == 30
    assert settings.market_judge_judgment_retention_days == 30
    assert settings.market_judge_compact_recent_run_count == 48
    assert settings.market_judge_compact_min_chars == 20_000
    assert settings.market_judge_compact_symbol_min_chars == 2_000
    assert settings.market_pulse_enabled is True
    assert settings.market_pulse_db_path == ".runtime/market_pulse.db"
    assert settings.market_pulse_interval_sec == 60
    assert settings.market_pulse_closed_interval_sec == 1800
    assert settings.market_pulse_retention_days == 3
    assert settings.market_pulse_archive_retention_days == 7
    assert settings.market_pulse_index_codes == "KOSPI,KOSDAQ,KPI200,FUT"
    assert settings.market_pulse_investor_flow_enabled is True
    assert settings.market_pulse_investor_flow_markets == "KOSPI,KOSDAQ,FUT"
    assert settings.market_pulse_program_trading_enabled is True
    assert settings.market_pulse_program_trading_markets == "KOSPI,KOSDAQ"
    assert settings.market_pulse_fx_enabled is True


def test_kis_block_trader_defaults_are_safe(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_KIS_BLOCK_TRADER_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.kis_block_trader_enabled is False
    assert settings.kis_block_trader_execute_orders is False
    assert settings.kis_block_trader_db_path == ".runtime/kis_blocks.db"
    assert settings.kis_block_trader_rule_interval_sec == 10
    assert settings.kis_block_trader_manager_interval_sec == 1800
    assert settings.kis_block_trader_manager_error_retry_sec == 300
    assert settings.kis_block_trader_retention_interval_sec == 3600
    assert settings.kis_block_trader_max_manager_symbols == 80
    assert settings.kis_block_trader_prompt_target_chars == 100_000
    assert settings.kis_block_trader_prompt_warn_chars == 150_000
    assert settings.kis_block_trader_prompt_max_chars == 190_000
    assert settings.kis_rate_limit_enabled is True
    assert settings.kis_rest_rate_limit_per_sec == 8.0
    assert settings.kis_account_min_interval_sec == 8.0
    assert settings.kis_token_min_interval_sec == 65.0
    assert settings.kis_rate_limit_db_path == ".runtime/kis_rate_limit.db"
    assert settings.dashboard_kis_balance_cache_ttl_sec == 180
    assert settings.dashboard_crypto_balance_cache_ttl_sec == 180
    assert settings.dashboard_stale_balance_cache_ttl_sec == 7200
    assert settings.dashboard_kis_balance_error_cooldown_sec == 180
    assert settings.dashboard_balance_fetch_timeout_sec == 25.0
    assert settings.dashboard_kis_us_balance_enabled is True
    assert settings.kis_block_trader_quote_retention_days == 3
    assert settings.kis_block_trader_reconciliation_retention_days == 7
    assert settings.kis_block_trader_manager_run_retention_days == 14
    assert settings.kis_block_trader_archive_retention_days == 7

    from tradecraft.services.settings_catalog import META

    cache_meta = META["dashboard_kis_balance_cache_ttl_sec"]
    assert cache_meta.category == "kis"
    assert cache_meta.min_value == 0
    assert cache_meta.max_value == 600
    crypto_cache_meta = META["dashboard_crypto_balance_cache_ttl_sec"]
    assert crypto_cache_meta.category == "binance"
    assert crypto_cache_meta.min_value == 0
    assert crypto_cache_meta.max_value == 600
    stale_cache_meta = META["dashboard_stale_balance_cache_ttl_sec"]
    assert stale_cache_meta.category == "system"
    assert stale_cache_meta.min_value == 0
    assert stale_cache_meta.max_value == 7200
    error_cooldown_meta = META["dashboard_kis_balance_error_cooldown_sec"]
    assert error_cooldown_meta.category == "kis"
    assert error_cooldown_meta.min_value == 0
    assert error_cooldown_meta.max_value == 600
    balance_timeout_meta = META["dashboard_balance_fetch_timeout_sec"]
    assert balance_timeout_meta.category == "system"
    assert balance_timeout_meta.min_value == 1
    assert balance_timeout_meta.max_value == 60
    assert META["dashboard_kis_us_balance_enabled"].category == "kis"
    assert "kis_block_trader_quote_retention_days" in META
    assert "kis_block_trader_reconciliation_retention_days" in META
    assert "kis_block_trader_manager_run_retention_days" in META
    assert "kis_block_trader_archive_retention_days" in META
    assert META["kis_block_trader_manager_error_retry_sec"].category == "ops"
    assert META["kis_block_trader_manager_error_retry_sec"].min_value == 60
    assert "kis_block_trader_retention_interval_sec" in META


def test_valuation_auto_collect_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_VALUATION_AUTO_COLLECT", raising=False)
    monkeypatch.delenv("TRADECRAFT_VALUATION_AUTO_MIN_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("TRADECRAFT_VALUATION_AUTO_MAX_SYMBOLS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.valuation_auto_collect is True
    assert settings.valuation_auto_min_interval_sec == 1800
    assert settings.valuation_auto_max_symbols == 8

    from tradecraft.services.settings_catalog import META

    assert META["valuation_auto_collect"].category == "signals"
    assert META["valuation_auto_min_interval_sec"].min_value == 300
    assert META["valuation_auto_max_symbols"].max_value == 30


def test_binance_block_trader_defaults() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.binance_block_trader_enabled is False
    assert settings.binance_block_trader_execute_spot_orders is False
    assert settings.binance_block_trader_execute_futures_orders is False
    assert settings.binance_block_trader_once is False
    assert settings.binance_block_trader_db_path == ".runtime/binance_blocks.db"
    assert settings.binance_block_trader_state_path == (
        ".runtime/binance_block_trader.json"
    )
    assert settings.binance_block_trader_quote_interval_sec == 15
    assert settings.binance_block_trader_rule_interval_sec == 15
    assert settings.binance_block_trader_manager_interval_sec == 1800
    assert settings.binance_block_trader_manager_error_retry_sec == 300
    assert settings.binance_block_trader_telegram_reports_enabled is True
    assert settings.binance_block_trader_telegram_report_slots == (
        "morning:06:00,noon:12:00,night:20:00"
    )
    assert settings.binance_block_trader_llm_model == "gpt-5.5"
    assert settings.binance_block_trader_llm_reasoning_effort == "xhigh"
    assert settings.binance_block_trader_llm_timeout_ms == 420_000
    assert settings.binance_block_trader_max_manager_symbols == 60
    assert settings.binance_block_trader_prompt_target_chars == 70_000
    assert settings.binance_block_trader_prompt_warn_chars == 90_000
    assert settings.binance_block_trader_prompt_max_chars == 190_000
    assert settings.binance_block_trader_jue_wiki_context_max_chars == 18_000
    assert settings.binance_block_trader_spot_universe == (
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
    )
    assert settings.binance_block_trader_futures_universe == (
        "BTCUSDT,ETHUSDT,SOLUSDT"
    )
    assert settings.binance_block_trader_max_futures_leverage == 2
    assert settings.binance_block_trader_min_liquidation_distance_pct == 12.0
    assert settings.binance_block_trader_aggressive_limit_bps == 20.0
    assert settings.binance_block_trader_failed_exit_retry_cooldown_sec == 60
    assert settings.binance_block_trader_spot_quote_budget_pct == 5.0
    assert settings.binance_block_trader_spot_min_quote_budget_usdt == 50.0
    assert settings.binance_block_trader_spot_max_quote_budget_usdt == 300.0
    assert settings.binance_block_trader_futures_quote_budget_pct == 10.0
    assert settings.binance_block_trader_futures_min_quote_budget_usdt == 25.0
    assert settings.binance_block_trader_futures_max_quote_budget_usdt == 150.0
    assert settings.binance_block_trader_budget_performance_scale_enabled is True
    assert settings.binance_block_trader_budget_performance_scale_min_samples == 10
    assert settings.binance_block_trader_budget_performance_scale_win_rate_pct == 55.0
    assert settings.binance_block_trader_budget_performance_scale_multiplier == 1.5
    assert settings.binance_block_trader_quote_retention_days == 7
    assert settings.binance_block_trader_manager_run_retention_days == 14
    assert settings.binance_block_trader_archive_retention_days == 7
    assert settings.binance_block_trader_performance_feedback_interval_sec == 300

    from tradecraft.services.settings_catalog import META

    assert META["binance_block_trader_manager_error_retry_sec"].category == "ops"
    assert META["binance_block_trader_manager_error_retry_sec"].min_value == 60
    assert settings.binance_block_trader_retention_interval_sec == 3600

    from tradecraft.services.settings_catalog import META

    assert "binance_block_trader_archive_retention_days" in META
    assert "binance_block_trader_performance_feedback_interval_sec" in META
    assert "binance_block_trader_retention_interval_sec" in META


def test_binance_jue_edge_defaults(monkeypatch) -> None:
    monkeypatch.delenv(
        "TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_INTERVALS",
        raising=False,
    )
    monkeypatch.delenv(
        "TRADECRAFT_BINANCE_BLOCK_TRADER_ACCOUNT_RISK_PCT",
        raising=False,
    )

    settings = AppSettings(_env_file=None)

    assert settings.crypto_market_research_kline_intervals == (
        "1m:120,5m:96,15m:96,1h:168,4h:180"
    )
    assert settings.crypto_market_research_kline_hot_window_rows == 720
    assert settings.crypto_market_research_market_hot_window_rows == 720
    assert settings.crypto_market_research_llm_top_symbols == 30
    assert settings.crypto_market_research_regime_enabled is True
    assert settings.crypto_market_research_squeeze_guard_enabled is True
    assert settings.binance_block_trader_account_risk_pct == 0.25
    assert settings.binance_block_trader_max_total_exposure_usdt == 0.0
    assert settings.binance_block_trader_max_symbol_exposure_pct == 25.0
    assert settings.binance_block_trader_min_reward_risk == 1.3
    assert settings.binance_block_trader_max_manager_symbols == 60
    assert settings.binance_block_trader_volatile_attack_enabled is True
    assert settings.binance_block_trader_volatile_attack_budget_multiplier == 0.35
    assert settings.binance_block_trader_volatile_attack_min_reward_risk == 2.0
    assert settings.binance_block_trader_daily_loss_stop_pct == 7.0
    assert settings.binance_block_trader_monthly_loss_stop_pct == 20.0
    assert settings.research_runner_collect_reports is False


def test_crypto_market_research_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CRYPTO_MARKET_RESEARCH_DB_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_MODEL", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.crypto_market_research_enabled is True
    assert settings.crypto_market_research_once is False
    assert settings.crypto_market_research_db_path == ".runtime/crypto_market_research.db"
    assert settings.crypto_market_research_state_path == ".runtime/crypto_market_research.json"
    universe = settings.crypto_market_research_universe.split(",")
    assert len(universe) >= 25
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}.issubset(universe)
    assert settings.crypto_market_research_max_symbols == 300
    assert settings.crypto_market_research_auto_universe_enabled is True
    assert settings.crypto_market_research_auto_universe_limit == 300
    assert settings.crypto_market_research_research_universe_limit == 80
    assert settings.crypto_market_research_llm_top_symbols == 30
    assert settings.crypto_market_research_min_quote_volume_usdt == 100_000.0
    assert settings.crypto_market_research_collect_symbol_timeout_sec == 20.0
    assert settings.crypto_market_research_collect_cycle_timeout_sec == 240.0
    assert settings.crypto_market_research_collect_concurrency == 4
    assert settings.crypto_market_research_feature_interval_sec == 300
    assert settings.crypto_market_research_llm_interval_sec == 3600
    assert settings.crypto_market_research_llm_model == "gpt-5.5"
    assert settings.crypto_market_research_llm_reasoning_effort == "xhigh"
    assert settings.crypto_market_research_external_enabled is True
    assert settings.crypto_market_research_external_sources == "coingecko,defillama,fear_greed"


def test_crypto_quant_defaults() -> None:
    from tradecraft.services.settings_catalog import META

    settings = AppSettings(_env_file=None)

    assert settings.crypto_quant_enabled is True
    assert settings.crypto_quant_db_path == ".runtime/crypto_quant.db"
    assert settings.crypto_quant_context_limit == 18
    assert settings.crypto_quant_hot_window_rows == 360
    assert settings.crypto_quant_archive_window_rows == 360
    assert settings.crypto_quant_retention_days == 3
    assert settings.crypto_market_research_retention_days == 3
    assert settings.crypto_market_research_kline_hot_window_rows == 720
    assert settings.crypto_market_research_market_hot_window_rows == 720
    assert "crypto_market_research_kline_hot_window_rows" in META
    assert "crypto_market_research_market_hot_window_rows" in META
    assert "crypto_quant_archive_window_rows" in META


def test_crypto_quant_short_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("CRYPTO_QUANT_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_QUANT_DB_PATH", ".runtime/custom_crypto_quant.db")
    monkeypatch.setenv("CRYPTO_QUANT_CONTEXT_LIMIT", "9")

    settings = AppSettings(_env_file=None)

    assert settings.crypto_quant_enabled is False
    assert settings.crypto_quant_db_path == ".runtime/custom_crypto_quant.db"
    assert settings.crypto_quant_context_limit == 9


def test_crypto_quant_settings_are_visible() -> None:
    from tradecraft.services.settings_catalog import META

    assert "crypto_quant_enabled" in META
    assert META["crypto_quant_enabled"].category == "signals"
    assert "crypto_quant_context_limit" in META
    assert "crypto_quant_hot_window_rows" in META


def test_crypto_pattern_lab_defaults() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.crypto_pattern_lab_enabled is True
    assert settings.crypto_pattern_lab_db_path == ".runtime/crypto_pattern_lab.db"
    assert settings.kr_equity_pattern_lab_db_path == ".runtime/kr_equity_pattern_lab.db"
    assert settings.kr_equity_pattern_lab_enabled is True
    assert settings.kr_equity_pattern_lab_min_samples == 3
    assert settings.crypto_pattern_lab_intervals == "5m,15m,1h"
    assert settings.crypto_pattern_lab_context_limit == 12
    assert settings.crypto_pattern_lab_backtests_per_tuple_retention == 4
    assert settings.crypto_pattern_lab_optimizer_runs_per_tuple_retention == 4
    assert settings.crypto_pattern_lab_optimizer_trials_per_run_retention == 8
    assert settings.crypto_pattern_lab_max_backtest_rows == 80_000
    assert settings.crypto_pattern_lab_max_optimizer_runs == 2_500
    assert settings.crypto_pattern_lab_max_optimizer_trials == 24_000
    from tradecraft.services.settings_catalog import META

    assert "kr_equity_pattern_lab_db_path" in META
    assert "kr_equity_pattern_lab_enabled" in META
    assert "kr_equity_pattern_lab_min_samples" in META
    assert "crypto_pattern_lab_backtests_per_tuple_retention" in META
    assert "crypto_pattern_lab_optimizer_runs_per_tuple_retention" in META
    assert "crypto_pattern_lab_optimizer_trials_per_run_retention" in META
    assert "crypto_pattern_lab_max_backtest_rows" in META
    assert "crypto_pattern_lab_max_optimizer_runs" in META
    assert "crypto_pattern_lab_max_optimizer_trials" in META


def test_crypto_pattern_lab_short_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PATTERN_LAB_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_PATTERN_LAB_DB_PATH", "/tmp/patterns.db")

    settings = AppSettings(_env_file=None)

    assert settings.crypto_pattern_lab_enabled is False
    assert settings.crypto_pattern_lab_db_path == "/tmp/patterns.db"


def test_crypto_alpha_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_DB_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_SOURCE_IDS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.crypto_alpha_enabled is True
    assert settings.crypto_alpha_once is False
    assert settings.crypto_alpha_db_path == ".runtime/crypto_alpha.db"
    assert settings.crypto_alpha_state_path == ".runtime/crypto_alpha.json"
    assert settings.crypto_alpha_source_ids == (
        "binance_announcements,coinbase_blog,kraken_blog"
    )
    assert settings.crypto_alpha_crawl_interval_sec == 3600
    assert settings.crypto_alpha_outcome_interval_sec == 900
    assert settings.crypto_alpha_rate_limit_sec == 2.0
    assert settings.crypto_alpha_context_limit == 12
    assert settings.crypto_alpha_llm_model == "gpt-5.5"
    assert settings.crypto_alpha_llm_reasoning_effort == "xhigh"


def test_investment_memory_defaults_are_safe(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_INVESTMENT_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_INVESTMENT_MEMORY_SEND_TELEGRAM", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.investment_memory_enabled is False
    assert settings.investment_memory_send_telegram is True
    assert settings.investment_memory_root_path == ".runtime/investment_memory"
    assert settings.investment_memory_db_path == ".runtime/investment_memory.db"
    assert settings.investment_memory_policy_mode == "soft_auto"
    assert settings.investment_memory_run_daily_discovery is False
    assert settings.investment_memory_compaction_interval_sec == 3600
    assert settings.investment_memory_policy_retired_keep == 2
    assert settings.investment_memory_validation_event_retained_rows_per_venue == 720
    assert settings.investment_memory_run_recent_rows_per_group == 24
    assert settings.investment_memory_symbol_analysis_recent_rows_per_symbol == 3

    from tradecraft.services.settings_catalog import META

    assert META["investment_memory_compaction_interval_sec"].category == "memory"
    assert META["investment_memory_run_daily_discovery"].category == "memory"
    assert META["investment_memory_policy_retired_keep"].category == "memory"
    assert (
        META["investment_memory_validation_event_retained_rows_per_venue"].category
        == "memory"
    )
    assert META["investment_memory_run_recent_rows_per_group"].category == "memory"
    assert (
        META["investment_memory_symbol_analysis_recent_rows_per_symbol"].category
        == "memory"
    )
