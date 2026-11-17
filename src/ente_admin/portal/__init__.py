"""The admin portal: unlock an instance with an Ente admin sign-in, then manage accounts
through museum's admin API, with previews, confirmations and an audit log.

Off unless ENTE_ADMIN_ENABLE_ACTIONS=true. Only dashboard admins (ENTE_ADMIN_ADMIN_GROUPS,
or everyone without it) can use it. See docs/ADMIN.md.
"""
from __future__ import annotations

import logging
import threading
import time

from ..sources.museum import MuseumClient, MuseumError
from . import login
from .actions import ACTIONS, ActionError, Ctx, summary, validate
from .audit import Audit
from .sessions import FRESH_S, RateLimiter, Sessions

log = logging.getLogger("ente_admin.portal")


class PortalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Portal:
    def __init__(self, cfg, dash):
        self.cfg, self.dash = cfg, dash
        self.clients = {i.name: MuseumClient(i.api_url or i.url) for i in cfg.instances
                        if i.source != "demo" and (i.api_url or i.url)}
        self.sessions = Sessions(cfg.admin_idle_minutes * 60, cfg.admin_max_minutes * 60, self._sign_out)
        self.audit = Audit(cfg.state_dir)
        self.code_limit = RateLimiter(3, 10 * 60)  # codes emailed, per user and instance
        self.verify_limit = RateLimiter(8, 15 * 60)  # code / password / TOTP attempts
        self.action_limit = RateLimiter(30, 60)  # changes per user
        self.read_limit = RateLimiter(120, 60)  # live account reads per user
        threading.Thread(target=self._reaper, daemon=True, name="portal-reaper").start()

    # ---- helpers ----
    def _sign_out(self, instance: str, token: str) -> None:
        client = self.clients.get(instance)
        if client:
            threading.Thread(target=login.logout, args=(client, token), daemon=True).start()

    def close(self) -> None:
        """Sign every session out of museum now (at shutdown, so it can't be cut short)."""
        self.sessions.end_all(lambda inst, tok: self.clients.get(inst) and login.logout(self.clients[inst], tok))

    def _reaper(self) -> None:
        while True:
            time.sleep(30)
            try:
                self.sessions.reap()
            except Exception:  # noqa: BLE001 - the reaper must keep running
                log.exception("session reaper")

    def _client(self, instance: str) -> MuseumClient:
        client = self.clients.get(instance)
        if not client:
            raise PortalError(404, "unknown instance, or one without a museum URL")
        return client

    def _session(self, viewer, instance: str):
        s = self.sessions.get(viewer.user, instance)
        if not s:
            raise PortalError(401, "locked: unlock admin actions for this server first")
        return s

    def _account_emails(self, instance: str) -> set[str]:
        for snap in self.dash.data():
            if snap["instance"] == instance:
                return {(a.get("email") or "").lower() for a in snap["accounts"] if a.get("email")}
        return set()

    def _uid(self, instance: str, label: str) -> int:
        uid = self.dash.account_id(instance, label)
        if uid is None:
            raise PortalError(404, "unknown account")
        return uid

    def _fetch(self, instance: str, token: str, uid: int) -> dict:
        try:
            return summary(self._client(instance).get("/admin/user", token, id=uid))
        except MuseumError as e:
            raise PortalError(502, f"museum: {e}") from None

    def _ctx_extra(self, viewer, session) -> dict:
        return {"admin_id": session.admin_id, "mail_from": self.cfg.mail_from, "viewer_email": viewer.email}

    # ---- status, unlock, lock ----
    def status(self, viewer) -> list[dict]:
        out = []
        for name in self.clients:
            st = self.sessions.status(viewer.user, name)
            out.append({"instance": name, **st})
        return out

    def send_code(self, viewer, instance: str, email: str) -> None:
        client = self._client(instance)
        email = (email or "").strip().lower()
        if not email or email not in self._account_emails(instance):
            # museum would create an account for an unknown address, so only existing ones get a code
            raise PortalError(400, "no account with that email on this server")
        if not self.code_limit.allow(f"{viewer.user}|{instance}"):
            raise PortalError(429, "too many codes requested; wait a few minutes")
        login.send_code(client, email)
        self.sessions.begin(viewer.user, instance, email)
        self.audit.add(viewer.user, instance, "unlock-code-sent", "ok", account_email=email)
        log.warning("portal: code sent for %s on %s (by %s)", email, instance, viewer.user)

    def verify(self, viewer, instance: str, code: str, password: str, totp: str = "") -> dict:
        client = self._client(instance)
        key = f"{viewer.user}|{instance}"
        p = self.sessions.pending(viewer.user, instance)
        if not p:
            raise PortalError(400, "request a code first (codes are valid for 10 minutes)")
        if not self.verify_limit.allow(key):
            self.sessions.drop_pending(viewer.user, instance)
            raise PortalError(429, "too many attempts; request a new code in a few minutes")
        if not password:
            raise PortalError(400, "the account password is needed to open the session")
        try:
            if p.verified is None and not p.totp_session:
                if not (code or "").strip().isdigit():
                    raise PortalError(400, "enter the six-digit code from the email")
                resp = login.verify_code(client, p.email, code.strip())
                step = login.stage(resp)
                if step == "passkey":
                    self.sessions.drop_pending(viewer.user, instance)
                    raise PortalError(400, "this account signs in with a passkey, which the portal can't do; "
                                           "add an authenticator app (TOTP) to it")
                if step == "totp":
                    p.totp_session = resp.get("twoFactorSessionIDV2") or resp.get("twoFactorSessionID")
                else:
                    p.verified = resp
            if p.verified is None:
                if not (totp or "").strip().isdigit():
                    return {"step": "totp"}  # ask for the authenticator code
                p.verified = login.verify_totp(client, p.totp_session, totp.strip())
                login.stage(p.verified)
            token = login.decrypt_token(p.verified, password, self.cfg.kdf_max_mib << 20)
        except login.LoginError as e:
            self.audit.add(viewer.user, instance, "unlock", "error", account_email=p.email, error=str(e))
            raise PortalError(400, str(e)) from None
        admin_id = int(p.verified.get("id") or 0)
        try:  # an admin? museum answers 401 "insufficient permissions" otherwise
            client.get("/admin/user", token, id=admin_id)
        except MuseumError as e:
            login.logout(client, token)
            self.sessions.drop_pending(viewer.user, instance)
            msg = "this account isn't an admin on this server (internal.admins in museum.yaml)" \
                if e.status in (401, 403) else f"museum: {e}"
            self.audit.add(viewer.user, instance, "unlock", "error", account_email=p.email, error=msg)
            raise PortalError(403, msg) from None
        self.sessions.start(viewer.user, instance, token, admin_id, p.email)
        self.audit.add(viewer.user, instance, "unlock", "ok", account_id=admin_id, account_email=p.email)
        log.warning("portal: %s unlocked %s as %s", viewer.user, instance, p.email)
        return {"step": "done", **self.sessions.status(viewer.user, instance)}

    def lock(self, viewer, instance: str) -> None:
        if self.sessions.end(viewer.user, instance):
            self.audit.add(viewer.user, instance, "lock", "ok")

    # ---- accounts and actions ----
    def account(self, viewer, instance: str, label: str) -> dict:
        s = self._session(viewer, instance)
        if not self.read_limit.allow(viewer.user):
            raise PortalError(429, "slow down")
        acct = self._fetch(instance, s.token, self._uid(instance, label))
        extra = self._ctx_extra(viewer, s)
        return {"account": acct, "fresh_left": max(0, int(FRESH_S - (time.time() - s.created))),
                "actions": [a.public(acct, extra) for a in ACTIONS.values() if a.scope == "account"]}

    def server_actions(self, viewer, instance: str) -> list[dict]:
        s = self._session(viewer, instance)
        extra = self._ctx_extra(viewer, s)
        return [a.public({}, extra) for a in ACTIONS.values() if a.scope == "instance"]

    def _prepare(self, viewer, instance: str, name: str, label: str, raw_params):
        action = ACTIONS.get(name)
        if not action:
            raise PortalError(404, "unknown action")
        s = self._session(viewer, instance)
        if action.scope == "account":
            uid = self._uid(instance, label)
            acct = self._fetch(instance, s.token, uid)
        else:
            uid, acct = 0, {}
        extra = self._ctx_extra(viewer, s)
        reason = action.available(acct, extra)
        if reason:
            raise PortalError(409, f"not available: {reason}")
        try:
            params = validate(action.fields, raw_params if raw_params is not None else {})
            plan = action.plan(Ctx(uid=uid, acct=acct, params=params, admin_id=s.admin_id,
                                   mail_from=self.cfg.mail_from))
        except ActionError as e:
            raise PortalError(400, str(e)) from None
        return action, s, uid, acct, params, plan

    def preview(self, viewer, instance: str, name: str, label: str, raw_params) -> dict:
        action, s, *_, plan = self._prepare(viewer, instance, name, label, raw_params)
        out = {"action": name, "title": action.title, "danger": action.danger, **plan.public()}
        if action.fresh and not s.fresh():
            out["warnings"] = [*out["warnings"], "Museum needs a sign-in from the last 10 minutes for this: "
                               "lock and unlock again before applying."]
        return out

    def run(self, viewer, instance: str, name: str, label: str, raw_params, confirm: str | None) -> dict:
        if not self.action_limit.allow(viewer.user):
            raise PortalError(429, "too many changes in a minute; wait a moment")
        action, s, uid, before, params, plan = self._prepare(viewer, instance, name, label, raw_params)
        if action.fresh and not s.fresh():
            raise PortalError(409, "museum needs a sign-in from the last 10 minutes for this: lock and unlock again")
        if plan.confirm is not None and (confirm or "").strip().lower() != plan.confirm.lower():
            raise PortalError(400, f"type “{plan.confirm}” to confirm")
        client = self._client(instance)
        audit = {"actor": viewer.user, "instance": instance, "action": name, "params": params,
                 "account_id": uid or None, "account_email": before.get("email")}
        try:
            client.request(plan.method, plan.path, s.token, body=plan.body, params=plan.params)
        except MuseumError as e:
            self.audit.add(**audit, result="error", before=before or None, error=str(e))
            log.warning("portal: %s %s on %s/%s failed: %s", viewer.user, name, instance, uid, e)
            raise PortalError(502, f"museum refused: {e}") from None
        after = None
        if action.scope == "account" and name != "delete-user":
            try:
                after = self._fetch(instance, s.token, uid)
            except PortalError:
                after = None
        self.audit.add(**audit, result="ok", before=before or None, after=after)
        log.warning("portal: %s %s on %s/%s (%s) ok", viewer.user, name, instance, uid, before.get("email") or "-")
        threading.Thread(target=self.dash.refresh, daemon=True).start()
        return {"ok": True, "changes": plan.changes, "account": after}

    # ---- server health ----
    def health(self) -> list[dict]:
        out = []
        snaps = {s["instance"]: s for s in self.dash.data()}
        for name, client in self.clients.items():
            item = {"instance": name, "api": client.base}
            t0 = time.monotonic()
            try:
                pong = client.request("GET", "/ping")
                item.update(ok=True, version=str(pong.get("id") or "")[:12], ms=int((time.monotonic() - t0) * 1000))
            except MuseumError as e:
                item.update(ok=False, error=str(e))
            snap = snaps.get(name) or {}
            item.update(accounts=len(snap.get("accounts") or []), data_error=snap.get("error"),
                        data_at=snap.get("generated_at"), source=snap.get("source"))
            out.append(item)
        return out
