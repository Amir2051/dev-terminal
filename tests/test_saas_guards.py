import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main


def test_saas_mode_disables_host_local_features(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/projects")

    assert response.status_code == 501
    assert "disabled in SaaS mode" in response.json()["detail"]


def test_saas_identity_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/me")

    assert response.status_code == 401
    assert "Bearer access token required" in response.json()["detail"]


def test_saas_requires_https_for_nonlocal_hosts(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"dev-terminal.example.com"})
    client = TestClient(main.app)

    response = client.get("/", headers={"host": "dev-terminal.example.com"})

    assert response.status_code == 400
    assert "HTTPS is required" in response.text


def test_local_mode_guard_rejects_saas_execution(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")

    with pytest.raises(HTTPException) as exc:
        main._require_local_mode()

    assert exc.value.status_code == 501


def test_saas_responses_include_security_headers_and_are_not_cached(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example.supabase.co")
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "https://project.example.supabase.co" in response.headers["content-security-policy"]
