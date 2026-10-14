# Security policy

dev-terminal is a local developer tool, not an internet-facing service.

## Hard rules

1. Bind FastAPI and ttyd to 127.0.0.1. Do not bind either service to 0.0.0.0 or a public interface.
2. Keep ttyd authentication enabled. Never run ttyd without its -c USER:PASSWORD option. Use a strong, unique password.
3. Never port-forward ports 8000 or 7681 on your router. For remote access, use Tailscale only, with restrictive tailnet ACLs and a carefully configured Tailscale Serve/reverse-proxy setup. Keep the underlying listeners on loopback and keep ttyd authentication enabled.
4. The GitHub token belongs in .env only. It is read by the backend and never returned by an API route or embedded in frontend assets. Do not commit .env. If exposed, revoke it and create a replacement with minimum permissions.
5. Tool execution is allowlisted. The API does not accept arbitrary shell commands. Write-capable tools require explicit confirmation in the UI and a confirmed flag at the backend.
6. Do not expose the dashboard to untrusted browser origins. The app rejects non-loopback clients and unexpected Host/Origin headers.
7. Review commands before approving them. Git pull can change repository files; Git fetch updates local Git metadata.

## Scope and limitations

- Localhost binding is the primary security boundary, not a substitute for OS account isolation.
- ttyd provides a real shell with the permissions of the user running it. Authentication is mandatory.
- The command queue accepts only named, predefined tools. Do not add an arbitrary-command endpoint.
- GitHub repository metadata is fetched server-side. API responses omit the token.
- The service worker caches static frontend assets only. API responses are not cached.
- Tailscale access is not enabled automatically. A remote-browser setup must proxy both the dashboard and ttyd routes appropriately.

## If a token is exposed

Revoke it in GitHub, create a replacement with the minimum required permissions, update .env, and restart the backend.
