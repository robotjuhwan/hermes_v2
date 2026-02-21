from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tradecraft.services.freqtrade import (
    FreqtradeBotConfig,
    FreqtradeBridge,
    FreqtradeBridgeConfig,
)


def test_freqtrade_bridge_resolves_config_file_credentials(tmp_path: Path) -> None:
    cfg_path = tmp_path / "freqtrade.json"
    cfg_path.write_text(
        json.dumps(
            {
                "api_server": {
                    "listen_port": 8089,
                    "username": "u",
                    "password": "p",
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = FreqtradeBridge(
        FreqtradeBridgeConfig(
            bots=[
                FreqtradeBotConfig(
                    bot_id="spot",
                    label="Freqtrade Spot",
                    config_path=str(cfg_path),
                )
            ]
        )
    )
    resolved = bridge._resolve_bot_config(bridge.config.bots[0])
    assert resolved is not None
    assert resolved.api_url == "http://127.0.0.1:8089"
    assert resolved.username == "u"
    assert resolved.password == "p"


def test_freqtrade_bridge_prefers_bot_api_url_override(tmp_path: Path) -> None:
    cfg_path = tmp_path / "freqtrade.json"
    cfg_path.write_text(
        json.dumps(
            {
                "api_server": {
                    "listen_port": 8089,
                    "username": "u",
                    "password": "p",
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = FreqtradeBridge(
        FreqtradeBridgeConfig(
            bots=[
                FreqtradeBotConfig(
                    bot_id="spot",
                    label="Freqtrade Spot",
                    api_url="http://127.0.0.1:8080",
                    config_path=str(cfg_path),
                )
            ],
            bot_api_url_overrides={"spot": "http://127.0.0.1:18080"},
        )
    )
    resolved = bridge._resolve_bot_config(bridge.config.bots[0])
    assert resolved is not None
    assert resolved.api_url == "http://127.0.0.1:18080"
    assert resolved.username == "u"
    assert resolved.password == "p"


def test_freqtrade_bridge_maps_open_trade_to_session(monkeypatch) -> None:
    bridge = FreqtradeBridge(
        FreqtradeBridgeConfig(
            bots=[
                FreqtradeBotConfig(
                    bot_id="spot",
                    label="Freqtrade Spot",
                    api_url="http://127.0.0.1:8080",
                    username="u",
                    password="p",
                    venue_id="binance",
                    venue_label="바이낸스 현물",
                )
            ]
        )
    )

    async def fake_get_json(_, url: str, auth=None):
        _ = auth
        if url.endswith("/status"):
            return [
                {
                    "trade_id": 42,
                    "pair": "BTC/USDT",
                    "is_short": False,
                    "leverage": 1,
                    "open_rate": 100000.0,
                    "current_rate": 101000.0,
                    "stop_loss_abs": 95000.0,
                    "profit_abs": 10.0,
                    "profit_ratio": 0.01,
                    "realized_profit": 0.0,
                    "open_trade_value": 1000.0,
                    "stake_amount": 1000.0,
                    "fee_open_cost": 0.5,
                    "fee_close_cost": 0.0,
                    "open_timestamp": 1771113600000,
                }
            ]
        if url.endswith("/show_config"):
            return {"bot_name": "jurobot", "trading_mode": "spot", "state": "running"}
        if url.endswith("/count"):
            return {"current": 1, "max": 3, "total_stake": 1000}
        return {}

    monkeypatch.setattr(bridge, "_get_json", fake_get_json)
    payload = asyncio.run(bridge.fetch_sessions(usdt_krw_rate=1400.0))
    sessions = payload["sessions"]
    bots = payload["bots"]
    assert len(bots) == 1
    assert bots[0]["connected"] is True
    assert bots[0]["open_trades"] == 1
    assert len(sessions) == 1
    row = sessions[0]
    assert row["session_id"] == "freqtrade_spot_42"
    assert row["venue_id"] == "binance"
    assert row["trade_symbol"] == "BTC/USDT"
    assert row["unrealized_pnl_krw"] == pytest.approx(14_000.0)
    assert row["entry_price"] == pytest.approx(140_000_000.0)
