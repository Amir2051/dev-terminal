import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main


def test_saas_mode_disables_host_local_features(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app)

    response = client.get("/api/projects")

    assert response.status_code == 501
    assert "disabled in SaaS mode" in response.json()["detail"]


def test_saas_identity_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app)

    response = client.get("/api/me")

    assert response.status_code == 401
    assert "Bearer access token required" in response.json()["detail"]


def test_local_mode_guard_rejects_saas_execution(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")

    with pytest.raises(HTTPException) as exc:
        main._require_local_mode()

    assert exc.value.status_code == 501
