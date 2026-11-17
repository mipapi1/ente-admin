"""Synthetic data, for development, tests and screenshots. Deterministic per instance
name, so screenshots are reproducible. No real instance is contacted."""
from __future__ import annotations

import random
import time
from datetime import date, timedelta

from ..config import Instance
from . import register
from .model import DAYS, Account, Device, Snapshot

GiB = 1024 ** 3
FIRST = ["alex", "sam", "jordan", "casey", "riley", "morgan", "taylor", "jamie", "drew", "quinn", "avery", "rowan"]
DEVICES = [("iPhone 15 Pro · iOS 18.1 · Ente 0.9.95", "photos"), ("Pixel 8 · Android 15 · Ente 1.0.2", "photos"),
           ("Ente Desktop 1.7.14 · macOS", "photos"), ("Web (Safari · macOS)", "photos"),
           ("iPhone 13 · iOS 17.6 · Ente 4.4.1", "auth"), ("Ente Desktop 1.7.14 · Windows", "photos"),
           ("Web (Firefox · Linux)", "photos")]


@register("demo")
class DemoSource:
    kind = "demo"

    def __init__(self, instance: Instance):
        self.name = instance.name
        self.url = instance.url or f"https://{instance.name}.example.com"

    def snapshot(self, now: float | None = None) -> Snapshot:
        now = now or time.time()
        rnd = random.Random(self.name)  # noqa: S311 - synthetic data, not security
        n = rnd.randint(5, 9) if self.name != "family" else rnd.randint(3, 5)
        accounts = []
        for i in range(n):
            first = FIRST[(i + len(self.name)) % len(FIRST)]
            quota = rnd.choice([50, 100, 200, 1024]) * GiB
            used = int(quota * rnd.uniform(0.05, 0.95))
            files = int(used / rnd.uniform(2.5, 6.0) / 1024 ** 2)
            daily = [max(0, int(rnd.gauss(files / 900, files / 1200))) for _ in range(DAYS)]
            if rnd.random() < 0.3:
                daily[-1] += rnd.randint(40, 200)
            devices = []
            for _ in range(rnd.randint(1, 4)):
                dev, app = rnd.choice(DEVICES)
                signed = int(now - rnd.uniform(5, 500) * 86400)
                devices.append(Device(device=dev, app=app, signed_in=signed,
                                      last_seen=int(now - rnd.uniform(0, 72) * 3600)))
            devices.sort(key=lambda d: d.last_seen or 0, reverse=True)
            uploading = daily[-1] > 30 and rnd.random() < 0.5
            avg_size = rnd.uniform(2.5, 6.0) * 1024 ** 2
            accounts.append(Account(
                id=1_000_000_000 + rnd.randint(0, 10 ** 9),
                email=f"{first}@example.com",
                created=int(now - rnd.uniform(30, 900) * 86400),
                quota=quota, used=used, files=files, file_bytes=int(used * 0.97),
                albums=rnd.randint(2, 60), trash=rnd.randint(0, 120),
                two_factor=rnd.random() < 0.6, email_mfa=rnd.random() < 0.3,
                plan_expiry=int(now + rnd.uniform(-20, 3000) * 86400),
                last_upload=int(now - (rnd.uniform(0, 0.1) if uploading else rnd.uniform(0.2, 240)) * 3600),
                last_seen=devices[0].last_seen if devices else None,
                uploading=uploading, recent_uploads=rnd.randint(3, 40) if uploading else 0,
                daily_uploads=daily, daily_bytes=[int(v * avg_size) for v in daily], devices=devices))
        today = date.fromtimestamp(now)
        days = [(today - timedelta(days=DAYS - 1 - k)).isoformat() for k in range(DAYS)]
        return Snapshot(instance=self.name, source=self.kind, url=self.url, generated_at=int(now),
                        accounts=accounts, days=days, notes=["Demo data: synthetic accounts, no real instance."])
