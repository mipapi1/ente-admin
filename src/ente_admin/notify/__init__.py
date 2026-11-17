"""Email notifications self-hosted Ente doesn't send itself (docs/NOTIFICATIONS.md):

- **inactivity**: backup reminders when an account hasn't been used for N days (15 and 30
  by default), once per threshold per absence; using the account again resets them. Someone
  past several thresholds at once only gets the highest one.
- **share**: when someone adds a person to an album, that person gets an email.
- **uploads**: when someone adds photos to a shared album, everyone else in it gets one
  email per album per day.

Each instance runs in one mode: `off`, `shadow` (everything is worked out and recorded,
nothing is sent: for trying it out, or while another tool still sends) or `send`. Every
email, sent or not, is recorded in notify.db with its time, recipient, threshold and
result. Reminders go out only during the send window (local time of the process).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

from ..config import NOTIFY_MODES, smtp_for
from .mailer import Mailer, MailError
from .reader import DemoReader, PgReader
from .store import Store
from .templates import BUILTIN, ID_RE, Templates

log = logging.getLogger("ente_admin.notify")

KINDS = ("inactivity", "share", "uploads")
DEFAULTS = {"thresholds": [15, 30], "window": {"from": 10, "until": 20},
            "kinds": {"inactivity": True, "share": True, "uploads": True}}
UPLOAD_LAG_S = 60  # read additions at least this old, so slow transactions aren't missed
FAIL_LOG_S = 3600  # record a repeating failure at most once an hour (it's retried every round)
RIGHTS = {"VIEWER": "view it", "COLLABORATOR": "view it and add photos",
          "ADMIN": "view it, add photos and manage it", "OWNER": "manage it"}


class NotifyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def names(emails: list[str]) -> str:
    if not emails:
        return "Someone"
    return emails[0] if len(emails) == 1 else ", ".join(emails[:-1]) + " and " + emails[-1]


def _day(us: int | float | None) -> str:
    return datetime.fromtimestamp(us / 1e6).date().isoformat() if us else "—"


class Notifier:
    def __init__(self, cfg, dash):
        self.cfg, self.dash = cfg, dash
        self.store = Store(cfg.state_dir)
        self.templates = Templates(self.store, cfg.templates_dir)
        self.instances = {i.name: i for i in cfg.instances}
        self.sources = {s.name: s for s in dash.sources}
        self.mailers = {i.name: Mailer(smtp_for(cfg, i)) for i in cfg.instances}
        self.last_run: dict[str, dict] = {}
        self._fail_logged: dict[str, float] = {}
        self._upcoming: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()  # one round, or one manual send, at a time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- settings (portal-edited values over the config defaults) ----
    def mode(self, inst: str) -> str:
        m = self.store.setting(f"mode:{inst}")
        return m if m in NOTIFY_MODES else self.instances[inst].notify

    def thresholds(self) -> list[int]:
        return sorted(self.store.setting("thresholds", DEFAULTS["thresholds"]))

    def window(self) -> dict:
        return self.store.setting("window", DEFAULTS["window"])

    def kinds(self) -> dict:
        return {**DEFAULTS["kinds"], **self.store.setting("kinds", {})}

    def server_name(self, inst: str) -> str:
        return self.store.setting(f"server_name:{inst}") or inst

    def in_window(self, now: float | None = None) -> bool:
        w, hour = self.window(), datetime.fromtimestamp(now or time.time()).hour
        return w["from"] <= hour < w["until"] if w["from"] <= w["until"] else hour >= w["from"] or hour < w["until"]

    def settings(self) -> dict:
        return {"thresholds": self.thresholds(), "window": self.window(), "kinds": self.kinds(),
                "interval": self.cfg.notify_interval,
                "instances": {n: {"mode": self.mode(n), "config_mode": i.notify, "server_name": self.server_name(n),
                                  "source": i.source, "smtp": self.mailers[n].public(),
                                  "supported": i.source in ("postgres", "demo")}
                              for n, i in self.instances.items()}}

    # ---- the loop ----
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="notifier")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(5)  # let the first dashboard refresh finish
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop must keep running
                log.exception("notifier round")
            self._stop.wait(self.cfg.notify_interval)

    def _reader(self, inst: str):
        src = self.sources.get(inst)
        if src is None or src.kind not in ("postgres", "demo"):
            return None
        return DemoReader(src) if src.kind == "demo" else PgReader(src)

    def run_once(self, now: float | None = None) -> None:
        for name in self.instances:
            mode = self.mode(name)
            if mode == "off":
                self.last_run.pop(name, None)
                continue
            reader = self._reader(name)
            if not reader:
                continue
            t0 = time.time()
            try:
                with self._lock, reader.cursor() as cur:
                    kinds = self.kinds()
                    if kinds["inactivity"]:
                        self._inactivity(name, mode, reader, cur, now)
                    if kinds["share"]:
                        self._shares(name, mode, reader, cur)
                    if kinds["uploads"]:
                        self._uploads(name, mode, reader, cur, now)
                self.last_run[name] = {"at": t0, "ok": True, "ms": int((time.time() - t0) * 1000),
                                       "features": reader.features.public()}
            except Exception as e:  # noqa: BLE001 - reported in the UI, retried next round
                log.warning("notifications %s: %s: %s", name, type(e).__name__, e)
                self.last_run[name] = {"at": t0, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}

    # ---- delivery ----
    def _values(self, inst: str, email: str | None, extra: dict) -> dict:
        mailer = self.mailers[inst]
        return {"email": email or "", "name": (email or "").split("@")[0], "server_name": self.server_name(inst),
                "app_url": self.instances[inst].apps.get("photos", ""),
                "sender_name": mailer.s.get("from_name") or self.server_name(inst), **extra}

    def deliver(self, inst: str, kind: str, *, user_id: int | None, email: str | None, values: dict,
                trigger: str, mode: str, actor: str | None = None, level: int | None = None,
                to: str | None = None, ref: int | None = None, tid: str | None = None) -> dict:
        """Render and send (or, in shadow mode, only record) one email. `to` sends to another
        address instead of the account's (tests). Returns the history row."""
        last = kind == "inactivity" and level == max(self.thresholds(), default=level)
        if tid:
            t, _ = self.templates.source(tid)
            if not t:
                raise NotifyError(404, "unknown template")
        else:
            tid, t, _ = self.templates.resolve(kind, level, last)
        vals = self._values(inst, email, values)
        r = self.templates.render(t, vals)
        recipient = to or email
        detail = {k: v for k, v in values.items() if k not in ("email",)}
        detail["template"] = tid
        base = {"level": level, "user_id": user_id, "email": recipient, "actor": actor, "subject": r.subject,
                "detail": detail, "ref": ref}
        if not recipient:
            eid = self.store.add(inst, kind, "failed", trigger, error="no email address for this account", **base)
            return self.store.event(eid)
        if mode == "shadow" and not to:
            eid = self.store.add(inst, kind, "shadow", trigger, **base)
            return self.store.event(eid)
        mailer = self.mailers[inst]
        try:
            mailer.send(r.message(mailer.sender, recipient))
        except MailError as e:
            key = f"{inst}|{kind}|{user_id}|{level}|{trigger}"
            if trigger != "auto" or time.time() - self._fail_logged.get(key, 0) > FAIL_LOG_S:
                self._fail_logged[key] = time.time()
                eid = self.store.add(inst, kind, "failed", trigger, error=str(e)[:500], **base)
                ev = self.store.event(eid)
            else:
                ev = {"status": "failed", "error": str(e)}
            log.warning("notifications %s: %s email to user %s failed: %s", inst, kind, user_id, e)
            return ev
        eid = self.store.add(inst, kind, "sent", trigger, **base)
        log.info("notifications %s: %s email sent to user %s (%s)", inst, kind, user_id, trigger)
        return self.store.event(eid)

    # ---- inactivity reminders ----
    def _inactivity(self, inst, mode, reader, cur, now=None) -> None:
        now_s = now or time.time()
        now_us = now_s * 1e6
        rows = reader.activity(cur)
        rem = self.store.get_state(inst, "reminders", {})
        thresholds = self.thresholds()
        window = self.in_window(now_s)
        changed = False
        for uid, email, last in rows:
            key = str(uid)
            rec = rem.get(key)
            if rec is None or rec["since"] != last:
                rec = rem[key] = {"since": last, "sent": []}  # new account, or used again
                changed = True
            days = int((now_us - last) // 86400e6)
            due = [d for d in thresholds if days >= d and d not in rec["sent"]]
            if not due or not window:
                continue
            level = max(due)
            if self.store.suppressed(inst, uid, "inactivity", now_s):
                ev = {"status": "suppressed"}
                self.store.add(inst, "inactivity", "suppressed", "auto", level=level, user_id=uid, email=email,
                               detail={"days": level, "days_inactive": days})
            else:
                ev = self.deliver(inst, "inactivity", user_id=uid, email=email, trigger="auto", mode=mode,
                                  level=level, values={"days": level, "days_inactive": days,
                                                       "last_active": _day(last)})
            if ev["status"] in ("sent", "shadow", "suppressed"):
                rec["sent"] = sorted(set(rec["sent"]) | {d for d in thresholds if d <= level})
                changed = True
        for key in set(rem) - {str(r[0]) for r in rows}:  # deleted accounts
            del rem[key]
            changed = True
        if changed:
            self.store.set_state(inst, "reminders", rem)
        self._upcoming.pop(inst, None)

    def upcoming(self, inst: str, force: bool = False) -> list[dict]:
        """Per account: last activity, the reminders already handled this absence, and the
        next one with its due date. Cached for a minute."""
        cached = self._upcoming.get(inst)
        if cached and not force and time.time() - cached[0] < 60:
            return cached[1]
        reader = self._reader(inst)
        if not reader:
            return []
        with reader.cursor() as cur:
            rows = reader.activity(cur)
        rem = self.store.get_state(inst, "reminders", {})
        sup = {s["user_id"]: s for s in self.store.suppressions(inst) if s["kind"] in ("inactivity", "*")
               and (s["until"] is None or s["until"] > time.time())}
        now_us, thresholds = time.time() * 1e6, self.thresholds()
        out = []
        for uid, email, last in rows:
            rec = rem.get(str(uid))
            sent = rec["sent"] if rec and rec["since"] == last else []
            days = (now_us - last) / 86400e6
            nxt = next((d for d in thresholds if d not in sent and d > max(sent, default=0)), None)
            due_at = (last / 1e6 + nxt * 86400) if nxt else None
            if uid in sup:
                state = "suppressed"
            elif nxt is None:
                state = "done"
            elif due_at <= time.time():
                state = "due"
            else:
                state = "scheduled"
            out.append({"user_id": uid, "email": email, "last_active": last / 1e6, "days_inactive": round(days, 1),
                        "sent": sent, "next": nxt, "due_at": due_at, "state": state,
                        "suppressed": sup.get(uid)})
        out.sort(key=lambda r: (r["due_at"] is None, r["due_at"] or 0))
        self._upcoming[inst] = (time.time(), out)
        return out

    # ---- album shares ----
    def _shares(self, inst, mode, reader, cur) -> None:
        shares = reader.shares(cur)
        if shares is None:
            return
        seen = self.store.get_state(inst, "shares")
        if seen is None:  # first look: existing shares are old news
            self.store.set_state(inst, "shares", sorted(shares))
            log.info("notifications %s: %d existing share(s), not notifying them", inst, len(shares))
            return
        seen_set = set(seen)
        for key in sorted(set(shares) - seen_set):
            s = shares[key]
            to = s["to"]
            if self.store.suppressed(inst, to, "share"):
                self.store.add(inst, "share", "suppressed", "auto", user_id=to, email=s["to_email"],
                               detail={"sharer": s["from_email"], "role": s["role"]})
                seen_set.add(key)
                continue
            ev = self.deliver(inst, "share", user_id=to, email=s["to_email"], trigger="auto", mode=mode,
                              values={"sharer": s["from_email"] or "Someone",
                                      "rights": RIGHTS.get(s["role"], "open it")})
            if ev["status"] in ("sent", "shadow"):
                seen_set.add(key)
        self.store.set_state(inst, "shares", sorted(seen_set & set(shares)))  # removed: sharing again notifies

    # ---- new photos in shared albums ----
    def _uploads(self, inst, mode, reader, cur, now=None) -> None:
        now_us = int((now or time.time()) * 1e6)
        until = now_us - UPLOAD_LAG_S * 1_000_000
        st = self.store.get_state(inst, "uploads")
        if st is None:
            self.store.set_state(inst, "uploads", {"since": until, "pending": {}, "sent": {}})
            return
        today = date.fromtimestamp(now_us / 1e6).isoformat()
        st["sent"] = {k: d for k, d in st["sent"].items() if d == today}
        adds = reader.additions(cur, st["since"], until)
        if adds is None:
            return
        st["since"] = until
        if adds:
            members, _ = reader.members(cur, sorted({a[0] for a in adds}))
            for cid, adder, count in adds:
                for uid in members.get(cid, ()):
                    key = f"{uid}:{cid}"
                    if uid == adder or key in st["sent"]:
                        continue
                    p = st["pending"].setdefault(key, {"count": 0, "adders": []})
                    p["count"] += count
                    if adder not in p["adders"]:
                        p["adders"].append(adder)
        if st["pending"]:
            due = dict(st["pending"])
            cids = sorted({int(k.split(":")[1]) for k in due})
            _, owners = reader.members(cur, cids)
            ids = {int(k.split(":")[0]) for k in due} | {a for p in due.values() for a in p["adders"]} | \
                set(owners.values())
            emails = reader.emails(cur, sorted(ids))
            for key, p in due.items():
                uid, cid = (int(x) for x in key.split(":"))
                owner = owners.get(cid)
                if uid not in emails or owner is None:
                    del st["pending"][key]
                    continue
                album = "one of your shared albums" if owner == uid else \
                    f"an album {emails.get(owner, 'someone')} shared with you"
                n = p["count"]
                values = {"adders": names([emails[a] for a in p["adders"] if a in emails]), "count": n,
                          "count_text": f"{n} new photo{'s' if n != 1 else ''}", "album": album}
                if self.store.suppressed(inst, uid, "uploads"):
                    self.store.add(inst, "uploads", "suppressed", "auto", user_id=uid, email=emails[uid],
                                   detail=values)
                    ev = {"status": "suppressed"}
                else:
                    ev = self.deliver(inst, "uploads", user_id=uid, email=emails[uid], trigger="auto", mode=mode,
                                      values=values)
                if ev["status"] in ("sent", "shadow", "suppressed"):
                    del st["pending"][key]
                    st["sent"][key] = today
        self.store.set_state(inst, "uploads", st)

    # ---- manual actions (portal) ----
    def _account(self, inst: str, uid: int) -> tuple[str | None, int | None]:
        """The account's email and last activity (µs), read now."""
        reader = self._reader(inst)
        if not reader:
            raise NotifyError(409, "notifications need the postgres source for this instance")
        with reader.cursor() as cur:
            for row_uid, email, last in reader.activity(cur):
                if row_uid == uid:
                    return email, last
        raise NotifyError(404, "unknown account")

    def _check(self, inst: str) -> None:
        if inst not in self.instances:
            raise NotifyError(404, "unknown instance")

    def reminder_values(self, last_us: int | None, level: int) -> dict:
        days = int((time.time() * 1e6 - last_us) // 86400e6) if last_us else level
        return {"days": level, "days_inactive": days, "last_active": _day(last_us)}

    def send_now(self, inst: str, uid: int, level: int, actor: str) -> dict:
        """Send a reminder for one threshold now, outside the schedule (counts as that
        threshold's reminder for this absence)."""
        self._check(inst)
        if level not in self.thresholds():
            raise NotifyError(400, "not one of the reminder thresholds")
        email, last = self._account(inst, uid)
        with self._lock:
            ev = self.deliver(inst, "inactivity", user_id=uid, email=email, trigger="manual", mode=self.mode(inst),
                              actor=actor, level=level, values=self.reminder_values(last, level))
            if ev["status"] in ("sent", "shadow"):
                rem = self.store.get_state(inst, "reminders", {})
                rec = rem.get(str(uid))
                if not rec or rec["since"] != last:  # not seen by a round yet
                    rec = rem[str(uid)] = {"since": last, "sent": []}
                rec["sent"] = sorted(set(rec["sent"]) | {level})
                self.store.set_state(inst, "reminders", rem)
        self._upcoming.pop(inst, None)
        return ev

    def resend(self, event_id: int, actor: str) -> dict:
        ev = self.store.event(event_id)
        if not ev:
            raise NotifyError(404, "unknown notification")
        inst = ev["instance"]
        self._check(inst)
        if ev["trigger"] == "test":
            raise NotifyError(400, "test emails can't be resent; send a new test")
        email = ev["email"]
        if ev["user_id"]:
            email, _ = self._account(inst, ev["user_id"])  # the account's current address
        values = {k: v for k, v in (ev["detail"] or {}).items() if k != "template"}
        with self._lock:
            return self.deliver(inst, ev["kind"], user_id=ev["user_id"], email=email, values=values,
                                trigger="resend", mode=self.mode(inst), actor=actor, level=ev["level"],
                                ref=event_id, tid=(ev["detail"] or {}).get("template"))

    def sample_values(self, inst: str, kind: str, level: int | None, uid: int | None) -> tuple[str | None, dict]:
        email, last = (None, None)
        if uid:
            email, last = self._account(inst, uid)
        if kind == "inactivity":
            level = level or self.thresholds()[0]
            return email, self.reminder_values(last or int((time.time() - level * 86400) * 1e6), level)
        if kind == "share":
            return email, {"sharer": "someone@example.com", "rights": RIGHTS["COLLABORATOR"]}
        if kind == "uploads":
            return email, {"adders": "someone@example.com", "count": 5, "count_text": "5 new photos",
                           "album": "one of your shared albums"}
        return email, {}

    def preview(self, inst: str, tid: str, uid: int | None = None, level: int | None = None,
                draft: dict | None = None) -> dict:
        self._check(inst)
        if not ID_RE.match(tid):
            raise NotifyError(400, "unknown template id")
        kind = tid.split("-")[0]
        if kind == "inactivity" and level is None:
            level = int(tid[11:]) if tid[11:].isdigit() else max(self.thresholds()) if tid == "inactivity-last" \
                else None
        t = draft or self.templates.source(tid)[0] or BUILTIN.get(kind)
        email, values = self.sample_values(inst, kind, level, uid)
        r = self.templates.render(t, self._values(inst, email or "someone@example.com", values))
        return {"subject": r.subject, "text": r.text, "html": r.preview_html(),
                "images": sorted(r.images), "to": email or "someone@example.com"}

    def send_test(self, inst: str, tid: str, to: str, actor: str, uid: int | None = None,
                  level: int | None = None) -> dict:
        """Send one template to any address (yours), with an account's data or sample data.
        Works in every mode: it never goes to the account."""
        self._check(inst)
        if not ID_RE.match(tid) or not self.templates.source(tid)[0]:
            raise NotifyError(400, "unknown template")
        kind = tid.split("-")[0]
        if kind == "inactivity" and level is None:
            level = int(tid[11:]) if tid[11:].isdigit() else max(self.thresholds()) if tid == "inactivity-last" \
                else None
        email, values = self.sample_values(inst, kind, level, uid)
        with self._lock:
            return self.deliver(inst, kind, user_id=uid, email=email, values=values, trigger="test", mode="send",
                                actor=actor, level=level if kind == "inactivity" else None, to=to, tid=tid)

    def import_legacy(self, inst: str, data: dict, actor: str) -> dict:
        """Take over from ente-notify (the script this module replaces): its
        notified.json says who was already reminded, which shares were already announced and
        where it stopped reading additions, so nothing is sent twice."""
        self._check(inst)
        if not isinstance(data, dict):
            raise NotifyError(400, "expected ente-notify's notified.json")
        shares = (data.get("shares") or {}).get(inst) if "shares" in data else data.get(inst)
        reminders = (data.get("reminders") or {}).get(inst)
        uploads = (data.get("uploads") or {}).get(inst)
        out = {}
        if isinstance(shares, list):
            self.store.set_state(inst, "shares", sorted(set(map(str, shares))))
            out["shares"] = len(shares)
        if isinstance(reminders, dict):
            clean = {str(k): {"since": int(v["since"]), "sent": sorted({int(d) for d in v.get("sent", [])})}
                     for k, v in reminders.items() if isinstance(v, dict) and "since" in v}
            self.store.set_state(inst, "reminders", clean)
            out["reminders"] = len(clean)
        if isinstance(uploads, dict) and "since" in uploads:
            self.store.set_state(inst, "uploads", {"since": int(uploads["since"]),
                                                   "pending": uploads.get("pending") or {},
                                                   "sent": uploads.get("sent") or {}})
            out["uploads"] = True
        if not out:
            raise NotifyError(400, f"nothing for instance {inst!r} in that file")
        self._upcoming.pop(inst, None)
        return out

    # ---- overview ----
    def overview(self) -> dict:
        day = time.time() - 86400
        week = time.time() - 7 * 86400
        c1, c7 = self.store.counts(day), self.store.counts(week)
        out = {}
        for name in self.instances:
            ups = []
            try:
                if self.mode(name) != "off":
                    ups = self.upcoming(name)
            except Exception as e:  # noqa: BLE001 - shown in the UI
                log.warning("notifications %s: upcoming: %s", name, e)
            out[name] = {"mode": self.mode(name), "last_run": self.last_run.get(name),
                         "last_24h": c1.get(name, {}), "last_7d": c7.get(name, {}),
                         "due": sum(1 for u in ups if u["state"] == "due"),
                         "scheduled_7d": sum(1 for u in ups if u["state"] == "scheduled"
                                             and u["due_at"] - time.time() < 7 * 86400),
                         "suppressed": sum(1 for u in ups if u["state"] == "suppressed")}
        return out
