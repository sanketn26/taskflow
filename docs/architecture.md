# Taskflow Architecture

## Value Proposition

High-performance distributed task execution with no operational broker.
Celery-style task composition, Go-powered runtime, ships as a single `pip install`.

---

## Design Principles

| Principle | Application |
|-----------|-------------|
| Two-layer separation | Platform engineers own config. Developers own task logic. Neither touches the other. |
| No external infrastructure | The Go sidecar IS the broker. No Redis, no RabbitMQ, no Zookeeper. |
| At-least-once delivery | Lease + heartbeat ensures tasks re-queue on worker failure. Idempotency is the caller's responsibility. |
| Pull over push | Workers pull from sidecar. Backpressure is natural. No overwhelmed workers. |
| Result delivery is pluggable | Direct TCP (zero infra, retries on failure) or Kafka (retention, replay). Config-driven, invisible to developer. |
| Python 3.13+ free-threaded | No GIL. SDK uses threads aggressively for result server, heartbeat, and worker concurrency. |

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
│  Go Sidecar  (taskflow-agent)          always-on system daemon  │
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
 0       1               17      18          22
 ┌───────┬───────────────┬───────┬───────────┬─────────────────┐
 │ type  │   task_id     │ flags │  pay_len  │    payload      │
 │  1B   │    16B        │  1B   │    4B     │    N bytes      │
 └───────┴───────────────┴───────┴───────────┴─────────────────┘
```

| Field | Description |
|-------|-------------|
| type | MessageType enum (1 byte) |
| task_id | UUID as raw bytes (16 bytes) |
| flags | Bit field: 0x01 = error, 0x02 = idempotent |
| pay_len | Payload length, big-endian uint32 |
| payload | Message-specific data (cloudpickle, msgpack, or empty) |

### Message Types

| Value | Name | Direction | Payload |
|-------|------|-----------|---------|
| 0x01 | SUBMIT | App → Sidecar | msgpack: {task_bytes, callback_addr, label, idempotent} |
| 0x02 | PULL | Worker → Sidecar | empty |
| 0x03 | TASK | Sidecar → Worker | msgpack: {task_bytes, lease_id, ttl, callback_addr} |
| 0x04 | HEARTBEAT | Worker → Sidecar | msgpack: {lease_id} |
| 0x05 | RESULT | Worker → App | cloudpickle result (flags=0x00) or exception (flags=0x01) |
| 0x07 | COMPLETE | Sidecar → App | msgpack: {task_id, status} — lease released |
| 0x08 | STEAL | Sidecar → Sidecar | msgpack: {n} — request n tasks |
| 0x09 | ACK | Any → Any | empty |

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

socket: "/var/run/taskflow/agent.sock"
advertise_addr: "10.0.1.5"     # routable IP for result callbacks

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
    topic: "taskflow-results"
```

---

## Repository Structure

```
taskflow/
├── agent/                        Go sidecar (taskflow-agent binary)
│   ├── cmd/taskflow-agent/       main entrypoint
│   ├── internal/
│   │   ├── config/               config loading + validation
│   │   ├── ipc/                  Unix socket server
│   │   ├── queue/                in-memory queue + lease manager
│   │   ├── scheduler/            label-based routing + work stealing
│   │   ├── worker/               Python worker process manager
│   │   └── cluster/              gossip cluster (memberlist)
│   └── pkg/protocol/             shared wire protocol (used by tests too)
│
├── sdk/                          Python package (pip install taskflow)
│   ├── taskflow/
│   │   ├── task.py               @task decorator, TaskDefinition, BatchSubmission
│   │   ├── runtime.py            Runtime — the developer's entry point
│   │   ├── future.py             TaskFuture
│   │   ├── config.py             Config dataclass hierarchy
│   │   ├── exceptions.py         exception hierarchy
│   │   ├── protocol/             FrameCodec, MessageType
│   │   ├── ipc/                  SidecarClient, ResultServer
│   │   └── worker/               WorkerRunner (spawned by agent)
│   └── tests/
│       ├── unit/                 no sidecar needed
│       └── integration/          requires running agent
│
├── config/
│   └── taskflow.example.yaml
│
├── docs/
│   ├── architecture.md           this file
│   └── phases/                   per-phase implementation plans
│
└── Makefile
```

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
│  taskflow.yaml:                         │
│    workers.count: 8                     │
│    routing.rules:                       │
│      - match: {label: "cpu"}            │
│        prefer: {workload: "cpu"}        │
│    result_delivery.mode: "queue"        │
└─────────────────────────────────────────┘
```

The `label` on `@task` is the only thing that crosses the boundary.
Everything else — executor type, worker count, delivery mode, cluster topology — is invisible to the developer.
