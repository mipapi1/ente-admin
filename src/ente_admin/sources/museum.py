"""Museum's admin API (the endpoints the `ente admin` CLI uses). Needs an auth token of
an account listed under `internal.admins` in museum.yaml.

- GET /admin/user?id=<id>   per-account details (email, storage, files, plan, devices).
  Accepts any valid admin token.
- GET /admin/users          the account list. Museum only accepts a token created less
  than 10 minutes ago here, so the list is refreshed when a fresh token is available
  (FRESH_TOKEN_FILE, or TOKEN_FILE if it happens to be fresh) and otherwise read from a
  cache of account ids in the state directory. The cache holds ids only, no emails.

Tokens are read from files on every refresh and never logged or sent to the browser.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import Instance
from . import register
from .model import Account, Device, Snapshot, device_name

log = logging.getLogger(__name__)
TIMEOUT = 10


class MuseumError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


class MuseumClient:
    def __init__(self, base_url: str, timeout: float = TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, token: str, **params) -> dict:
        return self.request("GET", path, token, params=params)

    def request(self, method: str, path: str, token: str | None = None, body: dict | None = None,
                params: dict | None = None) -> dict:
        """One JSON call. Errors become MuseumError with museum's message (and `status`);
        the request itself (token, body) is never part of the error."""
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "ente-admin"}
        if token:
            headers["X-Auth-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.load(e)
            except (ValueError, OSError):
                err = {}
            err = err if isinstance(err, dict) else {}
            # museum's error bodies are {"code": ..., "message": ...}; never echo the request
            msg = err.get("message") or err.get("error") or err.get("code") or e.reason
            raise MuseumError(f"HTTP {e.code}: {msg}", status=e.code, code=str(err.get("code") or "")) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise MuseumError(f"cannot reach museum: {getattr(e, 'reason', e)}") from None

    def ping(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/ping", timeout=self.timeout) as resp:
            return json.load(resp)


def _read(path: str) -> str:
    return Path(path).read_text().strip() if path else ""


def _sec(us: int | None) -> int | None:
    return int(us / 1_000_000) if us else None


@register("museum")
class MuseumSource:
    kind = "museum"

    def __init__(self, instance: Instance, client: MuseumClient | None = None):
        self.name = instance.name
        self.url = instance.url
        self.instance = instance
        self.client = client or MuseumClient(instance.url)
        state = Path(instance.state_dir or ".")
        self.cache = state / f"{instance.name}.account-ids.json"

    def _account_ids(self, token: str, notes: list[str]) -> list[int]:
        for tok in (_read(self.instance.fresh_token_file), token):
            if not tok:
                continue
            try:
                users = self.client.get("/admin/users", tok, sinceTime=0).get("users") or []
            except MuseumError as e:
                if "too old" not in str(e).lower():
                    raise
                continue
            ids = [int(u["ID"]) for u in users if "ID" in u]
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps({"ids": ids, "at": int(time.time())}))
            return ids
        if self.cache.exists():
            data = json.loads(self.cache.read_text())
            age_h = (time.time() - data.get("at", 0)) / 3600
            notes.append(f"Account list from cache ({age_h:.0f} h old): museum only lists accounts with an "
                         "admin token less than 10 minutes old. New accounts appear after the next fresh token.")
            return [int(i) for i in data.get("ids", [])]
        raise MuseumError("no account list yet: museum needs an admin token less than 10 minutes old "
                          "once (FRESH_TOKEN_FILE); see docs/CONFIG.md")

    def snapshot(self) -> Snapshot:
        now = int(time.time())
        notes: list[str] = []
        token = _read(self.instance.token_file)
        ids = self._account_ids(token, notes)
        accounts = []
        for uid in ids:
            d = self.client.get("/admin/user", token, id=uid)
            u = d.get("user") or {}
            sub = d.get("subscription") or {}
            details = d.get("details") or {}
            quota = sub.get("storage")
            if quota is not None:
                quota += details.get("storageBonus") or 0
            devices = [Device(device=device_name(t.get("ua")), app=str(t.get("app") or "photos"),
                              signed_in=_sec(t.get("creationTime")), last_seen=_sec(t.get("lastUsedTime")))
                       for t in (d.get("tokens") or []) if not t.get("isDeleted")]
            devices.sort(key=lambda x: x.last_seen or 0, reverse=True)
            accounts.append(Account(
                id=int(u.get("ID", uid)), email=u.get("email") or None, created=_sec(u.get("creationTime")),
                quota=quota, used=d.get("storageConsumed", details.get("usage")),
                files=d.get("photosFileCount", details.get("fileCount")),
                two_factor=u.get("isTwoFactorEnabled"), email_mfa=u.get("isEmailMFAEnabled"),
                plan_expiry=_sec(sub.get("expiryTime")),
                last_seen=devices[0].last_seen if devices else None, devices=devices))
        notes.append("Albums, trash and daily uploads need the postgres source; museum's API doesn't report them.")
        return Snapshot(instance=self.name, source=self.kind, url=self.url, generated_at=now,
                        accounts=accounts, notes=notes)
