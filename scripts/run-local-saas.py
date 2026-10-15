#!/usr/bin/env python3
"""Run Dev Terminal SaaS mode against this repo's isolated local Supabase stack.

Uses the URL and anon key from `supabase status`; does not read or modify .env,
so a hosted project's URL/key cannot accidentally be mixed into local testing.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys


def available_port() -> int:
    requested = os.environ.get("DEV_TERMINAL_PORT", "").strip()
    candidates = [int(requested)] if requested else list(range(8003, 8013))
    for port in candidates:
        if not 1 <= port <= 65535:
            raise ValueError("DEV_TERMINAL_PORT must be between 1 and 65535")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("No free port found in 8003-8012; set DEV_TERMINAL_PORT to a free port")


def main() -> int:
    status = subprocess.run(
        ["supabase", "status", "-o", "env"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode:
        print("Could not read local Supabase status. Start it with `supabase start` first.", file=sys.stderr)
        return status.returncode

    values: dict[str, str] = {}
    for line in status.stdout.splitlines():
        match = re.fullmatch(r'([A-Z_]+)=(?:"(.*)"|(.*))', line.strip())
        if match:
            values[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)

    api_url = values.get("API_URL", "").rstrip("/")
    anon_key = values.get("ANON_KEY", "")
    if not api_url or not anon_key:
        print("Local Supabase status did not include API_URL and ANON_KEY.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    try:
        port = available_port()
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    origins = env.get("APP_ORIGINS", "").strip()
    if not origins:
        origins = f"http://127.0.0.1:{port},http://localhost:{port}"
    env.update(
        APP_MODE="saas",
        SUPABASE_URL=api_url,
        SUPABASE_ANON_KEY=anon_key,
        ALLOWED_HOSTS=env.get("ALLOWED_HOSTS", "127.0.0.1,localhost"),
        APP_ORIGINS=origins,
    )
    print(f"Starting local SaaS test server at http://127.0.0.1:{port} against {api_url}; hosted .env is unchanged.")
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
