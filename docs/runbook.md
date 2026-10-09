# Runbook

## 1. Install on one machine (Docker)

Needs Docker with the Compose plugin. Clone `ML-API` and `MLAPI-Supervisor` side by side.

```shell
cd MLAPI-Supervisor
cp .env.example .env
docker compose run --rm --no-deps supervisor python -m app.admin_key    # prints a key and its hash
```

Put the hash in `.env` as `ADMIN_API_KEYS`, set a long random `REDIS_PASSWORD`, then:

```shell
docker compose up -d --build
curl localhost:8001/health          # Supervisor (admin API, localhost only)
curl localhost:8000/health          # Router (what clients use)
```

Open `http://localhost:8001/docs` for the admin API. Both ports are bound to `127.0.0.1` by default; set
`ROUTER_BIND=0.0.0.0` to let other machines call the Router (put TLS in front of it), and **never** publish the
Supervisor port.

**Security note.** The Supervisor mounts the Docker socket, which is root-equivalent on the host. Run this stack only on
a device you trust. Model containers run with all capabilities dropped, `no-new-privileges`, CPU/memory limits and no
published ports, but on Docker they share one network and can reach each other (Kubernetes NetworkPolicies prevent this).

## 2. Day-to-day

```shell
export SUP=http://localhost:8001 ADMIN="X-Admin-Key: <your key>"

# register a model image that exists on this machine (or use "source": "ghcr" with ghcr.io/<org>/<package>)
curl -X POST $SUP/v1/models -H "$ADMIN" -H 'Content-Type: application/json' \
     -d '{"image": "my-model:latest", "source": "local"}'

curl $SUP/v1/approvals -H "$ADMIN"                              # what it asks for
curl -X POST $SUP/v1/approvals/1/approve -H "$ADMIN"            # or send {"cpu": "1", "memory": "2Gi"} to grant less

curl $SUP/v1/models/my-model -H "$ADMIN"                        # state, deploy history, schema, live status

# give a client access to some models; the key is shown once
curl -X POST $SUP/v1/keys -H "$ADMIN" -H 'Content-Type: application/json' \
     -d '{"name": "hospital-pipeline", "allowed_models": ["my-model", "ecg-*"]}'

# the client then calls the Router
curl -X POST localhost:8000/predict -H 'X-API-Key: mlapi_…' -H 'Content-Type: application/json' \
     -d '{"model": "my-model", "data": {"features": [1, 2, 3]}}'
```

Revoke a key with `DELETE /v1/keys/{id}`: it stops working immediately.

### Model author checklist

An image must listen on port 8000 and serve `/health`, `/predict`, `/info`, `/schema` (what `fastmlapi` does), run as
a non-root user, and carry these labels (all but the first are optional):

```dockerfile
LABEL mlapi.model=true \
      mlapi.model.name=my-model \
      mlapi.cpu=2 mlapi.memory=4Gi mlapi.gpu=false mlapi.disk=10Gi \
      mlapi.mode=sync
```

Defaults: 1 CPU, 2Gi memory, no GPU, 5Gi disk, `sync`. Use `mlapi.mode=queue` for long-running jobs (the Router then
exposes `/jobs` instead of `/predict`; needs `QUEUE_ACL_SECRET`). Asking for more resources in a later version
pauses the rollout until an admin approves; the previous version keeps running.

### What happens automatically

* **Sleep/wake.** A sync model with no traffic for 15 minutes (`PATCH /v1/models/{name}/config` with
  `{"idle_timeout_s": N}`, `0` = never) is stopped. The next client request gets `503` with `Retry-After`, and wakes it.
* **Rollback.** A new version that does not become ready (30 minutes by default) is replaced by the previous one.
* **Self-healing.** Every minute the Supervisor compares the database with Docker/Kubernetes and Redis, restarts
  workloads that disappeared, republishes routes and keys, and removes orphans.

## 3. Install on a server (Kubernetes / k3s)

```shell
kubectl apply -f deploy/k8s/00-namespaces.yaml -f deploy/k8s/10-rbac.yaml -f deploy/k8s/20-quota.yaml
kubectl -n mlapi-system create secret generic mlapi-redis --from-literal=password="$(openssl rand -hex 24)"
kubectl -n mlapi-system create secret generic mlapi-queue-redis --from-literal=password="$(openssl rand -hex 24)"
kubectl apply -f deploy/k8s/30-redis.yaml -f deploy/k8s/35-queue-redis.yaml
# image pull secret so the cluster can pull private model images from GHCR
kubectl -n mlapi-models create secret docker-registry ghcr-pull --docker-server=ghcr.io \
        --docker-username=<user> --docker-password=<token with read:packages>
# environment for the Supervisor and Router (hash from `python -m app.admin_key`)
kubectl -n mlapi-system create secret generic mlapi-supervisor-env \
  --from-literal=ADMIN_API_KEYS=<hash> --from-literal=GHCR_TOKEN=<token> \
  --from-literal=REDIS_URL=redis://:<password>@mlapi-redis:6379/0 \
  --from-literal=QUEUE_REDIS_URL=redis://:<queue password>@mlapi-queue-redis:6379/0 \
  --from-literal=MODEL_REDIS_URL=redis://mlapi-queue-redis.mlapi-system.svc:6379/0 \
  --from-literal=QUEUE_ACL_SECRET="$(openssl rand -hex 24)"
kubectl apply -f deploy/k8s/40-supervisor.yaml -f deploy/k8s/50-router.yaml
kubectl -n mlapi-system port-forward svc/mlapi-supervisor 8001:8000     # reach the admin API
```

Put an Ingress with TLS in front of `mlapi-router` only. Edit the quota in `20-quota.yaml` to match the node.

**GPU.** Install the NVIDIA driver and container toolkit on the node, then the device plugin with
`60-nvidia-time-slicing.yaml` (4 slices of one card). Slices share VRAM with no isolation: model authors should cap
their own usage (`gpu_memory_fraction` in `fastmlapi`). A model that needs a GPU when none is free shows
`waiting_for_gpu` instead of failing.

## 4. Operations

| Task | How |
|---|---|
| Rotate an admin key | Generate a new hash, add it to `ADMIN_API_KEYS` (comma-separated), restart the Supervisor, remove the old hash later |
| Back up | The SQLite file lives in the `supervisor_data` volume (`/data/supervisor.db`); with Postgres use `pg_dump`. Redis needs no backup |
| Redis was restarted or wiped | Nothing to do: the Supervisor republishes routes, schemas and keys within a minute, and recreates the queue-mode models' Redis users. Queued jobs are lost by design (persistence is off) |
| Queue-mode models stop responding | Their queue lives on a separate Redis (`queue-redis` / `mlapi-queue-redis`) because model code can run Lua scripts that hang a Redis server. Restart that Redis; routes, keys and sync models are unaffected |
| A deploy failed | `GET /v1/models/{name}` shows the reason in `deployments`. Fix the image, push a new `latest`, or `POST …/redeploy` |
| Roll back | `POST /v1/models/{name}/rollback` |
| Model stuck in `waiting_for_gpu` | Another model holds all GPU slices. Put it to sleep (`/sleep`) or remove it |
| Move to an external Postgres | Set `DATABASE_URL`; migrations run at startup. Copy data with your usual SQLite→Postgres tooling first |
| Move to an external Kubernetes | Run the Supervisor with `CLUSTER_BACKEND=kubernetes` and a `KUBECONFIG` for that cluster; make sure the Router can reach the services (an in-cluster Router needs to run there too) |
| See what happened | Subscribe a webhook (`POST /v1/observers/`) or read the container logs |

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Router answers `503 AuthBackendUnavailable` | Redis is down or the password in `REDIS_URL` differs between Router and Supervisor |
| Router answers `404` for a model that exists | The key's `allowed_models` does not include it (404 on purpose), or the model was never approved/deployed |
| Router answers `503 ModelUnavailable` | The model is `starting`, `sleeping` (a request wakes it) or `waiting_for_gpu`; see the `X-Model-Status` header |
| Model `failed` with "identity check failed" | The image's `/info` reports a different `name` than its `mlapi.model.name` label |
| Model `failed` with "timed out" | It did not report `model_loaded` in time; weights may be downloading slowly. Raise `DEPLOY_TIMEOUT` |
| Queue-mode model `failed`: "QUEUE_ACL_SECRET" | Set `QUEUE_ACL_SECRET` and redeploy |


## 6. Known limits (read before exposing this to people you don't trust)

* **Queue-mode (long-running job) models share a Redis with each other, not with the platform.** A model can list the *names*
  of the other queue models' keys and can hang that Redis with a script; it can never read other models' data or touch routes
  and API keys, which live on the control Redis. The Supervisor will not start a queue-mode model unless `QUEUE_REDIS_URL`
  points at that separate instance. If a queue model misbehaves, restart the queue Redis.
* **Docker installs:** model containers can reach each other, and the Supervisor holds the Docker socket (root on the host).
* **GPU memory is shared**, not isolated.
* **Disk limits are not enforced on Docker** (they are sized into the cache volume on Kubernetes).
* `fastmlapi`'s queue mode currently relies on a grace period instead of worker heartbeats (a worker that never reports is
  trusted after 10 s of running).
