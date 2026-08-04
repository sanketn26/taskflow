# Taskwire Architecture Overview

This document is a non-normative orientation guide. All requirements, schemas, failure semantics, tests, and exit gates are contained in the phase documents; implementation must not depend on this overview.

## System Shape

Taskwire is a brokerless, at-least-once task system with a language-neutral execution protocol and a Go control plane. The control plane is **gRPC over a local Unix domain socket**, defined by `proto/taskwire/v1/control.proto`: any language with a gRPC implementation can generate a working client from that file alone. The v0.1 Python Runtime submits registered task identities and input references to a Go agent. The agent owns transactional task state, leases, immutable objects, capability-aware worker pools, replayable results, and optional clustering/integrations. Python is the first worker runtime; Node.js and Go workers are planned bindings of the same service rather than separate schedulers.

```text
Python Runtime (v0.1; later Node.js/Go clients)
      │ gRPC over Unix socket: Submit, Cancel, WatchResults, QueryTasks
      ▼
Go agent ───── TaskStateStore (SQLite default)
   │   └────── ObjectStore (filesystem default)
   │
   ├──── managed worker pools: Python; later Node.js and Go (Work stream)
   ├──── optional authenticated peer agents
   └──── optional Kafka terminal-event outbox
```

The gRPC boundary is deliberately thin. Generated messages enter handlers,
semantic validation rejects invalid domain values, and handlers call task,
worker, result, and object services. There is no second transport-neutral
session framework layered over gRPC.

Local Runtime identity travels in per-RPC metadata (`taskwire-role`,
`taskwire-owner-id`) and is admitted by Unix-socket ownership and permissions.
Worker capability and lease state belong exclusively to one `Work` stream.
Peer-agent identity is a separate authenticated transport concern introduced
with clustering; a local caller cannot become a peer merely by supplying a role
string. Errors are gRPC statuses carrying stable Taskwire error details.

Workers never connect to submitting applications. The first `Work` message
registers runtime, codecs, and a complete capability generation; later
`update_tasks` messages replace that snapshot. Pulls are fenced by worker ID and
generation. Stream lifetime bounds capability and lease ownership, so a dropped
stream triggers recovery. Workers read and write objects through their local
agent and complete under a fencing lease. Portable tasks use one value encoded
with the shared MsgPack profile or bytes. Python-specific calling conventions
and cloudpickle remain explicit non-portable capabilities. The origin agent
commits terminal state before notifying the Runtime. Owner ID plus cursor makes
results replayable across reconnects until acknowledgement or retention expiry.

SQLite/filesystem requires no external service. Memory backends are explicitly ephemeral. PostgreSQL/S3 are not supported merely because interfaces leave room for future adapters — that support is Phase 6's own conformance-tested deliverable. Kafka is an optional downstream terminal-event integration, never Runtime result delivery or task completion storage.

## Delivery Sequence

```text
serialize/store input
→ durable task Create
→ Submit response
→ exclusive Claim + fencing lease
→ execute with heartbeat
→ immutable result Put
→ fenced terminal transaction + result cursor
→ WatchResults notification
→ AckResult
```

Execution is at least once, not exactly once. Cancellation succeeds only while queued at the current owner. Cluster partitions and expired leases may create duplicates; terminal writes and remote completions are fenced/idempotent.

## Authoritative Phase Set

1. [Phase 0 — Repository, Packaging, and Test Spine](phases/phase-0-repository-packaging.md)
2. [Phase 1 — Protocol and Configuration](phases/phase-1-protocol-config.md)
3. [Phase 2 — Go Agent Core](phases/phase-2-sidecar-core.md)
4. [Phase 3 — Python Worker and Single-Node E2E](phases/phase-3-worker-e2e.md)
5. [Phase 4 — Python SDK](phases/phase-4-python-sdk.md)
6. [Phase 5 — Clustering](phases/phase-5-clustering.md)
7. [Phase 6 — Distributed Storage Backends](phases/phase-6-distributed-storage-backends.md)
8. [Phase 7 — Kafka Result Integration](phases/phase-7-queue-delivery.md)
9. [Phase 8 — Hardening and Release](phases/phase-8-hardening-release.md)
10. [Phase 9 — Examples and Adoption](phases/phase-9-examples-use-cases.md)
11. [Phase 10 — Node.js Worker and SDK](phases/phase-10-nodejs-runtime.md)
12. [Phase 11 — Go Worker and SDK](phases/phase-11-go-runtime.md)
13. [Phase 12 — Admin Console (Flower-class)](phases/phase-12-admin-console.md)

Each phase is independently implementation-ready: it declares its own contract, required tests, implementation order, and exit gate. If this overview differs from a phase, the phase wins.
