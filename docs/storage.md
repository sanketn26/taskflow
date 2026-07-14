# Storage Decision Summary

This file is a non-normative decision summary. The complete interfaces, records, ordering rules, backend behavior, configuration, tests, and exit gates are embedded in Phases 1–6. Implementers do not need this document.

## Decision

Taskwire separates transactional task state from immutable objects:

- `TaskStateStore` owns task creation, eligibility, claims, fencing leases, attempts, cancellation, terminal state, owner result cursors, acknowledgements, and transfer/outbox state.
- `ObjectStore` owns immutable input/result bytes, metadata, size, codec, and SHA-256 integrity.

The zero-infrastructure defaults are SQLite and the local filesystem. Memory implementations are for tests and explicitly ephemeral development. Future PostgreSQL/S3 adapters must pass the same conformance contracts before being advertised.

The ordering boundary is:

```text
object Put → fenced terminal transaction/result record → notify Runtime → Runtime ACK
```

Workers access storage through their local agent and COMPLETE with an `ObjectRef`. They never receive Runtime callback addresses. The origin agent replays results by owner ID and cursor. Kafka, when enabled, publishes committed terminal events from a transactional outbox and is not a source of truth.

Normative storage work is contained in:

- Phase 1: wire references, value unions, object transfer, and configuration.
- Phase 2: interfaces, SQLite/filesystem semantics, state transitions, conformance, and recovery.
- Phase 3: worker object access and fenced completion.
- Phase 4: transparent SDK upload/download and result replay.
- Phase 5: remote object movement and durable origin ownership.
- Phase 6: transactional terminal-event outbox.
