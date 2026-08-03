# Phase 4 — Python SDK

## Goal

Provide the Python `@task`, `Runtime`, and `TaskFuture` APIs over one local agent connection, including bounded submission acknowledgement, owner/cursor result replay, cancellation, shutdown, and transparent object references. This is one language binding for the Phase 1 contract; decorator and Future conventions are not agent-level task semantics.

This is the single-node pre-alpha MVP gate.

## Phase 0 Baseline

Add the public SDK to the existing `python/taskwire` package and extend, rather than replace, its current exports. `taskwire.__version__`, `find_agent_binary()`, `AgentNotFoundError`, `TASKWIRE_AGENT_PATH`, and bundled lookup at `taskwire/bin/taskwire-agent` are compatibility contracts established by Phase 0. `Runtime` may accept an explicit socket/config override, but its default local-agent discovery must use that contract and must never build or download an agent.

The build frontend remains the root `Makefile` plus Poetry/`python -m build`. Keep CPython 3.11–3.13 and pure-Python operation green. Extend `make smoke-wheel` from import/version/discovery coverage to a registered-task E2E; run it with the repository unavailable as an import source. SDK tests stay in the existing unit/integration trees and reuse `AgentHarness`, deadline polling, diagnostics, and the seeded chaos timeline.

## Public API

```python
@task(name="reports.generate", version="v3", invocation="value", labels={"workload": "cpu"}, idempotent=True)
def generate_report(request): ...

with Runtime() as runtime:
    future = runtime.submit(generate_report, input_ref)
    value = future.result(timeout=30)
```

`name` and `version` are required in production mode. `invocation="value"` is the portable default and accepts exactly one portable input value. `invocation="python_args"` explicitly enables ordinary Python positional/keyword calling semantics and is not executable by Node.js or Go workers. The decorator returns an immutable `TaskDefinition` retaining the callable for local registration. Convenience inference from module/qualname is allowed only if it produces a stable explicit value in exported metadata; lambdas and local functions require inline development mode.

## Files

```text
python/taskwire/task.py
python/taskwire/future.py
python/taskwire/runtime.py
python/taskwire/registry.py
python/taskwire/serialization.py
python/taskwire/__init__.py
python/tests/unit/test_task.py
python/tests/unit/test_future.py
python/tests/integration/test_sdk_e2e.py
```

## `TaskFuture`

Thread-safe terminal states are result, task failure, cancelled, submission failure, connection failure, and Runtime shutdown. Only the first terminal transition wins. Public methods are `result(timeout=None)`, `exception(timeout=None)`, `done()`, `cancel()`, `cancelled()`, `task_id`, and `owner_id`.

`result()` timeout raises `TimeoutError` without changing task state. `cancel()` sends a request and returns true only for the agent's successful queued-state transition. Duplicate/out-of-order result notifications are harmless. Future completion callbacks are not part of the v0.1 public API.

## Runtime Identity and Connection

Runtime owns a random persistent 16-byte `owner_id`. Callers may pass a previously persisted owner ID to resume after process restart; it is a bearer capability and must not be logged at info level. The Runtime attaches `taskwire-role: runtime` and its hex-encoded `taskwire-owner-id` to every RPC and maintains:

- `_submitting`: task IDs awaiting a `Submit` response.
- `_pending`: ACKed task IDs with live Futures.
- `_cursor`: highest contiguously handled result cursor.
- one serialized writer and one continuously reading dispatcher.

On connect/reconnect, open `WatchResults(owner_id, after_cursor)`, process notifications in cursor order, and call `AckResult` only after each Future reaches the corresponding terminal state. Unknown task IDs may be retained as reattachable terminal records rather than discarded; bounded retention follows agent policy.

### Submission ordering

1. Allocate task ID and install its Future in `_submitting` before writing.
2. Serialize the single portable input for `value`, or the canonical `{args, kwargs}` adapter payload for `python_args`. Inline values above the threshold are uploaded through the agent and replaced with `ObjectRef`.
3. Send `SUBMIT` with owner, registered identity, labels, and value reference.
4. Wait at most `ipc.submit_ack_timeout_ms` for the `Submit` response or error status.
5. Move to `_pending` only on a successful response. Timeout, rejection, or disconnect terminalizes and removes the Future; no pending entry leaks.

An unACKed submission has unknown acceptance and is not automatically retried with a new task ID. Retrying the same task ID/envelope is safe because agent creation is idempotent.

### Result handling

Fetch and checksum-verify an object-backed result, then deserialize it. Deserialization failure terminalizes that Future with `TaskExecutionError`; it must not terminate the dispatcher. Failures and cancellation map to stable SDK exceptions. Call `AckResult` only after recording the terminal transition. On ack loss the agent replays; first-terminal-wins makes this safe.

### Reattach

`Runtime(owner_id=...)` resumes the owner cursor. `reattach(task_ids)` sends bounded Phase 1 `TASK_QUERY` batches and creates Futures for queued/leased tasks or immediately terminal Futures for retained terminal snapshots. Unknown snapshots raise `TaskNotFoundError` without revealing whether the ID belongs to another owner. It does not depend on Kafka. Applications that need cross-process recovery must persist the owner ID and task IDs securely. An expired/acknowledged result may no longer be reattachable after retention.

### Error mapping

Protocol validation and role errors become `ProtocolError`; `task_conflict` becomes `TaskConflictError`; `task_not_found` becomes `TaskNotFoundError`; `too_late` makes cancellation return `False`; `stale_lease` and `unknown_lease` are worker-internal; `unknown_task` becomes `TaskExecutionError`; codec errors become `SerializationError`; storage-unavailable and retryable transport errors become `AgentUnavailableError`; checksum/storage-consistency errors become `StorageConsistencyError`; shutdown becomes `RuntimeClosedError`; and an unmapped/internal error becomes `TaskwireError` carrying the stable code and retryability. Exception messages never contain serialized arguments or results.

### Shutdown

`close(wait=True, timeout=None)` stops new submissions, optionally waits for pending Futures until the deadline, terminalizes unresolved Futures with `RuntimeClosedError`, closes the connection, and joins dispatcher/reconnect threads. It does not cancel remote work implicitly. Context-manager exit calls `close`.

## Security and Compatibility

The Unix socket and configured object store are trusted-code boundaries when cloudpickle is enabled. `cloudpickle`, inline functions, and `python_args` are explicitly Python runtime features and are never advertised as portable. Production portable tasks use the Phase 1 msgpack value profile or bytes. No Runtime TCP listener is created, no routable address is advertised, and no Rust result server exists.

Pure Python is mandatory. Native acceleration may optimize framing/heartbeat only behind parity tests.

## Required Tests

- Decorator validation, registry collision, stable name/version, and inline-mode rejection.
- Portable `value` submissions match the shared conformance bytes; `python_args` and cloudpickle registrations are marked Python-only and cannot be leased to synthetic Node.js/Go capabilities.
- Future first-terminal-wins, timeout, cancellation race, callback isolation, and thread safety.
- Disconnect before the `Submit` response leaves no Future leak and reports unknown acceptance clearly.
- ACKed submit survives Runtime disconnect; reconnect with owner/cursor resolves it.
- Owner metadata and same-owner stream replacement preserve result replay without delivering new notifications to the superseded stream.
- Disconnect after a notification but before `AckResult` causes replay and only one resolution.
- Duplicate, out-of-order, malformed, corrupt-object, and deserialization-failure results do not kill the dispatcher.
- Large arguments/results cross the object threshold; explicit `ObjectRef` is preserved.
- Concurrent submit/result/cancel stress test under the SQLite/filesystem defaults.
- Clean-wheel E2E on every supported Python version, including pure-Python fallback.
- Runtime shutdown has no thread, FD, Future, or socket leak.
- Every Phase 1 error code has an asserted SDK mapping; TASK_QUERY reattach covers active, terminal, expired, unknown, and wrong-owner IDs.

## Implementation Order

1. `TaskDefinition`, decorator, registry, and Future state machine.
2. Runtime connection/dispatcher and strict `Submit` response lifecycle.
3. Object upload/download and result mapping.
4. Reconnect, cursor replay, reattach, cancellation, and shutdown.
5. Extend the existing clean-wheel smoke test to SDK E2E and add seeded chaos scenarios without bypassing packaged agent discovery.

## Exit Gate

Phase 4 is complete when a clean installed wheel completes single-node tasks through SQLite/filesystem storage, every submitted Future reaches a bounded terminal outcome during tested failures, Runtime restart replay succeeds without Kafka, cancellation durability passes, and no callback server or direct-delivery configuration remains.

---

## Implementation Guide

> **Depends on:** Phases 2–3. This phase is the **single-node pre-alpha MVP gate**.
> After Phase 4, a user writes ordinary `@task` functions and `Runtime.submit`.

### File map

```text
python/taskwire/task.py
python/taskwire/future.py
python/taskwire/runtime.py
python/taskwire/exceptions.py   # SDK exception hierarchy
python/taskwire/registry.py     # extend Phase 3
python/taskwire/serialization.py
python/taskwire/__init__.py     # export task, Runtime, exceptions
python/tests/unit/test_task.py
python/tests/unit/test_future.py
python/tests/integration/test_sdk_e2e.py
```

### Step 1 — Exceptions + Future

```python
# python/taskwire/exceptions.py
class TaskwireError(Exception):
    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

class ProtocolError(TaskwireError): ...
class TaskConflictError(TaskwireError): ...
class TaskNotFoundError(TaskwireError): ...
class TaskExecutionError(TaskwireError): ...
class SerializationError(TaskwireError): ...
class AgentUnavailableError(TaskwireError): ...
class StorageConsistencyError(TaskwireError): ...
class RuntimeClosedError(TaskwireError): ...
```

```python
# python/taskwire/future.py
import threading
from typing import Any

class TaskFuture:
    def __init__(self, task_id: bytes, owner_id: bytes):
        self._task_id = task_id
        self._owner_id = owner_id
        self._cond = threading.Condition()
        self._done = False
        self._result: Any = None
        self._exc: BaseException | None = None
        self._cancelled = False

    @property
    def task_id(self) -> bytes: return self._task_id
    @property
    def owner_id(self) -> bytes: return self._owner_id

    def _set_result(self, value: Any) -> bool:
        with self._cond:
            if self._done:
                return False  # first terminal wins
            self._result, self._done = value, True
            self._cond.notify_all()
            return True

    def _set_exception(self, exc: BaseException) -> bool:
        with self._cond:
            if self._done:
                return False
            self._exc, self._done = exc, True
            self._cond.notify_all()
            return True

    def result(self, timeout: float | None = None) -> Any:
        with self._cond:
            if not self._cond.wait_for(lambda: self._done, timeout):
                raise TimeoutError()  # does NOT cancel task
            if self._exc:
                raise self._exc
            return self._result

    def done(self) -> bool:
        with self._cond:
            return self._done

    def cancelled(self) -> bool:
        with self._cond:
            return self._cancelled
```

### Step 2 — `@task` decorator

```python
# python/taskwire/task.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True, slots=True)
class TaskDefinition:
    name: str
    version: str
    invocation: str
    labels: dict[str, str]
    idempotent: bool
    fn: Callable[..., Any]

def task(
    *,
    name: str,
    version: str,
    invocation: str = "value",
    labels: dict[str, str] | None = None,
    idempotent: bool = False,
):
    if not name or not version:
        raise ValueError("name and version are required")
    if invocation not in ("value", "python_args"):
        raise ValueError(invocation)

    def deco(fn: Callable[..., Any]) -> TaskDefinition:
        defn = TaskDefinition(
            name=name,
            version=version,
            invocation=invocation,
            labels=dict(labels or {}),
            idempotent=idempotent,
            fn=fn,
        )
        # optional: auto-register on module import for workers
        return defn

    return deco
```

Usage:

```python
from taskwire import task, Runtime

@task(name="examples.add", version="v1", invocation="python_args", idempotent=True)
def add(a: int, b: int) -> int:
    return a + b

with Runtime(config="taskwire.yaml") as rt:
    fut = rt.submit(add, 20, 22)   # python_args
    assert fut.result(timeout=10) == 42
```

For portable `value`:

```python
@task(name="examples.inc", version="v1", invocation="value")
def inc(x):  # single value
    return x + 1

rt.submit(inc, 41)  # one positional value only
```

### Step 3 — Runtime connection lifecycle

```python
# python/taskwire/runtime.py
class Runtime:
    def __init__(
        self,
        *,
        config: str | Path | None = None,
        socket: str | None = None,
        owner_id: bytes | None = None,
    ):
        self._owner_id = owner_id or os.urandom(16)
        self._submitting: dict[bytes, TaskFuture] = {}
        self._pending: dict[bytes, TaskFuture] = {}
        self._cursor: int = 0
        self._client: AgentClient | None = None
        # threads: reader/dispatcher, optional reconnect
        ...

    def __enter__(self) -> Runtime:
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self) -> None:
        # resolve socket from config or find_agent_binary + external agent
        # WatchResults(owner, after_cursor=self._cursor)
        ...

    def submit(self, defn: TaskDefinition, *args, **kwargs) -> TaskFuture:
        task_id = uuid.uuid4().bytes
        fut = TaskFuture(task_id, self._owner_id)
        self._submitting[task_id] = fut
        try:
            value_ref = self._encode_input(defn, args, kwargs)
            self._client.submit(
                task_id=task_id,
                owner_id=self._owner_id,
                name=defn.name,
                version=defn.version,
                invocation=defn.invocation,
                input=value_ref,
                labels=defn.labels,
                idempotent=defn.idempotent,
            )
            # wait submit_ack_timeout_ms
            self._pending[task_id] = self._submitting.pop(task_id)
            return fut
        except Exception as exc:
            self._submitting.pop(task_id, None)
            fut._set_exception(map_error(exc))
            return fut

    def reattach(self, task_ids: list[bytes]) -> list[TaskFuture]:
        # TASK_QUERY batches → Futures
        ...

    def close(self, wait: bool = True, timeout: float | None = None) -> None:
        # stop submits; optional wait pending; RuntimeClosedError rest; join threads
        ...
```

**Dispatcher rules:**

```python
def _on_result(self, note: ResultNotification) -> None:
    fut = self._pending.get(note.task_id)
    # map state → set_result / set_exception
    # only after Future terminal: AckResult(owner, task_id, cursor)
    # advance _cursor contiguously
```

**Error mapping:** implement the table in “Error mapping” above; unit-test every Phase 1 code.

### Step 4 — Object threshold

```python
def _encode_input(self, defn, args, kwargs):
    if defn.invocation == "value":
        if len(args) != 1 or kwargs:
            raise TypeError("value invocation expects exactly one positional argument")
        raw = encode_portable(args[0])
        codec = "msgpack"
    else:
        raw = encode_python_args(args, kwargs)
        codec = "msgpack"  # or cloudpickle if configured
    if len(raw) > self._inline_threshold:
        ref = self._client.object_put(raw, codec=codec)
        return ValueRef(object=ref)
    return ValueRef(inline=raw, codec=codec)
```

### Step 5 — Public exports

```python
# python/taskwire/__init__.py
from taskwire.task import task, TaskDefinition
from taskwire.runtime import Runtime
from taskwire.future import TaskFuture
from taskwire.exceptions import *
from taskwire.agent_locate import find_agent_binary, AgentNotFoundError

__all__ = [
    "__version__",
    "task", "TaskDefinition", "Runtime", "TaskFuture",
    "find_agent_binary", "AgentNotFoundError",
    # exceptions...
]
```

### Step 6 — Tests + smoke-wheel

```python
# python/tests/integration/test_sdk_e2e.py
def test_submit_result(harness):
    with Runtime(socket=harness.socket_path) as rt:
        fut = rt.submit(add, 1, 2)
        assert fut.result(timeout=10) == 3

def test_reconnect_replay(harness):
    owner = os.urandom(16)
    with Runtime(socket=..., owner_id=owner) as rt:
        fut = rt.submit(...)
        task_id = fut.task_id
    with Runtime(socket=..., owner_id=owner) as rt:
        fut2 = rt.reattach([task_id])[0]
        assert fut2.result(timeout=10) == ...
```

Extend `Makefile` `smoke-wheel` to run a registered task E2E with repo not importable.

### Done checklist

- [ ] `@task` requires name/version; invocation value vs python_args enforced  
- [ ] `Submit` response timeout bounds; no `_pending` leak on failure  
- [ ] Future first-terminal-wins; `result(timeout)` does not cancel  
- [ ] Cancel returns True only when agent queued-cancel succeeds  
- [ ] Reconnect + reattach without Kafka  
- [ ] Large payload uses ObjectRef transparently  
- [ ] All Phase 1 error codes mapped  
- [ ] Clean wheel E2E on supported Python  
- [ ] Shutdown: no thread/FD/socket leaks  
- [ ] No TCP callback server  

### Review request template

```text
Please review Phase 4 (MVP gate).
Branch: phase-4-...
Implemented: @task, Runtime, TaskFuture, reattach, smoke-wheel E2E
Commands: make format lint unit integration smoke-wheel
Gaps: ...
```

**After Phase 4 passes review, the single-node pre-alpha MVP is done.** Phases 5–7 are optional feature gates.
