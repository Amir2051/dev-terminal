import asyncio
from collections import deque

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
    monkeypatch.setattr(main, "_shared_pairing_limiter_reachable", lambda: asyncio.sleep(0, result=True))
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
    assert "http://project.example.supabase.co" not in response.headers["content-security-policy"]


def test_health_separates_rate_limit_configuration_from_live_reachability(monkeypatch):
    async def unreachable():
        return False

    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_URL", "https://redis.example")
    monkeypatch.setattr(main, "PAIRING_RATE_LIMIT_REST_TOKEN", "test-token")
    monkeypatch.setattr(main, "_shared_pairing_limiter_reachable", unreachable)
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["pairing_rate_limit_configured"] is True
    assert response.json()["pairing_rate_limit_shared"] is False


def test_saas_csp_allows_http_only_for_loopback_supabase(monkeypatch):
    monkeypatch.setattr(main, "_shared_pairing_limiter_reachable", lambda: asyncio.sleep(0, result=False))
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "SUPABASE_URL", "http://127.0.0.1:55321")
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/health")

    policy = response.headers["content-security-policy"]
    assert response.status_code == 200
    assert "http://127.0.0.1" in policy
    assert "ws://127.0.0.1" in policy


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/repos", None),
        ("GET", "/api/projects", None),
        ("GET", "/api/tools", None),
        ("POST", "/api/tools/queue", {"tool_id": "pwd"}),
        ("GET", "/api/terminal/next-command", None),
    ],
)
def test_saas_mode_disables_every_host_execution_route(monkeypatch, method, path, payload):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app, base_url="https://testserver")

    response = client.request(method, path, json=payload)

    assert response.status_code == 501
    assert "disabled in SaaS mode" in response.json()["detail"]


def test_saas_mode_rejects_unapproved_browser_origins(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "APP_ORIGINS", {"https://app.example.com"})
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/health", headers={"origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.text == "Unexpected Origin"


def test_saas_mode_rejects_unknown_hosts(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"app.example.com"})
    client = TestClient(main.app, base_url="https://unknown.example.com")

    response = client.get("/api/health")

    assert response.status_code == 403
    assert response.text == "Unexpected Host header"


def test_tool_queue_stores_structured_tool_id_not_shell_command(monkeypatch, tmp_path):
    project = tmp_path / "safe-project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(main, "_command_queue", deque(maxlen=20))
    client = TestClient(main.app, base_url="http://localhost")

    response = client.post("/api/tools/queue", json={"tool_id": "git-status", "project": "safe-project"})

    assert response.status_code == 200
    assert main._command_queue[-1] == {"tool_id": "git-status", "project": "safe-project"}
    assert "command" not in main._command_queue[-1]
    assert "cd --" not in str(main._command_queue[-1])


def test_write_tool_still_requires_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "safe-project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(main, "_command_queue", deque(maxlen=20))
    client = TestClient(main.app, base_url="http://localhost")

    response = client.post("/api/tools/queue", json={"tool_id": "git-pull", "project": "safe-project"})

    assert response.status_code == 400
    assert not main._command_queue


def test_shell_poll_returns_structured_tool_and_requires_token(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "TERMINAL_QUEUE_TOKEN", "test-queue-token")
    monkeypatch.setattr(main, "_command_queue", deque([{"tool_id": "pwd", "project": None}], maxlen=20))
    client = TestClient(main.app, base_url="http://localhost")

    denied = client.get("/api/terminal/next-command")
    allowed = client.get(
        "/api/terminal/next-command",
        headers={"X-Terminal-Queue-Token": "test-queue-token"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == {"tool": {"tool_id": "pwd", "project": None}}


def test_shell_hook_never_evaluates_http_response():
    from pathlib import Path

    shell_hook = (Path(__file__).resolve().parents[1] / "scripts" / "terminal-shell.sh").read_text()
    assert "eval \"$command\"" not in shell_hook
    assert "case \"$tool_id\" in" in shell_hook
    assert "Unknown tool ID" in shell_hook


def test_project_path_rejects_symlink_and_path_traversal(monkeypatch, tmp_path):
    from fastapi import HTTPException

    projects = tmp_path / "projects"
    projects.mkdir()
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (projects / "real-project" / ".git").mkdir(parents=True)
    (projects / "linked-project").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(main, "PROJECTS_DIR", projects)

    with pytest.raises(HTTPException) as traversal:
        main._project_path("../outside")
    with pytest.raises(HTTPException) as symlink:
        main._project_path("linked-project")

    assert traversal.value.status_code == 400
    assert symlink.value.status_code == 400
    assert main._project_path("real-project") == projects / "real-project"


def test_workspace_routes_require_authentication(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    client = TestClient(main.app, base_url="https://testserver")

    listed = client.get("/api/workspaces")
    created = client.post("/api/workspaces", json={"name": "My workspace"})

    assert listed.status_code == 401
    assert created.status_code == 401


def test_list_workspaces_uses_verified_user_token_and_rls_scoped_request(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "user-123", "role": "authenticated"})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return [{"id": "workspace-1", "name": "Personal"}]

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/workspaces", headers={"Authorization": "Bearer verified-token"})

    assert response.status_code == 200
    assert response.json()["workspaces"][0]["id"] == "workspace-1"
    assert calls["token"] == "verified-token"
    assert calls["method"] == "GET"
    assert calls["resource"] == "workspaces"
    assert calls["kwargs"]["params"]["select"] == "id,name,owner_id,workspace_type,created_at,updated_at"


def test_create_workspace_uses_verified_subject_and_not_client_owner_id(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user", "role": "authenticated"})
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, kwargs=kwargs)
        return [{
            "id": "workspace-1",
            "name": "Safe workspace",
            "owner_id": "trusted-user",
            "workspace_type": "hybrid",
        }]

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    client = TestClient(main.app, base_url="https://testserver")

    response = client.post(
        "/api/workspaces",
        headers={"Authorization": "Bearer verified-token"},
        json={"name": "  Safe workspace  ", "owner_id": "attacker-user"},
    )

    assert response.status_code == 201
    assert response.json()["workspace"]["owner_id"] == "trusted-user"
    assert calls["method"] == "POST"
    assert calls["resource"] == "workspaces"
    assert calls["kwargs"]["payload"] == {"name": "Safe workspace", "owner_id": "trusted-user"}
    assert calls["kwargs"]["prefer"] == "return=representation"


def test_create_workspace_rejects_blank_name(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user", "role": "authenticated"})
    client = TestClient(main.app, base_url="https://testserver")

    response = client.post(
        "/api/workspaces",
        headers={"Authorization": "Bearer verified-token"},
        json={"name": "   "},
    )

    assert response.status_code == 422


def test_supabase_workspace_proxy_forwards_user_jwt_and_anon_key_only(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "public-anon-test-key")
    calls = {}

    class FakeResponse:
        status_code = 200
        content = b'[{"id":"workspace-1"}]'

        @staticmethod
        def json():
            return [{"id": "workspace-1"}]

    class FakeClient:
        def __init__(self, timeout):
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            calls.update(method=method, url=url, kwargs=kwargs)
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)

    import asyncio
    result = asyncio.run(
        main._supabase_user_request(
            "verified-user-jwt",
            "GET",
            "workspaces",
            params={"select": "id,name"},
        )
    )

    assert result == [{"id": "workspace-1"}]
    assert calls["url"] == "https://project.example/rest/v1/workspaces"
    assert calls["kwargs"]["headers"]["Authorization"] == "Bearer verified-user-jwt"
    assert calls["kwargs"]["headers"]["apikey"] == "public-anon-test-key"
    assert "service_role" not in str(calls["kwargs"]["headers"]).lower()


def test_supabase_workspace_proxy_rejects_rls_denial_without_leaking_response(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "public-anon-test-key")

    class FakeResponse:
        status_code = 403
        content = b'{"message":"private database detail"}'

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)

    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._supabase_user_request("verified-user-jwt", "GET", "workspaces"))

    assert exc.value.status_code == 403
    assert "private database detail" not in exc.value.detail


def test_workspace_routes_are_hidden_in_local_mode(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    client = TestClient(main.app, base_url="http://localhost")

    assert client.get("/api/workspaces", headers={"Authorization": "Bearer token"}).status_code == 404
    assert client.post(
        "/api/workspaces",
        headers={"Authorization": "Bearer token"},
        json={"name": "Local"},
    ).status_code == 404


def test_invalid_workspace_token_never_reaches_supabase(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})

    def reject_token(_token):
        raise HTTPException(status_code=401, detail="Invalid access token.")

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("Supabase must not be called for an invalid token")

    monkeypatch.setattr(main, "verify_access_token", reject_token)
    monkeypatch.setattr(main, "_supabase_user_request", unexpected_request)
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/api/workspaces", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401


def test_supabase_workspace_proxy_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "")

    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._supabase_user_request("token", "GET", "workspaces"))

    assert exc.value.status_code == 503


def test_supabase_workspace_proxy_hides_upstream_transport_errors(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "public-anon-test-key")

    class BrokenClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, *args, **kwargs):
            raise main.httpx.ConnectError("sensitive upstream detail")

    monkeypatch.setattr(main.httpx, "AsyncClient", BrokenClient)

    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._supabase_user_request("token", "GET", "workspaces"))

    assert exc.value.status_code == 502
    assert "sensitive upstream detail" not in exc.value.detail


def test_workspace_audit_migration_uses_minimal_trusted_trigger_writes():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "supabase/migrations/20261014172000_workspace_audit_triggers.sql"
    sql = migration.read_text()

    assert "security definer" in sql.lower()
    assert "set search_path = ''" in sql.lower()
    assert "after insert or update on public.workspaces" in sql.lower()
    assert "'workspace.created'" in sql
    assert "'workspace.updated'" in sql
    assert "revoke all on function private.audit_workspace_changes() from public, anon, authenticated" in sql.lower()
    assert "new.name is distinct from old.name" in sql.lower()
    assert "workspace_audit_events" in sql


def test_workspace_select_policy_allows_owner_to_read_new_workspace_before_membership_trigger():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "supabase/migrations/20261014174500_allow_owner_to_read_new_workspace.sql"
    sql = migration.read_text().lower()

    assert 'drop policy if exists "workspaces_select_member" on public.workspaces' in sql
    assert 'for select to authenticated' in sql
    assert 'owner_id = (select auth.uid()) or private.is_workspace_member(id)' in sql
    assert 'with check' not in sql
    assert 'service_role' not in sql
