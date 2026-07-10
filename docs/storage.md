# Storage and Reference Architecture

## Decision

Taskwire separates transactional task state from immutable payload/result objects. These are two interfaces because their correctness requirements differ. A backend must implement one contract explicitly; a generic CRUD adapter is not sufficient.

The default installation requires no external service:

- SQLite stores task state, leases, ownership, attempts, and result metadata.
- The local filesystem stores immutable payload and result objects.
- In-memory implementations exist for tests and explicitly ephemeral development.

Optional production backends are PostgreSQL for shared task state and S3-compatible storage for shared objects. Couchbase is not a supported or planned core backend. Additional backends belong in separately versioned adapters and must pass the common conformance suite.

## Why Two Interfaces

```text
┌────────────────────────────────┐
│ TaskStateStore                 │
│ atomic transitions and leases │
│ memory · SQLite · PostgreSQL   │
└────────────────────────────────┘

┌────────────────────────────────┐
│ ObjectStore                    │
│ immutable bytes and checksums  │
│ memory · filesystem · S3       │
└────────────────────────────────┘
```

S3 cannot atomically claim a queued task or fence a lease. An in-memory queue cannot provide durable, shared object retention. Pretending both are one interchangeable store would hide materially different guarantees.

## `TaskStateStore`

The Go contract is behavioral; exact Go types may evolve without weakening it:

```go
type TaskStateStore interface {
    Create(ctx context.Context, task TaskRecord) error
    Claim(ctx context.Context, workerID string, ttl time.Duration) (*TaskRecord, error)
    Renew(ctx context.Context, taskID TaskID, leaseID LeaseID, ttl time.Duration) error
    Complete(ctx context.Context, taskID TaskID, leaseID LeaseID, result ObjectRef) error
    Fail(ctx context.Context, taskID TaskID, leaseID LeaseID, failure Failure) error
    RequeueExpired(ctx context.Context, now time.Time, limit int) ([]TaskID, error)
    Cancel(ctx context.Context, taskID TaskID) (bool, error)
    Get(ctx context.Context, taskID TaskID) (*TaskRecord, error)
    ListResults(ctx context.Context, ownerID OwnerID, after Cursor, limit int) ([]ResultRecord, Cursor, error)
    AcknowledgeResult(ctx context.Context, taskID TaskID, ownerID OwnerID) error
    Close() error
}
```

Required properties:

- `Create` is idempotent by `task_id`; conflicting content returns an error.
- `Claim` atomically selects one eligible task and creates a unique fencing `lease_id`.
- `Renew`, `Complete`, and `Fail` compare the active lease ID; stale workers cannot mutate a newer attempt.
- `Complete` atomically records the result reference and terminal state.
- `Cancel` succeeds only from an eligible queued state.
- Result listing is resumable by cursor and isolated by submitting Runtime owner ID.
- Implementations define a transaction isolation strategy and pass race/conformance tests.

Implementations:

| Type | Use | Guarantee |
|---|---|---|
| `memory` | Unit tests and disposable development | Lost on agent exit; single process |
| `sqlite` | Default single-node production | Durable local transactions; SQLite WAL mode |
| `postgres` | Optional shared/multi-node state | Durable shared transactions; row locking or `SKIP LOCKED` claims |

PostgreSQL is optional, not required for clustering in the first implementation. Phase 5 may continue using origin ownership with per-node SQLite. A later shared-state scheduler can use PostgreSQL after its failure semantics are tested independently.

## `ObjectStore`

```go
type ObjectStore interface {
    Put(ctx context.Context, key ObjectKey, body io.Reader, meta ObjectMetadata) (ObjectRef, error)
    Get(ctx context.Context, ref ObjectRef) (io.ReadCloser, ObjectMetadata, error)
    Stat(ctx context.Context, ref ObjectRef) (ObjectMetadata, error)
    Delete(ctx context.Context, ref ObjectRef) error
    Close() error
}
```

`Put` is idempotent for a content-addressed key. Every reference contains:

```text
store name · immutable key · byte size · SHA-256 checksum · media/codec type
```

Implementations:

| Type | Use | Guarantee |
|---|---|---|
| `memory` | Tests and small ephemeral examples | Lost on exit; bounded by configured memory limit |
| `filesystem` | Default single-node production | Atomic rename after write/fsync; local to one agent |
| `s3` | Optional shared payloads/results | Durable shared objects; checksum verified; S3-compatible APIs |

Small values may remain inline in the task-state record. Values above `inline_threshold_bytes` are written to the object store. Explicit `ObjectRef` arguments are never copied inline.

## Task Identity and Serialization

Production tasks are referenced by registered name and version:

```python
@task(name="reports.generate", version="v3", idempotent=True)
def generate_report(input_ref):
    ...
```

The task envelope contains `task_name`, `task_version`, serialized small arguments or an `input_ref`, labels, and ownership metadata. Workers reject unknown names or versions as terminal deployment errors.

Inline cloudpickled functions are an explicit development compatibility mode, disabled by default in production configuration. Arguments/results may still use cloudpickle when configured, so the local socket and object store remain trusted-code boundaries.

## Agent-Relayed Results

Workers never connect to the submitting application. They store the result object through their local agent and send COMPLETE with its `ObjectRef`. The state transition is fenced by `lease_id`.

```text
Runtime ⇄ origin agent ⇄ worker
                    │
                    ├── TaskStateStore
                    └── ObjectStore
```

For a remote worker:

```text
Runtime ⇄ origin agent ⇄ remote agent ⇄ worker
```

The remote agent returns the result reference and terminal state to the origin. If the configured object store is local, agents proxy or copy immutable objects as part of forwarding; if it is shared S3, the same reference is usable by both. Workers do not receive application callback addresses.

The Runtime reads RESULT notifications over its existing local agent connection. On reconnect it resumes using `owner_id` plus a result cursor. Result records remain until acknowledged or until the configured retention policy expires.

## Ordering and Failure Recovery

Submission:

```text
serialize → store object (if externalized) → create task state → SUBMIT ACK
```

Completion:

```text
store result object → fenced terminal state update → notify Runtime → Runtime ACK
```

Task state and object storage do not use a distributed transaction. Recovery is reconciliation-based:

- Object written but state creation failed: orphan collector deletes it after a grace period.
- Result written but completion failed: worker retries the idempotent fenced completion.
- State references a missing/corrupt object: terminal `StorageConsistencyError`, metric, and operator alert.
- Runtime disconnects after completion: durable result record is delivered after reconnect until acknowledged or expired.
- Stale worker completes after lease expiry: fencing rejects the old lease without changing state.

Garbage collection must never delete inputs referenced by queued, leased, or retryable tasks. Deletion is idempotent, bounded per pass, observable, and delayed by a safety grace period.

## Configuration

The canonical shape is represented in `taskwire.example.yaml`:

```yaml
storage:
  state:
    type: "sqlite"
    sqlite:
      path: "/var/lib/taskwire/state.db"

  objects:
    default: "local"
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    stores:
      local:
        type: "filesystem"
        path: "/var/lib/taskwire/objects"
```

Credentials are loaded through environment/file references or platform credential providers. Plaintext database passwords and cloud secrets do not belong in normal YAML.

## Backend Conformance Gate

Every implementation runs the same suite for idempotent creation, concurrent claim exclusivity, lease fencing, expiry/requeue, cancellation races, result cursor replay, object checksum validation, partial-write cleanup, retention, and shutdown/reopen behavior. A backend is not supported merely because it satisfies the Go interface at compile time.
