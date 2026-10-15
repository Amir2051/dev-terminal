import jwt
import pytest
from fastapi import HTTPException

from app.auth import verify_access_token


def test_malformed_token_is_rejected_without_auth_configuration():
    with pytest.raises(HTTPException) as exc:
        verify_access_token("not-a-jwt")
    assert exc.value.status_code == 401


def test_missing_supabase_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    token = jwt.encode({"sub": "user-1"}, "unit-test-only-hmac-secret-key-at-least-32-bytes-long", algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        verify_access_token(token)

    assert exc.value.status_code == 503


def test_legacy_hs256_token_is_checked_by_supabase_auth(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    token = jwt.encode({"sub": "untrusted-user"}, "unit-test-only-untrusted-hmac-secret-key-at-least-32-bytes", algorithm="HS256")

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "verified-user", "email": "dev@example.com"}

    calls = {}

    def fake_get(url, headers, timeout):
        calls.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("app.auth.httpx.get", fake_get)
    claims = verify_access_token(token)

    assert claims["sub"] == "verified-user"
    assert claims["email"] == "dev@example.com"
    assert calls["url"] == "https://project.example/auth/v1/user"
    assert calls["headers"]["Authorization"] == f"Bearer {token}"
    assert calls["timeout"] == 5.0


def test_supabase_rejection_rejects_legacy_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")

    class FakeResponse:
        status_code = 401

    monkeypatch.setattr("app.auth.httpx.get", lambda *args, **kwargs: FakeResponse())
    token = jwt.encode({"sub": "user-1"}, "unit-test-only-untrusted-hmac-secret-key-at-least-32-bytes", algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        verify_access_token(token)

    assert exc.value.status_code == 401
