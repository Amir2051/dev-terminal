#!/usr/bin/env python3
"""Add the official GitHub remote MCP server as a read-only RZ Agent integration.

This writes configuration only. It never reads or writes .env and never handles a token.
Set RZ_GITHUB_MCP_TOKEN and RZ_MCP_ALLOW_REMOTE=1 in the app process environment.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG = Path(os.getenv("RZ_MCP_CONFIG", "~/.config/dev-terminal/mcp_servers.json")).expanduser()
SERVER = {
    "name": "github",
    "url": "https://api.githubcopilot.com/mcp/readonly",
    "enabled": True,
    "headers": {"X-MCP-Toolsets": "repos,users,issues,pull_requests,actions"},
    "headers_env": {"Authorization": "RZ_GITHUB_MCP_TOKEN"},
    "read_only_tools": [
        "get_me", "search_repositories", "search_code", "get_file_contents",
        "list_branches", "list_commits", "get_commit",
        "list_issues", "issue_read", "search_issues",
        "list_pull_requests", "pull_request_read", "search_pull_requests",
        "list_workflows", "list_workflow_runs", "get_workflow_run", "get_job_logs",
    ],
}


def main() -> int:
    if CONFIG.is_symlink():
        print(f"Refusing to replace symlinked MCP config: {CONFIG}", file=sys.stderr)
        return 2
    existing = {"servers": []}
    if CONFIG.exists():
        if not CONFIG.is_file() or CONFIG.stat().st_size > 64 * 1024:
            print("Existing MCP config is not a regular file or exceeds 64 KiB; leaving it unchanged.", file=sys.stderr)
            return 2
        try:
            existing = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            print("Existing MCP config is invalid JSON; leaving it unchanged.", file=sys.stderr)
            return 2
    if not isinstance(existing, dict) or not isinstance(existing.get("servers", []), list):
        print("Existing MCP config has an unexpected structure; leaving it unchanged.", file=sys.stderr)
        return 2
    servers = existing.setdefault("servers", [])
    existing_server = next((i for i, item in enumerate(servers) if isinstance(item, dict) and item.get("name") == "github"), None)
    if existing_server is None:
        servers.append(SERVER)
    else:
        servers[existing_server] = SERVER
    CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG.parent, 0o700)
    temporary = CONFIG.with_name(CONFIG.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        print("Temporary config path already exists; remove it manually after inspection.", file=sys.stderr)
        return 2
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(existing, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, CONFIG)
        os.chmod(CONFIG, 0o600)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"Could not write MCP config ({exc.__class__.__name__}); check permissions.", file=sys.stderr)
        return 2
    print(f"Configured read-only GitHub MCP server in {CONFIG}")
    print("No token was read or saved. Set RZ_GITHUB_MCP_TOKEN in the environment used to start RZ Agent.")
    print("Also set RZ_MCP_ALLOW_REMOTE=1 to explicitly permit this HTTPS MCP server, then restart the local API.")
    print("The server URL and local tool allowlist both restrict access to read-only operations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
