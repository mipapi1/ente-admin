"""Email templates: built-in neutral ones, overridden by files in ENTE_ADMIN_TEMPLATES_DIR,
overridden in turn by versions edited in the portal (stored in notify.db).

A template is a subject, a plain-text body and an HTML body, with `${name}` placeholders.
Values are escaped in the HTML. An HTML body that isn't a whole document (no `<html`) is
put inside a simple built-in layout. Images: `src="cid:logo.png"` embeds that file from the
templates folder; `src="cid:random:art-*.png"` embeds one matching file at random. Files in
the folder are named `<id>.subject`, `<id>.txt` and `<id>.html`.

Template ids: `inactivity` (backup reminders), optionally `inactivity-<days>` for one
threshold and `inactivity-last` for the last one; `share` (someone shared an album with
you); `uploads` (new photos in a shared album); `test` (test email).
"""
from __future__ import annotations

import base64
import mimetypes
import random
import re
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from pathlib import Path
from string import Template

ID_RE = re.compile(r"^(inactivity(-\d{1,3}|-last)?|share|uploads|test)$")
CID_RE = re.compile(r"""src=(["'])cid:([^"']+)\1""")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# What each template can use; shown in the editor
VARIABLES = {
    "common": {"email": "the recipient's email", "name": "the part of the email before @",
               "server_name": "your server's name (Notifications settings)",
               "app_url": "the Photos web app URL, if set",
               "sender_name": "the sender name"},
    "inactivity": {"days": "the reminder's threshold (e.g. 15)",
                   "days_inactive": "days since the account was last used",
                   "last_active": "the date it was last used"},
    "share": {"sharer": "who shared the album", "rights": "what the recipient may do (view it, add photos...)"},
    "uploads": {"adders": "who added the photos", "count": "how many", "count_text": "e.g. '5 new photos'",
                "album": "'one of your shared albums' or 'an album <owner> shared with you'"},
    "test": {},
}

P = '<p style="margin:12px 0 0;font-size:16px;line-height:1.55;color:#33413a;">'
BUILTIN = {
    "inactivity": {
        "subject": "Your photos haven't been backed up in ${days} days",
        "text": "Hi ${name},\n\nYour Ente account on ${server_name} hasn't backed up anything for ${days_inactive} "
                "days (last activity: ${last_active}).\n\nOpen Ente on your phone and let it catch up: new "
                "photos are backed up and end-to-end encrypted.\n\nIf you meant to take a break, you can "
                "ignore this email.",
        "html": "<h1 style=\"margin:0;font-size:22px;color:#111a14;\">It's been ${days} days</h1>"
                f"{P}Your Ente account on <strong>${{server_name}}</strong> hasn't backed up anything for "
                "<strong>${days_inactive} days</strong> (last activity: ${last_active}).</p>"
                f"{P}Open Ente on your phone and let it catch up: new photos are backed up and end-to-end "
                "encrypted.</p>"
                f"{P}If you meant to take a break, you can ignore this email.</p>",
    },
    "inactivity-last": {
        "subject": "Reminder: no backups for ${days} days",
        "text": "Hi ${name},\n\nIt has now been ${days_inactive} days since your Ente account on ${server_name} "
                "backed anything up. Photos that only live on your phone are lost if the phone is.\n\nOpen Ente "
                "on your phone to back them up. This is the last reminder until you use the account again.",
        "html": "<h1 style=\"margin:0;font-size:22px;color:#111a14;\">No backups for ${days} days</h1>"
                f"{P}It has now been <strong>${{days_inactive}} days</strong> since your Ente account on "
                "<strong>${server_name}</strong> backed anything up. Photos that only live on your phone are "
                "lost if the phone is.</p>"
                f"{P}<strong>Open Ente on your phone</strong> to back them up.</p>"
                f"{P}This is the last reminder until you use the account again.</p>",
    },
    "share": {
        "subject": "${sharer} shared an album with you",
        "text": "${sharer} shared an album with you on ${server_name}. You can ${rights}.\n\nTo see it, open the "
                "Ente app and go to the Sharing tab. Photos stay end-to-end encrypted: only you and the "
                "people you share with can see them.",
        "html": "<h1 style=\"margin:0;font-size:22px;color:#111a14;\">You've been added to an album</h1>"
                f"{P}<strong>${{sharer}}</strong> shared an album with you. You can ${{rights}}.</p>"
                f"{P}To see it, open the Ente app and go to the <strong>Sharing</strong> tab.</p>"
                f"{P}Photos stay end-to-end encrypted: only you and the people you share with can see them.</p>",
    },
    "uploads": {
        "subject": "${adders} added ${count_text} to a shared album",
        "text": "${adders} added ${count_text} to ${album}.\n\nTo see them, open the Ente app, go to the Sharing "
                "tab and open the album. You get at most one of these per album per day.",
        "html": "<h1 style=\"margin:0;font-size:22px;color:#111a14;\">New photos in a shared album</h1>"
                f"{P}<strong>${{adders}}</strong> added ${{count_text}} to ${{album}}.</p>"
                f"{P}To see them, open the Ente app, go to the <strong>Sharing</strong> tab and open the album.</p>"
                f"{P}You get at most one of these per album per day.</p>",
    },
    "test": {
        "subject": "Test email from ${server_name}",
        "text": "This is a test email from the Ente admin dashboard for ${server_name}. Email delivery works.",
        "html": "<h1 style=\"margin:0;font-size:22px;color:#111a14;\">Email works</h1>"
                f"{P}This is a test email from the Ente admin dashboard for <strong>${{server_name}}</strong>.</p>",
    },
}

LAYOUT = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>${__subject}</title></head>
<body style="margin:0;padding:0;background:#f2f5f3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f5f3;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:520px;background:#ffffff;border-radius:14px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="height:6px;background:#16a34a;font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td style="padding:28px 32px 32px;">${__body}</td></tr>
</table>
<p style="max-width:520px;margin:16px auto 0;font-family:Helvetica,Arial,sans-serif;font-size:12px;line-height:1.5;color:#8e9b94;text-align:center;">${__footer}</p>
</td></tr></table></body></html>"""
FOOTER = "Sent by ${sender_name} for ${server_name}. This mailbox isn't monitored."


@dataclass
class Rendered:
    subject: str
    text: str
    html: str  # with cid: references
    images: dict[str, tuple[bytes, str]]  # cid -> (content, mime type)

    def preview_html(self) -> str:
        """The HTML with images inlined as data: URIs, for showing in the browser."""
        def sub(m):
            img = self.images.get(m.group(2))
            if not img:
                return m.group(0)
            return f'src="data:{img[1]};base64,{base64.b64encode(img[0]).decode()}"'
        return CID_RE.sub(sub, self.html)

    def message(self, sender: str, recipient: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, recipient, self.subject
        msg.set_content(self.text)
        msg.add_alternative(self.html, subtype="html")
        html_part = msg.get_payload()[1]
        for cid, (content, mime) in self.images.items():
            main, _, sub = mime.partition("/")
            html_part.add_related(content, main, sub, cid=f"<{cid}>", filename=cid, disposition="inline")
        return msg


class Templates:
    def __init__(self, store, folder: str = ""):
        self.store, self.folder = store, Path(folder) if folder else None

    def _file(self, tid: str) -> dict | None:
        if not self.folder:
            return None
        parts = {}
        for key, ext in (("subject", "subject"), ("text", "txt"), ("html", "html")):
            f = self.folder / f"{tid}.{ext}"
            if f.is_file():
                parts[key] = f.read_text(encoding="utf-8")
        if not parts:
            return None
        base = BUILTIN.get(tid) or BUILTIN.get(tid.split("-")[0], {})
        return {k: parts.get(k, base.get(k, "")).strip() if k == "subject" else parts.get(k, base.get(k, ""))
                for k in ("subject", "text", "html")}

    def source(self, tid: str) -> tuple[dict | None, str]:
        """The template with that exact id and where it comes from (edited, file, built-in)."""
        t = self.store.template(tid)
        if t:
            return {k: t[k] for k in ("subject", "text", "html")}, "edited"
        f = self._file(tid)
        if f:
            return f, "file"
        if tid in BUILTIN:
            return dict(BUILTIN[tid]), "built-in"
        return None, ""

    def resolve(self, kind: str, level: int | None = None, last: bool = False) -> tuple[str, dict, str]:
        """The template to use: for reminders `inactivity-<days>`, then `inactivity-last` (for
        the last threshold), then `inactivity`."""
        ids = [kind]
        if kind == "inactivity":
            ids = ([f"inactivity-{level}"] if level else []) + (["inactivity-last"] if last else []) + ["inactivity"]
        for tid in ids:
            t, origin = self.source(tid)
            if t:
                return tid, t, origin
        raise KeyError(kind)

    def catalog(self) -> list[dict]:
        ids = set(BUILTIN) | set(self.store.templates())
        if self.folder:
            ids |= {p.stem for p in self.folder.iterdir() if p.suffix in (".subject", ".txt", ".html")
                    and ID_RE.match(p.stem)}
        out = []
        for tid in sorted(ids, key=lambda t: (t.split("-")[0], t)):
            t, origin = self.source(tid)
            kind = tid.split("-")[0]
            out.append({"id": tid, "kind": kind, "origin": origin, "builtin": tid in BUILTIN,
                        "has_file": bool(self._file(tid)), **(t or {}),
                        "variables": {**VARIABLES["common"], **VARIABLES.get(kind, {})}})
        return out

    def _image(self, name: str) -> tuple[bytes, str] | None:
        if not self.folder or not NAME_RE.match(name):
            return None
        f = self.folder / name
        if not f.is_file() or f.stat().st_size > 2 << 20:
            return None
        return f.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"

    def render(self, t: dict, values: dict) -> Rendered:
        plain = {k: "" if v is None else str(v) for k, v in values.items()}
        safe = {k: escape(v) for k, v in plain.items()}
        subject = Template(t["subject"]).safe_substitute(plain).replace("\n", " ").strip()[:250]
        text = Template(t["text"]).safe_substitute(plain)
        body = Template(t["html"]).safe_substitute(safe) if t.get("html") else \
            "".join(f"{P}{escape(par).replace(chr(10), '<br>')}</p>" for par in text.split("\n\n"))
        if "<html" not in body.lower():
            body = Template(LAYOUT).safe_substitute({"__subject": escape(subject), "__body": body,
                                                     "__footer": Template(FOOTER).safe_substitute(safe)})
            text = f"{text}\n\n--\n{Template(FOOTER).safe_substitute(plain)}\n"
        images: dict[str, tuple[bytes, str]] = {}
        counter = iter(range(1, 1000))

        def embed(m):
            ref = m.group(2)
            if ref.startswith("random:") and self.folder:
                pattern = ref[7:]
                choices = sorted(p.name for p in self.folder.glob(pattern) if NAME_RE.match(p.name))
                ref = random.choice(choices) if choices else ""  # noqa: S311 - artwork, not security
            img = self._image(ref) if ref else None
            if not img:
                return m.group(0)
            cid = ref if ref not in images else f"{next(counter)}-{ref}"
            images[cid] = img
            return f'src="cid:{cid}"'
        body = CID_RE.sub(embed, body)
        return Rendered(subject=subject, text=text, html=body, images=images)
