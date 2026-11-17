"""The audit log: who did what, when, to which account, with the values before and after.

SQLite in the state directory (the same volume as the shares). Rows are only added,
never changed. Parameters are stored as validated by the action, and actions never
take secrets, so passwords, codes and tokens can't end up here. Unlocks and locks are
recorded too, without the code or password.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Audit:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "audit.db"
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("""create table if not exists audit (
            id integer primary key autoincrement, at real not null, actor text not null,
            instance text not null, account_id integer, account_email text, action text not null,
            params text, before text, after text, result text not null, error text)""")
        return conn

    def add(self, actor: str, instance: str, action: str, result: str, account_id: int | None = None,
            account_email: str | None = None, params: dict | None = None, before: dict | None = None,
            after: dict | None = None, error: str | None = None) -> int:
        def js(v):
            return json.dumps(v, sort_keys=True) if v is not None else None
        with self._lock, self._db() as conn:
            cur = conn.execute(
                "insert into audit (at, actor, instance, account_id, account_email, action, params, before, after,"
                " result, error) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), actor, instance, account_id, account_email, action, js(params), js(before), js(after),
                 result, error))
            return int(cur.lastrowid)

    def recent(self, limit: int = 200, instance: str | None = None) -> list[dict]:
        q = ("select id, at, actor, instance, account_id, account_email, action, params, before, after, result, error"
             " from audit")
        args: list = []
        if instance:
            q += " where instance = ?"
            args.append(instance)
        q += " order by id desc limit ?"
        args.append(max(1, min(limit, 1000)))
        with self._lock, self._db() as conn:
            rows = conn.execute(q, args).fetchall()
        keys = ("id", "at", "actor", "instance", "account_id", "account_email", "action", "params", "before", "after",
                "result", "error")
        out = []
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            for k in ("params", "before", "after"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out
