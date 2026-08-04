# Phase 2 — Go Agent Core (Single Node)

## Goal

Implement the language-neutral single-node agent around the behavioral `TaskStateStore` and `ObjectStore` contracts. SQLite and the local filesystem are the production defaults; memory implementations exist for tests and explicitly ephemeral development. Python is the first worker implementation, but storage, claiming, and process supervision must not encode Python calling conventions.

## Phase 0 Baseline

Extend the existing `agent/cmd/taskwire-agent` command and `AgentHarness`; do not introduce a second daemon entry point or test-only process wrapper. Replace `agent/internal/controlserver` with the production gRPC service implementation while preserving `taskwire-agent version`, `taskwire-agent --config PATH` during the pre-release phases, deterministic binary lookup, SIGTERM cleanup, and the harness-owned directory layout (`agent.sock`, `agent.log`, `state/`, and `objects/`). The service-style `run --config` command can be added compatibly in Phase 8.

Phase 1 migrates `AgentHarness.status()` to the `Status` RPC; `worker_pids()` remains a Phase 3 placeholder. This phase replaces the readiness adapter with the production service while keeping the harness API, lifecycle diagnostics, and seeded `ChaosTimeline` intact. Add fault scenarios to the existing `integration`, `chaos`, and `resource` pytest markers rather than creating parallel harness conventions. Every change must continue to pass the Phase 0 version, discovery, lifecycle, and clean-wheel tests.

## Testable Outcome

A generated gRPC client can submit, receive a `SubmitResponse` at the configured durability boundary, claim with a fenced lease, renew, complete with an immutable result reference, replay/acknowledge results by owner and cursor, cancel queued work, inspect status, and recover correctly after restart.

## Storage Interfaces

Implement these Go interfaces; backend-specific types must not leak into callers:

```go
type TaskStateStore interface {
    Create(ctx context.Context, task TaskRecord) error
    Claim(ctx context.Context, worker WorkerCapabilities, ttl time.Duration) (*TaskRecord, error)
    Renew(ctx context.Context, taskID TaskID, leaseID LeaseID, ttl time.Duration) error
    Complete(ctx context.Context, taskID TaskID, leaseID LeaseID, result ObjectRef) error
    Fail(ctx context.Context, taskID TaskID, leaseID LeaseID, failure Failure) error
    RequeueExpired(ctx context.Context, now time.Time, limit int) ([]TaskID, error)
    Cancel(ctx context.Context, taskID TaskID, ownerID OwnerID) (bool, error)
    // CancelAdmin is added by Phase 12 for privileged operator cancel by task
    // ID only. Phase 2 implementations may stub it as unsupported until Phase
    // 12; Runtime IPC must never call it. See phase-12-admin-console.md.
    // CancelAdmin(ctx context.Context, taskID TaskID) (bool, error)
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

`Create` is content-idempotent by task ID. `Claim` is exclusive, creates the fencing lease, and considers only tasks compatible with the registered worker's exact name/version, invocation profile, and codec before applying label routing. `Complete`/`Fail` atomically write terminal state and a monotonically increasing per-owner result cursor. `Cancel` succeeds only for a queued record owned by the caller and also writes a terminal result. Phase 12 adds privileged `CancelAdmin(taskID)` for the admin console—atomic queued-only cancel without `ownerID`; Runtime IPC must continue to use owner-scoped `Cancel` only. `ListResults` is ordered by cursor and owner-isolated. Acknowledgement is idempotent and cannot acknowledge another owner or mismatched cursor. `PurgeResults` removes only acknowledged or retention-expired terminal records and returns object references that may have become unreferenced. Object references are immutable; all reads validate size and SHA-256.

The v0.1 backend registry contains `memory` and `sqlite` for state, and `memory` and `filesystem` for objects. PostgreSQL/S3 remain separately gated future adapters implemented against these same interfaces in Phase 6; configuring an unregistered backend fails startup with `unsupported_backend`.

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
- gRPC owns per-stream framing and flow control; handlers never write raw bytes.
- Enforce role/owner permissions from Phase 1 request metadata via the protocol interceptors; gRPC correlates responses.
- Require the first `Work` message to register the worker and its complete
  capability generation; accept later `update_tasks` snapshots only on that
  stream, remove capabilities on disconnect, and fence stale generations.
- Permit only one primary result-notification connection per owner; replacement is atomic and never drops persisted results.
- Return semantic failures as gRPC statuses with Taskwire error details; a bad
  stream message ends only that stream.
- Compare the authenticated Runtime owner with every owner-scoped request.
  Disconnect never removes task or result state.
- Bound RPCs, streams, object transfers, and shutdown waits with deadlines.

### Request semantics

- `Submit`: validate identity and envelope; store referenced/large input first; idempotently `Create` by task ID; respond only after the selected backend durability boundary. Same ID plus same content returns the same response; same ID plus different content is `task_conflict`.
- `Work` registration: validate the initial worker identity, runtime, codecs,
  and complete task set. Later `update_tasks` messages atomically replace the
  stream's capability generation. Capability state is not persisted as task
  state.
- `PullRequest` on `Work`: atomically `Claim` using the stream's registered capabilities and configured pool labels; send nothing when no compatible eligible task exists; otherwise send `LeasedTask` with lease ID, TTL, and attempt.
- `HeartbeatRequest`: renew only the matching active lease. Unknown/stale leases return a typed error.
- `Completion`: validate the referenced result through `ObjectStore.Stat`, then atomically record terminal state/result record under the active lease. Only after commit may the agent publish it to `WatchResults`.
- `WatchResults`: call `ListResults(owner, after, limit)` and stream ordered notifications. Never expose another owner's records.
- `QueryTasks`: return owner-isolated snapshots in request order; unknown and wrong-owner IDs are indistinguishable.
- `AckResult`: atomically acknowledge the matching owner/task/cursor; duplicates are idempotent.
- `Cancel`: conditional queued → cancelled transition. Leased, remote, terminal, or unknown tasks return `too_late`; a successful cancellation creates a terminal result record so the Future resolves.
- `Status`: local-socket-only snapshot with counts by state, active leases, worker PIDs/restarts, storage health, and cluster members.

Connection queues, active transfers, object bytes, notifier batches, and query batches are bounded by the Phase 1 configuration. When a connection's serialized write queue reaches its bound, close it with `transfer_limit` or `shutdown` as appropriate; never allow a slow Runtime to block state commits, lease renewal, or another connection.

### Lease reaper

Poll using a monotonic scheduling loop while comparing persisted wall-clock expiries. `RequeueExpired(now, limit)` is transactional. Tasks below `max_attempts` become eligible again; exhausted tasks become dead-lettered with a terminal failure/result record. Reaper batches are bounded and repeat until no expired rows remain.

### Retention and object garbage collection

A separate bounded sweeper applies `storage.objects.result_retention_seconds`. Acknowledgement permits cleanup but does not require immediate deletion; expiry permits cleanup even without acknowledgement. Purging state and deleting objects are intentionally not one transaction: first remove/mark the state reference, then delete only when a state-store reference check proves no live task, result, transfer, or outbox row uses the object. Delete is idempotent. Failures retry with metrics/logging, and a missing referenced object is a `StorageConsistencyError`, never silently treated as an empty result.

### Worker manager

Spawn every configured worker pool using its argv directly, without a shell, and pass the agent socket, config path, worker ID, and pool name through reserved environment variables. The manager treats Python, Node.js, and Go commands uniformly; it does not import modules or inspect task code. Track PID, pool, runtime, exit status, and restart count. Apply bounded exponential restart backoff and a per-pool restart-rate circuit breaker so a failing runtime cannot cause a fork storm or suppress healthy pools. An empty pool list and a pool count of zero are valid.

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
- Disconnect before the `Submit` response never produces a false durability claim.
- Runtime disconnect before `AckResult` followed by reconnect replays the record.
- Malformed/oversized messages are rejected by gRPC without crashing the agent.
- Concurrent RPCs receive matching responses even when handlers finish out of order; a slow result consumer cannot stall workers or other owners.
- Role-metadata violations, owner replacement, `QueryTasks` non-disclosure, and every owner-scoped operation are tested.
- Registration-before-PULL, stale/conflicting capability generations, disconnect cleanup, and concurrent Python/Node.js/Go synthetic capability sets prove incompatible tasks are never claimed and label routing occurs only after compatibility filtering.
- Graceful shutdown rejects new work and leaves no worker or socket leaks.
- Kill/restart with SQLite loses no acknowledged task; the explicit memory backend test demonstrates and documents that restart may lose acknowledged state.
- At scenario end the agent is alive unless deliberately killed, has no panic, has no orphan workers/socket, and reports conservation: submitted equals terminal plus queued/leased work.

Plant failpoints for state-create-before-response, object-put-before-state-create, terminal-commit-before-notify, result-notify-before-ack, and lease-expiry races.

## Implementation Order

1. Memory state/object stores and shared conformance suites.
2. SQLite state store and filesystem object store.
3. Lease reaper, status snapshot, and worker manager.
4. IPC server request lifecycle and replayable result notifier.
5. Replace the Phase 0 stub server/status probe in place and add crash, race, malformed-input, and graceful-shutdown scenarios to the existing harness.
6. Run the root `unit`, `integration`, and `smoke-wheel` targets so storage/runtime additions are tested through the packaged agent path.

## Exit Gate

Phase 2 is complete when SQLite/filesystem recovery and conformance tests pass, response/lease/result ordering is demonstrated by failpoint tests, cancellation survives reopen, result replay works after disconnect, and no in-memory queue, Bolt store, WAL config, callback address, or direct worker-to-app result path remains.

---

## Implementation Guide

> **Status:** **Next phase to implement.** Today `taskwire-agent` still serves only
> the `Status` RPC via `agent/internal/controlserver`. Replace that with real
> stores + scheduling without changing the Phase 1 service or config.

### Success definition (one sentence)

A generated gRPC client can **Submit → response → Pull/LeasedTask → Heartbeat → Completion → WatchResults → AckResult**, and after Runtime reconnect **WatchResults** still delivers the same cursor-ordered outcomes against **SQLite + filesystem**.

### Package layout to create

```text
agent/internal/state/
  types.go          # TaskID, LeaseID, OwnerID, Cursor, TaskRecord, ResultRecord, Failure, WorkerCapabilities
  store.go          # TaskStateStore interface (exact methods in Storage Interfaces above)
  memory.go
  sqlite.go
  schema.sql        # or embedded schema string
  memory_test.go    # conformance entry
  sqlite_test.go
  conformance_test.go  # shared suite: run against memory + sqlite

agent/internal/object/
  types.go          # ObjectKey, ObjectMetadata, ObjectRef (or re-export protocol types)
  store.go          # ObjectStore interface
  memory.go
  filesystem.go
  conformance_test.go

agent/internal/lease/reaper.go
agent/internal/worker/manager.go
agent/internal/status/status.go
agent/internal/faults/faults.go   # optional failpoints behind env flag
agent/internal/ipc/server.go      # production gRPC service implementation
agent/cmd/taskwire-agent/main.go  # wire components; swap controlserver for the run path

# delete or gut when IPC is ready:
agent/internal/controlserver/     # keep only if tests still need a minimal Status-only fake
```

### Step 1 — Domain types + memory stores (TDD)

Start with types and in-memory backends so conformance is pure Go unit tests.

```go
// agent/internal/state/types.go
package state

import (
	"time"

	"github.com/sanketn26/taskwire/agent/pkg/protocol" // or local copies of wire types
)

type TaskID [16]byte
type LeaseID [16]byte
type OwnerID [16]byte
type Cursor uint64

type TaskState string

const (
	StateQueued       TaskState = "queued"
	StateLeased       TaskState = "leased"
	StateSucceeded    TaskState = "succeeded"
	StateFailed       TaskState = "failed"
	StateCancelled    TaskState = "cancelled"
	StateDeadLettered TaskState = "dead_lettered"
)

type WorkerCapabilities struct {
	WorkerID             string
	Runtime              string // python | nodejs | go
	Codecs               []string
	CapabilityGeneration uint64
	// Exact identities this Work stream registered:
	Tasks []TaskCapability
	// Pool labels from config (applied after capability filter):
	Labels map[string]string
}

type TaskCapability struct {
	Name, Version, Invocation string
	Codecs                    []string
}

type TaskRecord struct {
	ID           TaskID
	OwnerID      OwnerID
	Name         string
	Version      string
	Invocation   string
	InputCodec   string
	InputInline  []byte // optional; prefer ObjectRef for large
	InputObject  *ObjectRef
	Labels       map[string]string
	Idempotent   bool
	State        TaskState
	Attempt      uint32
	LeaseID      *LeaseID
	LeaseExpiry  *time.Time
	Result       *ObjectRef
	Failure      *Failure
	ResultCursor *Cursor
	AckedAt      *time.Time
	CreatedAt    time.Time
	UpdatedAt    time.Time
	// envelope bytes or fields enough to rebuild LeasedTask
}

type ResultRecord struct {
	TaskID  TaskID
	OwnerID OwnerID
	Cursor  Cursor
	State   TaskState // succeeded | failed | cancelled (terminal for Runtime)
	Result  *ObjectRef
	Failure *Failure
}

type Failure struct {
	Code      string
	Message   string
	Details   []byte // optional ValueRef bytes; keep bounded
	Retryable bool
}

type ObjectRef struct {
	Store  string
	Key    string
	Size   uint64
	SHA256 [32]byte
	Codec  string
}
```

```go
// agent/internal/state/store.go — paste interface from "Storage Interfaces" above exactly.
// Do not add methods that leak SQLite types.
```

**Conformance cases (must pass for every backend):**

```go
// agent/internal/state/conformance_test.go
func runStateConformance(t *testing.T, open func(t *testing.T) TaskStateStore) {
	t.Run("create_idempotent_same_content", ...)
	t.Run("create_conflict_different_content", ...)
	t.Run("claim_exclusive_under_parallel", ...) // use -race and many goroutines
	t.Run("claim_filters_by_name_version_invocation_codec", ...)
	t.Run("renew_complete_fail_fence_on_lease", ...)
	t.Run("stale_lease_complete_no_state_change", ...)
	t.Run("requeue_expired_increments_attempt", ...)
	t.Run("max_attempts_dead_letter_terminal_result", ...)
	t.Run("cancel_queued_ok_leased_too_late", ...)
	t.Run("list_results_owner_isolated_cursor_order", ...)
	t.Run("ack_idempotent_wrong_owner_rejected", ...)
	t.Run("reopen_preserves_queued_and_terminal", ...) // memory: document loss if ephemeral
}
```

Object store conformance:

```go
func runObjectConformance(t *testing.T, open func(t *testing.T) ObjectStore) {
	t.Run("put_get_stat_roundtrip_checksum", ...)
	t.Run("get_checksum_mismatch_error", ...)
	t.Run("put_idempotent_same_key", ...)
	t.Run("partial_write_cleaned", ...)
	t.Run("path_traversal_rejected", ...) // filesystem only assertions
}
```

### Step 2 — SQLite state store

```go
// agent/internal/state/sqlite.go (sketch)
// PRAGMA journal_mode=WAL;
// PRAGMA foreign_keys=ON;
// PRAGMA busy_timeout=<cfg>;
// PRAGMA synchronous=FULL|NORMAL from config;

// Claim must be ONE transaction:
//  1. SELECT eligible row WHERE state='queued' AND capability match
//     ORDER BY created_at LIMIT 1
//  2. UPDATE state='leased', attempt=attempt+1, lease_id=?, lease_expiry=?
//  3. COMMIT → return record
// No separate in-memory queue.

// Complete/Fail:
//  UPDATE ... WHERE id=? AND lease_id=? AND state='leased'
//  INSERT result_record with next cursor for owner
//  If rows affected == 0 → stale_lease
```

Schema sketch:

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE tasks (
  id BLOB PRIMARY KEY,              -- 16 bytes
  owner_id BLOB NOT NULL,
  task_name TEXT NOT NULL,
  task_version TEXT NOT NULL,
  invocation TEXT NOT NULL,
  input_codec TEXT NOT NULL,
  input_inline BLOB,
  input_store TEXT,
  input_key TEXT,
  input_size INTEGER,
  input_sha256 BLOB,
  labels_json TEXT NOT NULL,
  idempotent INTEGER NOT NULL,
  state TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_id BLOB,
  lease_expiry_ms INTEGER,
  result_store TEXT,
  result_key TEXT,
  result_size INTEGER,
  result_sha256 BLOB,
  result_codec TEXT,
  failure_code TEXT,
  failure_message TEXT,
  failure_retryable INTEGER,
  result_cursor INTEGER,
  acked_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE INDEX idx_tasks_claim ON tasks(state, created_at_ms);
CREATE INDEX idx_tasks_lease_expiry ON tasks(state, lease_expiry_ms);

CREATE TABLE results (
  owner_id BLOB NOT NULL,
  cursor INTEGER NOT NULL,
  task_id BLOB NOT NULL,
  state TEXT NOT NULL,
  -- result / failure columns ...
  PRIMARY KEY (owner_id, cursor)
);

CREATE TABLE owner_cursors (
  owner_id BLOB PRIMARY KEY,
  next_cursor INTEGER NOT NULL  -- last assigned; increment on terminal write
);
```

### Step 3 — Filesystem object store

```go
// Put path:
//  root/<key> must stay under root (reject ..)
//  write to root/.tmp/<uuid>
//  stream + sha256 + size
//  fsync file
//  rename to final
//  fsync parent dir
// Get: open final, re-hash or trust recorded meta with size check; mismatch → error
```

```go
func (s *FilesystemStore) Put(ctx context.Context, key ObjectKey, body io.Reader, meta ObjectMetadata) (ObjectRef, error) {
	final := s.safeJoin(key)
	tmp := filepath.Join(s.root, ".tmp", uuidNew())
	// write, checksum, fsync, rename, fsync dir
	return ObjectRef{Store: s.name, Key: string(key), Size: meta.Size, SHA256: sum, Codec: meta.Codec}, nil
}
```

### Step 4 — Lease reaper + retention sweeper

```go
// agent/internal/lease/reaper.go
for {
	select {
	case <-ctx.Done():
		return
	case <-ticker.C:
		for {
			ids, err := store.RequeueExpired(ctx, time.Now(), batchLimit)
			if err != nil || len(ids) == 0 {
				break
			}
		}
	}
}
// RequeueExpired: leased && lease_expiry < now
//   if attempt < max_attempts → queued, clear lease
//   else → dead_lettered + terminal result (failure max_attempts_exceeded)
```

Retention: `PurgeResults` then delete unreferenced objects (state check first, then object delete). Not one distributed transaction—document the order.

### Step 5 — Worker manager (no task execution yet)

```go
// agent/internal/worker/manager.go
// For each pool with count > 0:
//   cmd := exec.Command(pool.Command[0], pool.Command[1:]...)
//   cmd.Dir = pool.WorkingDirectory
//   cmd.Env = merge(os.Environ(), pool.Environment, map[string]string{
//     "TASKWIRE_SOCKET": cfg.Socket,
//     "TASKWIRE_CONFIG": configPath,
//     "TASKWIRE_WORKER_ID": workerID,
//     "TASKWIRE_POOL": pool.Name,
//   })
// Restart with exponential backoff; circuit breaker per pool.
// Phase 3 workers will use these env vars.
```

In Phase 2 you may leave `workers.pools: []` in tests; still implement manager so empty pools are a no-op.

### Step 6 — IPC server (heart of Phase 2)

Replace controlserver's handlers with production ones using Phase 1 `pkg/protocol`.

```go
// agent/internal/ipc/server.go (structure)
type Server struct {
	cfg      *config.Config
	state    state.TaskStateStore
	objects  object.ObjectStore
	// owner primary connection map, worker capability registry (connection-scoped)
	// write queues per conn
}

func (s *Server) Serve(ctx context.Context) error {
	// remove stale socket only if no live listener
	// listen unix, chmod 0660, optional chown group
	// accept loop
}

// grpc.NewServer with the protocol role interceptors; gRPC owns the
// accept loop, per-stream framing, and response correlation.
```

**Submit handler outline:**

```go
func (s *Server) Submit(ctx context.Context, env *pb.TaskEnvelope) (*pb.SubmitResponse, error) {
	// 1. caller, _ := protocol.CallerFrom(ctx); owner_id must match caller.OwnerID
	// 2. if input is large inline above threshold, reject or require ObjectRef
	//    (Runtime/Phase 4 uploads first; raw clients may PutObject first)
	// 3. taskID := deterministic ID for this envelope (must be 16-byte nonzero)
	// 4. record := TaskRecord{... State: queued}
	// 5. err := s.state.Create(ctx, record)
	//    - nil → &pb.SubmitResponse{TaskId: taskID}
	//    - conflict → protocol.StatusError(protocol.TaskConflict, ...)
	// 6. respond only after Create returned (durability boundary)
}
```

**Work stream (pull / complete):**

```go
func (s *Server) Work(stream pb.TaskwireControl_WorkServer) error {
	// first message must be WorkerRegistration; then loop on Recv:
	//
	// pull:     require registration generation match
	//           rec, err := s.state.Claim(ctx, caps, ttl)
	//           rec == nil → send nothing, the stream stays open
	//           otherwise  → stream.Send(&pb.AgentMessage{Task: LeasedTask})
	//
	// complete: Stat result object if present
	//           Complete or Fail under lease
	//           after commit: publish to the owner's WatchResults stream
	//
	// stream teardown → release this worker's capabilities and reap leases
}
```

**Result replay:**

```go
// WatchResults(owner, after_cursor) → ListResults(owner, after, limit)
//   → stream.Send(ResultNotification) per record, in cursor order
// AckResult → AcknowledgeResult(task, owner, cursor)
```

**Primary owner stream:**

```go
// Keep this registry inside the result-stream service and protect it with a
// mutex. Opening a stream atomically replaces and cancels the prior stream for
// that owner. Persisted results remain the source of truth.
```

### Step 7 — Wire `main.go`

```go
func main() {
	// version | --config
	cfg, err := config.Load(configPath)
	// open state backend by cfg.Storage.State.Type
	// open object stores by cfg.Storage.Objects.Stores
	// start reaper, sweeper, worker manager
	// start ipc.Server on cfg.Socket
	// SIGTERM → shutdown order from phase doc
}
```

### Step 8 — Harness / integration tests

Add (or extend) Python integration tests that use the generated gRPC client without the SDK:

```python
# python/tests/integration/test_agent_core_e2e.py
def test_submit_claim_complete_result_replay(harness):
    # ControlClient.admin(socket) → Status ready
    # ControlClient.runtime(socket, owner_id)
    # PutObject for the result path as needed
    # Submit(TaskEnvelope)
    # ControlClient.worker(socket): Work stream → register, pull
    # send Completion with ObjectRef
    # expect a WatchResults notification, then AckResult
    # reopen WatchResults → empty or already-acked
```

Keep Phase 0 lifecycle tests green. `AgentHarness.status()` already uses the `Status` RPC; ensure production server sets `ready=true` only when stores open and listener is up.

### Step 9 — Failpoints (recommended)

```go
// agent/internal/faults/faults.go
// TASKWIRE_FAULT=state-create-before-response,object-put-before-create,...
func MaybeInject(name string) error
```

Plant in SUBMIT/COMPLETE/notify paths; chaos tests flip them with `TASKWIRE_CHAOS_SEED`.

### Implementation order (execute in sequence)

1. Types + memory state/object + conformance suite green  
2. SQLite + filesystem + reopen/race tests (`go test -race`)  
3. Reaper + retention  
4. Worker manager (empty pools OK)  
5. IPC server message handlers  
6. Switch `main.go` to the production service  
7. Python raw-client E2E + crash/restart conservation  
8. `make unit integration smoke-wheel`

### Commands before review

```bash
make format lint
cd agent && go test ./internal/state/... ./internal/object/... ./internal/ipc/... -race -count=1
make unit integration smoke-wheel
```

### Done checklist

- [ ] Memory + SQLite pass shared state conformance  
- [ ] Memory + filesystem pass object conformance  
- [ ] `Submit` responds only after durable Create  
- [ ] Claim exclusive; stale_lease on late COMPLETE  
- [ ] RESULT only after terminal commit; cursor ordered; owner isolated  
- [ ] Cancel queued only; leased → `too_late`  
- [ ] Restart with SQLite: no loss of ACKed submits / terminal results  
- [ ] Memory backend restart loss is documented/tested as expected  
- [ ] No in-memory queue divergent from SQLite  
- [ ] No callback_addr / worker→app RESULT  
- [ ] Shutdown: no orphan workers/socket  
- [ ] Phase 0/1 tests still green  

### Review request template

```text
Please review Phase 2.
Branch: phase-2-...
Implemented: memory+sqlite state, fs objects, ipc server, reaper, worker manager
Commands:
  make format lint unit integration smoke-wheel
  go test ./internal/state/... ./internal/object/... ./internal/ipc/... -race
Known gaps: <e.g. failpoints deferred>
```
