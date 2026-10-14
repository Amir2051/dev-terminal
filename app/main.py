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

load_dotenv()
PROJECTS_DIR = Path(os.path.expandvars(os.path.expanduser(os.getenv("PROJECTS_DIR", "~/Projects")))).resolve()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
TERMINAL_QUEUE_TOKEN = os.getenv("TERMINAL_QUEUE_TOKEN", "").strip()
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


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    client_host = request.client.host if request.client else None
    if not _loopback(client_host):
        return PlainTextResponse("Localhost access only", status_code=403)
    host_header = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
    if host_header and host_header not in {"127.0.0.1", "localhost", "::1"}:
        return PlainTextResponse("Unexpected Host header", status_code=403)
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            return PlainTextResponse("Unexpected Origin", status_code=403)
    return await call_next(request)


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
        for page in range(1, 11):
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
    return repos


@app.get("/api/health")
async def health():
    return {"status": "ok", "localhost_only": True, "projects_dir": str(PROJECTS_DIR)}


@app.get("/api/repos")
async def list_github_repos():
    try:
        return {"configured": bool(GITHUB_TOKEN and GITHUB_TOKEN != "github_pat_replace_me"), "repos": await _github_repos()}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API request failed: {type(exc).__name__}")


@app.get("/api/projects")
async def list_local_projects():
    return {"projects_dir": str(PROJECTS_DIR), "projects": await asyncio.to_thread(_list_projects)}


@app.get("/api/tools")
async def list_tools():
    return {"tools": [
        {"id": key, "label": value["label"], "description": value["description"], "write": value["write"], "needs_project": value["needs_project"]}
        for key, value in TOOLS.items()
    ]}


@app.post("/api/tools/queue")
async def queue_tool(body: QueueToolRequest):
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
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
