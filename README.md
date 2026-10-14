# dev-terminal

A self-hosted developer dashboard with two explicit modes:

- **Local mode (default):** private localhost dashboard for GitHub repositories, local Git projects, an allowlisted tool launcher, and an embedded ttyd shell.
- **SaaS foundation (opt-in):** Supabase Auth sign-up/sign-in, server-verified access tokens, an account identity endpoint, and strict disabling of host-local project/terminal APIs.

The SaaS mode is an authentication foundation, **not yet a production cloud IDE**. Isolated cloud workspaces and authenticated local-agent pairing are not implemented. No authenticated SaaS user can use this app to run commands on the server.

## Current features

- Lists cloned Git repositories under PROJECTS_DIR with branch, dirty state, and ahead/behind counts when an upstream exists.
- Lists repositories visible to a server-side GitHub token in local mode.
- Embeds ttyd; this project does not implement a terminal emulator.
- Tool launcher queues only predefined commands for the local ttyd shell.
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

4. Install dependencies and start the app. In SaaS mode, / serves the account page, /api/public-config returns only the public Supabase URL/key, and /api/me verifies the user's access token. Asymmetric ES256/RS256 tokens are checked against Supabase's JWKS and issuer/audience; legacy HS256 tokens are verified by asking Supabase Auth.
5. Test sign-up, email confirmation (if enabled), sign-in, /api/me, and sign-out.

### SaaS database schema (scaffold only)

The SaaS schema is tracked in `supabase/migrations/20261014155254_dev_terminal_saas_foundation.sql`, with subsequent hardening in `20261014160423_harden_private_workspace_helpers.sql`  `20261014160507_index_workspace_foreign_keys.sql`, and `20261014160631_tighten_workspace_owner_and_billing_access.sql`. It defines profiles, workspaces, workspace membership/roles, local-agent metadata, subscription metadata, and audit events with row-level security. The foundation and hardening migrations have been applied to the dedicated dev-terminal Supabase project. The workspace helper functions live in the non-exposed `private` schema; Supabase's security advisor returned no findings after this change.

The database schema is still a scaffold and **is not wired to workspace-provisioning or billing routes**. Keep dev-terminal on its own Supabase project; do not apply these migrations to SafeNestT or another product's production database. Test migrations and RLS changes before release.

### SaaS mode limitations

- Local project discovery, GitHub repository listing, tool execution, and the terminal queue return 501 in SaaS mode. They must remain disabled until workspace isolation or authenticated local-agent pairing exists.
- The SaaS account page does not provision a workspace, store projects, or bill users.
- The database schema scaffold exists, but application routes for workspace provisioning, team membership management, billing, agent pairing, and audit persistence are not implemented.
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
- GET /api/me — requires Authorization: Bearer <Supabase access token>

FastAPI docs/OpenAPI are disabled.

## Tests

Install development dependencies and run:

```bash
pip install -r requirements-dev.txt
pytest -q
```

Read SECURITY.md before using dev-terminal.
