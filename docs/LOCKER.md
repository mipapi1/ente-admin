# Ente Locker

[Ente Locker](https://ente.com/locker) stores documents, notes, secrets and other items,
end-to-end encrypted, on the same museum as Photos. The **Locker** section shows how it's
used on your server. Like the rest of the dashboard it sees metadata only: counts, sizes,
dates and states, never names or contents. It needs the postgres source with the `locker`
grants ([POSTGRES.md](POSTGRES.md#optional-grants-for-the-newer-views)); without them it
says what's missing.

## What it shows

- **Usage and limits** per person: items and size against their tier's limits, Locker share
  links, albums shared out and in, trusted contacts and legacy kits.
- **Share links**: every public link on the server (Locker, Photos albums, single files), its
  owner, dates, options and state; filter by app and state. **Revoke** needs the optional
  write role, an unlocked server and typing `revoke` (docs/POSTGRES.md#optional-write-role).
  Owners can always remove their own links in the app.
- **Locker web apps**: whether the Locker web app and the share-link app answer, and whether
  museum's `apps.public-locker` points at the share app.
- The **Accounts** table gets a Locker column and a **Uses Locker** filter; the account
  panel shows the same per-person details.

## Limits

Museum fixes them in code (`server/pkg/controller/usage.go`), not in museum.yaml:

| Tier | Items | Storage |
|---|---|---|
| free | 100 | 1 GB |
| paid | 1,000 | 10 GB |

On a self-hosted server an account counts as paid while its subscription is valid or it has
extra storage (a support add-on). To move someone to the paid tier, extend their plan or add
extra storage (Account → Manage → Storage & plan). The dashboard can't change the limits
themselves; that would need a change to museum.

## Turning it on

The Locker apps talk to the same museum. What a server needs:

1. Host the Locker web app (and, for share links, the share app) like the other Ente web
   apps, and list their URLs in `ENTE_ADMIN_<NAME>_APPS` (`locker=https://...,share=https://...`)
   so the dashboard checks them.
2. Tell museum where share links live, in museum.yaml:

   ```yaml
   apps:
     public-locker: https://share.example.com
   ```

**museum.yaml change…** (in Locker or Server) builds this for you: pick the apps and URLs,
add museum admins or close sign-ups, and it shows the snippet, and with
`ENTE_ADMIN_<NAME>_MUSEUM_CONFIG_FILE` mounted read-only, a diff against your file with
secrets replaced by `<redacted>`. The portal never writes museum.yaml: you apply the change,
restart museum, then press **Verify**, which checks museum answers, the apps answer and the
file has the new values. Both steps go to the audit log.

## Backups

Locker files are stored in the same buckets as photos. **Server → Storage and backups** shows
which buckets hold each app's files (from museum's `object_keys`) and flags files stored in fewer
buckets than the rest. Backups of the buckets and the database happen outside Ente; after
testing a restore, record it with **Record a backup check…** so the next person knows when
it last worked.
