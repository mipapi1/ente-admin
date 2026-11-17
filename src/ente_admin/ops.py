"""Server views and operations beyond museum's admin API (docs/ADMIN.md, docs/LOCKER.md):

- public links (album, Locker and file links): list them, and revoke one through the opt-in
  write role, which flips the same `is_disabled` flag museum's own "remove link" does;
- an account's signed-in devices, and signing one out (the same `is_deleted` flag museum's
  logout sets);
- storage coverage: objects and bytes per storage bucket (datacenter) and app, and how many
  objects have fewer copies than the others; plus backup checks recorded by an operator;
- web apps: which ones are configured, whether they answer, and museum.yaml snippets and
  diffs to enable them (the operator applies them: the dashboard never writes museum.yaml);
- setup: what the database role can read, the grants for what it can't, the write role,
  email and config file, as a checklist.

Everything here reads metadata only: never file contents, keys, link tokens or session
tokens. museum.yaml is shown with secrets redacted.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .db import GRANTS, PG, WRITE_GRANTS, Features, missing_privilege
from .privacy import mask_email

log = logging.getLogger("ente_admin.ops")

# our web app names -> museum.yaml `apps:` keys (museum builds links and emails with them)
YAML_APPS = {"albums": "public-albums", "embed": "embed-albums", "share": "public-locker", "paste": "public-paste",
             "cast": "cast", "accounts": "accounts", "memories": "public-memories", "legacy": "legacy"}
APP_INFO = {
    "photos": "Ente Photos web app",
    "accounts": "passkeys, family invites and other cross-app flows",
    "albums": "public album links (Photos)",
    "share": "Locker share links (museum's apps.public-locker)",
    "locker": "Ente Locker web app",
    "auth": "Ente Auth web app",
    "cast": "Cast albums to a TV",
    "embed": "embedding public albums in other sites",
    "paste": "Ente Paste",
    "memories": "public memory shares",
    "legacy": "legacy kit recovery (inheritance)",
}
SECRET_RE = re.compile(r"^(\s*-?\s*)([\w.-]*(password|secret|key|hash|token|dsn|jwt|encryption|credential)[\w.-]*)"
                       r"(\s*:\s*)(\S.*)$", re.I)
PROBE_TTL_S = 60
# features the account snapshot doesn't read: checked with an empty query
FEATURE_PROBES = {
    "storage": "select ok.file_id, ok.o_type, ok.size, ok.datacenters, ok.is_deleted, f.app from object_keys ok "
               "join files f on f.file_id = ok.file_id limit 0",
    "uploads": "select cf.collection_id, cf.file_id, cf.f_owner_id, cf.is_deleted, cf.created_at, c.owner_id "
               "from collection_files cf join collections c on c.collection_id = cf.collection_id limit 0",
}


class OpsError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _sec(us):
    return us / 1e6 if us else None


# ---- museum.yaml (a small line-based editor: enough for the `apps` and `internal` blocks) ----
def redact(text: str) -> str:
    """museum.yaml with every value that looks like a secret replaced."""
    out = []
    for line in text.splitlines():
        m = SECRET_RE.match(line)
        if m and not m.group(5).startswith(("#", "{", "[")) and m.group(5).strip() not in ('""', "''", "~"):
            line = f"{m.group(1)}{m.group(2)}{m.group(4)}<redacted>"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Top-level key -> (first line, end line) of its block."""
    starts = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := re.match(r"^([A-Za-z][\w.-]*)\s*:", ln))]
    out = {}
    for k, (i, key) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        while end > i + 1 and not lines[end - 1].strip():
            end -= 1
        out[key] = (i, end)
    return out


def _child_indent(lines: list[str], start: int, end: int) -> str:
    for ln in lines[start + 1:end]:
        if ln.strip() and not ln.lstrip().startswith("#"):
            return ln[: len(ln) - len(ln.lstrip())]
    return "    "


def _set_child(lines: list[str], block: str, key: str, value: str | None, list_value: list | None = None) -> None:
    """Set `block.key` to a scalar (or an inline list), adding the block or key if missing."""
    rendered = f"[{', '.join(str(v) for v in list_value)}]" if list_value is not None else value
    blocks = _blocks(lines)
    if block not in blocks:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"{block}:", f"    {key}: {rendered}"])
        return
    start, end = blocks[block]
    if re.match(rf"^{re.escape(block)}\s*:\s*\S", lines[start]) and not lines[start].split(":", 1)[1].strip() \
            .startswith("#"):
        lines[start] = f"{block}:"  # e.g. "apps: {}" becomes a block
    indent = _child_indent(lines, start, end)
    for i in range(start + 1, end):
        if re.match(rf"^{indent}{re.escape(key)}\s*:", lines[i]):
            j = i + 1  # drop the old value's continuation lines (a block list under the key)
            while j < end and (lines[j].startswith(indent + " ") or lines[j].startswith(indent + "-")) \
                    and lines[j].strip():
                j += 1
            lines[i:j] = [f"{indent}{key}: {rendered}"]
            return
    lines.insert(end, f"{indent}{key}: {rendered}")


def _get_child(lines: list[str], block: str, key: str) -> str | None:
    blocks = _blocks(lines)
    if block not in blocks:
        return None
    start, end = blocks[block]
    indent = _child_indent(lines, start, end)
    for i in range(start + 1, end):
        m = re.match(rf"^{indent}{re.escape(key)}\s*:\s*(.*?)\s*(#.*)?$", lines[i])
        if m:
            val = m.group(1)
            if not val:  # a block list below
                items = []
                for ln in lines[i + 1:end]:
                    mm = re.match(r"^\s+-\s*(\S+)", ln)
                    if not mm:
                        break
                    items.append(mm.group(1))
                return f"[{', '.join(items)}]" if items else ""
            return val.strip("\"'")
    return None


def read_settings(text: str) -> dict:
    """The museum.yaml values the dashboard cares about."""
    lines = text.splitlines()
    apps = {name: v for name, key in YAML_APPS.items() if (v := _get_child(lines, "apps", key))}
    admins_raw = _get_child(lines, "internal", "admins") or ""
    admins = [int(x) for x in re.findall(r"\d+", admins_raw)]
    reg = (_get_child(lines, "internal", "disable-registration") or "").lower() == "true"
    return {"apps": apps, "admins": admins, "disable_registration": reg}


def propose(text: str, apps: dict[str, str] | None = None, admins: list[int] | None = None,
            disable_registration: bool | None = None) -> str:
    lines = text.splitlines()
    for name, url in (apps or {}).items():
        _set_child(lines, "apps", YAML_APPS[name], url)
    if admins is not None:
        _set_child(lines, "internal", "admins", None, list_value=sorted(set(admins)))
    if disable_registration is not None:
        _set_child(lines, "internal", "disable-registration", "true" if disable_registration else "false")
    return "\n".join(lines) + "\n"


def snippet(apps: dict[str, str] | None = None, admins: list[int] | None = None,
            disable_registration: bool | None = None) -> str:
    return propose("", apps, admins, disable_registration).lstrip("\n")


class Ops:
    def __init__(self, cfg, dash, audit):
        self.cfg, self.dash, self.audit = cfg, dash, audit
        self.instances = {i.name: i for i in cfg.instances}
        self.sources = {s.name: s for s in dash.sources}
        self._probes: dict[str, tuple[float, list]] = {}
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    # ---- helpers ----
    def _inst(self, name: str):
        inst = self.instances.get(name)
        if not inst:
            raise OpsError(404, "unknown instance")
        return inst

    def _pg(self, name: str):
        inst = self._inst(name)
        if inst.source != "postgres":
            raise OpsError(409, "this needs the postgres source for this instance (docs/POSTGRES.md)")
        return self.sources[name]

    def _writer(self, name: str):
        inst = self._inst(name)
        if not inst.write_dsn:
            raise OpsError(409, "the write role isn't configured for this instance (WRITE_DSN, docs/POSTGRES.md)")
        return PG(inst.write_dsn, inst.write_password_file, readonly=False)

    def cached(self, key: tuple, ttl: float, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def invalidate(self, name: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k[1] == name]:
                del self._cache[k]

    def _who(self, uid: int | None, emails: dict[int, str | None]) -> dict:
        if uid is None:
            return {"label": None, "email": None}
        e = emails.get(uid)
        return {"label": self.dash.label(uid), "email": e if self.cfg.reveal_emails else mask_email(e)}

    def _emails(self, cur, src, uids) -> dict[int, str | None]:
        if not uids:
            return {}
        cur.execute("select user_id, encrypted_email, email_decryption_nonce from users where user_id = any(%s)",
                    (sorted(uids),))
        return {uid: src.email(enc, nonce) for uid, enc, nonce in cur.fetchall()}

    # ---- public links ----
    def links(self, name: str) -> dict:
        return self.cached(("links", name), 30, lambda: self._links(name))

    def _links(self, name: str) -> dict:
        src = self._pg(name)
        f = Features()
        now_us = time.time() * 1e6
        out = []
        with src.connect() as conn, conn.cursor() as cur:
            rows = f.run(cur, "links", """
                select t.id::text, c.owner_id, c.app::text, t.is_disabled, t.valid_till, t.device_limit, t.created_at,
                       t.enable_download, t.enable_collect, t.enable_join
                from public_collection_tokens t join collections c on c.collection_id = t.collection_id
                order by t.created_at desc""") or []
            for lid, owner, app, disabled, till, dev, created, dl, collect, join in rows:
                out.append({"kind": "album", "id": lid, "owner": owner, "app": app, "disabled": disabled,
                            "valid_till": _sec(till), "device_limit": dev, "created": _sec(created),
                            "download": dl, "collect": collect, "join": join})
            rows = f.run(cur, "links", """
                select id, owner_id, app, is_disabled, valid_till, device_limit, created_at, enable_download
                from public_file_tokens order by created_at desc""") or []
            for lid, owner, app, disabled, till, dev, created, dl in rows:
                out.append({"kind": "file", "id": lid, "owner": owner, "app": app or "photos", "disabled": disabled,
                            "valid_till": _sec(till), "device_limit": dev, "created": _sec(created),
                            "download": dl, "collect": None, "join": None})
            emails = self._emails(cur, src, {r["owner"] for r in out})
        for r in out:
            r.update(self._who(r.pop("owner"), emails))
            expired = bool(r["valid_till"]) and r["valid_till"] * 1e6 < now_us
            r["state"] = "revoked" if r["disabled"] else "expired" if expired else "active"
        return {"links": out, "features": f.public(), "can_revoke": bool(self._inst(name).write_dsn)}

    def revoke_link(self, name: str, kind: str, link_id: str, actor: str) -> dict:
        """Disable one link, like its owner's "remove link" would. Takes effect for viewers
        within a minute (museum caches link lookups)."""
        table = {"album": "public_collection_tokens", "file": "public_file_tokens"}.get(kind)
        if not table or not re.fullmatch(r"[\w-]{1,64}", link_id or ""):
            raise OpsError(400, "unknown link")
        before = next((r for r in self._links(name)["links"] if r["kind"] == kind and r["id"] == link_id), None)
        if not before:
            raise OpsError(404, "unknown link")
        if before["disabled"]:
            raise OpsError(409, "that link is already revoked")
        audit = {"actor": actor, "instance": name, "action": "revoke-link", "params": {"kind": kind, "id": link_id},
                 "account_email": before["email"], "before": {"state": before["state"], "app": before["app"]}}
        try:
            with self._writer(name).connect() as conn, conn.cursor() as cur:
                cur.execute(f"update {table} set is_disabled = true where id = %s and not is_disabled",  # noqa: S608 - table from a fixed map
                            (int(link_id) if kind == "album" else link_id,))
                n = cur.rowcount
        except Exception as e:  # noqa: BLE001 - reported
            msg = "the write role lacks the grant (docs/POSTGRES.md)" if missing_privilege(e) else \
                f"{type(e).__name__}: {e}".splitlines()[0]
            self.audit.add(**audit, result="error", error=msg)
            raise OpsError(502, msg) from None
        self.invalidate(name)
        self.audit.add(**audit, result="ok" if n else "error", after={"state": "revoked"} if n else None,
                       error=None if n else "no change")
        log.warning("ops: %s revoked %s link %s on %s", actor, kind, link_id, name)
        return {"ok": bool(n), "note": "Viewers lose access within a minute (museum caches links)."}

    # ---- devices ----
    def sessions(self, name: str, uid: int) -> list[dict]:
        src = self._pg(name)
        with src.connect() as conn, conn.cursor() as cur:
            cur.execute("""select creation_time, app::text, user_agent, last_used_at from tokens
                           where user_id = %s and not coalesce(is_deleted, false) order by last_used_at desc""",
                        (uid,))
            rows = cur.fetchall()
        from .sources.model import device_name
        return [{"id": str(created), "signed_in": _sec(created), "app": app, "device": device_name(ua),
                 "last_seen": _sec(last)} for created, app, ua, last in rows]

    def sign_out(self, name: str, uid: int, session_id: str, actor: str, email: str | None) -> dict:
        if not (session_id or "").isdigit():
            raise OpsError(400, "unknown session")
        cur_sessions = {s["id"]: s for s in self.sessions(name, uid)}
        before = cur_sessions.get(session_id)
        if not before:
            raise OpsError(404, "unknown or already signed-out session")
        audit = {"actor": actor, "instance": name, "action": "sign-out-device", "account_id": uid,
                 "account_email": email, "params": {"session": session_id},
                 "before": {k: before[k] for k in ("app", "device", "signed_in", "last_seen")}}
        try:
            with self._writer(name).connect() as conn, conn.cursor() as cur:
                cur.execute("update tokens set is_deleted = true where user_id = %s and creation_time = %s "
                            "and not coalesce(is_deleted, false)", (uid, int(session_id)))
                n = cur.rowcount
        except Exception as e:  # noqa: BLE001 - reported
            msg = "the write role lacks the grant (docs/POSTGRES.md)" if missing_privilege(e) else \
                f"{type(e).__name__}: {e}".splitlines()[0]
            self.audit.add(**audit, result="error", error=msg)
            raise OpsError(502, msg) from None
        self.audit.add(**audit, result="ok" if n else "error", error=None if n else "no change")
        threading.Thread(target=self.dash.refresh, daemon=True).start()
        log.warning("ops: %s signed out a %s session of %s on %s", actor, before["app"], uid, name)
        return {"ok": bool(n), "note": "The device is signed out within a minute (museum caches sessions)."}

    # ---- storage and backups ----
    def storage(self, name: str) -> dict:
        return self.cached(("storage", name), 300, lambda: self._storage(name))

    def _storage(self, name: str) -> dict:
        src = self._pg(name)
        f = Features()
        with src.connect() as conn, conn.cursor() as cur:
            by_dc = f.run(cur, "storage", """
                select dc, coalesce(f.app::text, 'photos'), count(*), coalesce(sum(ok.size), 0)::bigint
                from object_keys ok cross join lateral unnest(ok.datacenters) dc
                left join files f on f.file_id = ok.file_id
                where not ok.is_deleted group by 1, 2 order by 1, 2""")
            copies = f.run(cur, "storage", """
                select coalesce(cardinality(datacenters), 0), count(*), coalesce(sum(size), 0)::bigint
                from object_keys where not is_deleted group by 1 order by 1""")
        dcs: dict[str, dict] = {}
        for dc, app, n, size in by_dc or ():
            d = dcs.setdefault(dc, {"datacenter": dc, "objects": 0, "bytes": 0, "apps": {}})
            d["objects"] += n
            d["bytes"] += size
            d["apps"][app] = {"objects": n, "bytes": size}
        copies = [{"copies": c, "objects": n, "bytes": s} for c, n, s in copies or ()]
        most = max((c["copies"] for c in copies), default=0)
        return {"datacenters": list(dcs.values()), "copies": copies,
                "under_replicated": sum(c["objects"] for c in copies if c["copies"] < most),
                "features": f.public()}

    def backup_checks(self, name: str) -> list[dict]:
        return [e for e in self.audit.recent(1000, name) if e["action"] == "backup-check"][:50]

    def record_backup_check(self, name: str, actor: str, what: str, result: str, note: str) -> dict:
        self._inst(name)
        if result not in ("ok", "problem") or not (1 <= len(what) <= 100) or len(note) > 500:
            raise OpsError(400, "describe what was checked (1-100 characters) and the result")
        self.audit.add(actor, name, "backup-check", "ok", params={"what": what, "result": result, "note": note})
        return {"ok": True}

    # ---- web apps and museum.yaml ----
    def _config_text(self, inst) -> str | None:
        if not inst.museum_config_file:
            return None
        try:
            return Path(inst.museum_config_file).read_text(encoding="utf-8")
        except OSError as e:
            log.warning("ops: %s: can't read museum.yaml: %s", inst.name, e)
            return None

    @staticmethod
    def _probe(url: str) -> dict:
        t0 = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": "ente-admin"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 - operator-configured URL
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"ok": False, "error": str(getattr(e, "reason", e))[:200]}
        return {"ok": code < 400, "status": code, "ms": int((time.monotonic() - t0) * 1000)}

    def apps(self, name: str, force: bool = False) -> dict:
        inst = self._inst(name)
        text = self._config_text(inst)
        museum = read_settings(text)["apps"] if text is not None else {}
        cached = self._probes.get(name)
        if cached and not force and time.time() - cached[0] < PROBE_TTL_S:
            rows = cached[1]
        else:
            from .config import WEB_APPS
            rows = []
            for app in WEB_APPS:
                url = inst.apps.get(app) or museum.get(app)
                rows.append({"app": app, "about": APP_INFO.get(app, ""), "url": url or "",
                             "yaml_key": YAML_APPS.get(app), "in_museum_yaml": museum.get(app),
                             "probe": self._probe(url) if url else None})
            self._probes[name] = (time.time(), rows)
        locker = next(r for r in rows if r["app"] == "locker")
        share = next(r for r in rows if r["app"] == "share")
        return {"apps": rows, "config_file": bool(inst.museum_config_file), "config_readable": text is not None,
                "locker": {"web_app": bool(locker["url"]), "answers": bool(locker["probe"] and locker["probe"]["ok"]),
                           "share_links_url": share["in_museum_yaml"] or (share["url"] if text is None else None),
                           "share_app_answers": bool(share["probe"] and share["probe"]["ok"])}}

    def config(self, name: str) -> dict:
        """What museum.yaml says about apps and admins (if the file is mounted)."""
        inst = self._inst(name)
        text = self._config_text(inst)
        if text is None:
            return {"readable": False, "file": bool(inst.museum_config_file)}
        return {"readable": True, "file": True, **read_settings(text)}

    def propose_config(self, name: str, actor: str, apps: dict | None, admins: list | None,
                       disable_registration: bool | None) -> dict:
        """A museum.yaml change for the operator to apply: the snippet, and with the file
        mounted a diff against it (secrets redacted). Nothing is written."""
        inst = self._inst(name)
        apps = {k: str(v).strip() for k, v in (apps or {}).items() if str(v).strip()}
        bad = [k for k in apps if k not in YAML_APPS]
        if bad:
            raise OpsError(400, f"not a museum.yaml app: {', '.join(bad)}")
        for v in apps.values():
            if not re.fullmatch(r"https?://[\w.\-:\[\]]+(/[\w./-]*)?", v):
                raise OpsError(400, f"not a URL: {v}")
        if admins is not None:
            if not isinstance(admins, list) or not all(str(a).isdigit() for a in admins) or len(admins) > 20:
                raise OpsError(400, "admins must be a list of account ids")
            admins = [int(a) for a in admins]
        if disable_registration is not None and not isinstance(disable_registration, bool):
            raise OpsError(400, "disable_registration must be true or false")
        if not apps and admins is None and disable_registration is None:
            raise OpsError(400, "nothing to change")
        text = self._config_text(inst)
        out = {"snippet": snippet(apps, admins, disable_registration), "diff": None,
               "steps": ["Edit museum.yaml where you keep it (file, secret or vault) with the change below.",
                         "Restart museum (it reads museum.yaml only at start).",
                         "Come back here and press Verify."]}
        if text is not None:
            after = propose(text, apps, admins, disable_registration)
            out["diff"] = "".join(difflib.unified_diff(redact(text).splitlines(True), redact(after).splitlines(True),
                                                       "museum.yaml (now)", "museum.yaml (proposed)", n=2))
            if not out["diff"]:
                out["diff"] = ""
                out["steps"] = ["museum.yaml already has these values."]
        self.audit.add(actor, name, "museum-config-proposed", "ok",
                       params={"apps": apps, "admins": admins, "disable_registration": disable_registration})
        return out

    def verify_config(self, name: str, actor: str, apps: dict | None, admins: list | None,
                      disable_registration: bool | None) -> dict:
        """After the operator applied a change: museum answers, the apps answer, and (with
        the file mounted) museum.yaml has the values."""
        inst = self._inst(name)
        checks = []
        client = getattr(self.dash.portal, "clients", {}).get(name) if self.dash.portal else None
        if client:
            try:
                client.request("GET", "/ping")
                checks.append({"check": "museum answers /ping", "ok": True})
            except Exception as e:  # noqa: BLE001 - reported
                checks.append({"check": "museum answers /ping", "ok": False, "detail": str(e)[:200]})
        for app, url in (apps or {}).items():
            p = self._probe(str(url))
            checks.append({"check": f"{app} answers at {url}", "ok": p["ok"], "detail": p.get("error") or
                           f"HTTP {p.get('status')}"})
        text = self._config_text(inst)
        if text is not None:
            now = read_settings(text)
            for app, url in (apps or {}).items():
                checks.append({"check": f"museum.yaml apps.{YAML_APPS.get(app, app)}",
                               "ok": now["apps"].get(app) == url, "detail": now["apps"].get(app) or "not set"})
            if admins is not None:
                checks.append({"check": "museum.yaml internal.admins",
                               "ok": sorted(now["admins"]) == sorted(int(a) for a in admins),
                               "detail": ", ".join(map(str, now["admins"])) or "not set"})
            if disable_registration is not None:
                checks.append({"check": "museum.yaml internal.disable-registration",
                               "ok": now["disable_registration"] == disable_registration,
                               "detail": str(now["disable_registration"]).lower()})
        else:
            checks.append({"check": "museum.yaml values", "ok": None,
                           "detail": "museum.yaml isn't mounted (MUSEUM_CONFIG_FILE): check it by hand"})
        ok = all(c["ok"] is not False for c in checks)
        self._probes.pop(name, None)
        self.audit.add(actor, name, "museum-config-verified", "ok" if ok else "error",
                       params={"apps": apps, "admins": admins, "disable_registration": disable_registration},
                       after={"checks": checks}, error=None if ok else "some checks failed")
        return {"ok": ok, "checks": checks}

    # ---- setup checklist ----
    def setup(self, name: str) -> dict:
        inst = self._inst(name)
        snap = next((s for s in self.dash.data() if s["instance"] == name), {})
        items = [{"item": "Data source", "ok": not snap.get("error"), "detail": snap.get("error") or inst.source}]
        feats = dict(snap.get("features") or {})
        if inst.source == "postgres" and not snap.get("error"):
            feats.update(self.cached(("probes", name), 120, lambda: self._feature_probes(name)))
        if inst.source == "postgres":
            for feat, grant in GRANTS.items():
                st = feats.get(feat)
                items.append({"item": f"Database: {feat}", "ok": None if st is None else st["ok"],
                              "detail": None if not st or st["ok"] else st["reason"],
                              "fix": None if st and st["ok"] else grant})
            items.append({"item": "Emails shown", "ok": bool(inst.email_key_file),
                          "detail": None if inst.email_key_file else "set EMAIL_KEY_FILE (museum's key.encryption)"})
            w = self._write_status(inst)
            items.append({"item": "Write role (link revoke, device sign-out)", "ok": w["ok"], "detail": w["detail"],
                          "fix": None if w["ok"] else WRITE_GRANTS})
        else:
            items.append({"item": "Database views (Locker, links, sharing, notifications)", "ok": None,
                          "detail": "need the postgres source (docs/POSTGRES.md)"})
        items.append({"item": "museum.yaml mounted (read-only)", "ok": bool(self._config_text(inst)) if
                      inst.museum_config_file else None,
                      "detail": None if inst.museum_config_file else "optional: MUSEUM_CONFIG_FILE, for config diffs"})
        items.append({"item": "Admin portal", "ok": bool(self.cfg.enable_actions),
                      "detail": None if self.cfg.enable_actions else "ENTE_ADMIN_ENABLE_ACTIONS=true"})
        return {"instance": name, "items": items}

    def _feature_probes(self, name: str) -> dict:
        f = Features()
        try:
            with self.sources[name].connect() as conn, conn.cursor() as cur:
                for feat, sql in FEATURE_PROBES.items():
                    f.run(cur, feat, sql)
        except Exception as e:  # noqa: BLE001 - the data source item shows connection problems
            log.info("ops: %s: feature probes: %s", name, e)
        return f.public()

    def _write_status(self, inst) -> dict:
        if not inst.write_dsn:
            return {"ok": None, "detail": "optional, off (WRITE_DSN)"}

        def check():
            try:
                with PG(inst.write_dsn, inst.write_password_file, readonly=False).connect() as conn, \
                        conn.cursor() as cur:
                    cur.execute("""select has_column_privilege('public_collection_tokens', 'is_disabled', 'UPDATE'),
                                          has_column_privilege('public_file_tokens', 'is_disabled', 'UPDATE'),
                                          has_column_privilege('tokens', 'is_deleted', 'UPDATE')""")
                    a, b, c = cur.fetchone()
                ok = a and b and c
                return {"ok": ok, "detail": None if ok else "connected, but some grants are missing"}
            except Exception as e:  # noqa: BLE001 - reported
                return {"ok": False, "detail": f"{type(e).__name__}: {e}".splitlines()[0][:200]}
        return self.cached(("write", inst.name), 120, check)
