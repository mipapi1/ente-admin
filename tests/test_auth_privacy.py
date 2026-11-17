from conftest import PASSWORD, basic

from ente_admin import auth
from ente_admin.privacy import mask_email, mask_id


def test_password_roundtrip(user_hash):
    assert auth.verify_password(PASSWORD, user_hash)
    assert not auth.verify_password("wrong", user_hash)
    assert not auth.verify_password(PASSWORD, "garbage")


def test_basic(user_hash):
    users = {"admin": user_hash}
    assert auth.check_basic(basic(), users) == "admin"
    assert auth.check_basic(basic(password="nope"), users) is None
    assert auth.check_basic(basic(user="eve"), users) is None
    assert auth.check_basic("Bearer x", users) is None
    assert auth.check_basic("Basic !!!", users) is None


def test_header_only_from_trusted_proxy():
    assert auth.check_header("192.0.2.10", "alice", ["192.0.2.0/24"]) == "alice"
    assert auth.check_header("198.51.100.5", "alice", ["192.0.2.0/24"]) is None
    assert auth.check_header("192.0.2.10", "", ["192.0.2.0/24"]) is None


def test_mask_email():
    assert mask_email("alice@example.com") == "a••••@e••••.com"
    assert mask_email(None) is None
    assert "alice" not in mask_email("alice@mail.example.org")


def test_mask_id_stable_and_salted():
    assert mask_id(42, "s1") == mask_id(42, "s1")
    assert mask_id(42, "s1") != mask_id(42, "s2")
    assert "42" not in mask_id(42, "s1")
