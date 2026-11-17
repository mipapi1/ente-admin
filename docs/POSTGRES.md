# The postgres source

Optional. Reads museum's database directly for what the API doesn't report: albums, trash,
uploads per day and "uploading now". Create a role that can read only these columns:

```sql
CREATE ROLE ente_dashboard LOGIN PASSWORD '<random>';
GRANT CONNECT ON DATABASE ente_db TO ente_dashboard;
GRANT USAGE ON SCHEMA public TO ente_dashboard;
GRANT SELECT ON users, subscriptions, usage, trash TO ente_dashboard;
GRANT SELECT (owner_id, is_deleted) ON collections TO ente_dashboard;
GRANT SELECT (owner_id, updation_time, info) ON files TO ente_dashboard;
GRANT SELECT (user_id, app, user_agent, creation_time, last_used_at, is_deleted) ON tokens TO ente_dashboard;
```

`files.info` is museum's own record of each file's size (not the encrypted metadata). No access
to tokens' secret columns, keys, encrypted file metadata or anything else. Emails are
stored encrypted; to show them, give the dashboard museum's `key.encryption`
(`EMAIL_KEY_FILE`). Without it accounts are shown by label. The image needs the extras:
`docker build --build-arg EXTRAS=postgres`, or `pip install 'ente-admin[postgres]'`.

## Optional grants for the newer views

Each feature checks its own grants and, if one is missing, says so in **Server → Setup
checklist** with the exact lines to run; the rest of the dashboard keeps working. Grant
only what you want shown:

| Feature | Shows | Grants |
|---|---|---|
| `locker` | Locker items and size per person | `collections (collection_id, owner_id, app, type, is_deleted)`, `collection_files (collection_id, file_id, f_owner_id, is_deleted, created_at)`, `object_keys (file_id, size, is_deleted)` |
| `links` | public album, file and Locker links | `public_collection_tokens` and `public_file_tokens` (ids, flags, dates; never the link token itself), `collections (collection_id, owner_id, app)` |
| `sharing` | who shares with whom; album-shared emails | `collection_shares (collection_id, from_user_id, to_user_id, role_type, is_deleted)` |
| `legacy` | trusted contacts and legacy kits (counts and states) | `emergency_contact`, `legacy_kit` (ids, states, dates) |
| `paid_tier` | free or paid Locker limits | `storage_bonus (user_id, type, storage, valid_till, is_revoked)` |
| `storage` | which buckets hold each file, missing copies | `object_keys (file_id, o_type, size, datacenters, is_deleted)`, `files (file_id, app)` |
| `uploads` | new-photo emails for shared albums | `collection_files (...)`, `collections (collection_id, owner_id)` |

The exact statements are in `GRANTS` in `src/ente_admin/db.py` and in the checklist.
Notifications also need the base grants above (activity comes from `tokens.last_used_at`
and `files.updation_time`).

## Optional write role

Off unless `ENTE_ADMIN_<NAME>_WRITE_DSN` is set. It lets admins **revoke a share link**
and **sign out a device**, the same single-column updates museum makes when the owner does
it in the app. Each one also needs the server unlocked with an Ente admin sign-in and a
typed confirmation, and goes to the audit log.

```sql
CREATE ROLE ente_dashboard_write LOGIN PASSWORD '<random>';
GRANT CONNECT ON DATABASE ente_db TO ente_dashboard_write;
GRANT USAGE ON SCHEMA public TO ente_dashboard_write;
GRANT SELECT (id, collection_id, is_disabled), UPDATE (is_disabled) ON public_collection_tokens TO ente_dashboard_write;
GRANT SELECT (id, owner_id, is_disabled), UPDATE (is_disabled) ON public_file_tokens TO ente_dashboard_write;
GRANT SELECT (user_id, creation_time, is_deleted), UPDATE (is_deleted) ON tokens TO ente_dashboard_write;
```

Museum caches link and session checks for about a minute, so a revoked link or signed-out
device stops working within a minute. Try it on a test server first.
