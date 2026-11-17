import json

import pytest
from test_server import get

from ente_admin import config
from ente_admin.sources.model import device_name

PROXY = {"ENTE_ADMIN_AUTH": "header", "ENTE_ADMIN_TRUSTED_PROXIES": "127.0.0.1/32", "ENTE_ADMIN_ADMIN_GROUPS": "admins"}


def as_user(user, groups="", email="", name=""):
    return {"Remote-User": user, "Remote-Groups": groups, "Remote-Email": email, "Remote-Name": name}


def data(url, **who):
    code, _, body = get(url + "/api/data", headers=as_user(**who))
    assert code == 200
    return json.loads(body)


def accounts(d):
    return [(i["instance"], a) for i in d["instances"] for a in i["accounts"]]


def share(url, by, **payload):
    return get(url + "/api/grants", method="POST", data=json.dumps(payload).encode(),
               headers={**as_user(**by), "X-Ente-Admin": "1", "Content-Type": "application/json"})


def test_admin_sees_everything(demo_server):
    url = demo_server(**PROXY)
    d = data(url, user="root", groups="admins,users")
    assert d["me"] == {"user": "root", "name": "root", "admin": True, "email": ""}
    assert len(accounts(d)) > 5


def test_viewer_sees_own_account_only(demo_server):
    url = demo_server(**PROXY, ENTE_ADMIN_REVEAL_EMAILS="true")
    everyone = accounts(data(url, user="root", groups="admins"))
    inst, target = everyone[0]
    d = data(url, user="alex", groups="users", email=target["email"].upper())
    assert d["me"]["admin"] is False
    got = accounts(d)
    # the same email on several instances: all of them, nothing else
    assert sorted(i for i, _ in got) == sorted(i for i, a in everyone if a["email"] == target["email"])
    assert {a["email"] for _, a in got} == {target["email"]} and all(a["own"] for _, a in got)
    assert all("shared_with" not in a for _, a in got) and "candidates" not in d
    assert all(i["notes"] == [] for i in d["instances"])


def test_viewer_without_match_sees_nothing(demo_server):
    url = demo_server(**PROXY)
    assert data(url, user="eve", groups="users", email="eve@nowhere.test")["instances"] == []


def test_groups_header_ignored_from_untrusted_address(demo_server):
    url = demo_server(**{**PROXY, "ENTE_ADMIN_TRUSTED_PROXIES": "192.0.2.0/24"})
    assert get(url + "/api/data", headers=as_user("root", groups="admins"))[0] == 403


def test_sharing(demo_server, tmp_path):
    directory = tmp_path / "directory.json"
    directory.write_text(json.dumps([
        {"user": "bob", "name": "Bob", "groups": ["app-photos"]},
        {"user": "carol", "groups": ["other"]},
        {"user": "dave", "groups": ["app-photos"], "disabled": True},
        {"user": "root", "groups": ["admins", "app-photos"]},
        {"user": "bad name", "groups": ["app-photos"]},
    ]))
    url = demo_server(**PROXY, ENTE_ADMIN_SHARING="true", ENTE_ADMIN_DIRECTORY_FILE=str(directory),
                      ENTE_ADMIN_SHARE_GROUPS="app-photos")
    admin = {"user": "root", "groups": "admins"}
    d = data(url, **admin)
    assert d["candidates"] == [{"user": "bob", "name": "Bob"}]
    inst, target = accounts(d)[0]
    assert target["shared_with"] == []

    assert data(url, user="bob", groups="app-photos")["instances"] == []
    assert share(url, admin, add=True, username="bob", instance=inst, account=target["label"])[0] == 200
    got = accounts(data(url, user="bob", groups="app-photos"))
    assert [a["label"] for _, a in got] == [target["label"]] and got[0][1]["own"] is False
    assert [a for _, a in accounts(data(url, **admin)) if a["label"] == target["label"]][0]["shared_with"] == ["bob"]

    assert share(url, admin, add=False, username="bob", instance=inst, account=target["label"])[0] == 200
    assert data(url, user="bob", groups="app-photos")["instances"] == []


@pytest.mark.parametrize("by,payload,headers,code", [
    ({"user": "bob", "groups": "users"},
     {"add": True, "username": "bob", "instance": "photos", "account": "x"}, {}, 403),
    ({"user": "root", "groups": "admins"}, {"add": True, "username": "bob", "instance": "photos", "account": "x"},
     {"X-Ente-Admin": None}, 403),
    ({"user": "root", "groups": "admins"}, {"add": True, "username": "no spaces", "instance": "photos", "account": "x"},
     {}, 400),
    ({"user": "root", "groups": "admins"}, {"add": True, "username": "bob", "instance": "photos", "account": "nope"},
     {}, 400),
])
def test_share_guards(demo_server, by, payload, headers, code):
    url = demo_server(**PROXY, ENTE_ADMIN_SHARING="true")
    h = {**as_user(**by), "X-Ente-Admin": "1", "Content-Type": "application/json", **headers}
    h = {k: v for k, v in h.items() if v is not None}
    assert get(url + "/api/grants", method="POST", data=json.dumps(payload).encode(), headers=h)[0] == code


def test_sharing_off_by_default(demo_server):
    url = demo_server(**PROXY)
    code, _, _ = share(url, {"user": "root", "groups": "admins"},
                       add=True, username="bob", instance="photos", account="x")
    assert code == 403
    assert "candidates" not in data(url, user="root", groups="admins")


def test_viewer_gets_no_actions(demo_server):
    url = demo_server(**PROXY, ENTE_ADMIN_ENABLE_ACTIONS="true")
    cfg = json.loads(get(url + "/api/config", headers=as_user("bob", groups="users"))[2])
    assert cfg["portal"] is False
    code, _, body = get(url + "/api/admin/run", method="POST", data=b"{}",
                        headers={**as_user("bob", groups="users"), "X-Ente-Admin": "1",
                                 "Content-Type": "application/json"})
    assert code == 403 and b"admins only" in body
    assert get(url + "/api/admin/audit", headers=as_user("bob", groups="users"))[0] == 403


def test_branding(demo_server, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b" / "secret.txt").write_text("no")
    (tmp_path / "b" / "branding.json").write_text(json.dumps({
        "title": "Photos", "badge": "admin", "accent": "#08c225", "accent_2": "red;}", "hero": "hero.png",
        "footer_image": "../etc/passwd", "empty_image": "secret.txt",
        "kpi_images": {"accounts": "hero.png", "x": "hero.png"},
    }))
    url = demo_server(**PROXY, ENTE_ADMIN_BRANDING_DIR=str(tmp_path / "b"),
                      ENTE_ADMIN_LOGOUT_URL="https://auth.example.com/logout")
    who = as_user("root", groups="admins")
    cfg = json.loads(get(url + "/api/config", headers=who)[2])
    b = cfg["branding"]
    assert b["title"] == "Photos" and b["accent"] == "#08c225" and "accent_2" not in b
    assert b["hero"] == "/branding/hero.png" and "footer_image" not in b and "empty_image" not in b
    assert b["kpi_images"] == {"accounts": "/branding/hero.png"}
    assert cfg["logout_url"] == "https://auth.example.com/logout"
    code, headers, body = get(url + "/branding/hero.png", headers=who)
    assert code == 200 and headers["Content-Type"] == "image/png" and body.startswith(b"\x89PNG")
    assert get(url + "/branding/secret.txt", headers=who)[0] == 404
    assert get(url + "/branding/branding.json", headers=who)[0] == 404
    assert get(url + "/branding/hero.png")[0] == 403  # still needs a login


def test_config_validation(user_hash, tmp_path):
    base = {"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS": f"admin:{user_hash}"}
    with pytest.raises(config.ConfigError, match="AUTH=header"):
        config.load({**base, "ENTE_ADMIN_ADMIN_GROUPS": "admins"})
    with pytest.raises(config.ConfigError, match="ADMIN_GROUPS"):
        config.load({"ENTE_ADMIN_DEMO": "true", **{k: v for k, v in PROXY.items() if k != "ENTE_ADMIN_ADMIN_GROUPS"},
                     "ENTE_ADMIN_SHARING": "true"})
    with pytest.raises(config.ConfigError, match="LOGOUT_URL"):
        config.load({**base, "ENTE_ADMIN_LOGOUT_URL": "javascript:alert(1)"})
    with pytest.raises(config.ConfigError, match="branding.json"):
        config.load({**base, "ENTE_ADMIN_BRANDING_DIR": str(tmp_path)})
    cfg = config.load({"ENTE_ADMIN_DEMO": "true", **PROXY, "ENTE_ADMIN_SHARE_GROUPS": "a, b"})
    assert cfg.admin_groups == ["admins"] and cfg.share_groups == ["a", "b"]


@pytest.mark.parametrize("ua,name", [
    ("io.ente.photos/0.9.95 (iOS 18.1; Apple; iPhone16,1; abc)", "iPhone 15 Pro · iOS 18.1 · Ente 0.9.95"),
    ("io.ente.photos/1.0.2 (Android 15; Google; Pixel 8; abc)", "Pixel 8 · Android 15 · Ente 1.0.2"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ente/1.7.14 Chrome/130 Electron/33.0",
     "Ente Desktop 1.7.14 · macOS"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "Web (Firefox · Linux)"),
    ("Dart/3.5 (dart:io) android", "Android"),
    (None, "Unknown"),
])
def test_device_names(ua, name):
    assert device_name(ua) == name
