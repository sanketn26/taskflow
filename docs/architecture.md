# Taskwire Architecture

> **Architecture amendment:** [Storage and Reference Architecture](storage.md) is authoritative for task state, payloads, results, and delivery. It replaces worker-to-application callbacks, `callback_addr`, the Bolt-only durability design, and mandatory inline function payloads described in older phase detail below. Those sections remain as historical implementation sketches until rewritten phase-by-phase.

## Value Proposition

High-performance distributed task execution with no operational broker.
Celery-style task composition, Go-powered runtime, ships as a single `pip install`.

---

## Design Principles

| Principle | Application |
|-----------|-------------|
| Two-layer separation | Platform engineers own config. Developers own task logic. Neither touches the other. |
| No external infrastructure | The Go sidecar IS the broker. No Redis, no RabbitMQ, no Zookeeper. |
| At-least-once delivery | Lease + heartbeat ensures tasks re-queue on worker failure. Idempotency is the caller's responsibility. **Scope:** this covers worker crashes. Sidecar restart loses the in-memory queue unless `queue.persistence: wal` is enabled — be explicit about this in user docs. |
| Pull over push | Workers pull from sidecar. Backpressure is natural. No overwhelmed workers. |
| Results flow through the agent | Workers store results through their agent and COMPLETE with an immutable reference. The origin agent records and relays results over the Runtime's existing connection, including reconnect replay. No application callback listener or routable callback address. |
| Storage by capability | Transactional `TaskStateStore` and immutable `ObjectStore` are separate contracts. Memory supports tests; SQLite/filesystem are defaults; PostgreSQL/S3 are optional. |
| Broad Python support | Standard CPython 3.11+ is the baseline. Free-threaded builds (3.13t+) are an *accelerator*, never a requirement — requiring 3.13t would exclude nearly the entire ecosystem (most C-extension wheels still don't ship free-threaded variants). |
| Rust is optional acceleration | The MVP does not require a result server because results flow through the Go agent. A native frame codec or heartbeat may be added only after profiling/correctness tests justify its packaging cost; pure Python remains supported. |
| Secure by default | Clustering is disabled by default and requires a shared key when enabled (unless an explicit development-only insecure override is set). The Unix socket is `0660` owned by a dedicated group; docs state plainly that anyone who can write to it can execute arbitrary code (cloudpickle). |

---

## Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Developer Layer  (Python)                                      │
│                                                                 │
│   @task(label="cpu")                                            │
│   def process(data): ...                                        │
│                                                                 │
│   with Runtime() as rt:                                         │
│       future = rt.submit(process, data)                         │
│       result = future.result()                                  │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Unix socket
┌──────────────────────▼──────────────────────────────────────────┐
│  Go Sidecar  (taskwire-agent)          always-on system daemon  │
│                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │  IPC Server│  │  Work Queue  │  │  Lease Manager          │ │
│  │ Unix socket│  │  (pull-based)│  │  heartbeat + TTL expiry │ │
│  └────────────┘  └──────────────┘  └─────────────────────────┘ │
│                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │  Scheduler │  │   Cluster    │  │  Worker Manager         │ │
│  │  label     │  │   gossip     │  │  spawn + monitor        │ │
│  │  routing   │  │   memberlist │  │  Python workers         │ │
│  └────────────┘  └──────────────┘  └─────────────────────────┘ │
└──────┬──────────────────────┬───────────────────────────────────┘
       │ gossip (TCP)         │ spawn + Unix socket
       │                 ┌────▼─────────────────────┐
       │                 │  Python Workers           │
       │                 │  pull → execute → deliver │
       │                 └───────────────────────────┘
       │
┌──────▼──────────┐   ┌──────────────────┐   ┌──────────────────┐
│  Go Sidecar     │   │  Go Sidecar      │   │  Go Sidecar      │
│  Node 2         │   │  Node 3          │   │  Node N          │
└─────────────────┘   └──────────────────┘   └──────────────────┘
```

---

## Data Flow

### Authoritative single-node flow

```text
Python Runtime          Go Agent             Python Worker       Storage
      │                    │                       │                 │
      │── store/submit ───►│──── object Put ──────────────────────►│
      │◄── SUBMIT ACK ─────│──── state Create ────────────────────►│
      │                    │◄──── PULL ────────────│                 │
      │                    │──── leased TASK ─────►│                 │
      │                    │◄──── HEARTBEAT ───────│                 │
      │                    │                       │── execute       │
      │                    │◄──── COMPLETE(ref) ───│──── result Put ►│
      │                    │──── fenced terminal state ────────────►│
      │◄── RESULT(ref) ────│                       │                 │
      │── result ACK ─────►│                       │                 │
```

The agent ACKs submission only after referenced input and task state satisfy the configured durability contract. COMPLETE is accepted only for the active `lease_id`. Result notification is replayable by owner/cursor until acknowledged or retention expiry.

### Task Submission (direct delivery mode)

```
Python App                 Go Sidecar              Python Worker
    │                          │                        │
    │  Runtime.submit(task)    │                        │
    │─── SUBMIT ──────────────►│                        │
    │    task_id               │                        │
    │    cloudpickle(fn+args)  │                        │
    │    callback_addr         │◄─── PULL ──────────────│
    │    label                 │─── TASK ──────────────►│
    │                          │    task_id              │
    │                          │    payload              │
    │                          │    lease_id + TTL       │
    │                          │    callback_addr        │
    │                          │                        │ ── heartbeat loop
    │                          │◄─── HEARTBEAT ─────────│ (every TTL/2)
    │                          │                        │
    │                          │                        │ ── execute fn(*args)
    │◄══════════════════════════════════════════════════│
    │   RESULT + lease_id      │                        │
    │════════ ACK(task_id, lease_id) ═════════════════►│
    │   task_id + result_bytes │                        │
    │                          │◄─── COMPLETE ──────────│
    │                          │  release lease,        │
    │                          │  delete WAL record     │
    │  future.result() returns │                        │
```

### Task Submission (queue delivery mode)

```
Python App         Go Sidecar         Python Worker         Kafka
    │                  │                    │                  │
    │── SUBMIT ────────►│                   │                  │
    │                  │◄─── PULL ──────────│                  │
    │                  │──── TASK ─────────►│                  │
    │                  │                    │── execute         │
    │                  │                    │── publish ───────►│
    │                  │◄── COMPLETE ───────│   result_bytes    │
    │                  │  (after flush())   │                   │
    │◄═══════════════════════════════════════════════════════   │
    │  Runtime Kafka consumer resolves Future                   │
```

### Lease Expiry (at-least-once)

```
Go Sidecar                   Python Worker (crashed)
    │                              │
    │  lease granted, TTL = 30s    │
    │  ...                         x  (crash — no more heartbeats)
    │
    │  [30s pass, no heartbeat]
    │  LeaseManager.expire()
    │  → queue.Requeue(task)
    │  → task available for next PULL
```

---

## Wire Protocol

All communication uses the same binary frame format:

```
 0       1       2               18      19          23
 ┌───────┬───────┬───────────────┬───────┬───────────┬─────────────────┐
 │  ver  │ type  │   task_id     │ flags │  pay_len  │    payload      │
 │  1B   │  1B   │    16B        │  1B   │    4B     │    N bytes      │
 └───────┴───────┴───────────────┴───────┴───────────┴─────────────────┘
```

| Field | Description |
|-------|-------------|
| ver | Protocol version, currently `0x01`. Receivers reject frames with an unknown version with `ProtocolError` — this is what lets v0.2 change the wire format without silent corruption. |
| type | MessageType enum (1 byte) |
| task_id | UUID as raw bytes (16 bytes) |
| flags | Bit field: 0x01 = error, 0x02 = idempotent, 0x04 = forwarded (Phase 5 — task arrived via forward/steal and must never be re-forwarded) |
| pay_len | Payload length, big-endian uint32. Receivers enforce `max_frame_size` (default 16 MiB) — a corrupt or hostile length prefix must not OOM the agent. |
| payload | Message-specific data (cloudpickle, msgpack, or empty) |

### Message Types

| Value | Name | Direction | Payload |
|-------|------|-----------|---------|
| 0x01 | SUBMIT | App → Sidecar; Sidecar → Sidecar (Phase 5 forwarding, flags 0x04) | msgpack: {task_bytes, callback_addr, label, idempotent} |
| 0x02 | PULL | Worker → Sidecar | empty |
| 0x03 | TASK | Sidecar → Worker | msgpack: {task_bytes, lease_id, ttl_ms, callback_addr, delivery_mode} |
| 0x04 | HEARTBEAT | Worker → Sidecar | msgpack: {lease_id} |
| 0x05 | RESULT | Worker → App | msgpack `{lease_id, result_bytes}` where `result_bytes` is a cloudpickle result (flags=0x00) or exception (flags=0x01) |
| 0x06 | CANCEL | App → Sidecar | empty — best-effort: remove task from queue if not yet leased; reply ACK (flags=0x00 cancelled, 0x01 too late) |
| 0x07 | COMPLETE | Worker → Sidecar; Sidecar → Sidecar (Phase 5); Sidecar → App | msgpack: {lease_id, task_id, status, reason} where status is `"ok"` or `"delivery_failed"`. Worker → Sidecar after result delivery: sidecar releases the lease and deletes the WAL record — **this is the only way a task leaves the system successfully**. Phase 5 relays it thief/forwardee → origin to clear the shadow map. On `delivery_failed` the sidecar dead-letters the task and relays COMPLETE to the submitting app's connection so the future raises `DeliveryFailedError` instead of hanging. |
| 0x08 | STEAL | Sidecar → Sidecar | request: msgpack {n}; response: up to n TASK frames (flags 0x04, no lease_id) followed by ACK with msgpack {count} |
| 0x09 | ACK | Any → Any | empty, msgpack `{task_id}` for SUBMIT, or `{task_id, lease_id}` for RESULT |
| 0x0A | STATUS | App/CLI → Sidecar (**local Unix socket only** — never served on the cluster TCP listener) | request: empty; response: msgpack {queue_depth, active_leases: [{task_id, age_ms, attempts}], worker_pids, worker_restarts, deadletter_count, members} — implemented in Phase 2 (the test harness invariants I5/I6 depend on it), surfaced as `taskwire-agent status --json` in Phase 7 |

**SUBMIT is acknowledged.** The sidecar replies with ACK {task_id} after `queue.Push` — and, when `queue.persistence: wal`, only after the WAL write has fsync'd. "Acknowledged" is the exact boundary of the at-least-once guarantee (harness invariant I1): an unACKed submit may be lost; an ACKed one may not.

**COMPLETE ordering rule.** In direct mode, the worker sends RESULT and waits for the Runtime to ACK the matching `task_id` and `lease_id`; only then does it send COMPLETE. A successful TCP write alone is not delivery. A worker crash or lost ACK leaves the lease alive → expiry → re-execution → duplicate delivery, which at-least-once permits. Queue mode requires a successful broker delivery report before COMPLETE. The reverse order could lose an acknowledged task result forever.

---

## Native Acceleration (Rust)

The Python SDK has two kinds of code: *task logic* (the user's functions — always pure Python) and *infrastructure* (framing, sockets, keep-alives). Infrastructure lives in a Rust extension module, `taskwire._native`, built with PyO3 + maturin. A pure-Python implementation of every native component ships alongside it; selection happens once at import time:

```python
try:
    from taskwire import _native as impl   # Rust
except ImportError:
    from taskwire.protocol import frames as impl   # pure Python fallback
```

Why Rust here and not "rewrite it all in Rust":

| Component | Why native matters |
|-----------|--------------------|
| Worker heartbeat | **Correctness, not speed.** On standard (GIL) CPython, a Python heartbeat thread can be starved by CPU-bound task code holding the GIL → lease expires → the sidecar re-queues a task that is *still running*. A Rust OS thread never needs the GIL, so heartbeats flow no matter what the task does. |
| Result server | Accept loop + frame parsing + per-connection handling run on Rust threads; the GIL is acquired only for the brief `on_result` callback. On GIL builds this removes contention between result handling and user code; on free-threaded builds it's simply faster. |
| Frame codec | Zero-copy encode/decode and `read_exact` socket loops. Marginal per-frame, significant at >10k tasks/s. |

Not worth doing in Rust: cloudpickle (must be Python), msgpack (C extension already), the `@task`/`Runtime` API surface (ergonomics > speed, and it's not hot).

---

## Configuration

One YAML file shared by the Go sidecar and Python SDK. Platform engineer owns it, developer never touches it.

```yaml
cluster:
  enabled: false                # no discovery or cluster TCP listener unless enabled
  allow_insecure: false         # dev-only override; otherwise encryption_key is required
  node_name: ""                 # auto-generated UUID if empty
  bind_addr: "0.0.0.0:7946"
  advertise_addr: "10.0.1.5:7946"
  task_port: 7947               # cluster TCP endpoint (STEAL/forwarding) — memberlist owns 7946, so this is a separate listener
  seeds: []
  mdns: true
  encryption_key: ""            # base64 32-byte key; gossip encrypted + cluster TCP HMAC-authenticated when set

socket: "/var/run/taskwire/agent.sock"
socket_group: "taskwire"        # socket chmod 0660, chown root:taskwire
advertise_addr: "10.0.1.5"     # routable IP for result callbacks

queue:
  persistence: "none"           # "none" | "wal" — wal survives agent restart
  wal_dir: "/var/lib/taskwire/wal"
  max_attempts: 5               # after this many lease expiries → dead-letter
  max_frame_size_mb: 16
  lease_ttl_ms: 30000

ipc:
  submit_ack_timeout_ms: 5000

workers:
  count: 4
  labels:
    workload: "general"
  resources:
    max_memory_mb: 2048
    max_cpu_percent: 80

routing:
  rules:
    - match: { label: "cpu" }
      prefer: { workload: "cpu" }

result_delivery:
  mode: "direct"                # "direct" | "queue"
  direct:
    max_retries: 3
    retry_backoff_ms: 500
    retry_strategy: "exponential"
  queue:
    type: "kafka"
    brokers: ["kafka:9092"]
    topic: "taskwire-results"

metrics:
  listen_addr: ""               # e.g. "127.0.0.1:9464" — Prometheus /metrics when set (Phase 7)
```

This schema is the contract: `taskwire.example.yaml` (repo root) must contain every field above, because both the Python and Go config parsers are tested against that one file (Phase 1).

---

## Repository Structure

```
taskwire/
├── agent/                        Go sidecar (taskwire-agent binary)
│   ├── cmd/taskwire-agent/       main entrypoint
│   ├── internal/
│   │   ├── config/               config loading + validation
│   │   ├── ipc/                  Unix socket server
│   │   ├── queue/                in-memory queue + lease manager
│   │   ├── scheduler/            label-based routing + work stealing
│   │   ├── worker/               Python worker process manager
│   │   └── cluster/              gossip cluster (memberlist)
│   └── pkg/protocol/             shared wire protocol (used by tests too)
│
├── python/                       Python package (pip install taskwire; pyproject.toml at repo root points here)
│   ├── taskwire/
│   │   ├── task.py               @task decorator, TaskDefinition, BatchSubmission
│   │   ├── runtime.py            Runtime — the developer's entry point
│   │   ├── future.py             TaskFuture
│   │   ├── config.py             Config dataclass hierarchy
│   │   ├── exceptions.py         exception hierarchy
│   │   ├── protocol/             FrameCodec, MessageType (pure-Python fallback)
│   │   ├── _native.pyi           type stubs for the Rust extension
│   │   ├── _bin.py               locate bundled agent binary (Phase 7)
│   │   ├── ipc/                  SidecarClient, ResultServer
│   │   └── worker/               WorkerRunner (spawned by agent)
│   └── tests/
│       ├── harness/              AgentHarness, ClusterHarness, ChaosProxy, ledger
│       │                         (see docs/testing-harness.md)
│       ├── unit/                 no sidecar needed
│       ├── integration/          requires running agent
│       ├── chaos/                fault-injection scenarios, -m chaos
│       └── benchmark/            Phase 7
│
├── native/                       Rust extension crate (taskwire._native)
│   ├── Cargo.toml                pyo3 + maturin, abi3-py311
│   └── src/
│       ├── lib.rs                module init
│       ├── frame.rs              zero-copy frame codec
│       ├── result_server.rs      threaded TCP result listener (no GIL)
│       └── heartbeat.rs          lease heartbeat on a native OS thread
│
├── packaging/                    service templates, cross-compile + wheel scripts, Dockerfile (Phase 7)
│
├── taskwire.example.yaml         canonical config — both parsers tested against it
│
├── docs/
│   ├── architecture.md           this file
│   ├── testing-harness.md        harness + chaos machinery
│   └── phases/                   per-phase implementation plans
│
├── pyproject.toml
└── Makefile
```

Historical note: early drafts used `sdk/` for the Python package; the tree on disk is `python/` and all docs now use that path.

---

## Implementation Order

Each phase doc carries its own build-order section; this is the cross-phase view. The spine is **1 → 2 → 3 → 4**: after Phase 4 a single-node `pip install` + `@task` + `future.result()` works end-to-end, and that is the first moment the project is demoable and the design is validated. Everything after is widening, not deepening.

| Step | What | Why this position |
|------|------|-------------------|
| 1 | Phase 1, pure Python + Go only (skip Rust codec for now) | The wire protocol and config schema are the contract everything else compiles against. The cross-language frame test is the cheapest bug-catcher in the whole project. |
| 2 | Phase 2 with `NullStore` only (skip `BoltStore` for now) | Working agent: SUBMIT/PULL/COMPLETE/STATUS + lease/expiry against a Go test client. Defer durability — it decorates a queue that must exist first. |
| 3 | Phase 3 with the *Python* heartbeat fallback | First end-to-end task execution. Use generous TTLs (30s+) so the GIL-starvation issue can't bite while the plumbing stabilises. |
| 4 | Phase 4, pure Python result server | `@task` / `Runtime` / `TaskFuture` — **milestone: demoable product.** Tag it `0.0.1`, get it in front of a few users; their reaction tells you whether Phases 5–6 are even the right next investment. |
| 5 | `native/` crate: codec → heartbeat → result server (Phases 1/3/4 Rust sections) | One focused pass now that the contracts are frozen and covered by tests. Heartbeat is the priority (correctness: GIL starvation → spurious lease expiry); codec and result server ride along. Parity + `test_heartbeat_survives_gil_hog` are the acceptance gates. |
| 6 | Phase 2's `BoltStore` + dead-letter | Durability matters the moment anyone uses this for real work; it's also fully local (one package), making it a good parallel track while 5 is in flight. |
| 7 | Phase 5 (clustering) | The headline feature, and the riskiest code. Internal order per the phase doc: gossip → router → authed TCP endpoint → stealing → forwarded-task ownership last. |
| 8 | Phase 6 (Kafka delivery) | Optional surface area; needs real demand to justify its CI weight. Skippable for `0.1.0` if nobody asks. |
| 9 | Phase 7 (hardening + release) | Packaging, service install, observability, honest benchmarks, security gate. The name re-check (`taskwire` still free) happens here. |

Parallelisation: steps 5 and 6 are independent of each other (different languages, different packages) and both depend only on step 4. Phase 6 is independent of Phase 5. A second contributor's best entry points are therefore `BoltStore` (6) or Kafka delivery (8).

The test harness (`docs/testing-harness.md`) is built in slices alongside the steps above — `AgentHarness` + fault injection points with step 2, the execution ledger with step 3, `ChaosProxy` with step 4, `ClusterHarness` with step 7. From step 7 onward, a release candidate ships only after a green nightly chaos run on that commit.

Deferred by design: long-poll PULL, ring-buffer queue, per-client Kafka reply topics, custom WAL — all listed in their phase docs as Phase 7+/Phase 8 items. None block a release.

---

## Two-Layer Separation

```
┌─────────────────────────────────────────┐
│  Developer writes                       │
│                                         │
│  @task(label="cpu")                     │
│  def crunch(data): return transform(data│
│                                         │
│  rt.submit(crunch, data)                │
│  future.result()                        │
└─────────────────────────────────────────┘
            │ only coupling: label string
┌─────────────────────────────────────────┐
│  Platform engineer owns                 │
│                                         │
│  taskwire.yaml:                         │
│    workers.count: 8                     │
│    routing.rules:                       │
│      - match: {label: "cpu"}            │
│        prefer: {workload: "cpu"}        │
│    result_delivery.mode: "queue"        │
└─────────────────────────────────────────┘
```

The `label` on `@task` is the only thing that crosses the boundary.
Everything else — executor type, worker count, delivery mode, cluster topology — is invisible to the developer.
