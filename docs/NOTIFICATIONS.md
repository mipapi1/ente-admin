# Email notifications

Self-hosted Ente doesn't email people when they stop backing up, when someone shares an
album with them, or when new photos land in a shared album. The notifications module does,
from museum's database, through your SMTP server. It needs the **postgres source**
([POSTGRES.md](POSTGRES.md)); demo instances work too, for trying it out.

| Email | When | How often |
|---|---|---|
| Backup reminder (`inactivity`) | an account hasn't been used for 15 or 30 days (the thresholds are yours to change) | once per threshold per absence; using the account again resets it. Someone past several thresholds at once gets only the highest one |
| Album shared (`share`) | someone adds a person to an album | once per share. Shares that exist when the module first runs aren't announced |
| New photos (`uploads`) | someone adds photos to a shared album | one email per album per person per day, never to the person who added them |

"Used" means any of: a device called museum (background backup counts), an upload, or the
account being created.

## Modes

Each server runs in one mode, set with `ENTE_ADMIN_NOTIFY` (or `ENTE_ADMIN_<NAME>_NOTIFY`)
and changeable in **Notifications**:

- `off`: nothing is read or sent.
- `shadow`: everything is worked out and recorded in the history as "shadow", nothing is
  sent. Use it to see what would go out, or while another tool still sends.
- `send`: emails go out. Switching to it in the portal needs the phrase `send emails` typed.

Reminders go out only between the send hours (10:00–20:00 by default, the container's
time zone); due ones wait for the window. Shares and new photos go out as soon as they're
seen.

## Setup

1. SMTP: `ENTE_ADMIN_SMTP_HOST`, `_PORT` (587), `_SECURITY` (`starttls`, `ssl`, `none`),
   `_USERNAME`, `_PASSWORD_FILE`, `_FROM`, `_FROM_NAME`. Per-server overrides:
   `ENTE_ADMIN_<NAME>_SMTP_*`.
2. `ENTE_ADMIN_NOTIFY=shadow` and restart. Open **Notifications**: **Check** tests the SMTP
   connection; **Send a test email** sends any template to the address you type, never to
   an account.
3. Look at **Upcoming** and **History** for a day or two. Then switch to `send`.

Check the SMTP server's own limits and SPF/DKIM for the `From` domain, or the emails will
land in spam.

## In the portal

- **Upcoming**: every account, when it was last used, the reminders already sent this
  absence and the next one with its date. **Send…** sends one now (it counts as that
  threshold's reminder); **Suppress** stops emails to that person, for some days or until
  removed.
- **History**: every email with time, recipient, kind, threshold, trigger (automatic,
  manual, resend, test), result and error. Filter by kind and result; **Resend** any of them.
- **Suppressed**: the list, with who suppressed it, why and until when.
- **Templates**: edit subject, text and HTML, with a live preview and a test send. **Reset**
  goes back to the folder's or the built-in version.
- **Settings**: thresholds (1–6 values, 1–365 days), send hours, which kinds, the server name
  used in emails, and importing ente-notify's state.

Every change goes to the audit log.

## Templates

Lookup order: edited in the portal → `ENTE_ADMIN_TEMPLATES_DIR` → built-in.

Files in the folder: `<id>.subject`, `<id>.txt`, `<id>.html`. Ids: `inactivity`,
`inactivity-<days>` (one threshold, e.g. `inactivity-30`), `inactivity-last` (the highest
threshold), `share`, `uploads`, `test`. A reminder uses `inactivity-<days>` if it exists,
then `inactivity-last` for the last threshold, then `inactivity`.

Placeholders are `${name}`; values are escaped in HTML:

| Template | Values |
|---|---|
| all | `email`, `name` (before the @), `server_name`, `app_url`, `sender_name` |
| `inactivity` | `days` (the threshold), `days_inactive`, `last_active` (date) |
| `share` | `sharer`, `rights` ("view it", "view it and add photos", ...) |
| `uploads` | `adders`, `count`, `count_text` ("5 new photos"), `album` |

An HTML body without `<html` is wrapped in a simple layout. Images: `src="cid:logo.png"`
embeds `logo.png` from the folder; `src="cid:random:art-*.png"` embeds one matching file at
random.

## Taking over from ente-notify

If a script already sends these emails, keep this module in `shadow`, import the script's
`notified.json` in **Settings** (who was already reminded, which shares were announced, where
it stopped reading new photos), stop the script, then switch to `send`. Nobody gets an email
twice.

## Data

`notify.db` (SQLite) in the state directory: history, suppressions, settings, edited
templates and the module's own bookkeeping. Nothing is written to museum's database.
