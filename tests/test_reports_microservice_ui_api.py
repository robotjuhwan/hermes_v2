from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tradecraft.reports_api import main as reports_main
from tradecraft.reports_api.saved_views import ReportsSavedViewStore


def _auth_header(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(reports_main.settings, "reports_api_tokens", "")
    return {"Authorization": "Bearer secret"}


def test_ui_overview_allowed_for_local(monkeypatch) -> None:
    fresh_now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(
        reports_main.repository,
        "status",
        lambda: {
            "total_reports": 3,
            "last_updated_at": fresh_now,
            "last_published_at": "2026-02-24",
            "category_counts": {"company_analysis": 3},
            "total_symbols": 2,
            "symbol_last_updated_at": fresh_now,
            "quality": {
                "missing_company_name_count": 0,
                "html_company_name_count": 1,
                "missing_symbol_count": 0,
                "missing_broker_count": 0,
                "missing_analyst_count": 0,
                "unknown_category_count": 0,
                "symbol_directory_drift_count": 0,
            },
            "db_path": ".runtime/naver_reports.db",
        },
    )

    with TestClient(reports_main.app) as client:
        response = client.get("/ui-api/overview", headers=_auth_header(monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["reports"]["total_reports"] == 3
    assert payload["quality"]["status"] == "warn"
    assert "worker" in payload
    assert "readiness" in payload


def test_ui_api_blocks_non_allowed_forwarded_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "10.0.0.0/8",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", True)

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/ui-api/overview",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )

    assert response.status_code == 403


def test_ui_api_requires_bearer_token_for_allowed_local_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(reports_main.settings, "reports_api_tokens", "")

    with TestClient(reports_main.app) as client:
        response = client.get("/ui-api/overview")

    assert response.status_code == 401


def test_ui_action_crawl_once(monkeypatch) -> None:
    async def _crawl_once() -> dict[str, object]:
        return {
            "status": "ok",
            "inserted": 1,
            "repository": {"total_reports": 11},
        }

    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(reports_main.settings, "rag_enabled", False)
    monkeypatch.setattr(reports_main.crawler, "crawl_once", _crawl_once)

    with TestClient(reports_main.app) as client:
        response = client.post(
            "/ui-api/actions/crawl-once",
            json={},
            headers=_auth_header(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["inserted"] == 1


def test_ui_report_detail_with_chunks(monkeypatch) -> None:
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(
        reports_main.repository,
        "get_report",
        lambda report_id: {
            "report_id": int(report_id),
            "title": "삼성전자 리포트",
            "detail_url": "https://example.com/report/7",
            "symbol": "005930",
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
                "content": "핵심 요약",
                "section_title": "summary",
            }
        ],
    )

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/ui-api/reports/7",
            params={"include_chunks": True},
            headers=_auth_header(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["report_id"] == 7
    assert payload["facts"]["rating"] == "BUY"
    assert payload["chunks"][0]["chunk_index"] == 0


def test_ui_saved_views_crud_and_alert_preview(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(
        reports_main,
        "saved_view_store",
        ReportsSavedViewStore(str(tmp_path / "saved-views.json")),
    )

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
        _ = (category, analyst, date_from, date_to)
        return [
            {
                "report_id": 17,
                "title": "반도체 업데이트",
                "company_name": "삼성전자",
                "broker": broker or "테스트증권",
                "symbol": symbol or "005930",
                "published_at": "2026-04-04",
                "updated_at": "2026-04-05T00:00:00+00:00",
                "category": "company_analysis",
                "snippet": f"{query}-{limit}",
            }
        ]

    monkeypatch.setattr(reports_main.repository, "search", _search)

    with TestClient(reports_main.app) as client:
        headers = _auth_header(monkeypatch)
        create = client.post(
            "/ui-api/saved-views",
            json={
                "name": "반도체 모니터",
                "filters": {
                    "query": "반도체",
                    "symbol": "005930",
                    "broker": "테스트증권",
                    "limit": 12,
                },
                "alert": {"enabled": True, "channel": "telegram", "target": "123"},
            },
            headers=headers,
        )
        assert create.status_code == 200
        view_id = create.json()["view"]["view_id"]

        listing = client.get("/ui-api/saved-views", headers=headers)
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["count"] == 1
        assert payload["items"][0]["name"] == "반도체 모니터"
        assert payload["items"][0]["filters"]["broker"] == "테스트증권"

        preview = client.post(
            f"/ui-api/saved-views/{view_id}/alert-preview",
            json={"limit": 3},
            headers=headers,
        )
        assert preview.status_code == 200
        preview_payload = preview.json()
        assert preview_payload["count"] == 1
        assert "반도체 모니터" in preview_payload["message"]
        assert "삼성전자" in preview_payload["message"]

        deleted = client.delete(f"/ui-api/saved-views/{view_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        empty_listing = client.get("/ui-api/saved-views", headers=headers)
        assert empty_listing.json()["count"] == 0


def test_ui_saved_view_alert_test(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        reports_main.settings,
        "reports_ui_allowed_cidrs",
        "127.0.0.1/32",
    )
    monkeypatch.setattr(reports_main.settings, "reports_ui_trust_proxy", False)
    monkeypatch.setattr(
        reports_main,
        "saved_view_store",
        ReportsSavedViewStore(str(tmp_path / "saved-views.json")),
    )
    monkeypatch.setattr(
        reports_main.repository,
        "search",
        lambda **_: [
            {
                "report_id": 9,
                "title": "자동차 업데이트",
                "company_name": "현대차",
                "broker": "테스트증권",
                "symbol": "005380",
                "published_at": "2026-04-05",
                "updated_at": "2026-04-05T00:00:00+00:00",
                "category": "company_analysis",
                "snippet": "요약",
            }
        ],
    )

    async def _send_message(self, text: str, parse_mode=None, chat_id=None) -> dict[str, object]:
        _ = parse_mode
        return {"ok": True, "detail": {"chat_id": chat_id, "text": text}}

    monkeypatch.setattr(reports_main.TelegramBridge, "send_message", _send_message)

    with TestClient(reports_main.app) as client:
        headers = _auth_header(monkeypatch)
        create = client.post(
            "/ui-api/saved-views",
            json={
                "name": "자동차 알림",
                "filters": {"query": "자동차"},
                "alert": {"enabled": True, "channel": "telegram", "target": "room-7"},
            },
            headers=headers,
        )
        view_id = create.json()["view"]["view_id"]

        alert = client.post(
            f"/ui-api/saved-views/{view_id}/alert-test",
            json={"limit": 2},
            headers=headers,
        )

    assert alert.status_code == 200
    payload = alert.json()
    assert payload["count"] == 1
    assert payload["result"]["detail"]["chat_id"] == "room-7"
    assert "현대차" in payload["message"]
