"""Masking of emails and identifiers. Applied on the server, before anything is sent to
a browser, so masked values never leave the process."""
from __future__ import annotations

import hashlib


def mask_email(email: str | None) -> str | None:
    """alice@example.com -> a••••@e••••.com"""
    if not email:
        return email
    local, sep, domain = email.partition("@")
    if not sep:
        return local[:1] + "••••"
    host, dot, tld = domain.rpartition(".")
    if not dot:
        host, tld = domain, ""
    masked_host = (host[:1] + "••••") if host else ""
    return f"{local[:1]}••••@{masked_host}{'.' + tld if tld else ''}"


def mask_id(value: int | str | None, salt: str) -> str | None:
    """A stable, non-reversible label for an account id (same input -> same label)."""
    if value is None:
        return None
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return "acct-" + digest[:8]
