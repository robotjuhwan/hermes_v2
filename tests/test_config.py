import json

from tradecraft.config import AppSettings


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


def test_llm_bridge_mode_prefers_command(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_COMMAND", "/usr/bin/openai")
    monkeypatch.setenv(
        "TRADECRAFT_LLM_BRIDGE_URL", "https://example.com/v1/chat/completions"
    )

    settings = AppSettings(_env_file=None)

    assert settings.llm_bridge_mode == "command"
    assert settings.llm_bridge_ready is True


def test_llm_bridge_mode_uses_url_when_command_missing(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_COMMAND", "")
    monkeypatch.setenv(
        "TRADECRAFT_LLM_BRIDGE_URL", "https://example.com/v1/chat/completions"
    )

    settings = AppSettings(_env_file=None)

    assert settings.llm_bridge_mode == "url"
    assert settings.llm_bridge_ready is True


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

    assert [row["source_id"] for row in sources] == [
        "whale_insight",
        "after_close_330",
    ]
    assert sources[0]["kind"] == "whale_insight_static"
    assert sources[1]["kind"] == "sesiban_leading"


def test_naver_reports_llm_facts_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_URL", "https://example.com/v1/chat")
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED", "false")

    settings = AppSettings()

    assert settings.llm_bridge_ready is True
    assert settings.naver_reports_llm_facts_enabled is False
    assert settings.naver_reports_llm_facts_active is False


def test_naver_reports_llm_facts_active_requires_bridge(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED", "true")
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_COMMAND", "")
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_URL", "")

    settings = AppSettings()

    assert settings.naver_reports_llm_facts_enabled is True
    assert settings.llm_bridge_ready is False
    assert settings.naver_reports_llm_facts_active is False


def test_rag_skip_existing_defaults_to_incremental_sync(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_RAG_SKIP_EXISTING", raising=False)

    settings = AppSettings()

    assert settings.rag_skip_existing is True


def test_market_judge_defaults_are_advisory_only(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_MARKET_JUDGE_ENABLED", raising=False)

    settings = AppSettings()

    assert settings.market_judge_enabled is False
    assert settings.market_judge_db_path == ".runtime/market_judgment.db"
    assert settings.market_judge_interval_sec == 600
    assert settings.market_judge_llm_max_symbols == 12
    assert settings.market_judge_use_naver_fallback is True


def test_kis_block_trader_defaults_are_safe(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_KIS_BLOCK_TRADER_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.kis_block_trader_enabled is False
    assert settings.kis_block_trader_execute_orders is False
    assert settings.kis_block_trader_db_path == ".runtime/kis_blocks.db"
    assert settings.kis_block_trader_rule_interval_sec == 5
    assert settings.kis_block_trader_manager_interval_sec == 1800


def test_investment_memory_defaults_are_safe(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_INVESTMENT_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_INVESTMENT_MEMORY_SEND_TELEGRAM", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.investment_memory_enabled is False
    assert settings.investment_memory_send_telegram is True
    assert settings.investment_memory_root_path == ".runtime/investment_memory"
    assert settings.investment_memory_db_path == ".runtime/investment_memory.db"
    assert settings.investment_memory_policy_mode == "soft_auto"
