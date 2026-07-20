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

`result()` timeout raises `TimeoutError` without changing task state. `cancel()` sends a request and returns true only for the agent's successful queued-state transition. Duplicate/out-of-order RESULT frames are harmless. Future completion callbacks are not part of the v0.1 public API.

## Runtime Identity and Connection

Runtime owns a random persistent 16-byte `owner_id`. Callers may pass a previously persisted owner ID to resume after process restart; it is a bearer capability and must not be logged at info level. The Runtime registers with `HELLO(role="runtime", owner_id=...)` before any owner-scoped request and maintains:

- `_submitting`: task IDs awaiting SUBMIT ACK.
- `_pending`: ACKed task IDs with live Futures.
- `_cursor`: highest contiguously handled result cursor.
- one serialized writer and one continuously reading dispatcher.

On connect/reconnect, send `RESUME_RESULTS(owner_id, after_cursor, batch_size)`, process results in cursor order, and ACK each result only after its Future reaches the corresponding terminal state. Unknown task IDs may be retained as reattachable terminal records rather than discarded; bounded retention follows agent policy.

### Submission ordering

1. Allocate task ID and install its Future in `_submitting` before writing.
2. Serialize the single portable input for `value`, or the canonical `{args, kwargs}` adapter payload for `python_args`. Inline values above the threshold are uploaded through the agent and replaced with `ObjectRef`.
3. Send `SUBMIT` with owner, registered identity, labels, and value reference.
4. Wait at most `ipc.submit_ack_timeout_ms` for typed ACK/ERROR.
5. Move to `_pending` only on ACK. Timeout, rejection, disconnect, or partial write terminalizes and removes the Future; no pending entry leaks.

An unACKed submission has unknown acceptance and is not automatically retried with a new task ID. Retrying the same task ID/envelope is safe because agent creation is idempotent.

### Result handling

Fetch and checksum-verify an object-backed result, then deserialize it. Deserialization failure terminalizes that Future with `TaskExecutionError`; it must not terminate the dispatcher. Failures and cancellation map to stable SDK exceptions. ACK only after recording the terminal transition. On ACK loss the agent replays; first-terminal-wins makes this safe.

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
- Disconnect before ACK leaves no Future leak and reports unknown acceptance clearly.
- ACKed submit survives Runtime disconnect; reconnect with owner/cursor resolves it.
- HELLO registration and same-owner connection replacement preserve result replay without delivering new notifications to the superseded connection.
- Disconnect after RESULT before ACK causes replay and only one resolution.
- Duplicate, out-of-order, malformed, corrupt-object, and deserialization-failure results do not kill the dispatcher.
- Large arguments/results cross the object threshold; explicit `ObjectRef` is preserved.
- Concurrent submit/result/cancel stress test under the SQLite/filesystem defaults.
- Clean-wheel E2E on every supported Python version, including pure-Python fallback.
- Runtime shutdown has no thread, FD, Future, or socket leak.
- Every Phase 1 error code has an asserted SDK mapping; TASK_QUERY reattach covers active, terminal, expired, unknown, and wrong-owner IDs.

## Implementation Order

1. `TaskDefinition`, decorator, registry, and Future state machine.
2. Runtime connection/dispatcher and strict SUBMIT ACK lifecycle.
3. Object upload/download and result mapping.
4. Reconnect, cursor replay, reattach, cancellation, and shutdown.
5. Extend the existing clean-wheel smoke test to SDK E2E and add seeded chaos scenarios without bypassing packaged agent discovery.

## Exit Gate

Phase 4 is complete when a clean installed wheel completes single-node tasks through SQLite/filesystem storage, every submitted Future reaches a bounded terminal outcome during tested failures, Runtime restart replay succeeds without Kafka, cancellation durability passes, and no callback server or direct-delivery configuration remains.
