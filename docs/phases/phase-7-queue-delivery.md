# Phase 7 — Kafka Result Integration

## Goal

Add Kafka as an optional integration fed from already-committed terminal result records. Kafka does not replace agent-relayed Runtime results, change worker behavior, or become the source of truth.

## Phase 0 Baseline

Kafka remains an optional Python dependency through the existing `kafka` extra and must not enter the default install, import path, agent startup path, or clean-wheel smoke test. Agent-side Kafka code stays behind an adapter boundary so `make build-agent`, `make unit`, and the default integration suite remain broker-free at runtime while producing the same agent binary used when Kafka is enabled.

Use the existing `kafka` pytest marker and `integrations.kafka` configuration namespace. Kafka-enabled artifact tests start from the same built wheel and packaged `taskwire-agent` used by the Phase 0 smoke path; they must not substitute source-tree imports or a special agent binary.

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

The Go agent owns a small producer interface (`Produce`, delivery-report channel, `Close`) implemented with `github.com/twmb/franz-go`, pinned in `go.mod`; tests use a fake implementation. Kafka code is compiled into the normal agent but creates no producer, goroutine, DNS lookup, or broker connection while disabled. The Python `kafka` extra is only for the optional consumer helper and is not the agent publisher dependency.

Each outbox row persists `event_id`, task/owner/cursor, encoded event bytes, task key, state (`pending`, `leased`, `published`, `dead_lettered`), lease owner/expiry, attempts, next-attempt time, last stable error code, created time, and published time. The event ID and encoded bytes are created in the same transaction as the terminal result and never regenerated on retry. Permanent errors remain operator-visible until an explicit administrative dead-letter action; retention never silently deletes pending/leased rows.

Requirements:

- Claim outbox rows with a publisher lease so concurrent loops do not publish the same row unnecessarily.
- Preserve per-owner cursor order when configured; no global ordering is promised across partitions.
- Use task ID as the default partition key.
- Bound batch size, in-flight messages, delivery timeout, retry backoff, and shutdown drain.
- Retry retriable errors indefinitely within retention; surface permanent/serialization errors in status and metrics without dropping the row.
- On restart, release expired publisher leases and resume pending rows.
- Published rows are retained for the configured `published_retention_seconds`; permanent rows require the explicit `taskwire-agent kafka dead-letter --event-id` operator command and are never silently discarded.
- Kafka is disabled in default configuration and its integration dependencies are absent from the default Python test environment; the normal Go agent artifact contains the dormant producer adapter.

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
4. Status/metrics, retention tooling, and optional consumer helper behind the existing `kafka` extra.
5. Re-run the default artifact and integration suites with Kafka dependencies absent.

## Exit Gate

Phase 7 is complete when broker outage/recovery and agent restart tests prove no committed result depends on Kafka, outbox publication is replayable and deduplicatable, forwarded results publish once at the origin, and the full Phases 1–5 suite passes with Kafka absent.

---

## Implementation Guide

> **Kafka is a notification side-channel.** Runtime Futures still use agent RESULT
> replay. Workers never import a Kafka client.

### File map

```text
agent/internal/integrations/kafka/publisher.go
agent/internal/integrations/kafka/producer.go   # franz-go adapter + fake for tests
agent/internal/state/outbox.go                  # outbox ops on TaskStateStore or sibling
python/taskwire/integrations/kafka.py           # optional consumer helper (extra)
python/tests/integration/test_kafka_outbox.py   # marker: kafka
```

### Atomic terminal + outbox write

```go
// Inside Complete/Fail/Cancel transaction on origin:
//  1. write terminal task state + result cursor row
//  2. insert outbox row with event_id = new UUID, encoded event bytes FIXED
//  3. commit
// Only then: notify Runtime + async publish outbox
```

### Event encode (msgpack)

```python
# logical event — implement in Go for producer; Python helper decodes same shape
event = {
    "schema_version": 1,
    "event_id": event_id_16,
    "task_id": task_id_16,
    "owner_id": owner_id_16,
    "cursor": cursor_u64,
    "state": "succeeded",  # | failed | cancelled
    "result": object_ref_or_none,
    "failure": failure_or_none,
    "completed_at_unix_ms": int(...),
}
# partition key = task_id; consumers dedupe on event_id
```

### Publisher loop

```go
type Producer interface {
	Produce(ctx context.Context, topic string, key, value []byte) (delivery <-chan error)
	Close() error
}

func (p *Publisher) Run(ctx context.Context) {
	for {
		rows := claimOutboxBatch(limit) // publisher lease
		for _, row := range rows {
			ch := p.prod.Produce(ctx, topic, row.TaskID[:], row.Bytes)
			select {
			case err := <-ch:
				if err == nil {
					markPublished(row)
				} else {
					backoff(row, err)
				}
			case <-time.After(deliveryTimeout):
				backoff(row, errTimeout)
			}
		}
	}
}
```

**Rules:**

- Disabled config ⇒ no producer, no goroutine, no DNS  
- Mark published only on **delivery callback**, not `Produce()` return alone  
- Kafka down ⇒ Runtime still resolves; status shows outbox lag  
- Forwarded completions publish **once at origin** only  

### Consumer helper (optional Python extra)

```python
# pip install taskwire[kafka]
from taskwire.integrations.kafka import ResultEventConsumer

def handle(event):
    # event.event_id, event.task_id, event.state
    # fetch object via authorized store client if needed
    ...

# commit offsets only after handle succeeds
```

### Tests

```bash
# default suite must not need a broker
make unit integration

# kafka tier
pytest -m kafka -v
```

### Done checklist

- [ ] Terminal commit does not depend on broker  
- [ ] Same event_id on retry/duplicate publish  
- [ ] Delivery callback required  
- [ ] Default install has no kafka import side effects  
- [ ] Origin-only publish for remote completions  

### Review request

```text
Please review Phase 7.
Commands: make unit integration; pytest -m kafka
Gaps: ...
```
