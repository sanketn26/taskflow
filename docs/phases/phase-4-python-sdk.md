# Phase 4 — Python SDK

## Goal

Developer-facing API. `@task`, `Runtime`, `TaskFuture`. The developer writes a decorated function, submits it via a Runtime, and awaits a Future. They never touch frames, sockets, or config.

**This phase is the MVP gate**: when its acceptance tests pass, a single-node `pip install` + `@task` + `future.result()` works end-to-end — tag it `0.0.1` and demo it (see architecture.md "Implementation Order"). Phases 5–6 widen the product; they do not block demoability.

## Testable Outcome

- `@task` wraps a function; calling it directly raises `RuntimeError`
- `@task(label="cpu")` stores label; available on `TaskDefinition.label`
- `Runtime()` connects to sidecar or raises `SidecarNotRunning`
- `runtime.submit(my_task, arg)` returns `TaskFuture`
- `future.result()` blocks until result arrives and returns correct value
- `future.result()` raises `TaskExecutionError` if task raised on the worker
- `future.result()` raises `DeliveryFailedError` if the worker exhausted delivery retries (via the sidecar's COMPLETE relay — the future fails fast instead of hanging)
- `runtime.map(my_task, [1, 2, 3])` returns `list[TaskFuture]`, all resolve correctly
- `with Runtime() as rt:` cleans up on exit

---

## Files

```
python/taskwire/task.py
python/taskwire/future.py
python/taskwire/runtime.py
python/taskwire/__init__.py
native/src/result_server.rs            (Rust ResultServer — preferred when built)
python/tests/unit/test_task.py
python/tests/unit/test_future.py
python/tests/integration/test_sdk_e2e.py
```

---

## Python

### `python/taskwire/task.py`

#### `TaskDefinition`

Wraps a callable and its metadata. Immutable after construction. Never executes — that is the Runtime's job.

| Field | Type | Description |
|-------|------|-------------|
| `func` | `Callable` | The wrapped function (cloudpickle-serialisable) |
| `label` | `str \| None` | Routing label; maps to a routing rule in config |
| `idempotent` | `bool` | Whether it is safe to re-execute on re-queue |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(func: Callable, label: str \| None, idempotent: bool)` | Store fields. Call `functools.update_wrapper(self, func)` so the TaskDefinition looks like the original function for introspection. |
| `__call__` | `(*args, **kwargs) -> NoReturn` | Always raise `RuntimeError("Tasks must be submitted via Runtime.submit(). Did you mean: runtime.submit(my_task, ...)?")`. Prevents accidental direct invocation. |
| `map` | `(items: Iterable[Any]) -> BatchSubmission` | Return `BatchSubmission(task=self, items=items)`. Does not execute or connect to anything. |
| `__repr__` | `() -> str` | `f"TaskDefinition({self.__name__!r}, label={self.label!r})"` |

#### `BatchSubmission` (dataclass, frozen)

A descriptor for a map operation. Passed to `Runtime.map()`.

| Field | Type | Description |
|-------|------|-------------|
| `task` | `TaskDefinition` | The task to apply |
| `items` | `Iterable[Any]` | Items to map over |

#### `task` decorator

| Form | Behaviour |
|------|-----------|
| `@task` (no parens) | `func` is passed directly, returns `TaskDefinition(func, label=None, idempotent=False)` |
| `@task(label="cpu")` | Returns a decorator that wraps the function |
| `@task(label="io", idempotent=True)` | Stores both on the `TaskDefinition` |

Implementation: check if `func is not None` (bare decorator) or `func is None` (called with arguments). Use `functools.wraps` in all paths.

---

### `python/taskwire/future.py`

#### `TaskFuture`

Result handle returned by `Runtime.submit`. Thread-safe — `_set_result` and `_set_exception` are called from the result-server thread (Rust or Python); `result()` is called from the user's thread. The `threading.Event` synchronisation is mandatory on every build — never rely on the GIL for visibility, and on free-threaded builds there is no GIL to rely on.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `bytes` | 16-byte UUID — for logging and diagnostics |
| `_event` | `threading.Event` | Set when result or exception arrives |
| `_result` | `Any` | Stored result value; `None` until resolved |
| `_exception` | `BaseException \| None` | Stored exception; `None` until resolved |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(task_id: bytes)` | Store task_id, create Event, set fields to None |
| `result` | `(timeout: float \| None = None) -> Any` | `self._event.wait(timeout)`. If not set after wait: raise `TimeoutError(f"Task {task_id.hex()[:8]} timed out")`. If `_exception` is set: `raise self._exception`. Return `self._result`. |
| `done` | `() -> bool` | Return `self._event.is_set()` |
| `cancel` | `() -> bool` | Delegate to `Runtime._cancel(task_id)`: send CANCEL (0x06) frame and wait for the Runtime reader thread to process the ACK. If ACK flags=0x00 (sidecar removed it from the queue before any worker pulled it): resolve self with `CancelledError`, return `True`. If flags=0x01 (already leased): return `False` — the task will run to completion; cancellation is best-effort, exactly like `concurrent.futures`. |
| `exception` | `() -> BaseException \| None` | If not `done()`: raise `RuntimeError("Future is not done yet")`. Return `self._exception`. |
| `_set_result` | `(value: Any) -> None` | Store `_result = value`, `_event.set()`. Called only by Runtime. |
| `_set_exception` | `(exc: BaseException) -> None` | Store `_exception = exc`, `_event.set()`. Called only by Runtime. |

The API deliberately mirrors `concurrent.futures.Future` (`result(timeout)`, `done()`, `cancel()`, `exception()`) — developers already know this contract, and it keeps a later `asyncio` bridge (`asyncio.wrap_future`-style) cheap.

---

### `python/taskwire/runtime.py`

The Facade. Hides `SidecarClient`, `ResultServer`, frame encoding, and config entirely. Developer interacts with this class only.

#### `Runtime`

| Field | Type | Description |
|-------|------|-------------|
| `_config` | `Config` | Loaded from YAML on init |
| `_sidecar` | `SidecarClient` | Connection to local agent Unix socket |
| `_result_server` | `ResultServer` | TCP listener; receives direct result frames from workers |
| `_pending` | `dict[bytes, TaskFuture]` | Maps `task_id → TaskFuture`; resolved by `_on_result` |
| `_submit_acks` | `dict[bytes, threading.Event]` | Per-submit ACK waiters. `submit()` returns only after the reader thread sees ACK `{task_id}`. |
| `_cancel_results` | `dict[bytes, CancelWaiter]` | Per-cancel ACK waiters. `CancelWaiter` holds an Event plus a mutable `cancelled: bool \| None`; the reader thread fills `cancelled` from the ACK flags. |
| `_lock` | `threading.Lock` | Protects `_pending` dict (Python 3.13 free-threaded safe) |
| `_reader` | `threading.Thread` | Daemon thread looping `FrameCodec.read_frame` on the sidecar socket. The sidecar connection is message-driven: this is the *only* place that reads it. Dispatch: ACK `{task_id}` → set the matching submit or cancel event; COMPLETE `{task_id, status: "delivery_failed", reason}` → pop the pending future, `_set_exception(DeliveryFailedError(task_id, reason))`. Exits on socket close. |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(config: str \| None = None)` | `Config.load(config)`. Create `SidecarClient` — raises `SidecarNotRunning` immediately if agent unreachable (fail fast). Create `ResultServer(advertise_addr=config.advertise_addr, on_result=self._on_result)`. Initialise `_pending`, `_submit_acks`, and `_cancel_results`. Start `_reader` only after all maps exist. |
| `submit` | `(task: TaskDefinition, *args, **kwargs) -> TaskFuture` | Generate `task_id = uuid.uuid4().bytes`. Build `future = TaskFuture(task_id)` and `ack = threading.Event()`. Under `_lock`, store `_pending[task_id] = future` and `_submit_acks[task_id] = ack`. Call `_sidecar.submit(task_id, _build_payload(task, args, kwargs))` and wait (5s timeout) for the reader thread to signal ACK — the ACK is the durability boundary, so `submit` returning means the sidecar owns the task. On timeout or send failure: remove both maps and raise `SidecarNotRunning`. Return `future`. |
| `map` | `(task: TaskDefinition, items: Iterable[Any], **kwargs) -> list[TaskFuture]` | Call `submit(task, item, **kwargs)` for each item. Return list of futures. Also accepts a `BatchSubmission` as first arg. |
| `shutdown` | `(wait: bool = True) -> None` | If `wait=True`: call `f.result(timeout=30)` for all pending futures (best-effort drain). Stop `_result_server`. Close `_sidecar`. |
| `__enter__` | `() -> Runtime` | Return `self` |
| `__exit__` | `(*args) -> None` | Call `shutdown()` |
| `_on_result` | `(task_id: bytes, result_bytes: bytes, is_error: bool) -> None` | Under `_lock`, pop `future = _pending.pop(task_id, None)`. If `future is None`: discard silently (duplicate delivery — at-least-once). If `is_error`: `future._set_exception(TaskExecutionError(task_id, ..., cloudpickle.loads(result_bytes)))`. Else: `future._set_result(cloudpickle.loads(result_bytes))`. |
| `_build_payload` | `(task: TaskDefinition, args: tuple, kwargs: dict) -> bytes` | `msgpack.dumps({"task_bytes": cloudpickle.dumps({"func": task.func, "args": args, "kwargs": kwargs}), "callback_addr": self._result_server.address, "label": task.label or "", "idempotent": task.idempotent})` |

Additions to the table above:

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `_cancel` | `(task_id: bytes) -> bool` | Register a cancel waiter, send `Frame(CANCEL, task_id, 0, b"")` via `_sidecar`, and wait for the reader thread to process the ACK. ACK flag `0x00` means cancelled; `0x01` means too late. Used by `TaskFuture.cancel()`. No method except `_reader` reads from the sidecar socket. |

**`_on_result` hardening:** `cloudpickle.loads(result_bytes)` can itself raise (missing class on the client side, version skew). Wrap it; on failure resolve the future with `TaskExecutionError(task_id, "result deserialisation failed: ...")` rather than letting the exception kill the result-server callback thread — a dead callback thread means every subsequent future hangs forever, which is the worst possible failure mode.

**Pattern:** Facade (hides all infrastructure). Null Object (`_pending.pop(..., None)` on duplicate delivery — silently discard rather than error). Context Manager for resource cleanup.

---

### `native/src/result_server.rs` — Rust ResultServer (`taskwire._native.ResultServer`)

Drop-in replacement for the Phase 3 pure-Python `ResultServer`; `Runtime.__init__` selects it when importable. Same constructor and surface: `address` property, `stop()`, `on_result` callback.

What lives where:

| In Rust (no GIL) | In Python (GIL held briefly) |
|------------------|------------------------------|
| `TcpListener` accept loop | — |
| Per-connection thread, `read_exact` frame parse | — |
| Frame validation (version, size cap) | — |
| — | the `on_result(task_id, payload, is_error)` callback: `Python::with_gil(\|py\| cb.call1(py, (...)))` |

Why it pays: with 8 workers finishing tasks concurrently against a GIL-build Runtime, eight Python handler threads contend with the user's own code for the GIL on every result. In Rust, parsing and socket I/O cost the GIL nothing; only the dict-update callback does. The callback should therefore stay tiny (it is: pop + `Event.set`).

Implementation notes:

- Spawn `std::thread` per connection (connections are short-lived, one frame each); a tokio runtime is overkill and complicates the wheel — revisit only if benchmarks demand it.
- `stop()` must unblock `accept()`: connect-to-self on loopback after setting the stop flag, the standard trick.
- Hold the callback as `Py<PyAny>`; never call it while holding any Rust-side lock (a callback that blocks on the GIL while a GIL-holding thread waits on that lock is a deadlock).
- Keep the Phase 3 Python implementation as the fallback and as the differential-testing reference: the e2e suite runs against both (`TASKWIRE_PURE_PYTHON=1` env toggle).

---

### `python/taskwire/__init__.py`

Public surface area — only export what developers need.

```
from .task import task, TaskDefinition, BatchSubmission
from .runtime import Runtime
from .future import TaskFuture
from .exceptions import (
    TaskwireError,
    SidecarNotRunning,
    ConfigError,
    TaskExecutionError,
    DeliveryFailedError,
)

__all__ = [
    "task", "TaskDefinition", "BatchSubmission",
    "Runtime", "TaskFuture",
    "TaskwireError", "SidecarNotRunning", "ConfigError",
    "TaskExecutionError", "DeliveryFailedError",
]
```

---

## Tests

### `python/tests/unit/test_task.py`

No sidecar needed.

| Test | Asserts |
|------|---------|
| `test_decorator_bare` | `@task` (no parens) wraps function; `isinstance(fn, TaskDefinition)` |
| `test_decorator_with_label` | `@task(label="cpu")` stores `label="cpu"` |
| `test_decorator_idempotent` | `@task(idempotent=True)` stores `idempotent=True` |
| `test_direct_call_raises` | `my_task(arg)` raises `RuntimeError` |
| `test_map_returns_batch` | `my_task.map([1,2,3])` returns `BatchSubmission`, no execution |
| `test_functools_wraps` | `my_task.__name__` == original function name |

### `python/tests/unit/test_future.py`

No sidecar needed.

| Test | Asserts |
|------|---------|
| `test_set_result_resolves` | `future._set_result(42)`, `future.result() == 42` |
| `test_set_exception_raises` | `future._set_exception(ValueError("x"))`, `future.result()` raises `ValueError` |
| `test_result_timeout` | `future.result(timeout=0.05)` raises `TimeoutError` before set |
| `test_done_before_after` | `future.done()` is False before set, True after |
| `test_concurrent_set_and_get` | Thread A calls `result()`, Thread B calls `_set_result(99)` 50ms later — Thread A unblocks with `99`. (Python 3.13 free-threaded: no GIL assists here, Event sync is essential) |

### `python/tests/integration/test_sdk_e2e.py`

Requires running `taskwire-agent`.

| Test | Asserts |
|------|---------|
| `test_submit_simple` | `@task` fn returns `x + 1`. `rt.submit(fn, 41).result() == 42` |
| `test_submit_exception` | Fn raises `ValueError`. `future.result()` raises `TaskExecutionError` with original preserved |
| `test_map_all_resolve` | `rt.map(fn, range(10))` — all 10 futures resolve with correct values |
| `test_context_manager` | `with Runtime() as rt:` — no resource leaks after block |
| `test_sidecar_not_running` | `Runtime(config="path/to/bad_socket.yaml")` raises `SidecarNotRunning` immediately |
| `test_concurrent_submit_100` | 100 tasks submitted, all futures resolve, correct values, within 10s |
| `test_cancel_unleased` | Pause workers (workers.count=0 config), submit, `future.cancel()` returns True, `future.result()` raises `CancelledError` |
| `test_cancel_too_late` | Submit a running slow task, `cancel()` returns False, `result()` still returns the value |
| `test_both_result_servers` | Full suite passes with `TASKWIRE_PURE_PYTHON=1` and without (Rust) — parametrised fixture |

---

## Implementation Guide

Build order:

1. **`task.py` + `future.py` with unit tests** — zero I/O, pure contract work. Get the decorator's three forms and the Future state machine exact.
2. **`Runtime` against the real agent** — submit/result happy path first, then exception propagation, then `map`.
3. **`cancel()`** — needs the CANCEL handler added to the Go server (`handleCancel`: remove from queue by task_id → ACK 0x00, else ACK 0x01). Small, but it's the first sidecar change driven by the SDK; do it after the happy path is stable.
4. **Rust `ResultServer`** — last; the Python one is already passing e2e, so wiring the Rust one in is a pure swap validated by `test_both_result_servers`.

Gotchas:

- **`shutdown(wait=True)` deadlock**: draining with `f.result(timeout=30)` while the result receiver is already stopped hangs every future. Order is law: drain *first*, stop receiver second. Encode that in a comment and a regression test.
- **`_pending` leak**: a future whose result is never delivered (worker host died, direct delivery exhausted) sits in `_pending` forever. Acceptable for this phase, but track it: add `Runtime.pending_count` property now so Phase 7 observability has the hook.
- **`uuid.uuid4().bytes` is fine** — don't reach for anything fancier for task IDs; collision risk is negligible and the sidecar treats IDs as opaque.
- **Free-threaded wheels**: `cp313t` users get the pure-Python fallback until the PyO3 free-threaded story is marked stable in CI; that combination must stay green in the test matrix.

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Facade | `Runtime` | Single entry point; all infrastructure hidden |
| Null Object | `_pending.pop(..., None)` | Duplicate delivery is silently discarded instead of crashing |
| Decorator / Factory | `task()` | Transparent wrapping; supports both `@task` and `@task(label=...)` |
| Context Manager | `Runtime.__enter__/__exit__` | Deterministic resource cleanup |
| Value Object | `TaskFuture`, `TaskDefinition` | Carry data; side effects only via explicit method calls |
