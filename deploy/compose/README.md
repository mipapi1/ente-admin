# Docker Compose (next to Ente's quickstart)

`ente-admin.yaml` is an extra compose file for the `my-ente` directory that Ente's
[quickstart](https://ente.com/help/self-hosting/) creates. Because it's merged into the same
project, ente-admin joins the quickstart's network and reaches museum as `http://museum:8080`.

1. **An admin account.** Museum treats accounts listed under `internal.admins` in
   `museum.yaml` as admins (or the first account, if none are listed). A separate account
   used only for administration is a good idea: its token is a full login for that account.
2. **Its token.** With the [Ente CLI](https://github.com/ente-io/ente/tree/main/cli) pointed
   at your server, `ente account add`, then
   `ente account get-token --app photos --email <admin email> > ente-admin/secrets/token`.
3. **A dashboard login.** `docker run --rm -it ghcr.io/mipapi1/ente-admin hash-password`, and
   put `admin:<hash>` in `ente-admin/secrets/users` (one `name:hash` per line).
4. `docker compose -f compose.yaml -f ente-admin.yaml up -d ente-admin` and open
   http://localhost:8001.

**The account list.** Museum only lists accounts for a token less than 10 minutes old. Right
after step 2 the token is fresh, so the first start fetches the list and caches the account
ids in the state volume. To pick up new accounts later, log in again (`ente account add`)
and write the new token to `ente-admin/secrets/fresh-token` within 10 minutes; it's read on
the next refresh.

**More detail** (albums, trash, uploads per day): add a `postgres` instance pointing at the
quickstart's database with a read-only role, see [docs/POSTGRES.md](../../docs/POSTGRES.md).
