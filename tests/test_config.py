from pathlib import Path

import pytest

from ente_admin import config


def test_refuses_to_start_without_auth(tmp_path):
    with pytest.raises(config.ConfigError, match="authentication is required"):
        config.load({"ENTE_ADMIN_DEMO": "true"})


def test_no_auth_needs_flag_and_localhost():
    with pytest.raises(config.ConfigError, match="INSECURE_LOCALHOST"):
        config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_AUTH": "none"})
    with pytest.raises(config.ConfigError, match="localhost"):
        config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_AUTH": "none",
                     "ENTE_ADMIN_INSECURE_LOCALHOST": "true", "ENTE_ADMIN_HOST": "0.0.0.0"})
    cfg = config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_AUTH": "none", "ENTE_ADMIN_INSECURE_LOCALHOST": "true"})
    assert cfg.auth == "none"


def test_header_auth_needs_trusted_proxies():
    with pytest.raises(config.ConfigError, match="TRUSTED_PROXIES"):
        config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_AUTH": "header"})


def test_defaults_are_safe(user_hash):
    cfg = config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS": f"admin:{user_hash}"})
    assert cfg.host == "127.0.0.1"
    assert not cfg.enable_actions and not cfg.reveal_emails


def test_instances_from_env(tmp_path, user_hash):
    tok = tmp_path / "t"
    tok.write_text("x")
    cfg = config.load({"ENTE_ADMIN_USERS": f"admin:{user_hash}", "ENTE_ADMIN_INSTANCES": "photos,my-family",
                       "ENTE_ADMIN_PHOTOS_URL": "http://museum:8080", "ENTE_ADMIN_PHOTOS_TOKEN_FILE": str(tok),
                       "ENTE_ADMIN_MY_FAMILY_SOURCE": "demo"})
    assert [(i.name, i.source) for i in cfg.instances] == [("photos", "museum"), ("my-family", "demo")]


def test_secret_from_file(tmp_path, user_hash):
    f = tmp_path / "users"
    f.write_text(f"admin:{user_hash}\n")
    cfg = config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS_FILE": str(f)})
    assert "admin" in cfg.users


def test_config_file(tmp_path, user_hash):
    f = tmp_path / "c.toml"
    f.write_text(f'modules = ["overview", "accounts"]\n[users]\nadmin = "{user_hash}"\n'
                 '[[instances]]\nname = "x"\nsource = "demo"\n')
    cfg = config.load({"ENTE_ADMIN_CONFIG": str(f)})
    assert cfg.modules == ("overview", "accounts")


def test_unknown_module(user_hash):
    with pytest.raises(config.ConfigError, match="unknown modules"):
        config.load({"ENTE_ADMIN_DEMO": "true", "ENTE_ADMIN_USERS": f"admin:{user_hash}", "ENTE_ADMIN_MODULES": "nope"})


def test_examples_load():
    root = Path(__file__).parent.parent
    cfg = config.load({"ENTE_ADMIN_CONFIG": str(root / "config.example.toml")})
    assert [i.name for i in cfg.instances] == ["photos", "family"]
    assert cfg.instances[1].apps["share"] == "https://share.example.com" and cfg.smtp["port"] == "587"
    env = {}
    for line in (root / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k] = v
    assert env["ENTE_ADMIN_DEMO"] == "true" and env["ENTE_ADMIN_NOTIFY"] == "off"
    env["ENTE_ADMIN_USERS"] = "admin:pbkdf2_sha256$1$a$b"
    del env["ENTE_ADMIN_USERS_FILE"]
    assert config.load(env).instances[0].source == "demo"
