"""HTTP server: the UI, a JSON API, and the opt-in admin portal and sharing.

Read-only by default. Everything sent to the browser goes through `public_view()`, which
keeps only the accounts the viewer may see and masks emails and account ids unless
ENTE_ADMIN_REVEAL_EMAILS is set. Tokens, keys and passwords never leave the process and
are never logged.
"""
from __future__ import annotations

import json
import logging
import secrets
import signal
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import __version__, auth, viewers
from .branding import Branding
from .config import Config
from .privacy import mask_email, mask_id
from .sources import build
from .viewers import Viewer

log = logging.getLogger("ente_admin")

STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "admin.js": "text/javascript; charset=utf-8",
          "icon.svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
# email previews: shown in a sandboxed frame of the UI; inline styles and inlined images only
PREVIEW_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox; frame-ancestors 'self'"
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}


def _salt(state_dir: str) -> str:
    """Per-installation salt for account labels, so labels are stable across restarts."""
    p = Path(state_dir) / "label-salt"
    try:
        return p.read_text().strip()
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        s = secrets.token_hex(16)
        p.write_text(s)
        p.chmod(0o600)
        return s


class Dashboard:
    """Holds the sources and the latest snapshots."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sources = [build(i) for i in cfg.instances]
        self.salt = _salt(cfg.state_dir)
        self.grants = viewers.Grants(cfg.state_dir)
        self.branding = Branding(cfg.branding_dir)
        self._lock = threading.Lock()
        self._data: list[dict] = []
        self._at = 0.0
        self.portal = None
        if cfg.enable_actions:
            from .portal import Portal
            self.portal = Portal(cfg, self)
        from .api import Api
        from .ops import Ops
        from .portal.audit import Audit
        self.audit = self.portal.audit if self.portal else Audit(cfg.state_dir)
        self.notifier = None
        if "notifications" in cfg.modules and any(i.source in ("postgres", "demo") for i in cfg.instances):
            from .notify import Notifier
            self.notifier = Notifier(cfg, self)
        self.ops = Ops(cfg, self, self.audit)
        self.api = Api(cfg, self, self.audit, self.notifier, self.ops)

    def refresh(self) -> None:
        out = []
        for src in self.sources:
            try:
                out.append(src.snapshot().to_dict())
            except Exception as e:  # noqa: BLE001 - one broken instance must not hide the others
                log.warning("instance %s: %s", src.name, e)
                out.append({"instance": src.name, "source": src.kind, "url": getattr(src, "url", ""),
                            "generated_at": int(time.time()), "accounts": [], "days": None,
                            "notes": [], "error": str(e)})
        with self._lock:
            self._data, self._at = out, time.time()

    def data(self) -> list[dict]:
        if time.time() - self._at > self.cfg.refresh_seconds:
            self.refresh()
        with self._lock:
            return self._data

    def label(self, account_id: int) -> str:
        return str(account_id) if self.cfg.reveal_emails else mask_id(account_id, self.salt)

    def public_view(self, viewer: Viewer | None = None) -> list[dict]:
        """The instances and accounts `viewer` may see, masked for the browser. Admins see
        every account (and, with sharing on, who each one is shared with); other viewers
        see accounts with their own email and the ones shared with them."""
        viewer = viewer or Viewer(user="")
        reveal = self.cfg.reveal_emails
        grants = self.grants.all() if self.cfg.admin_groups else []
        mine = {(i, a) for u, i, a in grants if u == viewer.user}
        shared: dict[tuple[str, int], list[str]] = {}
        for u, i, a in grants:
            shared.setdefault((i, a), []).append(u)
        view = []
        for snap in self.data():
            snap = json.loads(json.dumps(snap))  # deep copy
            accounts = []
            for a in snap["accounts"]:
                own = bool(viewer.email) and (a.get("email") or "").lower() == viewer.email
                if not (viewer.admin or own or (snap["instance"], a["id"]) in mine):
                    continue
                a["own"] = own
                if viewer.admin and self.cfg.sharing:
                    a["shared_with"] = sorted(shared.get((snap["instance"], a["id"]), []))
                a["label"] = self.label(a["id"])
                a["email"] = a["email"] if reveal else mask_email(a["email"])
                del a["id"]
                accounts.append(a)
            if not viewer.admin:
                if not accounts:
                    continue
                snap["notes"], snap["error"] = [], None
            snap["accounts"] = accounts
            view.append(snap)
        return view

    def account_id(self, instance: str, label: str) -> int | None:
        for snap in self.data():
            if snap["instance"] == instance:
                for a in snap["accounts"]:
                    if self.label(a["id"]) == label:
                        return a["id"]
        return None


def make_handler(dash: Dashboard):
    cfg = dash.cfg

    class Handler(BaseHTTPRequestHandler):
        server_version = "ente-admin"
        sys_version = ""

        def log_message(self, fmt, *args):  # path and status only; never headers or bodies
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            headers = {"Cache-Control": "no-store", **SECURITY_HEADERS, **(extra or {})}
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _user(self) -> str | None:
            if cfg.auth == "none":
                return "local"
            if cfg.auth == "header":
                return auth.check_header(self.client_address[0], self.headers.get(cfg.trusted_header),
                                         cfg.trusted_proxies)
            return auth.check_basic(self.headers.get("Authorization"), cfg.users)

        def _viewer(self) -> Viewer | None:
            user = self._user()
            return viewers.from_headers(user, self.headers, cfg) if user else None

        def _require_user(self) -> Viewer | None:
            viewer = self._viewer()
            if viewer:
                return viewer
            if cfg.auth == "basic":
                self._send(401, b"login required\n", "text/plain; charset=utf-8",
                           {"WWW-Authenticate": 'Basic realm="ente-admin", charset="UTF-8"'})
            else:
                self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
            return None

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain; charset=utf-8")
            viewer = self._require_user()
            if not viewer:
                return
            if path == "/":
                html = resources.files("ente_admin.ui").joinpath("index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC:
                body = resources.files("ente_admin.ui").joinpath(path[8:]).read_bytes()
                return self._send(200, body, STATIC[path[8:]])
            if path.startswith("/branding/") and (img := dash.branding.read(path[10:])):
                return self._send(200, img[0], img[1], {"Cache-Control": "private, max-age=86400"})
            if path == "/api/config":
                return self._json(200, {"version": __version__, "modules": list(cfg.modules),
                                        "reveal_emails": cfg.reveal_emails,
                                        "portal": bool(dash.portal and viewer.admin),
                                        "admin_idle_minutes": cfg.admin_idle_minutes,
                                        "refresh_seconds": cfg.refresh_seconds,
                                        "branding": dash.branding.public, "logout_url": cfg.logout_url,
                                        "sharing": cfg.sharing and viewer.admin,
                                        "notifications": bool(dash.notifier and viewer.admin),
                                        "instances": [{"name": i.name, "source": i.source,
                                                       "write_role": bool(i.write_dsn)} for i in cfg.instances]
                                        if viewer.admin else []})
            if path == "/api/data":
                out = {"instances": dash.public_view(viewer), "me": viewer.public()}
                if cfg.sharing and viewer.admin:
                    out["candidates"] = viewers.candidates(cfg)
                return self._json(200, out)
            if path.startswith("/api/admin/"):
                return self._admin_get(viewer, path[len("/api/admin/"):])
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

        # ---- admin portal ----
        def _query(self) -> dict[str, str]:
            q = urllib.parse.parse_qs(self.path.partition("?")[2], max_num_fields=10)
            return {k: v[0] for k, v in q.items()}

        def _portal(self, viewer: Viewer):
            if not dash.portal:
                self._json(403, {"error": "the admin portal is off (ENTE_ADMIN_ENABLE_ACTIONS)"})
                return None
            if not viewer.admin:
                self._json(403, {"error": "admins only"})
                return None
            return dash.portal

        def _same_origin(self) -> bool:
            """CSRF: a custom header (needs CORS cross-site, which is never granted), JSON only,
            and the browser's own origin signals when it sends them."""
            if self.headers.get("X-Ente-Admin") != "1":
                return False
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return False
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                return False
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
                return False
            return True

        def _body(self, limit: int = 8192) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ValueError("request too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            return data

        def _api_get(self, viewer: Viewer, op: str) -> bool:
            """Notifications, Locker, storage, apps, setup (api.py). True if it answered."""
            from .api import ERRORS
            if op == "notify/preview.html":
                try:
                    html = dash.api.preview_page(viewer, self._query())
                except ERRORS as e:
                    self._json(e.status, {"error": str(e)})
                    return True
                self._send(200, html.encode(), "text/html; charset=utf-8",
                           {"Content-Security-Policy": PREVIEW_CSP, "X-Frame-Options": "SAMEORIGIN"})
                return True
            try:
                out = dash.api.get(viewer, op, self._query())
            except ERRORS as e:
                self._json(e.status, {"error": str(e)})
                return True
            except Exception as e:  # noqa: BLE001 - e.g. the database is down: say so, keep serving
                log.warning("api %s: %s: %s", op, type(e).__name__, e)
                self._json(502, {"error": f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"})
                return True
            if out is None:
                return False
            self._json(200, out)
            return True

        def _api_post(self, viewer: Viewer, op: str) -> bool:
            from .api import ERRORS
            if not self._same_origin():
                self._json(403, {"error": "cross-site or malformed request refused"})
                return True
            try:
                req = self._body(4 << 20 if op == "notify/import" else 200_000)
                out = dash.api.post(viewer, op, req)
            except ERRORS as e:
                self._json(e.status, {"error": str(e)})
                return True
            except (ValueError, TypeError, UnicodeDecodeError, KeyError):
                self._json(400, {"error": "bad request"})
                return True
            except Exception as e:  # noqa: BLE001 - reported, keep serving
                log.warning("api %s: %s: %s", op, type(e).__name__, e)
                self._json(502, {"error": f"{type(e).__name__}"})
                return True
            if out is None:
                return False
            self._json(200, out)
            return True

        def _admin_get(self, viewer: Viewer, op: str):
            from .portal import PortalError
            if viewer.admin and self._api_get(viewer, op):
                return None
            portal = self._portal(viewer)
            if not portal:
                return None
            q = self._query()
            try:
                if op == "status":
                    return self._json(200, {"instances": portal.status(viewer),
                                            "idle_minutes": cfg.admin_idle_minutes})
                if op == "account":
                    return self._json(200, portal.account(viewer, q.get("instance", ""), q.get("account", "")))
                if op == "server-actions":
                    return self._json(200, {"actions": portal.server_actions(viewer, q.get("instance", ""))})
                if op == "health":
                    return self._json(200, {"instances": portal.health(), "version": __version__})
                if op == "audit":
                    limit = int(q.get("limit") or 200) if (q.get("limit") or "200").isdigit() else 200
                    return self._json(200, {"entries": portal.audit.recent(limit, q.get("instance") or None)})
            except PortalError as e:
                return self._json(e.status, {"error": str(e)})
            return self._json(404, {"error": "not found"})

        def _admin_post(self, viewer: Viewer, op: str):
            from .portal import PortalError
            portal = self._portal(viewer)
            if not portal:
                return None
            if op.split("/")[0] in ("notify", "links", "sessions", "backups", "config") and \
                    self._api_post(viewer, op):
                return None
            if not self._same_origin():
                return self._json(403, {"error": "cross-site or malformed request refused"})
            try:
                req = self._body()
                inst = str(req.get("instance", ""))
                if op == "unlock/send":
                    portal.send_code(viewer, inst, str(req.get("email", "")))
                    return self._json(200, {"ok": True, "step": "code"})
                if op == "unlock/verify":
                    return self._json(200, portal.verify(viewer, inst, str(req.get("code", "")),
                                                         str(req.get("password", "")), str(req.get("totp", ""))))
                if op == "lock":
                    portal.lock(viewer, inst)
                    return self._json(200, {"ok": True})
                if op in ("preview", "run"):
                    name, label = str(req.get("action", "")), str(req.get("account", ""))
                    if op == "preview":
                        return self._json(200, portal.preview(viewer, inst, name, label, req.get("params")))
                    return self._json(200, portal.run(viewer, inst, name, label, req.get("params"),
                                                      req.get("confirm")))
            except PortalError as e:
                return self._json(e.status, {"error": str(e)})
            except (ValueError, TypeError, UnicodeDecodeError):
                return self._json(400, {"error": "bad request"})
            return self._json(404, {"error": "not found"})

        def _share(self, viewer: Viewer):
            """Admins share or unshare an account: {"add", "username", "instance", "account"}."""
            if not (cfg.sharing and viewer.admin):
                return self._json(403, {"error": "sharing is off, or you're not an admin"})
            if self.headers.get("X-Ente-Admin") != "1":
                return self._json(403, {"error": "missing X-Ente-Admin header"})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                req = json.loads(self.rfile.read(length) or b"{}")
                username, instance = str(req.get("username", "")).strip(), str(req.get("instance", ""))
                if not viewers.USERNAME_RE.match(username):
                    return self._json(400, {"error": "invalid username"})
                uid = dash.account_id(instance, str(req.get("account", "")))
                if uid is None:
                    return self._json(400, {"error": "unknown account"})
            except (ValueError, TypeError):
                return self._json(400, {"error": "bad request"})
            add = bool(req.get("add"))
            dash.grants.change(add, username, instance, uid, viewer.user)
            log.warning("%s %s/%s %s %s by %s", "share" if add else "unshare", instance,
                        dash.label(uid), "with" if add else "from", username, viewer.user)
            self._json(200, {"ok": True})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            viewer = self._require_user()
            if not viewer:
                return
            if path == "/api/grants":
                return self._share(viewer)
            if path.startswith("/api/admin/"):
                return self._admin_post(viewer, path[len("/api/admin/"):])
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

    return Handler


def serve(cfg: Config) -> None:
    dash = Dashboard(cfg)
    dash.refresh()
    if dash.notifier:
        dash.notifier.start()
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(dash))
    # On SIGTERM (Kubernetes, systemd) stop serving, so unlocked admin sessions are signed out
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    log.info("ente-admin %s on http://%s:%d (auth=%s, %d instance(s), admin portal %s)", __version__, cfg.host,
             cfg.port, cfg.auth, len(cfg.instances), "on" if cfg.enable_actions else "off")
    try:
        httpd.serve_forever()
    finally:
        if dash.portal:
            dash.portal.close()
