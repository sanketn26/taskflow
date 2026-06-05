# Phase 2 — Go Sidecar Core (Single Node)

## Goal

A working single-node `taskflow-agent` binary.
Accepts SUBMIT over Unix socket, queues tasks, serves PULL to workers, manages leases with heartbeat + TTL expiry, re-queues tasks on lease expiry. No Python workers yet — verified with a Go test client.

## Testable Outcome

- `taskflow-agent` starts, creates Unix socket, accepts connections
- Go test client submits a task → task enters queue
- Go test client pulls task → receives task payload + lease_id
- Go test client heartbeats → lease TTL resets
- Go test client stops heartbeating → after TTL, task is back in queue and pullable again
- Go test client pulls + releases → lease removed, task gone from queue
- Worker manager spawns N processes configured in YAML

---

## Files

```
agent/cmd/taskflow-agent/main.go
agent/internal/ipc/server.go
agent/internal/queue/queue.go
agent/internal/queue/lease.go
agent/internal/worker/manager.go
agent/internal/queue/queue_test.go
agent/internal/queue/lease_test.go
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
| `Start() error` | Remove stale socket file if exists (`os.Remove`). `net.Listen("unix", socketPath)`. Store listener. Start `accept()` goroutine. |
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
| `spawn() (*workerProcess, error)` | `exec.Command("python3", "-m", "taskflow.worker.runner", socketPath)`. Set `Stdout`/`Stderr` to os.Stdout/Stderr for visible logs. `cmd.Start()`. Store in `workers`. |
| `monitor` (private goroutine) | For each worker: `go watchWorker(wp)`. `watchWorker` calls `wp.cmd.Wait()` blocking until exit. On unexpected exit (and `stopCh` not closed), calls `spawn()` to replace. Rate-limit restarts: if worker lived < 1s, sleep 5s before respawn to prevent tight crash loop. |

---

### `agent/cmd/taskflow-agent/main.go`

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

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Interface Segregation | `Queuer`, `Leaser` | Small interfaces; test doubles are trivial to write; DIP in Server |
| Dependency Injection | `Server` takes `Queuer` and `Leaser` | Swap real implementations for mocks in tests |
| Goroutine Ownership | `LeaseManager.expire`, `Manager.monitor` | Each goroutine has a clear owner and a clear shutdown signal |
| Single Responsibility | `handleSubmit`, `handlePull`, `handleHeartbeat` | Each handles exactly one message type |
