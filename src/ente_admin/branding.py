"""Optional branding from ENTE_ADMIN_BRANDING_DIR: a branding.json and the images it names.

    {
      "title": "Photos", "badge": "admin", "tagline": "Our photo server at a glance.",
      "accent": "#16a34a", "accent_2": "#15803d",
      "favicon": "icon.svg", "hero": "hero.png", "empty_image": "empty.png",
      "footer_image": "small.svg", "footer_text": "Photos are end-to-end encrypted.",
      "kpi_images": {"accounts": "people.webp", "uploading": "rocket.webp"}
    }

Every key is optional. Images must sit in the same folder; only the names listed here are
served (under /branding/<name>). Colours must be #rgb or #rrggbb. Text is shown as text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
TEXT_KEYS = {"title": 40, "badge": 20, "tagline": 200, "footer_text": 300}
IMAGE_KEYS = ("favicon", "hero", "empty_image", "footer_image")
KPI_KEYS = ("accounts", "storage", "files", "devices", "uploading")
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class Branding:
    def __init__(self, folder: str):
        self.folder = Path(folder) if folder else None
        self.images: dict[str, str] = {}  # served name -> content type
        self.public: dict = {}
        if self.folder:
            self._load()

    def _image(self, name) -> str | None:
        if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            return None
        ctype = IMAGE_TYPES.get(Path(name).suffix.lower())
        f = self.folder / name
        if not ctype or not f.is_file() or f.stat().st_size > MAX_IMAGE_BYTES:
            return None
        self.images[name] = ctype
        return f"/branding/{name}"

    def _load(self) -> None:
        raw = json.loads((self.folder / "branding.json").read_text())
        if not isinstance(raw, dict):
            raise ValueError("branding.json must be an object")
        out: dict = {}
        for key, limit in TEXT_KEYS.items():
            if isinstance(raw.get(key), str) and raw[key].strip():
                out[key] = raw[key].strip()[:limit]
        for key in ("accent", "accent_2"):
            if isinstance(raw.get(key), str) and COLOR_RE.match(raw[key]):
                out[key] = raw[key]
        for key in IMAGE_KEYS:
            if (url := self._image(raw.get(key))):
                out[key] = url
        kpis = raw.get("kpi_images") if isinstance(raw.get("kpi_images"), dict) else {}
        out["kpi_images"] = {k: url for k in KPI_KEYS if (url := self._image(kpis.get(k)))}
        self.public = out

    def read(self, name: str) -> tuple[bytes, str] | None:
        ctype = self.images.get(name)
        if not ctype:
            return None
        return (self.folder / name).read_bytes(), ctype
