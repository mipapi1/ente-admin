"""Unlocked admin sessions and logins in progress, in memory only.

A session is one dashboard user's museum token for one instance. It ends after
`idle_s` without use, after `max_s` in total, on Lock, or when the process stops; ending
it signs the token out of museum. Nothing here is written to disk, and tokens never
leave this module except to call museum.

Museum accepts some admin calls (list users, delete user, disable 2FA) only with a token
created less than 10 minutes ago: `fresh()` tells whether a session still qualifies.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

FRESH_S = 10 * 60 - 30  # museum's limit, minus a margin for clock skew and slow calls
PENDING_S = 10 * 60  # museum's codes are valid for 10 minutes


@dataclass
class Session:
    token: str = field(repr=False)
    admin_id: int
    email: str
    created: float
    last_used: float

    def fresh(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.created < FRESH_S


@dataclass
class Pending:
    """A login between "code sent" and "token opened"."""
    email: str
    created: float
    attempts: int = 0
    verified: dict | None = field(default=None, repr=False)  # museum's verify response (no secrets in clear)
    totp_session: str = field(default="", repr=False)


class Sessions:
    def __init__(self, idle_s: float, max_s: float, on_end: Callable[[str, str], None]):
        self.idle_s, self.max_s = idle_s, max_s
        self.on_end = on_end  # (instance, token) -> sign out; called outside the lock
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], Session] = {}
        self._pending: dict[tuple[str, str], Pending] = {}

    def _expired(self, s: Session, now: float) -> bool:
        return now - s.last_used > self.idle_s or now - s.created > self.max_s

    def start(self, user: str, instance: str, token: str, admin_id: int, email: str) -> Session:
        now = time.time()
        with self._lock:
            old = self._sessions.get((user, instance))
            s = self._sessions[(user, instance)] = Session(token, admin_id, email, now, now)
            self._pending.pop((user, instance), None)
        if old:
            self.on_end(instance, old.token)
        return s

    def get(self, user: str, instance: str, touch: bool = True) -> Session | None:
        now = time.time()
        with self._lock:
            s = self._sessions.get((user, instance))
            if s and self._expired(s, now):
                del self._sessions[(user, instance)]
                ended, s = s, None
            else:
                ended = None
                if s and touch:
                    s.last_used = now
        if ended:
            self.on_end(instance, ended.token)
        return s

    def end(self, user: str, instance: str) -> bool:
        with self._lock:
            s = self._sessions.pop((user, instance), None)
            self._pending.pop((user, instance), None)
        if s:
            self.on_end(instance, s.token)
        return bool(s)

    def reap(self) -> int:
        now = time.time()
        with self._lock:
            gone = [(k, s) for k, s in self._sessions.items() if self._expired(s, now)]
            for k, _ in gone:
                del self._sessions[k]
            for k in [k for k, p in self._pending.items() if now - p.created > PENDING_S]:
                del self._pending[k]
        for (_, instance), s in gone:
            self.on_end(instance, s.token)
        return len(gone)

    def end_all(self, on_end: Callable[[str, str], None] | None = None) -> None:
        with self._lock:
            items, self._sessions = list(self._sessions.items()), {}
            self._pending.clear()
        for (_, instance), s in items:
            (on_end or self.on_end)(instance, s.token)

    def status(self, user: str, instance: str) -> dict:
        s = self.get(user, instance, touch=False)
        p = self.pending(user, instance)
        now = time.time()
        if not s:
            return {"unlocked": False, "pending": p.email if p else None,
                    "step": ("totp" if p.totp_session else "code") if p else None}
        return {"unlocked": True, "email": s.email,
                "idle_left": max(0, int(self.idle_s - (now - s.last_used))),
                "left": max(0, int(min(self.idle_s - (now - s.last_used), self.max_s - (now - s.created)))),
                "fresh_left": max(0, int(FRESH_S - (now - s.created)))}

    # ---- logins in progress ----
    def begin(self, user: str, instance: str, email: str) -> Pending:
        with self._lock:
            p = self._pending[(user, instance)] = Pending(email=email, created=time.time())
            return p

    def pending(self, user: str, instance: str) -> Pending | None:
        with self._lock:
            p = self._pending.get((user, instance))
            if p and time.time() - p.created > PENDING_S:
                del self._pending[(user, instance)]
                p = None
            return p

    def drop_pending(self, user: str, instance: str) -> None:
        with self._lock:
            self._pending.pop((user, instance), None)


class RateLimiter:
    """At most `limit` events per `window_s` per key (sliding window)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
