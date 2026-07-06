from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.etf import (
    ETFRouteDeps,
    build_etf_router,
    etf_auto_collect_skipped,
    etf_read_only_auto_collect,
    etf_symbols_from_payload,
    etf_universe_item_payload,
)


class _Repo:
    def __init__(self) -> None:
        self.seeded = False

    def status(self) -> dict[str, Any]:
        return {"snapshot_count": 1, "score_count": 2}

    def list_universe(self) -> list[dict[str, Any]]:
        return [{"symbol": "069500", "name": "KODEX 200"}]


def _client(repo: _Repo, calls: list[tuple[str, Any]]) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_etf_router(
            ETFRouteDeps(
                require_admin_auth=lambda: None,
                repository_factory=lambda: repo,
                configured_universe=lambda: [{"symbol": "069500", "name": "KODEX 200"}],
                expanded_universe=lambda configured: configured,
                universe_item_payload=lambda item: dict(item),
                settings_payload=lambda: {"db_path": "etf.db", "max_symbols": 3},
                list_candidates=lambda repository, universe: [
                    {"symbol": universe[0]["symbol"], "latest_score": {"label": "unknown"}}
                ],
                read_only_auto_collect=lambda: {
                    "status": "skipped",
                    "reason": "read_only_endpoint",
                    "auto": False,
                },
                seed_universe=lambda repository: calls.append(("seed", {}))
                or [{"symbol": "069500", "name": "KODEX 200"}],
                symbols_from_payload=lambda payload, universe: ["069500"],
                collect_snapshots=lambda **kwargs: calls.append(("collect", kwargs))
                or {"status": "ok", "requested": kwargs["symbols"]},
                fetch_quote=lambda symbol: {"symbol": symbol},
            )
        )
    )
    return TestClient(app)


def test_etf_router_status_and_candidates_are_read_only() -> None:
    repo = _Repo()
    calls: list[tuple[str, Any]] = []

    with _client(repo, calls) as client:
        status = client.get("/api/etf/research/status")
        candidates = client.get("/api/etf/research/candidates")

    assert status.status_code == 200
    assert status.json()["db_path"] == "etf.db"
    assert status.json()["configured_universe"][0]["symbol"] == "069500"
    assert candidates.json()["items"][0]["symbol"] == "069500"
    assert calls == []


def test_etf_router_collect_seeds_and_delegates_collection() -> None:
    repo = _Repo()
    calls: list[tuple[str, Any]] = []

    with _client(repo, calls) as client:
        response = client.post("/api/etf/research/collect", json={"force": True})

    assert response.status_code == 200
    assert response.json()["requested"] == ["069500"]
    assert calls[0] == ("seed", {})
    assert calls[1][0] == "collect"
    assert calls[1][1]["repository"] is repo
    assert calls[1][1]["symbols"] == ["069500"]
    assert calls[1][1]["force"] is True


def test_etf_api_payload_helpers_are_owned_by_etf_router_module() -> None:
    item = type(
        "ETFItem",
        (),
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "category": "core",
            "tags": ["configured"],
        },
    )()

    assert etf_universe_item_payload(item) == {
        "symbol": "069500",
        "name": "KODEX 200",
        "category": "core",
        "tags": ["configured"],
    }
    assert etf_read_only_auto_collect() == {
        "status": "skipped",
        "reason": "read_only_endpoint",
        "auto": False,
    }
    assert etf_auto_collect_skipped("fresh") == {
        "status": "skipped",
        "reason": "fresh",
        "auto": True,
    }


def test_etf_symbols_from_payload_defaults_dedupes_and_rejects_bad_symbols() -> None:
    universe = [
        {"symbol": "069500"},
        {"symbol": "102110"},
        {"symbol": "091160"},
    ]

    assert etf_symbols_from_payload(None, universe, max_symbols=2) == [
        "069500",
        "102110",
    ]
    assert etf_symbols_from_payload(
        {"symbols": ["069500", "069500", "102110"]},
        universe,
        max_symbols=10,
    ) == ["069500", "102110"]

    with pytest.raises(HTTPException) as non_list:
        etf_symbols_from_payload({"symbols": "069500"}, universe, max_symbols=2)
    assert non_list.value.status_code == 400
    assert non_list.value.detail == "symbols must be a list"

    with pytest.raises(HTTPException) as empty:
        etf_symbols_from_payload({"symbols": []}, universe, max_symbols=2)
    assert empty.value.detail == "symbols must not be empty"

    with pytest.raises(HTTPException) as invalid:
        etf_symbols_from_payload({"symbols": ["ABC"]}, universe, max_symbols=2)
    assert invalid.value.detail == "symbols must contain 6 digit codes"
