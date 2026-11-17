# Admin portal

With `ENTE_ADMIN_ENABLE_ACTIONS=true`, dashboard admins can manage accounts: everything
`ente admin` (the CLI) does, plus the rest of museum's admin API that applies to a
self-hosted server. Everything goes through museum's API (the two exceptions, revoking a share
link and signing out a device, use an optional write role: docs/POSTGRES.md); nothing
runs commands on the server.

## Unlocking

Admin actions need an auth token of an Ente account listed under `internal.admins` in
museum.yaml. The portal doesn't keep one. An admin unlocks a server when needed:

1. **Unlock** → the Ente admin email. Museum emails a code (only to emails of existing
   accounts: museum would create an account for an unknown address).
2. The code and the Ente password (and the authenticator code, if two-factor is on). The
   portal opens the session token with the password the way the apps do (argon2id →
   master key → secret key → sealed token); the password isn't kept.
3. The session belongs to that dashboard user and server, lives in memory only, and ends
   after `ENTE_ADMIN_ADMIN_IDLE_MINUTES` (15) without use, `ENTE_ADMIN_ADMIN_MAX_MINUTES`
   (60) in total, on **Lock**, or when the process stops. Ending it signs the token out
   of museum. It never reaches the browser.

Museum accepts **turn off two-factor** and **delete account** only within 10 minutes of the
sign-in; after that, lock and unlock again. Passkey-only admin accounts can't unlock (the
passkey ceremony needs Ente's accounts app); add an authenticator app to the admin account.

Key derivation uses the account's own memory setting (256 MiB for current accounts), one
at a time; `ENTE_ADMIN_KDF_MAX_MIB` (512) caps it. Give the container about 200 MiB more
than that cap's real need (512 MiB limit is fine for 256 MiB accounts).

Every unlock leaves an extra "web" session on the admin account for its duration (it's
the token the portal uses). An unlock that's abandoned after the code step can leave one
behind; sign it out from the Ente app.

## Actions

| Area | Action | Museum call | CLI equivalent | Confirmation |
|---|---|---|---|---|
| Storage & plan | Change plan storage (and expiry), free plan only | `PUT /admin/user/subscription` | `update-subscription` | click |
| | Add or change extra storage (support add-on, 1–2000 GB, 1–100 years) | `POST /admin/user/bonus` | — | click |
| | Remove extra storage | `POST /admin/user/bonus` | — | click |
| Security | Turn off two-factor | `POST /admin/user/disable-2fa` | `disable-2fa` | type the email; sign-in < 10 min |
| | Remove passkeys | `POST /admin/user/disable-passkeys` | `disable-passkey` | type the email |
| | Email code at sign-in on/off | `POST /admin/user/update-email-mfa` | — | click |
| Account | Change email | `PUT /admin/user/change-email` | — | type the current email |
| | Close family plan | `POST /admin/user/close-family` | — | type the email |
| | Allow sign-in after a storage warning | `POST /admin/user/unblock-storage-warning-login` | — | click |
| Danger zone | Delete account | `DELETE /admin/user/delete` | `delete-user` | type `delete <email>`; sign-in < 10 min |
| Server | Send a test email | `POST /admin/mail` | `send-mail` | click |
| (read) | Account details: plan, extra storage, family, 2FA, sessions | `GET /admin/user` | `get-user-id` | — |
| (read) | Account list | postgres or museum source | `list-users` | — |

Each action first shows a **preview** built from the account's current state: every value
before and after. Risky ones need the text typed in; the preview is rebuilt from fresh
data when the action runs. The admin's own Ente account can't be deleted from the portal.

### Left out, on purpose

| Museum endpoint | Why |
|---|---|
| `POST /admin/user/add-ott` (set a login code for a user) | lets an admin finish someone else's sign-in: an account-takeover tool |
| `POST /admin/user/recover` | Ente's cloud support flow |
| `POST /admin/user/update-flag`, `update-referral` | internal feature flags and referral codes |
| `POST /admin/mail/subscribe`, `unsubscribe`, `GET /admin/listmonk/...`, `POST /admin/discount/add-coupons` | Ente's cloud mailing lists and billing |
| `POST /admin/queue/re-queue`, `/admin/user/init-file-counts` | internal maintenance |
| `POST /admin/user/terminate-session` | needs the session's raw token, which admins never see |
| `GET /admin/email-hash`, `POST /admin/emails-from-hashes` | lookup helpers with no use here |

Server settings (`internal.admins`, `internal.disable-registration`,
`internal.hardcoded-ott`) live in museum.yaml: change them there (in git, if you manage it
that way) and restart museum.

## Audit log

`audit.db` (SQLite) in `ENTE_ADMIN_STATE_DIR`: time, dashboard user, server, account id
and email, action, parameters, the account's state before and after, result and error.
Unlocks and locks are recorded too. Rows are only added. Passwords, codes and tokens are
never part of it (actions don't take secrets). Shown under **Audit log**; also
logged to stdout at WARNING level.

## Limits and guards

- Only dashboard admins (`ENTE_ADMIN_ADMIN_GROUPS`, or everyone without it) see the portal.
- POSTs need the `X-Ente-Admin` header and `application/json`, and are refused when the
  browser marks them cross-site (`Sec-Fetch-Site`) or the `Origin` is another host.
- Bodies over 8 KiB are refused; parameters are validated per action (types, ranges,
  dates from today to 2200, email syntax), unknown ones are rejected.
- Rate limits per dashboard user: 3 codes per server per 10 minutes, 8 unlock attempts
  per 15 minutes, 30 changes and 120 account reads per minute.

## Settings

| Variable | Default | |
|---|---|---|
| `ENTE_ADMIN_ENABLE_ACTIONS` | `false` | turn the portal on |
| `ENTE_ADMIN_<NAME>_API_URL` | `<NAME>_URL` | museum URL for admin calls, e.g. the in-cluster service |
| `ENTE_ADMIN_ADMIN_IDLE_MINUTES` | `15` | lock after this long without use |
| `ENTE_ADMIN_ADMIN_MAX_MINUTES` | `60` | lock after this long in total |
| `ENTE_ADMIN_KDF_MAX_MIB` | `512` | largest password key derivation to run |
| `ENTE_ADMIN_MAIL_FROM` | — | sender for "Send a test email" (unset: that action is off) |

Museum must be reachable from the dashboard at `API_URL` (or `URL`).
