"""Direct, read-only access to museum's Postgres. Off unless an instance asks for it
(SOURCE=postgres). Gives what the API doesn't: albums, trash, uploads per day, and with
the optional grants Locker usage, public links, sharing and inheritance settings.

Use a role that can only read the columns in docs/POSTGRES.md. It never reads session
tokens, keys, link tokens or file metadata beyond size and timestamps. Optional parts the
role can't read are reported as unavailable (db.Features), not as errors. Emails are
stored encrypted; they're decrypted only if EMAIL_KEY_FILE (museum's key.encryption) is
given, otherwise accounts are shown by id. Needs the optional extras:
`pip install ente-admin[postgres]`.
"""
from __future__ import annotations

import base64
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import Instance
from ..db import PG, Features
from . import register
from .model import DAYS, LOCKER_LIMITS, Account, Device, Snapshot, device_name

ACTIVE_WINDOW_S = 5 * 60


def _sec(us):
    return int(us / 1_000_000) if us else None


@register("postgres")
class PostgresSource:
    kind = "postgres"

    def __init__(self, instance: Instance):
        self.name = instance.name
        self.url = instance.url
        self.instance = instance
        self._box = None
        if instance.email_key_file:
            import nacl.secret  # optional dependency
            key = base64.b64decode(Path(instance.email_key_file).read_text().strip())
            self._box = nacl.secret.SecretBox(key)

    def _email(self, enc, nonce) -> str | None:
        if not self._box or enc is None or nonce is None:
            return None
        try:
            return self._box.decrypt(bytes(enc), bytes(nonce)).decode()
        except Exception:  # noqa: BLE001 - a bad row must not break the page
            return None

    def email(self, enc, nonce) -> str | None:
        return self._email(enc, nonce)

    def _connect(self):
        return PG(self.instance.dsn, self.instance.password_file).connect()

    def connect(self):
        """A read-only connection, for the notifier and the Locker views."""
        return self._connect()

    def snapshot(self) -> Snapshot:
        now = time.time()
        users: dict[int, Account] = {}
        day0 = date.fromtimestamp(now).toordinal() - (DAYS - 1)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""
                select u.user_id, u.encrypted_email, u.email_decryption_nonce, u.creation_time,
                       u.is_two_factor_enabled, coalesce(u.email_mfa, false),
                       s.storage, s.expiry_time, coalesce(g.storage_consumed, 0)
                from users u
                left join subscriptions s on s.user_id = u.user_id
                left join usage g on g.user_id = u.user_id
                where u.encrypted_email is not null
                order by u.creation_time""")
            for uid, enc, nonce, created, tfa, emfa, quota, expiry, used in cur.fetchall():
                users[uid] = Account(id=uid, email=self._email(enc, nonce), created=_sec(created),
                                     two_factor=tfa, email_mfa=emfa, quota=quota, plan_expiry=_sec(expiry),
                                     used=used, files=0, file_bytes=0, albums=0, trash=0, uploading=False,
                                     recent_uploads=0, daily_uploads=[0] * DAYS, daily_bytes=[0] * DAYS)

            # info->>'fileSize' is the size museum records for the file (not encrypted metadata)
            cur.execute("""select owner_id, count(*), coalesce(sum((info->>'fileSize')::bigint), 0)::bigint,
                                  max(updation_time), count(*) filter (where updation_time > %s)
                           from files group by owner_id""", (int((now - ACTIVE_WINDOW_S) * 1e6),))
            for uid, n, size, last, recent in cur.fetchall():
                if uid in users:
                    a = users[uid]
                    a.files, a.file_bytes, a.last_upload = n, size, _sec(last)
                    a.recent_uploads, a.uploading = recent, recent > 0

            # per local day: bucket by hour in SQL, map hours to days here (the process's TZ)
            cur.execute("""select owner_id, (updation_time / 3600000000)::bigint, count(*),
                                  coalesce(sum((info->>'fileSize')::bigint), 0)::bigint
                           from files where updation_time > %s group by 1, 2""",
                        (int((now - (DAYS + 1) * 86400) * 1e6),))
            for uid, hour, n, size in cur.fetchall():
                idx = datetime.fromtimestamp(hour * 3600).date().toordinal() - day0
                if uid in users and 0 <= idx < DAYS:
                    users[uid].daily_uploads[idx] += n
                    users[uid].daily_bytes[idx] += size

            cur.execute("select owner_id, count(*) from collections where not is_deleted group by 1")
            for uid, n in cur.fetchall():
                if uid in users:
                    users[uid].albums = n
            cur.execute("select user_id, count(*) from trash where not is_deleted and not is_restored group by 1")
            for uid, n in cur.fetchall():
                if uid in users:
                    users[uid].trash = n
            cur.execute("""select user_id, app, user_agent, creation_time, last_used_at from tokens
                           where not is_deleted order by last_used_at desc""")
            for uid, app, ua, created, last in cur.fetchall():
                if uid in users:
                    users[uid].devices.append(Device(device=device_name(ua), app=str(app),
                                                     signed_in=_sec(created), last_seen=_sec(last)))
            feats = Features()
            self._optional(cur, users, feats, now)
        for a in users.values():
            a.last_seen = max((d.last_seen or 0 for d in a.devices), default=0) or None
        days = [(date.fromordinal(day0) + timedelta(days=k)).isoformat() for k in range(DAYS)]
        notes = [] if self._box else ["Emails are encrypted in the database; set EMAIL_KEY_FILE to show them."]
        return Snapshot(instance=self.name, source=self.kind, url=self.url, generated_at=int(now),
                        accounts=list(users.values()), days=days, notes=notes, features=feats.public())

    def _optional(self, cur, users: dict[int, Account], f: Features, now: float) -> None:
        """Locker, links, sharing, inheritance: each needs its own grants (db.GRANTS)."""
        def each(rows, fn):
            for row in rows or ():
                if row[0] in users:
                    fn(users[row[0]], *row[1:])

        # Locker: item count as museum keeps it (usage), size and collections like museum
        # computes them (repo/usage.go getLockerUsage)
        cur.execute("select user_id, coalesce(locker_file_count, 0) from usage")
        locker_counts = dict(cur.fetchall())
        rows = f.run(cur, "locker", """
            select c.owner_id, count(distinct c.collection_id) filter (where not c.is_deleted)
            from collections c where c.app = 'locker' group by 1""")
        if rows is not None:
            for a in users.values():
                a.locker_items, a.locker_bytes, a.locker_collections = locker_counts.get(a.id, 0), 0, 0
            each(rows, lambda a, n: setattr(a, "locker_collections", n))
            each(f.run(cur, "locker", """
                select u.owner_id, coalesce(sum(ok.size), 0)::bigint
                from (select distinct c.owner_id, cf.file_id from collections c
                      join collection_files cf on cf.collection_id = c.collection_id
                      where c.app = 'locker' and cf.f_owner_id = c.owner_id and not cf.is_deleted) u
                left join object_keys ok on ok.file_id = u.file_id and not ok.is_deleted
                group by 1"""), lambda a, n: setattr(a, "locker_bytes", n))
            self._locker_tier(cur, users, f, now)
            for a in users.values():  # the files table holds Locker items too: count them apart
                a.files = max(0, (a.files or 0) - (a.locker_items or 0))
                a.file_bytes = max(0, (a.file_bytes or 0) - (a.locker_bytes or 0))

        rows = f.run(cur, "links", """
            select c.owner_id, c.app::text, count(*) from public_collection_tokens t
            join collections c on c.collection_id = t.collection_id
            where not t.is_disabled and (t.valid_till = 0 or t.valid_till > %s) group by 1, 2""",
                     (int(now * 1e6),))
        if rows is not None:
            for a in users.values():
                a.album_links, a.locker_links = 0, 0
            for uid, app, n in rows:
                if uid in users:
                    attr = "locker_links" if app == "locker" else "album_links"
                    setattr(users[uid], attr, getattr(users[uid], attr) + n)
            each(f.run(cur, "links", """
                select owner_id, app, count(*) from public_file_tokens
                where not is_disabled and (valid_till = 0 or valid_till > %s) group by 1, 2""", (int(now * 1e6),)),
                 lambda a, app, n: setattr(a, "locker_links" if app == "locker" else "album_links",
                                           (getattr(a, "locker_links" if app == "locker" else "album_links") or 0)
                                           + n))

        rows = f.run(cur, "sharing", """
            select from_user_id, count(*) from collection_shares where not coalesce(is_deleted, false) group by 1""")
        if rows is not None:
            for a in users.values():
                a.shares_out, a.shares_in = 0, 0
            each(rows, lambda a, n: setattr(a, "shares_out", n))
            each(f.run(cur, "sharing", """select to_user_id, count(*) from collection_shares
                                          where not coalesce(is_deleted, false) group by 1"""),
                 lambda a, n: setattr(a, "shares_in", n))

        rows = f.run(cur, "legacy", """
            select user_id, count(*) from emergency_contact where state in ('INVITED', 'ACCEPTED')
            group by 1""")
        if rows is not None:
            for a in users.values():
                a.trusted_contacts, a.trusted_by, a.legacy_kits = 0, 0, 0
            each(rows, lambda a, n: setattr(a, "trusted_contacts", n))
            each(f.run(cur, "legacy", """
                select emergency_contact_id, count(*) from emergency_contact
                where state in ('INVITED', 'ACCEPTED') group by 1"""),
                 lambda a, n: setattr(a, "trusted_by", n))
            each(f.run(cur, "legacy", "select user_id, count(*) from legacy_kit where not is_deleted group by 1"),
                 lambda a, n: setattr(a, "legacy_kits", n))

    def _locker_tier(self, cur, users: dict[int, Account], f: Features, now: float) -> None:
        """Museum's rule (controller/usage.go, billing.go): on a self-hosted server (no user
        with id 1) an account gets the paid Locker limits if its plan, or its family admin's,
        hasn't expired, or it has a paid add-on; otherwise the free ones."""
        cur.execute("select exists(select 1 from users where user_id = 1)")
        ente_cloud = cur.fetchone()[0]
        now_us = int(now * 1e6)
        cur.execute("""select u.user_id, coalesce(u.family_admin_id, u.user_id),
                              s.expiry_time, s.product_id
                       from users u left join subscriptions s on s.user_id = coalesce(u.family_admin_id, u.user_id)""")
        subs = cur.fetchall()
        addons = f.run(cur, "paid_tier", """
            select user_id from storage_bonus
            where not is_revoked and (valid_till = 0 or valid_till > %s)
              and type not in ('SIGN_UP', 'REFERRAL') group by 1""", (now_us,))
        addon = {r[0] for r in addons or ()}
        for uid, payer, expiry, product in subs:
            a = users.get(uid)
            if not a:
                continue
            active = expiry is not None and expiry > now_us
            if ente_cloud:  # Ente's own servers: a real paid plan counts, the free one doesn't
                paid = (active and product != "free") or payer in addon
            else:
                paid = active or payer in addon
            a.locker_tier = "paid" if paid else "free"
            a.locker_item_limit, a.locker_byte_limit = LOCKER_LIMITS[a.locker_tier]
