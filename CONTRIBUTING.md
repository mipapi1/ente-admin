# Contributing

- `pip install -e '.[dev,postgres]'`, then `pytest` and `ruff check .` must pass.
- UI changes: run demo mode and check light, dark and a narrow window. No external
  resources, no inline scripts or styles (the CSP blocks them), insert data as text.
- New data source: add a module in `src/ente_admin/sources/`, decorate a class with
  `@register("<kind>")`, return a `Snapshot`; leave fields you can't fill as `None`.
- New action: add it to `ACTIONS` in `src/ente_admin/portal/actions.py`. It stays behind
  `ENTE_ADMIN_ENABLE_ACTIONS` and the typed confirmation.
- Never log or return tokens, keys or unmasked identifiers.
- Test against Ente's quickstart in Docker (`tests/e2e/run.sh`) or demo mode, never a
  server with real users.
