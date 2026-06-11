# Taskwire Architecture

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
| Result delivery is pluggable | Direct TCP (zero infra, retries on failure) or Kafka (retention, replay). Config-driven, invisible to developer. Direct mode requires the submitting app to be reachable from workers (`advertise_addr` must be routable — NAT/firewalls break it; use queue mode there). |
| Broad Python support | Standard CPython 3.11+ is the baseline. Free-threaded builds (3.13t+) are an *accelerator*, never a requirement — requiring 3.13t would exclude nearly the entire ecosystem (most C-extension wheels still don't ship free-threaded variants). |
| Rust for the hot path | Infrastructure threads (frame codec, result server, worker heartbeat) are implemented in a Rust extension (`taskwire._native`, PyO3/maturin) with a pure-Python fallback. Rust threads run without the GIL, so heartbeats and result handling stay responsive even while task code holds the GIL on standard builds. |
| Secure by default | Unix socket is `0660` owned by a dedicated group; gossip traffic is encrypted with a shared key; docs state plainly that anyone who can write to the socket can execute arbitrary code (cloudpickle). |

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
    │   RESULT (direct TCP)    │                        │
    │   task_id + result_bytes │                        │
    │                          │                        │
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
    │                  │                    │   result_bytes    │
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
| flags | Bit field: 0x01 = error, 0x02 = idempotent |
| pay_len | Payload length, big-endian uint32. Receivers enforce `max_frame_size` (default 16 MiB) — a corrupt or hostile length prefix must not OOM the agent. |
| payload | Message-specific data (cloudpickle, msgpack, or empty) |

### Message Types

| Value | Name | Direction | Payload |
|-------|------|-----------|---------|
| 0x01 | SUBMIT | App → Sidecar | msgpack: {task_bytes, callback_addr, label, idempotent} |
| 0x02 | PULL | Worker → Sidecar | empty |
| 0x03 | TASK | Sidecar → Worker | msgpack: {task_bytes, lease_id, ttl, callback_addr} |
| 0x04 | HEARTBEAT | Worker → Sidecar | msgpack: {lease_id} |
| 0x05 | RESULT | Worker → App | cloudpickle result (flags=0x00) or exception (flags=0x01) |
| 0x06 | CANCEL | App → Sidecar | empty — best-effort: remove task from queue if not yet leased; reply ACK (flags=0x00 cancelled, 0x01 too late) |
| 0x07 | COMPLETE | Sidecar → App | msgpack: {task_id, status} — lease released |
| 0x08 | STEAL | Sidecar → Sidecar | msgpack: {n} — request n tasks |
| 0x09 | ACK | Any → Any | empty |

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
  node_name: ""                 # auto-generated UUID if empty
  bind_addr: "0.0.0.0:7946"
  advertise_addr: "10.0.1.5:7946"
  seeds: []
  mdns: true
  encryption_key: ""            # base64 32-byte key; gossip + STEAL encrypted when set

socket: "/var/run/taskwire/agent.sock"
socket_group: "taskwire"        # socket chmod 0660, chown root:taskwire
advertise_addr: "10.0.1.5"     # routable IP for result callbacks

queue:
  persistence: "none"           # "none" | "wal" — wal survives agent restart
  wal_dir: "/var/lib/taskwire/wal"
  max_attempts: 5               # after this many lease expiries → dead-letter
  max_frame_size_mb: 16

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
```

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
├── sdk/                          Python package (pip install taskwire)
│   ├── taskwire/
│   │   ├── task.py               @task decorator, TaskDefinition, BatchSubmission
│   │   ├── runtime.py            Runtime — the developer's entry point
│   │   ├── future.py             TaskFuture
│   │   ├── config.py             Config dataclass hierarchy
│   │   ├── exceptions.py         exception hierarchy
│   │   ├── protocol/             FrameCodec, MessageType (pure-Python fallback)
│   │   ├── _native.pyi           type stubs for the Rust extension
│   │   ├── ipc/                  SidecarClient, ResultServer
│   │   └── worker/               WorkerRunner (spawned by agent)
│   └── tests/
│       ├── unit/                 no sidecar needed
│       └── integration/          requires running agent
│
├── native/                       Rust extension crate (taskwire._native)
│   ├── Cargo.toml                pyo3 + maturin, abi3-py311
│   └── src/
│       ├── lib.rs                module init
│       ├── frame.rs              zero-copy frame codec
│       ├── result_server.rs      threaded TCP result listener (no GIL)
│       └── heartbeat.rs          lease heartbeat on a native OS thread
│
├── config/
│   └── taskwire.example.yaml
│
├── docs/
│   ├── architecture.md           this file
│   └── phases/                   per-phase implementation plans
│
└── Makefile
```

---

## Implementation Order

Each phase doc carries its own build-order section; this is the cross-phase view. The spine is **1 → 2 → 3 → 4**: after Phase 4 a single-node `pip install` + `@task` + `future.result()` works end-to-end, and that is the first moment the project is demoable and the design is validated. Everything after is widening, not deepening.

| Step | What | Why this position |
|------|------|-------------------|
| 1 | Phase 1, pure Python + Go only (skip Rust codec for now) | The wire protocol and config schema are the contract everything else compiles against. The cross-language frame test is the cheapest bug-catcher in the whole project. |
| 2 | Phase 2 with `NullStore` only (skip `BoltStore` for now) | Working agent: SUBMIT/PULL/lease/expiry against a Go test client. Defer durability — it decorates a queue that must exist first. |
| 3 | Phase 3 with the *Python* heartbeat fallback | First end-to-end task execution. Use generous TTLs (30s+) so the GIL-starvation issue can't bite while the plumbing stabilises. |
| 4 | Phase 4, pure Python result server | `@task` / `Runtime` / `TaskFuture` — **milestone: demoable product.** Tag it `0.0.1`, get it in front of a few users; their reaction tells you whether Phases 5–6 are even the right next investment. |
| 5 | `native/` crate: codec → heartbeat → result server (Phases 1/3/4 Rust sections) | One focused pass now that the contracts are frozen and covered by tests. Heartbeat is the priority (correctness: GIL starvation → spurious lease expiry); codec and result server ride along. Parity + `test_heartbeat_survives_gil_hog` are the acceptance gates. |
| 6 | Phase 2's `BoltStore` + dead-letter | Durability matters the moment anyone uses this for real work; it's also fully local (one package), making it a good parallel track while 5 is in flight. |
| 7 | Phase 5 (clustering) | The headline feature, and the riskiest code. Internal order per the phase doc: gossip → router → authed TCP endpoint → stealing → forwarded-task ownership last. |
| 8 | Phase 6 (Kafka delivery) | Optional surface area; needs real demand to justify its CI weight. Skippable for `0.1.0` if nobody asks. |
| 9 | Phase 7 (hardening + release) | Packaging, service install, observability, honest benchmarks, security gate. The name re-check (`taskwire` still free) happens here. |

Parallelisation: steps 5 and 6 are independent of each other (different languages, different packages) and both depend only on step 4. Phase 6 is independent of Phase 5. A second contributor's best entry points are therefore `BoltStore` (6) or Kafka delivery (8).

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
