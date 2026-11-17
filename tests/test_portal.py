import json
import logging
import threading
import time
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer

import pytest
from fake_museum import CODE, TOTP, FakeMuseum, GiB
from test_server import get

from ente_admin import config
from ente_admin.server import Dashboard, make_handler

ADMIN, ALICE, BOB, MALLORY = 1001, 1002, 1003, 1004
HDRS = {"Remote-User": "root", "Remote-Groups": "admins", "Remote-Email": "root@example.com",
        "X-Ente-Admin": "1", "Content-Type": "application/json"}


@pytest.fixture
def portal(tmp_path):
    museum = FakeMuseum()
    museum.add_user(ADMIN, "admin@example.com", "admin-pw", admin=True)
    museum.add_user(ALICE, "alice@example.com", two_factor=True, family=["bob@example.com"])
    museum.add_user(BOB, "bob@example.com")
    museum.add_user(MALLORY, "mallory@example.com", "m-pw")
    base = museum.serve()
    (tmp_path / "token").write_text(museum.new_token(ADMIN))  # for the read-only museum source
    env = {"ENTE_ADMIN_AUTH": "header", "ENTE_ADMIN_TRUSTED_PROXIES": "127.0.0.1/32",
           "ENTE_ADMIN_ADMIN_GROUPS": "admins", "ENTE_ADMIN_ENABLE_ACTIONS": "true",
           "ENTE_ADMIN_REVEAL_EMAILS": "true", "ENTE_ADMIN_STATE_DIR": str(tmp_path),
           "ENTE_ADMIN_MAIL_FROM": "noreply@example.com", "ENTE_ADMIN_KDF_MAX_MIB": "64",
           "ENTE_ADMIN_INSTANCES": "photos", "ENTE_ADMIN_PHOTOS_SOURCE": "museum",
           "ENTE_ADMIN_PHOTOS_URL": base, "ENTE_ADMIN_PHOTOS_TOKEN_FILE": str(tmp_path / "token")}
    dash = Dashboard(config.load(env))
    dash.refresh()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dash))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url, museum, dash
    httpd.shutdown()
    museum.httpd.shutdown()


def post(url, path, payload, headers=None):
    code, _, body = get(url + path, method="POST", data=json.dumps(payload).encode(), headers=headers or HDRS)
    return code, json.loads(body or b"{}")


def unlock(url, email="admin@example.com", password="admin-pw"):
    assert post(url, "/api/admin/unlock/send", {"instance": "photos", "email": email}) == (200, {"ok": True,
                                                                                               "step": "code"})
    return post(url, "/api/admin/unlock/verify", {"instance": "photos", "code": CODE, "password": password})


def status(url):
    return json.loads(get(url + "/api/admin/status", headers=HDRS)[2])["instances"][0]


def run(url, action, account, params=None, confirm=None, preview=False):
    return post(url, "/api/admin/preview" if preview else "/api/admin/run",
                {"instance": "photos", "action": action, "account": str(account), "params": params or {},
                 "confirm": confirm})


def audit(url):
    return json.loads(get(url + "/api/admin/audit", headers=HDRS)[2])["entries"]


# ---- unlock / lock ----
def test_unlock_and_lock(portal):
    url, museum, _ = portal
    assert status(url)["unlocked"] is False
    code, out = unlock(url)
    assert code == 200 and out["step"] == "done" and out["unlocked"] and out["email"] == "admin@example.com"
    assert status(url)["unlocked"] is True
    tokens = set(museum.tokens)
    assert post(url, "/api/admin/lock", {"instance": "photos"})[0] == 200
    assert status(url)["unlocked"] is False
    time.sleep(0.2)  # sign-out runs in the background
    assert len(museum.logged_out) == 1 and museum.logged_out[0] in tokens
    assert [e["action"] for e in audit(url)][:3] == ["lock", "unlock", "unlock-code-sent"]


def test_code_only_for_existing_accounts(portal):
    url, museum, _ = portal
    code, out = post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "new@example.com"})
    assert code == 400 and "no account" in out["error"]
    assert not any(p == "/users/ott" for _, p, _ in museum.calls)  # museum would create the account


def test_wrong_password_then_right(portal):
    url, _, _ = portal
    code, out = unlock(url, password="nope")
    assert code == 400 and out["error"] == "wrong password"
    # the code was used up; the verified response is kept, so only the password is retried
    code, out = post(url, "/api/admin/unlock/verify", {"instance": "photos", "code": "", "password": "admin-pw"})
    assert code == 200 and out["unlocked"]


def test_wrong_code(portal):
    url, _, _ = portal
    post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "admin@example.com"})
    code, out = post(url, "/api/admin/unlock/verify", {"instance": "photos", "code": "000000", "password": "x"})
    assert code == 400 and "code" in out["error"]


def test_two_factor_unlock(portal):
    url, museum, _ = portal
    museum.users[ADMIN].two_factor = True
    post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "admin@example.com"})
    code, out = post(url, "/api/admin/unlock/verify", {"instance": "photos", "code": CODE, "password": "admin-pw"})
    assert code == 200 and out == {"step": "totp"}
    assert status(url)["step"] == "totp"
    code, out = post(url, "/api/admin/unlock/verify", {"instance": "photos", "totp": TOTP, "password": "admin-pw"})
    assert code == 200 and out["unlocked"]


def test_non_admin_account_is_signed_out(portal):
    url, museum, _ = portal
    code, out = unlock(url, "mallory@example.com", "m-pw")
    assert code == 403 and "isn't an admin" in out["error"]
    assert status(url)["unlocked"] is False
    assert len(museum.logged_out) == 1


def test_code_rate_limit(portal):
    url, _, _ = portal
    for _ in range(3):
        assert post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "admin@example.com"})[0] == 200
    assert post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "admin@example.com"})[0] == 429


def test_idle_session_locks_and_signs_out(portal):
    url, museum, dash = portal
    unlock(url)
    dash.portal.sessions.idle_s = 0.05
    time.sleep(0.1)
    assert status(url)["unlocked"] is False
    code, out = run(url, "email-mfa", BOB, {"enabled": True})
    assert code == 401 and "locked" in out["error"]
    time.sleep(0.2)
    assert len(museum.logged_out) == 1


def test_locked_by_default(portal):
    url, _, _ = portal
    code, out = run(url, "email-mfa", BOB, {"enabled": True})
    assert code == 401


# ---- request guards ----
@pytest.mark.parametrize("change", [
    {"X-Ente-Admin": None}, {"Content-Type": "text/plain"}, {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://evil.example"},
])
def test_csrf_guards(portal, change):
    url, museum, _ = portal
    h = {k: v for k, v in {**HDRS, **change}.items() if v is not None}
    code, out = post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "admin@example.com"}, headers=h)
    assert code == 403 and "refused" in out["error"]
    assert not museum.otts


def test_same_origin_is_fine(portal):
    url, _, _ = portal
    host = url.split("//")[1]
    h = {**HDRS, "Origin": url, "Host": host, "Sec-Fetch-Site": "same-origin"}
    send = {"instance": "photos", "email": "admin@example.com"}
    assert post(url, "/api/admin/unlock/send", send, headers=h)[0] == 200


def test_viewers_cannot_use_the_portal(portal):
    url, _, _ = portal
    h = {**HDRS, "Remote-User": "bob", "Remote-Groups": "users"}
    send = {"instance": "photos", "email": "admin@example.com"}
    assert post(url, "/api/admin/unlock/send", send, headers=h)[0] == 403
    assert get(url + "/api/admin/health", headers=h)[0] == 403
    cfg = json.loads(get(url + "/api/config", headers=h)[2])
    assert cfg["portal"] is False


def test_big_body_refused(portal):
    url, _, _ = portal
    code, _ = post(url, "/api/admin/unlock/send", {"instance": "photos", "email": "x" * 9000})
    assert code == 400


# ---- account details and actions ----
def test_account_details(portal):
    url, _, _ = portal
    unlock(url)
    d = json.loads(get(url + f"/api/admin/account?instance=photos&account={ALICE}", headers=HDRS)[2])
    assert d["account"]["email"] == "alice@example.com" and d["account"]["two_factor"] is True
    assert d["account"]["family"][1]["email"] == "bob@example.com"
    names = {a["name"]: a for a in d["actions"]}
    assert names["disable-2fa"]["unavailable"] is None and names["remove-bonus"]["unavailable"]
    assert "send-test-mail" not in names


def test_set_storage(portal):
    url, museum, _ = portal
    unlock(url)
    code, prev = run(url, "set-storage", BOB, {"storage_gb": 50, "expiry": "2100-01-31"}, preview=True)
    assert code == 200 and prev["confirm"] is None
    assert prev["changes"][0] == {"what": "Plan storage", "before": "10 GB", "after": "50 GB"}
    assert museum.users[BOB].storage == 10 * GiB  # a preview changes nothing
    code, out = run(url, "set-storage", BOB, {"storage_gb": 50, "expiry": "2100-01-31"})
    assert code == 200 and out["ok"] and out["account"]["plan_storage"] == 50 * GiB
    assert museum.users[BOB].storage == 50 * GiB and museum.users[BOB].product == "free"
    assert museum.users[BOB].expiry == int(datetime(2100, 1, 31, 23, 59, 59, tzinfo=UTC).timestamp()) * 1_000_000
    e = audit(url)[0]
    assert e["action"] == "set-storage" and e["actor"] == "root" and e["account_id"] == BOB
    assert e["before"]["plan_storage"] == 10 * GiB and e["after"]["plan_storage"] == 50 * GiB
    assert e["params"] == {"storage_gb": 50, "expiry": "2100-01-31"}


def test_paid_plan_storage_refused(portal):
    url, museum, _ = portal
    museum.users[BOB].product = "stripe_200gb"
    unlock(url)
    code, out = run(url, "set-storage", BOB, {"storage_gb": 50, "expiry": "2100-01-31"})
    assert code == 409 and "paid plan" in out["error"]


@pytest.mark.parametrize("params,msg", [
    ({"storage_gb": 0, "expiry": "2100-01-01"}, "between"), ({"storage_gb": "lots", "expiry": "2100-01-01"}, "whole"),
    ({"storage_gb": 5, "expiry": "2001-01-01"}, "from today"), ({"storage_gb": 5}, "required"),
    ({"storage_gb": 5, "expiry": "2100-01-01", "extra": 1}, "unknown"),
])
def test_validation(portal, params, msg):
    url, _, _ = portal
    unlock(url)
    code, out = run(url, "set-storage", BOB, params)
    assert code == 400 and msg in out["error"]


def test_bonus_add_update_remove(portal):
    url, museum, _ = portal
    unlock(url)
    assert run(url, "set-bonus", BOB, {"storage_gb": 100, "years": 5})[0] == 200
    assert museum.calls[-2][2]["action"] == "ADD"
    _, prev = run(url, "set-bonus", BOB, {"storage_gb": 200, "years": 5}, preview=True)
    assert prev["changes"][0]["before"].startswith("100 GB")
    assert run(url, "set-bonus", BOB, {"storage_gb": 200, "years": 5})[0] == 200
    assert museum.users[BOB].bonuses[0]["storage"] == 200 * GiB
    assert run(url, "remove-bonus", BOB)[0] == 200
    assert museum.users[BOB].bonuses == []
    assert run(url, "remove-bonus", BOB)[0] == 409


def test_disable_2fa_needs_typed_email(portal):
    url, museum, _ = portal
    unlock(url)
    _, prev = run(url, "disable-2fa", ALICE, preview=True)
    assert prev["confirm"] == "alice@example.com" and prev["danger"] == "danger"
    code, out = run(url, "disable-2fa", ALICE, confirm="alice")
    assert code == 400 and "to confirm" in out["error"] and museum.users[ALICE].two_factor
    assert run(url, "disable-2fa", ALICE, confirm="Alice@example.com")[0] == 200
    assert not museum.users[ALICE].two_factor
    assert run(url, "disable-2fa", ALICE, confirm="alice@example.com")[0] == 409  # already off


def test_fresh_sign_in_needed(portal):
    url, museum, dash = portal
    unlock(url)
    s = dash.portal.sessions.get("root", "photos")
    s.created -= 11 * 60
    _, prev = run(url, "disable-2fa", ALICE, preview=True)
    assert any("10 minutes" in w for w in prev["warnings"])
    code, out = run(url, "disable-2fa", ALICE, confirm="alice@example.com")
    assert code == 409 and "10 minutes" in out["error"] and museum.users[ALICE].two_factor
    assert run(url, "email-mfa", BOB, {"enabled": True})[0] == 200  # other actions still work


def test_email_mfa_and_passkeys_and_unblock(portal):
    url, museum, _ = portal
    unlock(url)
    assert run(url, "email-mfa", BOB, {"enabled": True})[0] == 200 and museum.users[BOB].email_mfa
    code, out = run(url, "email-mfa", BOB, {"enabled": True})
    assert code == 400 and "already on" in out["error"]
    assert run(url, "remove-passkeys", BOB, confirm="bob@example.com")[0] == 200
    assert run(url, "unblock-login", BOB)[0] == 200
    assert {p for _, p, _ in museum.calls} >= {"/admin/user/disable-passkeys",
                                              "/admin/user/unblock-storage-warning-login"}


def test_change_email(portal):
    url, museum, _ = portal
    unlock(url)
    code, out = run(url, "change-email", BOB, {"email": "not-an-email"}, confirm="bob@example.com")
    assert code == 400 and "email" in out["error"]
    code, out = run(url, "change-email", BOB, {"email": "alice@example.com"}, confirm="bob@example.com")
    assert code == 502 and "already in use" in out["error"]
    assert audit(url)[0]["result"] == "error"
    assert run(url, "change-email", BOB, {"email": "Bobby@Example.com"}, confirm="bob@example.com")[0] == 200
    assert museum.users[BOB].email == "bobby@example.com"


def test_close_family(portal):
    url, museum, _ = portal
    unlock(url)
    assert run(url, "close-family", BOB, confirm="bob@example.com")[0] == 409
    _, prev = run(url, "close-family", ALICE, preview=True)
    assert "bob@example.com" in prev["changes"][0]["before"]
    assert run(url, "close-family", ALICE, confirm="alice@example.com")[0] == 200
    assert museum.users[ALICE].family == []


def test_delete_user(portal):
    url, museum, _ = portal
    unlock(url)
    _, prev = run(url, "delete-user", BOB, preview=True)
    assert prev["confirm"] == "delete bob@example.com" and prev["danger"] == "irreversible"
    assert run(url, "delete-user", BOB, confirm="bob@example.com")[0] == 400
    assert not museum.users[BOB].deleted
    code, out = run(url, "delete-user", BOB, confirm="delete bob@example.com")
    assert code == 200 and out["account"] is None and museum.users[BOB].deleted
    e = audit(url)[0]
    assert e["action"] == "delete-user" and e["before"]["email"] == "bob@example.com" and e["after"] is None


def test_cannot_delete_own_admin_account(portal):
    url, museum, _ = portal
    unlock(url)
    code, out = run(url, "delete-user", ADMIN, confirm="delete admin@example.com")
    assert code == 409 and "signed in with" in out["error"] and not museum.users[ADMIN].deleted


def test_test_mail(portal):
    url, museum, _ = portal
    unlock(url)
    acts = json.loads(get(url + "/api/admin/server-actions?instance=photos", headers=HDRS)[2])["actions"]
    assert acts[0]["name"] == "send-test-mail" and acts[0]["fields"][0]["default"] == "root@example.com"
    assert run(url, "send-test-mail", "", {"to": "root@example.com"})[0] == 200
    assert museum.mail[0]["to"] == ["root@example.com"] and museum.mail[0]["fromEmail"] == "noreply@example.com"


def test_unknown_action_and_account(portal):
    url, _, _ = portal
    unlock(url)
    assert run(url, "make-coffee", BOB)[0] == 404
    assert run(url, "email-mfa", 999, {"enabled": True})[0] == 404


def test_action_rate_limit(portal):
    url, _, dash = portal
    unlock(url)
    dash.portal.action_limit.limit = 2
    assert run(url, "unblock-login", BOB)[0] == 200
    assert run(url, "unblock-login", BOB)[0] == 200
    assert run(url, "unblock-login", BOB)[0] == 429


def test_health(portal):
    url, _, _ = portal
    h = json.loads(get(url + "/api/admin/health", headers=HDRS)[2])["instances"][0]
    assert h["ok"] and h["version"] == "abc123def456" and h["accounts"] == 4


def test_no_secrets_in_logs_or_audit(portal, caplog, tmp_path):
    url, museum, dash = portal
    caplog.set_level(logging.DEBUG)
    unlock(url)
    run(url, "unblock-login", BOB)
    token = dash.portal.sessions.get("root", "photos").token
    text = caplog.text + (tmp_path / "audit.db").read_bytes().decode("latin-1")
    for secret in ("admin-pw", CODE, token):
        assert secret not in text
    status_body = get(url + "/api/admin/status", headers=HDRS)[2].decode()
    assert token not in status_body


def test_close_signs_everything_out(portal):
    url, museum, dash = portal
    unlock(url)
    dash.portal.close()
    assert len(museum.logged_out) == 1 and status(url)["unlocked"] is False
