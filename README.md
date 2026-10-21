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

The Supervisor is **never** exposed to clients. It has an admin API and an admin UI (`/admin`); with `ENABLE_DOCS=true`
every endpoint is described by OpenAPI at `/docs`.

## Run it

**On one machine with Docker** (SQLite, no Kubernetes):

```shell
cp .env.example .env                  # fill in ADMIN_API_KEYS and REDIS_PASSWORD
docker compose up -d --build          # Router + Supervisor + Redis; the Router is built from ../ML-API
```

**On a server** with Kubernetes (k3s): see `deploy/k8s/` and the [runbook](docs/runbook.md).

Po polsku: [instalacja dla administratora](docs/pl/instalacja-admin.md) i
[przewodnik dla autorów modeli](docs/pl/przewodnik-autor-modelu.md) (jak przygotować obraz dla pollera GHCR i poprosić o zasoby).

## The idea in four steps

1. A model author builds an image with [`fastmlapi`](https://github.com/AI-MED-AGH/fast-ML-Api) and labels it
   (`mlapi.model=true`, plus the CPU/memory/GPU/disk it needs).
2. The image is registered (`POST /v1/models`) or found by polling GHCR. The resources it asks for wait for an admin.
3. An admin approves them (`POST /v1/approvals/{id}/approve`, optionally with lower values). Only then does it run,
   and only ever with the approved resources. An increase in a later version needs approval again, and the old
   version keeps serving until then.
4. An admin issues a client key limited to certain models (`POST /v1/keys`). Clients call the Router with it.

## Admin UI

Open `http://127.0.0.1:8001/admin` and sign in with an admin key (the same keys as `X-Admin-Key`). The UI manages models
(deploy, rollback, sleep/wake, config, delete), resource approvals, API keys and webhook observers. On a key's row,
**Edit** sets its limits and **Usage** shows what it was used for (requests, model errors and refusals per model, up to 30
days). A model's page has a **Rate limits** card that shows what the image asks for and what applies, and lets you override it.

- Login (`POST /v1/auth/login`) sets a signed `HttpOnly`, `SameSite=Strict` session cookie (`SESSION_TTL`, default 8 h; `Secure`
  automatically over https or `X-Forwarded-Proto: https`). The key is never stored in the browser. Cookie-authenticated
  mutations must send `X-Requested-With: mlapi-admin`; `X-Admin-Key` keeps working for scripts. Failed logins are rate limited.
- Set `SESSION_SECRET` to keep sessions across restarts. Removing a key from `ADMIN_API_KEYS` ends the sessions it created.
- Put it behind TLS if it is reachable beyond localhost. Do not expose the Supervisor publicly without it.
- Development: `cd ui && npm install && npm run dev` (proxies `/v1` to `SUPERVISOR_URL`, default `http://127.0.0.1:8001`);
  `npm test`, `npm run build`. The Docker image builds the UI and serves it from `UI_DIR`.

## Admin API (all under `/v1`, header `X-Admin-Key`)

| Area | Endpoints |
|---|---|
| Models | `POST /models` · `GET /models` · `GET /models/{name}` · `PATCH /models/{name}/config` · `POST /models/{name}/redeploy\|rollback\|sleep\|wake` · `DELETE /models/{name}` |
| Approvals | `GET /approvals` · `POST /approvals/{id}/approve\|reject` |
| Client keys | `POST /keys` (returns the key once) · `GET /keys` · `PATCH /keys/{id}` · `DELETE /keys/{id}` · `GET /keys/{id}/usage?days=7` |
| Webhooks | `POST /observers/` · `GET /observers/` · `DELETE /observers/{id}` |

`GET /health` needs no key. The API description (`/docs`, `/openapi.json`) is off unless `ENABLE_DOCS=true`.

### Limits

A key can carry `limits` (`rpm`, `poll_rpm`, `jobs_per_day`, `max_outstanding_jobs`; `0` = unlimited, absent = the Router's
default). A model's limits come from its image labels, `mlapi.rate.rpm` (per key), `mlapi.rate.total_rpm` (all keys),
`mlapi.rate.concurrency` and, for queue-mode models, `mlapi.queue.max_pending`, or from an admin:
`PATCH /v1/models/{name}/config` with `{"limits": {"rpm_per_key": 30, "rpm_total": 300, "max_concurrency": 4, "max_pending": 500}}`.
An admin's values win, and `{"limits": {}}` goes back to the image's. Values from labels are lowered to the ceilings below and
cannot be unlimited, so a model author can protect their model but not lift the protection. Everything except `max_pending`
takes effect immediately; a new `max_pending` redeploys the model, because the model enforces it. `GET /models/{name}` shows
`declared_limits` (from the image) and `limits` (what applies).

### Admin keys and the audit log

`ADMIN_API_KEYS` entries may be named: `alice:<hash>,bob:<hash>`. Approvals record who decided, and every change made with an
admin key, and every failed attempt, is logged on the `app.audit` logger without the key. A plain hash is called `admin`.

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
| `QUEUE_LEGACY_ACL` | `true` only for models built with an older fastmlapi (Lua scripts, `SCAN`): grants broader Redis permissions and requires a dedicated queue Redis |
| `KEY_MAX_LIFETIME_SECONDS` | `0` (default) lets keys live forever. Otherwise every key, including `allow_all` ones, needs an expiry no further away than this |
| `LIMIT_CEILING_RPM_PER_KEY`, `LIMIT_CEILING_RPM_TOTAL`, `LIMIT_CEILING_CONCURRENCY`, `LIMIT_CEILING_MAX_PENDING` | The most an image's own rate labels may ask for (600, 6000, 64, 10000). Admin values are not capped |
| `DEFAULT_MAX_PENDING` | Queued jobs a queue-mode model accepts before answering 429 (1000) |
| `DOCKER_ISOLATE_MODELS`, `DOCKER_PEERS` | Docker backend: give every model a network of its own so models cannot reach each other. `DOCKER_PEERS` names the containers (Router, Supervisor) attached to each of them; `docker-compose.yml` sets both |
| `ENABLE_DOCS` | Serve `/docs` and `/openapi.json` (off by default) |
| `GHCR_ORG`, `GHCR_TOKEN` | Optional: poll this organisation's container registry for model images |

## Development

```shell
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                    # unit tests, no Docker or cluster needed
RUN_DOCKER_TESTS=1 pytest tests/test_docker_real.py     # also exercises a real Docker engine
```

Design: `docs/superpowers/specs/2026-10-08-supervisor-design.md` in the workspace that holds both repositories.
