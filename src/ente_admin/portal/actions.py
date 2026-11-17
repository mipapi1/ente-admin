"""Admin actions: what each one asks for, what it will change, and the museum call.

An action never runs blind: `plan()` turns the account's current state (from museum's
GET /admin/user) and the validated parameters into a Plan, the list of changes shown
in the preview, plus the text the admin has to type for the risky ones. The same plan
is rebuilt from fresh data when the action runs, so the preview can't go stale.

Only endpoints that make sense on a self-hosted server are here; see docs/ADMIN.md for
the ones left out (cloud billing, mailing lists, coupons, custom login codes).
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

GiB = 1024 ** 3
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
SUPPORT_BONUS = "ADD_ON_SUPPORT"  # the add-on type museum allows any size (1-2000 GB) and term (1-100 y) for


class ActionError(ValueError):
    pass


# ---- the account, as the portal shows it ----
def summary(d: dict) -> dict:
    """The parts of GET /admin/user an admin acts on. Also what the audit log stores."""
    u, sub, det = d.get("user") or {}, d.get("subscription") or {}, d.get("details") or {}
    bonuses = [{"type": b.get("type"), "storage": b.get("storage"), "valid_till": b.get("validTill")}
               for b in ((det.get("bonusData") or {}).get("storageBonuses") or []) if not b.get("isRevoked")]
    fam = det.get("familyData") or None
    members = [{"email": m.get("email"), "status": m.get("status"), "usage": m.get("usage"), "admin": m.get("isAdmin")}
               for m in (fam or {}).get("members") or []]
    plan = sub.get("storage")
    return {
        "id": u.get("ID"), "email": u.get("email"), "created": u.get("creationTime"),
        "two_factor": bool(u.get("isTwoFactorEnabled")), "email_mfa": bool(u.get("isEmailMFAEnabled")),
        "product": sub.get("productID"), "plan_storage": plan, "plan_expiry": sub.get("expiryTime"),
        "bonus_storage": det.get("storageBonus") or 0, "bonuses": bonuses,
        "quota": (plan or 0) + (det.get("storageBonus") or 0) if plan is not None else None,
        "used": d.get("storageConsumed", det.get("usage")), "files": d.get("photosFileCount", det.get("fileCount")),
        "family_admin_id": u.get("familyAdminID"), "family": members or None,
        "sessions": sum(1 for t in d.get("tokens") or [] if not t.get("isDeleted")), "auth_codes": d.get("authCodes"),
    }


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    units, v, i = ["B", "KB", "MB", "GB", "TB", "PB"], float(n), 0
    while v >= 1024 and i < len(units) - 1:
        v, i = v / 1024, i + 1
    return f"{v:.0f} {units[i]}" if v >= 100 or i == 0 or v == int(v) else f"{v:.1f} {units[i]}"


def fmt_date(us: int | None) -> str:
    return datetime.fromtimestamp(us / 1e6, UTC).date().isoformat() if us else "—"


def on_off(v: bool) -> str:
    return "on" if v else "off"


# ---- parameters ----
@dataclass
class Field:
    name: str
    label: str
    kind: str  # int | date | email | bool
    min: int | None = None
    max: int | None = None
    help: str = ""
    default: Callable[[dict, dict], object] | None = None  # (summary, context) -> value for the form

    def public(self, acct: dict, ctx: dict) -> dict:
        out = {"name": self.name, "label": self.label, "kind": self.kind, "help": self.help}
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.default:
            out["default"] = self.default(acct, ctx)
        return out


def validate(fields: list[Field], raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ActionError("parameters must be an object")
    unknown = set(raw) - {f.name for f in fields}
    if unknown:
        raise ActionError(f"unknown parameter: {sorted(unknown)[0]}")
    out = {}
    for f in fields:
        v = raw.get(f.name)
        if v is None or v == "":
            raise ActionError(f"{f.label}: required")
        if f.kind == "int":
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            if not isinstance(v, int) or isinstance(v, bool):
                raise ActionError(f"{f.label}: a whole number")
            if (f.min is not None and v < f.min) or (f.max is not None and v > f.max):
                raise ActionError(f"{f.label}: between {f.min} and {f.max}")
        elif f.kind == "date":
            try:
                d = date.fromisoformat(str(v))
            except ValueError:
                raise ActionError(f"{f.label}: a date (YYYY-MM-DD)") from None
            if d < date.today() or d.year > 2200:
                raise ActionError(f"{f.label}: from today up to 2200")
            v = d.isoformat()
        elif f.kind == "email":
            v = str(v).strip().lower()
            if len(v) > 254 or not EMAIL_RE.match(v):
                raise ActionError(f"{f.label}: not an email address")
        elif f.kind == "bool":
            if not isinstance(v, bool):
                raise ActionError(f"{f.label}: true or false")
        out[f.name] = v
    return out


def end_of_day_us(iso: str) -> int:
    d = date.fromisoformat(iso)
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC).timestamp() * 1_000_000)


# ---- plans ----
@dataclass
class Plan:
    changes: list[dict]  # {"what", "before", "after"}, display strings
    method: str
    path: str
    body: dict | None = None
    params: dict | None = None
    warnings: list[str] = field(default_factory=list)
    confirm: str | None = None  # text the admin must type, or None for a confirmation click

    def public(self) -> dict:
        return {"changes": self.changes, "warnings": self.warnings, "confirm": self.confirm}


@dataclass
class Ctx:
    uid: int
    acct: dict  # summary() of the account (instance actions: {})
    params: dict
    admin_id: int  # the Ente admin account the session belongs to
    mail_from: str = ""
    now: float = field(default_factory=time.time)


@dataclass
class Action:
    name: str
    title: str
    group: str
    description: str
    plan: Callable[[Ctx], Plan]
    fields: list[Field] = field(default_factory=list)
    danger: str = "normal"  # normal | danger | irreversible
    fresh: bool = False  # museum wants a token less than 10 minutes old
    scope: str = "account"  # account | instance
    available: Callable[[dict, dict], str | None] = lambda acct, ctx: None  # reason it can't run, or None

    def public(self, acct: dict, ctx: dict) -> dict:
        return {"name": self.name, "title": self.title, "group": self.group, "description": self.description,
                "danger": self.danger, "fresh": self.fresh, "scope": self.scope,
                "unavailable": self.available(acct, ctx), "fields": [f.public(acct, ctx) for f in self.fields]}


def _support_bonus(acct: dict) -> dict | None:
    return next((b for b in acct.get("bonuses") or [] if b["type"] == SUPPORT_BONUS), None)


def _quota_change(acct: dict, delta: int) -> dict:
    q = acct.get("quota")
    return {"what": "Total storage", "before": fmt_bytes(q), "after": fmt_bytes(None if q is None else q + delta)}


# Storage & plan
def plan_set_storage(c: Ctx) -> Plan:
    a, new = c.acct, c.params["storage_gb"] * GiB
    expiry = end_of_day_us(c.params["expiry"])
    p = Plan(changes=[
        {"what": "Plan storage", "before": fmt_bytes(a["plan_storage"]), "after": fmt_bytes(new)},
        {"what": "Plan valid until", "before": fmt_date(a["plan_expiry"]), "after": c.params["expiry"]},
        _quota_change(a, new - (a["plan_storage"] or 0))],
        method="PUT", path="/admin/user/subscription",
        body={"userID": c.uid, "storage": new, "expiryTime": expiry, "productID": "free", "paymentProvider": "",
              "transactionID": f"ente-admin-{int(c.now)}"})
    if a.get("used") is not None and new + (a["bonus_storage"] or 0) < a["used"]:
        p.warnings.append(f"Less than the account already uses ({fmt_bytes(a['used'])}): uploads will stop.")
    return p


def plan_set_bonus(c: Ctx) -> Plan:
    a, cur = c.acct, _support_bonus(c.acct)
    gb, years = c.params["storage_gb"], c.params["years"]
    today = date.fromtimestamp(c.now)
    try:
        until = today.replace(year=today.year + years).isoformat()
    except ValueError:  # 29 February
        until = today.replace(year=today.year + years, day=28).isoformat()
    before = f"{fmt_bytes(cur['storage'])} until {fmt_date(cur['valid_till'])}" if cur else "none"
    return Plan(changes=[{"what": "Support add-on", "before": before, "after": f"{fmt_bytes(gb * GiB)} until {until}"},
                         _quota_change(a, gb * GiB - (cur["storage"] if cur else 0))],
                method="POST", path="/admin/user/bonus",
                body={"bonusType": SUPPORT_BONUS, "action": "UPDATE" if cur else "ADD", "userID": c.uid,
                      "year": years, "storageInGB": gb})


def plan_remove_bonus(c: Ctx) -> Plan:
    cur = _support_bonus(c.acct)
    return Plan(changes=[{"what": "Support add-on", "before": f"{fmt_bytes(cur['storage'])} until "
                          f"{fmt_date(cur['valid_till'])}", "after": "none"}, _quota_change(c.acct, -cur["storage"])],
                method="POST", path="/admin/user/bonus",
                body={"bonusType": SUPPORT_BONUS, "action": "REMOVE", "userID": c.uid})


# Security
def plan_disable_2fa(c: Ctx) -> Plan:
    return Plan(changes=[{"what": "Two-factor (authenticator app)", "before": "on", "after": "off"}],
                method="POST", path="/admin/user/disable-2fa", body={"userID": c.uid}, confirm=c.acct["email"],
                warnings=["Verify who is asking (out of band) first: this removes the second factor."])


def plan_remove_passkeys(c: Ctx) -> Plan:
    return Plan(changes=[{"what": "Passkeys", "before": "any registered", "after": "none"}],
                method="POST", path="/admin/user/disable-passkeys", body={"userID": c.uid}, confirm=c.acct["email"],
                warnings=["Verify who is asking first. An authenticator app (TOTP), if set up, stays on."])


def plan_email_mfa(c: Ctx) -> Plan:
    on = c.params["enabled"]
    if on == c.acct["email_mfa"]:
        raise ActionError(f"email codes are already {on_off(on)}")
    return Plan(changes=[{"what": "Email code at sign-in", "before": on_off(c.acct["email_mfa"]), "after": on_off(on)}],
                method="POST", path="/admin/user/update-email-mfa", body={"userID": c.uid, "emailMFA": on})


# Account
def plan_change_email(c: Ctx) -> Plan:
    new = c.params["email"]
    if new == (c.acct["email"] or "").lower():
        raise ActionError("that is already the account's email")
    return Plan(changes=[{"what": "Email (sign-in)", "before": c.acct["email"], "after": new}],
                method="PUT", path="/admin/user/change-email", body={"userID": c.uid, "email": new},
                confirm=c.acct["email"],
                warnings=["The account signs in with the new address from now on; codes go there.",
                          "Museum refuses an address another account already uses."])


def plan_close_family(c: Ctx) -> Plan:
    others = [m["email"] for m in c.acct["family"] or [] if not m["admin"]]
    return Plan(changes=[{"what": "Family plan", "before": f"{len(others)} member(s): " + ", ".join(others) if others
                          else "no members", "after": "closed"}],
                method="POST", path="/admin/user/close-family", body={"userID": c.uid}, confirm=c.acct["email"],
                warnings=["Members keep their accounts and files but lose the shared storage."])


def plan_unblock_login(c: Ctx) -> Plan:
    return Plan(changes=[{"what": "Sign-in after a storage warning", "before": "blocked (if it was)",
                          "after": "allowed"}],
                method="POST", path="/admin/user/unblock-storage-warning-login", body={"userID": c.uid})


def plan_delete(c: Ctx) -> Plan:
    a = c.acct
    p = Plan(changes=[{"what": "Account", "before": a["email"], "after": "deleted"},
                      {"what": "Files", "before": f"{a['files'] if a['files'] is not None else '—'} "
                       f"({fmt_bytes(a['used'])})", "after": "deleted"},
                      {"what": "Signed-in devices", "before": str(a["sessions"]), "after": "signed out"}],
             method="DELETE", path="/admin/user/delete", params={"email": a["email"]},
             confirm=f"delete {a['email']}",
             warnings=["This can't be undone: museum removes the account now and its files from storage after "
                       "the deletion hold. Only a database and object-storage restore brings it back."])
    if a["family"]:
        p.warnings.append("The account has a family plan; its members lose the shared storage.")
    return p


# Server
def plan_test_mail(c: Ctx) -> Plan:
    to = c.params["to"]
    return Plan(changes=[{"what": "Test email", "before": "", "after": f"sent to {to} from {c.mail_from}"}],
                method="POST", path="/admin/mail",
                body={"to": [to], "fromName": "Ente admin", "fromEmail": c.mail_from,
                      "subject": "Test email from your Ente server",
                      "body": "This is a test email sent from the Ente admin portal. Email delivery works."})


def _expiry_default(acct, ctx):
    return fmt_date(acct.get("plan_expiry")) if acct.get("plan_expiry") else ""


ACTIONS: dict[str, Action] = {a.name: a for a in [
    Action("set-storage", "Change plan storage", "Storage & plan",
           "Set the storage and expiry of the account's plan (free plan only, like `ente admin update-subscription`).",
           plan_set_storage,
           fields=[Field("storage_gb", "Plan storage (GB)", "int", 1, 1_000_000,
                         default=lambda a, c: round((a.get("plan_storage") or 0) / GiB)),
                   Field("expiry", "Valid until", "date", default=_expiry_default)],
           available=lambda a, c: None if a.get("product") == "free" else
           f"the account is on a paid plan ({a.get('product')}); change it with its payment provider"),
    Action("set-bonus", "Add or change extra storage", "Storage & plan",
           "Extra storage on top of the plan (a support add-on), for a number of years.", plan_set_bonus,
           fields=[Field("storage_gb", "Extra storage (GB)", "int", 1, 2000,
                         default=lambda a, c: round((_support_bonus(a) or {"storage": 50 * GiB})["storage"] / GiB)),
                   Field("years", "For (years)", "int", 1, 100, default=lambda a, c: 10)]),
    Action("remove-bonus", "Remove extra storage", "Storage & plan", "Remove the support add-on.", plan_remove_bonus,
           available=lambda a, c: None if _support_bonus(a) else "no extra storage to remove"),
    Action("disable-2fa", "Turn off two-factor", "Security",
           "For someone who lost their authenticator app (`ente admin disable-2fa`).", plan_disable_2fa,
           danger="danger", fresh=True,
           available=lambda a, c: None if a.get("two_factor") else "two-factor is off"),
    Action("remove-passkeys", "Remove passkeys", "Security",
           "For someone who lost the devices holding their passkeys (`ente admin disable-passkey`).",
           plan_remove_passkeys, danger="danger"),
    Action("email-mfa", "Email code at sign-in", "Security",
           "Ask for a code sent by email at every sign-in, or stop asking.", plan_email_mfa,
           fields=[Field("enabled", "Ask for an email code", "bool", default=lambda a, c: not a.get("email_mfa"))]),
    Action("change-email", "Change email", "Account", "Change the address the account signs in with.",
           plan_change_email, danger="danger", fields=[Field("email", "New email", "email")]),
    Action("close-family", "Close family plan", "Account", "End the account's family plan.", plan_close_family,
           danger="danger",
           available=lambda a, c: None if a.get("family") and a.get("family_admin_id") in (None, a.get("id"))
           else "the account doesn't run a family plan"),
    Action("unblock-login", "Allow sign-in after storage warning", "Account",
           "Lets the account sign in again if museum blocked it after a storage-limit warning.", plan_unblock_login),
    Action("delete-user", "Delete account", "Danger zone",
           "Delete the account and all its files (`ente admin delete-user`).", plan_delete,
           danger="irreversible", fresh=True,
           available=lambda a, c: "you can't delete the admin account you're signed in with"
           if a.get("id") == c.get("admin_id") else None),
    Action("send-test-mail", "Send a test email", "Server", "Check that the server can send email.", plan_test_mail,
           scope="instance", fields=[Field("to", "Send to", "email", default=lambda a, c: c.get("viewer_email", ""))],
           available=lambda a, c: None if c.get("mail_from") else "set ENTE_ADMIN_MAIL_FROM (the sender address)"),
]}
