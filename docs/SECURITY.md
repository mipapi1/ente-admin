# Security notes

**What it can see.** Ente encrypts files and their metadata on the device. Museum, and so
this dashboard, sees account metadata: email (stored encrypted, decrypted by museum for
admin calls), sizes, counts, timestamps, device user agents. Never photos, file names,
types, places or dates.

**Tokens.** The museum source uses an admin account's auth token. That is a full login for
that account, so use a separate account that only administers (no photos), keep the token
file readable by the dashboard only, and revoke it (sign the session out) if it leaks.
Tokens are read from files, sent only to museum, never to the browser, never logged.

**Login.** Required by default:
- `basic`: PBKDF2-SHA256 (600k iterations) hashes; unknown users cost the same time.
- `header`: only from `ENTE_ADMIN_TRUSTED_PROXIES`. Also block direct access to the
  container (the Kubernetes example has a NetworkPolicy).
- `none`: refused unless bound to localhost and `ENTE_ADMIN_INSECURE_LOCALHOST=true`.

**Browser.** Strict CSP (`default-src 'none'`, scripts and styles from the app only, no
inline code), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store`. The UI inserts
data as text, never HTML. No external requests: no CDN, fonts, analytics or telemetry.

**Viewers.** With `ENTE_ADMIN_ADMIN_GROUPS`, the groups and email headers decide what a
user sees; they're read only from requests that come from `ENTE_ADMIN_TRUSTED_PROXIES`, like
the username. Accounts a viewer may not see are removed on the server, before masking.
Sharing changes need an admin, the same-origin `X-Ente-Admin` header, and are logged with
the admin's username (never emails). Branding images are served only by the names in
`branding.json`, and its text is shown as text.

**Masking.** Emails and account ids are masked on the server before they're sent; full
values only with `ENTE_ADMIN_REVEAL_EMAILS=true`. Labels (`acct-xxxxxxxx`) are salted
hashes, stable per installation.

**Admin portal.** Off by default (docs/ADMIN.md). Only dashboard admins can use it, and only
after unlocking a server with an Ente admin sign-in: email code, password (used once to
open the token, never stored or logged) and TOTP if on. The museum token stays in memory,
per dashboard user and server, locks after 15 minutes idle or 60 in total, is signed out
of museum when it ends, and never reaches the browser. Codes go only to emails of existing
accounts (museum would create an account otherwise), and an unlock by an account that
isn't a museum admin is signed out at once. Changes need a same-origin JSON request
(custom header, `Sec-Fetch-Site`/`Origin` checks), validated parameters, a preview, and
typed confirmation for risky ones; they're rate limited and recorded in `audit.db` with
the values before and after. Endpoints that would let an admin act as a user (custom
login codes) are not exposed.

**Postgres source.** Use the read-only role in docs/POSTGRES.md; the connection also sets
`default_transaction_read_only`. It never reads token values (only which devices exist and
when they were used), keys or file metadata. Newer views check their own column grants and
say what's missing instead of failing.

**Write role.** Off unless `WRITE_DSN` is set. It can only flip `is_disabled` on share links
and `is_deleted` on sessions, the same updates museum makes when owners do it themselves.
Each use needs an unlocked server, a typed confirmation, and is audited.

**museum.yaml.** Read-only, optional. Lines whose key looks like a secret (password, key,
secret, token, hash, dsn, jwt...) are replaced by `<redacted>` before anything leaves the
server; the portal never writes the file.

**Notifications.** SMTP credentials come from files and are never sent to the browser
(the UI sees host, port and sender). Email previews are rendered in a sandboxed frame with
their own CSP (no scripts, no network, only embedded images). Test emails go only to the
address the admin types. Switching to sending real emails needs a typed confirmation.

Report vulnerabilities as described in [SECURITY.md](../SECURITY.md).
