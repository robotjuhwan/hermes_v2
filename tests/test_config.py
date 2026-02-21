from tradecraft.config import AppSettings


def test_freqtrade_bot_api_url_map_parses_pairs(monkeypatch) -> None:
    monkeypatch.setenv(
        "FREQTRADE_BOT_API_URLS",
        "spot=http://127.0.0.1:8080, freqai_reforcexy=http://127.0.0.1:8084/ , broken-entry",
    )

    settings = AppSettings()

    assert settings.freqtrade_bot_api_url_map == {
        "spot": "http://127.0.0.1:8080",
        "freqai_reforcexy": "http://127.0.0.1:8084",
    }


def test_naver_reports_seed_url_list_parses_csv(monkeypatch) -> None:
    monkeypatch.setenv(
        "TRADECRAFT_NAVER_REPORTS_SEED_URLS",
        "https://finance.naver.com/research/market_info_list.naver, https://finance.naver.com/research/company_list.naver",
    )

    settings = AppSettings()

    assert settings.naver_reports_seed_url_list == [
        "https://finance.naver.com/research/market_info_list.naver",
        "https://finance.naver.com/research/company_list.naver",
    ]


def test_llm_bridge_mode_prefers_command(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_COMMAND", "/usr/bin/openai")
    monkeypatch.setenv(
        "TRADECRAFT_LLM_BRIDGE_URL", "https://example.com/v1/chat/completions"
    )

    settings = AppSettings()

    assert settings.llm_bridge_mode == "command"
    assert settings.llm_bridge_ready is True


def test_llm_bridge_mode_uses_url_when_command_missing(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_LLM_BRIDGE_COMMAND", "")
    monkeypatch.setenv(
        "TRADECRAFT_LLM_BRIDGE_URL", "https://example.com/v1/chat/completions"
    )

    settings = AppSettings()

    assert settings.llm_bridge_mode == "url"
    assert settings.llm_bridge_ready is True
