"""A small stand-in for museum: the login endpoints (with Ente's real key scheme: argon2id,
secretbox, sealed box) and the admin endpoints the portal calls, keeping state in memory
so tests can check what changed. Shapes follow museum's handlers (pkg/api/admin.go)."""
from __future__ import annotations

import base64
import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nacl import public, pwhash, secret, utils

GiB = 1024 ** 3
MEM, OPS = 8 * 1024 * 1024, 1  # tiny argon2 parameters, so tests are fast
CODE, TOTP = "123456", "654321"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


@dataclass
class User:
    id: int
    email: str
    password: str
    two_factor: bool = False
    email_mfa: bool = False
    storage: int = 10 * GiB
    expiry: int = 4_000_000_000_000_000
    product: str = "free"
    bonuses: list = field(default_factory=list)
    family: list = field(default_factory=list)  # member emails, when this user runs a family plan
    deleted: bool = False
    key_attributes: dict = field(default_factory=dict)
    public_key: bytes = b""

    def setup_keys(self):
        salt = utils.random(16)
        kek = pwhash.argon2id.kdf(32, self.password.encode(), salt, opslimit=OPS, memlimit=MEM)
        master = utils.random(32)
        sk = public.PrivateKey.generate()
        n1, n2 = utils.random(24), utils.random(24)
        self.public_key = bytes(sk.public_key)
        self.key_attributes = {
            "kekSalt": b64(salt), "memLimit": MEM, "opsLimit": OPS, "publicKey": b64(self.public_key),
            "encryptedKey": b64(secret.SecretBox(kek).encrypt(master, n1).ciphertext), "keyDecryptionNonce": b64(n1),
            "encryptedSecretKey": b64(secret.SecretBox(master).encrypt(bytes(sk), n2).ciphertext),
            "secretKeyDecryptionNonce": b64(n2)}


class FakeMuseum:
    def __init__(self):
        self.users: dict[int, User] = {}
        self.admins: set[int] = set()
        self.tokens: dict[str, tuple[int, float]] = {}  # token -> (user id, created)
        self.otts: dict[str, str] = {}
        self.two_factor_sessions: dict[str, int] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.mail: list[dict] = []
        self.logged_out: list[str] = []
        self.lock = threading.Lock()

    def add_user(self, uid: int, email: str, password: str = "pw", admin: bool = False, **kw) -> User:
        u = User(uid, email, password, **kw)
        u.setup_keys()
        self.users[uid] = u
        if admin:
            self.admins.add(uid)
        return u

    def new_token(self, uid: int, age_s: float = 0) -> str:
        tok = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        self.tokens[tok] = (uid, time.time() - age_s)
        return tok

    def by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email and not u.deleted), None)

    def login_result(self, u: User) -> dict:
        tok = self.new_token(u.id)
        sealed = public.SealedBox(public.PublicKey(u.public_key)).encrypt(base64.urlsafe_b64decode(tok))
        return {"id": u.id, "keyAttributes": u.key_attributes, "encryptedToken": b64(sealed)}

    def user_json(self, u: User) -> dict:
        bonus = sum(b["storage"] for b in u.bonuses)
        details = {"usage": 3 * GiB, "storageBonus": bonus, "fileCount": 42,
                   "bonusData": {"storageBonuses": [dict(b, isRevoked=False) for b in u.bonuses]},
                   "subscription": {"productID": u.product, "storage": u.storage, "expiryTime": u.expiry}}
        if u.family:
            details["familyData"] = {"members": [{"email": u.email, "status": "SELF", "isAdmin": True, "usage": 1}] + [
                {"email": e, "status": "ACCEPTED", "isAdmin": False, "usage": 2} for e in u.family]}
        return {"user": {"ID": u.id, "email": u.email, "creationTime": 1_700_000_000_000_000,
                         "familyAdminID": u.id if u.family else None,
                         "isTwoFactorEnabled": u.two_factor, "isEmailMFAEnabled": u.email_mfa},
                "subscription": {"productID": u.product, "storage": u.storage, "expiryTime": u.expiry},
                "details": details, "storageConsumed": 3 * GiB, "photosFileCount": 42,
                "tokens": [{"creationTime": 1, "lastUsedTime": 2, "ua": "Dart/3.0", "app": "photos",
                            "isDeleted": False}], "authCodes": 0}

    # ---- HTTP ----
    def serve(self):
        museum = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj=None):
                body = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_any(self):
                url = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else {}
                with museum.lock:
                    museum.calls.append((self.command, url.path, body or q))
                    return museum.route(self, self.command, url.path, q, body)

            do_GET = do_POST = do_PUT = do_DELETE = handle_any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def route(self, h, method, path, q, body):  # noqa: C901 - one switch, like a router
        if path == "/ping":
            return h.reply(200, {"id": "abc123def4567890", "message": "pong"})
        if (method, path) == ("POST", "/users/ott"):
            u = self.by_email(body.get("email"))
            if u and body.get("purpose") == "login":
                self.otts[u.email] = CODE
            return h.reply(200)
        if (method, path) == ("POST", "/users/verify-email"):
            u = self.by_email(body.get("email"))
            if not u or self.otts.get(u.email) != body.get("ott"):
                return h.reply(401, {"code": "INVALID_OTT", "message": "invalid ott"})
            del self.otts[u.email]
            if u.two_factor:
                sid = secrets.token_hex(8)
                self.two_factor_sessions[sid] = u.id
                return h.reply(200, {"id": u.id, "twoFactorSessionID": sid})
            return h.reply(200, self.login_result(u))
        if (method, path) == ("POST", "/users/two-factor/verify"):
            uid = self.two_factor_sessions.get(body.get("sessionID"))
            if uid is None or body.get("code") != TOTP:
                return h.reply(401, {"code": "INVALID_CODE", "message": "invalid code"})
            return h.reply(200, self.login_result(self.users[uid]))
        tok = h.headers.get("X-Auth-Token")
        if tok not in self.tokens:
            return h.reply(401, {"code": "UNAUTHORIZED", "message": "invalid token"})
        uid, created = self.tokens[tok]
        if (method, path) == ("POST", "/users/logout"):
            del self.tokens[tok]
            self.logged_out.append(tok)
            return h.reply(200)
        if not path.startswith("/admin/"):
            return h.reply(404)
        if uid not in self.admins:
            return h.reply(401, {"error": "insufficient permissions"})
        fresh = time.time() - created < 600
        if path in ("/admin/users", "/admin/user/disable-2fa", "/admin/user/delete") and not fresh:
            return h.reply(400, {"code": "BAD_REQUEST", "message": "Token is too old"})
        if (method, path) == ("GET", "/admin/users"):
            return h.reply(200, {"users": [{"ID": u.id, "email": u.email} for u in self.users.values()
                                           if not u.deleted]})
        if (method, path) == ("GET", "/admin/user"):
            u = self.users.get(int(q.get("id", 0)))
            return h.reply(200, self.user_json(u)) if u and not u.deleted else h.reply(404, {"message": "no user"})
        if (method, path) == ("POST", "/admin/mail"):
            self.mail.append(body)
            return h.reply(200)
        if (method, path) == ("DELETE", "/admin/user/delete"):
            u = self.by_email(q.get("email"))
            if not u:
                return h.reply(404, {"message": "no user"})
            u.deleted = True
            return h.reply(200, {})
        u = self.users.get(int(body.get("userID") or 0))
        if not u:
            return h.reply(404, {"message": "no user"})
        if (method, path) == ("PUT", "/admin/user/subscription"):
            u.storage, u.expiry, u.product = body["storage"], body["expiryTime"], body["productID"]
        elif (method, path) == ("POST", "/admin/user/bonus"):
            u.bonuses = [b for b in u.bonuses if b["type"] != body["bonusType"]]
            if body["action"] in ("ADD", "UPDATE"):
                u.bonuses.append({"type": body["bonusType"], "storage": body["storageInGB"] * GiB,
                                  "validTill": int((time.time() + body["year"] * 365 * 86400) * 1e6)})
        elif (method, path) == ("POST", "/admin/user/disable-2fa"):
            u.two_factor = False
        elif (method, path) == ("POST", "/admin/user/update-email-mfa"):
            u.email_mfa = body["emailMFA"]
        elif (method, path) == ("PUT", "/admin/user/change-email"):
            if self.by_email(body["email"]):
                return h.reply(403, {"code": "PERMISSION_DENIED", "message": "email already in use"})
            u.email = body["email"]
        elif (method, path) == ("POST", "/admin/user/close-family"):
            u.family = []
        elif path not in ("/admin/user/disable-passkeys", "/admin/user/unblock-storage-warning-login"):
            return h.reply(404)
        return h.reply(200)
