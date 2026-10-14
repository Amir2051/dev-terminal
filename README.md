# dev-terminal

A self-hosted, **localhost-only** developer dashboard for Linux. It combines a FastAPI backend, local Git repository status, server-side GitHub repository listing, an installable PWA, and an embedded [ttyd](https://github.com/tsl0922/ttyd) shell. There is no frontend build step.

## Features

- Lists cloned Git repositories under PROJECTS_DIR with branch, dirty state, and ahead/behind counts when an upstream exists.
- Lists repositories visible to your GitHub token. The token is read only by FastAPI and never returned to the browser.
- Embeds ttyd; this project does not implement a terminal emulator.
- Tool launcher queues allowlisted commands for the ttyd shell. The shell consumes them at its next prompt using a separate server-side queue secret.
- Write-capable tools (Git fetch and Git pull) require UI confirmation and backend confirmation.
- Installable PWA with a manifest and service worker. API responses are not cached.
- Localhost guard on the backend. ttyd must also be started with authentication.

## Requirements

Linux, Python 3.10+, Git, ttyd, curl, and bash. A GitHub personal access token is needed to populate the GitHub repository list; local projects work without it.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
openssl rand -hex 32
```

Edit .env. Set PROJECTS_DIR to the directory containing your cloned repositories. Set GITHUB_TOKEN to a fine-grained token with Metadata: Read-only access to the repositories you want listed. Set TTYD_USER and TTYD_PASSWORD to non-default credentials. Copy the output from openssl rand -hex 32 into TERMINAL_QUEUE_TOKEN. Keep .env private; it is ignored by Git.

## Run the backend

Terminal 1, from the repository root:

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/**.

## Run ttyd

Install ttyd using your Linux distribution's package manager or the upstream installation instructions. Terminal 2, from the repository root:

```bash
set -a
source .env
set +a
chmod +x scripts/terminal-shell.sh
ttyd -W -i 127.0.0.1 -p 7681 -c "${TTYD_USER}:${TTYD_PASSWORD}" ./scripts/terminal-shell.sh
```

- -W enables terminal input.
- -i 127.0.0.1 prevents listening on network interfaces.
- -c enables ttyd basic authentication.
- The dashboard embeds http://127.0.0.1:7681. If the embedded view is blocked by browser policy or authentication, use Open separately and sign in.

The shell hook polls the backend for commands queued by the tool launcher. It accepts only named tools in the backend allowlist; the shell queue request is authenticated with TERMINAL_QUEUE_TOKEN. Do not add an arbitrary-command endpoint.

## PWA installation

Open the dashboard in Chromium/Chrome or another browser that supports installation. Use the browser's Install app option. The service worker caches static frontend assets only; API responses are fetched live.

## Tool launcher

Available tools include pwd, file listing, Git status/log/diff, pytest, Ruff, Git fetch, and Git pull. Tools requiring a repository need a local clone selected in the sidebar. Git fetch and Git pull ask for confirmation in the browser and are rejected by the backend unless confirmed=true is supplied.

Queued commands execute when the ttyd shell reaches its next prompt. If no command runs, make sure both FastAPI and ttyd are running and ttyd was started with scripts/terminal-shell.sh.

## Remote access

Remote access is **not enabled by default**. Never port-forward ports 8000 or 7681 from your router and never bind services to 0.0.0.0. If remote access is genuinely needed, use Tailscale only, with restrictive tailnet ACLs and a carefully configured Tailscale Serve/reverse proxy. Keep the local services bound to loopback and keep ttyd authentication enabled. A remote-browser setup must proxy both dashboard and ttyd routes; the default iframe URL is intended for use on the same machine.

## API

- GET /api/health
- GET /api/repos
- GET /api/projects
- GET /api/tools
- POST /api/tools/queue — accepts a predefined tool_id, optional project, and confirmed boolean.
- GET /api/terminal/next-command — local shell hook only; requires TERMINAL_QUEUE_TOKEN and returns an allowlisted queued command.

FastAPI docs/OpenAPI are disabled in this dashboard.

Read [SECURITY.md](SECURITY.md) before using dev-terminal. ttyd grants a real shell with the OS user's permissions.
