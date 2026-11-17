# Setting up ente-admin

This guide adds ente-admin to a self-hosted Ente server. It follows the layout of Ente's
[self-hosting docs](https://ente.com/help/self-hosting/): if you used the quickstart, you
already have a `my-ente` folder with `compose.yaml` and `museum.yaml`.

## Prerequisites

- A running Ente server (museum), version from 2025 or later.
- Docker with Compose v2 (or Kubernetes, see `deploy/kubernetes/`, or Python 3.12+ with
  `pip install ente-admin`, see `deploy/standalone/`).
- An Ente account to administer the server with. A separate account with no photos is best:
  its token is a full login for it.
- The [Ente CLI](https://github.com/ente-io/ente/tree/main/cli), for the admin token.

Optional, for more:

- Access to museum's Postgres database (Locker, share links, storage, notifications).
- An SMTP server (email notifications).

## 1. Get the code

```sh
git clone https://github.com/mipapi1/ente-admin.git
cd ente-admin
cp .env.example .env
mkdir -p secrets
```

Secrets go in `secrets/` as files. On Linux the container runs as user 10001: make the
files readable by it (`sudo chown -R 10001 secrets && chmod 700 secrets`). Docker Desktop
doesn't need this.

## 2. A dashboard login

```sh
printf 'admin:' > secrets/users
docker compose run --rm ente-admin hash-password | tr -d '\r' >> secrets/users
```

It asks for a password (12 characters or more). Add more users as more `name:hash` lines.
Behind a login proxy, use `ENTE_ADMIN_AUTH=header` instead (see [Reverse proxy](#7-reverse-proxy)).

Try it now with synthetic data: `.env` starts with `ENTE_ADMIN_DEMO=true`.

```sh
docker compose up -d
```

Open http://localhost:8001 and sign in. Then remove `ENTE_ADMIN_DEMO` from `.env` to connect
your server.

## 3. Museum admins

Museum lets only its admins use the admin API. In `museum.yaml`, list your admin account's
user id:

```yaml
internal:
  admins:
    - 1580559962386438
```

Find the id with the CLI (`ente admin list-users`, or the account's details), or let the
dashboard build the snippet: **Server → museum.yaml change…**. Without an `admins` list,
museum treats the first account created as the admin. Restart museum after changing it.

## 4. Admin token

With the Ente CLI pointed at your server (its `config.yaml` has `endpoint.api`):

```sh
ente account add                    # sign in as the admin account
ente account get-token --app photos --email admin@example.com > secrets/token
```

Then in `.env`:

```sh
ENTE_ADMIN_INSTANCES=photos
ENTE_ADMIN_PHOTOS_SOURCE=museum
ENTE_ADMIN_PHOTOS_URL=http://museum:8080
ENTE_ADMIN_PHOTOS_TOKEN_FILE=/secrets/token
```

`URL` must be reachable from the container: next to the quickstart use
`deploy/compose/ente-admin.yaml`, which joins its network (`http://museum:8080`); otherwise
use your public API URL.

> **Note.** Museum lists accounts only for a token less than 10 minutes old. The first start
> right after `get-token` caches the account ids. To pick up new accounts later, get a new
> token and put it in `secrets/fresh-token` (`ENTE_ADMIN_PHOTOS_FRESH_TOKEN_FILE`). The postgres
> source has no such limit.

## 5. Connect to Photos data (postgres, optional)

For Locker, share links, storage, uploads per day and notifications, read museum's database
with a read-only role. As a Postgres superuser:

```sql
CREATE ROLE ente_dashboard LOGIN PASSWORD '<random>';
GRANT CONNECT ON DATABASE ente_db TO ente_dashboard;
GRANT USAGE ON SCHEMA public TO ente_dashboard;
GRANT SELECT ON users, subscriptions, usage, trash TO ente_dashboard;
GRANT SELECT (owner_id, is_deleted) ON collections TO ente_dashboard;
GRANT SELECT (owner_id, updation_time, info) ON files TO ente_dashboard;
GRANT SELECT (user_id, app, user_agent, creation_time, last_used_at, is_deleted) ON tokens TO ente_dashboard;
```

Put the password in `secrets/db-password`, and museum's `key.encryption` (from `museum.yaml`
or its credentials file) in `secrets/email-key` to show emails. Then:

```sh
ENTE_ADMIN_PHOTOS_SOURCE=postgres
ENTE_ADMIN_PHOTOS_DSN=host=postgres port=5432 dbname=ente_db user=ente_dashboard
ENTE_ADMIN_PHOTOS_PASSWORD_FILE=/secrets/db-password
ENTE_ADMIN_PHOTOS_EMAIL_KEY_FILE=/secrets/email-key
ENTE_ADMIN_PHOTOS_URL=https://api.photos.example.com
```

**Server → Setup checklist** shows which optional grants are missing and the lines to add
them. See [POSTGRES.md](POSTGRES.md).

## 6. Admin portal (optional)

To manage accounts (storage, two-factor, passkeys, email, deletion) from the dashboard:

```sh
ENTE_ADMIN_ENABLE_ACTIONS=true
ENTE_ADMIN_PHOTOS_API_URL=http://museum:8080
```

Admins unlock a server with the Ente admin account's email code and password when needed;
nothing is stored. See [ADMIN.md](ADMIN.md).

### Locker (optional)

If you host Ente Locker, list the web apps so the dashboard checks them:

```sh
ENTE_ADMIN_PHOTOS_APPS=photos=https://photos.example.com,locker=https://locker.example.com,share=https://share.example.com
```

and set `apps.public-locker` in `museum.yaml` for share links. See [LOCKER.md](LOCKER.md).

### Email (optional)

Backup reminders after 15 and 30 days, album-shared and new-photo emails need the postgres
source and SMTP:

```sh
ENTE_ADMIN_NOTIFY=shadow
ENTE_ADMIN_SMTP_HOST=smtp.example.com
ENTE_ADMIN_SMTP_PORT=587
ENTE_ADMIN_SMTP_USERNAME=noreply@example.com
ENTE_ADMIN_SMTP_PASSWORD_FILE=/secrets/smtp-password
ENTE_ADMIN_SMTP_FROM=noreply@example.com
```

Start in `shadow` (record, don't send), check **Notifications**, then switch to `send` there.
See [NOTIFICATIONS.md](NOTIFICATIONS.md).

Apply changes with `docker compose up -d`. `docker compose run --rm ente-admin check-config`
validates the settings.

## 7. Reverse proxy

The container listens on plain HTTP, published on `127.0.0.1:8001`. Put a reverse proxy
with TLS in front, and don't expose it publicly without a login in front (see
[SECURITY.md](../SECURITY.md#hardening-checklist)).

Caddy (ente-admin does the login itself):

```caddyfile
admin.photos.example.com {
	reverse_proxy 127.0.0.1:8001
}
```

Behind a login proxy that sets `Remote-User` (Authelia, Authentik, oauth2-proxy), let the
proxy do the login:

```sh
ENTE_ADMIN_AUTH=header
ENTE_ADMIN_TRUSTED_PROXIES=172.16.0.0/12   # the proxy's address only
ENTE_ADMIN_ADMIN_GROUPS=admins             # optional: everyone else sees only their own account
```

nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## 8. Upgrading

```sh
git pull
docker compose build --pull
docker compose up -d
```

Read CHANGELOG.md first. Settings and data stay in `.env`, `secrets/` and the `state` volume
(audit log, notification history, caches). Back up the volume if you rely on them:

```sh
docker run --rm -v ente-admin_state:/state -v "$PWD":/out alpine tar czf /out/state.tgz -C /state .
```

## Troubleshooting

See the README's [Troubleshooting](../README.md#troubleshooting) section.
