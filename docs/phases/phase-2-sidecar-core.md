# Phase 2 — Go Sidecar Core (Single Node)

## Goal

A working single-node `taskwire-agent` binary.
Accepts SUBMIT over Unix socket, queues tasks, serves PULL to workers, manages leases with heartbeat + TTL expiry, re-queues tasks on lease expiry. No Python workers yet — verified with a Go test client.

## Testable Outcome

- `taskwire-agent` starts, creates Unix socket, accepts connections
- Go test client submits a task → task enters queue
- Go test client pulls task → receives task payload + lease_id
- Go test client heartbeats → lease TTL resets
- Go test client stops heartbeating → after TTL, task is back in queue and pullable again
- Go test client pulls + releases → lease removed, task gone from queue
- Worker manager spawns N processes configured in YAML
- With `queue.persistence: wal`: submit 5 tasks, `kill -9` the agent, restart → all 5 tasks still pullable
- Task that expires its lease `max_attempts` times lands in the dead-letter store, not back in the queue
- Socket file is created with mode `0660`

---

## Files

```
agent/cmd/taskwire-agent/main.go
agent/internal/ipc/server.go
agent/internal/queue/queue.go
agent/internal/queue/lease.go
agent/internal/queue/store.go          (TaskStore interface + NullStore + factory)
agent/internal/queue/wal.go            (BoltStore + DurableQueue — persistence: "wal")
agent/internal/queue/deadletter.go     (dead-letter store after max_attempts)
agent/internal/worker/manager.go
agent/internal/queue/queue_test.go
agent/internal/queue/lease_test.go
agent/internal/queue/wal_test.go
agent/internal/ipc/server_test.go
agent/go.mod
```

---

## Go

### `agent/internal/queue/queue.go`

#### `Queuer` interface

Small, focused interface (Interface Segregation). Only what callers need.

| Method | Signature | Description |
|--------|-----------|-------------|
| `Push` | `(task *Task) error` | Add task to back of queue |
| `Pull` | `() (*Task, bool)` | Remove and return task from front; `(nil, false)` if empty |
| `Len` | `() int` | Current queue depth |
| `Requeue` | `(task *Task) error` | Prepend task to front (priority re-queue after lease expiry); increments `task.Attempts` |

#### `Task` struct

| Field | Type | Description |
|-------|------|-------------|
| `ID` | `[16]byte` | UUID raw bytes, set by submitter |
| `Payload` | `[]byte` | cloudpickle-serialised `{func, args, kwargs, callback_addr, label}` |
| `CallbackAddr` | `string` | Where the worker sends the result directly |
| `Label` | `string` | Routing label from `@task(label=...)` |
| `Attempts` | `int` | Incremented on each re-queue; visible to scheduler for dead-letter logic (future) |
| `SubmittedAt` | `time.Time` | Wall time of original submission |

#### `InMemoryQueue` struct (implements `Queuer`)

| Field | Type | Description |
|-------|------|-------------|
| `mu` | `sync.Mutex` | Protects all field access |
| `items` | `[]*Task` | Ordered slice used as deque |

| Method | Responsibility |
|--------|----------------|
| `NewInMemoryQueue() *InMemoryQueue` | Allocate and return |
| `Push` | Lock, append to `items`, unlock |
| `Pull` | Lock, if empty return `(nil, false)`, else remove `items[0]` (shift), return `(task, true)`, unlock |
| `Len` | Lock, return `len(items)`, unlock |
| `Requeue` | Lock, increment `task.Attempts`, prepend to `items[0:0]`, unlock |

**Note:** `Pull` uses a slice shift. For Phase 2 correctness is the goal. Phase 7 hardening may replace with a ring buffer or `container/list` for O(1) head removal.

---

### `agent/internal/queue/store.go` + `wal.go` — durability (the honest at-least-once)

`InMemoryQueue` loses every queued and leased task when the agent restarts. That makes "at-least-once delivery" a half-truth: it only covers *worker* crashes. Durability closes the gap when `queue.persistence: "wal"` is set.

**The seam: ordering and existence are separate concerns.** `Queuer` answers "what runs next" (always in memory, always fast). A new, deliberately tiny `TaskStore` interface answers "what must survive a restart". The queue is *configurable* today and *pluggable* internally — new backends are one file and one registry entry — but storage is **not** an external plugin surface, and Redis/Postgres stores are explicitly out of scope: an external datastore as the durability layer reintroduces the broker this project exists to eliminate.

#### `TaskStore` interface (`store.go`)

| Method | Signature | Description |
|--------|-----------|-------------|
| `Put` | `(task *Task) error` | Persist (or update) a task record, fsync'd before return |
| `Delete` | `(taskID [16]byte) error` | Remove — called only on successful completion |
| `LoadAll` | `() ([]*Task, error)` | Recovery: every surviving task, for re-queueing at startup |
| `Close` | `() error` | Flush and release the backing file |

Implementations, selected by a factory from `queue.persistence`:

| Config value | Implementation | Notes |
|--------------|----------------|-------|
| `"none"` (default) | `NullStore` | All methods no-ops. The fast path: zero overhead, zero files. |
| `"wal"` | `BoltStore` | **bbolt** (`go.etcd.io/bbolt`): one file, one writer, crash-safe B-tree, zero operational surface — in the spirit of "no external infrastructure". A hand-rolled segment log is a Phase 8 optimisation at best. Bucket `tasks`: key `task_id` (16B) → msgpack-encoded Task (payload, callback_addr, label, attempts, submitted_at). |

Factory: `NewStore(cfg QueueConfig) (TaskStore, error)` — a `switch` on the string today; the registry stays internal (no `plugin` package, no dlopen). Future in-tree candidates that respect the zero-infra constraint: `sqlite`, `pebble`. Anything requiring a server process gets rejected at design review, not at runtime.

#### `DurableQueue` struct (implements `Queuer`, wraps `InMemoryQueue` + `TaskStore`)

Decorator over `InMemoryQueue`: the in-memory queue remains the source of *ordering*; the store is the source of *existence*. With `NullStore` it degenerates to the plain in-memory behaviour, so there is exactly one queue code path regardless of config.

| Method | Responsibility |
|--------|----------------|
| `Push` | `store.Put` first (write-ahead — a crash between the two re-delivers rather than loses), then in-memory `Push`. |
| `Pull` | In-memory `Pull` only — the store record stays. A pulled-but-unfinished task must survive a restart. |
| `Complete(taskID)` | Called by the lease `Release` flow: `store.Delete`. This is the only place a task leaves disk. |
| `Requeue` | In-memory requeue; `store.Put` to update `attempts` so dead-letter accounting survives restarts. |
| `Recover()` | At startup: `store.LoadAll()`, `Push` everything into the in-memory queue ordered by `submitted_at`. Tasks that were leased at crash time simply reappear as queued — correct under at-least-once. |

**Performance note:** one bbolt tx per submit caps throughput around a few thousand tasks/s on SSDs. Batch commits (group commits every 5ms or N submits, whichever first) recover most of it. Document the trade-off in the config reference; `persistence: "none"` remains the default and the fast path.

---

### `agent/internal/queue/deadletter.go`

Without this, a poison task (segfaults the worker, or always outlives its lease) re-queues forever and eats a worker slot for eternity.

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `Add` | `(task *Task, reason string)` | Store task + reason + timestamp. In-memory ring (cap 1000) when persistence off; bbolt `deadletter` bucket when on. |
| `List` | `() []DeadLetter` | For the `status` command (Phase 7) |

Wire-in point: `LeaseManager.expire()` — before calling `queue.Requeue`, check `task.Attempts >= cfg.Queue.MaxAttempts`; if so, `deadletter.Add(task, "max attempts exceeded")` instead, and log at WARN with the task_id.

---

### `agent/internal/queue/lease.go`

#### `Leaser` interface

| Method | Signature | Description |
|--------|-----------|-------------|
| `Grant` | `(task *Task, ttl time.Duration) ([16]byte, error)` | Record lease, return leaseID |
| `Heartbeat` | `(leaseID [16]byte) error` | Reset TTL for lease; return `ErrLeaseNotFound` if not present |
| `Release` | `(leaseID [16]byte) error` | Remove lease (task delivered successfully) |

#### Errors

| Sentinel | Description |
|----------|-------------|
| `ErrLeaseNotFound` | Heartbeat or Release called with unknown leaseID |

#### `lease` struct (unexported)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `[16]byte` | Random UUID |
| `task` | `*Task` | Pointer to the leased task |
| `expiresAt` | `time.Time` | Deadline for next heartbeat |

#### `LeaseManager` struct (implements `Leaser`)

| Field | Type | Description |
|-------|------|-------------|
| `mu` | `sync.Mutex` | Protects `leases` map |
| `leases` | `map[[16]byte]*lease` | Active leases |
| `queue` | `Queuer` | Used to re-queue expired tasks |
| `defaultTTL` | `time.Duration` | Applied on Grant and each Heartbeat |
| `stopCh` | `chan struct{}` | Signals `expire` goroutine to stop |

| Method | Responsibility |
|--------|----------------|
| `NewLeaseManager(q Queuer, ttl time.Duration) *LeaseManager` | Initialise, start `expire()` goroutine |
| `Grant` | Generate random 16-byte leaseID (`crypto/rand`), build `lease{id, task, now+TTL}`, store in map under lock, return leaseID |
| `Heartbeat` | Lock, look up lease, if missing return `ErrLeaseNotFound`, else set `expiresAt = now + defaultTTL`, unlock |
| `Release` | Lock, delete from map, unlock, return `ErrLeaseNotFound` if missing |
| `expire` (private goroutine) | `time.Ticker` every 1s. Under lock: scan `leases`, collect expired entries (now > expiresAt). Unlock, then call `queue.Requeue` for each. Re-lock, delete from map. Repeat until `stopCh` closed. |
| `Stop` | Close `stopCh`, wait for expire goroutine to exit |

**Pattern:** Goroutine ownership — `LeaseManager` owns the `expire` goroutine. `Stop()` is the only way to terminate it. No goroutine leaks.

---

### `agent/internal/ipc/server.go`

Depends on `Queuer` and `Leaser` interfaces — not concrete types. Dependency Inversion Principle.

#### `Server` struct

| Field | Type | Description |
|-------|------|-------------|
| `socketPath` | `string` | Unix domain socket path |
| `queue` | `Queuer` | Injected — push submitted tasks, pull for work |
| `leaser` | `Leaser` | Injected — grant/heartbeat/release leases |
| `listener` | `net.Listener` | Accepts incoming connections |
| `wg` | `sync.WaitGroup` | Tracks in-flight connections for graceful shutdown |

| Method | Responsibility |
|--------|----------------|
| `NewServer(socketPath string, q Queuer, l Leaser) *Server` | Store fields, do not connect yet |
| `Start() error` | Remove stale socket file if exists (`os.Remove`). `net.Listen("unix", socketPath)`. **Set permissions before accepting:** `os.Chmod(socketPath, 0o660)` and chown group to `cfg.SocketGroup` if it exists — anyone who can write this socket can execute arbitrary Python (cloudpickle), so world-writable is a local-privilege-escalation hole. Store listener. Start `accept()` goroutine. |
| `Stop() error` | Close listener (causes `accept()` to return error and exit). `wg.Wait()` for all active connections to finish. |
| `accept` (private goroutine) | Loop: `listener.Accept()`. On error, if listener is closed return. Otherwise log and continue. For each conn: `wg.Add(1)`, `go handleConn(conn)`. |
| `handleConn(conn net.Conn)` | `defer wg.Done()`, `defer conn.Close()`. Loop: `protocol.ReadFrame(conn)`. On `io.EOF` exit cleanly. Dispatch by `frame.Type`: SUBMIT → `handleSubmit`, PULL → `handlePull`, HEARTBEAT → `handleHeartbeat`. Unknown type → log and continue. |
| `handleSubmit(conn net.Conn, f *protocol.Frame)` | Deserialise payload (msgpack) to extract `{label, callback_addr}`. Build `Task{ID: f.TaskID, Payload: f.Payload, ...}`. Call `queue.Push`. Send ACK frame back on `conn`. |
| `handlePull(conn net.Conn, f *protocol.Frame)` | Call `queue.Pull()`. If empty: send ACK with empty payload (worker will retry after backoff). If task returned: call `leaser.Grant(task, defaultTTL)` to get leaseID. Build TASK frame payload (msgpack): `{task_payload, lease_id, ttl_ms, callback_addr}`. Send TASK frame on `conn`. |
| `handleHeartbeat(f *protocol.Frame)` | Deserialise payload to get `lease_id`. Call `leaser.Heartbeat(leaseID)`. No response needed (fire-and-forget). |

**Pattern:** Template Method in `handleConn` — the dispatch table is the skeleton, handlers are the steps. Each handler does one thing (SRP).

---

### `agent/internal/worker/manager.go`

#### `workerProcess` struct (unexported)

| Field | Type | Description |
|-------|------|-------------|
| `cmd` | `*exec.Cmd` | The running Python process |
| `pid` | `int` | OS PID, for logging |
| `startedAt` | `time.Time` | For metrics and restart rate limiting |

#### `Manager` struct

| Field | Type | Description |
|-------|------|-------------|
| `cfg` | `WorkerConfig` | Worker count and labels from config |
| `socketPath` | `string` | Passed to each worker as argv[1] |
| `workers` | `[]*workerProcess` | Tracked worker processes |
| `mu` | `sync.Mutex` | Protects `workers` slice |
| `stopCh` | `chan struct{}` | Signals monitor goroutine to stop |
| `wg` | `sync.WaitGroup` | Waits for monitor goroutine on Stop |

| Method | Responsibility |
|--------|----------------|
| `NewManager(cfg WorkerConfig, socketPath string) *Manager` | Initialise |
| `Start() error` | Spawn `cfg.Count` workers via `spawn()`. Start `monitor()` goroutine. |
| `Stop() error` | Close `stopCh`. Under lock, SIGTERM all `workers[i].cmd.Process`. `wg.Wait()`. |
| `spawn() (*workerProcess, error)` | `exec.Command("python3", "-m", "taskwire.worker.runner", socketPath)`. Set `Stdout`/`Stderr` to os.Stdout/Stderr for visible logs. `cmd.Start()`. Store in `workers`. |
| `monitor` (private goroutine) | For each worker: `go watchWorker(wp)`. `watchWorker` calls `wp.cmd.Wait()` blocking until exit. On unexpected exit (and `stopCh` not closed), calls `spawn()` to replace. Rate-limit restarts: if worker lived < 1s, sleep 5s before respawn to prevent tight crash loop. |

---

### `agent/cmd/taskwire-agent/main.go`

Wires everything together. No business logic here — only composition.

| Responsibility |
|----------------|
| Load config via `config.Load()` (or `config.FindConfigFile()`) |
| Create `InMemoryQueue` |
| Create `LeaseManager` with queue and TTL from config (default 30s) |
| Create `ipc.Server` with queue and lease manager |
| Create `worker.Manager` with worker config and socket path |
| Call `server.Start()` |
| Call `workerManager.Start()` |
| Block on `os.Signal` channel for SIGTERM/SIGINT |
| On signal: call `workerManager.Stop()`, then `server.Stop()`, then `leaseManager.Stop()` |

---

## Tests

### `agent/internal/queue/queue_test.go`

| Test | Asserts |
|------|---------|
| `TestPushPull_FIFO` | Push tasks A, B, C → Pull returns A, B, C in order |
| `TestPull_EmptyQueue` | Pull on empty queue returns `(nil, false)` |
| `TestRequeue_Prepends` | Push A, B. Pull A (removes it). Requeue A. Pull returns A (front of queue). |
| `TestRequeue_IncrementsAttempts` | Task.Attempts is 0 initially; after Requeue it is 1 |

### `agent/internal/queue/lease_test.go`

| Test | Asserts |
|------|---------|
| `TestGrant_ReturnsLeaseID` | `Grant` returns a non-zero 16-byte ID |
| `TestHeartbeat_ResetsExpiry` | Grant with 50ms TTL, heartbeat at 40ms, wait 80ms → task NOT re-queued (heartbeat extended it) |
| `TestExpiry_RequeuesTask` | Grant with 50ms TTL, no heartbeat, wait 150ms → task back in queue |
| `TestRelease_RemovesLease` | Grant then Release → lease map empty, task NOT re-queued after TTL |
| `TestHeartbeat_UnknownLease` | Returns `ErrLeaseNotFound` |

### `agent/internal/ipc/server_test.go`

Uses a real Unix socket. Test spins up Server, connects with a raw Go client.

| Test | Asserts |
|------|---------|
| `TestSubmit_TaskEntersQueue` | Client sends SUBMIT frame → queue.Len() == 1 |
| `TestPull_ReturnsTask` | SUBMIT then PULL → response is TASK frame with matching task_id |
| `TestPull_EmptyQueue` | PULL on empty queue → ACK with empty payload |
| `TestHeartbeat_Accepted` | SUBMIT, PULL (get leaseID), HEARTBEAT with leaseID → no error |
| `TestLeaseExpiry_RequeuesForNextPull` | SUBMIT, PULL (hold lease), wait > TTL without heartbeat, PULL again → same task returned |

### `agent/internal/queue/wal_test.go`

| Test | Asserts |
|------|---------|
| `TestRecover_AfterRestart` | Push 5 tasks to DurableQueue(BoltStore), close it, open a new one on the same dir → Len() == 5, FIFO order preserved |
| `TestComplete_RemovesFromDisk` | Push, Pull, Complete, restart → Len() == 0 |
| `TestPulledNotCompleted_SurvivesRestart` | Push, Pull (no Complete), restart → task is queued again |
| `TestDeadLetter_AfterMaxAttempts` | Task with Attempts == max → expire moves it to dead-letter, queue stays empty |

---

## Implementation Guide

Build order:

1. **`InMemoryQueue` + tests** — pure data structure, no I/O.
2. **`LeaseManager` + tests** — the expiry tests use real short TTLs (50–150ms); use `require.Eventually` rather than bare sleeps to keep them un-flaky.
3. **`Server`** — start with SUBMIT/PULL only against a fake `Queuer`; add HEARTBEAT once leases work.
4. **`TaskStore` + `DurableQueue`** — `NullStore` first (one-line methods, proves the decorator), then `BoltStore`. The decorator wraps the already-tested in-memory queue; its tests are mostly restart simulations (close + reopen, never mock the filesystem).
5. **`Manager`** — last, because it needs a Python interpreter on the test machine; gate its tests behind a build tag if CI lacks one.

Concurrency gotchas to design around (these are the bugs this phase will actually have):

- **Lock ordering in `expire()`**: the spec deliberately unlocks before calling `queue.Requeue` — `LeaseManager` and `Queuer` have separate mutexes, and calling one while holding the other invites deadlock the moment someone adds a reverse call. Keep that discipline; document it in a comment on the mutex fields.
- **PULL responses and connection writes**: a worker's connection handles both PULL responses and (future) ACKs. All writes to one `net.Conn` must go through a per-connection write mutex — two goroutines interleaving partial frames corrupts the stream irrecoverably.
- **`handlePull` when queue is empty**: returning an empty ACK and letting the worker back off (as specced) is simple and correct. Resist the temptation to hold the PULL open server-side (long-poll) in this phase — it complicates shutdown; consider it in Phase 7 if the 100ms worker backoff shows up in benchmarks.
- **Graceful shutdown ordering** in `main.go` matters: stop accepting (server), stop workers, *then* stop the lease manager — reversed, expiring leases re-queue tasks into a queue no one will drain, which is harmless in-memory but writes pointless WAL churn.
- **Respawn rate-limiting**: the `< 1s lifetime → sleep 5s` rule in `monitor` is load-bearing. A worker that crashes on import (bad deploy) without it forks-bombs the host.

| Pattern | Where | Why |
|---------|-------|-----|
| Interface Segregation | `Queuer`, `Leaser` | Small interfaces; test doubles are trivial to write; DIP in Server |
| Dependency Injection | `Server` takes `Queuer` and `Leaser` | Swap real implementations for mocks in tests |
| Goroutine Ownership | `LeaseManager.expire`, `Manager.monitor` | Each goroutine has a clear owner and a clear shutdown signal |
| Single Responsibility | `handleSubmit`, `handlePull`, `handleHeartbeat` | Each handles exactly one message type |
