# Going back

The new dashboard is meant to run **next to** an existing one (its own Deployment,
Service and hostname), so going back is just using the old address and removing the new
deployment. Nothing it does changes Ente: it is read-only unless actions are enabled.

Before switching, note in your deployment repo:
- the previous dashboard's image (or ConfigMap) and the commit that deployed it;
- its hostname.

To revert: point users at the old hostname, delete the new deployment
(`kubectl delete -k <overlay>` or `docker compose rm ente-admin`), and revoke the admin
token you created for it (sign that session out in the Ente app or with the CLI). If you
created the `ente_dashboard` role only for this, `DROP ROLE ente_dashboard;`.

The first version of this dashboard is kept in [legacy/](../legacy/) for reference.
