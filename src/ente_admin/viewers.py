"""Who is looking, and which accounts they may see.

Without ENTE_ADMIN_ADMIN_GROUPS every logged-in user is an admin and sees everything.
With it (header auth only), members of those groups are admins; anyone else sees the
Ente accounts whose email matches their own (from the login proxy's email header) plus
the accounts an admin shared with them. The filtering happens on the server, so a
browser never receives accounts it may not see.

Shares are stored in SQLite in the state directory: (username, instance, account id).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")


@dataclass
class Viewer:
    user: str
    name: str = ""
    email: str = ""
    groups: list[str] = field(default_factory=list)
    admin: bool = True

    def public(self) -> dict:
        return {"user": self.user, "name": self.name or self.user, "admin": self.admin, "email": self.email}


def _clean(value: str | None, limit: int = 256) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit and value.isprintable() else ""


def from_headers(user: str, headers, cfg) -> Viewer:
    """The viewer for a request already authenticated as `user`. The extra headers are only
    read in header mode, where the request is known to come from a trusted proxy."""
    if cfg.auth != "header":
        return Viewer(user=user)
    groups = [g.strip() for g in _clean(headers.get(cfg.groups_header), 4096).split(",") if g.strip()]
    admin = not cfg.admin_groups or any(g in cfg.admin_groups for g in groups)
    return Viewer(user=user, name=_clean(headers.get(cfg.name_header)),
                  email=_clean(headers.get(cfg.email_header)).lower(), groups=groups, admin=admin)


class Grants:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "grants.db"
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("""create table if not exists grants (
            username text not null, instance text not null, account_id integer not null,
            granted_by text not null, granted_at real not null,
            primary key (username, instance, account_id))""")
        return conn

    def all(self) -> list[tuple[str, str, int]]:
        with self._lock, self._db() as conn:
            return conn.execute("select username, instance, account_id from grants order by username").fetchall()

    def change(self, add: bool, username: str, instance: str, account_id: int, by: str) -> None:
        with self._lock, self._db() as conn:
            if add:
                conn.execute("insert or ignore into grants values (?, ?, ?, ?, ?)",
                             (username, instance, account_id, by, time.time()))
            else:
                conn.execute("delete from grants where username = ? and instance = ? and account_id = ?",
                             (username, instance, account_id))


def candidates(cfg) -> list[dict] | None:
    """Users an admin can share with, from ENTE_ADMIN_DIRECTORY_FILE: enabled, in one of
    ENTE_ADMIN_SHARE_GROUPS (if set), and not admins (they see everything anyway).
    None when there is no directory: the UI then asks for a username instead."""
    if not cfg.directory_file:
        return None
    try:
        people = json.loads(Path(cfg.directory_file).read_text())
    except (OSError, ValueError):
        return []
    out = []
    for p in people if isinstance(people, list) else []:
        if not isinstance(p, dict) or not USERNAME_RE.match(str(p.get("user", ""))) or p.get("disabled"):
            continue
        groups = [str(g) for g in p.get("groups") or []]
        if cfg.share_groups and not any(g in cfg.share_groups for g in groups):
            continue
        if cfg.admin_groups and any(g in cfg.admin_groups for g in groups):
            continue
        out.append({"user": str(p["user"]), "name": _clean(str(p.get("name") or p["user"])) or str(p["user"])})
    return sorted(out, key=lambda c: c["user"])
