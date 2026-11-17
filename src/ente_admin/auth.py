"""Who is asking. Three modes:

- basic:  HTTP Basic against PBKDF2 hashes from ENTE_ADMIN_USERS.
- header: a login proxy (Authelia, oauth2-proxy, ...) sets a header with the username;
          trusted only when the request comes from ENTE_ADMIN_TRUSTED_PROXIES.
- none:   no login; config refuses this unless bound to localhost with an explicit flag.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
import secrets

ITERATIONS = 600_000


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        scheme, iterations, salt, expected = hashed.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(dk, base64.b64decode(expected))
    except (ValueError, binascii.Error):
        return False


# Spend the same time on unknown usernames as on known ones
_DUMMY = hash_password(os.urandom(8).hex(), iterations=ITERATIONS)


def check_basic(header: str | None, users: dict[str, str]) -> str | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        name, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    hashed = users.get(name)
    ok = verify_password(password, hashed or _DUMMY)
    return name if ok and hashed else None


def _in(addr: str, networks: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for n in networks:
        try:
            if ip in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def check_header(client_ip: str, value: str | None, proxies: list[str]) -> str | None:
    if not value or not _in(client_ip, proxies):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 128 and value.isprintable() else None
