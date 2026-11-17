# Kubernetes

`base/` is a plain Deployment, Service and a small PVC; `overlays/example/` adds an Ingress,
header auth behind a login proxy, and a NetworkPolicy. Copy the overlay and adjust:

- the museum URL (`ENTE_ADMIN_PHOTOS_URL`), more instances via `ENTE_ADMIN_INSTANCES`
- `ENTE_ADMIN_TRUSTED_PROXIES`: your ingress controller's pod CIDR or addresses
- the Ingress host and your login proxy's annotations
- the NetworkPolicy's selector for your ingress controller

Create the Secret (or let your secrets operator do it):

```bash
kubectl -n ente-admin create secret generic ente-admin \
  --from-file=users=./users --from-file=token=./token
kustomize build overlays/example | kubectl apply -f -
```
