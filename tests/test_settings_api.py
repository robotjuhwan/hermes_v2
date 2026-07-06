from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.config import AppSettings
from tradecraft.services.settings_catalog import build_settings_catalog


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_settings_catalog_masks_secret_values(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.settings, "kis_primary_app_secret", "super-secret")
    monkeypatch.setattr(main.settings, "llm_model", "gpt-5.5")

    with TestClient(main.app) as client:
        response = client.get(
            "/api/settings/catalog",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    serialized = response.text
    assert "super-secret" not in serialized

    items = {row["key"]: row for row in payload["items"]}
    assert items["kis_primary_app_secret"]["secret"] is True
    assert items["kis_primary_app_secret"]["editable"] is False
    assert items["kis_primary_app_secret"]["configured"] is True
    assert items["kis_primary_app_secret"]["value"] is None
    assert items["llm_model"]["editable"] is True
    assert items["llm_model"]["value"] == "gpt-5.5"


def test_settings_update_writes_env_and_marks_restart(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(main.app) as client:
        response = client.patch(
            "/api/settings/values",
            headers=_admin_headers(monkeypatch),
            json={
                "updates": {
                    "kis_block_trader_rule_interval_sec": 15,
                    "llm_reasoning_effort": "high",
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["restart_required"] is True
    assert {row["key"] for row in payload["changed"]} == {
        "kis_block_trader_rule_interval_sec",
        "llm_reasoning_effort",
    }
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TRADECRAFT_KIS_BLOCK_TRADER_RULE_INTERVAL_SEC=15" in env_text
    assert "TRADECRAFT_LLM_REASONING_EFFORT=high" in env_text


def test_settings_update_removes_duplicate_env_keys(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TRADECRAFT_LLM_REASONING_EFFORT=medium",
                "TRADECRAFT_LLM_REASONING_EFFORT=low",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with TestClient(main.app) as client:
        response = client.patch(
            "/api/settings/values",
            headers=_admin_headers(monkeypatch),
            json={"updates": {"llm_reasoning_effort": "xhigh"}},
        )

    assert response.status_code == 200
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert env_text.count("TRADECRAFT_LLM_REASONING_EFFORT=") == 1
    assert "TRADECRAFT_LLM_REASONING_EFFORT=xhigh" in env_text


def test_settings_catalog_includes_crypto_alpha_options(tmp_path) -> None:
    payload = build_settings_catalog(AppSettings(_env_file=None), env_path=tmp_path / ".env")
    items = {row["key"]: row for row in payload["items"]}

    assert items["crypto_alpha_enabled"]["env"] == "TRADECRAFT_CRYPTO_ALPHA_ENABLED"
    assert items["crypto_alpha_enabled"]["category"] == "signals"
    assert items["crypto_alpha_once"]["editable"] is False
    assert items["crypto_alpha_context_limit"]["min"] == 1
    assert items["crypto_alpha_llm_reasoning_effort"]["choices"] == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]


def test_settings_catalog_hides_retired_kis_direct_trader_options(tmp_path) -> None:
    payload = build_settings_catalog(AppSettings(_env_file=None), env_path=tmp_path / ".env")
    keys = {row["key"] for row in payload["items"]}

    assert "kis_trader_enabled" not in keys
    assert "kis_trader_execute_orders" not in keys
    assert "kis_trader_state_path" not in keys
    assert "kis_block_trader_execute_orders" in keys


def test_settings_catalog_locks_cors_allow_origins(tmp_path) -> None:
    payload = build_settings_catalog(AppSettings(_env_file=None), env_path=tmp_path / ".env")
    items = {row["key"]: row for row in payload["items"]}

    assert items["allow_origins"]["editable"] is False
    assert items["allow_origins"]["risk"] == "danger"


def test_settings_update_rejects_retired_kis_direct_trader_option(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(main.app) as client:
        response = client.patch(
            "/api/settings/values",
            headers=_admin_headers(monkeypatch),
            json={"updates": {"kis_trader_execute_orders": True}},
        )

    assert response.status_code == 400
    assert "retired" in response.json()["detail"]
    assert not (tmp_path / ".env").exists()


def test_settings_update_rejects_any_retired_kis_direct_trader_prefix(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(main.app) as client:
        response = client.patch(
            "/api/settings/values",
            headers=_admin_headers(monkeypatch),
            json={"updates": {"kis_trader_future_ghost_option": True}},
        )

    assert response.status_code == 400
    assert "retired" in response.json()["detail"]
    assert not (tmp_path / ".env").exists()


def test_settings_update_rejects_locked_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(main.app) as client:
        response = client.patch(
            "/api/settings/values",
            headers=_admin_headers(monkeypatch),
            json={"updates": {"admin_token": "new-secret"}},
        )

    assert response.status_code == 400
    assert "locked" in response.json()["detail"]
    assert not (tmp_path / ".env").exists()


def test_settings_update_requires_high_risk_confirmation(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        rejected = client.patch(
            "/api/settings/values",
            headers=headers,
            json={"updates": {"kis_block_trader_execute_orders": True}},
        )
        accepted = client.patch(
            "/api/settings/values",
            headers=headers,
            json={
                "updates": {"kis_block_trader_execute_orders": True},
                "confirm_high_risk": True,
            },
        )

    assert rejected.status_code == 400
    assert "high risk confirmation required" in rejected.json()["detail"]
    assert accepted.status_code == 200
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS=true" in env_text
