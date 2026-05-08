from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Callable

from tradecraft.config import AppSettings
from tradecraft.reports_api.ops import build_deployment_checks

logger = logging.getLogger(__name__)


def _is_truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _project_root() -> Path:
    # /.../src/tradecraft/reports_api/stack.py -> project root is parents[3]
    return Path(__file__).resolve().parents[3]


def _ui_dir(root: Path) -> Path:
    return root / "web" / "reports-console"


def _ui_dist_index() -> Path:
    return Path(__file__).resolve().parent / "web_dist" / "index.html"


def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    logger.info("running: %s (cwd=%s)", " ".join(cmd), str(cwd))
    run_fn(
        cmd,
        cwd=str(cwd),
        check=True,
        text=True,
    )


def ensure_ui_built(
    *,
    root: Path,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if _is_truthy(os.getenv("TRADECRAFT_REPORTS_UI_BUILD_SKIP", "")):
        logger.info("skip UI build: TRADECRAFT_REPORTS_UI_BUILD_SKIP=true")
        return False

    ui_dir = _ui_dir(root)
    dist_index = _ui_dist_index()
    force_build = _is_truthy(os.getenv("TRADECRAFT_REPORTS_UI_BUILD_FORCE", ""))

    if not ui_dir.exists():
        raise RuntimeError(f"UI project not found: {ui_dir}")
    if dist_index.exists() and not force_build:
        logger.info("UI dist already exists: %s", dist_index)
        return False

    npm_cmd = os.getenv("TRADECRAFT_REPORTS_NPM_CMD", "npm").strip() or "npm"
    node_modules_dir = ui_dir / "node_modules"
    if not node_modules_dir.exists():
        _run_command([npm_cmd, "install"], cwd=ui_dir, run_fn=run_fn)
    _run_command([npm_cmd, "run", "build"], cwd=ui_dir, run_fn=run_fn)
    return True


def _terminate_process(proc: subprocess.Popen[str], name: str) -> None:
    if proc.poll() is not None:
        return
    logger.info("stopping %s pid=%s", name, proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("force kill %s pid=%s", name, proc.pid)
        proc.kill()
        proc.wait(timeout=5)


def run() -> None:
    settings = AppSettings()
    checks = build_deployment_checks(settings, require_worker=True)
    if checks["status"] == "error":
        details = "; ".join(
            item["detail"] for item in checks["issues"] if item["level"] == "error"
        )
        raise RuntimeError(f"reports stack preflight failed: {details}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    for issue in checks["issues"]:
        log_fn = logger.warning if issue["level"] == "warn" else logger.info
        log_fn("reports stack preflight %s: %s", issue["code"], issue["detail"])

    root = _project_root()
    ensure_ui_built(root=root)

    api_url = f"http://{settings.reports_api_host}:{settings.reports_api_port}"
    logger.info("starting reports stack at %s", api_url)

    api_proc = subprocess.Popen(
        [sys.executable, "-m", "tradecraft.reports_api.main"],
        cwd=str(root),
        text=True,
    )
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "tradecraft.reports_api.worker"],
        cwd=str(root),
        text=True,
    )
    processes = [("api", api_proc), ("worker", worker_proc)]

    stop_requested = False

    def _signal_handler(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        nonlocal stop_requested
        logger.info("signal received: %s", signum)
        stop_requested = True

    old_sigint = signal.signal(signal.SIGINT, _signal_handler)
    old_sigterm = signal.signal(signal.SIGTERM, _signal_handler)

    exit_code = 0
    try:
        while not stop_requested:
            for name, proc in processes:
                rc = proc.poll()
                if rc is None:
                    continue
                if rc != 0:
                    logger.error("%s exited with code %s", name, rc)
                    exit_code = int(rc)
                else:
                    logger.info("%s exited", name)
                stop_requested = True
                break
            time.sleep(0.6)
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        for name, proc in processes:
            _terminate_process(proc, name)

    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
