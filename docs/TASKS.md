# Hand-done tasks and where they are in the portal

Everything an operator used to do by hand on a self-hosted Ente server: with the Ente CLI,
SQL against museum's database, edits to museum.yaml or scripts. Each one is either a portal
action (with a route and an audit entry), or listed under **Not in the portal** with the
reason.

"Unlock" means an Ente admin sign-in for that server (docs/ADMIN.md). "Write role" is the
optional database role for the two database changes (docs/POSTGRES.md).

## In the portal

| Task | Before | Where now | Route | Audit action | Needs |
|---|---|---|---|---|---|
| Find an account by email | `ente admin get-user-id`, SQL | Accounts → search | `GET /api/data` | — | — |
| Raise or lower storage | `ente admin update-subscription` | Account → Manage → Storage & plan | `POST /api/admin/run` `set-storage`, `set-bonus`, `remove-bonus` | same as the action | unlock |
| Turn off two-factor, remove passkeys | `ente admin disable-2fa`, `disable-passkey` | Account → Manage → Security | `POST /api/admin/run` | same | unlock, typed email |
| Change an account's email | museum API with curl | Account → Manage → Account | `POST /api/admin/run` `change-email` | same | unlock, typed email |
| Delete an account | `ente admin delete-user` | Account → Manage → Danger zone | `POST /api/admin/run` `delete-user` | same | unlock, typed `delete <email>` |
| See signed-in devices | SQL on `tokens` | Account → Devices | `GET /api/admin/sessions` | — | — |
| Sign out a device | SQL `update tokens` | Account → Devices → Sign out | `POST /api/admin/sessions/signout` | `sign-out-device` | unlock, write role, typed `sign out` |
| Who hasn't backed up in N days | SQL + ente-notify script | Notifications → Upcoming | `GET /api/admin/notify/upcoming` | — | postgres source |
| Send a backup reminder now | ente-notify script | Notifications → Upcoming → Send | `POST /api/admin/notify/send` | `notify-send` | — |
| Resend a failed or missed email | rerun the script, edit its state file | Notifications → History → Resend | `POST /api/admin/notify/resend` | `notify-resend` | — |
| Stop emails to one person | edit the script's state file | Notifications → Suppress | `POST /api/admin/notify/suppress`, `/unsuppress` | `notify-suppress`, `notify-unsuppress` | — |
| Change reminder days, send hours, email kinds | edit the script, redeploy | Notifications → Settings | `POST /api/admin/notify/settings` | `notify-settings` | typed `send emails` to turn sending on |
| Change email wording | edit templates, redeploy | Notifications → Templates | `POST /api/admin/notify/template`, `/template-reset` | `notify-template`, `notify-template-reset` | — |
| Check SMTP works | `ente admin send-mail`, swaks | Notifications → Email (SMTP) → Check / Send a test email | `GET /api/admin/notify/smtp`, `POST /api/admin/notify/test` | `notify-test` | — |
| Take over from ente-notify | copy its state by hand | Notifications → Settings → notified.json | `POST /api/admin/notify/import` | `notify-import` | — |
| Locker items, size and limits per person | SQL on `collections`, `files`, `usage` | Locker → Usage and limits | `GET /api/data` (Locker fields) | — | postgres source |
| List public links | SQL on `public_collection_tokens`, `public_file_tokens` | Locker → Share links | `GET /api/admin/links` | — | postgres source |
| Revoke a public link | SQL `update ... set is_disabled` | Locker → Share links → Revoke | `POST /api/admin/links/revoke` | `revoke-link` | unlock, write role, typed `revoke` |
| Turn the Locker or share apps on | edit `apps:` in museum.yaml | Locker / Server → museum.yaml change… | `POST /api/admin/config/propose`, `/verify` | `museum-config-proposed`, `museum-config-verified` | you apply the change |
| Add a museum admin, close sign-ups | edit `internal:` in museum.yaml | Server → museum.yaml change… | same | same | you apply the change |
| Check the web apps answer | curl each URL | Server → Web apps | `GET /api/admin/apps` | — | — |
| Where files are stored, missing copies | SQL on `object_keys` (datacenters) | Server → Storage and backups | `GET /api/admin/storage` | — | postgres source |
| Note a backup restore test | a text file | Server → Record a backup check… | `POST /api/admin/backups/check` | `backup-check` | — |
| Check database grants | `\dp` in psql | Server → Setup checklist (shows the missing GRANTs) | `GET /api/admin/setup` | — | — |

## Not in the portal

| Task | Why not |
|---|---|
| Writing museum.yaml, restarting museum | The portal would need write access to the server's config and a way to restart it, which turns a dashboard compromise into a server compromise. It shows the diff; you apply it and press Verify. |
| Running SQL, shell commands, kubectl | No raw shell or SQL from a browser. The few database changes it makes are fixed queries on two columns through a separate, optional role. |
| Granting database access | Needs a superuser. The setup checklist prints the exact `GRANT` lines to run. |
| Changing Locker limits | Museum hardcodes them (100 items / 1 GB free, 1,000 / 10 GB paid). Extending someone's plan or adding extra storage moves them to the paid tier. |
| Reading Locker items, file names, photos | End-to-end encrypted. The portal sees only counts, sizes and timestamps. |
| Resetting a password | Impossible with end-to-end encryption: the password protects the keys. People use their recovery key. |
| Creating accounts | Sign-up goes through the apps (and email verification). |
| Reading session tokens | The portal never reads the `tokens.token` column; devices are identified by their creation time. |
| Backups, secrets, DNS, reverse proxy, TLS | Infrastructure outside Ente; the portal records backup checks and reports what it can reach. |
