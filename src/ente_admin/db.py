"""Connections to museum's Postgres for the postgres source, notifications, Locker views and
the opt-in write role.

Every optional feature reads through `Features.run()`: if the role lacks a grant (or an
older museum lacks a table), the feature is marked unavailable with the reason and the SQL
that would enable it, and the rest of the page still works. Connections run in autocommit,
so one refused query doesn't abort the others.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("ente_admin.db")


class DBError(Exception):
    pass


@dataclass
class PG:
    dsn: str
    password_file: str = ""
    readonly: bool = True

    def connect(self):
        import psycopg  # optional dependency: pip install 'ente-admin[postgres]'
        password = Path(self.password_file).read_text().strip() if self.password_file else None
        opts = "-c default_transaction_read_only=on" if self.readonly else None
        conn = psycopg.connect(self.dsn, password=password, connect_timeout=5, options=opts)
        conn.autocommit = True
        return conn


def missing_privilege(e: Exception) -> bool:
    """A refused or impossible query (no grant, no such table/column): the feature is off."""
    name = type(e).__name__
    return name in ("InsufficientPrivilege", "UndefinedTable", "UndefinedColumn", "UndefinedObject")


@dataclass
class Features:
    """What the database role allowed this round: feature -> None (ok) or the reason it's off."""
    status: dict[str, str | None] = field(default_factory=dict)

    def run(self, cur, feature: str, sql: str, args=()) -> list | None:
        if self.status.get(feature):  # already failed this round
            return None
        try:
            cur.execute(sql, args)
            rows = cur.fetchall()
        except Exception as e:  # noqa: BLE001 - classified below
            if not missing_privilege(e):
                raise
            msg = str(e).splitlines()[0]
            self.status[feature] = msg
            log.info("feature %s unavailable: %s", feature, msg)
            return None
        self.status.setdefault(feature, None)
        return rows

    def public(self) -> dict[str, dict]:
        return {k: {"ok": v is None, "reason": v, "grant": GRANTS.get(k, "")} for k, v in self.status.items()}


# The grants each optional feature needs (docs/POSTGRES.md). Shown in Server -> Setup when
# a feature is unavailable, ready to copy. Never anything that holds secrets (tokens.token,
# keys, encrypted metadata, link access tokens, password hashes).
GRANTS = {
    "locker": "GRANT SELECT (collection_id, owner_id, app, type, is_deleted) ON collections TO ente_dashboard;\n"
              "GRANT SELECT (collection_id, file_id, f_owner_id, is_deleted, created_at) ON collection_files "
              "TO ente_dashboard;\n"
              "GRANT SELECT (file_id, size, is_deleted) ON object_keys TO ente_dashboard;",
    "links": "GRANT SELECT (id, collection_id, is_disabled, valid_till, device_limit, created_at, updated_at,\n"
             "  enable_download, enable_collect, enable_join) ON public_collection_tokens TO ente_dashboard;\n"
             "GRANT SELECT (id, file_id, owner_id, app, is_disabled, valid_till, device_limit, created_at,\n"
             "  updated_at, enable_download) ON public_file_tokens TO ente_dashboard;\n"
             "GRANT SELECT (collection_id, owner_id, app) ON collections TO ente_dashboard;",
    "sharing": "GRANT SELECT (collection_id, from_user_id, to_user_id, role_type, is_deleted) ON collection_shares "
               "TO ente_dashboard;",
    "legacy": "GRANT SELECT (user_id, emergency_contact_id, state, notice_period_in_hrs, created_at, updated_at)\n"
              "  ON emergency_contact TO ente_dashboard;\n"
              "GRANT SELECT (id, user_id, variant, notice_period_in_hrs, is_deleted, created_at, updated_at)\n"
              "  ON legacy_kit TO ente_dashboard;",
    "paid_tier": "GRANT SELECT (user_id, type, storage, valid_till, is_revoked) ON storage_bonus TO ente_dashboard;",
    "storage": "GRANT SELECT (file_id, o_type, size, datacenters, is_deleted) ON object_keys TO ente_dashboard;\n"
               "GRANT SELECT (file_id, app) ON files TO ente_dashboard;",
    "uploads": "GRANT SELECT (collection_id, file_id, f_owner_id, is_deleted, created_at) ON collection_files "
               "TO ente_dashboard;\nGRANT SELECT (collection_id, owner_id) ON collections TO ente_dashboard;",
}

# The opt-in write role (WRITE_DSN): only flips "disabled"/"deleted" flags, like museum does
WRITE_GRANTS = """CREATE ROLE ente_dashboard_write LOGIN PASSWORD '<random>';
GRANT CONNECT ON DATABASE ente_db TO ente_dashboard_write;
GRANT USAGE ON SCHEMA public TO ente_dashboard_write;
-- revoke share links (museum's own "remove link" does the same update)
GRANT SELECT (id, collection_id, is_disabled), UPDATE (is_disabled) ON public_collection_tokens TO ente_dashboard_write;
GRANT SELECT (id, owner_id, is_disabled), UPDATE (is_disabled) ON public_file_tokens TO ente_dashboard_write;
-- sign out a device (museum's own logout does the same update)
GRANT SELECT (user_id, creation_time, is_deleted), UPDATE (is_deleted) ON tokens TO ente_dashboard_write;"""
