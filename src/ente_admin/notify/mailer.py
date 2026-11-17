"""Sending email over SMTP, and checking that the SMTP server answers.

Settings come from ENTE_ADMIN_SMTP_* (and per-instance overrides, config.smtp_for):
host, port, security (starttls | ssl | none), username, password, from, from_name. The
health check connects, says EHLO, upgrades to TLS and logs in, without sending anything;
its result is cached for a few minutes. Passwords never leave this module.
"""
from __future__ import annotations

import smtplib
import ssl
import threading
import time
from email.utils import formataddr

HEALTH_TTL_S = 300


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, settings: dict[str, str]):
        self.s = settings
        self._health: dict | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.s.get("host") and self.s.get("from"))

    def public(self) -> dict:
        """What the UI may show: never the username or password."""
        return {"configured": self.configured, "host": self.s.get("host", ""), "port": self.s.get("port", ""),
                "security": self.s.get("security", ""), "from": self.s.get("from", ""),
                "from_name": self.s.get("from_name", ""), "auth": bool(self.s.get("username"))}

    @property
    def sender(self) -> str:
        return formataddr((self.s.get("from_name") or "", self.s["from"]))

    def _connect(self) -> smtplib.SMTP:
        host, port, sec = self.s["host"], int(self.s.get("port") or 587), self.s.get("security") or "starttls"
        ctx = ssl.create_default_context()
        if sec == "ssl":
            conn = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            conn = smtplib.SMTP(host, port, timeout=20)
            conn.ehlo()
            if sec == "starttls":
                conn.starttls(context=ctx)
                conn.ehlo()
        if self.s.get("username"):
            conn.login(self.s["username"], self.s.get("password", ""))
        return conn

    def send(self, msg) -> None:
        if not self.configured:
            raise MailError("SMTP isn't configured (ENTE_ADMIN_SMTP_HOST and ENTE_ADMIN_SMTP_FROM)")
        try:
            with self._connect() as conn:
                conn.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"{type(e).__name__}: {e}") from None

    def health(self, force: bool = False) -> dict:
        if not self.configured:
            return {"ok": False, "configured": False, "error": "not configured", "at": time.time()}
        with self._lock:
            if self._health and not force and time.time() - self._health["at"] < HEALTH_TTL_S:
                return self._health
        t0 = time.monotonic()
        try:
            with self._connect() as conn:
                conn.noop()
            res = {"ok": True, "configured": True, "ms": int((time.monotonic() - t0) * 1000), "at": time.time()}
        except (smtplib.SMTPException, OSError) as e:
            res = {"ok": False, "configured": True, "error": f"{type(e).__name__}: {e}"[:300], "at": time.time()}
        with self._lock:
            self._health = res
        return res
