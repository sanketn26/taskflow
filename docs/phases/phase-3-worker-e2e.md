# Phase 3 — Python Worker + Single Node E2E

## Goal

Python worker process that the sidecar spawns, pulls tasks, executes via `cloudpickle`, and delivers results directly to the callback address. Verified end-to-end using a raw-protocol Python test script — no SDK yet.

## Testable Outcome

- Sidecar spawns `N` Python worker processes on startup (from Phase 2 manager)
- Worker connects to sidecar socket, pulls task, executes `cloudpickle`'d function
- Worker delivers result directly to the callback address TCP listener
- Worker sends heartbeats during execution; sidecar lease stays alive
- Worker handles execution exceptions: delivers error frame with pickled exception
- Raw Python test script (no `@task`, no `Runtime`): submit SUBMIT frame → receive RESULT frame at a local TCP listener

---

## Files

```
sdk/taskflow/ipc/client.py
sdk/taskflow/ipc/result_server.py
sdk/taskflow/worker/runner.py
sdk/taskflow/protocol/frames.py        (Phase 1, complete)
sdk/taskflow/protocol/messages.py      (Phase 1, complete)
sdk/taskflow/exceptions.py             (Phase 1, complete)
sdk/tests/integration/test_worker_e2e.py
```

---

## Python

### `sdk/taskflow/ipc/client.py`

Used by both the worker (to talk to the sidecar) and the SDK Runtime (Phase 4). Responsible only for sending frames over a Unix socket.

#### `SidecarClient`

| Field | Type | Description |
|-------|------|-------------|
| `_socket_path` | `str` | Path to agent Unix socket |
| `_sock` | `socket.socket` | Connected AF_UNIX socket |
| `_lock` | `threading.Lock` | Serialises concurrent sends (Python 3.13 free-threaded safe) |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(socket_path: str)` | Store path, call `_connect()` |
| `_connect` | `() -> socket.socket` | `socket.socket(AF_UNIX, SOCK_STREAM)`, `.connect(socket_path)`. Raise `SidecarNotRunning` with a clear message ("Is taskflow-agent running?") on `FileNotFoundError` or `ConnectionRefusedError` |
| `submit` | `(task_id: bytes, payload: bytes) -> None` | Build `Frame(SUBMIT, task_id, 0, payload)`, call `_send` |
| `pull` | `() -> Frame` | Send `Frame(PULL, zero_id, 0, b"")`, call `_send`. Then call `FrameCodec.read_frame(self._sock)` and return the received frame. |
| `heartbeat` | `(lease_id: bytes) -> None` | Build `Frame(HEARTBEAT, zero_id, 0, msgpack.dumps({lease_id}))`, call `_send` |
| `_send` | `(frame: Frame) -> None` | Acquire `_lock`, `self._sock.sendall(FrameCodec.encode(frame))`, release. Thread-safe. |
| `close` | `() -> None` | `self._sock.close()` |

**Note:** `pull` is blocking — it sends PULL and waits for the TASK response on the same connection. This is fine because each worker has a dedicated connection to the sidecar. The sidecar handles each connection in its own goroutine.

---

### `sdk/taskflow/ipc/result_server.py`

Receives results sent directly by workers. Runs in the same Python process as the Runtime (Phase 4) or as a standalone listener in tests.

#### `ResultServer`

| Field | Type | Description |
|-------|------|-------------|
| `_on_result` | `Callable[[bytes, bytes, bool], None]` | Callback: `(task_id, result_bytes, is_error)` |
| `_server_sock` | `socket.socket` | Bound TCP socket |
| `_address` | `str` | `"host:port"` string |
| `_thread` | `threading.Thread` | Daemon thread running `_serve` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(advertise_addr: str \| None, on_result: Callable)` | Store callback. Create `socket.socket(AF_INET, SOCK_STREAM)`. `setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)`. Bind to `("0.0.0.0", 0)` — OS assigns port. Compute `_address` using `_resolve_ip()` + assigned port. `sock.listen(128)`. Start daemon thread. |
| `address` | `property -> str` | Return `_address` — the routable `"host:port"` passed to workers as callback_addr |
| `stop` | `() -> None` | Close `_server_sock` — causes `_serve` to exit on next `accept()` |
| `_serve` | `() -> None` | Loop: `conn, _ = _server_sock.accept()`. On `OSError` (socket closed) break. Spawn daemon thread: `threading.Thread(target=_handle_connection, args=(conn,))` |
| `_handle_connection` | `(conn: socket.socket) -> None` | `with conn:` read frame via `FrameCodec.read_frame(conn)`. Extract `is_error = bool(frame.flags & 0x01)`. Call `_on_result(frame.task_id, frame.payload, is_error)`. |
| `_resolve_ip` | `(advertise_addr: str \| None) -> str` | If `advertise_addr` given, return it. Else: open `socket.socket(AF_INET, SOCK_DGRAM)`, `connect(("8.8.8.8", 80))`, return `getsockname()[0]`. Does not send any traffic. |

**Python 3.13 note:** `_serve` spawns a thread per connection. With free-threaded Python, these threads run truly in parallel — no GIL contention between connection handlers.

---

### `sdk/taskflow/worker/runner.py`

The worker process. Entry point: `python -m taskflow.worker.runner <socket_path>`. Spawned by `agent/internal/worker/manager.go`.

#### `TaskEnvelope` (dataclass)

Represents a task received from the sidecar.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `bytes` | 16-byte UUID |
| `lease_id` | `bytes` | 16-byte lease ID from sidecar |
| `ttl_ms` | `int` | Lease TTL in milliseconds |
| `payload` | `bytes` | cloudpickle bytes of `{func, args, kwargs}` |
| `callback_addr` | `str` | `"host:port"` to deliver result to |
| `label` | `str \| None` | Task label (informational in worker) |

#### `WorkerRunner`

| Field | Type | Description |
|-------|------|-------------|
| `_client` | `SidecarClient` | Connection to local sidecar |
| `_stop_event` | `threading.Event` | Set to signal clean shutdown |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(socket_path: str)` | Create `SidecarClient(socket_path)`, create `_stop_event` |
| `run` | `() -> None` | Main loop until `_stop_event` set. Call `_pull_task()`. Start `_heartbeat_loop` in daemon Thread. Call `_execute(envelope)`. Stop heartbeat thread. Call `_deliver_result(...)`. Repeat. |
| `_pull_task` | `() -> TaskEnvelope` | Call `_client.pull()`. If ACK with empty payload (no work available), sleep for backoff (100ms), retry. If TASK frame, decode msgpack payload into `TaskEnvelope`. |
| `_execute` | `(envelope: TaskEnvelope) -> tuple[bytes, bool]` | `cloudpickle.loads(envelope.payload)` to get `{func, args, kwargs}`. Call `func(*args, **kwargs)`. On success: return `(cloudpickle.dumps(result), False)`. On any exception `e`: return `(cloudpickle.dumps(e), True)`. Never re-raise — exceptions are data here. |
| `_deliver_result` | `(task_id: bytes, result: bytes, error: bool, callback_addr: str) -> None` | Parse `callback_addr` into host:port. Open TCP socket, connect, send RESULT frame (`flags=0x01` if error). If connection fails: retry up to `config.direct.max_retries` with exponential backoff (base: `retry_backoff_ms`). If all retries fail: log warning and return — worker moves on. The sidecar side handles `DeliveryFailedError` accounting. |
| `_heartbeat_loop` | `(lease_id: bytes, ttl_ms: int, stop_event: threading.Event) -> None` | Sleep for `ttl_ms / 2` ms. Call `_client.heartbeat(lease_id)`. Repeat until `stop_event` set. Runs in a daemon Thread started by `run()`. |
| `stop` | `() -> None` | Set `_stop_event` |

#### Module entry point (`__main__`)

```python
if __name__ == "__main__":
    import sys
    runner = WorkerRunner(sys.argv[1])
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
```

**Pattern:** The heartbeat loop is a classic concurrent "keep-alive" pattern. `stop_event` is the clean shutdown signal — no daemon thread magic needed. Thread per task is fine with Python 3.13 free-threaded.

---

## Payload Serialisation

The SUBMIT → TASK → worker payload chain uses **msgpack** (for the envelope metadata) wrapping **cloudpickle** (for the Python function):

```
SUBMIT.payload = msgpack({
    "task_bytes":     cloudpickle.dumps({func, args, kwargs}),
    "callback_addr":  "10.0.1.5:51234",
    "label":          "cpu",
    "idempotent":     false
})

TASK.payload = msgpack({
    "task_bytes":     <same cloudpickle bytes from SUBMIT>,
    "lease_id":       <16 bytes>,
    "ttl_ms":         30000,
    "callback_addr":  "10.0.1.5:51234"
})
```

The Go sidecar passes `task_bytes` through opaquely — it never deserialises the cloudpickle. Only the Python worker deserialises it. This means the Go sidecar has no dependency on Python serialisation formats.

---

## Tests

### `sdk/tests/integration/test_worker_e2e.py`

Requires a running `taskflow-agent` (started in test setup via subprocess).

| Test | Asserts |
|------|---------|
| `test_simple_function` | Submit `lambda x: x * 2` with arg `21`. Result listener receives `42`. |
| `test_exception_propagation` | Submit function that raises `ValueError("bad input")`. Result listener receives frame with `flags=0x01`. `cloudpickle.loads(payload)` is a `ValueError`. |
| `test_multiple_concurrent_tasks` | Submit 20 tasks. All results arrive within 5s. All values correct. (Tests Python 3.13 free-threaded worker concurrency) |
| `test_heartbeat_keeps_lease_alive` | Submit a slow function (sleep 2s) with 1s TTL. Verify result arrives (heartbeat kept lease alive). |
| `test_worker_respawn` | Kill worker process by PID. Within 5s, submit a new task and verify it completes. (Tests manager.monitor respawn) |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy (later, Phase 6) | `_deliver_result` | Direct delivery today; Kafka delivery later. Same interface. |
| Thread-per-connection | `ResultServer._serve` | Python 3.13 free-threaded — truly parallel result handling |
| Separation of concerns | `_execute` never raises | Exceptions are return values here, not control flow |
| Heartbeat keep-alive | `_heartbeat_loop` | Standard distributed systems pattern for lease renewal |
