"""ente-admin [serve | hash-password | check-config]"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys

from . import __version__, auth, config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ente-admin", description="Admin dashboard for self-hosted Ente.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the dashboard (default)")
    h = sub.add_parser("hash-password", help="print a password hash for ENTE_ADMIN_USERS")
    h.add_argument("--stdin", action="store_true", help="read the password from stdin instead of a prompt")
    sub.add_parser("check-config", help="validate the configuration and exit")
    args = p.parse_args(argv)

    if args.cmd == "hash-password":
        pw = sys.stdin.readline().rstrip("\n") if args.stdin else getpass.getpass("Password: ")
        if len(pw) < 12:
            print("use at least 12 characters", file=sys.stderr)
            return 2
        print(auth.hash_password(pw))
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = config.load()
    except (config.ConfigError, OSError) as e:
        print(f"ente-admin: configuration error: {e}", file=sys.stderr)
        return 2
    if args.cmd == "check-config":
        print(f"ok: auth={cfg.auth}, instances={', '.join(f'{i.name}({i.source})' for i in cfg.instances)}, "
              f"modules={', '.join(cfg.modules)}, actions={'on' if cfg.enable_actions else 'off'}")
        return 0
    from .server import serve
    serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
