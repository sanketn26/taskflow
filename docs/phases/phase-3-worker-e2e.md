# Phase 3 — Python Worker and Single-Node E2E

## Goal

Implement registered Python workers that claim tasks from their local agent, execute them, store immutable results through that agent, and complete using a fencing lease. Workers never contact a submitting Runtime or external queue.

## Phase 0 Baseline

The Python package already contains `python/taskwire/worker/` and `python/taskwire/ipc/` package roots, and the Go agent already runs under `AgentHarness`. Fill those roots in place. The agent manager must launch workers with the same interpreter/environment as the installed SDK or an explicit configured interpreter; tests must not depend on importing from the checkout.

Implement `AgentHarness.worker_pids()` using observable agent status introduced in Phase 2, preserving its existing public signature and shutdown leak assertion. Record worker kill, stop, restart, and lease-expiry actions through the existing seeded `ChaosTimeline`, use `wait_until` for readiness, and put end-to-end scenarios under the existing `integration`, `chaos`, and `resource` markers. Keep the pure-Python `_accel` fallback suite mandatory.

## Testable Outcome

A raw protocol client submits a registered task to one agent, a managed worker executes it, heartbeats keep its lease alive, and the client receives a replayable agent-relayed result. Worker crash, stale completion, unknown task identity, serialization failure, and poison-task paths terminate predictably.

## Files

```text
python/taskwire/ipc/client.py
python/taskwire/registry.py
python/taskwire/worker/runner.py
python/taskwire/worker/heartbeat.py
python/taskwire/serialization.py
python/tests/integration/test_worker_e2e.py
harness/ledger.py
```

## Worker Connection

The worker connects only to the configured local Unix socket, registers with `HELLO(role="worker")`, then performs `PULL`, receives `TASK`, starts a heartbeat tied to `lease_id`, and uses agent RPCs to read/write objects. One connection multiplexes control and object-transfer requests using the Phase 1 request and transfer IDs; one reader dispatches responses and a serialized writer prevents frame interleaving.

All socket operations have deadlines. EOF or agent restart ends the current worker process cleanly so the manager can restart it; it does not continue executing work whose lease can no longer be renewed.

## Task Registry

Production tasks are resolved by exact `(task_name, task_version)`. Registration rejects duplicate identities unless the same callable is being idempotently registered. Unknown names/versions produce terminal `unknown_task` failures; they are deployment errors and are not retried.

The agent starts `workers.python_executable -m taskwire.worker.runner` in `workers.working_directory`, passes only the configured environment plus Taskwire-owned socket/config/worker-ID variables, and loads every `tasks.import_modules` module before polling. Import failure is a worker-start failure visible in status; the restart circuit breaker applies. Inline functions use the reserved `__inline__` identity and are accepted only when `tasks.allow_inline_functions` is enabled; this mode is explicitly trusted-code development compatibility.

## Execution Contract

1. Decode and validate `LeasedTask`.
2. Resolve the registered callable.
3. Fetch `arguments` when it is an `ObjectRef`; verify size and SHA-256.
4. Deserialize the canonical `{args, kwargs}` value and validate that `args` is an array and `kwargs` is a string-keyed map.
5. Start heartbeat before invoking user code. Its interval is `lease_ttl_ms / 3` with bounded jitter.
6. Execute the callable once for this attempt.
7. Serialize the return value or structured failure. Serialization errors become `serialization_error`; they never crash-loop the worker.
8. Put result bytes through the local agent/object store and receive an `ObjectRef`.
9. Send `COMPLETE {lease_id, result|failure}` and require an ACK. Stop retrying when the agent reports `stale_lease`.
10. Stop heartbeat and pull again.

Task exceptions are data. Catch `BaseException` around user invocation, but treat worker shutdown signals separately so controlled shutdown is not reported as a user failure. Preserve exception type/module, message, and a bounded traceback in `Failure`; an optional serialized exception belongs in `details` and deserialization must not be required to display the error.

Completion retry is idempotent for the same task, lease, and reference. If object upload succeeds but the COMPLETE response is lost, resend the same reference. A resumed worker completing after lease expiry is rejected by fencing and must not overwrite the accepted attempt.

## Heartbeat

The baseline pure-Python heartbeat supports ordinary Python workloads but cannot guarantee progress when native code holds the GIL indefinitely. Document that limitation. An optional native heartbeat may run on an OS thread; if present it must have identical start/stop/error semantics and the full suite must pass without it.

Two consecutive renewal failures or an explicit stale-lease response set a lost-lease flag. Cooperative tasks may inspect it; regardless, any later completion is expected to be rejected. The worker must not kill the whole process merely because arbitrary user code cannot be interrupted safely.

## Object Transfer

Object RPCs stream bounded chunks and never embed values larger than the configured inline threshold in a protocol frame. Upload supplies expected size, codec, and checksum; the agent chooses the key/store and returns the canonical reference. Workers cannot request filesystem paths directly.

## Required Tests

- Registered success and task exception, inline and object-backed arguments/results.
- Unknown task version is terminal and not requeued.
- Result serialization failure resolves as a structured failure.
- Worker killed mid-task is respawned and the lease is requeued.
- Worker SIGSTOP for two TTLs causes retry; its late completion is fenced.
- Lost COMPLETE ACK causes idempotent retry, not re-execution by that worker.
- Corrupt/missing input object terminates with `StorageConsistencyError` and emits an operator-visible error.
- Poison worker crash reaches max attempts/dead letter without a restart storm.
- Pure-Python fallback suite passes; native-heartbeat-specific GIL test is conditional.
- An append-only per-worker execution ledger proves every acknowledged task becomes terminal or remains accounted for, every Future reaches a bounded outcome, duplicates occur only after an induced lease expiry, the agent survives, no process/socket leaks remain, and submitted work equals terminal plus queued/leased work.

## Implementation Order

1. Registry and serialization helpers.
2. Agent client including object streaming and typed errors.
3. Worker loop without heartbeat against memory stores.
4. Heartbeat and lease-loss handling.
5. Worker-manager integration, including the existing harness `worker_pids()` hook, SQLite/filesystem E2E, and seeded chaos scenarios.
6. Exercise the installed wheel/packaged agent combination with `make smoke-wheel` extended to run one registered task.

## Exit Gate

Phase 3 is complete when a raw client receives agent-relayed results end to end, crash/retry/fencing tests pass, large values use verified `ObjectRef`s, registered identity is enforced, and the worker code contains no `callback_addr`, result listener, Kafka producer, or direct RESULT sender.
