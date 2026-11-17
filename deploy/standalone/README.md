# Standalone (no Docker)

Python 3.11 or newer.

```bash
python3 -m venv /opt/ente-admin && /opt/ente-admin/bin/pip install ente-admin          # or: pip install .
/opt/ente-admin/bin/ente-admin hash-password                                         # -> ENTE_ADMIN_USERS
sudo install -d -m 700 -o ente-admin /etc/ente-admin /var/lib/ente-admin
sudoedit /etc/ente-admin/env          # see env.example; secrets go in files under /etc/ente-admin
sudo cp ente-admin.service /etc/systemd/system/ && sudo systemctl enable --now ente-admin
```

It listens on 127.0.0.1:8000 by default. Put a reverse proxy with TLS in front of it.
For the postgres source: `pip install 'ente-admin[postgres]'`.
