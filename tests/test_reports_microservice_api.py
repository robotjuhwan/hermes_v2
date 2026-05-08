from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tradecraft.reports_api import main as reports_main


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_v1_reports_search_passes_extended_filters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _search(
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, object]]:
        captured.update(
            {
                "query": query,
                "symbol": symbol,
                "category": category,
                "broker": broker,
                "analyst": analyst,
                "date_from": date_from,
                "date_to": date_to,
                "limit": limit,
            }
        )
        return [
            {
                "report_id": 11,
                "category": category,
                "title": "테스트",
                "company_name": "삼성전자",
                "broker": broker,
                "analyst": analyst,
                "symbol": symbol,
                "published_at": "2026-02-10",
                "updated_at": "2026-02-11T00:00:00+00:00",
                "snippet": "summary",
            }
        ]

    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(reports_main.repository, "search", _search)

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/v1/reports/search",
            params={
                "query": "반도체",
                "symbol": "005930",
                "category": "company_analysis",
                "broker": "테스트증권",
                "analyst": "홍길동",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "limit": 15,
            },
            headers=_auth_header("secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert captured == {
        "query": "반도체",
        "symbol": "005930",
        "category": "company_analysis",
        "broker": "테스트증권",
        "analyst": "홍길동",
        "date_from": "2026-01-01",
        "date_to": "2026-12-31",
        "limit": 15,
    }


def test_v1_reports_detail_with_chunks(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(
        reports_main.repository,
        "get_report",
        lambda report_id: {
            "report_id": int(report_id),
            "title": "삼성전자 리포트",
            "symbol": "005930",
            "content": "본문",
        },
    )
    monkeypatch.setattr(
        reports_main.repository,
        "get_report_facts",
        lambda report_id: {"rating": "BUY"} if int(report_id) == 7 else None,
    )
    monkeypatch.setattr(
        reports_main.repository,
        "list_report_chunks",
        lambda report_id, limit: [
            {
                "chunk_id": 1,
                "report_id": int(report_id),
                "chunk_index": 0,
                "content": "요약",
            }
        ],
    )

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/v1/reports/7",
            params={"include_chunks": True, "chunk_limit": 100},
            headers=_auth_header("secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["report_id"] == 7
    assert payload["facts"]["rating"] == "BUY"
    assert payload["chunks"][0]["chunk_index"] == 0


def test_v1_reports_detail_not_found(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(reports_main.repository, "get_report", lambda report_id: None)

    with TestClient(reports_main.app) as client:
        response = client.get("/v1/reports/404", headers=_auth_header("secret"))

    assert response.status_code == 404


def test_v1_reports_status_includes_quality_payload(monkeypatch) -> None:
    fresh_now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(
        reports_main.repository,
        "status",
        lambda: {
            "total_reports": 20,
            "last_updated_at": fresh_now,
            "last_published_at": "2026-04-05",
            "category_counts": {"company_analysis": 20},
            "total_symbols": 15,
            "symbol_last_updated_at": fresh_now,
            "quality": {
                "missing_company_name_count": 0,
                "html_company_name_count": 2,
                "missing_symbol_count": 1,
                "missing_broker_count": 0,
                "missing_analyst_count": 0,
                "unknown_category_count": 0,
                "symbol_directory_drift_count": 3,
            },
        },
    )

    with TestClient(reports_main.app) as client:
        response = client.get("/v1/reports/status", headers=_auth_header("secret"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["quality"]["status"] == "warn"
    codes = {item["code"] for item in payload["quality"]["issues"]}
    assert "html_company_name" in codes
    assert "missing_symbol" in codes
    assert "symbol_directory_drift" in codes
