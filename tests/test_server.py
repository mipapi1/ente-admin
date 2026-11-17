import json
import urllib.error
import urllib.request

from conftest import basic


def get(url, auth=None, method="GET", data=None, headers=None):
    h = dict(headers or {})
    if auth:
        h["Authorization"] = auth
    req = urllib.request.Request(url, headers=h, method=method, data=data)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_login_required(demo_server):
    url = demo_server()
    code, headers, _ = get(url + "/")
    assert code == 401 and "Basic" in headers["WWW-Authenticate"]
    assert get(url + "/api/data")[0] == 401
    assert get(url + "/api/data", auth=basic(password="wrong"))[0] == 401
    assert get(url + "/healthz")[0] == 200


def test_security_headers(demo_server):
    url = demo_server()
    code, headers, body = get(url + "/", auth=basic())
    assert code == 200 and b"Ente admin" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"


def test_no_external_resources(demo_server):
    url = demo_server()
    for path in ("/", "/static/app.js", "/static/app.css"):
        body = get(url + path, auth=basic())[2].decode()
        assert "http://" not in body.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in body


def test_data_is_masked_by_default(demo_server):
    url = demo_server()
    data = json.loads(get(url + "/api/data", auth=basic())[2])
    accts = [a for i in data["instances"] for a in i["accounts"]]
    assert accts
    for a in accts:
        assert "•" in a["email"] and a["label"].startswith("acct-")
        assert "id" not in a


def test_reveal(demo_server):
    url = demo_server(ENTE_ADMIN_REVEAL_EMAILS="true")
    data = json.loads(get(url + "/api/data", auth=basic())[2])
    assert data["instances"][0]["accounts"][0]["email"].endswith("@example.com")


def test_portal_off_by_default(demo_server):
    url = demo_server()
    cfg = json.loads(get(url + "/api/config", auth=basic())[2])
    assert cfg["portal"] is False
    code, _, body = get(url + "/api/admin/run", auth=basic(), method="POST", data=b"{}",
                        headers={"X-Ente-Admin": "1", "Content-Type": "application/json"})
    assert code == 403 and b"off" in body
    assert get(url + "/api/admin/status", auth=basic())[0] == 403
