import json
import pytest
from fastapi.testclient import TestClient

from app import main


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("failed", request=None, response=None)

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.response = FakeResponse({"models": [{"name": "mistral:7b"}, {"name": "qwen3:1.7b"}]})
        self.chat_response = FakeResponse({"message": {"content": "Use a focused unit test."}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        assert url == "http://127.0.0.1:11434/api/tags"
        return self.response

    async def post(self, url, **kwargs):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert kwargs["json"]["stream"] is False
        assert kwargs["json"]["options"]["num_predict"] <= 700
        return self.chat_response


def test_ai_status_reports_local_model_without_exposing_other_services(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.get("/api/ai/status")
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["model_available"] is True
    assert response.json()["provider"] == "ollama"


def test_ai_chat_uses_local_model_and_bounded_prompt(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/chat", json={"prompt": "How should I test this?", "tool_context": "Git status: clean"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Use a focused unit test."
    assert response.json()["provider"] == "ollama"
    assert response.json()["context_project"] is None

def test_ai_chat_returns_verified_inventory_without_another_slow_model_turn(monkeypatch):
    class InventoryToolClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            return FakeResponse({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "mcp__github__list_accessible_repositories", "arguments": {}}}
            ]}})

    async def fake_discovery():
        return ([{"type": "function", "function": {"name": "mcp__github__list_accessible_repositories", "description": "inventory", "parameters": {"type": "object", "properties": {}}}}],
                [{"name": "github", "status": "connected", "tools": "15"}])

    async def fake_inventory(name, args):
        assert name == "mcp__github__list_accessible_repositories"
        return json.dumps({"source": "GitHub REST API /user/repos", "collected": 2,
                           "pages_checked": 1, "complete": True,
                           "repositories": [{"full_name": "owner/one", "private": True},
                                            {"full_name": "owner/two", "private": False}]})

    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "discover_mcp_tools", fake_discovery)
    monkeypatch.setattr(main, "call_mcp_tool", fake_inventory)
    monkeypatch.setattr(main.httpx, "AsyncClient", InventoryToolClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/chat", json={"prompt": "List all GitHub repositories in my account"})
    assert response.status_code == 200
    payload = response.json()
    assert "Collected: 2" in payload["answer"]
    assert "Complete: yes" in payload["answer"]
    assert "Private repositories in collected results: 1" in payload["answer"]
    assert "owner/one" in payload["answer"] and "owner/two" in payload["answer"]
    assert payload["tools_used"][0]["tool"] == "mcp__github__list_accessible_repositories"


def test_ai_chat_limits_repository_inventory_to_deterministic_mcp_tool(monkeypatch):
    seen_tools = []

    class InventorySchemaClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            seen_tools.extend(kwargs["json"].get("tools", []))
            return FakeResponse({"message": {"role": "assistant", "content": "I will inventory the repositories."}})

    async def fake_discovery():
        return ([
            {"type": "function", "function": {"name": "mcp__github__search_repositories", "description": "Search repos", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mcp__github__list_all_repositories", "description": "Search repos with pagination", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "mcp__github__list_accessible_repositories", "description": "List token-accessible repos", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "mcp__github__search_code", "description": "Search code", "parameters": {"type": "object", "properties": {}}}},
        ], [{"name": "github", "status": "connected", "tools": "15"}])

    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "discover_mcp_tools", fake_discovery)
    monkeypatch.setattr(main.httpx, "AsyncClient", InventorySchemaClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/chat", json={"prompt": "List all GitHub repositories in my account"})
    assert response.status_code == 200
    assert [item["function"]["name"] for item in seen_tools] == ["mcp__github__list_accessible_repositories"]


def test_ai_chat_call_word_does_not_trigger_repository_inventory(monkeypatch):
    seen_tools = []

    class IntentClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            seen_tools.extend(kwargs["json"].get("tools", []))
            return FakeResponse({"message": {"content": "Ready to inspect the file."}})

    async def fake_discovery():
        names = ["mcp__github__list_accessible_repositories", "mcp__github__get_file_contents", "mcp__github__search_code"]
        schemas = [{"type": "function", "function": {"name": name, "description": name,
                   "parameters": {"type": "object", "properties": {}}}} for name in names]
        return schemas, [{"name": "github", "status": "connected", "tools": "15"}]

    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "discover_mcp_tools", fake_discovery)
    monkeypatch.setattr(main.httpx, "AsyncClient", IntentClient)
    response = TestClient(main.app, base_url="http://localhost").post(
        "/api/ai/chat", json={"prompt": "Call the file contents tool to read a source file in my repository."}
    )
    assert response.status_code == 200
    offered = [item["function"]["name"] for item in seen_tools]
    assert "mcp__github__get_file_contents" in offered
    assert offered != ["mcp__github__list_accessible_repositories"]


def test_ai_agent_executes_model_requested_safe_tool_then_summarizes(monkeypatch, tmp_path):
    _prepare_ai_repo(monkeypatch, tmp_path)

    class ToolCallingClient(FakeAsyncClient):
        calls = 0

        async def post(self, url, **kwargs):
            self.calls += 1
            body = kwargs["json"]
            assert body["tools"]
            if self.calls == 1:
                return FakeResponse({"message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_code", "arguments": {"query": "VALUE"}}}],
                }})
            assert any(message.get("role") == "tool" and "VALUE = 1" in message.get("content", "")
                       for message in body["messages"])
            return FakeResponse({"message": {"role": "assistant", "content": "I found VALUE = 1 in src/example.py."}})

    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main.httpx, "AsyncClient", ToolCallingClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/chat", json={"prompt": "Find VALUE in this repository", "project": "allowed"})
    assert response.status_code == 200
    assert "VALUE = 1" in response.json()["answer"]
    assert response.json()["tools_used"][0]["tool"] == "search_code"


def test_selected_project_uses_only_local_agent_tool_schemas(monkeypatch, tmp_path):
    _prepare_ai_repo(monkeypatch, tmp_path)
    seen_tools = []

    class SelectedProjectClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            seen_tools.extend(kwargs["json"].get("tools", []))
            return FakeResponse({"message": {"role": "assistant", "content": "Repository inspected."}})

    async def fake_discovery():
        return ([
            {"type": "function", "function": {
                "name": "mcp__github__search_code",
                "description": "Remote GitHub search",
                "parameters": {"type": "object", "properties": {}},
            }},
            {"type": "function", "function": {
                "name": "mcp__github__list_accessible_repositories",
                "description": "Remote repository inventory",
                "parameters": {"type": "object", "properties": {}},
            }},
        ], [{"name": "github", "status": "connected", "tools": "15"}])

    monkeypatch.setattr(main, "discover_mcp_tools", fake_discovery)
    monkeypatch.setattr(main.httpx, "AsyncClient", SelectedProjectClient)
    response = TestClient(main.app, base_url="http://localhost").post(
        "/api/ai/chat",
        json={"prompt": "Inspect the selected repository", "project": "allowed"},
    )

    assert response.status_code == 200
    offered_names = [item["function"]["name"] for item in seen_tools]
    local_names = [item["function"]["name"] for item in main._AI_AGENT_TOOL_SPECS]
    assert offered_names == local_names
    assert not any(name.startswith("mcp__") for name in offered_names)


def test_run_tests_tool_uses_project_python_module_invocation(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    project_python = repo / ".venv" / "bin" / "python"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    captured = {}

    class Result:
        returncode = 0
        stdout = "3 passed in 0.10s"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    import asyncio
    output = asyncio.run(main._execute_agent_tool("run_tests", {}, "allowed"))

    assert captured["args"] == [str(project_python), "-m", "pytest", "-q"]
    assert captured["cwd"] == repo
    assert captured["timeout"] == 45
    assert "Exit code: 0" in output
    assert "3 passed" in output


def test_ai_agent_stops_after_repeated_identical_tool_call(monkeypatch, tmp_path):
    _prepare_ai_repo(monkeypatch, tmp_path)
    model_calls = []
    tool_executions = []

    class RepeatingToolClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            model_calls.append(kwargs["json"])
            if len(model_calls) <= 2:
                return FakeResponse({"message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_code", "arguments": {"query": "x"}}}],
                }})
            return FakeResponse({"message": {"role": "assistant", "content": "The search query was invalid; I stopped after detecting the repeated call."}})

    async def fake_discovery():
        return [], []

    async def fake_execute(name, arguments, project):
        tool_executions.append((name, arguments, project))
        return "Tool argument error: query must contain at least two characters."

    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "discover_mcp_tools", fake_discovery)
    monkeypatch.setattr(main, "_execute_agent_tool", fake_execute)
    monkeypatch.setattr(main.httpx, "AsyncClient", RepeatingToolClient)
    response = TestClient(main.app, base_url="http://localhost").post(
        "/api/ai/chat", json={"prompt": "Search for a token", "project": "allowed"}
    )

    assert response.status_code == 200
    assert len(tool_executions) == 1
    assert len(model_calls) == 3  # first call, duplicate detection, final summary
    assert "repeated call" in response.json()["answer"]


def test_ai_chat_rejects_invalid_project_before_model_request(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "PROJECTS_DIR", __import__("pathlib").Path("/tmp"))
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/chat", json={"prompt": "Review this", "project": "../outside"})
    assert response.status_code == 400


def test_ai_routes_are_disabled_in_saas_mode(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"localhost"})
    monkeypatch.setattr(main, "APP_ORIGINS", set())
    client = TestClient(main.app, base_url="http://localhost")
    response = client.get("/api/ai/status")
    assert response.status_code == 501


def test_ollama_url_must_be_loopback(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://example.com:11434")
    with pytest.raises(Exception, match="loopback"):
        main._ollama_base_url()


def test_local_project_allowlist_filters_projects_and_blocks_direct_access(monkeypatch, tmp_path):
    root = tmp_path / "repos"
    allowed = root / "allowed"
    denied = root / "denied"
    (allowed / ".git").mkdir(parents=True)
    (denied / ".git").mkdir(parents=True)
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "PROJECTS_DIR", root)
    monkeypatch.setattr(main, "LOCAL_PROJECT_ALLOWLIST", {"allowed"})
    assert [item["name"] for item in main._list_projects()] == ["allowed"]
    with pytest.raises(Exception, match="LOCAL_PROJECT_ALLOWLIST"):
        main._project_path("denied")


def test_ai_read_only_tools_search_code_and_git_status(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    import subprocess
    original_run = subprocess.run
    def fake_run(args, **kwargs):
        assert args == ["git", "status", "--short", "--branch"]
        assert kwargs["timeout"] == 6
        class Result:
            returncode = 0
            stdout = "## main\n M src/example.py\n"
            stderr = ""
        return Result()
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    client = TestClient(main.app, base_url="http://localhost")
    search = client.post("/api/ai/tool", json={"tool_id": "search-code", "project": "allowed", "query": "VALUE"})
    assert search.status_code == 200
    assert "src/example.py:1: VALUE = 1" in search.json()["output"]
    status = client.post("/api/ai/tool", json={"tool_id": "git-status", "project": "allowed"})
    assert status.status_code == 200 and "main" in status.json()["output"]
    denied = client.post("/api/ai/tool", json={"tool_id": "arbitrary-shell", "project": "allowed"})
    assert denied.status_code == 400
    monkeypatch.setattr(main.subprocess, "run", original_run)


def test_ai_tools_are_disabled_in_saas_mode(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "saas")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"localhost"})
    monkeypatch.setattr(main, "APP_ORIGINS", set())
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/tool", json={"tool_id": "project-map", "project": "dev-terminal"})
    assert response.status_code == 501


class FakeEditAsyncClient(FakeAsyncClient):
    async def post(self, url, **kwargs):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert kwargs["json"]["format"] == "json"
        return FakeResponse({"message": {"content": json.dumps({"new_content": "VALUE = 2\n"})}})


def _prepare_ai_repo(monkeypatch, tmp_path):
    import pathlib
    root = tmp_path / "repos"
    repo = root / "allowed"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "PROJECTS_DIR", pathlib.Path(root))
    monkeypatch.setattr(main, "LOCAL_PROJECT_ALLOWLIST", {"allowed"})
    return repo


def test_ai_file_listing_and_reading_exclude_hidden_secret_paths(monkeypatch, tmp_path):
    _prepare_ai_repo(monkeypatch, tmp_path)
    client = TestClient(main.app, base_url="http://localhost")
    listing = client.get("/api/ai/files", params={"project": "allowed"})
    assert listing.status_code == 200
    assert "src/example.py" in listing.json()["files"]
    assert ".env" not in listing.json()["files"]
    read = client.post("/api/ai/file", json={"project": "allowed", "path": "src/example.py"})
    assert read.status_code == 200 and read.json()["content"] == "VALUE = 1\n"
    denied = client.post("/api/ai/file", json={"project": "allowed", "path": ".env"})
    assert denied.status_code == 403
    traversal = client.post("/api/ai/file", json={"project": "allowed", "path": "../outside"})
    assert traversal.status_code == 400
    windows_traversal = client.post("/api/ai/file", json={"project": "allowed", "path": "..\\outside"})
    assert windows_traversal.status_code == 400


def test_ai_apply_edit_requires_explicit_approval_and_detects_stale_files(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    import hashlib
    from datetime import datetime, timezone
    original = "VALUE = 1\n"
    proposal_id = "proposal-token-long-enough-for-test"
    monkeypatch.setattr(main, "_ai_edit_proposals", {proposal_id: {
        "project": "allowed", "relative_path": "src/example.py",
        "absolute_path": str(repo / "src" / "example.py"), "original": original,
        "new_content": "VALUE = 2\n", "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).timestamp(), "diff": "", "model": "test-model",
    }})
    client = TestClient(main.app, base_url="http://localhost")
    denied = client.post("/api/ai/apply-edit", json={"proposal_id": proposal_id, "confirmed": False})
    assert denied.status_code == 400
    applied = client.post("/api/ai/apply-edit", json={"proposal_id": proposal_id, "confirmed": True})
    assert applied.status_code == 200 and applied.json()["applied"] is True
    assert (repo / "src" / "example.py").read_text() == "VALUE = 2\n"

    original = "VALUE = 1\n"
    (repo / "src" / "example.py").write_text("CHANGED = True\n")
    monkeypatch.setattr(main, "_ai_edit_proposals", {proposal_id: {
        "project": "allowed", "relative_path": "src/example.py",
        "absolute_path": str(repo / "src" / "example.py"), "original": original,
        "new_content": "VALUE = 3\n", "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).timestamp(), "diff": "", "model": "test-model",
    }})
    stale = client.post("/api/ai/apply-edit", json={"proposal_id": proposal_id, "confirmed": True})
    assert stale.status_code == 409
    assert (repo / "src" / "example.py").read_text() == "CHANGED = True\n"


def test_ai_edit_generates_reviewable_diff_without_writing(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeEditAsyncClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/propose-edit", json={
        "project": "allowed", "path": "src/example.py", "instructions": "Change the value to two."
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_confirmation"] is True
    assert "-VALUE = 1" in payload["diff"] and "+VALUE = 2" in payload["diff"]
    assert (repo / "src" / "example.py").read_text() == "VALUE = 1\n"


class FakeInvalidPythonAsyncClient(FakeEditAsyncClient):
    async def post(self, url, **kwargs):
        return FakeResponse({"message": {"content": json.dumps({"new_content": "VALUE =\n"})}})


def test_ai_rejects_python_proposal_with_invalid_syntax(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeInvalidPythonAsyncClient)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/ai/propose-edit", json={
        "project": "allowed", "path": "src/example.py", "instructions": "Change the value."
    })
    assert response.status_code == 422
    assert "syntax validation" in response.json()["detail"]
    assert (repo / "src" / "example.py").read_text() == "VALUE = 1\n"


def test_agent_search_is_single_safe_bounded_walk(monkeypatch, tmp_path):
    repo = _prepare_ai_repo(monkeypatch, tmp_path)
    (repo / "src" / "needle.py").write_text("target_value = 42\n", encoding="utf-8")
    (repo / "src" / ".hidden.py").write_text("target_value = 99\n", encoding="utf-8")
    (repo / "src" / "credentials.json").write_text("target_value = 'secret'\n", encoding="utf-8")
    try:
        (repo / "src" / "linked.py").symlink_to(repo / "src" / "needle.py")
    except OSError:
        pass
    output = main._search_project_code("allowed", "target_value", max_files=250, max_matches=60)
    assert "src/needle.py:1: target_value = 42" in output
    assert ".hidden.py" not in output
    assert "credentials.json" not in output
    assert "linked.py" not in output
    assert "SECRET" not in output


def test_agent_find_files_and_git_diff_tools(monkeypatch, tmp_path):
    _prepare_ai_repo(monkeypatch, tmp_path)
    import asyncio
    import subprocess
    assert asyncio.run(main._execute_agent_tool("find_files", {"query": "example"}, "allowed")) == "src/example.py"
    rejected = asyncio.run(main._execute_agent_tool("find_files", {"query": "../"}, "allowed"))
    assert "Tool argument error" in rejected
    original_run = subprocess.run
    def fake_run(args, **kwargs):
        assert args[:3] == ["git", "diff", "--no-ext-diff"]
        assert kwargs["timeout"] == 6
        class Result:
            returncode = 0
            stdout = "diff --git a/src/example.py b/src/example.py\n+VALUE = 2\n"
            stderr = ""
        return Result()
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    output = asyncio.run(main._execute_agent_tool("git_diff", {}, "allowed"))
    assert "Exit code: 0" in output and "+VALUE = 2" in output
    monkeypatch.setattr(main.subprocess, "run", original_run)


def test_mcp_config_defaults_to_loopback_and_explicit_read_only_allowlist(monkeypatch, tmp_path):
    from app import mcp_tools
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [
        {"name": "local_docs", "url": "http://127.0.0.1:9000/mcp", "read_only_tools": ["search_docs"]},
        {"name": "remote_docs", "url": "https://example.com/mcp", "read_only_tools": ["search_docs"]},
        {"name": "bad_url", "url": "http://user:pass@127.0.0.1:9001/mcp", "read_only_tools": ["search_docs"]},
    ]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    monkeypatch.delenv("RZ_MCP_ALLOW_REMOTE", raising=False)
    servers = mcp_tools._load_servers()
    assert [server["name"] for server in servers] == ["local_docs"]
    assert servers[0]["read_only_tools"] == {"search_docs"}


def test_mcp_discovery_exposes_only_explicitly_allowlisted_tools(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app import mcp_tools
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "name": "docs", "url": "http://localhost:9000/mcp", "read_only_tools": ["search_docs"]
    }]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    async def fake_session(server, operation):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="search_docs", description="Search documentation", inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
            SimpleNamespace(name="delete_docs", description="Delete documents", inputSchema={"type": "object", "properties": {}}),
        ])
    monkeypatch.setattr(mcp_tools, "_with_session", fake_session)
    schemas, status = asyncio.run(mcp_tools.discover_tools())
    assert [item["function"]["name"] for item in schemas] == ["mcp__docs__search_docs"]
    assert status == [{"name": "docs", "status": "connected", "tools": "1"}]


def test_mcp_status_route_is_local_mode_only(monkeypatch):
    monkeypatch.setattr(main, "APP_MODE", "local")
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"localhost"})
    monkeypatch.setattr(main, "APP_ORIGINS", set())
    client = TestClient(main.app, base_url="http://localhost")
    response = client.get("/api/ai/mcp/status")
    assert response.status_code == 200
    assert "servers" in response.json()
    monkeypatch.setattr(main, "APP_MODE", "saas")
    disabled = client.get("/api/ai/mcp/status")
    assert disabled.status_code == 501


def test_mcp_call_only_invokes_allowlisted_server_tool(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app import mcp_tools
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "name": "docs", "url": "http://localhost:9000/mcp", "read_only_tools": ["search_docs"]
    }]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    calls = []
    async def fake_session(server, operation):
        class FakeSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name="search_docs"), SimpleNamespace(name="delete_docs")])
            async def call_tool(self, name, args):
                calls.append((name, args))
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="docs result")], isError=False, structuredContent=None)
        return await operation(FakeSession())
    monkeypatch.setattr(mcp_tools, "_with_session", fake_session)
    output = asyncio.run(mcp_tools.call_tool("mcp__docs__search_docs", {"query": "auth"}))
    assert output == "docs result"
    assert calls == [("search_docs", {"query": "auth"})]
    rejected = asyncio.run(mcp_tools.call_tool("mcp__docs__delete_docs", {}))
    assert "rejected" in rejected.lower()
    assert len(calls) == 1


def test_github_repository_inventory_paginates_until_complete(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app import mcp_tools

    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "name": "github", "url": "https://api.githubcopilot.com/mcp/readonly",
        "enabled": True, "headers_env": {"Authorization": "RZ_GITHUB_MCP_TOKEN"},
        "read_only_tools": ["search_repositories"]
    }]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    monkeypatch.setenv("RZ_MCP_ALLOW_REMOTE", "1")
    monkeypatch.setenv("RZ_GITHUB_OWNER", "owner")
    page_calls = []

    async def fake_session(server, operation):
        class FakeSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name="search_repositories")])
            async def call_tool(self, name, args):
                page = args["page"]
                page_calls.append(page)
                start = (page - 1) * 10
                names = [f"owner/repo-{i:02d}" for i in range(start, min(start + 10, 12))]
                payload = {"total_count": 12, "incomplete_results": False, "items": [
                    {"full_name": repo, "private": repo.endswith("01"), "fork": False,
                     "archived": False, "default_branch": "main", "html_url": f"https://github.com/{repo}"}
                    for repo in names
                ]}
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], isError=False, structuredContent=None)
        return await operation(FakeSession())

    monkeypatch.setattr(mcp_tools, "_with_session", fake_session)
    output = asyncio.run(mcp_tools.call_tool("mcp__github__list_all_repositories", {"query": "user:OWNER"}))
    inventory = json.loads(output)
    assert inventory["complete"] is True
    assert inventory["total_count"] == inventory["collected"] == 12
    assert inventory["pages_checked"] == 2
    assert len(inventory["repositories"]) == 12
    assert page_calls == [1, 2]


def test_github_accessible_repository_inventory_uses_rest_affiliations_and_pagination(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app import mcp_tools

    monkeypatch.setenv("RZ_GITHUB_MCP_TOKEN", "test-token-not-a-real-secret")
    calls = []
    pages = [
        ([{"full_name": "owner/repo-a", "owner": {"login": "owner"}, "private": True,
           "fork": False, "archived": False, "default_branch": "main", "html_url": "https://github.com/owner/repo-a"},
          {"full_name": "org/repo-b", "owner": {"login": "org"}, "private": False,
           "fork": True, "archived": False, "default_branch": "trunk", "html_url": "https://github.com/org/repo-b"}],
         '<https://api.github.com/user/repos?page=2>; rel="next", <https://api.github.com/user/repos?page=2>; rel="last"'),
        ([{"full_name": "collab/repo-c", "owner": {"login": "collab"}, "private": True,
           "fork": False, "archived": True, "default_branch": "main", "html_url": "https://github.com/collab/repo-c"}], ""),
    ]

    class FakeResponse:
        status_code = 200
        def __init__(self, payload, link):
            self._payload = payload
            self.headers = {"Link": link}
        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, *, headers, params):
            assert url == "https://api.github.com/user/repos"
            assert headers["Authorization"] == "Bearer test-token-not-a-real-secret"
            assert params["affiliation"] == "owner,collaborator,organization_member"
            calls.append(params["page"])
            payload, link = pages[params["page"] - 1]
            return FakeResponse(payload, link)

    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", FakeAsyncClient)
    output = asyncio.run(mcp_tools.call_tool("mcp__github__list_accessible_repositories", {"max_pages": 10}))
    inventory = json.loads(output)
    assert inventory["complete"] is True
    assert inventory["collected"] == 3
    assert inventory["pages_checked"] == 2
    assert [repo["full_name"] for repo in inventory["repositories"]] == ["owner/repo-a", "org/repo-b", "collab/repo-c"]
    assert {repo["owner"] for repo in inventory["repositories"]} == {"owner", "org", "collab"}
    assert calls == [1, 2]
    assert "test-token-not-a-real-secret" not in output


def test_github_repository_search_compacts_long_json_without_corrupting_it(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app import mcp_tools

    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "name": "github", "url": "https://api.githubcopilot.com/mcp/readonly",
        "enabled": True, "headers_env": {"Authorization": "RZ_GITHUB_MCP_TOKEN"},
        "read_only_tools": ["search_repositories"]
    }]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    monkeypatch.setenv("RZ_MCP_ALLOW_REMOTE", "1")
    monkeypatch.setenv("RZ_GITHUB_MCP_TOKEN", "test-token-not-a-real-secret")
    long_description = "repository description " * 500
    payload = {"total_count": 1, "incomplete_results": False, "items": [{
        "full_name": "owner/repo", "name": "repo", "description": long_description,
        "private": True, "fork": False, "archived": False, "default_branch": "main",
        "html_url": "https://github.com/owner/repo", "updated_at": "2026-10-01T00:00:00Z",
        "unneeded_large_field": "z" * 5000
    }]}
    calls = []

    async def fake_session(server, operation):
        class FakeSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name="search_repositories")])
            async def call_tool(self, name, args):
                calls.append((name, args))
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], isError=False, structuredContent=None)
        return await operation(FakeSession())

    monkeypatch.setattr(mcp_tools, "_with_session", fake_session)
    output = asyncio.run(mcp_tools.call_tool("mcp__github__search_repositories", {"query": "user:owner", "perPage": 10, "page": 1}))
    parsed = json.loads(output)
    assert parsed["total_count"] == 1
    assert parsed["items"][0]["full_name"] == "owner/repo"
    assert parsed["items"][0]["private"] is True
    assert len(parsed["items"][0]["description"]) <= 180
    assert "unneeded_large_field" not in output
    assert calls == [("search_repositories", {"query": "user:owner", "perPage": 10, "page": 1})]


def test_github_mcp_config_script_writes_readonly_config_without_token(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    config = tmp_path / "private" / "mcp_servers.json"
    env = os.environ.copy()
    env["RZ_MCP_CONFIG"] = str(config)
    env.pop("RZ_GITHUB_MCP_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    result = subprocess.run([sys.executable, "scripts/configure-github-mcp.py"], cwd=main.STATIC_DIR.parent, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    payload = json.loads(config.read_text(encoding="utf-8"))
    server = payload["servers"][0]
    assert server["name"] == "github"
    assert server["url"] == "https://api.githubcopilot.com/mcp/readonly"
    assert server["headers"]["X-MCP-Toolsets"] == "repos,users,issues,pull_requests,actions"
    assert server["headers_env"] == {"Authorization": "RZ_GITHUB_MCP_TOKEN"}
    assert "create_issue" not in server["read_only_tools"]
    assert "pull_request_read" in server["read_only_tools"]
    assert "RZ_GITHUB_MCP_TOKEN" in result.stdout
    assert "github_pat_" not in config.read_text(encoding="utf-8")
    assert (config.stat().st_mode & 0o777) == 0o600
    assert (config.parent.stat().st_mode & 0o777) == 0o700


def test_mcp_static_headers_are_feature_headers_only(monkeypatch, tmp_path):
    from app import mcp_tools
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [
        {"name": "valid", "url": "http://127.0.0.1:9000/mcp", "read_only_tools": ["search"], "headers": {"X-MCP-Readonly": "true"}},
        {"name": "invalid", "url": "http://127.0.0.1:9001/mcp", "read_only_tools": ["search"], "headers": {"Authorization": "Bearer should-not-be-literal"}},
    ]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    servers = mcp_tools._load_servers()
    assert [item["name"] for item in servers] == ["valid"]
    assert mcp_tools._headers(servers[0]) == {"X-MCP-Readonly": "true"}


def test_github_mcp_remote_requires_explicit_opt_in_and_https(monkeypatch, tmp_path):
    from app import mcp_tools
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "name": "github", "url": "https://api.githubcopilot.com/mcp/readonly",
        "headers_env": {"Authorization": "RZ_GITHUB_MCP_TOKEN"}, "headers": {"X-MCP-Readonly": "true"},
        "read_only_tools": ["get_me"]
    }]}), encoding="utf-8")
    monkeypatch.setenv("RZ_MCP_CONFIG", str(config))
    monkeypatch.delenv("RZ_MCP_ALLOW_REMOTE", raising=False)
    assert mcp_tools._load_servers() == []
    monkeypatch.setenv("RZ_MCP_ALLOW_REMOTE", "1")
    servers = mcp_tools._load_servers()
    assert len(servers) == 1 and servers[0]["name"] == "github"
    assert servers[0]["headers_env"]["Authorization"] == "RZ_GITHUB_MCP_TOKEN"
