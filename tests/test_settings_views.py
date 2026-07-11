from __future__ import annotations

import pytest

from tradecraft.config import AppSettings
from tradecraft.services.settings_views import domain_settings_view


def test_domain_settings_view_is_read_only_and_keeps_env_aliases() -> None:
    settings = AppSettings()
    view = domain_settings_view(settings, "binance")

    assert view.binance_block_trader_enabled == settings.binance_block_trader_enabled
    assert view.aliases["binance_block_trader_enabled"] == (
        "TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED"
    )
    with pytest.raises(TypeError):
        view.values["binance_block_trader_enabled"] = False


def test_domain_settings_views_do_not_leak_unrelated_venue_fields() -> None:
    kis = domain_settings_view(AppSettings(), "kis")

    assert "kis_block_trader_enabled" in kis.values
    assert "binance_block_trader_enabled" not in kis.values
    with pytest.raises(AttributeError):
        _ = kis.binance_block_trader_enabled
