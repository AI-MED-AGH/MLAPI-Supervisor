# MLAPI Supervisor

The control plane of the MLAPI platform. It finds model images, deploys them within resource limits an admin
approved, keeps the [Router](https://github.com/AI-MED-AGH/ML-API) informed about where models are, manages client
API keys, puts idle models to sleep and wakes them on demand, and notifies webhook observers about what happens.

```
 client ──► Router (public) ──► model container          Supervisor (admin only)
              │  reads                  ▲                   │ deploys / scales / watches
              ▼                         └───────────────────┤
            Redis  ◄── routes, keys, schemas ───────────────┘
```

The Supervisor is **never** exposed to clients. It has an admin API only (no UI yet; every endpoint is described by
OpenAPI at `/docs`).

## Run it

**On one machine with Docker** (SQLite, no Kubernetes):

```shell
cp .env.example .env                  # fill in ADMIN_API_KEYS and REDIS_PASSWORD
docker compose up -d --build          # Router + Supervisor + Redis; the Router is built from ../ML-API
```

**On a server** with Kubernetes (k3s): see `deploy/k8s/` and the [runbook](docs/runbook.md).

## The idea in four steps

1. A model author builds an image with [`fastmlapi`](https://github.com/AI-MED-AGH/fast-ML-Api) and labels it
   (`mlapi.model=true`, plus the CPU/memory/GPU/disk it needs).
2. The image is registered (`POST /v1/models`) or found by polling GHCR. The resources it asks for wait for an admin.
3. An admin approves them (`POST /v1/approvals/{id}/approve`, optionally with lower values). Only then does it run,
   and only ever with the approved resources. An increase in a later version needs approval again, and the old
   version keeps serving until then.
4. An admin issues a client key limited to certain models (`POST /v1/keys`). Clients call the Router with it.

## Admin API (all under `/v1`, header `X-Admin-Key`)

| Area | Endpoints |
|---|---|
| Models | `POST /models` · `GET /models` · `GET /models/{name}` · `PATCH /models/{name}/config` · `POST /models/{name}/redeploy\|rollback\|sleep\|wake` · `DELETE /models/{name}` |
| Approvals | `GET /approvals` · `POST /approvals/{id}/approve\|reject` |
| Client keys | `POST /keys` (returns the key once) · `GET /keys` · `PATCH /keys/{id}` · `DELETE /keys/{id}` |
| Webhooks | `POST /observers/` · `GET /observers/` · `DELETE /observers/{id}` |

`GET /health` needs no key.

Webhook events: `deploy.started`, `deploy.succeeded`, `deploy.failed`, `approval.requested`, `approval.approved`,
`approval.rejected`, `model.sleeping`, `model.woke`, `model.waiting_for_gpu`, `model.removed`. Payload:
`{event, model, digest, status, timestamp, details}`.

## Configuration

Environment variables (see `.env.example`). The ones that matter:

| Variable | Meaning |
|---|---|
| `ADMIN_API_KEYS` | Comma-separated SHA-256 hashes of admin keys. Generate with `python -m app.admin_key` |
| `REDIS_URL` | Redis shared with the Router (password required) |
| `CLUSTER_BACKEND` | `docker` (single device, default), `kubernetes`, or `fake` (tests) |
| `DATABASE_URL` | Empty = SQLite file in `DATA_DIR`; set a Postgres URL (`postgresql+asyncpg://…`) for an external DB |
| `QUEUE_ACL_SECRET` | Needed for queue-mode (long-running job) models: derives each model's Redis password |
| `GHCR_ORG`, `GHCR_TOKEN` | Optional: poll this organisation's container registry for model images |

## Development

```shell
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                    # unit tests, no Docker or cluster needed
RUN_DOCKER_TESTS=1 pytest tests/test_docker_real.py     # also exercises a real Docker engine
```

Design: `docs/superpowers/specs/2026-10-08-supervisor-design.md` in the workspace that holds both repositories.
