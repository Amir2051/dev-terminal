# dev-terminal

A self-hosted developer dashboard with two explicit modes:

- **Local mode (default):** private localhost dashboard for GitHub repositories, local Git projects, an allowlisted tool launcher, and an embedded ttyd shell.
- **SaaS foundation (opt-in):** Supabase Auth sign-up/sign-in, server-verified access tokens, an account identity endpoint, and strict disabling of host-local project/terminal APIs.

The SaaS mode is an authentication and controlled local-agent foundation, **not yet a production cloud IDE**. Pairing codes and agent credentials are hashed at rest, pairing codes expire after ten minutes and are single-use, and agents can be revoked. The local agent now polls a workspace-scoped queue and executes only fixed, locally allowlisted tools; it never evaluates server-supplied shell text. `pytest`, `git-fetch`, and `git-pull` require an owner/admin approval.

## Current features

- Lists cloned Git repositories under PROJECTS_DIR with branch, dirty state, and ahead/behind counts when an upstream exists.
- Lists repositories visible to a server-side GitHub token in local mode.
- Embeds ttyd; this project does not implement a terminal emulator.
- Tool launcher queues only predefined commands for the local ttyd shell.
- RZ Agent uses local Ollama through a loopback-only model endpoint, bounded prompts/output, and limited selected-repository context. The default model is the installed `qwen3:1.7b` for responsiveness on constrained hardware. `mistral:7b` is also installed and can be selected with `OLLAMA_MODEL=mistral:7b`, but benchmark it locally first: on this 16 GB CPU-only development machine, a short live inference exceeded the 120-second request timeout under memory pressure. `OLLAMA_MODEL` can be set to any compatible installed model.
- RZ Agent includes bounded read-only tools for a project map, literal source-code search, Git status, and recent commits. Tool output is displayed for review and can be attached to the next model request. It can list/read bounded text files (excluding hidden/secret paths, symlinks, dependencies, and large/binary files), propose a single-file full-content change as a unified diff, and apply that one-file proposal only after explicit user confirmation. Proposals expire after 15 minutes, Python/JSON syntax is checked where applicable, and changes are rejected if the source file changed after review.
- The AI workbench does not execute commands or tests. Run tests separately through the existing fixed-tool launcher and review results before committing.
- Write-capable tools (Git fetch and Git pull) require confirmation in the UI and backend.
- Installable PWA; API responses are not cached by the service worker.
- Local mode enforces loopback-only clients. SaaS mode requires explicit host and browser-origin allowlists.
- Optional Supabase Auth account UI, JWT verification, and /api/me.

## Requirements

Linux, Python 3.10+, Git, ttyd for local terminal use, and a Supabase project for SaaS authentication.

## Local mode setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
openssl rand -hex 32
```

Edit .env:

- Keep APP_MODE=local.
- Set PROJECTS_DIR to the directory containing your cloned repositories.
- Optionally set LOCAL_PROJECT_ALLOWLIST to a comma-separated list of repository folder names; when set, local project listing and access are restricted to those names.
- Set GITHUB_TOKEN to a fine-grained token with Metadata: Read-only access for repositories you want listed.
- Set TTYD_USER and TTYD_PASSWORD to unique credentials.
- Copy the output from openssl rand -hex 32 into TERMINAL_QUEUE_TOKEN.

Keep .env private and never commit it. The .env file is ignored by Git.

Start FastAPI on a free loopback port. For example, if port 8000 is already used by another service:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

Open http://127.0.0.1:8003/.

### RZ Agent (local Ollama)

Install and start Ollama separately, then ensure the model you want is already downloaded. The default is `qwen3:1.7b`, which can be overridden with `OLLAMA_MODEL`. `OLLAMA_URL` defaults to `http://127.0.0.1:11434`; the app rejects non-loopback model URLs so prompts and repository context are not sent to a remote provider.

The dashboard shows model availability and provides a chat panel. When a repository is selected, the assistant receives only a bounded README excerpt, top-level entry names, and a Git status summary. RZ Agent has bounded repository tools for project mapping, literal code search, filename search, safe file reads, Git status/diff/history, and an optional test runner. Code edits remain a separate reviewable-diff workflow.

#### Optional MCP connections

RZ Agent supports **MCP Streamable HTTP** servers configured in `~/.config/dev-terminal/mcp_servers.json` (or the path in `RZ_MCP_CONFIG`). Start with loopback-only servers. Only tools named in each server's `read_only_tools` list are exposed to the model; do not put write/destructive tools in this list. MCP servers on non-loopback hosts are blocked unless `RZ_MCP_ALLOW_REMOTE=1` is explicitly set; remote servers must use HTTPS. Use `headers_env` to reference credentials stored in the app environment—never put tokens directly in the JSON file. MCP tool output is untrusted input.

Example configuration for an MCP server already listening locally:

```json
{
  "servers": [
    {
      "name": "local_docs",
      "url": "http://127.0.0.1:9000/mcp",
      "enabled": true,
      "read_only_tools": ["search_docs", "fetch_page"],
      "headers_env": {}
    }
  ]
}
```

Check configured server health and allowlisted tool counts at `GET /api/ai/mcp/status`. RZ Agent discovers schemas and calls allowlisted tools as part of its normal tool loop. Stdio MCP servers are intentionally not enabled yet: launching arbitrary server commands with the app user's OS privileges would expand the local code-execution risk before command sandboxing is in place. Remote MCP servers also require explicit opt-in because repository context and tool output can contain sensitive information.

##### GitHub MCP (read-only)

The official GitHub remote MCP endpoint is `https://api.githubcopilot.com/mcp/readonly`. To add a preconfigured, read-only GitHub server without putting credentials in a config file, run:

```bash
cd /home/ronzoro/dev-terminal
.venv/bin/python scripts/configure-github-mcp.py
```

Create a **fine-grained GitHub personal access token** and choose **All repositories** if RZ Agent should discover every repository your account can access. A token restricted to selected repositories cannot see repositories outside that selection. Grant read-only repository metadata and contents, plus issues, pull requests, and Actions permissions as needed. Organization-owned repositories may also require organization approval/policy authorization. Add the token privately to `.env` as `RZ_GITHUB_MCP_TOKEN=...` and explicitly enable remote MCP by setting `RZ_MCP_ALLOW_REMOTE=1`. Do not paste the token into chat, source code, the MCP JSON config, or screenshots. Restart the local API after updating `.env`, then open `http://127.0.0.1:8005/api/ai/mcp/status` and confirm GitHub is connected. The setup script preserves other MCP servers, sets restrictive config permissions, and never reads or writes `.env`.

For repository discovery, RZ Agent uses GitHub repository search scoped to the authenticated account (for example, `user:LOGIN`) and can paginate through results. The GitHub server URL is read-only and RZ Agent additionally checks its own per-tool allowlist. This is defense in depth, not a substitute for least-privilege token permissions. GitHub's official server may change tool names; if a listed tool is unavailable, it will simply not be exposed. No repository writes, issues, pull requests, or workflow dispatches are enabled by this preset.

### Local terminal

Install ttyd using your Linux distribution's package manager or the upstream instructions. In another terminal, from the repository root:

```bash
source .venv/bin/activate
set -a
source .env
set +a
export DEV_TERMINAL_API_URL=http://127.0.0.1:8003
chmod +x scripts/terminal-shell.sh
ttyd -W -i 127.0.0.1 -p 7681 -c "${TTYD_USER}:${TTYD_PASSWORD}" ./scripts/terminal-shell.sh
```

Change DEV_TERMINAL_API_URL to the actual local FastAPI port. The -i 127.0.0.1 option prevents ttyd from listening on network interfaces; -c enables basic authentication. Never expose the ttyd port publicly. The terminal runs with the OS user's permissions.

## SaaS authentication foundation

This is intentionally opt-in and does not change the default local workflow.

1. Create a Supabase project and enable email/password authentication. Configure the site's URL, allowed redirect URLs, and email confirmation policy in Supabase Auth.
2. Copy the project's URL and **publishable/anon key** into .env as SUPABASE_URL and SUPABASE_ANON_KEY. Do not use a service-role or secret key.
3. Configure the exact public hostname and browser origins. For local-only testing, for example:

   ```dotenv
   APP_MODE=saas
   SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   SUPABASE_ANON_KEY=YOUR_PUBLISHABLE_OR_ANON_KEY
   ALLOWED_HOSTS=127.0.0.1,localhost
   APP_ORIGINS=http://127.0.0.1:8003,http://localhost:8003
   ```

   Replace the example Supabase values with your own. For a hosted deployment, use your real hostname and HTTPS origin. ALLOWED_HOSTS contains hostnames without schemes or ports; APP_ORIGINS contains exact origins including scheme and, when needed, port.

4. Install dependencies and start the app. In SaaS mode, / serves the account page, /api/public-config returns only the public Supabase URL/key, and /api/me verifies the user's access token. Authenticated users can list and create workspace metadata through /api/workspaces; these requests forward the user's JWT to Supabase PostgREST so database RLS authorizes each operation. Asymmetric ES256/RS256 tokens are checked against Supabase's JWKS and issuer/audience; legacy HS256 tokens are verified by asking Supabase Auth.
5. Test sign-up, email confirmation (if enabled), sign-in, /api/me, and sign-out.


### Test SaaS mode against the isolated local Supabase stack

Do not mix the hosted project URL/key from `.env` with the local Supabase stack. The URL and public key must come from the same project. Start the local stack with `supabase start`, then run the helper below. It reads `API_URL` and `ANON_KEY` from `supabase status -o env`, configures SaaS mode for local testing, and leaves the hosted `.env` unchanged:

```bash
python3 scripts/run-local-saas.py
```

The helper chooses the first free port from `8003` through `8012` and configures matching local browser origins. To require a specific port, set `DEV_TERMINAL_PORT` (the helper will fail safely if it is occupied). This helper is for local development only, not production deployment.

### SaaS database schema (scaffold only)

The SaaS schema is tracked in `supabase/migrations/20261014155254_dev_terminal_saas_foundation.sql`, with subsequent hardening in `20261014160423_harden_private_workspace_helpers.sql`, `20261014160507_index_workspace_foreign_keys.sql`, `20261014160631_tighten_workspace_owner_and_billing_access.sql`, workspace lifecycle audit triggers in `20261014172000_workspace_audit_triggers.sql`, local-agent pairing in `20261014190000_secure_local_agent_pairing.sql`, and command queue/approval/audit functions in `20261014200000_secure_agent_commands.sql`. It defines profiles, workspaces, workspace membership/roles, local-agent metadata, subscription metadata, and audit events with row-level security. The foundation, hardening, pairing, and command-queue migrations have been applied to the isolated local Dev Terminal Supabase stack for development/testing only. They have not been applied to the hosted Supabase project. The workspace helper functions live in the non-exposed `private` schema. **Production readiness note (2026-10-14):** Supabase currently reports leaked-password protection disabled; this is deferred until the project plan/settings allow it and does not require changing database RLS policies.

The database schema remains a partial SaaS foundation. Workspace listing and creation manage metadata; local-agent pairing registers revocable identities, and the command queue persists structured allowlisted tool requests with approvals and audit events. This does not provision cloud compute, enforce paid-plan entitlements, or implement billing. Keep Dev Terminal migrations scoped to the dedicated Dev Terminal Supabase project; do not apply them to SafeNestT or another product's production database. Test migrations and RLS changes before release.

### SaaS mode limitations

- Local project discovery and GitHub repository listing remain disabled in SaaS mode. The SaaS page can queue fixed tool IDs for an online paired agent and display command status/output. Command records and workspace audit events are persisted in the dedicated local Dev Terminal database during development.
- The agent stores its credential in `~/.config/dev-terminal/agent.json` with owner-only permissions. Run it with `python3 scripts/local-agent.py run`. The projects root defaults to `~/Projects`; set `DEV_TERMINAL_PROJECTS_DIR` on the agent machine to an approved directory containing direct-child Git repositories, and set `DEV_TERMINAL_ALLOWED_PROJECTS` to a comma-separated allowlist of exact folder names. Project tools refuse to run if this allowlist is missing or the selected name is not on it.
- The local runner uses fixed argument vectors with `shell=False`, rejects unknown tool IDs and symlink/path traversal, and caps each tool at 60 seconds. `pytest`, `git-fetch`, and `git-pull` remain blocked until an owner/admin approves them in the dashboard. The runner still executes under the local OS account; use only trusted repositories, and do not expose this prototype publicly. Production use needs OS-user/container isolation and stronger sandboxing.
- Team membership management and billing/subscription enforcement are not implemented. Workspace lifecycle and command actions write minimal audit events. Subscription metadata in the database is not proof of payment or entitlement.
- Use HTTPS behind a carefully configured reverse proxy before internet exposure. Set trusted proxy behavior explicitly, enforce TLS, rate-limit authentication endpoints at the edge, and monitor logs.
- Do not put a Supabase service-role key in this app's .env or browser. Add database access only with user-scoped authorization and Row Level Security policies.

## PWA

Open the local dashboard in a browser that supports installation and use the browser's Install app option. The service worker caches static assets only; API responses are fetched live.

## API

Local mode:

- GET /api/health
- GET /api/repos
- GET /api/projects
- GET /api/tools
- POST /api/tools/queue
- GET /api/terminal/next-command — local shell hook only; requires TERMINAL_QUEUE_TOKEN

SaaS mode:

- GET /api/health
- GET /api/public-config — exposes only the public Supabase URL and publishable/anon key
- GET /api/me — requires a Supabase access token in the Authorization bearer header
- GET /api/workspaces — lists workspaces visible to the authenticated user through RLS
- POST /api/workspaces — creates workspace metadata owned by the authenticated user; JSON body includes a name
- GET /api/workspaces/{workspace_id}/agents — lists workspace agents visible through RLS
- POST /api/agents/pairing — creates a ten-minute pairing code for a workspace owner/admin
- POST /api/agents/redeem — consumes a pairing code and returns the agent credential once
- POST /api/agents/heartbeat — validates the agent credential and refreshes its last-seen time
- POST /api/agents/{agent_id}/revoke — revokes an agent for a workspace owner/admin
- POST /api/workspaces/{workspace_id}/commands — queues a fixed allowlisted tool for an online agent in that workspace
- GET /api/workspaces/{workspace_id}/commands — lists command status/output for workspace owners/admins
- POST /api/commands/{command_id}/approval — approves or rejects a pending sensitive tool request
- POST /api/agents/next-command — authenticated agent claims one queued command
- POST /api/agents/commands/{command_id}/complete — authenticated agent reports a bounded result

Workspace routes forward only the user's access token and the public/publishable Supabase key. They do not use a service-role key. FastAPI docs/OpenAPI are disabled.

## Tests

Install development dependencies and run:

```bash
pip install -r requirements-dev.txt
pytest -q
```

Read SECURITY.md before using dev-terminal.

### Pair a Linux computer (allowlisted local-agent MVP)

1. Sign in to the SaaS account page and choose **Pair Linux computer** for a workspace you own or administer.
2. On the Linux computer, run `python3 scripts/local-agent.py pair --server https://YOUR-DEV-TERMINAL-HOST` and enter the one-time code when prompted. For local testing, `http://127.0.0.1:8004` is permitted.
3. Run `python3 scripts/local-agent.py run` to poll for commands every ten seconds. Configure `DEV_TERMINAL_PROJECTS_DIR` and set `DEV_TERMINAL_ALLOWED_PROJECTS` to exact approved folder names on the agent machine.
4. In the dashboard's **Remote tools** panel, choose the paired computer, tool ID, and project folder name. `pytest`, `git-fetch`, and `git-pull` appear as pending approval until an owner/admin approves or rejects them.
5. Keep the credential file private. To disconnect, stop the agent and revoke its agent record; revocation is enforced when the agent next polls or reports a result.

The runner never executes arbitrary server-supplied shell commands. It uses a local fixed allowlist, `shell=False`, direct-child Git project validation, output limits, and a 60-second timeout. This is still a local prototype running under the current OS account, not a production sandbox. Do not expose the app publicly until production rate limiting, HTTPS/proxy configuration, OS/container isolation, and adversarial end-to-end security tests are complete.
