#!/usr/bin/env python3
"""Dev Terminal local agent: pairing, authenticated polling, and fixed-tool execution.

The server can choose only a tool ID and a project name. This process never invokes
a shell, never evaluates server-provided command text, and enforces its own allowlist.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".config" / "dev-terminal" / "agent.json"
POLL_SECONDS = 10
MAX_OUTPUT_CHARS = 12_000

# The argument vectors are local constants, not received from the server.
READ_ONLY_TOOLS: dict[str, tuple[str, ...]] = {
    "pwd": ("pwd",),
    "list-files": ("ls", "-la"),
    "git-status": ("git", "status", "--short", "--branch"),
    "git-log": ("git", "--no-pager", "log", "-10", "--oneline", "--decorate"),
    "git-diff": ("git", "--no-pager", "diff", "--stat"),
    "ruff": ("ruff", "check", "."),
}
APPROVAL_REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    # pytest can execute repository-defined code, so require an explicit approval.
    "pytest": ("pytest", "-q"),
    "git-fetch": ("git", "fetch", "--all", "--prune"),
    "git-pull": ("git", "pull", "--ff-only"),
}
ALL_TOOLS = {**READ_ONLY_TOOLS, **APPROVAL_REQUIRED_TOOLS}


def validate_server(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Server must be an absolute HTTPS URL (HTTP is allowed only for loopback development).")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Refusing plaintext HTTP for a non-loopback server.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Server URL must not contain credentials, query parameters, or a fragment.")
    return value.rstrip("/")


def request_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            details = json.loads(exc.read().decode("utf-8"))
            message = details.get("detail", "request rejected")
        except (ValueError, AttributeError):
            message = "request rejected"
        raise RuntimeError(f"Server returned HTTP {exc.code}: {message}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError("Could not reach the Dev Terminal server or parse its response.") from None
    if not isinstance(data, dict):
        raise TypeError("Server returned an invalid response.")
    return data


def save_config(config: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_PATH.parent, 0o700)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def pair(server_arg: str) -> int:
    try:
        server = validate_server(server_arg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    code = getpass.getpass("One-time pairing code (input hidden): ").strip()
    if not code:
        print("Pairing code is required.", file=sys.stderr)
        return 2
    try:
        result = request_json(
            f"{server}/api/agents/redeem", {"pairing_code": code, "platform": platform.system().lower()}
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    credential = result.get("credential")
    agent_id = result.get("agent_id")
    if not isinstance(credential, str) or not credential or not isinstance(agent_id, str):
        print("Server did not return a valid agent credential.", file=sys.stderr)
        return 1
    save_config({"server": server, "credential": credential, "agent_id": agent_id})
    print(f"Paired agent {agent_id} to workspace {result.get('workspace_id', '(unknown)')}.")
    print(f"Credential stored at {CONFIG_PATH} with owner-only permissions.")
    print("Agent runner is ready to poll for allowlisted tools; mutating Git tools require dashboard approval.")
    return 0


def load_config() -> dict[str, str]:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"No agent configuration found at {CONFIG_PATH}. Run the pair command first.")
    if CONFIG_PATH.stat().st_mode & 0o077:
        raise RuntimeError(f"Agent config permissions are too broad. Run chmod 600 {CONFIG_PATH}.")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server = validate_server(config["server"])
    credential = config["credential"]
    if not isinstance(credential, str) or len(credential) < 32:
        raise RuntimeError("Agent credential is missing or malformed.")
    return {"server": server, "credential": credential, "agent_id": str(config.get("agent_id", "(unknown)"))}


def project_directory(project_name: str) -> Path:
    if not project_name or project_name in {".", ".."} or "/" in project_name or "\\" in project_name or "\n" in project_name:
        raise RuntimeError("Invalid project name; refusing execution.")
    approved_projects = {
        name.strip()
        for name in os.environ.get("DEV_TERMINAL_ALLOWED_PROJECTS", "").split(",")
        if name.strip()
    }
    if not approved_projects:
        raise RuntimeError("No project allowlist configured. Set DEV_TERMINAL_ALLOWED_PROJECTS to approved folder names.")
    if project_name not in approved_projects:
        raise RuntimeError("Project is not in DEV_TERMINAL_ALLOWED_PROJECTS; refusing execution.")
    configured_root = os.environ.get("DEV_TERMINAL_PROJECTS_DIR", str(Path.home() / "Projects"))
    try:
        root = Path(configured_root).expanduser().resolve(strict=True)
    except OSError:
        raise RuntimeError("Agent projects root is unavailable. Set DEV_TERMINAL_PROJECTS_DIR to an approved directory.") from None
    candidate = root / project_name
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError("Project must be a direct, non-symlink directory.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise RuntimeError("Project directory could not be resolved.") from None
    git_marker = resolved / ".git"
    if resolved.parent != root or not git_marker.exists() or git_marker.is_symlink():
        raise RuntimeError("Project must be a direct child Git repository of the configured projects root.")
    return resolved


def tool_argv(tool_id: str, cwd: Path) -> list[str]:
    if tool_id not in ALL_TOOLS:
        raise RuntimeError("Unknown tool ID; refusing execution.")
    args = list(ALL_TOOLS[tool_id])
    if tool_id == "pytest":
        # Run pytest as a module so Python includes the repository root on sys.path.
        # This avoids collection failures such as "No module named app" when invoking
        # the virtualenv's pytest console script directly.
        local_python = cwd / ".venv" / "bin" / "python"
        if local_python.is_file():
            return [str(local_python), "-m", "pytest", "-q"]
        if shutil.which("pytest") is None:
            return [sys.executable, "-m", "pytest", "-q"]
    if tool_id == "ruff":
        local_bin = cwd / ".venv" / "bin" / "ruff"
        if local_bin.is_file():
            args[0] = str(local_bin)
        elif shutil.which("ruff") is None:
            raise RuntimeError("Ruff is not installed in this environment.")
    return args


def execute_command(command: dict[str, Any]) -> tuple[str, str]:
    command_id = command.get("id")
    tool_id = command.get("tool_id")
    project_name = command.get("project_name")
    if not isinstance(command_id, str) or len(command_id) != 36:
        raise RuntimeError("Malformed command ID; refusing execution.")
    if not isinstance(tool_id, str) or tool_id not in ALL_TOOLS:
        raise RuntimeError("Server requested a tool outside the local allowlist.")
    if tool_id in APPROVAL_REQUIRED_TOOLS:
        # Defense in depth: mutating tools need a persisted approval marker as well
        # as being in the local fixed-argv allowlist.
        if not command.get("approved_by") or not command.get("approved_at"):
            raise RuntimeError("Mutating tool has no verifiable approval marker; refusing execution.")
        print(f"Executing approved mutating tool {tool_id}.", flush=True)
    if tool_id == "pwd":
        cwd = Path.home()
    else:
        if not isinstance(project_name, str):
            raise RuntimeError("Project name is missing; refusing execution.")
        cwd = project_directory(project_name)
    argv = tool_argv(tool_id, cwd)
    try:
        runner_env = {
            key: os.environ[key]
            for key in ("HOME", "PATH", "LANG", "LC_ALL", "TZ", "VIRTUAL_ENV")
            if key in os.environ
        }
        runner_env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONNOUSERSITE": "1",
        })
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            env=runner_env,
        )
    except subprocess.TimeoutExpired:
        return "failed", "Tool timed out after 60 seconds."
    except OSError as exc:
        return "failed", f"Tool could not start: {type(exc).__name__}."
    output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    output = output.strip()[:MAX_OUTPUT_CHARS]
    if result.returncode != 0:
        return "failed", f"Exit code {result.returncode}\n{output}"[:MAX_OUTPUT_CHARS]
    return "succeeded", output or "(completed with no output)"


def run() -> int:
    try:
        config = load_config()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server, credential = config["server"], config["credential"]
    print(f"Agent {config['agent_id']} runner started. Polling every {POLL_SECONDS}s; Ctrl+C stops it.")
    while True:
        try:
            result = request_json(
                f"{server}/api/agents/next-command",
                {"credential": credential},
                timeout=15,
            )
            command = result.get("command")
            if command is None:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] connected; queue empty", flush=True)
            elif not isinstance(command, dict):
                raise RuntimeError("Server returned a malformed command.")
            else:
                command_id = command.get("id")
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] received allowlisted tool {command.get('tool_id')}", flush=True)
                try:
                    outcome, output = execute_command(command)
                except RuntimeError as exc:
                    outcome, output = "failed", str(exc)
                completion = request_json(
                    f"{server}/api/agents/commands/{urllib.parse.quote(str(command_id), safe='')}/complete",
                    {"credential": credential, "outcome": outcome, "output": output[:MAX_OUTPUT_CHARS]},
                    timeout=15,
                )
                if completion.get("completed"):
                    print(f"Tool {command.get('tool_id')} finished: {outcome}.", flush=True)
                else:
                    print("Server did not confirm command completion.", file=sys.stderr, flush=True)
        except RuntimeError as exc:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pair_parser = sub.add_parser("pair", help="pair this computer using a one-time code")
    pair_parser.add_argument("--server", required=True, help="Dev Terminal base URL")
    sub.add_parser("run", help="poll for approved allowlisted tools and execute them")
    args = parser.parse_args()
    if args.command == "pair":
        return pair(args.server)
    try:
        return run()
    except KeyboardInterrupt:
        print("Agent stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
