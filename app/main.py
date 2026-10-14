from __future__ import annotations

import asyncio
import os
import secrets
import shlex
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import verify_access_token

load_dotenv()
APP_MODE = os.getenv("APP_MODE", "local").strip().lower()
if APP_MODE not in {"local", "saas"}:
    raise RuntimeError("APP_MODE must be either 'local' or 'saas'.")

PROJECTS_DIR = Path(os.path.expandvars(os.path.expanduser(os.getenv("PROJECTS_DIR", "~/Projects")))).resolve()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
TERMINAL_QUEUE_TOKEN = os.getenv("TERMINAL_QUEUE_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
ALLOWED_HOSTS = {item.strip().lower() for item in os.getenv("ALLOWED_HOSTS", "").split(",") if item.strip()}
APP_ORIGINS = {item.strip().rstrip("/") for item in os.getenv("APP_ORIGINS", "").split(",") if item.strip()}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

TOOLS: dict[str, dict[str, Any]] = {
    "pwd": {"label": "Print working directory", "description": "Show the shell's current directory.", "command": "pwd", "write": False, "needs_project": False},
    "list-files": {"label": "List files", "description": "List files in the selected repository.", "command": "ls -la", "write": False, "needs_project": True},
    "git-status": {"label": "Git status", "description": "Show branch and working-tree status.", "command": "git status --short --branch", "write": False, "needs_project": True},
    "git-log": {"label": "Recent commits", "description": "Show the last 10 commits.", "command": "git --no-pager log -10 --oneline --decorate", "write": False, "needs_project": True},
    "git-diff": {"label": "Diff summary", "description": "Show a summary of tracked changes.", "command": "git --no-pager diff --stat", "write": False, "needs_project": True},
    "pytest": {"label": "Run pytest", "description": "Run the repository test suite.", "command": "pytest -q", "write": False, "needs_project": True},
    "ruff": {"label": "Run Ruff", "description": "Lint the repository with Ruff.", "command": "ruff check .", "write": False, "needs_project": True},
    "git-fetch": {"label": "Git fetch", "description": "Fetch remote refs and update local Git metadata.", "command": "git fetch --all --prune", "write": True, "needs_project": True},
    "git-pull": {"label": "Git pull", "description": "Pull upstream changes into the current branch.", "command": "git pull --ff-only", "write": True, "needs_project": True},
}
_command_queue: deque[str] = deque(maxlen=20)
_queue_lock = threading.Lock()


class QueueToolRequest(BaseModel):
    tool_id: str = Field(min_length=1, max_length=64)
    project: str | None = Field(default=None, max_length=200)
    confirmed: bool = False


app = FastAPI(title="dev-terminal", docs_url=None, redoc_url=None, openapi_url=None)


def _loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _host_name(value: str) -> str:
    try:
        return (urlparse("//" + value).hostname or "").lower()
    except ValueError:
        return ""


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    host_header = request.headers.get("host", "")
    host_name = _host_name(host_header)

    if APP_MODE == "local":
        client_host = request.client.host if request.client else None
        if not _loopback(client_host):
            return PlainTextResponse("Localhost access only", status_code=403)
        if host_name and host_name not in {"127.0.0.1", "localhost", "::1"}:
            return PlainTextResponse("Unexpected Host header", status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
                return PlainTextResponse("Unexpected Origin", status_code=403)
        return await call_next(request)

    # SaaS mode is opt-in. Configure an explicit host and browser-origin allowlist.
    if not ALLOWED_HOSTS:
        return PlainTextResponse("SaaS mode requires ALLOWED_HOSTS configuration", status_code=503)
    if not host_name or host_name not in ALLOWED_HOSTS:
        return PlainTextResponse("Unexpected Host header", status_code=403)
    if host_name not in {"localhost", "127.0.0.1", "::1"} and request.url.scheme != "https":
        return PlainTextResponse("HTTPS is required for non-local SaaS hosts", status_code=400)
    origin = request.headers.get("origin")
    if origin:
        normalized_origin = origin.rstrip("/")
        if not APP_ORIGINS or normalized_origin not in APP_ORIGINS:
            return PlainTextResponse("Unexpected Origin", status_code=403)
    return await call_next(request)


def _require_local_mode() -> None:
    if APP_MODE != "local":
        raise HTTPException(
            status_code=501,
            detail="This endpoint accesses the server's local environment and is disabled in SaaS mode until authenticated local-agent pairing or isolated cloud workspaces are implemented.",
        )


def _project_path(name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid project name")
    candidate = PROJECTS_DIR / name
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    if resolved.parent != PROJECTS_DIR or candidate.is_symlink() or not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Project must be a direct, non-symlink directory")
    if not (resolved / ".git").exists():
        raise HTTPException(status_code=400, detail="Directory is not a Git repository")
    return resolved


def _run_git(path: Path, *args: str, timeout: float = 4.0) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args], check=False, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _project_status(path: Path) -> dict[str, Any]:
    branch = _run_git(path, "branch", "--show-current") or "(detached or unknown)"
    dirty = bool(_run_git(path, "status", "--porcelain", "--untracked-files=normal"))
    upstream = _run_git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    ahead = behind = None
    if upstream:
        counts = _run_git(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        if counts:
            try:
                ahead, behind = [int(part) for part in counts.split()]
            except (ValueError, TypeError):
                pass
    return {"branch": branch, "dirty": dirty, "upstream": upstream or None, "ahead": ahead, "behind": behind}


def _list_projects() -> list[dict[str, Any]]:
    if not PROJECTS_DIR.exists():
        return []
    try:
        children = sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    projects = []
    for child in children:
        if child.is_symlink() or not child.is_dir() or not (child / ".git").exists():
            continue
        try:
            resolved = child.resolve(strict=True)
            if resolved.parent != PROJECTS_DIR:
                continue
            projects.append({"name": child.name, "path": str(resolved), **_project_status(resolved)})
        except OSError:
            continue
    return projects


async def _github_repos() -> list[dict[str, Any]]:
    if not GITHUB_TOKEN or GITHUB_TOKEN == "github_pat_replace_me":
        return []
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }
    repos = []
    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        page = 1
        while True:
            response = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
            )
            if response.status_code == 401:
                raise HTTPException(status_code=502, detail="GitHub rejected GITHUB_TOKEN; check .env and token permissions")
            if response.status_code == 403:
                raise HTTPException(status_code=502, detail="GitHub API access denied or rate limit reached")
            response.raise_for_status()
            batch = response.json()
            repos.extend({
                "name": item["name"], "full_name": item["full_name"], "private": item["private"],
                "html_url": item["html_url"], "clone_url": item["clone_url"],
                "default_branch": item.get("default_branch"), "archived": item.get("archived", False),
                "description": item.get("description"),
            } for item in batch)
            if len(batch) < 100:
                break
            page += 1
    return repos


@app.get("/api/health")
async def health():
    result = {"status": "ok", "mode": APP_MODE, "localhost_only": APP_MODE == "local"}
    if APP_MODE == "local":
        result["projects_dir"] = str(PROJECTS_DIR)
    else:
        result["auth_configured"] = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
        result["host_allowlist_configured"] = bool(ALLOWED_HOSTS)
    return result


@app.get("/api/public-config")
async def public_config():
    if APP_MODE != "saas":
        raise HTTPException(status_code=404, detail="Not found")
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=503, detail="Configure SUPABASE_URL and SUPABASE_ANON_KEY on the server.")
    return {"supabase_url": SUPABASE_URL, "supabase_anon_key": SUPABASE_ANON_KEY}


@app.get("/api/me")
async def current_user(request: Request):
    if APP_MODE != "saas":
        return {"authenticated": False, "mode": "local"}
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Bearer access token required.")
    claims = verify_access_token(token.strip())
    return {
        "authenticated": True,
        "id": claims["sub"],
        "email": claims.get("email"),
        "role": claims.get("role", "authenticated"),
    }


@app.get("/api/repos")
async def list_github_repos():
    _require_local_mode()
    try:
        return {"configured": bool(GITHUB_TOKEN and GITHUB_TOKEN != "github_pat_replace_me"), "repos": await _github_repos()}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API request failed: {type(exc).__name__}")


@app.get("/api/projects")
async def list_local_projects():
    _require_local_mode()
    return {"projects_dir": str(PROJECTS_DIR), "projects": await asyncio.to_thread(_list_projects)}


@app.get("/api/tools")
async def list_tools():
    _require_local_mode()
    return {"tools": [
        {"id": key, "label": value["label"], "description": value["description"], "write": value["write"], "needs_project": value["needs_project"]}
        for key, value in TOOLS.items()
    ]}


@app.post("/api/tools/queue")
async def queue_tool(body: QueueToolRequest):
    _require_local_mode()
    tool = TOOLS.get(body.tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Unknown tool")
    if tool["write"] and not body.confirmed:
        raise HTTPException(status_code=400, detail="This action requires explicit confirmation")
    command = str(tool["command"])
    if tool["needs_project"]:
        if not body.project:
            raise HTTPException(status_code=400, detail="Select a project first")
        path = _project_path(body.project)
        command = f"cd -- {shlex.quote(str(path))} && {command}"
    with _queue_lock:
        if len(_command_queue) >= _command_queue.maxlen:
            raise HTTPException(status_code=429, detail="Terminal command queue is full")
        _command_queue.append(command)
    return {"queued": True, "tool": body.tool_id, "project": body.project, "message": "Command queued; it runs when the ttyd shell next reaches a prompt."}


@app.get("/api/terminal/next-command")
async def next_terminal_command(request: Request):
    _require_local_mode()
    if not TERMINAL_QUEUE_TOKEN:
        raise HTTPException(status_code=503, detail="Set TERMINAL_QUEUE_TOKEN in .env")
    supplied = request.headers.get("x-terminal-queue-token", "")
    if not secrets.compare_digest(supplied, TERMINAL_QUEUE_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid terminal queue token")
    if request.headers.get("origin"):
        raise HTTPException(status_code=403, detail="Shell polling endpoint only")
    with _queue_lock:
        command = _command_queue.popleft() if _command_queue else None
    return {"command": command}


@app.get("/", include_in_schema=False)
async def index():
    if APP_MODE == "saas":
        return FileResponse(STATIC_DIR / "saas.html")
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
