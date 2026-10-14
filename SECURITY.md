# Security policy

dev-terminal has two distinct modes. Keep local mode as the default until a production deployment has been reviewed.

## Local mode

1. Bind FastAPI and ttyd to 127.0.0.1. Do not bind either service to 0.0.0.0 or a public interface.
2. Keep ttyd authentication enabled. Never run ttyd without its -c USER:PASSWORD option. Use a strong, unique password.
3. Never port-forward the dashboard or ttyd ports from your router. For private remote access to a local installation, use a carefully configured private network such as Tailscale and keep underlying listeners bound to loopback.
4. The GitHub token belongs in .env only. It is read by the backend and never returned by an API route or embedded in frontend assets. Do not commit .env. If exposed, revoke it and create a replacement with minimum permissions.
5. Tool execution is allowlisted. The API does not accept arbitrary shell commands. Write-capable tools require explicit confirmation in the UI and a confirmed flag at the backend.
6. Do not expose the dashboard to untrusted browser origins. Local mode rejects non-loopback clients and unexpected Host/Origin headers.
7. Review commands before approving them. Git pull can change repository files; Git fetch updates local Git metadata.
8. The terminal is a real shell with the OS user's permissions. Do not run it as root.

## SaaS authentication foundation

SaaS mode is an early authentication scaffold, **not an authorization-complete or production-ready hosted IDE**.

1. SaaS mode requires explicit ALLOWED_HOSTS and APP_ORIGINS configuration. Use HTTPS on the public edge, a valid TLS certificate, a carefully configured reverse proxy, and strict trusted-proxy settings.
2. Keep SUPABASE_URL and SUPABASE_ANON_KEY in server configuration. The publishable/anon key may be sent to the browser; never put a Supabase service-role/secret key in this app or client.
3. Supabase access tokens must be verified. Asymmetric ES256/RS256 tokens are validated against the Supabase JWKS, issuer, audience, and expiry. Legacy HS256 tokens are validated by the Supabase Auth user endpoint rather than by trusting unverified claims or copying the JWT secret into this service.
4. Host-local project listing, GitHub repository listing, tool queueing, and terminal command polling are disabled in SaaS mode. Do not re-enable them until the feature uses authenticated, tenant-scoped local-agent pairing or isolated cloud workspaces.
5. Do not expose a host shell to SaaS customers. Every cloud workspace must run in an isolated environment with CPU, memory, disk, process, network, and execution-time limits; use per-workspace identities and enforce authorization server-side.
6. Before launching publicly, add request rate limits, abuse controls, security headers, structured audit events, secret rotation procedures, backups, alerting, and an incident response process.
7. Before storing workspace or billing data, add a persistent data model with explicit tenant/user ownership and enforce Row Level Security policies on every user-accessible table. Never rely on frontend filtering as authorization.
8. Account creation is not a paid subscription, and sign-in alone does not provision a workspace. Billing, workspace provisioning, local-agent pairing, team roles, and audit persistence are not implemented yet.

## Known limitations

- Local mode's command queue is in-memory and resets on restart.
- SaaS mode currently authenticates account identity only; it does not provide tenant workspaces or terminal execution.
- Localhost binding is a useful boundary for local mode, not a substitute for OS account isolation.
- Browser authentication sessions are handled by the Supabase client. Protect the page against XSS and keep dependencies current.

## If a token is exposed

Revoke the GitHub or Supabase credential in its provider, create a replacement with minimum permissions, update .env or deployment secrets, and restart/redeploy the service. Never paste tokens or passwords into issue trackers or chat.
