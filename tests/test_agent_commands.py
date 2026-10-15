import hashlib
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

_agent_path = Path(__file__).resolve().parents[1] / "scripts" / "local-agent.py"
_agent_spec = importlib.util.spec_from_file_location("dev_terminal_local_agent", _agent_path)
assert _agent_spec and _agent_spec.loader
local_agent = importlib.util.module_from_spec(_agent_spec)
_agent_spec.loader.exec_module(local_agent)


def setup_saas(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"testserver"})
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "public-test-key")
    monkeypatch.setattr(main, "verify_access_token", lambda token: {"sub": "trusted-user"})
    return TestClient(main.app, base_url="https://testserver")


def test_command_queue_requires_user_authentication(monkeypatch):
    client = setup_saas(monkeypatch)
    response = client.post(
        "/api/workspaces/00000000-0000-4000-8000-000000000001/commands",
        json={"agent_id": "00000000-0000-4000-8000-000000000002", "tool_id": "git-status", "project_name": "demo"},
    )
    assert response.status_code == 401


def test_command_queue_sends_only_allowlisted_structured_fields(monkeypatch):
    client = setup_saas(monkeypatch)
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, method=method, resource=resource, payload=kwargs.get("payload"))
        return {"id": "command-1", "status": "queued", "tool_id": "git-status"}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/workspaces/00000000-0000-4000-8000-000000000001/commands",
        headers={"Authorization": "Bearer user-jwt"},
        json={"agent_id": "00000000-0000-4000-8000-000000000002", "tool_id": "git-status", "project_name": "demo"},
    )
    assert response.status_code == 201
    assert calls["token"] == "user-jwt"
    assert calls["resource"] == "rpc/enqueue_agent_command"
    assert calls["payload"] == {
        "target_workspace_id": "00000000-0000-4000-8000-000000000001",
        "target_agent_id": "00000000-0000-4000-8000-000000000002",
        "requested_tool_id": "git-status",
        "requested_project_name": "demo",
    }
    assert "command" not in calls["payload"]


def test_command_queue_rejects_unknown_tool_before_database_call(monkeypatch):
    client = setup_saas(monkeypatch)

    async def unexpected(*args, **kwargs):
        pytest.fail("database should not be called for an unknown tool")

    monkeypatch.setattr(main, "_supabase_user_request", unexpected)
    response = client.post(
        "/api/workspaces/00000000-0000-4000-8000-000000000001/commands",
        headers={"Authorization": "Bearer user-jwt"},
        json={"agent_id": "00000000-0000-4000-8000-000000000002", "tool_id": "shell", "project_name": "demo"},
    )
    assert response.status_code == 400


def test_write_commands_are_queued_pending_approval_by_database_contract():
    migration = (Path(__file__).resolve().parents[1] / "supabase/migrations/20261014200000_secure_agent_commands.sql").read_text()
    assert "command_status := case when requested_tool_id in ('git-fetch','git-pull','pytest') then 'pending_approval' else 'queued' end" in migration
    assert "public.approve_agent_command" in migration
    assert "grant execute on function public.approve_agent_command(uuid,boolean) to authenticated" in migration
    assert "grant execute on function public.claim_agent_command(text) to anon" in migration
    assert "private.agent_commands from public, anon, authenticated" in migration


def test_approval_endpoint_requires_user_auth_and_calls_approval_rpc(monkeypatch):
    client = setup_saas(monkeypatch)
    unauthenticated = client.post("/api/commands/00000000-0000-4000-8000-000000000001/approval", json={"approve": True})
    assert unauthenticated.status_code == 401
    calls = {}

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, resource=resource, payload=kwargs.get("payload"))
        return {"id": "command-1", "status": "queued"}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/commands/00000000-0000-4000-8000-000000000001/approval",
        headers={"Authorization": "Bearer user-jwt"},
        json={"approve": True},
    )
    assert response.status_code == 200
    assert calls["token"] == "user-jwt"
    assert calls["resource"] == "rpc/approve_agent_command"
    assert calls["payload"] == {"target_command_id": "00000000-0000-4000-8000-000000000001", "approve": True}


def test_agent_claim_hashes_credential_and_never_sends_raw_credential_to_database(monkeypatch):
    client = setup_saas(monkeypatch)
    calls = {}
    credential = "agent-secret-value-that-is-long-enough-123"

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, resource=resource, payload=kwargs.get("payload"))
        return {"command": None}

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post("/api/agents/next-command", json={"credential": credential})
    assert response.status_code == 200
    assert calls["token"] == "public-test-key"
    assert calls["resource"] == "rpc/claim_agent_command"
    assert calls["payload"]["credential_hash"] == hashlib.sha256(credential.encode()).hexdigest()
    assert calls["payload"]["credential_hash"] != credential


def test_agent_completion_uses_credential_hash_and_limits_outcome(monkeypatch):
    client = setup_saas(monkeypatch)
    calls = {}
    credential = "agent-secret-value-that-is-long-enough-123"

    async def fake_request(token, method, resource, **kwargs):
        calls.update(token=token, resource=resource, payload=kwargs.get("payload"))
        return True

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/agents/commands/00000000-0000-4000-8000-000000000001/complete",
        json={"credential": credential, "outcome": "succeeded", "output": "ok"},
    )
    assert response.status_code == 200
    assert calls["resource"] == "rpc/complete_agent_command"
    assert calls["payload"]["credential_hash"] == hashlib.sha256(credential.encode()).hexdigest()
    assert calls["payload"]["outcome"] == "succeeded"


def test_local_runner_rejects_arbitrary_tool_id_without_subprocess(monkeypatch):
    monkeypatch.setattr(local_agent.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="outside the local allowlist"):
        local_agent.execute_command({"id": "00000000-0000-4000-8000-000000000001", "tool_id": "bash -c id"})


def test_local_runner_rejects_mutating_tool_without_approval_marker(monkeypatch):
    monkeypatch.setattr(local_agent.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="no verifiable approval marker"):
        local_agent.execute_command({
            "id": "00000000-0000-4000-8000-000000000001",
            "tool_id": "git-pull",
            "project_name": "demo",
        })


def test_pytest_uses_project_virtualenv_python_as_module(tmp_path):
    repo = tmp_path / "demo"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    assert local_agent.tool_argv("pytest", repo) == [str(python), "-m", "pytest", "-q"]


def test_local_runner_uses_fixed_argv_and_shell_false(monkeypatch, tmp_path):
    root = tmp_path / "projects"
    repo = root / "demo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("DEV_TERMINAL_PROJECTS_DIR", str(root))
    monkeypatch.setenv("DEV_TERMINAL_ALLOWED_PROJECTS", "demo")
    captured = {}

    class Result:
        returncode = 0
        stdout = "branch main"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(local_agent.subprocess, "run", fake_run)
    outcome, output = local_agent.execute_command({
        "id": "00000000-0000-4000-8000-000000000001",
        "tool_id": "git-status",
        "project_name": "demo",
    })
    assert outcome == "succeeded"
    assert output == "branch main"
    assert captured["argv"] == ["git", "status", "--short", "--branch"]
    assert captured["shell"] is False
    assert captured["cwd"] == str(repo)


def test_local_runner_rejects_path_traversal(monkeypatch, tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    monkeypatch.setenv("DEV_TERMINAL_PROJECTS_DIR", str(root))
    monkeypatch.setenv("DEV_TERMINAL_ALLOWED_PROJECTS", "safe-project")
    with pytest.raises(RuntimeError, match="Invalid project name"):
        local_agent.project_directory("../outside")



def test_saas_command_history_and_approval_routes_reject_unauthenticated_requests(monkeypatch):
    client = setup_saas(monkeypatch)
    workspace_id = "00000000-0000-4000-8000-000000000001"
    command_id = "00000000-0000-4000-8000-000000000002"
    assert client.get(f"/api/workspaces/{workspace_id}/commands").status_code == 401
    assert client.post(
        f"/api/commands/{command_id}/approval", json={"approve": True}
    ).status_code == 401


def test_local_runner_requires_approval_for_pytest(monkeypatch):
    monkeypatch.setattr(local_agent.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="no verifiable approval marker"):
        local_agent.execute_command({
            "id": "00000000-0000-4000-8000-000000000001",
            "tool_id": "pytest",
            "project_name": "demo",
        })


def test_agent_claim_invalid_credential_is_rejected(monkeypatch):
    client = setup_saas(monkeypatch)

    async def fake_request(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "_supabase_user_request", fake_request)
    response = client.post(
        "/api/agents/next-command",
        json={"credential": "test-agent-credential-which-is-long-enough-12345"},
    )
    assert response.status_code == 401



def test_local_runner_rejects_project_not_in_explicit_allowlist(monkeypatch, tmp_path):
    root = tmp_path / "projects"
    repo = root / "other-repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("DEV_TERMINAL_PROJECTS_DIR", str(root))
    monkeypatch.setenv("DEV_TERMINAL_ALLOWED_PROJECTS", "dev-terminal")
    with pytest.raises(RuntimeError, match="not in DEV_TERMINAL_ALLOWED_PROJECTS"):
        local_agent.project_directory("other-repo")
