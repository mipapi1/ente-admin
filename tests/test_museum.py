"""The museum source against a fake museum that mimics the admin API's responses."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ente_admin.config import Instance
from ente_admin.sources.museum import MuseumError, MuseumSource

USERS = {101: "alice@example.com", 102: "bob@example.com"}


def fake_museum(fresh_tokens):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            tok = self.headers.get("X-Auth-Token")
            if tok not in ("old", *fresh_tokens):
                return self._json(401, {"code": "UNAUTHORIZED"})
            if self.path.startswith("/admin/users"):
                if tok not in fresh_tokens:
                    return self._json(400, {"code": "BAD_REQUEST", "message": "Token is too old"})
                return self._json(200, {"users": [{"ID": i, "email": e, "creationTime": 1_700_000_000_000_000}
                                                  for i, e in USERS.items()]})
            if self.path.startswith("/admin/user?id="):
                uid = int(self.path.split("=")[1])
                return self._json(200, {
                    "user": {"ID": uid, "email": USERS[uid], "creationTime": 1_700_000_000_000_000},
                    "storageConsumed": 5 * 1024 ** 3, "photosFileCount": 1234,
                    "subscription": {"storage": 100 * 1024 ** 3, "expiryTime": 2_000_000_000_000_000},
                    "details": {"storageBonus": 10 * 1024 ** 3},
                    "tokens": [{"creationTime": 1_700_000_000_000_000, "lastUsedTime": 1_760_000_000_000_000,
                                "ua": "Dart/3.5 (dart:io) android", "app": "photos", "isDeleted": False},
                               {"creationTime": 1, "lastUsedTime": 2, "ua": "x", "app": "photos", "isDeleted": True}]})
            self._json(404, {})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def museum():
    fresh = set()
    httpd = fake_museum(fresh)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", fresh
    httpd.shutdown()


def make(tmp_path, url, token="old", fresh=None):
    (tmp_path / "tok").write_text(token)
    inst = Instance(name="photos", source="museum", url=url, token_file=str(tmp_path / "tok"), state_dir=str(tmp_path))
    if fresh is not None:
        (tmp_path / "fresh").write_text(fresh)
        inst.fresh_token_file = str(tmp_path / "fresh")
    return MuseumSource(inst)


def test_needs_a_fresh_token_once(tmp_path, museum):
    url, _ = museum
    with pytest.raises(MuseumError, match="less than 10 minutes"):
        make(tmp_path, url).snapshot()


def test_fresh_token_then_cache(tmp_path, museum):
    url, fresh = museum
    fresh.add("new")
    snap = make(tmp_path, url, fresh="new").snapshot()
    assert sorted(a.id for a in snap.accounts) == [101, 102]
    a = snap.accounts[0]
    assert a.quota == 110 * 1024 ** 3 and a.used == 5 * 1024 ** 3 and a.files == 1234
    assert [d.device for d in a.devices] == ["Android"]  # signed-out session left out
    assert a.albums is None and a.daily_uploads is None  # not available from the API
    cache = json.loads((tmp_path / "photos.account-ids.json").read_text())
    assert cache["ids"] == [101, 102] and "alice" not in json.dumps(cache)

    fresh.clear()  # the fresh token has aged out
    snap = make(tmp_path, url).snapshot()
    assert len(snap.accounts) == 2
    assert any("from cache" in n for n in snap.notes)


def test_bad_token(tmp_path, museum):
    url, _ = museum
    with pytest.raises(MuseumError, match="401"):
        make(tmp_path, url, token="wrong").snapshot()
