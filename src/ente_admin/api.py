"""Admin API routes for notifications, Locker and links, devices, storage, web apps,
museum.yaml advice and setup (server.py dispatches /api/admin/* here first).

Admins only. Reads need nothing more; every change is a POST that passes the same CSRF
checks as the portal, is rate limited and goes to the audit log. Changes to the database
(revoking a link, signing out a device) also need the instance unlocked with an Ente admin
sign-in, and a typed confirmation. Accounts are referred to by their dashboard label, and
emails are masked unless ENTE_ADMIN_REVEAL_EMAILS is on, like everywhere else.
"""
from __future__ import annotations

import re
import secrets
import time

from .notify import KINDS, NotifyError
from .notify.templates import ID_RE
from .ops import YAML_APPS, OpsError
from .privacy import mask_email

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
PREVIEW_TTL_S = 600


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Api:
    def __init__(self, cfg, dash, audit, notifier, ops):
        self.cfg, self.dash, self.audit, self.notifier, self.ops = cfg, dash, audit, notifier, ops
        self._previews: dict[str, tuple[float, str, dict]] = {}  # id -> (at, user, rendered)

    # ---- helpers ----
    def _email(self, e):
        return e if self.cfg.reveal_emails else mask_email(e)

    def _uid(self, inst: str, label) -> int:
        uid = self.dash.account_id(inst, str(label or ""))
        if uid is None:
            raise ApiError(404, "unknown account")
        return uid

    def _inst(self, inst: str) -> str:
        if inst not in {i.name for i in self.cfg.instances}:
            raise ApiError(404, "unknown instance")
        return inst

    def _need_notifier(self):
        if not self.notifier:
            raise ApiError(409, "notifications are off (the notifications module)")
        return self.notifier

    def _need_unlocked(self, viewer, inst: str):
        portal = self.dash.portal
        if not portal or not portal.sessions.get(viewer.user, inst):
            raise ApiError(401, "locked: unlock admin actions for this server first")

    @staticmethod
    def _confirm(req: dict, word: str) -> None:
        if str(req.get("confirm") or "").strip().lower() != word:
            raise ApiError(400, f"type “{word}” to confirm")

    def _limit(self, viewer) -> None:
        portal = self.dash.portal
        if portal and not portal.action_limit.allow(viewer.user):
            raise ApiError(429, "too many changes in a minute; wait a moment")

    def _event(self, e: dict) -> dict:
        e = dict(e)
        uid = e.pop("user_id", None)
        e["account"] = self.dash.label(uid) if uid else None
        e["email"] = self._email(e.get("email"))
        return e

    # ---- dispatch ----
    def get(self, viewer, op: str, q: dict) -> dict | None:
        inst = q.get("instance", "")
        if op == "notify/overview":
            n = self._need_notifier()
            return {"instances": n.overview(), "settings": n.settings(), "kinds": list(KINDS)}
        if op == "notify/events":
            n = self._need_notifier()
            uid = self._uid(inst, q["account"]) if q.get("account") else None
            limit = int(q["limit"]) if (q.get("limit") or "").isdigit() else 500
            rows = n.store.events(instance=inst or None, user_id=uid, kind=q.get("kind") or None,
                                  status=q.get("status") or None, limit=limit)
            return {"events": [self._event(e) for e in rows]}
        if op == "notify/upcoming":
            n = self._need_notifier()
            rows = n.upcoming(self._inst(inst), force=q.get("force") == "1")
            out = []
            for r in rows:
                r = dict(r)
                r["account"] = self.dash.label(r.pop("user_id"))
                r["email"] = self._email(r["email"])
                if r["suppressed"]:
                    r["suppressed"] = {k: r["suppressed"][k] for k in ("kind", "until", "reason", "actor", "at")}
                out.append(r)
            return {"upcoming": out, "thresholds": n.thresholds(), "in_window": n.in_window(),
                    "window": n.window(), "mode": n.mode(inst)}
        if op == "notify/templates":
            n = self._need_notifier()
            return {"templates": n.templates.catalog(), "folder": bool(self.cfg.templates_dir)}
        if op == "notify/suppressions":
            n = self._need_notifier()
            rows = n.store.suppressions(inst or None)
            for r in rows:
                r["account"] = self.dash.label(r.pop("user_id"))
            return {"suppressions": rows}
        if op == "notify/smtp":
            n = self._need_notifier()
            m = n.mailers[self._inst(inst)]
            return {"smtp": m.public(), "health": m.health(force=q.get("force") == "1")}
        if op == "links":
            data = self.ops.links(self._inst(inst))
            return data
        if op == "sessions":
            return {"sessions": self.ops.sessions(self._inst(inst), self._uid(inst, q.get("account")))}
        if op == "storage":
            return {**self.ops.storage(self._inst(inst)), "checks": self.ops.backup_checks(inst)}
        if op == "apps":
            return self.ops.apps(self._inst(inst), force=q.get("force") == "1")
        if op == "config":
            return {**self.ops.config(self._inst(inst)), "yaml_apps": YAML_APPS}
        if op == "setup":
            out = self.ops.setup(self._inst(inst))
            if self.notifier:
                n = self.notifier
                m = n.mailers[inst]
                out["items"].append({"item": "Email (SMTP)", "ok": m.configured or None,
                                     "detail": f"{m.s.get('host')}:{m.s.get('port')}" if m.configured else
                                     "optional: ENTE_ADMIN_SMTP_HOST and _FROM"})
                out["items"].append({"item": "Notifications", "ok": None if n.mode(inst) == "off" else True,
                                     "detail": f"mode: {n.mode(inst)}"})
            return out
        return None

    def preview_page(self, viewer, q: dict) -> str:
        """The HTML of a stored preview, for the preview frame."""
        hit = self._previews.get(q.get("id", ""))
        if not hit or hit[1] != viewer.user or time.time() - hit[0] > PREVIEW_TTL_S:
            raise ApiError(404, "preview expired; preview again")
        return hit[2]["html"]

    def post(self, viewer, op: str, req: dict) -> dict | None:
        inst = str(req.get("instance") or "")
        actor = viewer.user
        if op == "notify/preview":
            n = self._need_notifier()
            tid = str(req.get("template") or "")
            draft = req.get("draft")
            if draft is not None:
                if not isinstance(draft, dict) or not all(isinstance(draft.get(k, ""), str)
                                                          for k in ("subject", "text", "html")):
                    raise ApiError(400, "bad draft")
                draft = {k: draft.get(k, "") for k in ("subject", "text", "html")}
            uid = self._uid(inst, req["account"]) if req.get("account") else None
            level = int(req["level"]) if str(req.get("level") or "").isdigit() else None
            r = n.preview(self._inst(inst), tid, uid, level, draft)
            pid = secrets.token_urlsafe(16)
            now = time.time()
            self._previews = {k: v for k, v in self._previews.items() if now - v[0] < PREVIEW_TTL_S}
            self._previews[pid] = (now, viewer.user, r)
            return {"id": pid, "subject": r["subject"], "text": r["text"], "images": r["images"],
                    "to": self._email(r["to"])}
        # everything below changes something
        self._limit(viewer)
        if op == "notify/settings":
            n = self._need_notifier()
            changes = {}
            if "thresholds" in req:
                t = req["thresholds"]
                if not isinstance(t, list) or not 1 <= len(t) <= 6 or not all(isinstance(d, int) and 1 <= d <= 365
                                                                              for d in t):
                    raise ApiError(400, "thresholds: 1 to 6 whole numbers of days, 1-365")
                changes["thresholds"] = sorted(set(t))
            if "window" in req:
                w = req["window"]
                if not isinstance(w, dict) or not all(isinstance(w.get(k), int) and 0 <= w[k] <= 24
                                                      for k in ("from", "until")) or w["from"] == w["until"]:
                    raise ApiError(400, "window: from and until are hours, 0-24, not equal")
                changes["window"] = {"from": w["from"], "until": w["until"]}
            if "kinds" in req:
                k = req["kinds"]
                if not isinstance(k, dict) or set(k) - set(KINDS) or not all(isinstance(v, bool) for v in k.values()):
                    raise ApiError(400, "kinds: inactivity, share, uploads -> true/false")
                changes["kinds"] = {**n.kinds(), **k}
            if "mode" in req or "server_name" in req:
                self._inst(inst)
            if "mode" in req:
                mode = req["mode"]
                if mode not in ("off", "shadow", "send"):
                    raise ApiError(400, "mode: off, shadow or send")
                if mode == "send" and n.mode(inst) != "send":
                    self._confirm(req, "send emails")
                changes[f"mode:{inst}"] = mode
            if "server_name" in req:
                name = str(req["server_name"]).strip()
                if not 1 <= len(name) <= 60:
                    raise ApiError(400, "server name: 1-60 characters")
                changes[f"server_name:{inst}"] = name
            if not changes:
                raise ApiError(400, "nothing to change")
            before = {k: n.store.setting(k) for k in changes}
            for k, v in changes.items():
                n.store.set_setting(k, v)
            n._upcoming.clear()
            self.audit.add(actor, inst or "*", "notify-settings", "ok", params=changes, before=before)
            return {"ok": True, "settings": n.settings()}
        if op == "notify/send":
            n = self._need_notifier()
            uid = self._uid(self._inst(inst), req.get("account"))
            level = req.get("level")
            if not isinstance(level, int):
                raise ApiError(400, "which reminder (threshold)?")
            ev = n.send_now(inst, uid, level, actor)
            self.audit.add(actor, inst, "notify-send", "ok" if ev["status"] in ("sent", "shadow") else "error",
                           account_id=uid, account_email=ev.get("email"), params={"level": level},
                           after={"status": ev["status"], "event": ev.get("id")}, error=ev.get("error"))
            return {"event": self._event(ev)}
        if op == "notify/resend":
            n = self._need_notifier()
            eid = req.get("event")
            if not isinstance(eid, int):
                raise ApiError(400, "which notification?")
            ev = n.resend(eid, actor)
            self.audit.add(actor, ev.get("instance", inst), "notify-resend",
                           "ok" if ev["status"] in ("sent", "shadow") else "error", account_id=ev.get("user_id"),
                           account_email=ev.get("email"), params={"event": eid},
                           after={"status": ev["status"], "event": ev.get("id")}, error=ev.get("error"))
            return {"event": self._event(ev)}
        if op == "notify/test":
            n = self._need_notifier()
            to = str(req.get("to") or "").strip()
            if not EMAIL_RE.match(to):
                raise ApiError(400, "enter the address to send the test to")
            tid = str(req.get("template") or "test")
            uid = self._uid(inst, req["account"]) if req.get("account") else None
            level = int(req["level"]) if str(req.get("level") or "").isdigit() else None
            ev = n.send_test(self._inst(inst), tid, to, actor, uid, level)
            self.audit.add(actor, inst, "notify-test", "ok" if ev["status"] == "sent" else "error",
                           account_id=uid, account_email=to, params={"template": tid, "level": level},
                           after={"status": ev["status"], "event": ev.get("id")}, error=ev.get("error"))
            return {"event": self._event(ev)}
        if op in ("notify/suppress", "notify/unsuppress"):
            n = self._need_notifier()
            uid = self._uid(self._inst(inst), req.get("account"))
            kind = str(req.get("kind") or "*")
            if kind not in (*KINDS, "*"):
                raise ApiError(400, "kind: inactivity, share, uploads or * (all)")
            if op == "notify/unsuppress":
                ok = n.store.unsuppress(inst, uid, kind)
                self.audit.add(actor, inst, "notify-unsuppress", "ok" if ok else "error", account_id=uid,
                               params={"kind": kind}, error=None if ok else "wasn't suppressed")
                n._upcoming.pop(inst, None)
                return {"ok": ok}
            days = req.get("days")
            if days is not None and (not isinstance(days, int) or not 1 <= days <= 3650):
                raise ApiError(400, "days: 1-3650, or empty for until removed")
            reason = str(req.get("reason") or "").strip()[:200]
            until = time.time() + days * 86400 if days else None
            n.store.suppress(inst, uid, kind, until, reason, actor)
            self.audit.add(actor, inst, "notify-suppress", "ok", account_id=uid,
                           params={"kind": kind, "days": days, "reason": reason})
            n._upcoming.pop(inst, None)
            return {"ok": True}
        if op in ("notify/template", "notify/template-reset"):
            n = self._need_notifier()
            tid = str(req.get("id") or "")
            if not ID_RE.match(tid):
                raise ApiError(400, "template id: inactivity, inactivity-<days>, inactivity-last, share, uploads, test")
            before, origin = n.templates.source(tid)
            if op == "notify/template-reset":
                ok = n.store.delete_template(tid)
                self.audit.add(actor, "*", "notify-template-reset", "ok" if ok else "error", params={"id": tid},
                               before=before if origin == "edited" else None,
                               error=None if ok else "not edited in the portal")
                return {"ok": ok}
            t = {k: req.get(k) for k in ("subject", "text", "html")}
            if not all(isinstance(v, str) for v in t.values()) or not t["subject"].strip() or not t["text"].strip():
                raise ApiError(400, "a subject and a plain-text body are needed")
            if len(t["subject"]) > 250 or len(t["text"]) > 20000 or len(t["html"]) > 100000:
                raise ApiError(400, "template too long")
            try:
                n.templates.render(t, n._values(next(iter(n.instances)), "someone@example.com", {}))
            except (ValueError, KeyError) as e:
                raise ApiError(400, f"template doesn't render: {e}") from None
            n.store.save_template(tid, t["subject"].strip(), t["text"], t["html"], actor)
            self.audit.add(actor, "*", "notify-template", "ok", params={"id": tid, "subject": t["subject"].strip()},
                           before={"origin": origin, "subject": (before or {}).get("subject")})
            return {"ok": True}
        if op == "notify/import":
            n = self._need_notifier()
            out = n.import_legacy(self._inst(inst), req.get("data"), actor)
            self.audit.add(actor, inst, "notify-import", "ok", params=out)
            return {"ok": True, "imported": out}
        if op == "links/revoke":
            self._need_unlocked(viewer, self._inst(inst))
            self._confirm(req, "revoke")
            return self.ops.revoke_link(inst, str(req.get("kind") or ""), str(req.get("id") or ""), actor)
        if op == "sessions/signout":
            self._need_unlocked(viewer, self._inst(inst))
            self._confirm(req, "sign out")
            uid = self._uid(inst, req.get("account"))
            email = next((a.get("email") for s in self.dash.data() if s["instance"] == inst
                          for a in s["accounts"] if a["id"] == uid), None)
            return self.ops.sign_out(inst, uid, str(req.get("session") or ""), actor, email)
        if op == "backups/check":
            return self.ops.record_backup_check(self._inst(inst), actor, str(req.get("what") or "").strip(),
                                                str(req.get("result") or ""), str(req.get("note") or "").strip())
        if op in ("config/propose", "config/verify"):
            args = (self._inst(inst), actor, req.get("apps"), req.get("admins"), req.get("disable_registration"))
            if op == "config/propose":
                return self.ops.propose_config(*args)
            return self.ops.verify_config(*args)
        return None


ERRORS = (ApiError, NotifyError, OpsError)
