from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


@dataclass
class FreqtradeBotConfig:
    bot_id: str
    label: str
    api_url: str = ""
    username: str = ""
    password: str = ""
    config_path: str = ""
    venue_id: str = "binance"
    venue_label: str = "바이낸스 현물"
    market_tag: str = "FREQTRADE_SPOT"


@dataclass
class FreqtradeBridgeConfig:
    bots: list[FreqtradeBotConfig]
    timeout_sec: float = 3.5
    bot_api_url_overrides: dict[str, str] = field(default_factory=dict)


class FreqtradeBridge:
    def __init__(self, config: FreqtradeBridgeConfig) -> None:
        self.config = config

    async def fetch_sessions(self, usdt_krw_rate: float) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        bots: list[dict[str, Any]] = []
        timeout = httpx.Timeout(float(self.config.timeout_sec))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for bot in self.config.bots:
                resolved = self._resolve_bot_config(bot)
                if not resolved:
                    continue
                bot_state, bot_sessions = await self._fetch_bot_sessions(
                    client=client,
                    bot=resolved,
                    usdt_krw_rate=usdt_krw_rate,
                )
                bots.append(bot_state)
                sessions.extend(bot_sessions)
        return {"bots": bots, "sessions": sessions}

    def _resolve_bot_config(self, bot: FreqtradeBotConfig) -> FreqtradeBotConfig | None:
        out = FreqtradeBotConfig(**bot.__dict__)
        override_api_url = str(self.config.bot_api_url_overrides.get(out.bot_id) or "")
        if override_api_url:
            out.api_url = override_api_url

        from_file = self._load_api_server_from_config_path(out.config_path)
        if from_file:
            if not out.api_url:
                port = self._to_int(from_file.get("listen_port"))
                if port > 0:
                    out.api_url = f"http://127.0.0.1:{port}"
            if not out.username:
                out.username = str(from_file.get("username") or "")
            if not out.password:
                out.password = str(from_file.get("password") or "")

        out.api_url = out.api_url.strip().rstrip("/")
        out.username = out.username.strip()
        out.password = out.password.strip()
        if not out.api_url:
            return None
        return out

    async def _fetch_bot_sessions(
        self,
        client: httpx.AsyncClient,
        bot: FreqtradeBotConfig,
        usdt_krw_rate: float,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        auth = self._auth_tuple(bot.username, bot.password)
        status_url = f"{bot.api_url}/api/v1/status"
        show_config_url = f"{bot.api_url}/api/v1/show_config"
        count_url = f"{bot.api_url}/api/v1/count"

        try:
            status_payload = await self._get_json(client, status_url, auth=auth)
            if not isinstance(status_payload, list):
                status_payload = []
        except Exception as exc:
            return (
                {
                    "bot_id": bot.bot_id,
                    "label": bot.label,
                    "connected": False,
                    "configured": True,
                    "error": str(exc),
                    "open_trades": 0,
                },
                [],
            )

        show_config: dict[str, Any] = {}
        count_payload: dict[str, Any] = {}
        try:
            payload = await self._get_json(client, show_config_url, auth=auth)
            if isinstance(payload, dict):
                show_config = payload
        except Exception:
            show_config = {}

        try:
            payload = await self._get_json(client, count_url, auth=auth)
            if isinstance(payload, dict):
                count_payload = payload
        except Exception:
            count_payload = {}

        bot_name = str(show_config.get("bot_name") or bot.bot_id)
        trading_mode = str(show_config.get("trading_mode") or "").lower() or (
            "futures" if "futures" in bot.bot_id else "spot"
        )
        state = str(show_config.get("state") or "running").upper()
        sessions = self._map_trade_sessions(
            trades=status_payload,
            bot=bot,
            bot_name=bot_name,
            trading_mode=trading_mode,
            state=state,
            usdt_krw_rate=usdt_krw_rate,
        )
        return (
            {
                "bot_id": bot.bot_id,
                "label": bot.label,
                "connected": True,
                "configured": True,
                "error": "",
                "open_trades": len(status_payload),
                "current_trades": self._to_int(count_payload.get("current")),
                "max_trades": self._to_int(count_payload.get("max")),
                "trading_mode": trading_mode,
                "state": state,
                "bot_name": bot_name,
            },
            sessions,
        )

    def _map_trade_sessions(
        self,
        trades: list[dict[str, Any]],
        bot: FreqtradeBotConfig,
        bot_name: str,
        trading_mode: str,
        state: str,
        usdt_krw_rate: float,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        fx = self._to_float(usdt_krw_rate)
        if fx <= 0:
            fx = 1.0

        for row in trades:
            if not isinstance(row, dict):
                continue
            trade_id = self._to_int(row.get("trade_id"))
            pair = str(row.get("pair") or "-")
            is_short = bool(row.get("is_short"))
            side = "SHORT" if is_short else "LONG"
            open_rate = self._to_float(row.get("open_rate"))
            stop_loss_abs = self._to_float(row.get("stop_loss_abs"))
            current_rate = self._to_float(row.get("current_rate"))
            profit_abs = self._to_float(row.get("profit_abs"))
            realized_profit = self._to_float(row.get("realized_profit"))
            open_trade_value = abs(self._to_float(row.get("open_trade_value")))
            if open_trade_value <= 0:
                open_trade_value = abs(self._to_float(row.get("stake_amount")))

            fee_open_cost = abs(self._to_float(row.get("fee_open_cost")))
            fee_close_cost = abs(self._to_float(row.get("fee_close_cost")))
            funding_fees = abs(self._to_float(row.get("funding_fees")))
            total_fees = fee_open_cost + fee_close_cost + funding_fees

            leverage = self._to_float(row.get("leverage"))
            if leverage <= 0:
                leverage = 1.0
            profit_ratio = self._to_float(row.get("profit_ratio"))

            open_ts = self._to_int(row.get("open_timestamp"))
            hold_min = 0
            if open_ts > 0:
                opened = datetime.fromtimestamp(open_ts / 1000, tz=timezone.utc)
                hold_min = int(max((now - opened).total_seconds() // 60, 0))

            session_id = f"freqtrade_{bot.bot_id}_{trade_id if trade_id > 0 else pair.replace('/', '_')}"
            out.append(
                {
                    "session_id": session_id,
                    "venue_id": bot.venue_id,
                    "venue_label": bot.venue_label,
                    "name": f"{bot.label} 포지션",
                    "bot_name": bot_name,
                    "mode": "short_term",
                    "status": state or "RUNNING",
                    "cycle_sec": 5,
                    "active_markets": [bot.market_tag],
                    "strategy_count": 1,
                    "trade_count_today": 1,
                    "realized_pnl_krw": realized_profit * fx,
                    "unrealized_pnl_krw": profit_abs * fx,
                    "fees_paid_krw": -total_fees * fx,
                    "volume_traded_krw": open_trade_value * fx,
                    "trade_symbol": pair,
                    "position_side": side,
                    "entry_price": open_rate * fx if open_rate > 0 else None,
                    "stop_loss_price": stop_loss_abs * fx
                    if stop_loss_abs > 0
                    else None,
                    "take_profit_price": None,
                    "max_notional_krw": open_trade_value * fx,
                    "holding_limit_min": 0,
                    "win_rate_pct": max(min((profit_ratio * 100) + 50, 100.0), 0.0),
                    "avg_holding_min": float(hold_min),
                    "intraday_drawdown_pct": 0.0,
                    "fee_breakdown": {
                        "maker_krw": -total_fees * fx,
                        "taker_krw": 0.0,
                    },
                    "display_note": f"Freqtrade {trading_mode} open trade mirror (x{leverage:.2f})",
                    "risk_guard": "ON",
                    "last_heartbeat": now.isoformat(),
                }
            )

        return out

    def _load_api_server_from_config_path(self, config_path: str) -> dict[str, Any]:
        path = Path(config_path.strip()) if config_path else None
        if not path:
            return {}
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        api_server = payload.get("api_server")
        if not isinstance(api_server, dict):
            return {}
        return api_server

    @staticmethod
    async def _get_json(
        client: httpx.AsyncClient,
        url: str,
        auth: tuple[str, str] | None = None,
    ) -> Any:
        response = await client.get(
            url, auth=auth, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _auth_tuple(username: str, password: str) -> tuple[str, str] | None:
        if username or password:
            return username, password
        return None

    @staticmethod
    def _to_float(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    @staticmethod
    def _to_int(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
