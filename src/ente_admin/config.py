"""Configuration from environment variables, optionally layered over a TOML file.

Precedence: environment > config file (ENTE_ADMIN_CONFIG) > defaults. Every setting is
documented in docs/CONFIG.md. Values ending in _FILE are read from that file (for
secrets mounted by Docker or Kubernetes).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ALL_MODULES = ("overview", "ticker", "accounts", "devices", "activity", "locker", "notifications")
AUTH_MODES = ("basic", "header", "none")
NOTIFY_MODES = ("off", "shadow", "send")
SMTP_KEYS = ("host", "port", "security", "username", "password", "from", "from_name")
SMTP_SECURITY = ("starttls", "ssl", "none")
WEB_APPS = ("photos", "accounts", "albums", "auth", "cast", "share", "embed", "paste", "locker", "memories", "legacy")


class ConfigError(ValueError):
    pass


@dataclass
class Instance:
    name: str
    source: str  # demo | museum | postgres
    url: str = ""  # public museum URL (museum source: API base)
    api_url: str = ""  # portal: museum URL for admin calls, e.g. in-cluster (default: url)
    token_file: str = ""  # museum: admin auth token
    fresh_token_file: str = ""  # museum: optional, a token < 10 min old to refresh the account list
    dsn: str = ""  # postgres: libpq connection string without password
    password_file: str = ""  # postgres
    email_key_file: str = ""  # postgres: museum's key.encryption, to decrypt emails (optional)
    state_dir: str = ""  # set from Config.state_dir at startup
    # optional, all off unless set
    write_dsn: str = ""  # opt-in write role (share-link revoke, sign out a device), docs/POSTGRES.md
    write_password_file: str = ""
    apps: dict[str, str] = field(default_factory=dict)  # web app -> public URL, to check they answer
    museum_config_file: str = ""  # museum.yaml, read-only: show apps/admins and build config diffs
    notify: str = ""  # notifications for this instance: off | shadow | send (default: ENTE_ADMIN_NOTIFY)
    smtp: dict[str, str] = field(default_factory=dict)  # per-instance overrides of the SMTP settings


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8000
    auth: str = "basic"
    users: dict[str, str] = field(default_factory=dict)  # username -> pbkdf2 hash
    trusted_header: str = "Remote-User"
    trusted_proxies: list[str] = field(default_factory=list)
    insecure_localhost: bool = False
    modules: tuple[str, ...] = ALL_MODULES
    reveal_emails: bool = False
    enable_actions: bool = False
    refresh_seconds: int = 300
    state_dir: str = "state"
    instances: list[Instance] = field(default_factory=list)
    # header auth: more headers from the login proxy, and who counts as an admin
    groups_header: str = "Remote-Groups"
    email_header: str = "Remote-Email"
    name_header: str = "Remote-Name"
    admin_groups: list[str] = field(default_factory=list)  # empty: every user is an admin
    logout_url: str = ""
    sharing: bool = False  # admins share accounts with other users (needs admin_groups)
    directory_file: str = ""  # JSON list of {user, name, groups, disabled} to pick from when sharing
    share_groups: list[str] = field(default_factory=list)  # only offer directory users in these groups
    branding_dir: str = ""  # folder with branding.json and its images
    # admin portal (enable_actions)
    admin_idle_minutes: int = 15  # an unlocked instance locks after this long without use
    admin_max_minutes: int = 60  # ... and after this long in total
    kdf_max_mib: int = 512  # largest password key derivation (argon2 memory) the portal will run
    mail_from: str = ""  # sender address for "Send a test email"
    # email notifications (docs/NOTIFICATIONS.md)
    notify: str = "off"  # default mode for every instance: off | shadow | send
    notify_interval: int = 60  # seconds between checks
    templates_dir: str = ""  # your own email templates and images, over the built-in ones
    smtp: dict[str, str] = field(default_factory=dict)  # host, port, security, username, password, from, from_name


def _env(name: str, env: dict[str, str]) -> str | None:
    """ENTE_ADMIN_<name>, or the content of ENTE_ADMIN_<name>_FILE."""
    key = f"ENTE_ADMIN_{name}"
    if key in env:
        return env[key]
    path = env.get(key + "_FILE")
    if path:
        return Path(path).read_text().strip()
    return None


def _bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    raise ConfigError(f"not a boolean: {value!r}")


def _parse_users(value: str) -> dict[str, str]:
    """`alice:pbkdf2_sha256$...,bob:...` (one per line or comma separated)."""
    users = {}
    for item in value.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, hashed = item.partition(":")
        if not sep or not name or not hashed.startswith("pbkdf2_sha256$"):
            raise ConfigError("ENTE_ADMIN_USERS entries must be `name:pbkdf2_sha256$...` "
                              "(see `ente-admin hash-password`)")
        users[name] = hashed
    return users


def _pairs(value: str, what: str) -> dict[str, str]:
    """`photos=https://...,locker=https://...`"""
    out = {}
    for item in value.replace("\n", ",").split(","):
        if item.strip():
            k, sep, v = item.partition("=")
            if not sep or not k.strip() or not v.strip():
                raise ConfigError(f"{what}: expected name=value pairs, got {item.strip()!r}")
            out[k.strip().lower()] = v.strip()
    return out


def _smtp_from_env(prefix: str, env: dict[str, str]) -> dict[str, str]:
    """<prefix>SMTP_HOST, _PORT, _SECURITY, _USERNAME, _PASSWORD (or _PASSWORD_FILE), _FROM, _FROM_NAME"""
    out = {}
    for k in SMTP_KEYS:
        key = f"{prefix}SMTP_{k.upper()}"
        if key in env:
            out[k] = env[key]
        elif env.get(key + "_FILE"):
            out[k] = Path(env[key + "_FILE"]).read_text().strip()
    return out


def _instances_from_env(env: dict[str, str]) -> list[Instance]:
    """ENTE_ADMIN_INSTANCES=photos,family plus ENTE_ADMIN_<NAME>_SOURCE, _URL, ..."""
    names = [n.strip() for n in (env.get("ENTE_ADMIN_INSTANCES") or "").split(",") if n.strip()]
    out = []
    for n in names:
        p = f"ENTE_ADMIN_{n.upper().replace('-', '_')}_"
        out.append(Instance(
            name=n,
            source=env.get(p + "SOURCE", "museum"),
            url=env.get(p + "URL", ""),
            api_url=env.get(p + "API_URL", ""),
            token_file=env.get(p + "TOKEN_FILE", ""),
            fresh_token_file=env.get(p + "FRESH_TOKEN_FILE", ""),
            dsn=env.get(p + "DSN", ""),
            password_file=env.get(p + "PASSWORD_FILE", ""),
            email_key_file=env.get(p + "EMAIL_KEY_FILE", ""),
            write_dsn=env.get(p + "WRITE_DSN", ""),
            write_password_file=env.get(p + "WRITE_PASSWORD_FILE", ""),
            apps=_pairs(env.get(p + "APPS", ""), p + "APPS"),
            museum_config_file=env.get(p + "MUSEUM_CONFIG_FILE", ""),
            notify=env.get(p + "NOTIFY", ""),
            smtp=_smtp_from_env(p, env),
        ))
    return out


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    cfg = Config()

    path = env.get("ENTE_ADMIN_CONFIG")
    if path:
        data = tomllib.loads(Path(path).read_text())
        for key in ("host", "port", "auth", "trusted_header", "insecure_localhost", "reveal_emails",
                    "enable_actions", "refresh_seconds", "state_dir", "groups_header", "email_header",
                    "name_header", "logout_url", "sharing", "directory_file", "branding_dir",
                    "admin_idle_minutes", "admin_max_minutes", "kdf_max_mib", "mail_from", "notify",
                    "notify_interval", "templates_dir"):
            if key in data:
                setattr(cfg, key, data[key])
        for key in ("trusted_proxies", "admin_groups", "share_groups"):
            if key in data:
                setattr(cfg, key, list(data[key]))
        if "modules" in data:
            cfg.modules = tuple(data["modules"])
        if "users" in data:
            cfg.users = dict(data["users"])
        if "smtp" in data:
            cfg.smtp = {k: str(v) for k, v in data["smtp"].items()}
        cfg.instances = [Instance(**i) for i in data.get("instances", [])]
        for i in cfg.instances:
            i.smtp = {k: str(v) for k, v in i.smtp.items()}

    simple = {"HOST": ("host", str), "PORT": ("port", int), "AUTH": ("auth", str),
              "TRUSTED_HEADER": ("trusted_header", str), "REFRESH_SECONDS": ("refresh_seconds", int),
              "STATE_DIR": ("state_dir", str), "INSECURE_LOCALHOST": ("insecure_localhost", _bool),
              "REVEAL_EMAILS": ("reveal_emails", _bool), "ENABLE_ACTIONS": ("enable_actions", _bool),
              "GROUPS_HEADER": ("groups_header", str), "EMAIL_HEADER": ("email_header", str),
              "NAME_HEADER": ("name_header", str), "LOGOUT_URL": ("logout_url", str),
              "SHARING": ("sharing", _bool), "DIRECTORY_FILE": ("directory_file", str),
              "BRANDING_DIR": ("branding_dir", str), "ADMIN_IDLE_MINUTES": ("admin_idle_minutes", int),
              "ADMIN_MAX_MINUTES": ("admin_max_minutes", int), "KDF_MAX_MIB": ("kdf_max_mib", int),
              "MAIL_FROM": ("mail_from", str), "NOTIFY": ("notify", str),
              "NOTIFY_INTERVAL": ("notify_interval", int), "TEMPLATES_DIR": ("templates_dir", str)}
    for name, (attr, conv) in simple.items():
        v = _env(name, env)
        if v is not None:
            setattr(cfg, attr, conv(v))
    for name, attr in (("TRUSTED_PROXIES", "trusted_proxies"), ("ADMIN_GROUPS", "admin_groups"),
                       ("SHARE_GROUPS", "share_groups")):
        if (v := _env(name, env)) is not None:
            setattr(cfg, attr, [p.strip() for p in v.split(",") if p.strip()])
    if (v := _env("MODULES", env)) is not None:
        cfg.modules = tuple(m.strip() for m in v.split(",") if m.strip())
    if (v := _env("USERS", env)) is not None:
        cfg.users = _parse_users(v)
    cfg.smtp = {**cfg.smtp, **_smtp_from_env("ENTE_ADMIN_", env)}
    if env.get("ENTE_ADMIN_INSTANCES"):
        cfg.instances = _instances_from_env(env)
    if env.get("ENTE_ADMIN_DEMO") and _bool(env["ENTE_ADMIN_DEMO"]):
        cfg.instances = [Instance(name="photos", source="demo"), Instance(name="family", source="demo")]

    for i in cfg.instances:
        i.state_dir = i.state_dir or cfg.state_dir
        i.notify = i.notify or cfg.notify
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.auth not in AUTH_MODES:
        raise ConfigError(f"ENTE_ADMIN_AUTH must be one of {', '.join(AUTH_MODES)}")
    unknown = set(cfg.modules) - set(ALL_MODULES)
    if unknown:
        raise ConfigError(f"unknown modules: {', '.join(sorted(unknown))}")
    if cfg.auth == "basic" and not cfg.users:
        raise ConfigError("authentication is required: set ENTE_ADMIN_USERS (see `ente-admin hash-password`), "
                          "or use ENTE_ADMIN_AUTH=header behind a login proxy")
    if cfg.auth == "header" and not cfg.trusted_proxies:
        raise ConfigError("ENTE_ADMIN_AUTH=header needs ENTE_ADMIN_TRUSTED_PROXIES (the proxy's addresses), "
                          "otherwise anyone could send the header")
    if cfg.auth == "none":
        if not cfg.insecure_localhost:
            raise ConfigError("ENTE_ADMIN_AUTH=none also needs ENTE_ADMIN_INSECURE_LOCALHOST=true")
        if cfg.host not in ("127.0.0.1", "::1", "localhost"):
            raise ConfigError("ENTE_ADMIN_AUTH=none only listens on localhost")
    if cfg.admin_groups and cfg.auth != "header":
        raise ConfigError("ENTE_ADMIN_ADMIN_GROUPS needs ENTE_ADMIN_AUTH=header (groups come from the login proxy)")
    if cfg.sharing and not cfg.admin_groups:
        raise ConfigError("ENTE_ADMIN_SHARING needs ENTE_ADMIN_ADMIN_GROUPS (otherwise everyone sees everything)")
    if cfg.logout_url and not cfg.logout_url.startswith(("https://", "http://", "/")):
        raise ConfigError("ENTE_ADMIN_LOGOUT_URL must be an http(s) URL or a path")
    if cfg.branding_dir and not Path(cfg.branding_dir, "branding.json").is_file():
        raise ConfigError(f"ENTE_ADMIN_BRANDING_DIR: no branding.json in {cfg.branding_dir}")
    if cfg.enable_actions:
        if cfg.auth == "none":
            raise ConfigError("ENTE_ADMIN_ENABLE_ACTIONS needs a login (basic or header auth)")
        if not 1 <= cfg.admin_idle_minutes <= cfg.admin_max_minutes <= 24 * 60:
            raise ConfigError("ENTE_ADMIN_ADMIN_IDLE_MINUTES must be >= 1 and <= ENTE_ADMIN_ADMIN_MAX_MINUTES "
                              "(<= 1440)")
        if not 64 <= cfg.kdf_max_mib <= 4096:
            raise ConfigError("ENTE_ADMIN_KDF_MAX_MIB must be between 64 and 4096")
        if cfg.mail_from and "@" not in cfg.mail_from:
            raise ConfigError("ENTE_ADMIN_MAIL_FROM must be an email address")
    if cfg.notify not in NOTIFY_MODES:
        raise ConfigError(f"ENTE_ADMIN_NOTIFY must be one of {', '.join(NOTIFY_MODES)}")
    if not 15 <= cfg.notify_interval <= 3600:
        raise ConfigError("ENTE_ADMIN_NOTIFY_INTERVAL must be between 15 and 3600 seconds")
    if cfg.templates_dir and not Path(cfg.templates_dir).is_dir():
        raise ConfigError(f"ENTE_ADMIN_TEMPLATES_DIR: {cfg.templates_dir} is not a folder")
    _check_smtp("ENTE_ADMIN_SMTP_*", cfg.smtp)
    if not cfg.instances:
        raise ConfigError("no instances: set ENTE_ADMIN_INSTANCES, a config file, or ENTE_ADMIN_DEMO=true")
    for i in cfg.instances:
        if i.source not in ("demo", "museum", "postgres"):
            raise ConfigError(f"instance {i.name}: unknown source {i.source!r}")
        if i.source == "museum" and not (i.url and i.token_file):
            raise ConfigError(f"instance {i.name}: the museum source needs URL and TOKEN_FILE")
        if i.source == "postgres" and not i.dsn:
            raise ConfigError(f"instance {i.name}: the postgres source needs DSN")
        if i.notify not in NOTIFY_MODES:
            raise ConfigError(f"instance {i.name}: NOTIFY must be one of {', '.join(NOTIFY_MODES)}")
        if i.notify != "off" and i.source not in ("postgres", "demo"):
            raise ConfigError(f"instance {i.name}: notifications need the postgres source (they read activity "
                              "and shares from museum's database)")
        unknown_apps = set(i.apps) - set(WEB_APPS)
        if unknown_apps:
            raise ConfigError(f"instance {i.name}: unknown web app(s) in APPS: {', '.join(sorted(unknown_apps))} "
                              f"(known: {', '.join(WEB_APPS)})")
        for app, url in i.apps.items():
            if not url.startswith(("http://", "https://")):
                raise ConfigError(f"instance {i.name}: APPS {app} must be an http(s) URL")
        if i.write_dsn and i.source != "postgres":
            raise ConfigError(f"instance {i.name}: WRITE_DSN needs the postgres source")
        _check_smtp(f"instance {i.name} SMTP", i.smtp)


def _check_smtp(what: str, smtp: dict[str, str]) -> None:
    if smtp.get("security") and smtp["security"] not in SMTP_SECURITY:
        raise ConfigError(f"{what}: SECURITY must be one of {', '.join(SMTP_SECURITY)}")
    if smtp.get("port") and not smtp["port"].isdigit():
        raise ConfigError(f"{what}: PORT must be a number")
    if smtp.get("from") and "@" not in smtp["from"]:
        raise ConfigError(f"{what}: FROM must be an email address")


def smtp_for(cfg: Config, inst: Instance) -> dict[str, str]:
    """The SMTP settings an instance uses: its own overrides over the global ones. Empty
    `host` means no SMTP is configured."""
    s = {**cfg.smtp, **inst.smtp}
    s.setdefault("security", "starttls" if s.get("port", "587") != "465" else "ssl")
    s.setdefault("port", "465" if s["security"] == "ssl" else "587")
    s.setdefault("from", cfg.mail_from)
    s.setdefault("from_name", "")
    return s
