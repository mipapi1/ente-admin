# Changelog

## 0.4.0 (2026-10-25)
- Email notifications (`notifications` module, docs/NOTIFICATIONS.md): backup reminders
  after 15 and 30 days without activity, album-shared and new-photo emails, per server
  `off`/`shadow`/`send`. Upcoming reminders, full history, resend, send now, suppress,
  template editor with preview and test sends, thresholds and send hours, SMTP check,
  import of ente-notify's state. Templates from `ENTE_ADMIN_TEMPLATES_DIR`.
- Locker (docs/LOCKER.md): items, size and limits per person, share links (view, filter,
  revoke with the optional write role), sharing and trusted-contact counts, web app checks.
- Server: setup checklist with the missing grants, web app checks, storage per bucket and
  missing copies, backup check log, museum.yaml change builder (snippet, diff with secrets
  redacted, verify after you apply it; never writes the file).
- Devices per account, and signing one out with the optional write role.
- New layout: Overview, Accounts, Locker, Notifications, Server and Audit log sections;
  quick filters on accounts; works on phones and in dark mode.
- Optional write role (`<NAME>_WRITE_DSN`), web app URLs (`<NAME>_APPS`), museum.yaml
  (`<NAME>_MUSEUM_CONFIG_FILE`), SMTP settings and `ENTE_ADMIN_NOTIFY`, see docs/CONFIG.md.
- docker-compose.yml, .env.example, config.example.toml and docs/SETUP.md.

## 0.3.0
- Admin portal (`ENTE_ADMIN_ENABLE_ACTIONS`, docs/ADMIN.md): unlock a server with an Ente
  admin sign-in (email code, password, TOTP), kept in memory and locked when idle; then
  plan storage and expiry, extra storage, two-factor, passkeys, email codes, email change,
  family plans, account deletion and a test email, all through museum's admin API, each
  with a before/after preview, typed confirmation for risky ones, rate limits and an
  audit log (`audit.db`, shown in the UI). Server health per instance.
- Replaces the old disable-2FA action and its `/api/actions/*` route; `FRESH_TOKEN_FILE`
  is now only used for the account list.
- `pynacl` is a base dependency (the portal opens Ente tokens).

## 0.2.1 (unreleased)
- Phones: KPI cards with artwork take a full row instead of hiding the image.

## 0.2.0 (unreleased)
- Viewers: with `ENTE_ADMIN_ADMIN_GROUPS` (header login), members of those groups are
  admins; anyone else sees only the accounts with their own email plus the ones shared
  with them. Filtered on the server.
- Sharing (`ENTE_ADMIN_SHARING`): admins share an account with a user, optionally picked
  from a directory file (`ENTE_ADMIN_DIRECTORY_FILE`, `ENTE_ADMIN_SHARE_GROUPS`).
- Branding (`ENTE_ADMIN_BRANDING_DIR`): title, colours, favicon, hero, KPI images, footer
  and empty-state image from a folder you provide.
- User menu with name, role and an optional log-out link (`ENTE_ADMIN_LOGOUT_URL`).
- Activity ticker module; tooltips with files and size per day on every chart;
  detailed device names (model, OS, Ente version); size of the files per account.
- The page reloads when the login proxy's session has ended.

## 0.1.0 (unreleased)
- First release: museum API, postgres and demo sources; overview, accounts, devices and
  activity modules; Basic or trusted-header login; masking by default; optional
  disable-2FA action; Docker, Compose, Kubernetes and standalone deployment.
