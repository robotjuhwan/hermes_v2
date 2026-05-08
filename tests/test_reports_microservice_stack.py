from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tradecraft.reports_api import stack


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, cmd, cwd, check, text):  # type: ignore[no-untyped-def]
        assert check is True
        assert text is True
        self.calls.append((list(cmd), str(cwd)))
        return subprocess.CompletedProcess(cmd, 0)


def test_is_truthy_variants() -> None:
    assert stack._is_truthy("true")
    assert stack._is_truthy("YES")
    assert stack._is_truthy("1")
    assert not stack._is_truthy("0")
    assert not stack._is_truthy("")


def test_ensure_ui_built_skip(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    (root / "web" / "reports-console").mkdir(parents=True)

    monkeypatch.setenv("TRADECRAFT_REPORTS_UI_BUILD_SKIP", "true")
    monkeypatch.setattr(stack, "_ui_dist_index", lambda: root / "dist" / "index.html")

    runner = _Runner()
    built = stack.ensure_ui_built(root=root, run_fn=runner)

    assert built is False
    assert runner.calls == []


def test_ensure_ui_built_runs_install_and_build(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    ui_dir = root / "web" / "reports-console"
    ui_dir.mkdir(parents=True)

    dist_index = root / "fake-dist" / "index.html"
    monkeypatch.delenv("TRADECRAFT_REPORTS_UI_BUILD_SKIP", raising=False)
    monkeypatch.delenv("TRADECRAFT_REPORTS_UI_BUILD_FORCE", raising=False)
    monkeypatch.delenv("TRADECRAFT_REPORTS_NPM_CMD", raising=False)
    monkeypatch.setattr(stack, "_ui_dist_index", lambda: dist_index)

    runner = _Runner()
    built = stack.ensure_ui_built(root=root, run_fn=runner)

    assert built is True
    assert runner.calls == [
        (["npm", "install"], str(ui_dir)),
        (["npm", "run", "build"], str(ui_dir)),
    ]


def test_ensure_ui_built_skips_when_dist_exists(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    ui_dir = root / "web" / "reports-console"
    ui_dir.mkdir(parents=True)

    dist_index = root / "fake-dist" / "index.html"
    dist_index.parent.mkdir(parents=True)
    dist_index.write_text("ok", encoding="utf-8")

    monkeypatch.delenv("TRADECRAFT_REPORTS_UI_BUILD_SKIP", raising=False)
    monkeypatch.delenv("TRADECRAFT_REPORTS_UI_BUILD_FORCE", raising=False)
    monkeypatch.setattr(stack, "_ui_dist_index", lambda: dist_index)

    runner = _Runner()
    built = stack.ensure_ui_built(root=root, run_fn=runner)

    assert built is False
    assert runner.calls == []


def test_ensure_ui_built_force_runs_build_only_when_node_modules_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path
    ui_dir = root / "web" / "reports-console"
    node_modules = ui_dir / "node_modules"
    node_modules.mkdir(parents=True)

    dist_index = root / "fake-dist" / "index.html"
    dist_index.parent.mkdir(parents=True)
    dist_index.write_text("ok", encoding="utf-8")

    monkeypatch.setenv("TRADECRAFT_REPORTS_UI_BUILD_FORCE", "true")
    monkeypatch.setattr(stack, "_ui_dist_index", lambda: dist_index)

    runner = _Runner()
    built = stack.ensure_ui_built(root=root, run_fn=runner)

    assert built is True
    assert runner.calls == [(["npm", "run", "build"], str(ui_dir))]


def test_stack_run_fails_fast_when_worker_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_REPORTS_API_TOKEN", "secret")
    monkeypatch.delenv("TRADECRAFT_REPORTS_API_TOKENS", raising=False)
    monkeypatch.setenv("TRADECRAFT_NAVER_REPORTS_ENABLED", "false")

    with pytest.raises(RuntimeError, match="TRADECRAFT_NAVER_REPORTS_ENABLED"):
        stack.run()
