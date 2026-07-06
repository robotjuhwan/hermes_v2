from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.static import StaticRouteDeps, build_static_router


def test_static_root_serves_configured_index_html(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><title>HERMES</title>",
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        build_static_router(
            StaticRouteDeps(
                static_dir=lambda: static_dir,
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "HERMES" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_static_icon_fallbacks_avoid_browser_404_noise(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = FastAPI()
    app.include_router(
        build_static_router(
            StaticRouteDeps(
                static_dir=lambda: static_dir,
            )
        )
    )

    with TestClient(app) as client:
        favicon = client.get("/favicon.ico")
        apple = client.get("/apple-touch-icon.png")
        apple_precomposed = client.get("/apple-touch-icon-precomposed.png")

    assert favicon.status_code == 204
    assert apple.status_code == 204
    assert apple_precomposed.status_code == 204
