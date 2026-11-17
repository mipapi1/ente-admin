"""The data every source produces. Fields a source can't provide stay None, and the UI
shows them as unavailable rather than zero.

Ente is end-to-end encrypted: a server (and so this dashboard) only knows account
metadata, sizes and timestamps. No file names, types, dates, places or contents.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

DAYS = 14


@dataclass
class Device:
    device: str  # "iPhone", "Android", "Desktop (macOS)", "Web"...
    app: str  # photos, auth, locker
    signed_in: int | None = None  # unix seconds
    last_seen: int | None = None


@dataclass
class Account:
    id: int
    email: str | None
    created: int | None = None
    quota: int | None = None  # bytes, plan + bonus
    used: int | None = None  # bytes
    files: int | None = None
    file_bytes: int | None = None  # combined size of the files (without thumbnails)
    albums: int | None = None
    trash: int | None = None
    two_factor: bool | None = None
    email_mfa: bool | None = None
    plan_expiry: int | None = None
    last_upload: int | None = None
    last_seen: int | None = None
    uploading: bool | None = None
    recent_uploads: int | None = None  # files added in the last 5 minutes
    daily_uploads: list[int] | None = None  # DAYS entries, oldest first
    daily_bytes: list[int] | None = None  # same days, bytes added
    devices: list[Device] = field(default_factory=list)
    # Locker (postgres source with the "locker" grants)
    locker_items: int | None = None
    locker_bytes: int | None = None
    locker_collections: int | None = None
    locker_tier: str | None = None  # "paid" | "free": which Locker limits museum applies
    locker_item_limit: int | None = None
    locker_byte_limit: int | None = None
    # public links ("links" grants): active ones, per app
    album_links: int | None = None
    locker_links: int | None = None
    # sharing ("sharing" grants): albums shared with others / by others
    shares_out: int | None = None
    shares_in: int | None = None
    # inheritance ("legacy" grants): trusted contacts the account set up, accounts that trust
    # it, and legacy kits (Locker's 2-of-3 recovery cards)
    trusted_contacts: int | None = None
    trusted_by: int | None = None
    legacy_kits: int | None = None


# Locker limits as museum enforces them (server/pkg/controller/usage.go, hardcoded)
LOCKER_LIMITS = {"free": (100, 1 << 30), "paid": (1000, 10 << 30)}


@dataclass
class Snapshot:
    instance: str
    source: str
    url: str
    generated_at: int
    accounts: list[Account]
    days: list[str] | None = None  # ISO dates matching daily_uploads
    notes: list[str] = field(default_factory=list)  # e.g. "account list from cache"
    error: str | None = None
    features: dict = field(default_factory=dict)  # optional feature -> {"ok", "reason", "grant"}

    def to_dict(self) -> dict:
        return asdict(self)


# Apple model identifiers seen in Ente's mobile user agents -> marketing names
IPHONES = {
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)", "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max", "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,6": "iPhone SE (3rd gen)", "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,5": "iPhone 16e",
}
# "<app>/<version> (<iOS|Android> <os version>; <...>; <model>; ..."
MOBILE_RE = re.compile(r"^[^/]*/([\d.]+) \((iOS|Android) ([^;]+); [^;]*; ([^;]+);")


def device_name(ua: str | None) -> str:
    """A readable device from the user agent museum stored with the session: model, OS and
    Ente version where the agent has them, else the kind of client."""
    raw = ua or ""
    m = MOBILE_RE.match(raw)
    if m:
        version, os_name, os_version, model = m.groups()
        return f"{IPHONES.get(model.strip(), model.strip())} · {os_name} {os_version.strip()} · Ente {version}"
    if "Electron" in raw:
        v = re.search(r"ente/([\d.]+)", raw, re.I)
        os_label = next((label for key, label in (("Mac OS", "macOS"), ("Windows", "Windows"), ("Linux", "Linux"))
                         if key in raw), "")
        return "Ente Desktop" + (f" {v.group(1)}" if v else "") + (f" · {os_label}" if os_label else "")
    if raw.startswith("Mozilla/"):
        browser = next((b for b in ("Firefox", "Edg", "Chrome", "Safari") if b in raw), "Browser")
        os_label = next((label for key, label in (("iPhone", "iOS"), ("Android", "Android"), ("Mac OS", "macOS"),
                                                  ("Windows", "Windows"), ("Linux", "Linux")) if key in raw), "")
        return f"Web ({'Edge' if browser == 'Edg' else browser}{' · ' + os_label if os_label else ''})"
    return _family(raw)


def _family(ua: str) -> str:
    ua = ua.lower()
    if not ua:
        return "Unknown"
    if "iphone" in ua or "ios" in ua or ("darwin" in ua and "mobile" in ua):
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "android" in ua:
        return "Android"
    if "electron" in ua or "ente-desktop" in ua or "desktop" in ua:
        for os_name, label in (("mac", "macOS"), ("windows", "Windows"), ("linux", "Linux")):
            if os_name in ua:
                return f"Desktop ({label})"
        return "Desktop"
    if "mozilla" in ua:
        return "Web"
    if "dart" in ua or "okhttp" in ua:
        return "Mobile app"
    if "go-http-client" in ua or "ente-cli" in ua:
        return "CLI"
    return "Other"
