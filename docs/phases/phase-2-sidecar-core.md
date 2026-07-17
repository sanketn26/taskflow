# Phase 2 — Go Agent Core (Single Node)

## Goal

Implement the single-node agent around the behavioral `TaskStateStore` and `ObjectStore` contracts. SQLite and the local filesystem are the production defaults; memory implementations exist for tests and explicitly ephemeral development.

## Phase 0 Baseline

Extend the existing `agent/cmd/taskwire-agent` command and `AgentHarness`; do not introduce a second daemon entry point or test-only process wrapper. Replace `agent/internal/stubserver` with the framed IPC server while preserving `taskwire-agent version`, `taskwire-agent --config PATH` during the pre-release phases, deterministic binary lookup, SIGTERM cleanup, and the harness-owned directory layout (`agent.sock`, `agent.log`, `state/`, and `objects/`). The service-style `run --config` command can be added compatibly in Phase 7.

`AgentHarness.status()` is currently a text readiness probe and `worker_pids()` is a Phase 3 placeholder. This phase changes `status()` to the Phase 1 framed protocol and keeps lifecycle diagnostics and the seeded `ChaosTimeline` intact. Add fault scenarios to the existing `integration`, `chaos`, and `resource` pytest markers rather than creating parallel harness conventions. Every change must continue to pass the Phase 0 version, discovery, lifecycle, and clean-wheel tests.

## Testable Outcome

A raw protocol client can submit, receive an ACK at the configured durability boundary, claim with a fenced lease, renew, complete with an immutable result reference, replay/acknowledge results by owner and cursor, cancel queued work, inspect status, and recover correctly after restart.

## Storage Interfaces

Implement these Go interfaces; backend-specific types must not leak into callers:

```go
type TaskStateStore interface {
    Create(ctx context.Context, task TaskRecord) error
    Claim(ctx context.Context, workerID string, ttl time.Duration) (*TaskRecord, error)
    Renew(ctx context.Context, taskID TaskID, leaseID LeaseID, ttl time.Duration) error
    Complete(ctx context.Context, taskID TaskID, leaseID LeaseID, result ObjectRef) error
    Fail(ctx context.Context, taskID TaskID, leaseID LeaseID, failure Failure) error
    RequeueExpired(ctx context.Context, now time.Time, limit int) ([]TaskID, error)
    Cancel(ctx context.Context, taskID TaskID, ownerID OwnerID) (bool, error)
    Get(ctx context.Context, taskID TaskID) (*TaskRecord, error)
    ListResults(ctx context.Context, ownerID OwnerID, after Cursor, limit int) ([]ResultRecord, Cursor, error)
    AcknowledgeResult(ctx context.Context, taskID TaskID, ownerID OwnerID, cursor Cursor) error
    PurgeResults(ctx context.Context, before time.Time, limit int) ([]ObjectRef, error)
    Close() error
}

type ObjectStore interface {
    Put(ctx context.Context, key ObjectKey, body io.Reader, meta ObjectMetadata) (ObjectRef, error)
    Get(ctx context.Context, ref ObjectRef) (io.ReadCloser, ObjectMetadata, error)
    Stat(ctx context.Context, ref ObjectRef) (ObjectMetadata, error)
    Delete(ctx context.Context, ref ObjectRef) error
    Close() error
}
```

`Create` is content-idempotent by task ID. `Claim` is exclusive and creates the fencing lease. `Complete`/`Fail` atomically write terminal state and a monotonically increasing per-owner result cursor. `Cancel` succeeds only for a queued record owned by the caller and also writes a terminal result. `ListResults` is ordered by cursor and owner-isolated. Acknowledgement is idempotent and cannot acknowledge another owner or mismatched cursor. `PurgeResults` removes only acknowledged or retention-expired terminal records and returns object references that may have become unreferenced. Object references are immutable; all reads validate size and SHA-256.

The v0.1 backend registry contains `memory` and `sqlite` for state, and `memory` and `filesystem` for objects. PostgreSQL/S3 remain separately gated future adapters; configuring an unregistered backend fails startup with `unsupported_backend`.

SQLite owns ordering and eligibility; there is no separate in-memory queue whose state can diverge. `Claim` atomically selects an eligible task, increments its attempt, and creates a unique `lease_id`. `Renew`, `Complete`, and `Fail` compare that lease. Late completions return `stale_lease` without changing state.

### Minimum state model

Tasks use `queued`, `leased`, `succeeded`, `failed`, `cancelled`, and `dead_lettered`. Persist task envelope/reference, owner, attempt count, active lease and expiry, terminal reference/failure, result cursor, acknowledgement time, and timestamps. Schema creation and migrations are transactional and versioned.

SQLite requirements: WAL journal mode, foreign keys enabled, busy timeout configured, synchronous mode documented, and crash-safe transactions. Filesystem object writes use a same-filesystem temporary file, checksum while streaming, file fsync, atomic rename, and parent-directory fsync. Reads verify recorded size/checksum; partial temporary files are cleaned on startup.

## Agent Components

```text
agent/internal/state/{store,memory,sqlite}.go
agent/internal/object/{store,memory,filesystem}.go
agent/internal/ipc/server.go
agent/internal/lease/reaper.go
agent/internal/worker/manager.go
agent/internal/status/status.go
agent/internal/faults/faults.go
agent/cmd/taskwire-agent/main.go
```

### IPC lifecycle

- Remove a stale socket only after proving no live listener owns it.
- Bind the Unix socket, set `0660`, and apply the configured group before accepting.
- Each connection has one serialized writer; request handlers never interleave frame bytes.
- Require Phase 1 `HELLO`, enforce its role/owner permissions for every request, and route direct responses by request ID.
- Permit only one primary result-notification connection per owner; replacement is atomic and never drops persisted results.
- Decode and schema errors receive `ERROR` when possible and close only the offending connection.
- Track an authenticated/declared `owner_id` per Runtime connection. Disconnect removes connection registrations, never task/result state.
- Bound reads, writes, pending requests, and shutdown waits with deadlines.

### Request semantics

- `SUBMIT`: validate identity and envelope; store referenced/large input first; idempotently `Create` by task ID; ACK only after the selected backend durability boundary. Same ID plus same content returns the same ACK; same ID plus different content is `task_conflict`.
- `PULL`: atomically `Claim`; return an empty ACK when no eligible task exists; otherwise return `TASK` with lease ID, TTL, and attempt.
- `HEARTBEAT`: renew only the matching active lease. Unknown/stale leases return a typed error.
- `COMPLETE`: validate the referenced result through `ObjectStore.Stat`, then atomically record terminal state/result record under the active lease. Only after commit may the agent send `RESULT` to a connected owner.
- `RESUME_RESULTS`: call `ListResults(owner, after, limit)` and stream ordered notifications. Never expose another owner's records.
- `TASK_QUERY`: return owner-isolated snapshots in request order; unknown and wrong-owner IDs are indistinguishable.
- Result ACK: atomically acknowledge the matching owner/task/cursor; duplicates are idempotent.
- `CANCEL`: conditional queued → cancelled transition. Leased, remote, terminal, or unknown tasks return `too_late`; a successful cancellation creates a terminal result record so the Future resolves.
- `STATUS`: local-socket-only snapshot with counts by state, active leases, worker PIDs/restarts, storage health, and cluster members.

Connection queues, active transfers, object bytes, notifier batches, and query batches are bounded by the Phase 1 configuration. When a connection's serialized write queue reaches its bound, close it with `transfer_limit` or `shutdown` as appropriate; never allow a slow Runtime to block state commits, lease renewal, or another connection.

### Lease reaper

Poll using a monotonic scheduling loop while comparing persisted wall-clock expiries. `RequeueExpired(now, limit)` is transactional. Tasks below `max_attempts` become eligible again; exhausted tasks become dead-lettered with a terminal failure/result record. Reaper batches are bounded and repeat until no expired rows remain.

### Retention and object garbage collection

A separate bounded sweeper applies `storage.objects.result_retention_seconds`. Acknowledgement permits cleanup but does not require immediate deletion; expiry permits cleanup even without ACK. Purging state and deleting objects are intentionally not one transaction: first remove/mark the state reference, then delete only when a state-store reference check proves no live task, result, transfer, or outbox row uses the object. Delete is idempotent. Failures retry with metrics/logging, and a missing referenced object is a `StorageConsistencyError`, never silently treated as an empty result.

### Worker manager

Spawn configured Python workers with the agent socket and config path. Track PIDs, exit status, and restart count. Apply bounded exponential restart backoff and a restart-rate circuit breaker so poison tasks cannot cause a fork storm. `workers.count: 0` is valid.

### Shutdown order

1. Stop accepting new connections and submissions.
2. Stop new claims and allow active workers to drain for `workers.shutdown_grace_ms`.
3. Stop workers, lease reaper, and result notifiers.
4. Close listeners, object stores, and state store.
5. Remove the socket only if this process created it.

## Required Tests

- Common state-store conformance: idempotent create, concurrent claim exclusivity, fencing, renewal, expiry/requeue, max-attempt dead letter, cancellation races, cursor ordering/replay, acknowledgement idempotency, and reopen behavior.
- Common object-store conformance: immutable put/get/stat, checksum mismatch, duplicate put, partial-write cleanup, path traversal rejection, and reopen.
- Retention tests prove unacknowledged results replay before expiry, acknowledged/expired results become purgeable, shared objects are not deleted while referenced, and cleanup resumes after restart.
- SQLite race tests under `go test -race`; filesystem crash/reopen tests on real temporary directories.
- Disconnect before SUBMIT ACK never produces a false durability claim.
- Runtime disconnect before result ACK followed by reconnect replays the record.
- Malformed/truncated/oversized frames do not crash the agent or allocate claimed sizes.
- Concurrent request IDs receive the matching response even when handlers finish out of order; a slow result consumer cannot stall workers or other owners.
- HELLO role violations, duplicate request IDs, owner replacement, TASK_QUERY non-disclosure, and every owner-scoped operation are tested.
- Graceful shutdown rejects new work and leaves no worker or socket leaks.
- Kill/restart with SQLite loses no acknowledged task; the explicit memory backend test demonstrates and documents that restart may lose acknowledged state.
- At scenario end the agent is alive unless deliberately killed, has no panic, has no orphan workers/socket, and reports conservation: submitted equals terminal plus queued/leased work.

Plant failpoints for state-create-before-ACK, object-put-before-state-create, terminal-commit-before-notify, result-notify-before-ACK, and lease-expiry races.

## Implementation Order

1. Memory state/object stores and shared conformance suites.
2. SQLite state store and filesystem object store.
3. Lease reaper, status snapshot, and worker manager.
4. IPC server request lifecycle and replayable result notifier.
5. Replace the Phase 0 stub server/status probe in place and add crash, race, malformed-input, and graceful-shutdown scenarios to the existing harness.
6. Run the root `unit`, `integration`, and `smoke-wheel` targets so storage/runtime additions are tested through the packaged agent path.

## Exit Gate

Phase 2 is complete when SQLite/filesystem recovery and conformance tests pass, ACK/lease/result ordering is demonstrated by failpoint tests, cancellation survives reopen, result replay works after disconnect, and no in-memory queue, Bolt store, WAL config, callback address, or direct worker-to-app result path remains.
