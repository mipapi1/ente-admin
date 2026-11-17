import base64
import threading
from http.server import ThreadingHTTPServer

import pytest

from ente_admin import auth, config
from ente_admin.server import Dashboard, make_handler

PASSWORD = "correct horse battery"


@pytest.fixture(scope="session")
def user_hash():
    return auth.hash_password(PASSWORD, iterations=1000)


def basic(user="admin", password=PASSWORD):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.fixture
def demo_server(tmp_path, user_hash):
    def start(**env):
        e = {"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS": f"admin:{user_hash}",
             "ENTE_ADMIN_STATE_DIR": str(tmp_path), **env}
        cfg = config.load(e)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Dashboard(cfg)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"
    servers = []
    yield start
    for s in servers:
        s.shutdown()
