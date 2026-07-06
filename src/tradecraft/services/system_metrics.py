from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable


_COMPONENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("control", ("tradecraft-control", "tradecraft-ui")),
    ("runtime", ("tradecraft-runtime",)),
    ("strategy_insights", ("tradecraft-strategy-insights",)),
    ("market_judge", ("tradecraft-market-judge",)),
    ("market_pulse", ("tradecraft-market-pulse",)),
    ("live_evaluator", ("tradecraft-live-evaluator",)),
    ("investment_memory", ("tradecraft-investment-memory",)),
    ("jue_wiki", ("tradecraft-jue-wiki", "jue_wiki_runner")),
    ("watchdog", ("tradecraft-watchdog",)),
    ("crypto_research", ("tradecraft-crypto-market-research",)),
    ("crypto_pattern_lab", ("tradecraft-crypto-pattern-lab",)),
    ("crypto_alpha", ("tradecraft-crypto-alpha",)),
    ("research", ("tradecraft-research",)),
    ("kis_block_trader", ("tradecraft-kis-block-trader",)),
    ("binance_block_trader", ("tradecraft-binance-block-trader",)),
    ("naver_reports", ("tradecraft-naver-reports",)),
    ("reports_api", ("tradecraft-reports-api",)),
    ("reports_worker", ("tradecraft-reports-worker",)),
)


@dataclass(slots=True)
class SystemMetricsService:
    psutil_module: Any | None = None
    time_func: Callable[[], float] = time.time
    cache_ttl_sec: float = 10.0
    root_path: str = "."
    process_limit: int = 24
    _cache: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _cache_at: float = field(default=0.0, init=False, repr=False)
    _last_net: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.psutil_module is None:
            try:
                import psutil  # type: ignore
            except Exception:
                self.psutil_module = None
            else:
                self.psutil_module = psutil

    def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        now = float(self.time_func())
        if (
            not force
            and self._cache is not None
            and now - self._cache_at < max(float(self.cache_ttl_sec), 1.0)
        ):
            cached = dict(self._cache)
            cached["cache"] = {"hit": True, "ttl_sec": float(self.cache_ttl_sec)}
            return cached

        if self.psutil_module is None:
            payload = {
                "status": "unavailable",
                "error_message": "psutil is not available",
                "generated_at": self._iso_from_ts(now),
                "cache": {"hit": False, "ttl_sec": float(self.cache_ttl_sec)},
            }
            self._cache = payload
            self._cache_at = now
            return payload

        payload = {
            "status": "ok",
            "generated_at": self._iso_from_ts(now),
            "sample_ttl_sec": float(self.cache_ttl_sec),
            "cache": {"hit": False, "ttl_sec": float(self.cache_ttl_sec)},
            "system": self._system_metrics(),
            "network": self._network_metrics(now),
            "hermes": self._hermes_process_metrics(),
        }
        self._cache = payload
        self._cache_at = now
        return payload

    def _system_metrics(self) -> dict[str, Any]:
        psutil = self.psutil_module
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory() if hasattr(psutil, "swap_memory") else None
        disk = psutil.disk_usage(str(Path(self.root_path).resolve()))
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        return {
            "cpu_percent": self._safe_float(psutil.cpu_percent(interval=None)),
            "load_avg": {
                "one_min": self._safe_float(load_avg[0]),
                "five_min": self._safe_float(load_avg[1]),
                "fifteen_min": self._safe_float(load_avg[2]),
            },
            "memory": self._memory_payload(memory),
            "swap": self._memory_payload(swap) if swap is not None else {},
            "disk": self._memory_payload(disk),
        }

    def _network_metrics(self, now: float) -> dict[str, Any]:
        counters = self.psutil_module.net_io_counters()
        sent = int(getattr(counters, "bytes_sent", 0) or 0)
        recv = int(getattr(counters, "bytes_recv", 0) or 0)
        sent_per_sec = 0.0
        recv_per_sec = 0.0
        interval_sec = 0.0
        if self._last_net is not None:
            interval_sec = max(now - float(self._last_net.get("at", now)), 0.0)
            if interval_sec > 0:
                sent_per_sec = max(sent - int(self._last_net.get("sent", sent)), 0) / interval_sec
                recv_per_sec = max(recv - int(self._last_net.get("recv", recv)), 0) / interval_sec
        self._last_net = {"at": now, "sent": sent, "recv": recv}
        return {
            "bytes_sent": sent,
            "bytes_recv": recv,
            "sent_per_sec": sent_per_sec,
            "recv_per_sec": recv_per_sec,
            "interval_sec": interval_sec,
        }

    def _hermes_process_metrics(self) -> dict[str, Any]:
        processes: list[dict[str, Any]] = []
        rss_total = 0
        cpu_total = 0.0
        wrapper_process_count = 0
        exceptions = (
            getattr(self.psutil_module, "NoSuchProcess", Exception),
            getattr(self.psutil_module, "AccessDenied", Exception),
            getattr(self.psutil_module, "ZombieProcess", Exception),
        )
        attrs = ["pid", "name", "cmdline", "memory_info", "create_time", "status"]
        for proc in self.psutil_module.process_iter(attrs):
            try:
                info = dict(getattr(proc, "info", {}) or {})
                cmdline = [str(part) for part in (info.get("cmdline") or [])]
                command = " ".join(cmdline)
                component = self._component_for_command(command)
                if not component:
                    continue
                if self._is_wrapper_process(info.get("name"), cmdline):
                    wrapper_process_count += 1
                    continue
                memory_info = info.get("memory_info")
                rss = int(getattr(memory_info, "rss", 0) or 0)
                cpu = self._safe_float(proc.cpu_percent(interval=None))
                item = {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or ""),
                    "component": component,
                    "command": self._short_command(command),
                    "cpu_percent": cpu,
                    "memory_rss_bytes": rss,
                    "memory_rss_mb": rss / 1024 / 1024,
                    "status": str(info.get("status") or ""),
                    "started_at": self._iso_from_ts(self._safe_float(info.get("create_time"))),
                }
            except exceptions:
                continue
            processes.append(item)
            rss_total += int(item["memory_rss_bytes"])
            cpu_total += float(item["cpu_percent"])

        processes.sort(key=lambda row: float(row.get("memory_rss_bytes") or 0), reverse=True)
        return {
            "process_count": len(processes),
            "wrapper_process_count": wrapper_process_count,
            "cpu_percent": cpu_total,
            "memory_rss_bytes": rss_total,
            "memory_rss_mb": rss_total / 1024 / 1024,
            "processes": processes[: max(int(self.process_limit), 1)],
        }

    @staticmethod
    def _component_for_command(command: str) -> str:
        lowered = command.lower()
        for component, patterns in _COMPONENT_PATTERNS:
            if any(pattern.lower() in lowered for pattern in patterns):
                return component
        return ""

    @staticmethod
    def _is_wrapper_process(name: Any, cmdline: list[str]) -> bool:
        process_name = str(name or "").lower().strip()
        executable = str(cmdline[0] if cmdline else "").lower().strip()
        executable_name = PurePath(executable).name
        wrapper_names = {"sh", "bash", "zsh", "fish", "tmux", "tee"}
        return process_name in wrapper_names or executable_name in wrapper_names

    @staticmethod
    def _short_command(command: str, *, limit: int = 160) -> str:
        cleaned = " ".join(str(command or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(limit - 1, 1)]}…"

    @staticmethod
    def _memory_payload(value: Any) -> dict[str, Any]:
        return {
            "total_bytes": int(getattr(value, "total", 0) or 0),
            "used_bytes": int(getattr(value, "used", 0) or 0),
            "available_bytes": int(
                getattr(value, "available", getattr(value, "free", 0)) or 0
            ),
            "percent": SystemMetricsService._safe_float(getattr(value, "percent", 0.0)),
        }

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _iso_from_ts(value: float) -> str:
        return datetime.fromtimestamp(float(value or 0.0), timezone.utc).isoformat()
