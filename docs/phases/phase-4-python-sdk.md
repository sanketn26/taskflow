# Phase 4 — Python SDK

## Goal

Developer-facing API. `@task`, `Runtime`, `TaskFuture`. The developer writes a decorated function, submits it via a Runtime, and awaits a Future. They never touch frames, sockets, or config.

## Testable Outcome

- `@task` wraps a function; calling it directly raises `RuntimeError`
- `@task(label="cpu")` stores label; available on `TaskDefinition.label`
- `Runtime()` connects to sidecar or raises `SidecarNotRunning`
- `runtime.submit(my_task, arg)` returns `TaskFuture`
- `future.result()` blocks until result arrives and returns correct value
- `future.result()` raises `TaskExecutionError` if task raised on the worker
- `runtime.map(my_task, [1, 2, 3])` returns `list[TaskFuture]`, all resolve correctly
- `with Runtime() as rt:` cleans up on exit

---

## Files

```
sdk/taskflow/task.py
sdk/taskflow/future.py
sdk/taskflow/runtime.py
sdk/taskflow/__init__.py
sdk/tests/unit/test_task.py
sdk/tests/unit/test_future.py
sdk/tests/integration/test_sdk_e2e.py
```

---

## Python

### `sdk/taskflow/task.py`

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

### `sdk/taskflow/future.py`

#### `TaskFuture`

Result handle returned by `Runtime.submit`. Thread-safe — `_set_result` and `_set_exception` are called from the `ResultServer` thread; `result()` is called from the user's thread. Python 3.13 free-threaded: no GIL, so the `threading.Event` synchronisation is mandatory.

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
| `exception` | `() -> BaseException \| None` | If not `done()`: raise `RuntimeError("Future is not done yet")`. Return `self._exception`. |
| `_set_result` | `(value: Any) -> None` | Store `_result = value`, `_event.set()`. Called only by Runtime. |
| `_set_exception` | `(exc: BaseException) -> None` | Store `_exception = exc`, `_event.set()`. Called only by Runtime. |

---

### `sdk/taskflow/runtime.py`

The Facade. Hides `SidecarClient`, `ResultServer`, frame encoding, and config entirely. Developer interacts with this class only.

#### `Runtime`

| Field | Type | Description |
|-------|------|-------------|
| `_config` | `Config` | Loaded from YAML on init |
| `_sidecar` | `SidecarClient` | Connection to local agent Unix socket |
| `_result_server` | `ResultServer` | TCP listener; receives direct result frames from workers |
| `_pending` | `dict[bytes, TaskFuture]` | Maps `task_id → TaskFuture`; resolved by `_on_result` |
| `_lock` | `threading.Lock` | Protects `_pending` dict (Python 3.13 free-threaded safe) |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `__init__` | `(config: str \| None = None)` | `Config.load(config)`. Create `SidecarClient` — raises `SidecarNotRunning` immediately if agent unreachable (fail fast). Create `ResultServer(advertise_addr=config.advertise_addr, on_result=self._on_result)`. Initialise `_pending = {}`. |
| `submit` | `(task: TaskDefinition, *args, **kwargs) -> TaskFuture` | Generate `task_id = uuid.uuid4().bytes`. Build `future = TaskFuture(task_id)`. Under `_lock`, store `_pending[task_id] = future`. Call `_sidecar.submit(task_id, _build_payload(task, args, kwargs))`. Return `future`. |
| `map` | `(task: TaskDefinition, items: Iterable[Any], **kwargs) -> list[TaskFuture]` | Call `submit(task, item, **kwargs)` for each item. Return list of futures. Also accepts a `BatchSubmission` as first arg. |
| `shutdown` | `(wait: bool = True) -> None` | If `wait=True`: call `f.result(timeout=30)` for all pending futures (best-effort drain). Stop `_result_server`. Close `_sidecar`. |
| `__enter__` | `() -> Runtime` | Return `self` |
| `__exit__` | `(*args) -> None` | Call `shutdown()` |
| `_on_result` | `(task_id: bytes, result_bytes: bytes, is_error: bool) -> None` | Under `_lock`, pop `future = _pending.pop(task_id, None)`. If `future is None`: discard silently (duplicate delivery — at-least-once). If `is_error`: `future._set_exception(TaskExecutionError(task_id, ..., cloudpickle.loads(result_bytes)))`. Else: `future._set_result(cloudpickle.loads(result_bytes))`. |
| `_build_payload` | `(task: TaskDefinition, args: tuple, kwargs: dict) -> bytes` | `msgpack.dumps({"task_bytes": cloudpickle.dumps({"func": task.func, "args": args, "kwargs": kwargs}), "callback_addr": self._result_server.address, "label": task.label or "", "idempotent": task.idempotent})` |

**Pattern:** Facade (hides all infrastructure). Null Object (`_pending.pop(..., None)` on duplicate delivery — silently discard rather than error). Context Manager for resource cleanup.

---

### `sdk/taskflow/__init__.py`

Public surface area — only export what developers need.

```
from .task import task, TaskDefinition, BatchSubmission
from .runtime import Runtime
from .future import TaskFuture
from .exceptions import (
    TaskflowError,
    SidecarNotRunning,
    ConfigError,
    TaskExecutionError,
    DeliveryFailedError,
)

__all__ = [
    "task", "TaskDefinition", "BatchSubmission",
    "Runtime", "TaskFuture",
    "TaskflowError", "SidecarNotRunning", "ConfigError",
    "TaskExecutionError", "DeliveryFailedError",
]
```

---

## Tests

### `sdk/tests/unit/test_task.py`

No sidecar needed.

| Test | Asserts |
|------|---------|
| `test_decorator_bare` | `@task` (no parens) wraps function; `isinstance(fn, TaskDefinition)` |
| `test_decorator_with_label` | `@task(label="cpu")` stores `label="cpu"` |
| `test_decorator_idempotent` | `@task(idempotent=True)` stores `idempotent=True` |
| `test_direct_call_raises` | `my_task(arg)` raises `RuntimeError` |
| `test_map_returns_batch` | `my_task.map([1,2,3])` returns `BatchSubmission`, no execution |
| `test_functools_wraps` | `my_task.__name__` == original function name |

### `sdk/tests/unit/test_future.py`

No sidecar needed.

| Test | Asserts |
|------|---------|
| `test_set_result_resolves` | `future._set_result(42)`, `future.result() == 42` |
| `test_set_exception_raises` | `future._set_exception(ValueError("x"))`, `future.result()` raises `ValueError` |
| `test_result_timeout` | `future.result(timeout=0.05)` raises `TimeoutError` before set |
| `test_done_before_after` | `future.done()` is False before set, True after |
| `test_concurrent_set_and_get` | Thread A calls `result()`, Thread B calls `_set_result(99)` 50ms later — Thread A unblocks with `99`. (Python 3.13 free-threaded: no GIL assists here, Event sync is essential) |

### `sdk/tests/integration/test_sdk_e2e.py`

Requires running `taskflow-agent`.

| Test | Asserts |
|------|---------|
| `test_submit_simple` | `@task` fn returns `x + 1`. `rt.submit(fn, 41).result() == 42` |
| `test_submit_exception` | Fn raises `ValueError`. `future.result()` raises `TaskExecutionError` with original preserved |
| `test_map_all_resolve` | `rt.map(fn, range(10))` — all 10 futures resolve with correct values |
| `test_context_manager` | `with Runtime() as rt:` — no resource leaks after block |
| `test_sidecar_not_running` | `Runtime(config="path/to/bad_socket.yaml")` raises `SidecarNotRunning` immediately |
| `test_concurrent_submit_100` | 100 tasks submitted, all futures resolve, correct values, within 10s |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Facade | `Runtime` | Single entry point; all infrastructure hidden |
| Null Object | `_pending.pop(..., None)` | Duplicate delivery is silently discarded instead of crashing |
| Decorator / Factory | `task()` | Transparent wrapping; supports both `@task` and `@task(label=...)` |
| Context Manager | `Runtime.__enter__/__exit__` | Deterministic resource cleanup |
| Value Object | `TaskFuture`, `TaskDefinition` | Carry data; side effects only via explicit method calls |
