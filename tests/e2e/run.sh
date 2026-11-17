#!/usr/bin/env bash
# End-to-end test against a real museum: Ente's official quickstart stack in Docker,
# seeded with one synthetic admin account. Tests the museum and postgres sources.
# Never touches anything but the throwaway stack it creates (removed on exit).
#
#   IMAGE=ente-admin:dev tests/e2e/run.sh
#   MUSEUM_IMAGE=ghcr.io/ente/server:<tag> ...    # default: the quickstart's latest
set -euo pipefail
cd "$(dirname "$0")"
IMAGE=${IMAGE:-ente-admin:dev}
UPSTREAM_COMMIT=${UPSTREAM_COMMIT:-20fb6316bd98b1af613ef771c684d5cd46fbf371}
PROJECT=ente-admin-e2e
WORK=$(mktemp -d)
cleanup() {
  (cd "$WORK/my-ente" 2>/dev/null && docker compose -p "$PROJECT" down -v >/dev/null 2>&1) || true
  docker rm -f "$PROJECT-admin" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "== quickstart (upstream $UPSTREAM_COMMIT)"
curl -fsSL "https://raw.githubusercontent.com/ente-io/ente/$UPSTREAM_COMMIT/server/quickstart.sh" -o "$WORK/quickstart.sh"
(cd "$WORK" && echo n | sh quickstart.sh >/dev/null)
cd "$WORK/my-ente"
if [ -n "${MUSEUM_IMAGE:-}" ]; then
  printf 'services:\n  museum:\n    image: %s\n' "$MUSEUM_IMAGE" > compose.override.yaml
fi
docker compose -p "$PROJECT" up -d --wait postgres >/dev/null
docker compose -p "$PROJECT" up -d museum socat >/dev/null
for _ in $(seq 1 60); do curl -sf localhost:8080/ping >/dev/null && break; sleep 2; done
curl -sf localhost:8080/ping >/dev/null || { docker compose -p "$PROJECT" logs museum | tail -20; exit 1; }

psql() { docker compose -p "$PROJECT" exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' ; }

echo "== seed one synthetic admin account"
key=$(sed -n 's/^ *encryption: *//p' museum.yaml | head -1)
token=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' )
sql=$(printf '%s\n%s\n' "$key" "$token" | docker run --rm -i --entrypoint python "$IMAGE" -c '
import base64, hashlib, sys, time, nacl.secret, nacl.utils
key = base64.b64decode(sys.stdin.readline().strip()); token = sys.stdin.readline().strip()
box = nacl.secret.SecretBox(key); nonce = nacl.utils.random(24)
enc = box.encrypt(b"e2e-admin@example.com", nonce).ciphertext
now = int(time.time() * 1e6)
print(f"INSERT INTO users (creation_time, encrypted_email, email_decryption_nonce, email_hash) VALUES "
      f"({now}, decode(%s, %s), decode(%s, %s), %s);"
      % (repr(enc.hex()), repr("hex"), repr(nonce.hex()), repr("hex"), repr("e2e")))
print(f"INSERT INTO tokens (user_id, creation_time, token_hash, app) SELECT user_id, {now}, "
      f"decode({hashlib.sha256(token.encode()).hexdigest()!r}, %s), %s FROM users WHERE email_hash = %s;"
      % (repr("hex"), repr("photos"), repr("e2e")))
')
echo "$sql" | psql >/dev/null
# the read-only role from docs/POSTGRES.md
ro_pass=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
psql >/dev/null <<SQL
CREATE ROLE ente_dashboard LOGIN PASSWORD '$ro_pass';
GRANT CONNECT ON DATABASE ente_db TO ente_dashboard;
GRANT USAGE ON SCHEMA public TO ente_dashboard;
GRANT SELECT ON users, subscriptions, usage, trash TO ente_dashboard;
GRANT SELECT (owner_id, is_deleted) ON collections TO ente_dashboard;
GRANT SELECT (owner_id, updation_time, info) ON files TO ente_dashboard;
GRANT SELECT (user_id, app, user_agent, creation_time, last_used_at, is_deleted) ON tokens TO ente_dashboard;
SQL

echo "== run ente-admin against it"
secrets="$WORK/secrets"; mkdir -p "$secrets"; chmod 700 "$secrets"
printf '%s' "$token" > "$secrets/token"
printf '%s' "$ro_pass" > "$secrets/pg"
printf '%s' "$key" > "$secrets/email-key"
pw=$(head -c 18 /dev/urandom | base64)
hash=$(printf '%s\n' "$pw" | docker run --rm -i "$IMAGE" hash-password --stdin)
chmod 644 "$secrets"/*
net=$(docker inspect "$(docker compose -p "$PROJECT" ps -q postgres)" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker run -d --name "$PROJECT-admin" --network "$net" -p 127.0.0.1:18000:8000 \
  -v "$secrets:/secrets:ro" \
  -e "ENTE_ADMIN_USERS=e2e:$hash" -e ENTE_ADMIN_INSTANCES=api,db \
  -e ENTE_ADMIN_API_SOURCE=museum -e ENTE_ADMIN_API_URL=http://museum:8080 \
  -e ENTE_ADMIN_API_TOKEN_FILE=/secrets/token \
  -e ENTE_ADMIN_DB_SOURCE=postgres -e "ENTE_ADMIN_DB_DSN=host=postgres dbname=ente_db user=ente_dashboard" \
  -e ENTE_ADMIN_DB_PASSWORD_FILE=/secrets/pg -e ENTE_ADMIN_DB_EMAIL_KEY_FILE=/secrets/email-key \
  "$IMAGE" >/dev/null
for _ in $(seq 1 30); do curl -sf localhost:18000/healthz >/dev/null && break; sleep 1; done

fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' localhost:18000/api/data)" = 401 ] && ok "login required" || bad "login required"
curl -sf -u "e2e:$pw" localhost:18000/api/data > "$WORK/data.json"
python3 - "$WORK/data.json" <<'PY' && ok "both sources list the masked account" || bad "both sources list the masked account"
import json, sys
d = {i["instance"]: i for i in json.load(open(sys.argv[1]))["instances"]}
for name in ("api", "db"):
    snap = d[name]
    assert not snap["error"], (name, snap["error"])
    assert len(snap["accounts"]) == 1, (name, snap["accounts"])
    a = snap["accounts"][0]
    assert a["email"] == "e••••@e••••.com", a["email"]
    assert a["label"].startswith("acct-") and "id" not in a
assert d["db"]["days"] and d["db"]["accounts"][0]["daily_uploads"] is not None
assert d["api"]["accounts"][0]["albums"] is None  # the API doesn't report albums
PY
grep -q 'e2e-admin@' "$WORK/data.json" && bad "plain email in the response" || ok "no plain email in the response"
grep -qF "$token" "$WORK/data.json" && bad "token in the response" || ok "no token in the response"
docker logs "$PROJECT-admin" 2>&1 | grep -qF "$token" && bad "token in the logs" || ok "no token in the logs"
[ "$fail" = 0 ] && echo "e2e passed" || { docker logs "$PROJECT-admin" | tail -20; exit 1; }
