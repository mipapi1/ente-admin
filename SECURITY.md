# Security

## Reporting a vulnerability

Please don't open a public issue. Use the repository's private vulnerability reporting
("Report a vulnerability" under the Security tab), or contact the maintainers privately.
Include what you found, how to reproduce it and the version. You'll get an answer within a
week; fixes are released as a new version with a note in CHANGELOG.md.

Only the latest release gets fixes.

## How it's built

The design and its limits are in [docs/SECURITY.md](docs/SECURITY.md): what the dashboard
can see, how logins, tokens, the admin portal, the database roles and notifications are
protected.

## Hardening checklist

ente-admin can change accounts on your Ente server when the portal is on. Treat it like
the server's admin console.

- **Don't expose it to the internet.** Reach it over a VPN (WireGuard, Tailscale) or a
  private network, or put it behind a login proxy with two-factor (Authelia, Authentik,
  oauth2-proxy) and use `ENTE_ADMIN_AUTH=header` with `ENTE_ADMIN_TRUSTED_PROXIES` set to
  the proxy's address only.
- **TLS** at the reverse proxy. The container speaks plain HTTP and should only be
  reachable from the proxy (the compose files publish on localhost; the Kubernetes example
  has a NetworkPolicy).
- **Least access.** Start with the read-only postgres role and only the grants you need
  (docs/POSTGRES.md). Leave `ENTE_ADMIN_ENABLE_ACTIONS` and `WRITE_DSN` off until you need
  them. Use a dedicated Ente admin account with no photos.
- **Secrets in files**, readable by the container only (`*_FILE` variables), never in
  environment values, compose files or git.
- **Admin groups.** With a login proxy, set `ENTE_ADMIN_ADMIN_GROUPS` so only admins see
  everything; other users see only their own account.
- **Keep emails masked** (`ENTE_ADMIN_REVEAL_EMAILS=false`) unless the people using the
  dashboard need them.
- **Notifications in shadow mode first**, and SPF/DKIM for the sender domain.
- **Back up the state volume** if you rely on the audit log and notification history.
- **Update** to new releases; the image is small and runs as a non-root user with a
  read-only root filesystem and no capabilities. Keep it that way.
