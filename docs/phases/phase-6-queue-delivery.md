# Phase 6 — Kafka Result Integration

## Goal

Add Kafka as an optional integration fed from already-committed terminal result records. Kafka does not replace agent-relayed Runtime results, change worker behavior, or become the source of truth.

## Testable Outcome

When enabled, each committed terminal result is published durably and idempotently by the origin agent through an outbox. Broker outages do not roll back task completion or block Runtime cursor replay; publication resumes after restart. Consumers can use Kafka for downstream workflows without accessing worker payload paths or another owner's results accidentally.

## Semantics

Authoritative ordering remains:

```text
store result object
→ fenced terminal state + result record + Kafka outbox row (one transaction)
→ notify Runtime and publish outbox independently
→ Runtime ACK / broker delivery ACK
```

The worker sends COMPLETE only to its local agent. It never creates a Kafka producer. A Kafka failure is integration lag, not `DeliveryFailedError`, and does not change a succeeded task to failed.

Publication is at least once. The stable Kafka message key is task ID and the event includes a unique event ID so consumers can deduplicate. The agent removes/marks an outbox row only after the producer delivery callback confirms broker acknowledgement; `produce()` or `flush()` alone is insufficient.

## Event Schema

Use a versioned msgpack envelope:

```text
{
  schema_version: 1,
  event_id: binary(16),
  task_id: binary(16),
  owner_id: binary(16),
  cursor: uint64,
  state: "succeeded" | "failed" | "cancelled",
  result: ObjectRef | nil,
  failure: Failure | nil,
  completed_at_unix_ms: int64
}
```

The topic contains references and bounded failure metadata, not arbitrary large result bytes. Consumers need authorized access to the configured object store to dereference results. Owner IDs are sensitive routing capabilities; deployments requiring tenant isolation use separate topics/ACLs or a redacted event policy.

## Agent Outbox Publisher

```text
agent/internal/integrations/kafka/publisher.go
agent/internal/state/outbox.go
agent/internal/config/config.go
python/taskwire/integrations/kafka.py   # optional consumer helper only
```

Requirements:

- Claim outbox rows with a publisher lease so concurrent loops do not publish the same row unnecessarily.
- Preserve per-owner cursor order when configured; no global ordering is promised across partitions.
- Use task ID as the default partition key.
- Bound batch size, in-flight messages, delivery timeout, retry backoff, and shutdown drain.
- Retry retriable errors indefinitely within retention; surface permanent/serialization errors in status and metrics without dropping the row.
- On restart, release expired publisher leases and resume pending rows.
- Define an outbox retention/dead-letter operator policy; never silently discard unpublished events.
- Kafka dependencies are optional and absent from default builds/tests.

## Consumer Helper

An optional Python helper decodes and validates events, exposes the event ID/task/owner/cursor, and fetches referenced objects only through an explicitly configured authorized store client. It does not resolve `TaskFuture`; the Runtime continues to use its local agent and owner cursor. Consumer offset commits happen only after the user handler succeeds or explicitly dead-letters the event.

No `result_delivery.mode`, callback address, per-Runtime broadcast consumer group, or `Runtime.reattach` Kafka dependency is introduced.

## Required Tests

- Terminal state and outbox row commit atomically.
- Broker delivery callback is required before marking published.
- Kafka down during completion: Runtime result still resolves; status reports integration lag; publishing resumes after recovery/restart.
- Duplicate publication has the same event ID and task key.
- Producer timeout, retriable error, permanent error, poison row, and shutdown with in-flight events.
- Per-owner ordering when enabled and documented lack of cross-owner order.
- Maximum event size and malformed consumer event rejection.
- Two agents publish only their owned terminal records; forwarded completion publishes once at the origin.
- Kafka tests use testcontainers under a separate marker; the default suite remains infrastructure-free.

## Implementation Order

1. Versioned event codec and outbox state-store operations.
2. Publisher with a fake broker and delivery-report tests.
3. Optional Kafka client adapter and testcontainer outage/recovery tests.
4. Status/metrics, retention tooling, and optional consumer helper.

## Exit Gate

Phase 6 is complete when broker outage/recovery and agent restart tests prove no committed result depends on Kafka, outbox publication is replayable and deduplicatable, forwarded results publish once at the origin, and the full Phases 1–5 suite passes with Kafka absent.
