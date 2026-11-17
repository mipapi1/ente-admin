"""Notification history, per-instance state, suppressions, settings and edited templates,
in `notify.db` (SQLite) in the state directory.

History rows are only added (resends are new rows pointing at the original). Emails are
stored with each row so the history stays readable after an account is deleted; nothing
else personal is kept, and never message bodies.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
create table if not exists events (
    id integer primary key autoincrement, at real not null, instance text not null,
    kind text not null, level integer, user_id integer, email text,
    status text not null,      -- sent | failed | shadow | suppressed
    trigger text not null,     -- auto | manual | resend | test
    actor text, subject text, detail text, error text, ref integer);
create index if not exists events_user on events (instance, user_id, at);
create table if not exists state (instance text not null, key text not null, value text not null,
    primary key (instance, key));
create table if not exists suppressions (instance text not null, user_id integer not null, kind text not null,
    until real, reason text, actor text, at real not null, primary key (instance, user_id, kind));
create table if not exists settings (key text primary key, value text not null);
create table if not exists templates (id text primary key, subject text not null, text text not null,
    html text not null, updated_at real not null, actor text);
"""


class Store:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "notify.db"
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as c:
            c.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- history ----
    def add(self, instance: str, kind: str, status: str, trigger: str, *, level: int | None = None,
            user_id: int | None = None, email: str | None = None, actor: str | None = None,
            subject: str | None = None, detail: dict | None = None, error: str | None = None,
            ref: int | None = None, at: float | None = None) -> int:
        with self._lock, self._db() as c:
            cur = c.execute(
                "insert into events (at, instance, kind, level, user_id, email, status, trigger, actor, subject,"
                " detail, error, ref) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (at or time.time(), instance, kind, level, user_id, email, status, trigger, actor, subject,
                 json.dumps(detail, sort_keys=True) if detail else None, error, ref))
            return int(cur.lastrowid)

    def events(self, instance: str | None = None, user_id: int | None = None, limit: int = 500,
               kind: str | None = None, status: str | None = None) -> list[dict]:
        q, args = "select * from events where 1=1", []
        for col, v in (("instance", instance), ("user_id", user_id), ("kind", kind), ("status", status)):
            if v is not None:
                q += f" and {col} = ?"
                args.append(v)
        q += " order by id desc limit ?"
        args.append(max(1, min(limit, 5000)))
        with self._lock, self._db() as c:
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows

    def event(self, event_id: int) -> dict | None:
        with self._lock, self._db() as c:
            r = c.execute("select * from events where id = ?", (event_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        return d

    def counts(self, since: float) -> dict[str, dict[str, int]]:
        """instance -> status -> count, since a time."""
        out: dict[str, dict[str, int]] = {}
        with self._lock, self._db() as c:
            for inst, st, n in c.execute("select instance, status, count(*) from events where at >= ? "
                                         "group by 1, 2", (since,)):
                out.setdefault(inst, {})[st] = n
        return out

    # ---- per-instance state (cursors, who was reminded) ----
    def get_state(self, instance: str, key: str, default=None):
        with self._lock, self._db() as c:
            r = c.execute("select value from state where instance = ? and key = ?", (instance, key)).fetchone()
        return json.loads(r[0]) if r else default

    def set_state(self, instance: str, key: str, value) -> None:
        with self._lock, self._db() as c:
            c.execute("insert into state (instance, key, value) values (?, ?, ?) on conflict (instance, key) "
                      "do update set value = excluded.value", (instance, key, json.dumps(value)))

    # ---- suppressions ----
    def suppress(self, instance: str, user_id: int, kind: str, until: float | None, reason: str, actor: str):
        with self._lock, self._db() as c:
            c.execute("insert into suppressions (instance, user_id, kind, until, reason, actor, at) values "
                      "(?, ?, ?, ?, ?, ?, ?) on conflict (instance, user_id, kind) do update set until = "
                      "excluded.until, reason = excluded.reason, actor = excluded.actor, at = excluded.at",
                      (instance, user_id, kind, until, reason, actor, time.time()))

    def unsuppress(self, instance: str, user_id: int, kind: str) -> bool:
        with self._lock, self._db() as c:
            return c.execute("delete from suppressions where instance = ? and user_id = ? and kind = ?",
                             (instance, user_id, kind)).rowcount > 0

    def suppressions(self, instance: str | None = None) -> list[dict]:
        q, args = "select * from suppressions", []
        if instance:
            q += " where instance = ?"
            args.append(instance)
        with self._lock, self._db() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def suppressed(self, instance: str, user_id: int, kind: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        with self._lock, self._db() as c:
            r = c.execute("select * from suppressions where instance = ? and user_id = ? and kind in (?, '*') "
                          "and (until is null or until > ?)", (instance, user_id, kind, now)).fetchone()
        return dict(r) if r else None

    # ---- settings and templates ----
    def setting(self, key: str, default=None):
        with self._lock, self._db() as c:
            r = c.execute("select value from settings where key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, key: str, value) -> None:
        with self._lock, self._db() as c:
            c.execute("insert into settings (key, value) values (?, ?) on conflict (key) do update set "
                      "value = excluded.value", (key, json.dumps(value)))

    def template(self, tid: str) -> dict | None:
        with self._lock, self._db() as c:
            r = c.execute("select * from templates where id = ?", (tid,)).fetchone()
        return dict(r) if r else None

    def templates(self) -> dict[str, dict]:
        with self._lock, self._db() as c:
            return {r["id"]: dict(r) for r in c.execute("select * from templates").fetchall()}

    def save_template(self, tid: str, subject: str, text: str, html: str, actor: str) -> None:
        with self._lock, self._db() as c:
            c.execute("insert into templates (id, subject, text, html, updated_at, actor) values (?, ?, ?, ?, ?, ?) "
                      "on conflict (id) do update set subject = excluded.subject, text = excluded.text, "
                      "html = excluded.html, updated_at = excluded.updated_at, actor = excluded.actor",
                      (tid, subject, text, html, time.time(), actor))

    def delete_template(self, tid: str) -> bool:
        with self._lock, self._db() as c:
            return c.execute("delete from templates where id = ?", (tid,)).rowcount > 0
