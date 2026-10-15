import asyncio
import hashlib
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main


def setup_saas(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "public-test-key")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_URL", "https://redis.example")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_TOKEN", "redis-test-token")

    async def test_shared_limiter(client_host, now_epoch):
        return main._allow_pairing_attempt(client_host, float(now_epoch))

    monkeypatch.setattr(main, "_allow_pairing_attempt_shared", test_shared_limiter)
    return TestClient(main.app, base_url="https://testserver")


def test_pairing_creation_requires_authenticated_session(monkeypatch):
    client = setup_saas(monkeypatch)
    response = client.post("/api/agents/pairing", json={"workspace_id": "00000000-0000-4000-8000-000000000001", "label": "Linux"})
    assert response.status_code == 401


def test_pairing_creation_hashes_code_and_uses_verified_user_token(monkeypatch):
    client = setup_saas(monkeypatch)
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user"})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return {"agent_id": "agent-1", "expires_at": "2030-01-01T00:10:00+00:00"}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/agents/pairing",
        headers={"Authorization": "Bearer user-jwt"},
        json={"workspace_id": "00000000-0000-4000-8000-000000000001", "label": "My Linux", "platform": "linux"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["agent_id"] == "agent-1"
    assert len(body["pairing_code"]) >= 32
    assert calls["token"] == "user-jwt"
    assert calls["resource"] == "rpc/create_agent_pairing"
    sent_hash = calls["kwargs"]["payload"]["pairing_code_hash"]
    assert sent_hash == hashlib.sha256(body["pairing_code"].encode()).hexdigest()
    assert body["pairing_code"] != sent_hash


def test_pairing_redemption_returns_credential_once_and_persists_only_hash(monkeypatch):
    client = setup_saas(monkeypatch)
    monkeypatch.setattr(main, "_pairing_attempts", {})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return {"agent_id": "agent-1", "workspace_id": "workspace-1", "label": "My Linux"}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post("/api/agents/redeem", json={"pairing_code": "pairing-code-value-long-enough", "platform": "linux"})
    assert response.status_code == 200
    body = response.json()
    assert body["credential"]
    assert body["agent_id"] == "agent-1"
    assert calls["token"] == "public-test-key"
    assert calls["resource"] == "rpc/redeem_agent_pairing"
    assert calls["kwargs"]["payload"]["pairing_code_hash"] == hashlib.sha256(b"pairing-code-value-long-enough").hexdigest()
    assert calls["kwargs"]["payload"]["credential_hash"] == hashlib.sha256(body["credential"].encode()).hexdigest()
    assert body["credential"] != calls["kwargs"]["payload"]["credential_hash"]


def test_pairing_redemption_throttles_repeated_attempts(monkeypatch):
    client = setup_saas(monkeypatch)
    monkeypatch.setattr(main, "_pairing_attempts", {})

    async def fake_request(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    for _ in range(10):
        response = client.post("/api/agents/redeem", json={"pairing_code": "bad-pairing-code-value-long-enough"})
        assert response.status_code == 401
    limited = client.post("/api/agents/redeem", json={"pairing_code": "bad-pairing-code-value-long-enough"})
    assert limited.status_code == 429


def test_pairing_throttle_bounds_tracked_clients_and_cleans_expired_entries(monkeypatch):
    from collections import deque

    monkeypatch.setattr(main, "_pairing_attempts", {
        "old-a": deque([0.0]),
        "old-b": deque([0.0]),
    })
    monkeypatch.setattr(main, "_PAIRING_MAX_TRACKED_CLIENTS", 2)
    monkeypatch.setattr(main, "_pairing_last_cleanup", 0.0)

    assert main._allow_pairing_attempt("new-client", 30.0) is False
    assert main._allow_pairing_attempt("new-client", 61.0) is True
    assert set(main._pairing_attempts) == {"new-client"}


def test_shared_pairing_limiter_uses_atomic_redis_counter_and_hides_client_ip(monkeypatch):
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_URL", "https://redis.example")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_TOKEN", "secret-test-token")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"result": 1}]

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(main._allow_pairing_attempt_shared("192.0.2.17", 120)) is True
    assert captured["url"] == "https://redis.example/pipeline"
    assert captured["headers"]["Authorization"] == "Bearer secret-test-token"
    command = captured["json"][0]
    assert command[0] == "EVAL"
    assert "192.0.2.17" not in command[3]
    assert "ZREMRANGEBYSCORE" in command[1]
    assert command[4:7] == ["120000", "60000", "10"]
    assert len(command[7]) == 24


def test_shared_pairing_limiter_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_URL", "")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_TOKEN", "")
    try:
        asyncio.run(main._allow_pairing_attempt_shared("127.0.0.1", 120))
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("Shared pairing limiter must fail closed when not configured")


def test_saas_pairing_fails_closed_without_shared_limiter(monkeypatch):
    client = setup_saas(monkeypatch)
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_URL", "")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_TOKEN", "")
    monkeypatch.delenv("DEV_TERMINAL_ALLOW_LOCAL_RATE_LIMIT", raising=False)
    response = client.post("/api/agents/redeem", json={"pairing_code": "bad-pairing-code-value-long-enough"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Shared pairing rate limiting is not configured."


def test_agent_heartbeat_requires_valid_credential(monkeypatch):
    client = setup_saas(monkeypatch)
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, resource=resource, kwargs=kwargs)
        return {"agent_id": "agent-1", "workspace_id": "workspace-1", "label": "My Linux"}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post("/api/agents/heartbeat", json={"credential": "agent-secret-value-that-is-long-enough-123"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert calls["resource"] == "rpc/authenticate_local_agent"
    assert calls["kwargs"]["payload"]["credential_hash"] == hashlib.sha256(b"agent-secret-value-that-is-long-enough-123").hexdigest()


def test_agent_pairing_migration_stores_only_hashes_and_restricts_rpc_grants():
    migration = (Path(__file__).resolve().parents[1] / "supabase/migrations/20261014190000_secure_local_agent_pairing.sql").read_text()
    assert "code_hash text not null unique" in migration
    assert "token_hash text not null unique" in migration
    assert "grant execute on function public.create_agent_pairing" in migration
    assert "to authenticated" in migration
    assert "grant execute on function public.redeem_agent_pairing(text,text,text) to anon" in migration
    assert "grant execute on function public.authenticate_local_agent(text) to anon" in migration
    assert "private.has_workspace_role" in migration
    assert "raw pairing codes and agent credentials are never stored" in migration.lower()


def test_list_workspace_agents_uses_verified_user_token_and_rls(monkeypatch):
    client = setup_saas(monkeypatch)
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user"})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return [{"id": "agent-1", "status": "online"}]

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.get(
        "/api/workspaces/00000000-0000-4000-8000-000000000001/agents",
        headers={"Authorization": "Bearer user-jwt"},
    )
    assert response.status_code == 200
    assert response.json()["agents"][0]["id"] == "agent-1"
    assert calls["token"] == "user-jwt"
    assert calls["resource"] == "workspace_agents"
    assert calls["kwargs"]["params"]["workspace_id"] == "eq.00000000-0000-4000-8000-000000000001"


def test_agent_revoke_requires_user_auth_and_calls_authorized_rpc(monkeypatch):
    client = setup_saas(monkeypatch)
    unauthenticated = client.post("/api/agents/00000000-0000-4000-8000-000000000001/revoke")
    assert unauthenticated.status_code == 401
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user"})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return True

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/agents/00000000-0000-4000-8000-000000000001/revoke",
        headers={"Authorization": "Bearer user-jwt"},
    )
    assert response.status_code == 200
    assert response.json()["revoked"] is True
    assert calls["resource"] == "rpc/revoke_local_agent"
    assert calls["kwargs"]["payload"]["target_agent_id"] == "00000000-0000-4000-8000-000000000001"


def test_pairing_redemption_maps_invalid_code_to_unauthorized(monkeypatch):
    from fastapi import HTTPException

    client = setup_saas(monkeypatch)

    async def fake_request(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Supabase denied this workspace operation.")

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post("/api/agents/redeem", json={"pairing_code": "bad-pairing-code-value-long-enough"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Pairing code is invalid, expired, or already used."
