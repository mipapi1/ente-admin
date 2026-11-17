# Configuration

Environment variables, optionally over a TOML file (`ENTE_ADMIN_CONFIG=/path/config.toml`,
same keys in lower case; instances as `[[instances]]` tables). Any variable can instead
be given as `<NAME>_FILE` pointing at a file (for secrets).

| Variable | Default | |
|---|---|---|
| `ENTE_ADMIN_HOST` | `127.0.0.1` (`0.0.0.0` in the image) | listen address |
| `ENTE_ADMIN_PORT` | `8000` | |
| `ENTE_ADMIN_AUTH` | `basic` | `basic`, `header` or `none` |
| `ENTE_ADMIN_USERS` | — | `name:pbkdf2_sha256$...` entries, comma or newline separated. Make hashes with `ente-admin hash-password` |
| `ENTE_ADMIN_TRUSTED_HEADER` | `Remote-User` | header auth: header with the username |
| `ENTE_ADMIN_TRUSTED_PROXIES` | — | header auth: addresses/CIDRs allowed to send it (required) |
| `ENTE_ADMIN_INSECURE_LOCALHOST` | `false` | required for `AUTH=none`, which also only binds to localhost |
| `ENTE_ADMIN_MODULES` | all | `overview,ticker,accounts,devices,activity,locker,notifications` |
| `ENTE_ADMIN_REVEAL_EMAILS` | `false` | show full emails and numeric account ids |
| `ENTE_ADMIN_ENABLE_ACTIONS` | `false` | turn on the admin portal, see [ADMIN.md](ADMIN.md) |
| `ENTE_ADMIN_ADMIN_IDLE_MINUTES` | `15` | portal: lock an unlocked server after this long without use |
| `ENTE_ADMIN_ADMIN_MAX_MINUTES` | `60` | portal: lock after this long in total |
| `ENTE_ADMIN_KDF_MAX_MIB` | `512` | portal: largest password key derivation (argon2 memory) to run |
| `ENTE_ADMIN_MAIL_FROM` | — | portal: sender address for "Send a test email" |
| `ENTE_ADMIN_REFRESH_SECONDS` | `300` | how long data is cached |
| `ENTE_ADMIN_STATE_DIR` | `state` (`/state` in the image) | account-id cache, label salt, shares, audit log |
| `ENTE_ADMIN_DEMO` | `false` | two synthetic instances, ignores `ENTE_ADMIN_INSTANCES` |
| `ENTE_ADMIN_GROUPS_HEADER` | `Remote-Groups` | header auth: comma-separated groups of the user |
| `ENTE_ADMIN_EMAIL_HEADER` | `Remote-Email` | header auth: the user's email (matches their own Ente account) |
| `ENTE_ADMIN_NAME_HEADER` | `Remote-Name` | header auth: display name for the user menu |
| `ENTE_ADMIN_ADMIN_GROUPS` | — | header auth: groups whose members are admins. Unset: everyone is an admin. Set: see [Viewers and sharing](#viewers-and-sharing) |
| `ENTE_ADMIN_SHARING` | `false` | admins can share accounts with other users (needs `ADMIN_GROUPS`) |
| `ENTE_ADMIN_DIRECTORY_FILE` | — | JSON list of users to pick from when sharing; without it, admins type a username |
| `ENTE_ADMIN_SHARE_GROUPS` | — | only offer directory users in one of these groups |
| `ENTE_ADMIN_LOGOUT_URL` | — | "Log out" in the user menu (your login proxy's logout URL) |
| `ENTE_ADMIN_BRANDING_DIR` | — | folder with `branding.json` and images, see [Branding](#branding) |
| `ENTE_ADMIN_NOTIFY` | `off` | email notifications for every instance: `off`, `shadow` (record, don't send) or `send`, see [NOTIFICATIONS.md](NOTIFICATIONS.md) |
| `ENTE_ADMIN_NOTIFY_INTERVAL` | `60` | seconds between notification checks (15–3600) |
| `ENTE_ADMIN_TEMPLATES_DIR` | — | your own email templates and images, over the built-in ones |
| `ENTE_ADMIN_SMTP_HOST`, `_PORT`, `_SECURITY`, `_USERNAME`, `_PASSWORD`, `_FROM`, `_FROM_NAME` | port `587`, `starttls` | SMTP for notifications; `SECURITY` is `starttls`, `ssl` or `none`. Use `_PASSWORD_FILE` for the password |
| `ENTE_ADMIN_INSTANCES` | — | comma-separated names; each configured with the variables below |

Per instance `NAME` (upper case, `-` becomes `_`):

| Variable | Source | |
|---|---|---|
| `ENTE_ADMIN_<NAME>_SOURCE` | | `museum` (default), `postgres`, `demo` |
| `ENTE_ADMIN_<NAME>_URL` | museum | museum's base URL (`http://museum:8080`, or your public API URL) |
| `ENTE_ADMIN_<NAME>_TOKEN_FILE` | museum | file with an admin account's auth token |
| `ENTE_ADMIN_<NAME>_FRESH_TOKEN_FILE` | museum | optional, a token less than 10 minutes old (account list) |
| `ENTE_ADMIN_<NAME>_API_URL` | any | portal: museum URL for admin calls (default: `URL`), e.g. `http://museum:8080` |
| `ENTE_ADMIN_<NAME>_DSN` | postgres | libpq string without password, e.g. `host=postgres dbname=ente_db user=ente_dashboard` |
| `ENTE_ADMIN_<NAME>_PASSWORD_FILE` | postgres | the role's password |
| `ENTE_ADMIN_<NAME>_EMAIL_KEY_FILE` | postgres | optional, museum's `key.encryption`, to decrypt emails |
| `ENTE_ADMIN_<NAME>_WRITE_DSN` | postgres | optional write role for revoking share links and signing out devices, see [POSTGRES.md](POSTGRES.md#optional-write-role) |
| `ENTE_ADMIN_<NAME>_WRITE_PASSWORD_FILE` | postgres | that role's password |
| `ENTE_ADMIN_<NAME>_APPS` | any | web app URLs to check, `photos=https://...,locker=https://...,share=https://...` (keys: `photos`, `accounts`, `albums`, `auth`, `cast`, `share`, `embed`, `paste`, `locker`, `memories`, `legacy`) |
| `ENTE_ADMIN_<NAME>_MUSEUM_CONFIG_FILE` | any | optional, museum.yaml mounted read-only: show its settings and build diffs (secrets are never shown) |
| `ENTE_ADMIN_<NAME>_NOTIFY` | postgres, demo | this instance's notification mode (default: `ENTE_ADMIN_NOTIFY`) |
| `ENTE_ADMIN_<NAME>_SMTP_*` | any | per-instance SMTP overrides, same keys as above |

## Viewers and sharing

Behind a login proxy that sends the user's groups and email (Authelia, Authentik,
oauth2-proxy...), set `ENTE_ADMIN_ADMIN_GROUPS`:

- members of those groups are **admins**: every account, notes and errors, actions;
- everyone else is a **viewer**: only the Ente accounts whose email equals theirs
  (compared case-insensitively; needs emails, so the postgres source with `EMAIL_KEY_FILE`
  or the museum source) plus the accounts an admin shared with them. Instances where they
  see nothing are left out. The filtering happens on the server.

Your proxy decides who may open the dashboard at all; give viewers access there.

With `ENTE_ADMIN_SHARING=true`, an admin opens an account and shares it with a username.
Shares are stored in `grants.db` in the state directory (keep it on a volume). The list
to pick from comes from `ENTE_ADMIN_DIRECTORY_FILE`:

```json
[{"user": "alice", "name": "Alice", "groups": ["photos-viewers"], "disabled": false}]
```

Disabled users, admins and (with `ENTE_ADMIN_SHARE_GROUPS`) users outside those groups
are left out. Nothing else from the file is used, so it needn't contain emails or hashes.

## Branding

`ENTE_ADMIN_BRANDING_DIR` points to a folder with a `branding.json` and the images it names:

```json
{
  "title": "Photos", "badge": "admin", "tagline": "Our photo server at a glance.",
  "accent": "#16a34a", "accent_2": "#15803d",
  "favicon": "icon.svg", "hero": "hero.png", "empty_image": "empty.png",
  "footer_image": "small.svg", "footer_text": "Photos are end-to-end encrypted.",
  "kpi_images": {"accounts": "people.webp", "uploading": "rocket.webp"}
}
```

Every key is optional. With `hero`, the page opens with a coloured banner (title, badge,
tagline, live counters and the image). `kpi_images` keys: `accounts`, `storage`, `files`,
`devices`, `uploading`. Images (png, webp, svg, jpg, gif, up to 2 MB) must sit in the same
folder; only the names listed are served. Colours must be `#rgb` or `#rrggbb`. Use only
artwork you're allowed to use.

## The account list

Museum's `GET /admin/users` only accepts a token created less than 10 minutes ago
(`AdminTokenValidityInMinutes` in museum). Per-account details (`GET /admin/user?id=`)
accept any admin token. So:

1. Right after you log in with the Ente CLI, the token is fresh: the first refresh lists
   the accounts and caches their ids (ids only) in the state directory.
2. Later refreshes use the cache and fetch each account's details with the same token.
3. To pick up new accounts, log in again and put the new token in `FRESH_TOKEN_FILE`
   within 10 minutes. The page says how old the cached list is.

The postgres source has no such limit.
