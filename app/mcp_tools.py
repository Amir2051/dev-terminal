"""Opt-in, read-only MCP client for RZ Agent.

Only administrator-configured Streamable HTTP servers are supported here. Tools must
be explicitly listed in each server's read_only_tools allowlist before the model can
see or invoke them. Stdio servers are intentionally deferred until command isolation
is implemented because starting an MCP process executes code as the app's OS user.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession
try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # Compatibility with older MCP SDK releases in the supported range.
    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

_DEFAULT_CONFIG = Path.home() / ".config" / "dev-terminal" / "mcp_servers.json"
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
_MAX_CONFIG_BYTES = 64 * 1024
_MAX_SERVERS = 10
_MAX_TOOLS_PER_SERVER = 50
_MAX_EXPOSED_TOOLS = 20


def _config_path() -> Path:
    raw = os.getenv("RZ_MCP_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_CONFIG


def _load_servers() -> list[dict[str, Any]]:
    path = _config_path()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    servers = payload.get("servers", []) if isinstance(payload, dict) else []
    if not isinstance(servers, list):
        return []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in servers[:_MAX_SERVERS]:
        if not isinstance(item, dict) or item.get("enabled", True) is not True:
            continue
        name, url = item.get("name"), item.get("url")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or name in seen:
            continue
        if not isinstance(url, str) or len(url) > 2048:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            continue
        is_loopback = parsed.hostname.lower() in {"127.0.0.1", "localhost", "::1"}
        remote_enabled = os.getenv("RZ_MCP_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}
        if not is_loopback and (parsed.scheme != "https" or not remote_enabled):
            continue
        read_only_tools = item.get("read_only_tools", [])
        if not isinstance(read_only_tools, list) or any(not isinstance(t, str) for t in read_only_tools):
            continue
        headers_env = item.get("headers_env", {})
        if not isinstance(headers_env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers_env.items()):
            continue
        static_headers = item.get("headers", {})
        if not isinstance(static_headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in static_headers.items()):
            continue
        # Static config may set non-secret feature headers only. Authentication always comes from env.
        if any(not re.fullmatch(r"X-MCP-[A-Za-z0-9-]{1,60}", k) or len(v) > 200 for k, v in static_headers.items()):
            continue
        seen.add(name)
        accepted.append({"name": name, "url": url, "read_only_tools": set(read_only_tools), "headers_env": headers_env, "headers": static_headers})
    return accepted


def _headers(server: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = dict(server.get("headers", {}))
    for header, env_name in server["headers_env"].items():
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", header) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", env_name):
            continue
        value = os.getenv(env_name, "")
        if value and len(value) <= 4096 and "\r" not in value and "\n" not in value:
            result[header] = value
    return result


def _tool_key(server_name: str, tool_name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)[:60]
    return f"mcp__{server_name}__{safe}"[:120]


async def _with_session(server: dict[str, Any], operation):
    async with asyncio.timeout(12):
        async with streamable_http_client(server["url"], headers=_headers(server), timeout=8, sse_read_timeout=8) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await operation(session)


async def discover_tools() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return Ollama-compatible schemas and safe per-server status, never secret values."""
    schemas: list[dict[str, Any]] = []
    status: list[dict[str, str]] = []
    github_search_available = False
    for server in _load_servers():
        try:
            result = await _with_session(server, lambda session: session.list_tools())
            available = 0
            for tool in result.tools[:_MAX_TOOLS_PER_SERVER]:
                if len(schemas) >= _MAX_EXPOSED_TOOLS:
                    break
                if tool.name not in server["read_only_tools"]:
                    continue
                if server["name"] == "github" and tool.name == "search_repositories":
                    github_search_available = True
                schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object", "properties": {}}
                schemas.append({"type": "function", "function": {
                    "name": _tool_key(server["name"], tool.name),
                    "description": f"Read-only MCP tool from configured server '{server['name']}': {str(tool.description or tool.name)[:500]}",
                    "parameters": schema,
                }})
                available += 1
            status.append({"name": server["name"], "status": "connected", "tools": str(available)})
        except Exception as exc:
            # Do not return exception text: transports may include URLs or headers.
            status.append({"name": server["name"], "status": f"unavailable ({exc.__class__.__name__})", "tools": "0"})
    if github_search_available and len(schemas) <= _MAX_EXPOSED_TOOLS - 2:
        schemas.append({"type": "function", "function": {
            "name": "mcp__github__list_all_repositories",
            "description": "Read-only paginated repository search for a specific owner. Use query 'user:OWNER' or 'org:OWNER'. This is search-based and may omit repositories; check complete and total_count.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "GitHub search query such as user:Amir2051 or org:ORGNAME."},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}
            }},
        }})
        schemas.append({"type": "function", "function": {
            "name": "mcp__github__list_accessible_repositories",
            "description": "Preferred read-only inventory of repositories accessible to the authenticated GitHub token, including owner, collaborator, and organization-member repositories. Uses GitHub's authenticated /user/repos API and follows pagination. Returns repository names, owners, visibility, default branch, and whether the inventory is complete. It can only return repositories this token actually has access to.",
            "parameters": {"type": "object", "properties": {
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}
            }},
        }})
        for server_status in status:
            if server_status.get("name") == "github" and server_status.get("status") == "connected":
                server_status["tools"] = str(int(server_status.get("tools", "0")) + 2)
    return schemas, status


async def call_tool(tool_key: str, arguments: dict[str, Any]) -> str:
    servers = _load_servers()
    if tool_key == "mcp__github__list_accessible_repositories":
        token = os.getenv("RZ_GITHUB_MCP_TOKEN", "").strip()
        if not token:
            return json.dumps({"complete": False, "collected": 0, "error": "RZ_GITHUB_MCP_TOKEN is not configured in the API process environment."})
        try:
            max_pages = int(arguments.get("max_pages", 10))
        except (TypeError, ValueError):
            max_pages = 10
        max_pages = max(1, min(max_pages, 10))
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repositories: list[dict[str, Any]] = []
        pages_checked = 0
        has_next = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0), follow_redirects=False) as client:
                for page in range(1, max_pages + 1):
                    response = await client.get(
                        "https://api.github.com/user/repos",
                        headers=headers,
                        params={
                            "visibility": "all",
                            "affiliation": "owner,collaborator,organization_member",
                            "sort": "full_name",
                            "direction": "asc",
                            "per_page": 100,
                            "page": page,
                        },
                    )
                    if response.status_code in {401, 403}:
                        return json.dumps({"complete": False, "collected": len(repositories),
                                           "pages_checked": pages_checked,
                                           "error": f"GitHub repository inventory was denied (HTTP {response.status_code}); check token validity, resource-owner access, and repository metadata permission."})
                    if response.status_code != 200:
                        return json.dumps({"complete": False, "collected": len(repositories),
                                           "pages_checked": pages_checked,
                                           "error": f"GitHub repository inventory failed (HTTP {response.status_code})."})
                    payload = response.json()
                    if not isinstance(payload, list):
                        return json.dumps({"complete": False, "collected": len(repositories),
                                           "pages_checked": pages_checked,
                                           "error": "GitHub returned an unexpected repository inventory format."})
                    pages_checked += 1
                    for repo in payload:
                        if not isinstance(repo, dict) or not isinstance(repo.get("full_name"), str):
                            continue
                        owner_data = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
                        repositories.append({
                            "full_name": repo["full_name"],
                            "owner": owner_data.get("login"),
                            "private": bool(repo.get("private")),
                            "fork": bool(repo.get("fork")),
                            "archived": bool(repo.get("archived")),
                            "default_branch": repo.get("default_branch"),
                            "html_url": repo.get("html_url"),
                        })
                    has_next = 'rel="next"' in response.headers.get("Link", "")
                    if not has_next:
                        break
        except httpx.TimeoutException:
            return json.dumps({"complete": False, "collected": len(repositories),
                               "pages_checked": pages_checked, "error": "GitHub repository inventory timed out."})
        except httpx.HTTPError:
            return json.dumps({"complete": False, "collected": len(repositories),
                               "pages_checked": pages_checked, "error": "GitHub repository inventory request failed."})
        complete = not has_next
        inventory = {"source": "GitHub REST API /user/repos", "query": "visibility=all; affiliation=owner,collaborator,organization_member",
                     "collected": len(repositories), "returned": len(repositories), "pages_checked": pages_checked,
                     "complete": complete, "repositories_truncated": False, "repositories": repositories}
        encoded = json.dumps(inventory, ensure_ascii=False, separators=(",", ":"))
        while len(encoded) > 15_000 and inventory["repositories"]:
            inventory["repositories"].pop()
            inventory["returned"] = len(inventory["repositories"])
            inventory["repositories_truncated"] = True
            encoded = json.dumps(inventory, ensure_ascii=False, separators=(",", ":"))
        return encoded
    if tool_key == "mcp__github__list_all_repositories":
        server = next((item for item in servers if item["name"] == "github"), None)
        if server is None or "search_repositories" not in server["read_only_tools"]:
            return "Repository inventory rejected: the GitHub repository search tool is not configured in the read-only allowlist."
        owner = os.getenv("RZ_GITHUB_OWNER", "Amir2051").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", owner):
            owner = "Amir2051"
        query = arguments.get("query")
        if query is None or (isinstance(query, str) and query.strip().casefold() in {"owner", "user:owner", "org:owner"}):
            query = f"user:{owner}"
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            return "Repository inventory requires a valid GitHub search query (for example user:Amir2051)."
        try:
            max_pages = int(arguments.get("max_pages", 10))
        except (TypeError, ValueError):
            max_pages = 10
        max_pages = max(1, min(max_pages, 10))
        repos: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_count = None
        pages_checked = 0
        for page in range(1, max_pages + 1):
            raw = await call_tool("mcp__github__search_repositories", {"query": query.strip(), "perPage": 10, "page": page})
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return json.dumps({"query": query, "complete": False, "pages_checked": pages_checked,
                                   "collected": len(repos), "error": "A page could not be parsed; inventory is incomplete."})
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                return json.dumps({"query": query, "complete": False, "pages_checked": pages_checked,
                                   "collected": len(repos), "error": "GitHub returned an unexpected search result; inventory is incomplete."})
            pages_checked += 1
            if total_count is None:
                total_count = payload.get("total_count")
            for repo in payload["items"]:
                if not isinstance(repo, dict):
                    continue
                full_name = repo.get("full_name")
                if not isinstance(full_name, str) or full_name in seen:
                    continue
                seen.add(full_name)
                # Inventory output is consumed by a small local model; keep it compact so
                # the chat endpoint's bounded tool-message size never cuts the JSON in half.
                repos.append({key: repo[key] for key in ("full_name", "private") if key in repo})
            if not payload["items"] or (isinstance(total_count, int) and len(repos) >= total_count):
                break
        complete = isinstance(total_count, int) and len(repos) >= total_count
        return json.dumps({"query": query.strip(), "total_count": total_count, "collected": len(repos),
                           "pages_checked": pages_checked, "complete": complete, "repositories": repos},
                          ensure_ascii=False, separators=(",", ":"))[:16000]
    for server in servers:
        prefix = f"mcp__{server['name']}__"
        if not tool_key.startswith(prefix):
            continue
        wanted = tool_key[len(prefix):]
        try:
            result = await _with_session(server, lambda session: session.list_tools())
            for tool in result.tools[:_MAX_TOOLS_PER_SERVER]:
                if _tool_key(server["name"], tool.name) != tool_key or tool.name not in server["read_only_tools"]:
                    continue
                result = await _with_session(server, lambda session: session.call_tool(tool.name, arguments))
                blocks = []
                is_repository_search = tool.name == "search_repositories"
                for item in getattr(result, "content", []) or []:
                    if getattr(item, "type", None) == "text":
                        # Repository search is JSON: truncating before parsing can corrupt the
                        # first page when descriptions are long. Bound the raw payload, parse it,
                        # then return a compact allowlisted projection below.
                        limit = 256_000 if is_repository_search else 4_000
                        blocks.append(str(getattr(item, "text", ""))[:limit])
                if getattr(result, "isError", False):
                    return "MCP tool reported an error: " + "\n".join(blocks)[:6000]
                raw_output = "\n".join(blocks)
                if not raw_output:
                    raw_output = json.dumps(getattr(result, "structuredContent", None), ensure_ascii=False, default=str)
                if is_repository_search:
                    try:
                        payload = json.loads(raw_output)
                    except (json.JSONDecodeError, TypeError):
                        return "GitHub repository search returned malformed or oversized JSON; retry with perPage 10."
                    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                        compact_items = []
                        for repo in payload["items"][:100]:
                            if not isinstance(repo, dict):
                                continue
                            compact_items.append({key: repo[key] for key in (
                                "full_name", "name", "description", "private", "fork", "archived",
                                "default_branch", "html_url", "updated_at", "owner"
                            ) if key in repo and key != "owner"})
                            if compact_items[-1].get("description"):
                                compact_items[-1]["description"] = str(compact_items[-1]["description"])[:180]
                        payload = {"total_count": payload.get("total_count"),
                                   "incomplete_results": payload.get("incomplete_results"),
                                   "items": compact_items}
                        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:8000]
                return raw_output[:8000]
            return "MCP tool rejected: it is not in this server's explicit read-only allowlist."
        except TimeoutError:
            return "MCP tool timed out after 12 seconds."
        except Exception as exc:
            return f"MCP tool failed safely: {exc.__class__.__name__}."
    return "MCP tool rejected: no configured server exposes this tool."
