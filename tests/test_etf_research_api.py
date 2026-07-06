from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.config import AppSettings
from tradecraft.services.etf_research import ETFResearchRepository, ETFUniverseItem


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def _configure_etf_research(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "etf_research.db"
    monkeypatch.setattr(main.settings, "etf_research_db_path", str(db_path))
    monkeypatch.setattr(
        main.settings,
        "etf_research_universe",
        "069500:KODEX 200,102110:TIGER 200",
    )
    monkeypatch.setattr(main.settings, "etf_research_max_symbols", 2)
    monkeypatch.setattr(main.settings, "etf_research_auto_collect", False)
    monkeypatch.setattr(main, "_ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT", None)
    return db_path


def test_etf_research_endpoints_require_admin_token(monkeypatch, tmp_path: Path) -> None:
    _configure_etf_research(monkeypatch, tmp_path)
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")

    with TestClient(main.app) as client:
        status_response = client.get("/api/etf/research/status")
        candidates_response = client.get("/api/etf/research/candidates")
        collect_response = client.post("/api/etf/research/collect")

    assert status_response.status_code == 401
    assert candidates_response.status_code == 401
    assert collect_response.status_code == 401


def test_etf_research_status_and_candidates_use_configured_universe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _configure_etf_research(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        status_response = client.get("/api/etf/research/status", headers=headers)
        candidates_response = client.get("/api/etf/research/candidates", headers=headers)

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "ok"
    assert status_payload["db_path"] == str(db_path)
    assert status_payload["max_symbols"] == 2
    configured_symbols = [
        item["symbol"] for item in status_payload["configured_universe"]
    ]
    assert configured_symbols[:2] == ["069500", "102110"]

    assert candidates_response.status_code == 200
    candidates_payload = candidates_response.json()
    assert candidates_payload["status"] == "ok"
    candidate_symbols = [item["symbol"] for item in candidates_payload["items"]]
    assert candidate_symbols[:2] == ["069500", "102110"]
    assert candidates_payload["items"][0]["latest_snapshot"]["status"] == "missing"
    assert candidates_payload["items"][0]["latest_score"]["label"] == "unknown"
    assert candidates_payload["auto_collect"]["reason"] == "read_only_endpoint"


def test_etf_research_candidates_filter_stale_db_universe_rows(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _configure_etf_research(monkeypatch, tmp_path)
    repo = ETFResearchRepository(str(db_path))
    repo.upsert_universe(
        [
            ETFUniverseItem(symbol="069500", name="KODEX 200"),
            ETFUniverseItem(symbol="102110", name="TIGER 200"),
            ETFUniverseItem(symbol="999999", name="STALE ETF"),
        ]
    )
    monkeypatch.setattr(main.settings, "etf_research_universe", "069500:KODEX 200")

    with TestClient(main.app) as client:
        response = client.get(
            "/api/etf/research/candidates",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    symbols = [item["symbol"] for item in response.json()["items"]]
    assert symbols[0] == "069500"
    assert "999999" not in symbols


def test_etf_research_collect_rejects_non_list_symbols(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_etf_research(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/etf/research/collect",
            json={"symbols": "069500"},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "symbols must be a list"


def test_etf_research_collect_rejects_invalid_symbol_in_list(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_etf_research(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/etf/research/collect",
            json={"symbols": ["069500", "bad"]},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "symbols must contain 6 digit codes"


def test_etf_research_collect_rejects_empty_symbol_list(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_etf_research(monkeypatch, tmp_path)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/etf/research/collect",
            json={"symbols": []},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "symbols must not be empty"


def test_etf_research_collect_stores_ok_and_error_results(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_etf_research(monkeypatch, tmp_path)

    class FakeKIS:
        async def fetch_domestic_quote(self, symbol: str) -> dict[str, Any]:
            if symbol == "102110":
                raise RuntimeError("quote timeout")
            return {
                "symbol": symbol,
                "name": "KODEX 200",
                "price": 42150.0,
                "raw": {
                    "stck_prpr": "42150",
                    "prdy_ctrt": "1.80",
                    "acml_vol": "120000",
                    "acml_tr_pbmn": "5058000000",
                },
            }

    monkeypatch.setattr(main, "kis_primary", FakeKIS())

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        collect_response = client.post(
            "/api/etf/research/collect",
            json={"symbols": ["069500", "102110"], "force": True},
            headers=headers,
        )
        candidates_response = client.get("/api/etf/research/candidates", headers=headers)

    assert collect_response.status_code == 200
    payload = collect_response.json()
    assert payload["status"] == "partial"
    assert payload["requested"] == ["069500", "102110"]
    assert payload["collected"] == 1
    assert payload["errors"] == [{"symbol": "102110", "error": "quote timeout"}]
    assert [item["snapshot"]["status"] for item in payload["items"]] == ["ok", "error"]

    assert candidates_response.status_code == 200
    candidates = {item["symbol"]: item for item in candidates_response.json()["items"]}
    assert candidates["069500"]["latest_snapshot"]["raw"]["stck_prpr"] == "42150"
    assert candidates["069500"]["latest_score"]["liquidity_score"] > 0
    assert candidates["102110"]["latest_snapshot"]["status"] == "error"
    assert candidates["102110"]["latest_score"]["label"] == "unknown"
    assert "quote timeout" in candidates["102110"]["latest_score"]["risks"]


def test_etf_research_get_candidates_skips_auto_collect_until_explicit_post(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_etf_research(monkeypatch, tmp_path)
    monkeypatch.setattr(main.settings, "etf_research_auto_collect", True)
    monkeypatch.setattr(main.settings, "etf_research_auto_min_interval_sec", 0)
    monkeypatch.setattr(main.settings, "etf_research_stale_sec", 1800)

    class FakeKIS:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch_domestic_quote(self, symbol: str) -> dict[str, Any]:
            self.calls.append(symbol)
            return {
                "symbol": symbol,
                "name": symbol,
                "price": 42150.0 if symbol == "069500" else 39720.0,
                "raw": {
                    "hts_kor_isnm": symbol,
                    "stck_prpr": "42150" if symbol == "069500" else "39720",
                    "prdy_ctrt": "1.80",
                    "acml_vol": "120000",
                    "acml_tr_pbmn": "5058000000",
                },
            }

    fake_kis = FakeKIS()
    monkeypatch.setattr(main, "kis_primary", fake_kis)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        response = client.get(
            "/api/etf/research/candidates",
            headers=headers,
        )
        status_response = client.get(
            "/api/etf/research/status",
            headers=headers,
        )
        collect_response = client.post(
            "/api/etf/research/collect",
            json={"symbols": ["069500", "102110"], "force": True},
            headers=headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["auto_collect"] == {
        "status": "skipped",
        "reason": "read_only_endpoint",
        "auto": False,
    }
    candidates = {item["symbol"]: item for item in payload["items"]}
    assert candidates["069500"]["name"] == "KODEX 200"
    assert candidates["069500"]["latest_snapshot"]["status"] == "missing"
    assert candidates["069500"]["latest_score"]["label"] == "unknown"
    assert status_response.status_code == 200
    assert status_response.json()["auto_collect"] == {
        "status": "skipped",
        "reason": "read_only_endpoint",
        "auto": False,
    }
    assert fake_kis.calls == ["069500", "102110"]
    assert collect_response.status_code == 200
    assert collect_response.json()["collected"] == 2


def test_etf_research_config_env_fields(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "configured.db"
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_DB_PATH", str(db_path))
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_UNIVERSE", "123456:TEST ETF")
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_MAX_SYMBOLS", "7")
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_AUTO_COLLECT", "false")
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_STALE_SEC", "900")
    monkeypatch.setenv("TRADECRAFT_ETF_RESEARCH_AUTO_MIN_INTERVAL_SEC", "120")

    settings = AppSettings()

    assert settings.etf_research_db_path == str(db_path)
    assert settings.etf_research_universe == "123456:TEST ETF"
    assert settings.etf_research_max_symbols == 7
    assert settings.etf_research_auto_collect is False
    assert settings.etf_research_stale_sec == 900
    assert settings.etf_research_auto_min_interval_sec == 120
