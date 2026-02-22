from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from tradecraft.services.freqtrade import FreqtradeBotConfig


@dataclass(slots=True)
class FreqtradeProcessManagerConfig:
    executable_path: str = "third_party/freqtrade/.venv-ft/bin/freqtrade"
    workdir: str = "third_party/freqtrade"
    runtime_dir: str = ".runtime/freqtrade"
    stop_timeout_sec: float = 8.0


class FreqtradeProcessManager:
    def __init__(
        self,
        config: FreqtradeProcessManagerConfig,
        bots: list[FreqtradeBotConfig],
    ) -> None:
        self.config = config
        self._bots = {bot.bot_id: bot for bot in bots}
        self._order = [bot.bot_id for bot in bots]
        self._lock = Lock()
        runtime_dir = self._resolve_path(self.config.runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self._default_usdt_limits = {
            bot_id: self._load_default_available_capital(bot.config_path)
            for bot_id, bot in self._bots.items()
        }
        self._usdt_limits = self._load_usdt_limits()

    def set_usdt_limit(self, bot_id: str, usdt_limit: float | None) -> dict[str, Any]:
        with self._lock:
            bot = self._require_bot(bot_id)
            if usdt_limit is None:
                self._usdt_limits.pop(bot_id, None)
            else:
                value = float(usdt_limit)
                if value <= 0:
                    raise RuntimeError("usdt_limit must be > 0")
                self._usdt_limits[bot_id] = value
            self._save_usdt_limits()
            status = self._status_unlocked(bot)
            return {
                "bot_id": bot.bot_id,
                "label": bot.label,
                "action": "usdt_limit_updated",
                "usdt_limit": status.get("usdt_limit"),
                "usdt_limit_default": status.get("usdt_limit_default"),
                "usdt_limit_source": status.get("usdt_limit_source"),
            }

    def get_effective_usdt_limit(self, bot_id: str) -> float | None:
        if bot_id in self._usdt_limits:
            return self._usdt_limits[bot_id]
        return self._default_usdt_limits.get(bot_id)

    def list_statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._status_unlocked(self._bots[bot_id])
                for bot_id in self._order
                if bot_id in self._bots
            ]

    def start(self, bot_id: str) -> dict[str, Any]:
        with self._lock:
            bot = self._require_bot(bot_id)
            return self._start_unlocked(bot)

    def stop(self, bot_id: str) -> dict[str, Any]:
        with self._lock:
            bot = self._require_bot(bot_id)
            return self._stop_unlocked(bot)

    def start_all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self._lock:
            for bot_id in self._order:
                bot = self._bots.get(bot_id)
                if not bot:
                    continue
                out.append(self._start_unlocked(bot))
        return out

    def stop_all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self._lock:
            for bot_id in self._order:
                bot = self._bots.get(bot_id)
                if not bot:
                    continue
                out.append(self._stop_unlocked(bot))
        return out

    def _start_unlocked(self, bot: FreqtradeBotConfig) -> dict[str, Any]:
        status = self._status_unlocked(bot)
        if status["running"]:
            return {
                "bot_id": bot.bot_id,
                "label": bot.label,
                "action": "already_running",
                "pid": status["pid"],
            }

        executable = self._resolve_path(self.config.executable_path)
        if not executable.exists():
            raise RuntimeError(f"freqtrade executable not found: {executable}")

        config_path = self._resolve_path(bot.config_path)
        if not config_path.exists():
            raise RuntimeError(f"config not found for {bot.bot_id}: {config_path}")

        workdir = self._resolve_path(self.config.workdir)
        if not workdir.exists() or not workdir.is_dir():
            raise RuntimeError(f"workdir not found: {workdir}")

        runtime_dir = self._resolve_path(self.config.runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path(bot.bot_id)
        usdt_limit = self.get_effective_usdt_limit(bot.bot_id)
        override_path = self._override_config_path(bot.bot_id)
        override_payload: dict[str, Any] = {}
        if override_path.exists():
            try:
                loaded = json.loads(override_path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            if isinstance(loaded, dict):
                override_payload = loaded

        cmd = [
            str(executable),
            "trade",
            "-c",
            str(config_path),
        ]
        if usdt_limit is not None and usdt_limit > 0:
            override_payload["available_capital"] = usdt_limit
        if override_payload:
            override_path.write_text(
                json.dumps(override_payload, ensure_ascii=True),
                encoding="utf-8",
            )
            cmd.extend(["-c", str(override_path)])
        cmd.extend(
            [
                "--logfile",
                str(log_path),
            ]
        )

        try:
            with log_path.open("ab") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(workdir),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            raise RuntimeError(f"failed to start {bot.bot_id}: {exc}") from exc

        pid = int(proc.pid)
        self._pid_path(bot.bot_id).write_text(str(pid), encoding="utf-8")
        time.sleep(0.15)
        running = self._is_pid_alive(pid)
        if not running:
            self._clear_pid(bot.bot_id)
        return {
            "bot_id": bot.bot_id,
            "label": bot.label,
            "action": "started" if running else "start_failed",
            "pid": pid if running else None,
        }

    def _stop_unlocked(self, bot: FreqtradeBotConfig) -> dict[str, Any]:
        pid = self._read_pid(bot.bot_id)
        if pid <= 0 or not self._is_pid_alive(pid):
            self._clear_pid(bot.bot_id)
            return {
                "bot_id": bot.bot_id,
                "label": bot.label,
                "action": "already_stopped",
                "pid": None,
            }

        forced = False
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._clear_pid(bot.bot_id)
            return {
                "bot_id": bot.bot_id,
                "label": bot.label,
                "action": "already_stopped",
                "pid": None,
            }

        deadline = time.monotonic() + max(float(self.config.stop_timeout_sec), 0.2)
        while time.monotonic() < deadline:
            if not self._is_pid_alive(pid):
                break
            time.sleep(0.15)

        if self._is_pid_alive(pid):
            forced = True
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self._clear_pid(bot.bot_id)
        return {
            "bot_id": bot.bot_id,
            "label": bot.label,
            "action": "stopped",
            "pid": None,
            "forced": forced,
        }

    def _status_unlocked(self, bot: FreqtradeBotConfig) -> dict[str, Any]:
        executable = self._resolve_path(self.config.executable_path)
        config_path = self._resolve_path(bot.config_path)
        pid = self._read_pid(bot.bot_id)
        running = pid > 0 and self._is_pid_alive(pid)
        if pid > 0 and not running:
            self._clear_pid(bot.bot_id)
            pid = 0

        return {
            "bot_id": bot.bot_id,
            "label": bot.label,
            "running": running,
            "pid": pid if running else None,
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            "executable_path": str(executable),
            "executable_exists": executable.exists(),
            "log_path": str(self._log_path(bot.bot_id)),
            "usdt_limit": self.get_effective_usdt_limit(bot.bot_id),
            "usdt_limit_default": self._default_usdt_limits.get(bot.bot_id),
            "usdt_limit_source": (
                "override" if bot.bot_id in self._usdt_limits else "config"
            ),
        }

    def _require_bot(self, bot_id: str) -> FreqtradeBotConfig:
        bot = self._bots.get(bot_id)
        if not bot:
            raise KeyError(f"unknown bot_id: {bot_id}")
        return bot

    def _pid_path(self, bot_id: str) -> Path:
        return self._resolve_path(self.config.runtime_dir) / f"{bot_id}.pid"

    def _log_path(self, bot_id: str) -> Path:
        return self._resolve_path(self.config.runtime_dir) / f"{bot_id}.log"

    def _override_config_path(self, bot_id: str) -> Path:
        return self._resolve_path(self.config.runtime_dir) / f"{bot_id}.override.json"

    def _usdt_limits_path(self) -> Path:
        return self._resolve_path(self.config.runtime_dir) / "usdt_limits.json"

    def _read_pid(self, bot_id: str) -> int:
        path = self._pid_path(bot_id)
        if not path.exists():
            return 0
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0

    def _clear_pid(self, bot_id: str) -> None:
        path = self._pid_path(bot_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _load_usdt_limits(self) -> dict[str, float]:
        path = self._usdt_limits_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, float] = {}
        for key, value in payload.items():
            bot_id = str(key).strip()
            if not bot_id:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if num > 0:
                out[bot_id] = num
        return out

    def _save_usdt_limits(self) -> None:
        path = self._usdt_limits_path()
        payload = {
            key: value for key, value in self._usdt_limits.items() if float(value) > 0
        }
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    def _load_default_available_capital(self, config_path: str) -> float | None:
        path = self._resolve_path(config_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        raw = payload.get("available_capital")
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _resolve_path(path_value: str) -> Path:
        path = Path(path_value.strip()) if path_value else Path("")
        if not path:
            return Path.cwd()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
