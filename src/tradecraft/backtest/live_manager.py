from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from tradecraft.backtest.engine import BacktestConfig, BacktestEngine
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BacktestLiveManager:
    def __init__(self, state_path: str, result_path: str, max_curve_points: int = 4000) -> None:
        self.state_store = RuntimeStateStore(state_path)
        self.result_store = RuntimeStateStore(result_path)
        self.max_curve_points = max(int(max_curve_points), 200)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state: dict[str, Any] = {
            "updated_at": utc_now_iso(),
            "job": {"status": "idle"},
            "progress": {},
            "result": None,
        }

    def _set_state(self, payload: dict[str, Any]) -> None:
        self._state = payload
        self.state_store.write_snapshot(payload)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def is_running(self) -> bool:
        with self._lock:
            return str(self._state.get("job", {}).get("status") or "") == "running"

    def start(
        self,
        session_rows: list[dict[str, Any]],
        config: BacktestConfig,
        scenario: str,
        session_source: str,
        emit_interval: int = 1,
    ) -> dict[str, Any]:
        with self._lock:
            if str(self._state.get("job", {}).get("status") or "") == "running":
                raise RuntimeError("backtest job is already running")

            job_id = uuid.uuid4().hex[:12]
            payload = {
                "updated_at": utc_now_iso(),
                "job": {
                    "id": job_id,
                    "status": "running",
                    "scenario": scenario,
                    "session_source": session_source,
                    "started_at": _utc_iso_now(),
                    "finished_at": None,
                    "error": "",
                },
                "config": {
                    "cycles": int(config.cycles),
                    "step_sec": int(config.step_sec),
                    "speed": float(config.speed),
                    "fee_rate": float(config.fee_rate),
                    "slippage_bps": float(config.slippage_bps),
                    "drift_bps": float(config.drift_bps),
                    "volatility_bps": float(config.volatility_bps),
                    "seed": int(config.seed),
                },
                "progress": {
                    "cycle": 0,
                    "total_cycles": max(int(config.cycles), 1),
                    "progress_pct": 0.0,
                    "equity_curve": [],
                    "updated_at": _utc_iso_now(),
                },
                "result": None,
            }
            self._stop_event.clear()
            self._set_state(payload)

            thread = threading.Thread(
                target=self._run_job,
                args=(session_rows, config, emit_interval),
                name=f"backtest-{job_id}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return {"job_id": job_id, "status": "running"}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if str(self._state.get("job", {}).get("status") or "") != "running":
                return {"ok": False, "detail": "no running backtest"}
            self._stop_event.set()
            return {"ok": True, "detail": "stop requested"}

    def _append_curve(self, point: dict[str, Any]) -> None:
        curve = list((self._state.get("progress") or {}).get("equity_curve") or [])
        curve.append(point)
        if len(curve) > self.max_curve_points:
            trim = len(curve) - self.max_curve_points
            curve = curve[trim:]
        self._state["progress"]["equity_curve"] = curve

    def _run_job(self, session_rows: list[dict[str, Any]], config: BacktestConfig, emit_interval: int) -> None:
        try:
            engine = BacktestEngine.from_session_rows(rows=session_rows, config=config)
            total_cycles = max(int(config.cycles), 1)

            def on_cycle(progress: dict[str, Any]) -> None:
                with self._lock:
                    aggregate = progress.get("aggregate") or {}
                    point = {
                        "cycle": int(progress.get("cycle") or 0),
                        "time": str(progress.get("time") or ""),
                        "net_pnl_krw": float(aggregate.get("net_pnl_krw") or 0.0),
                    }
                    self._append_curve(point)
                    self._state["updated_at"] = utc_now_iso()
                    self._state["progress"]["cycle"] = int(progress.get("cycle") or 0)
                    self._state["progress"]["total_cycles"] = total_cycles
                    self._state["progress"]["progress_pct"] = round(
                        (self._state["progress"]["cycle"] / total_cycles) * 100.0,
                        2,
                    )
                    self._state["progress"]["aggregate"] = aggregate
                    self._state["progress"]["sessions"] = list(progress.get("sessions") or [])
                    self._state["progress"]["updated_at"] = _utc_iso_now()
                    self.state_store.write_snapshot(self._state)

            result = engine.run(
                on_cycle=on_cycle,
                emit_interval=max(int(emit_interval), 1),
                stop_event=self._stop_event,
            )
            self.result_store.write_snapshot(result)

            with self._lock:
                self._state["updated_at"] = utc_now_iso()
                self._state["job"]["status"] = str(result.get("backtest", {}).get("status") or "completed")
                self._state["job"]["finished_at"] = _utc_iso_now()
                self._state["job"]["error"] = ""
                self._state["result"] = result
                progress = self._state.setdefault("progress", {})
                done_cycles = int(result.get("backtest", {}).get("completed_cycles") or 0)
                total_cycles = max(int(result.get("backtest", {}).get("cycles") or 0), 1)
                progress["cycle"] = done_cycles
                progress["total_cycles"] = total_cycles
                progress["progress_pct"] = round((done_cycles / total_cycles) * 100.0, 2)
                self.state_store.write_snapshot(self._state)
        except Exception as exc:
            with self._lock:
                self._state["updated_at"] = utc_now_iso()
                self._state["job"]["status"] = "failed"
                self._state["job"]["finished_at"] = _utc_iso_now()
                self._state["job"]["error"] = f"{type(exc).__name__}: {exc}"
                self._state["result"] = {
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=5),
                }
                self.state_store.write_snapshot(self._state)
        finally:
            with self._lock:
                self._thread = None
