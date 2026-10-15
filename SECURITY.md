# Security policy

dev-terminal has two distinct modes. Keep local mode as the default until a production deployment has been reviewed.

## Local mode

1. Bind FastAPI and ttyd to 127.0.0.1. Do not bind either service to 0.0.0.0 or a public interface.
2. Keep ttyd authentication enabled. Never run ttyd without its -c USER:PASSWORD option. Use a strong, unique password.
3. Never port-forward the dashboard or ttyd ports from your router. For private remote access to a local installation, use a carefully configured private network such as Tailscale and keep underlying listeners bound to loopback.
4. The GitHub token belongs in .env only. It is read by the backend and never returned by an API route or embedded in frontend assets. Do not commit .env. If exposed, revoke it and create a replacement with minimum permissions.
5. Tool execution is allowlisted. The API queues structured tool IDs and project names, never shell source. The ttyd hook dispatches through a fixed shell case statement and must never evaluate HTTP response data. Write-capable tools require explicit confirmation in the UI and a confirmed flag at the backend.
6. Do not expose the dashboard to untrusted browser origins. Local mode rejects non-loopback clients and unexpected Host/Origin headers.
7. Review commands before approving them. Git pull can change repository files; Git fetch updates local Git metadata. The shell hook revalidates that the selected project is a direct, non-symlink Git directory beneath PROJECTS_DIR.
8. The terminal is a real shell with the OS user's permissions. Do not run it as root.
9. The local AI workbench only reads existing UTF-8 text files under the selected allowlisted Git repository. It rejects traversal, symlinks, hidden paths, common credential/key filenames, dependency/build/Git metadata directories, and files larger than 64 KiB. Model prompts and file context are sent only to a loopback Ollama endpoint.
10. AI-generated edits are untrusted proposals. The UI shows a unified diff and requires a separate confirmation before a single file is written. Proposals expire after 15 minutes, are limited to one file, and are rejected if the original content hash changed. Review the resulting Git diff and run tests before committing; no command is run automatically by the AI workbench.

## SaaS authentication foundation

SaaS mode is an early authentication scaffold, **not an authorization-complete or production-ready hosted IDE**.

1. SaaS mode requires explicit ALLOWED_HOSTS and APP_ORIGINS configuration. Use HTTPS on the public edge, a valid TLS certificate, a carefully configured reverse proxy, and strict trusted-proxy settings.
2. Keep SUPABASE_URL and SUPABASE_ANON_KEY in server configuration. The publishable/anon key may be sent to the browser; never put a Supabase service-role/secret key in this app or client.
3. Supabase access tokens must be verified. Asymmetric ES256/RS256 tokens are validated against the Supabase JWKS, issuer, audience, and expiry. Legacy HS256 tokens are validated by the Supabase Auth user endpoint rather than by trusting unverified claims or copying the JWT secret into this service.
4. Host-local project listing and GitHub repository listing remain disabled in SaaS mode. The local agent polls a workspace/agent-scoped queue of fixed tool IDs. The server never sends shell source; the agent rechecks its local allowlist and invokes argument arrays with `shell=False`. Workspace/agent authorization and RLS/RPC checks must remain enabled.
5. Do not expose a host shell to SaaS customers. Every cloud workspace must run in an isolated environment with CPU, memory, disk, process, network, and execution-time limits; use per-workspace identities and enforce authorization server-side.
6. SaaS responses include baseline security headers, a Content Security Policy, and no-store cache controls for API responses. Verify these through the real reverse proxy. Before launching publicly, add edge-level request rate limits and abuse controls, secret rotation procedures, backups, alerting, and an incident response process. Workspace create/update operations have minimal database-triggered audit events; broader audit workflows are not implemented.
7. The dedicated Dev Terminal Supabase project has RLS-enabled workspace tables. The `/api/workspaces` routes verify the user's access token and forward that user's JWT with only the public/anon key, so Postgres RLS remains the authorization boundary. Never rely on frontend filtering. Keep billing, agent, and privileged audit writes server-managed. Maintain adversarial tests proving users cannot read or mutate another user's workspace.
8. Account creation is not a paid subscription. Workspace creation stores metadata only; it does not provision compute or grant unrestricted shell access. Billing enforcement, team-management workflows, and isolated cloud execution are not implemented. Local-agent commands are limited to the fixed allowlist and workspace-scoped authorization; this is not a production execution sandbox.

## Known limitations

- Local mode's command queue is in-memory and resets on restart. CI audits declared Python requirements for known vulnerabilities.
- SaaS mode authenticates users and supports RLS-scoped workspace metadata plus controlled allowlisted local-agent tools. It does not provide an unrestricted terminal, cloud compute provisioning, billing, or paid-plan enforcement.
- Localhost binding is a useful boundary for local mode, not a substitute for OS account isolation.
- Browser authentication sessions are handled by the Supabase client. Protect the page against XSS and keep dependencies current.

## If a token is exposed

Revoke the GitHub or Supabase credential in its provider, create a replacement with minimum permissions, update .env or deployment secrets, and restart/redeploy the service. Never paste tokens or passwords into issue trackers or chat.


## Local-agent pairing boundary

- Pairing codes expire after ten minutes and can be redeemed once. The database stores only SHA-256 digests of pairing codes and agent credentials.
- Pairing requires a verified user access token and workspace owner/admin authorization. Agent redemption uses an anonymous RPC with a high-entropy, one-time code; apply production edge rate limits before internet exposure.
- Agent credentials are returned only once and should be stored with owner-only filesystem permissions. Revocation invalidates subsequent heartbeat authentication.
- The Linux agent polls every ten seconds and runs only locally defined tool IDs with fixed argument arrays; it does not expose a shell or evaluate server-supplied command strings. `pytest`, `git-fetch`, and `git-pull` require an owner/admin approval marker persisted in the database. Command output is capped, tool execution times out after 60 seconds, and stale running jobs are marked failed after five minutes.
- The runner runs as the current OS user, so an approved `pytest` invocation can execute repository-defined code with that user's permissions. Use only trusted repositories. Production use requires a dedicated low-privilege OS account or isolated container/sandbox, resource limits, and additional adversarial review.
- In-memory throttling is per application process and is defense-in-depth only; production deployments need durable/shared rate limiting at the reverse proxy or gateway.

11. RZ Agent inspection tools are a fixed allowlist: project map, bounded literal code search, `git status --short --branch`, and a bounded recent-commit log. Git commands use argument arrays without a shell and have a short timeout. No arbitrary command execution is exposed to model chat. Tool results and repository content remain untrusted input.
