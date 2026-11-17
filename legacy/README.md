# legacy

The first version of this dashboard: a single `app.py` (Python standard library +
psycopg + PyNaCl) that read museum's Postgres directly with a read-only role and decrypted
account emails with museum's `key.encryption`. Kept for reference and as a fallback.

Differences from the current app:
- direct database access only (no museum API source, no demo mode)
- per-viewer scoping and admin-managed sharing (the current app has both since 0.2.0,
  as options: `ENTE_ADMIN_ADMIN_GROUPS`, `ENTE_ADMIN_SHARING`)
- showed Ente's own illustrations (not included here; they are Ente's artwork; the
  current app can show any artwork you provide with `ENTE_ADMIN_BRANDING_DIR`)

Hostnames and instance names were replaced with `example.com` placeholders. To go back to
it, see [docs/REVERT.md](../docs/REVERT.md).
