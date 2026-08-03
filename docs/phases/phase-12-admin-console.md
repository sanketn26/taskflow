# Phase 12 — Admin Console (Flower-class)

> **Implementation:** This phase is already a detailed product contract (API,
> UI, probes). The hands-on path and review checklist are at the bottom under
> **Implementation Guide**. Prerequisites: Phases 0–4 minimum; cluster/Kafka
> UI only if Phases 5/7 green.

## Goal

Ship a Flower-class real-time admin console for local and cluster Taskwire
agents, plus the ops HTTP plane that production deployers actually need:

1. **Liveness / readiness / startup probes** for Kubernetes and microVM
   supervisors.
2. **Prometheus metrics** exposition with bounded labels.
3. **Versioned admin JSON API** (`/admin/api/v1`) as the single source of
   truth for operator UIs and automation.
4. **Embedded web UI** (Flower analogue) served from the same agent process.

The console is an **operator tool**. It is not a task-submit SDK, not a log
warehouse, not a distributed tracer, and not a substitute for long-term
Grafana retention and alerting.

## Feature Gate

This phase is post-MVP and independently gated.

| Prerequisite | Required for | Notes |
|--------------|--------------|-------|
| Phases 0–4 exit green | Entire phase | Single-node agent, STATUS, workers, tasks, results |
| Phase 2 production IPC + status | Live/ready predicates, task/worker accounting | Shared with socket `STATUS` |
| Phase 5 exit green | Cluster topology, transfers, aggregate overview only | UI/API return honest “disabled” otherwise |
| Phase 7 exit green | Kafka outbox panels and dead-letter mutation only | Same honesty rule |
| Phase 8 packaging patterns | Preferred for release artifacts | May implement against Phase 2–4 agent earlier; must not invent a second daemon |

Phases 0–4 may ship without this console. Phase 8 may mention health and
metrics without implementing the full Flower UI; this phase freezes the
operator UX contract and its tests. When Phase 5 or 7 has not passed, the
corresponding routes and nav items stay absent or return a stable
`feature_disabled` error—never a partial response that implies clustering or
Kafka is active.

## Phase 0 Baseline

Extend the single packaged `taskwire-agent` binary and root Make/CI paths.
Do not introduce a mandatory second long-running process for the default
console. Do not download UI assets at runtime or on first request.

Multi-agent console tests compose existing `AgentHarness` instances and, for
cluster views, `harness/cluster.py`. All waits use `wait_until` with
deadlines. Admin mutations that affect process lifecycle (drain, worker
restart) are recorded on the seeded `ChaosTimeline` when exercised under
chaos markers.

Pytest markers:

| Marker | Use |
|--------|-----|
| `admin` | Admin API and UI suites (this phase) |
| `integration` | Default localhost admin E2E with harness |
| `cluster` | Aggregate overview and transfer views |
| `kafka` | Outbox list and dead-letter mutation |
| `chaos` | Drain under load, restart mid-lease |
| `resource` | Probe/metrics under FD or storage pressure where relevant |

Default CI tiers that do not enable `http.admin` must remain green without
binding an ops port.

## Testable Outcome

**Local.** With admin HTTP enabled on a single-node agent, an operator opens
the documented admin URL, sees queue depth by state, workers and active
leases, drills into task **metadata** (never payloads), cancels a queued
task, restarts a managed worker, and observes that the UI ready badge,
socket `STATUS.ready`, and `GET /readyz` always agree.

**Cluster (Phase 5+).** The same console shows membership, origin vs
executor, and in-flight transfers via **server-side fan-in**. The browser
never speaks the cluster task protocol or memberlist ports.

**Orchestration.** A kubelet- or microVM-style HTTP client can drive
startup/liveness/readiness solely from `/startupz`, `/livez`, and `/readyz`
without Unix-socket access.

## Product Shape (Flower analogue)

```text
Celery + Redis/Rabbit     →  taskwire-agent (state, leases, workers, peers)
Flower                    →  taskwire /admin UI + /admin/api/v1  (this phase)
```

| Flower concept | Taskwire surface | Taskwire constraint |
|----------------|------------------|---------------------|
| Dashboard | `/admin/` + `GET .../overview` | live/ready badges share probe predicates |
| Workers | Workers screen + `GET .../workers` | pool restart circuit breaker visible |
| Tasks | Tasks screen + list/detail API | no args/kwargs/result bytes by default |
| Task revoke | `POST .../tasks/{id}/cancel` | queued-only; `too_late` otherwise (honest) |
| Worker shutdown/pool restart | `POST .../workers/{id}/restart` | fencing handles in-flight leases |
| Broker tab | storage + retention + outbox stats | not a Redis browser |
| Monitor charts | short-window rates on overview | Grafana for long retention |
| (missing in Flower) | Cluster topology + transfers | one-hop ownership model |
| Broker login | `auth.mode: static_token` | probes stay unauthenticated |

### Explicit non-goals

- OIDC/SSO, LDAP, multi-tenant RBAC
- In-browser cloudpickle / payload debugger
- Full log search or OpenTelemetry trace UI
- YAML config writer or hot-reload of production config
- Bulk queue purge in v1
- Force-complete or unfence a lease from the UI
- Windows beyond Phase 1 agent platform scope
- Replacing Prometheus/Grafana for historical SLOs
- Serving admin on the cluster task port or memberlist port

---

## Architecture

```text
                    ┌──────────────────────────────────────┐
 Browser / curl     │  ops HTTP (http.listen_addr)         │
 kubelet / microVM  │  /livez  /readyz  /startupz          │
 Prometheus         │  /metrics                            │
                    │  /admin/          (embedded UI)      │
                    │  /admin/api/v1/*  (JSON)             │
                    └──────────────────┬───────────────────┘
                                       │ in-process
                    ┌──────────────────▼───────────────────┐
                    │  taskwire-agent                      │
                    │  ready predicates · status · metrics │
                    │  TaskStateStore · workers · cluster  │
                    └──────────────────┬───────────────────┘
                                       │ Unix socket (unchanged)
                    Runtime / workers / CLI STATUS (admin role)
```

Rules:

1. **One process** serves task IPC (Unix socket) and optional ops HTTP.
2. Ops HTTP is **optional**; empty `listen_addr` preserves laptop/CI defaults.
3. Admin API is **node-local privileged** (sees this agent’s tasks), not
   owner-scoped like Runtime `TASK_QUERY`. Document that difference.
4. Cluster aggregate uses **server-side** HTTP fan-in to peer
   `advertise_url` values with timeouts; partial failure is data, not 500.
5. The socket `admin` role remains CLI/harness only (`Status`); browsers
   do not speak gRPC over the agent socket.

---

## Ops HTTP Surface

### Binding and lifecycle

- When `http.listen_addr` is non-empty, the agent binds **before** or
  atomically with marking `Started()` so `/startupz` can flip to 200 after
  storage open and migrations.
- Bind failures (address in use, permission) fail agent startup with a clear
  error—do not run “half up” with IPC only when the operator requested HTTP.
- The HTTP server uses bounded timeouts:
  - read header timeout ≤ 5s
  - read/write timeouts sufficient for list APIs but not unbounded
  - aggregate fan-in peer timeout default 2s per peer, overall budget ≤ 5s
- Graceful shutdown: stop accepting HTTP after SIGTERM readiness flip; in-flight
  admin requests finish within a short bound or are cancelled; then Phase 2
  drain order continues.
- `GOMAXPROCS` / worker pools are unaffected by HTTP concurrency; admin
  handlers must not hold state-store write locks longer than a single
  bounded query.

### Paths (normative)

| Method | Path | Auth | Success | Failure |
|--------|------|------|---------|---------|
| `GET` | `/livez` | none | **200** | **503** |
| `GET` | `/healthz` | none | alias of `/livez` | same |
| `GET` | `/readyz` | none | **200** | **503** |
| `GET` | `/startupz` | none | **200** | **503** |
| `GET` | `/metrics` | none (document network isolation) | **200** text | **404** if metrics disabled; **503** if registry unavailable |
| `GET` | `/admin/` | admin auth when configured | **200** HTML | **401**/**403**/**404** |
| `GET` | `/admin/*` | same | static assets | same |
| `*` | `/admin/api/v1/*` | admin auth when configured | **200** JSON | see error schema |

`GET /` may return a tiny plain-text or JSON index of links for humans, or
**404**. It must not redirect into admin without auth checks.

### Probe response contract

Content-Type: `application/json; charset=utf-8` for probes (kubelet ignores
body; microVM supervisors and humans use it).

**Liveness body (200):**

```json
{
  "status": "live",
  "live": true,
  "version": "0.1.0",
  "pid": 1234,
  "watchdog_ok": true
}
```

**Liveness body (503):**

```json
{
  "status": "not_live",
  "live": false,
  "version": "0.1.0",
  "pid": 1234,
  "watchdog_ok": false,
  "reasons": ["watchdog_stale"]
}
```

**Readiness body (200):**

```json
{
  "status": "ready",
  "live": true,
  "ready": true,
  "started": true,
  "version": "0.1.0",
  "reasons": []
}
```

**Readiness body (503):**

```json
{
  "status": "not_ready",
  "live": true,
  "ready": false,
  "started": true,
  "version": "0.1.0",
  "reasons": ["storage_unhealthy", "draining"]
}
```

**Startup body:** same shape with `started` true/false; **503** until first
successful transition to started (config loaded, stores opened, migrations
applied, socket listening, worker manager started if configured). After
started becomes true once, `/startupz` stays **200** for process life even
if readiness later flaps (k8s startupProbe semantics).

`reasons` is a bounded array of stable snake_case tokens from this registry
(extend only with docs + tests):

| Token | Affects |
|-------|---------|
| `watchdog_stale` | live |
| `not_started` | startup, ready |
| `socket_not_listening` | ready |
| `storage_unhealthy` | ready (if `require_storage`) |
| `object_store_unhealthy` | ready (if `require_storage`) |
| `draining` | ready |
| `workers_below_min` | ready (if `require_workers`) |
| `cluster_unavailable` | ready (if `require_cluster`) |
| `kafka_unavailable` | ready (if `fail_on_kafka`) |

### Live / Ready / Started predicates

Implement a single package used by probes, `STATUS`, and admin overview:

```text
Started()  → initial init complete (sticky true for process life after success)
Live()     → supervision watchdog tick within bound
             # Independent of ops HTTP. Default agents (empty listen_addr)
             # report live==true when the agent loop is healthy.
             # Optional: when listen_addr is set, ALSO require the ops HTTP
             # accept loop to be up—never require HTTP when it is not configured.
Ready()    → Started()
             AND not draining
             AND socket accepting
             AND (NOT require_storage OR (state healthy AND default object store healthy))
             AND (NOT require_workers OR active_workers >= min_workers)
             AND (NOT require_cluster OR cluster membership usable)
             AND (NOT fail_on_kafka OR kafka publisher healthy)
```

**Liveness is not “HTTP up.”** Socket `STATUS.live`, harness checks, and any
future non-HTTP supervisors use the same `Live()` predicate. Hitting
`GET /livez` already proves the HTTP stack answered for that request; the
predicate itself must remain meaningful when `http.listen_addr` is empty
(the default). Config field names under `http.watchdog_*` still apply: the
watchdog is an agent-wide supervision signal, not an HTTP-only feature.

**Watchdog.** A monotonic loop (or the existing scheduling/reaper loop)
updates `last_tick` at least every `http.watchdog_interval_ms` when the
`http` block is present, otherwise every `queue.reaper_interval_ms` (or 1000ms
if neither applies). Live fails if `now - last_tick` exceeds
`http.watchdog_stale_ms` when configured, else `3 ×` the tick interval. Do
**not** fail live on SQLite busy, slow object put, worker crash loops, Kafka
lag, empty membership, or ops HTTP being disabled.

**Drain.** On SIGTERM or `POST /admin/api/v1/drain`:

1. Set `draining = true` → `Ready() == false` immediately.
2. Reject new SUBMIT with `shutdown` (retryable) or equivalent Phase 2 rule.
3. Stop new claims; allow in-flight workers for `workers.shutdown_grace_ms`.
4. Then Phase 2 shutdown order.

**STATUS alignment.** Extend `StatusSnapshot` (Phase 1 forward-compatible
ignore-unknown on decoders; this phase **adds** fields the agent must emit):

```text
StatusSnapshot {
  version: string,
  pid: uint64,
  ready: bool,                 # == Ready()
  live: bool,                  # == Live()   (new)
  started: bool,               # == Started() (new)
  not_ready_reasons: array<string>,  # new; same tokens as probes
  task_counts: map<string,uint64>,
  active_leases: uint64,
  worker_pids: array<uint64>,
  worker_restarts: uint64,
  storage_healthy: bool,
  cluster_members: uint64,
  kafka_outbox_pending: uint64,
  last_error_code: string | nil,
  admin_http_enabled: bool,    # new
  ops_listen_addr: string      # new; may be empty; never include secrets
}
```

Harness `_socket_responds()` continues to require `ready`; optional tests
assert `live` independently.

### Metrics (Prometheus)

When `http.metrics.enabled` is true and listen_addr is set, `GET` on
`http.metrics.path` (default `/metrics`) returns Prometheus text exposition
(`Content-Type: text/plain; version=0.0.4; charset=utf-8` or OpenMetrics if
the chosen client library defaults to it—pick one and test it).

**Label rules:** only low-cardinality labels from this fixed allowlist
(implementations must not emit series labels outside it):

| Label | Allowed values / bounds |
|-------|-------------------------|
| `version` | agent release version string (one series via `taskwire_build_info`) |
| `state` | task/transfer states from the protocol enum |
| `pool` | configured pool names |
| `runtime` | `python` \| `nodejs` \| `go` |
| `result` | small enums: `accepted`\|`rejected`, `succeeded`\|`failed`\|`cancelled`, `ok`\|`error`, … |
| `code` | stable protocol/admin error codes only |
| `backend` | `state` \| `objects` or registered backend type names |
| `node` | `cluster.node_name` (bounded membership set) |
| `resource` | `memory` \| `cpu` \| `fd` |
| `role` | control-plane roles: `runtime` \| `worker` \| `admin` |
| `route` | fixed admin route tokens (see below), not raw URL paths |
| `action` | fixed mutation names: `cancel` \| `drain` \| `worker_restart` \| `kafka_dead_letter` |

**`route` allowlist (closed set):** `overview`, `tasks_list`, `tasks_get`,
`tasks_cancel`, `workers`, `workers_restart`, `leases`, `capabilities`,
`objects_stats`, `config_redacted`, `cluster_members`, `cluster_transfers`,
`cluster_overview`, `kafka_outbox`, `kafka_dead_letter`, `events`, `other`.
Unknown handlers map to `other`—never to the request path string.

**Forbidden as labels:** task_id, owner_id, lease_id, transfer_id, object
key, DSN, raw URL path, peer URL, worker_id (unbounded churn).

**Minimum series (names normative):** every label used below is on the
allowlist above.

| Name | Type | Labels | Meaning |
|------|------|--------|---------|
| `taskwire_build_info` | gauge | `version` | constantly 1 |
| `taskwire_agent_live` | gauge | | 1 if Live() |
| `taskwire_agent_ready` | gauge | | 1 if Ready() |
| `taskwire_tasks` | gauge | `state` | count by state |
| `taskwire_active_leases` | gauge | | |
| `taskwire_task_submit_total` | counter | `result` | accepted\|rejected |
| `taskwire_task_complete_total` | counter | `result` | succeeded\|failed\|cancelled |
| `taskwire_task_submit_to_complete_seconds` | histogram | | |
| `taskwire_lease_stale_total` | counter | | reaper expiries |
| `taskwire_dead_letters_total` | counter | | |
| `taskwire_workers` | gauge | `pool`, `runtime` | running processes |
| `taskwire_worker_restarts_total` | counter | `pool` | |
| `taskwire_worker_circuit_open` | gauge | `pool` | 0/1 |
| `taskwire_ipc_connections` | gauge | `role` | runtime\|worker\|admin |
| `taskwire_ipc_requests_rejected_total` | counter | `code` | |
| `taskwire_object_put_bytes_total` | counter | | |
| `taskwire_object_checksum_failures_total` | counter | | |
| `taskwire_storage_healthy` | gauge | `backend` | state\|objects |
| `taskwire_cluster_members` | gauge | | if cluster on |
| `taskwire_cluster_transfers` | gauge | `state` | if cluster on |
| `taskwire_kafka_outbox_pending` | gauge | | if kafka on |
| `taskwire_kafka_publish_total` | counter | `result` | if kafka on |
| `taskwire_admin_requests_total` | counter | `route`, `code` | closed route set |
| `taskwire_admin_mutations_total` | counter | `action`, `result` | closed action set |

Histogram buckets for latency: document defaults (e.g. 5ms … 60s); do not
require per-deploy bucket config in v1.

Console short-window rates (tasks/min) are derived from **in-process sliding
windows** (e.g. last 60s ring of counters) exposed on overview JSON—not by
scraping Prometheus from inside the agent.

---

## Configuration Contract

Extend the shared Phase 1 schema. Unknown fields remain errors. Relative
paths resolve against the config file directory as today.

### Canonical `http` block

```yaml
http:
  listen_addr: ""                      # empty = ops HTTP off (default)
  # Examples: "127.0.0.1:8080", "0.0.0.0:8080", "[::1]:8080"

  watchdog_interval_ms: 1000
  watchdog_stale_ms: 5000

  readiness:
    require_storage: true
    require_workers: false
    min_workers: 0
    require_cluster: false
    fail_on_kafka: false

  metrics:
    enabled: true                      # effective only if listen_addr set
    path: "/metrics"

  admin:
    enabled: false                     # default off
    ui: true                           # embed + serve /admin/
    allow_mutations: true
    max_list_limit: 100                # hard ceiling ≤ 500 in code
    list_default_limit: 50
    aggregate_peer_timeout_ms: 2000
    aggregate_overall_timeout_ms: 5000
    auth:
      mode: "none"                     # none | static_token
      token_env: "TASKWIRE_ADMIN_TOKEN"
    advertise_url: ""                  # peer admin base URL for fan-in; see transport rules
    allow_insecure_admin_transport: false  # allow http:// for non-loopback auth fan-in
    cors_origins: []                   # empty = same-origin only; no * in production examples
```

### Deprecated alias

```yaml
# Deprecated: accepted if and only if `http` is absent or http.listen_addr is empty.
metrics:
  listen_addr: ""
```

Loader rules:

1. If both `http.listen_addr` (non-empty) and top-level `metrics.listen_addr`
   (non-empty) are set → **config error** (conflict).
2. If only `metrics.listen_addr` is set → synthesize
   `http.listen_addr = metrics.listen_addr` with metrics enabled, admin
   disabled, readiness defaults.
3. Log a single startup warning when the deprecated alias is used.
4. `taskwire.example.yaml` and `testdata/config/normalized.yaml` migrate to
   `http:` with admin default off; keep one unit test for the alias.

### Validation

| Rule | Error |
|------|-------|
| `admin.enabled: true` and empty `listen_addr` | reject |
| `auth.mode` not in `none`, `static_token` | reject |
| `static_token` and empty token at **agent start** (env missing/blank) | reject startup |
| `max_list_limit` ∉ [1, 500] | reject |
| `list_default_limit` > `max_list_limit` | reject |
| `min_workers` < 0 | reject |
| `watchdog_stale_ms` < `watchdog_interval_ms` | reject |
| `metrics.path` empty or not starting with `/` | reject |
| `cors_origins` contains `"*"` with `allow_mutations: true` | reject |
| `advertise_url` if set must be absolute `http` or `https` URL without userinfo/credentials | reject otherwise |
| `advertise_url` uses `http:` and host is **not** loopback (`127.0.0.1`, `::1`, `localhost`) while `auth.mode: static_token` (this node or cluster fan-in will forward a bearer) | reject unless `allow_insecure_admin_transport: true` |
| `0.0.0.0` / `::` bind with `auth.mode: none` and `admin.enabled: true` | **reject** unless `allow_insecure_bind: true` |

Add:

```yaml
  admin:
    allow_insecure_bind: false              # required true to bind all interfaces without token
    allow_insecure_admin_transport: false   # required true to use http:// for non-loopback authenticated admin/fan-in
```

### Admin transport security (bearer tokens)

Bearer tokens are **confidential**. Rules:

1. **Loopback** (`127.0.0.1`, `::1`, `localhost`): `http://` is allowed for
   local Flower-class use with or without a token.
2. **Non-loopback authenticated admin** (`auth.mode: static_token`): peers
   and browsers that send `Authorization: Bearer …` must use **HTTPS**
   (`advertise_url` scheme `https:`, and production examples terminate TLS
   via sidecar, ingress, or agent TLS—document the deployment pattern).
3. **Cluster fan-in** must not attach a bearer token to an `http://` request
   whose host is non-loopback unless `allow_insecure_admin_transport: true`.
   Violations fail that peer with `error.code: insecure_transport` (peer
   `ok: false`), never silently send the token in cleartext.
4. **`allow_insecure_admin_transport: true`** is an explicit break-glass for
   trusted networks (some lab pod nets). Startup logs a single **warning**.
   Production docs must not recommend it; prefer HTTPS or a service mesh
   that provides mTLS on the ops port.
5. Probe routes stay unauthenticated and may use plain HTTP; do not put
   tokens on probe URLs.

### Example: local Flower-like enablement

```yaml
http:
  listen_addr: "127.0.0.1:8080"
  admin:
    enabled: true
    ui: true
    auth:
      mode: "none"
```

Open: `http://127.0.0.1:8080/admin/`

### Example: k8s-ish (HTTPS for authenticated admin)

```yaml
http:
  listen_addr: "0.0.0.0:8080"
  admin:
    enabled: true
    ui: true
    allow_insecure_bind: false
    allow_insecure_admin_transport: false
    auth:
      mode: "static_token"
      token_env: "TASKWIRE_ADMIN_TOKEN"
    # Cluster fan-in and operators use TLS-terminated URL, not raw pod HTTP.
    advertise_url: "https://taskwire-a.taskwire.svc:8443"
```

Probes may still hit container port 8080 over the pod network without a
token (`/livez`, `/readyz`, `/startupz`). Admin API and UI used across the
network require bearer + HTTPS (ingress/mesh/sidecar). Lab-only plain HTTP
on the pod IP requires `allow_insecure_admin_transport: true` and is not the
documented default.

---

## Admin HTTP API

Base path: `/admin/api/v1`.  
Content-Type requests/responses: `application/json; charset=utf-8`.  
No cookies required for v1 token auth (prefer `Authorization` header).

### Common list query parameters

| Param | Default | Rules |
|-------|---------|-------|
| `limit` | `list_default_limit` | 1…`max_list_limit` |
| `cursor` | empty | opaque server cursor; invalid → `invalid_argument` |
| `state` | empty | task/transfer/outbox filters; unknown → `invalid_argument` |

### Error envelope

HTTP status + body:

```json
{
  "error": {
    "code": "too_late",
    "message": "task is not queued at this owner",
    "retryable": false,
    "details": {}
  }
}
```

Stable admin API codes (subset; map protocol codes when identical):

| Code | HTTP | Retryable |
|------|-----:|-----------|
| `unauthorized` | 401 | false |
| `forbidden` | 403 | false |
| `not_found` | 404 | false |
| `feature_disabled` | 404 or 501 | false |
| `invalid_argument` | 400 | false |
| `too_late` | 409 | false |
| `task_not_found` | 404 | false |
| `conflict` | 409 | false |
| `insecure_transport` | 400 or peer `ok: false` | false |
| `storage_unavailable` | 503 | true |
| `shutdown` | 503 | true |
| `timeout` | 504 | true |
| `internal` | 500 | true |

`feature_disabled` is used when cluster/Kafka routes are called but the
feature config is off or the phase binary build omits them.

### Auth middleware

| Mode | `/admin` and `/admin/api/*` | probes + metrics |
|------|----------------------------|------------------|
| `none` | open | open |
| `static_token` | require `Authorization: Bearer <token>` constant-time compare | open |

Failed auth: **401** with `unauthorized`, no timing oracle on token length
beyond standard practices. Do not log the presented token.

Optional: `GET /admin/api/v1/overview` may be used by UI after storing token
in `sessionStorage` (document XSS risk; v1 accepts this for Flower parity on
trusted networks).

### Read routes and response schemas

#### `GET /admin/api/v1/overview`

```json
{
  "version": "0.1.0",
  "pid": 1234,
  "node_name": "node-a",
  "live": true,
  "ready": true,
  "started": true,
  "draining": false,
  "reasons": [],
  "uptime_seconds": 3600,
  "task_counts": {
    "queued": 10,
    "leased": 4,
    "succeeded": 1000,
    "failed": 3,
    "cancelled": 1,
    "dead_lettered": 0,
    "forwarding": 0,
    "forwarded": 2
  },
  "active_leases": 4,
  "workers": {
    "running": 4,
    "expected": 4,
    "restarts_total": 2,
    "circuits_open": 0
  },
  "storage": {
    "state_healthy": true,
    "objects_healthy": true,
    "state_backend": "sqlite",
    "objects_backend": "filesystem"
  },
  "rates": {
    "window_seconds": 60,
    "submits_per_minute": 12.5,
    "completes_per_minute": 11.0,
    "failures_per_minute": 0.2
  },
  "cluster": {
    "enabled": false,
    "members": 0
  },
  "kafka": {
    "enabled": false,
    "outbox_pending": 0
  },
  "ops": {
    "metrics_path": "/metrics",
    "admin_ui": true
  }
}
```

Rates use a fixed 60s window in v1 (field documents window). Missing cluster
or kafka objects still appear with `enabled: false`.

#### `GET /admin/api/v1/tasks`

Query: `state`, `task_name` (exact), `limit`, `cursor`.

```json
{
  "items": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "task_name": "examples.add",
      "task_version": "v1",
      "state": "leased",
      "attempt": 1,
      "owner_id_hash": "a1b2c3d4e5f6",
      "created_at_unix_ms": 0,
      "updated_at_unix_ms": 0,
      "lease": {
        "lease_id": "...",
        "worker_id": "python-default-0",
        "expires_at_unix_ms": 0
      },
      "transfer": null,
      "failure_code": null
    }
  ],
  "next_cursor": "",
  "limit": 50
}
```

`task_id` and `lease_id` are UUID hex with hyphens for operator ergonomics.
Never include input/result inline bytes or decoded values.

#### `GET /admin/api/v1/tasks/{task_id}`

```json
{
  "task_id": "...",
  "task_name": "examples.add",
  "task_version": "v1",
  "state": "failed",
  "attempt": 5,
  "max_attempts": 5,
  "owner_id_hash": "...",
  "invocation": "value",
  "input_codec": "msgpack",
  "labels": {"workload": "general"},
  "created_at_unix_ms": 0,
  "updated_at_unix_ms": 0,
  "input_ref": {
    "store": "local",
    "key": "...",
    "size": 128,
    "sha256": "...",
    "codec": "msgpack"
  },
  "result_ref": null,
  "failure": {
    "code": "max_attempts_exceeded",
    "message": "bounded message",
    "retryable": false
  },
  "lease": null,
  "transfer": {
    "transfer_id": "...",
    "state": "forwarded",
    "origin_node": "node-a",
    "target_node": "node-b",
    "updated_at_unix_ms": 0
  },
  "result_cursor": 42,
  "actions": {
    "cancel_allowed": false
  }
}
```

Admin **may** return tasks regardless of owner (node-local privilege). This
differs from Runtime `TASK_QUERY` owner isolation—document in security notes.

#### `GET /admin/api/v1/workers`

```json
{
  "pools": [
    {
      "name": "python-default",
      "runtime": "python",
      "count_configured": 4,
      "count_running": 4,
      "labels": {"workload": "general"},
      "resources": {
        "max_memory_mb": 2048,
        "max_cpu_percent": 80
      },
      "restart": {
        "restarts_in_window": 1,
        "limit": 5,
        "window_seconds": 60,
        "circuit_open": false
      },
      "workers": [
        {
          "worker_id": "python-default-0",
          "pid": 999,
          "state": "running",
          "registered": true,
          "capability_generation": 1,
          "active_task_id": "...",
          "active_lease_id": "...",
          "started_at_unix_ms": 0,
          "last_exit_code": null
        }
      ]
    }
  ]
}
```

Do not invent live RSS/CPU samples unless a future measurement subsystem
exists; display **configured** limits only until resource enforcement phase
ships. Optional null `usage` object is reserved:

```json
"usage": null
```

#### `GET /admin/api/v1/leases`

Active leases only; include `age_ms` and `ttl_ms` for UI warnings near expiry.

#### `GET /admin/api/v1/capabilities`

Flattened list of `{worker_id, pool, tasks: [{name, version, invocation, codecs[]}]}`
from current connection registrations—not historical.

#### `GET /admin/api/v1/objects/stats`

```json
{
  "default_store": "local",
  "stores": [
    {
      "name": "local",
      "type": "filesystem",
      "healthy": true,
      "approx_object_count": null,
      "approx_bytes": null,
      "partial_cleanup_pending": 0
    }
  ],
  "result_retention_seconds": 86400,
  "sweep_interval_ms": 60000
}
```

Approximate counts are best-effort; `null` when the backend cannot provide
them cheaply. Never list object keys in v1.

#### `GET /admin/api/v1/config/redacted`

Effective config JSON (or YAML-as-JSON tree) with:

- `cluster.encryption_key` → `"***"`
- DSN password userinfo → redacted
- any value from `token_env` → `"***"`
- Kafka credentials if present → `"***"`

#### Cluster routes (require `cluster.enabled`)

**`GET /admin/api/v1/cluster/members`**

```json
{
  "local_node": "node-a",
  "members": [
    {
      "node_name": "node-a",
      "advertise_addr": "...",
      "admin_url": "http://10.0.0.5:8080",
      "labels": {"zone": "z1"},
      "state": "alive",
      "is_local": true
    }
  ]
}
```

**`GET /admin/api/v1/cluster/transfers`**

Filter by `state=forwarding|forwarded|…`; metadata only (task_id, nodes,
ages, transfer_id).

**`GET /admin/api/v1/cluster/overview`**

Server-side fan-in:

1. Resolve members with non-empty `admin_url` / `advertise_url`.
2. For each peer URL, enforce **Admin transport security**: if a bearer will
   be sent and the URL is non-loopback `http:`, skip the request unless
   `allow_insecure_admin_transport` is true; record
   `error.code: insecure_transport` for that node.
3. Concurrent `GET {admin_url}/admin/api/v1/overview` with peer timeout.
4. Propagate the same bearer token if `static_token` mode **and** the
   transport is allowed (HTTPS, loopback HTTP, or explicit insecure
   override). Peers share ops token in v1 trusted cluster—or document
   per-node tokens as future work.
5. Return:

```json
{
  "generated_at_unix_ms": 0,
  "nodes": [
    {
      "node_name": "node-a",
      "ok": true,
      "overview": { },
      "error": null
    },
    {
      "node_name": "node-c",
      "ok": false,
      "overview": null,
      "error": { "code": "timeout", "message": "peer overall deadline" }
    }
  ],
  "totals": {
    "task_counts": { },
    "active_leases": 0,
    "workers_running": 0
  }
}
```

Never 500 solely because one peer failed. If cluster disabled →
`feature_disabled`.

#### Kafka routes (require integration enabled)

**`GET /admin/api/v1/integrations/kafka/outbox`** — metadata rows: event_id,
task_id, state, attempts, last_error_code, timestamps—not full event bytes
if large; include size.

#### Events (normative choice for v1: polling)

v1 **normative client** is HTTP polling every 1–2s on overview + visible
tables.  

Optional **SSE** `GET /admin/api/v1/events`:

```text
Content-Type: text/event-stream
event: overview
data: {...subset...}

event: membership
data: {...}
```

If SSE is not implemented in the first exit gate, routes return **501**
`feature_disabled` and the UI uses polling only—both are acceptable if
documented. Polling alone is enough to exit Phase 12.

### Mutation routes

Require `allow_mutations: true`; otherwise **403** `forbidden`.

| Method | Path | Body | Success |
|--------|------|------|---------|
| `POST` | `/admin/api/v1/tasks/{task_id}/cancel` | `{}` | `{ "cancelled": true }` or protocol-equivalent |
| `POST` | `/admin/api/v1/drain` | `{}` | `{ "draining": true }` |
| `POST` | `/admin/api/v1/workers/{worker_id}/restart` | `{}` | `{ "worker_id", "old_pid", "new_pid" }` when known |
| `POST` | `/admin/api/v1/kafka/outbox/{event_id}/dead-letter` | `{}` | `{ "event_id", "state": "dead_lettered" }` |

#### Privileged cancel storage operation

Phase 2 exposes owner-scoped cancellation only:

```go
Cancel(ctx context.Context, taskID TaskID, ownerID OwnerID) (bool, error)
```

The admin API has **task_id** only and must not round-trip
`Get → Cancel(taskID, row.OwnerID)`: that is racy under concurrent claim and
would invent an owner credential the admin HTTP surface deliberately does
not carry (UI shows `owner_id_hash` only).

This phase **adds** a privileged atomic store method on `TaskStateStore`
(all backends that implement the interface, including memory/sqlite and any
Phase 6 adapters):

```go
// CancelAdmin cancels a queued task by ID without an owner credential.
// It is reachable only from the local admin HTTP path (and tests), never
// from control-plane roles.
// Returns (true, nil) if this call transitioned queued → cancelled and
// wrote the terminal result record; (false, nil) if the task exists but
// is not cancellable (leased, forwarding, forwarded, already terminal)
// — map to too_late at the HTTP layer; (false, err) for storage failures;
// task absence is (false, nil) mapped to task_not_found or too_late
// consistently with admin non-disclosure policy documented below.
CancelAdmin(ctx context.Context, taskID TaskID) (cancelled bool, err error)
```

**Atomicity.** A single state-store transaction must:

1. Select the task row by `task_id` with the backend’s appropriate write lock
   / `SELECT … FOR UPDATE` equivalent.
2. If missing → not cancelled (HTTP `task_not_found` for admin is allowed;
   admin is node-local privileged and may distinguish absence).
3. If state ≠ `queued` (including `forwarding` / `forwarded` / leased /
   terminal) → not cancelled (`too_late`); no write.
4. If `queued` → set `cancelled`, write terminal result record / cursor
   exactly as `Cancel` does for a successful owner cancel, commit.

No TOCTOU window between “see queued” and “write cancelled.” Concurrent
`Claim` either wins (cancel → `too_late`) or loses (claim → empty); never
double-terminal or cancel-after-lease without fencing rules already in
Phase 2.

**Boundary.**

| Caller | API |
|--------|-----|
| Runtime IPC `CANCEL` | `Cancel(taskID, ownerID)` only |
| Admin HTTP cancel | `CancelAdmin(taskID)` only |
| Worker / cluster peer | neither cancel path |

Do not implement admin cancel by synthesizing an owner id from a prior read.

**HTTP mapping.**

| Store result | HTTP |
|--------------|------|
| `(true, nil)` | 200 `{ "cancelled": true }` |
| `(false, nil)` not queued | 409 `too_late` |
| `(false, nil)` missing | 404 `task_not_found` |
| storage error | 503 `storage_unavailable` or 500 `internal` |

Success path still creates a terminal result so any attached Runtime Future
can resolve via existing replay (same as owner cancel).

Drain is idempotent. Restart kills one managed child; manager applies
restart policy; in-flight lease expires or completes under fencing.

**Audit log** (structured, Phase 8 field style):

```text
component=admin action=cancel task_id=... result=ok|error code=... principal=token|anonymous
```

Never log bearer tokens or payloads.

---

## Embedded UI (Flower-class)

### Delivery

| Requirement | Detail |
|-------------|--------|
| Embed | Go `embed` (or equivalent) of built static files into `taskwire-agent` |
| Path | `/admin/` (trailing slash); `/admin` redirects to `/admin/` |
| Cache | Fingerprinted assets long-cache; `index.html` short or no-cache |
| Offline | No CDN, no phone-home, no external fonts required for usable layout |
| API | Same origin `/admin/api/v1` only |
| JS | Prefer a small SPA (any of Svelte/React/Vue/vanilla); build step produces static files committed or generated in `make build-agent` |
| Graceful degrade | If `ui: false`, API-only mode; `/admin/` returns **404** |

Build integration: `make build-agent` depends on UI build when UI sources
change; reproducible builds pin toolchain. UI unit tests optional; API
contract tests mandatory.

### Navigation

```text
Dashboard | Tasks | Workers | Leases | Cluster* | Failures | Ops
```

`Cluster` visible only if overview reports `cluster.enabled`.  
Kafka widgets visible only if `kafka.enabled`.

### Screen specifications

#### Dashboard

- Header chips: **LIVE** / **NOT LIVE**, **READY** / **NOT READY**, version,
  node name, uptime.
- Reason chips when not ready (tokens mapped to short human strings).
- Cards: task counts by state; active leases; workers running/expected;
  storage health; Kafka pending.
- Simple rate line or sparklines from `rates` (optional canvas; table of
  rates is enough for v1).
- Link “Prometheus metrics” → `ops.metrics_path` on same host.
- Auto-refresh 1–2s.

#### Tasks

- Filter tabs/chips: all, queued, leased, succeeded, failed, cancelled,
  dead_lettered, forwarding, forwarded (hide cluster states if disabled).
- Table columns: task_id (short), name@version, state, attempt, worker,
  updated age, failure code.
- Detail drawer/page: full metadata schema above; ObjectRef fields
  copyable; **Cancel** button disabled with tooltip when
  `actions.cancel_allowed` is false.
- Empty and error states with API `error.message`.

#### Workers

- Per-pool panel: configured count, circuit breaker badge, resource config.
- Per-worker row: worker_id, pid, registered, active task link, **Restart**.
- Confirm dialog on restart (“in-flight task may requeue under at-least-once”).

#### Leases

- Sort by soonest expiry; warn when `age_ms > 0.7 * ttl_ms`.
- Link to task detail.

#### Cluster

- Member table: name, state, labels, admin_url reachability from last
  aggregate.
- **Refresh aggregate** button → `GET .../cluster/overview`.
- Transfers table: task, origin, target, state, age.
- Task detail shows transfer block when present.
- Partition UX: show nodes with `ok: false` in red; do not blank the page.

#### Failures

- Failed + dead_lettered task filter shortcut.
- Kafka outbox permanent errors table when enabled; dead-letter action with
  confirm.

#### Ops

- Drain button + confirmation; show draining state.
- Redacted config viewer (read-only, collapsible secrets already `***`).
- Probe cheat-sheet with absolute URLs derived from `window.location`.
- k8s YAML snippet (static documentation block in UI or docs only—docs
  preferred for long snippets).
- Token entry field when `401` received (sessionStorage); never persist to
  disk from the agent.

### UX copy requirements

UI and docs must state:

- Execution is **at-least-once**; restart/cancel nuances.
- Cancel does **not** stop a running worker mid-function.
- Admin sees **this node’s** tasks (and aggregate counts), not a global
  multi-tenant ledger unless using shared Phase 6 backends (still per-agent
  admin API).

### Accessibility and quality bar

- Keyboard-reachable primary actions.
- No sole reliance on color for ready/live.
- Works at 1280px width; usable at 1024px.
- Does not require WebGL.

---

## Security Gate

| Topic | Requirement |
|-------|-------------|
| Defaults | admin off; listen empty; mutations on only when admin on |
| Bind | non-local bind + no token rejected without `allow_insecure_bind` |
| Transport | bearer never sent on non-loopback cleartext without `allow_insecure_admin_transport` |
| Surface | no SUBMIT/PULL/COMPLETE on HTTP |
| Cluster ports | no admin routes on task/memberlist listeners |
| Cancel | `CancelAdmin` only; never Get+owner `Cancel` from admin |
| Payloads | never in API default views |
| Owner ids | hash in JSON; full id not required in v1 UI |
| Tokens | not logged; not in redacted config; constant-time compare |
| CORS | default same-origin; no `*` with mutations |
| CSRF | bearer token not cookie-only; same-origin UI |
| DoS | max list limit; body size cap on POST (e.g. 64 KiB); timeouts |
| Threat model | open admin bind = local root equivalent for cancel/drain; treat as privileged |

Threat-model review artifact (short markdown in `docs/` or phase appendix)
covers: stolen token, **cleartext bearer on pod networks**, XSS in admin UI,
SSRF via aggregate `admin_url`, metadata leakage of task names. Mitigations:
HTTPS for non-loopback auth, `insecure_transport` fan-in guard, token auth,
CSP headers (`default-src 'self'`), validate `advertise_url`/member admin
URLs (http/https only, no credentials), redaction.

Recommended response headers for `/admin/*`:

```text
Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; base-uri 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cache-Control: no-store  (for index and API)
```

---

## Files (indicative)

```text
agent/internal/httpops/
  server.go              # ServeMux, timeouts, middleware
  ready.go               # Live/Ready/Started + reasons
  probes.go              # /livez /readyz /startupz
  metrics.go             # Prometheus registry + handler
agent/internal/admin/
  api.go                 # route table
  handlers_overview.go
  handlers_tasks.go
  handlers_workers.go
  handlers_cluster.go
  handlers_kafka.go
  auth.go
  redact.go
  aggregate.go
  audit.go
  ui/
    dist/                # built static assets (embed root)
    # or src/ + build pipeline writing to dist/
agent/internal/status/status.go   # share predicates with STATUS
agent/cmd/taskwire-agent/main.go
agent/internal/config/config.go   # http block + alias
python/taskwire/config.py
testdata/config/normalized.yaml
taskwire.example.yaml
python/tests/integration/test_admin_api.py
python/tests/integration/test_admin_probes.py
python/tests/integration/test_admin_ui_smoke.py
python/tests/integration/test_admin_cluster_aggregate.py  # marker cluster
python/tests/unit/test_config_http.py
docs/phases/phase-12-admin-console.md
```

---

## Required Tests

### Config and loaders (Python + Go)

| Case | Expect |
|------|--------|
| default example config | admin disabled, no listen required |
| admin enabled, empty listen | load error |
| static_token, missing env at run | process exit error |
| deprecated `metrics.listen_addr` only | maps to http.listen_addr; warning |
| both listen fields set | load error |
| insecure bind without flag/token | load error |
| max_list_limit 0 or 501 | load error |

### Probes and STATUS

| Case | Expect |
|------|--------|
| healthy agent | livez/readyz/startupz 200; bodies agree |
| inject storage unhealthy | readyz 503 + `storage_unhealthy`; livez 200 |
| before started | startupz 503; readyz 503 |
| SIGTERM / drain | readyz 503 with `draining` before exit |
| socket STATUS | `ready`/`live`/`started`/`not_ready_reasons` match probes |
| watchdog stop (failpoint) | livez 503 |

### Metrics

| Case | Expect |
|------|--------|
| scrape `/metrics` | 200; contains `taskwire_agent_ready` and `taskwire_tasks` |
| label allowlist | every label name on emitted series is in the fixed allowlist; includes `version`/`role`/`route`/`action` where used |
| label card | no task_id/owner_id/raw path label keys in exposition |
| metrics disabled | 404 on metrics path; probes still work; STATUS.live still true when healthy |

### Admin API

| Case | Expect |
|------|--------|
| overview | 200 shape; rates window present |
| tasks list limit | never exceeds max; cursor stable |
| task detail | no payload/base64 input bytes fields |
| cancel queued via admin | `CancelAdmin` → cancelled true; state cancelled; terminal result written |
| cancel leased / forwarding | 409 `too_late`; no state write |
| cancel missing task | 404 `task_not_found` |
| admin cancel does not call owner `Cancel` | unit/integration: no synthetic owner id; race with concurrent Claim has single winner |
| `CancelAdmin` store conformance | memory + sqlite (+ postgres when Phase 6 on): atomic queued-only; concurrent Claim exclusivity; terminal cursor like owner Cancel |
| restart worker | pid changes or restart count++; agent alive |
| STATUS.live with admin HTTP off | default config agent reports `live: true` when watchdog healthy |
| fan-in http + token non-loopback | peer `insecure_transport` unless override; token not sent |
| fan-in https + token | peer ok when reachable |
| drain | ready false; submit rejected/shutdown |
| mutations disabled | 403 |
| static_token wrong | 401 on API; 200 on livez |
| cluster disabled | cluster routes feature_disabled |
| kafka disabled | kafka routes feature_disabled |
| oversized limit | 400 |

### Cluster aggregate (`cluster` marker)

| Case | Expect |
|------|--------|
| two agents, both admin | overview nodes ok; totals sum queues |
| kill peer admin | one node ok false; HTTP 200 overall |
| peer `advertise_url` is non-loopback `http://` with shared token | that node `ok: false`, `insecure_transport`; Authorization header not sent (assert via test double) |
| same with `allow_insecure_admin_transport: true` | request proceeds; startup warning observed |
| browser not required to call peer URLs for overview | covered by API-only test |

### UI smoke

| Case | Expect |
|------|--------|
| GET `/admin/` | 200 HTML referencing embedded assets |
| asset GET | 200; not loaded from network outside host |
| clean wheel | agent binary serves UI without repo checkout |

### Regression

| Case | Expect |
|------|--------|
| admin off | Phases 0–4 tests unchanged; no port bind |
| chaos drain under load | conservation invariants hold |
| race | `go test -race` on httpops/admin packages |

---

## Implementation Order

1. **Config** — `http` block, validation, deprecated alias, example YAML,
   dual loaders, unit tests.
2. **Ready package** — Live/Ready/Started + reason tokens; Live independent
   of HTTP; wire into STATUS; failpoints for storage and watchdog.
3. **HTTP server** — bind, timeouts, `/livez` `/readyz` `/startupz`; SIGTERM
   not-ready-first.
4. **Prometheus** — registry, minimum series with allowlist-consistent
   labels, `/metrics`.
5. **`CancelAdmin` on TaskStateStore** — interface method, memory/sqlite
   conformance (atomic cancel vs claim), wire only to admin handlers.
6. **Admin auth + error envelope** — middleware, audit helper; transport
   checks for advertise_url / fan-in.
7. **Read API** — overview, tasks, workers, leases, capabilities,
   objects/stats, config/redacted.
8. **Mutations** — admin cancel via `CancelAdmin`, drain, worker restart.
9. **UI build + embed** — dashboard, tasks, workers, ops; polling refresh.
10. **Cluster** — members, transfers, aggregate with HTTPS/insecure_transport
    guards; UI section; chaos partial peer failure.
11. **Kafka** — outbox list + dead-letter; UI section.
12. **Hardening** — CSP headers, fuzz limits, insecure-bind and
    insecure-transport rules, race, docs (k8s probes, microVM, Flower
    quickstart).
13. **Packaging** — `make build-agent` embeds UI; clean-wheel admin smoke
    optional under `admin` marker.

---

## Documentation Requirements

Ship (under `docs/` or examples once gate is green):

1. **Operator quickstart** — enable localhost admin; open Flower-class UI;
   cancel a queued demo task; interpret ready vs live.
2. **Kubernetes** — container port 8080 for probes; startup/liveness/readiness;
   admin via `kubectl port-forward` (loopback) + token, or HTTPS ingress/mesh
   for in-cluster admin URLs; never document non-loopback cleartext bearer as
   default; NetworkPolicy note.
3. **microVM** — guest `listen_addr`; host supervisor curls `/readyz` before
   attaching workload; optional admin forward.
4. **Prometheus scrape** config snippet; label cardinality warning.
5. **Security** — privilege model, redaction, bearer transport rules,
   `CancelAdmin` vs owner cancel, non-goals (SSO, payload debug).
6. **Cluster ops** — `advertise_url` (https for authenticated fan-in), shared
   token assumption, `insecure_transport` partial failure, aggregate errors.

Phase 9 must not claim the console is shipped until this exit gate is green;
it may link forward as “planned / Phase 12”.

### k8s probe example (documentation only)

```yaml
ports:
  - name: ops
    containerPort: 8080
startupProbe:
  httpGet: { path: /startupz, port: ops }
  failureThreshold: 30
  periodSeconds: 2
livenessProbe:
  httpGet: { path: /livez, port: ops }
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /readyz, port: ops }
  periodSeconds: 2
  failureThreshold: 3
```

---

## Relationship to Other Phases

| Phase | Interaction |
|-------|-------------|
| 1 | Config extension; STATUS forward-compatible fields; admin socket role unchanged |
| 2 | Predicates, drain order, owner `Cancel`, worker manager restart hooks; this phase adds `CancelAdmin` to the same store interface |
| 3–4 | Task/worker metadata for UI; cancel parity with SDK |
| 5 | Cluster screens + aggregate; no protocol change |
| 6 | Storage health backends; still no payload listing |
| 7 | Outbox admin; still not Runtime delivery |
| 8 | Metrics/log policies; packaging embeds UI; release notes feature-gate console |
| 9 | Examples after exit gate |
| 10–11 | No UI change required; workers appear via runtime field |

Worker **CPU/memory enforcement** (cgroups/rlimits) remains outside this
phase unless implemented earlier. The console **displays** configured
`resources` and optional future `usage`; it must not claim hard enforcement
that does not exist.

---

## Exit Gate

Phase 12 is complete when all of the following hold:

1. **Packaged agent** with admin enabled serves `/admin/` and
   `GET /admin/api/v1/overview` from a clean install without CDN, network
   asset fetch, or source-tree paths.
2. **Probes** distinguish live vs ready vs started with automated tests for
   storage failure, pre-start, drain/SIGTERM, and STATUS parity; **default
   config (no ops HTTP) reports `STATUS.live == true`** when the watchdog is
   healthy.
3. **Prometheus** `/metrics` scrapes with the minimum series set; every
   emitted label is on the fixed allowlist (including `version`, `role`,
   `route`, `action` where used); no forbidden high-cardinality labels.
4. **API redaction** proven: task list/detail fixtures contain no input/result
   payload bytes; config redaction strips keys/DSNs/tokens.
5. **Mutations** use `CancelAdmin` (atomic, no owner credential), restart, and
   drain with fencing-compatible semantics; audit logs without secrets.
6. **Cluster and Kafka** surfaces are honest when disabled and correct under
   their markers when enabled; aggregate partial failure returns 200 with
   per-node errors; **authenticated fan-in never sends bearers over
   non-loopback cleartext** without `allow_insecure_admin_transport`.
7. **Defaults** keep admin off and introduce no mandatory ops port for
   Phases 0–4 CI.
8. **Documentation** describes Flower-class scope, k8s/microVM probes, and
   security defaults without claiming SSO, exactly-once, payload debug, or
   cancel-of-running-work.

When this gate is green, Phase 8 release notes and Phase 9 examples may
advertise the console as an optional operator feature.

---

## Implementation Guide

> The sections above are the full product contract. This guide is the
> build sequence only—do not invent routes or metrics outside the freeze.

### Prerequisites

| Need | Minimum phase |
|------|----------------|
| Live agent STATUS, tasks, workers | 0–4 |
| Cluster topology / transfers UI | 5 |
| Kafka outbox / dead-letter UI | 7 |
| Packaging / embedded assets | 8 patterns preferred |

### Suggested package layout

```text
agent/internal/admin/
  http.go           # mux: probes, metrics, /admin/api/v1, static UI
  api_v1.go         # JSON handlers
  auth.go           # local-only / token / mTLS as contracted above
  redaction.go
  aggregate.go      # cluster fan-in (Phase 5+)
agent/internal/state/store.go  # add CancelAdmin (queued-only, no owner)
agent/ui/admin/     # embedded FS (go:embed) — built assets only
```

### Build order

1. **Probes first** — `/livez`, `/readyz`, `/startupz` share predicates with socket `STATUS`.
2. **Prometheus** — fixed label allowlist; scrape test in CI.
3. **`CancelAdmin`** on state store — queued only; Runtime still uses owner `Cancel`.
4. **Read APIs** — overview, tasks (metadata only), workers; redaction tests first.
5. **Mutations** — cancel, worker restart, drain; audit log without secrets.
6. **Embedded UI** — static build into binary; no CDN at runtime.
7. **Cluster/Kafka panels** — `feature_disabled` when gates off; honest empty states.
8. **Default config** — admin HTTP **off**; Phases 0–4 CI never bind ops port.

### Config sketch (additive; must match frozen schema in this doc)

```yaml
http:
  admin:
    enabled: false
    listen_addr: "127.0.0.1:9090"
    # further fields per this phase's configuration section only
```

### Minimal handler skeleton

```go
mux.HandleFunc("GET /livez", func(w http.ResponseWriter, r *http.Request) {
	if !s.WatchdogOK() {
		http.Error(w, "not live", 503)
		return
	}
	w.WriteHeader(200)
})

mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
	if !s.StoresHealthy() || !s.SocketListening() || s.Draining() {
		http.Error(w, "not ready", 503)
		return
	}
	w.WriteHeader(200)
})

mux.HandleFunc("GET /admin/api/v1/overview", s.requireAdmin(s.handleOverview))
// task detail: never return input/result payload bytes
```

### Tests to add

```text
python/tests/integration/test_admin_probes.py      # marker admin, integration
python/tests/integration/test_admin_api_redaction.py
python/tests/integration/test_admin_cancel.py
# cluster/kafka markers when those phases exist
```

### Done checklist (maps to Exit Gate)

- [ ] Clean install serves `/admin/` + overview JSON without network asset fetch  
- [ ] live/ready/startup + STATUS parity tested (incl. storage fail, drain)  
- [ ] Metrics label allowlist enforced in tests  
- [ ] Task APIs redacted  
- [ ] `CancelAdmin` queued-only; no owner bearer required  
- [ ] Cluster/Kafka honest when disabled  
- [ ] Default admin off; MVP CI unbound  
- [ ] Docs do not claim SSO, exactly-once, payload debug, cancel-running  

### Review request

```text
Please review Phase 12.
Admin listen: 127.0.0.1:9090 (dev)
Commands: make unit integration; pytest -m admin
          curl probes + overview; redaction test output
Gaps: ...
```
