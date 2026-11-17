"""What the notifier reads from museum's database: account activity, album shares and
additions to shared albums. Read-only, through the postgres source's role; emails are
decrypted with museum's key.encryption like the rest of the dashboard does.

`DemoReader` gives synthetic data for demo mode.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from ..db import Features


class PgReader:
    def __init__(self, source):
        self.src = source
        self.features = Features()

    @contextmanager
    def cursor(self):
        self.features = Features()
        with self.src.connect() as conn, conn.cursor() as cur:
            yield cur

    def email(self, enc, nonce) -> str | None:
        return self.src.email(enc, nonce)

    def activity(self, cur) -> list[tuple[int, str | None, int]]:
        """(user id, email, last activity in µs): the newest of any device's last API call
        (background backup counts), last upload and account creation."""
        cur.execute("""
            select u.user_id, u.encrypted_email, u.email_decryption_nonce,
                   greatest(u.creation_time, coalesce(t.last_used, 0), coalesce(f.last_upload, 0))
            from users u
            left join (select user_id, max(last_used_at) last_used from tokens
                       where not coalesce(is_deleted, false) group by user_id) t on t.user_id = u.user_id
            left join (select owner_id, max(updation_time) last_upload from files
                       group by owner_id) f on f.owner_id = u.user_id
            where u.encrypted_email is not null""")
        return [(uid, self.email(enc, nonce), int(last)) for uid, enc, nonce, last in cur.fetchall()]

    def shares(self, cur) -> dict[str, dict] | None:
        """Current album shares, "collection:from:to" -> details. None: not readable."""
        rows = self.features.run(cur, "sharing", """
            select s.collection_id, s.from_user_id, s.to_user_id, s.role_type::text,
                   f.encrypted_email, f.email_decryption_nonce, t.encrypted_email, t.email_decryption_nonce
            from collection_shares s
            join users f on f.user_id = s.from_user_id
            join users t on t.user_id = s.to_user_id
            where not coalesce(s.is_deleted, false)""")
        if rows is None:
            return None
        return {f"{cid}:{frm}:{to}": {"to": to, "role": role, "from_email": self.email(fe, fn),
                                      "to_email": self.email(te, tn)}
                for cid, frm, to, role, fe, fn, te, tn in rows}

    def additions(self, cur, since: int, until: int) -> list[tuple[int, int, int]] | None:
        """(album, who added, how many) added to shared albums in (since, until]; the file's
        owner is who added it (museum leaves action_user empty)."""
        return self.features.run(cur, "uploads", """
            select cf.collection_id, cf.f_owner_id, count(*)
            from collection_files cf
            where cf.created_at > %s and cf.created_at <= %s and not coalesce(cf.is_deleted, false)
              and cf.collection_id in (select collection_id from collection_shares
                                       where not coalesce(is_deleted, false))
            group by 1, 2""", (since, until))

    def members(self, cur, cids: list[int]) -> tuple[dict[int, set[int]], dict[int, int]]:
        """Album -> everyone in it (owner + people it's shared with), and album -> owner."""
        cur.execute("""
            select c.collection_id, c.owner_id, true from collections c where c.collection_id = any(%s)
            union all
            select s.collection_id, s.to_user_id, false from collection_shares s
            where s.collection_id = any(%s) and not coalesce(s.is_deleted, false)""", (cids, cids))
        members: dict[int, set[int]] = {}
        owners: dict[int, int] = {}
        for cid, uid, is_owner in cur.fetchall():
            members.setdefault(cid, set()).add(uid)
            if is_owner:
                owners[cid] = uid
        return members, owners

    def emails(self, cur, uids: list[int]) -> dict[int, str]:
        cur.execute("select user_id, encrypted_email, email_decryption_nonce from users where user_id = any(%s)",
                    (sorted(uids),))
        return {uid: e for uid, enc, nonce in cur.fetchall() if (e := self.email(enc, nonce))}


class DemoReader:
    """Synthetic accounts for demo mode: some active, some due for a reminder."""

    def __init__(self, source):
        self.src = source
        self.features = Features()

    @contextmanager
    def cursor(self):
        yield None

    def activity(self, cur):
        now = time.time()
        out = []
        for a in self.src.snapshot().accounts:
            idle_days = (a.id * 7) % 41  # stable spread from 0 to 40 days
            out.append((a.id, a.email, int((now - idle_days * 86400 - 3600) * 1e6)))
        return out

    def shares(self, cur):
        return {}

    def additions(self, cur, since, until):
        return []

    def members(self, cur, cids):
        return {}, {}

    def emails(self, cur, uids):
        return {a.id: a.email for a in self.src.snapshot().accounts if a.id in uids}
