import json
import threading
from http.server import ThreadingHTTPServer

import pytest
from fake_smtp import FakeSMTP
from test_server import get

from ente_admin import config, ops
from ente_admin.server import Dashboard, make_handler

HDRS = {"Remote-User": "root", "Remote-Groups": "admins", "X-Ente-Admin": "1", "Content-Type": "application/json"}
USER = {"Remote-User": "someone", "X-Ente-Admin": "1", "Content-Type": "application/json"}


@pytest.fixture
def api(tmp_path):
    smtp = FakeSMTP()
    env = {"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_AUTH": "header", "ENTE_ADMIN_TRUSTED_PROXIES": "127.0.0.1/32",
           "ENTE_ADMIN_ADMIN_GROUPS": "admins", "ENTE_ADMIN_ENABLE_ACTIONS": "true",
           "ENTE_ADMIN_REVEAL_EMAILS": "true", "ENTE_ADMIN_STATE_DIR": str(tmp_path),
           "ENTE_ADMIN_MAIL_FROM": "noreply@example.com", "ENTE_ADMIN_NOTIFY": "shadow",
           "ENTE_ADMIN_SMTP_HOST": "127.0.0.1", "ENTE_ADMIN_SMTP_PORT": str(smtp.port),
           "ENTE_ADMIN_SMTP_SECURITY": "none", "ENTE_ADMIN_SMTP_FROM": "noreply@example.com"}
    dash = Dashboard(config.load(env))
    dash.refresh()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dash))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", smtp
    httpd.shutdown()
    smtp.close()


def call(url, path, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    code, hdrs, body = get(url + path, method="GET" if data is None else "POST", data=data, headers=headers or HDRS)
    return code, (json.loads(body) if hdrs.get("Content-Type", "").startswith("application/json") else body)


def audit(url):
    return [e["action"] for e in call(url, "/api/admin/audit")[1]["entries"]]


def first_account(url):
    return call(url, "/api/admin/notify/upcoming?instance=photos")[1]["upcoming"][0]


def test_reads_are_admin_only(api):
    url, _ = api
    for path in ("/api/admin/notify/overview", "/api/admin/notify/upcoming?instance=photos",
                 "/api/admin/apps?instance=photos", "/api/admin/setup?instance=photos"):
        assert call(url, path)[0] == 200, path
        assert call(url, path, headers=USER)[0] in (401, 403, 404), path
    code, out = call(url, "/api/admin/notify/overview")
    assert out["instances"]["photos"]["mode"] == "shadow"
    code, out = call(url, "/api/admin/links?instance=photos")  # needs the postgres source: says so
    assert code == 409 and "postgres" in out["error"]


def test_changes_need_same_origin(api):
    url, _ = api
    hdrs = {k: v for k, v in HDRS.items() if k != "X-Ente-Admin"}
    assert call(url, "/api/admin/notify/suppress", {"instance": "photos", "account": "1"}, hdrs)[0] == 403


def test_send_mode_needs_confirmation(api):
    url, _ = api
    code, out = call(url, "/api/admin/notify/settings", {"instance": "photos", "mode": "send"})
    assert code == 400 and "send emails" in out["error"]
    code, out = call(url, "/api/admin/notify/settings",
                     {"instance": "photos", "mode": "send", "confirm": "send emails"})
    assert code == 200 and out["settings"]["instances"]["photos"]["mode"] == "send"
    assert call(url, "/api/admin/notify/settings", {"thresholds": [0]})[0] == 400
    out = call(url, "/api/admin/notify/settings", {"thresholds": [7, 14, 30]})[1]
    assert out["settings"]["thresholds"] == [7, 14, 30]
    assert audit(url).count("notify-settings") == 2


def test_test_email_send_and_resend(api):
    url, smtp = api
    assert call(url, "/api/admin/notify/test", {"instance": "photos", "to": "not an address"})[0] == 400
    code, out = call(url, "/api/admin/notify/test", {"instance": "photos", "to": "me@example.com", "template": "share"})
    assert code == 200 and out["event"]["status"] == "sent"
    assert smtp.to() == ["me@example.com"]
    acct = first_account(url)
    code, out = call(url, "/api/admin/notify/send", {"instance": "photos", "account": acct["account"], "level": 15})
    assert code == 200 and out["event"]["status"] == "shadow"  # shadow mode: recorded, not sent
    assert len(smtp.messages) == 1
    eid = out["event"]["id"]
    code, out = call(url, "/api/admin/notify/resend", {"event": eid})
    assert code == 200 and out["event"]["ref"] == eid
    events = call(url, f"/api/admin/notify/events?instance=photos&account={acct['account']}")[1]["events"]
    assert len(events) == 2
    assert {"notify-test", "notify-send", "notify-resend"} <= set(audit(url))


def test_suppress_and_unsuppress(api):
    url, _ = api
    acct = first_account(url)["account"]
    assert call(url, "/api/admin/notify/suppress", {"instance": "photos", "account": acct, "kind": "bad"})[0] == 400
    assert call(url, "/api/admin/notify/suppress", {"instance": "photos", "account": acct, "days": 30})[0] == 200
    up = {u["account"]: u for u in call(url, "/api/admin/notify/upcoming?instance=photos")[1]["upcoming"]}
    assert up[acct]["state"] == "suppressed"
    assert call(url, "/api/admin/notify/unsuppress", {"instance": "photos", "account": acct})[1] == {"ok": True}
    assert call(url, "/api/admin/notify/unsuppress", {"instance": "photos", "account": acct})[1] == {"ok": False}


def test_preview_is_per_user_and_sandboxed(api):
    url, _ = api
    code, out = call(url, "/api/admin/notify/preview", {"instance": "photos", "template": "inactivity-last"})
    assert code == 200 and "30" in out["subject"]
    code, hdrs, body = get(url + f"/api/admin/notify/preview.html?id={out['id']}", headers=HDRS)
    assert code == 200 and "sandbox" in hdrs["Content-Security-Policy"] and b"<html" in body.lower()
    other = {**HDRS, "Remote-User": "root2"}
    assert get(url + f"/api/admin/notify/preview.html?id={out['id']}", headers=other)[0] == 404


def test_template_edit_and_reset(api):
    url, _ = api
    bad = {"id": "share", "subject": "Hi", "text": " ", "html": ""}
    assert call(url, "/api/admin/notify/template", bad)[0] == 400
    t = {"id": "inactivity-30", "subject": "Still there? ${days} days", "text": "Open the app.", "html": ""}
    assert call(url, "/api/admin/notify/template", t)[0] == 200
    tpls = {x["id"]: x for x in call(url, "/api/admin/notify/templates")[1]["templates"]}
    assert tpls["inactivity-30"]["origin"] == "edited"
    assert call(url, "/api/admin/notify/template-reset", {"id": "inactivity-30"})[1] == {"ok": True}


def test_database_changes_need_unlock(api):
    url, _ = api
    code, out = call(url, "/api/admin/links/revoke", {"instance": "photos", "kind": "album", "id": "1",
                                                      "confirm": "revoke"})
    assert code == 401 and "unlock" in out["error"]
    code, _ = call(url, "/api/admin/sessions/signout", {"instance": "photos", "account": "1", "session": "1",
                                                        "confirm": "sign out"})
    assert code == 401


def test_backup_check_is_audited(api):
    url, _ = api
    assert call(url, "/api/admin/backups/check", {"instance": "photos", "what": "", "result": "ok"})[0] == 400
    check = {"instance": "photos", "what": "restored db", "result": "ok"}
    assert call(url, "/api/admin/backups/check", check)[0] == 200
    assert "backup-check" in audit(url)


def test_config_proposal_writes_nothing(api):
    url, _ = api
    code, out = call(url, "/api/admin/config/propose", {"instance": "photos", "apps": {"share": "javascript:x"}})
    assert code == 400
    code, out = call(url, "/api/admin/config/propose", {"instance": "photos",
                                                        "apps": {"share": "https://share.example.com"}})
    assert code == 200 and "public-locker: https://share.example.com" in out["snippet"]
    assert "museum-config-proposed" in audit(url)


def test_museum_yaml_diff_hides_secrets():
    text = "db:\n  password: hunter2\napps:\n  public-albums: https://albums.example.com\ns3:\n  key: abc\n"
    out = ops.propose(ops.redact(text), apps={"share": "https://share.example.com"}, disable_registration=True)
    assert "hunter2" not in out and "abc" not in out and "<redacted>" in out
    assert "  public-locker: https://share.example.com" in out
    assert "  public-albums: https://albums.example.com" in out
    assert "disable-registration: true" in out
