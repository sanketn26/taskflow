# Taskwire Architecture Overview

This document is a non-normative orientation guide. All requirements, schemas, failure semantics, tests, and exit gates are contained in the phase documents; implementation must not depend on this overview.

## System Shape

Taskwire is a brokerless, at-least-once Python task system. A Python Runtime submits registered task identities and argument references over a local Unix socket to a Go agent. The agent owns transactional task state, leases, immutable objects, managed Python workers, replayable results, and optional clustering/integrations.

```text
Python Runtime
      │ local Unix socket: submit, cancel, result replay
      ▼
Go agent ───── TaskStateStore (SQLite default)
   │   └────── ObjectStore (filesystem default)
   │
   ├──── managed Python workers: pull, heartbeat, COMPLETE(ref)
   ├──── optional authenticated peer agents
   └──── optional Kafka terminal-event outbox
```

Workers never connect to submitting applications. They resolve exact registered task name/version, read and write objects through their local agent, and complete under a fencing lease. The origin agent commits terminal state before notifying the Runtime. Owner ID plus cursor makes results replayable across connection and Runtime restart until acknowledgement/retention.

SQLite/filesystem requires no external service. Memory backends are explicitly ephemeral. PostgreSQL/S3 are not supported merely because interfaces leave room for future adapters. Kafka is an optional downstream terminal-event integration, never Runtime result delivery or task completion storage.

## Delivery Sequence

```text
serialize/store input
→ durable task Create
→ SUBMIT ACK
→ exclusive Claim + fencing lease
→ execute with heartbeat
→ immutable result Put
→ fenced terminal transaction + result cursor
→ Runtime RESULT notification
→ Runtime ACK
```

Execution is at least once, not exactly once. Cancellation succeeds only while queued at the current owner. Cluster partitions and expired leases may create duplicates; terminal writes and remote completions are fenced/idempotent.

## Authoritative Phase Set

1. [Phase 0 — Repository, Packaging, and Test Spine](phases/phase-0-repository-packaging.md)
2. [Phase 1 — Protocol and Configuration](phases/phase-1-protocol-config.md)
3. [Phase 2 — Go Agent Core](phases/phase-2-sidecar-core.md)
4. [Phase 3 — Python Worker and Single-Node E2E](phases/phase-3-worker-e2e.md)
5. [Phase 4 — Python SDK](phases/phase-4-python-sdk.md)
6. [Phase 5 — Clustering](phases/phase-5-clustering.md)
7. [Phase 6 — Kafka Result Integration](phases/phase-6-queue-delivery.md)
8. [Phase 7 — Hardening and Release](phases/phase-7-hardening-release.md)
9. [Phase 8 — Examples and Adoption](phases/phase-8-examples-use-cases.md)

Each phase is independently implementation-ready: it declares its own contract, required tests, implementation order, and exit gate. If this overview differs from a phase, the phase wins.
