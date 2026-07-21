# Phase 3 — Python Worker and Single-Node E2E

## Goal

Implement the first registered worker runtime in Python, plus the reusable worker-protocol conformance suite that later Node.js and Go workers must pass. Python workers claim tasks from their local agent, execute them, store immutable results through that agent, and complete using a fencing lease. Workers never contact a submitting Runtime or external queue.

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

The worker loads its task registry, connects only to the configured local Unix socket, sends the full Phase 1 worker `HELLO`, publishes generation 1 with `REGISTER_TASKS`, and begins `PULL` only after the registration ACK. It receives `TASK`, starts a heartbeat tied to `lease_id`, and uses agent RPCs to read/write objects. One connection multiplexes control and object-transfer requests using the Phase 1 request and transfer IDs; one reader dispatches responses and a serialized writer prevents frame interleaving. Reconnect creates a new capability namespace and republishes the complete registry before pulling.

All socket operations have deadlines. EOF or agent restart ends the current worker process cleanly so the manager can restart it; it does not continue executing work whose lease can no longer be renewed.

## Task Registry

Production tasks are resolved by exact `(task_name, task_version)`. Registration rejects duplicate identities unless the same callable is being idempotently registered. Unknown names/versions produce terminal `unknown_task` failures; they are deployment errors and are not retried.

The agent starts the configured Python worker-pool command in the pool working directory and passes only the configured environment plus Taskwire-owned socket/config/worker-ID/pool variables. The command owns Python module loading before registration; import failure is a worker-start failure visible in status and the pool restart circuit breaker applies. Inline functions use the reserved `__inline__` identity and are accepted only when `tasks.allow_inline_functions` is enabled; this mode is explicitly trusted-code development compatibility and advertises only `python_args` plus `cloudpickle`.

## Execution Contract

1. Decode and validate `LeasedTask`.
2. Resolve the registered callable.
3. Fetch `input` when it is an `ObjectRef`; verify size and SHA-256.
4. Validate the leased invocation/codec against the accepted registration. For `value`, decode the portable profile and call the handler with one value. For `python_args`, deserialize canonical `{args, kwargs}`, validate its shape, and call the Python adapter.
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
- Capability registration is deterministic; the worker never pulls before ACK and republishes after reconnect.
- Portable-value conformance vectors cover every shared type and boundary; Python-specific values and `cloudpickle` never advertise portable compatibility.
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
2. Agent client including HELLO/capability registration, object streaming, and typed errors.
3. Worker loop without heartbeat against memory stores.
4. Heartbeat and lease-loss handling.
5. Worker-manager integration, including the existing harness `worker_pids()` hook, SQLite/filesystem E2E, and seeded chaos scenarios.
6. Exercise the installed wheel/packaged agent combination with `make smoke-wheel` extended to run one registered task.

## Exit Gate

Phase 3 is complete when a raw client receives agent-relayed results end to end, crash/retry/fencing tests pass, large values use verified `ObjectRef`s, registered identity and capability-aware leasing are enforced, the portable conformance vectors are reusable without Python imports, and the worker code contains no `callback_addr`, result listener, Kafka producer, or direct RESULT sender.

---

## Implementation Guide

> **Depends on:** Phase 2 exit green (real agent IPC, claim, complete, objects).
> **Does not include:** `@task` / `Runtime` public SDK (Phase 4). Use registry + raw client tests.

### File map

```text
python/taskwire/registry.py
python/taskwire/serialization.py
python/taskwire/ipc/client.py
python/taskwire/worker/runner.py
python/taskwire/worker/heartbeat.py
python/taskwire/worker/__main__.py   # optional: python -m taskwire.worker
harness/ledger.py
python/tests/integration/test_worker_e2e.py
python/tests/unit/test_registry.py
python/tests/unit/test_serialization.py
# portable vectors (JSON/msgpack fixtures shared with later Node/Go):
testdata/portable/values.json
```

### Step 1 — Registry + serialization

```python
# python/taskwire/registry.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True, slots=True)
class TaskSpec:
    name: str
    version: str
    invocation: str  # "value" | "python_args"
    codecs: tuple[str, ...]
    fn: Callable[..., Any]

class Registry:
    def __init__(self) -> None:
        self._by_id: dict[tuple[str, str], TaskSpec] = {}

    def register(self, spec: TaskSpec) -> None:
        key = (spec.name, spec.version)
        existing = self._by_id.get(key)
        if existing is not None and existing.fn is not spec.fn:
            raise TaskConflictError(f"duplicate {spec.name}@{spec.version}")
        self._by_id[key] = spec

    def get(self, name: str, version: str) -> TaskSpec | None:
        return self._by_id.get((name, version))

    def registration_payload(self, worker_id: str, generation: int = 1) -> dict:
        return {
            "worker_id": worker_id,
            "generation": generation,
            "tasks": [
                {
                    "task_name": s.name,
                    "task_version": s.version,
                    "invocation": s.invocation,
                    "codecs": list(s.codecs),
                }
                for s in self._by_id.values()
            ],
        }
```

```python
# python/taskwire/serialization.py
import msgpack
import hashlib

def encode_portable(value: object) -> bytes:
    """Phase 1 portable profile: reject NaN/inf/non-str keys/etc."""
    ...

def decode_portable(data: bytes) -> object:
    ...

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def encode_python_args(args: tuple, kwargs: dict) -> bytes:
    # canonical {args: list, kwargs: dict} via msgpack or cloudpickle per codec
    ...
```

### Step 2 — Multiplexed agent client

```python
# python/taskwire/ipc/client.py
class AgentClient:
    """One Unix connection: reader thread + serialized write; request_id futures."""

    def __init__(self, socket_path: str, *, max_frame: int): ...

    def connect(self) -> None: ...
    def close(self) -> None: ...

    def hello_worker(self, worker_id: str, runtime_version: str, codecs: list[str]) -> None:
        # HELLO role=worker; wait Ack(hello)
        ...

    def register_tasks(self, payload) -> None:
        # REGISTER_TASKS; wait Ack(register_tasks)
        ...

    def pull(self, worker_id: str, generation: int) -> LeasedTask | None:
        # PULL → TASK or Ack(empty_pull)
        ...

    def heartbeat(self, lease_id: bytes) -> None: ...

    def complete(self, task_id: bytes, lease_id: bytes, *, result=None, failure=None) -> None: ...

    def object_put(self, data: bytes, codec: str) -> ObjectRef: ...
    def object_get(self, ref: ObjectRef) -> bytes: ...
```

Writer must never interleave frames; use a lock or single writer thread + queue.

### Step 3 — Heartbeat

```python
# python/taskwire/worker/heartbeat.py
import threading
import time
import random

class Heartbeat:
    def __init__(self, client: AgentClient, lease_id: bytes, ttl_ms: int):
        self._client = client
        self._lease_id = lease_id
        self._interval = (ttl_ms / 3.0) * (1.0 + random.uniform(-0.1, 0.1))
        self._stop = threading.Event()
        self.lost = False
        self._failures = 0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="taskwire-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._client.heartbeat(self._lease_id)
                self._failures = 0
            except Exception:
                self._failures += 1
                if self._failures >= 2:
                    self.lost = True
```

Document: pure-Python heartbeat cannot interrupt GIL-holding native code.

### Step 4 — Worker loop

```python
# python/taskwire/worker/runner.py
def main(argv: list[str] | None = None) -> int:
    # argv or env: modules to import for side-effect registration
    # TASKWIRE_SOCKET, TASKWIRE_WORKER_ID, TASKWIRE_CONFIG
    registry = load_registry_from_modules(modules)
    client = AgentClient(os.environ["TASKWIRE_SOCKET"], max_frame=...)
    client.connect()
    client.hello_worker(...)
    client.register_tasks(registry.registration_payload(...))

    while not shutdown:
        leased = client.pull(...)
        if leased is None:
            time.sleep(idle_backoff)
            continue
        run_one(client, registry, leased)

def run_one(client, registry, leased: LeasedTask) -> None:
    hb = Heartbeat(client, leased.lease_id, leased.ttl_ms)
    hb.start()
    try:
        spec = registry.get(leased.task_name, leased.task_version)
        if spec is None:
            fail(client, leased, code="unknown_task", retryable=False)
            return
        raw = resolve_input(client, leased.input)  # ObjectRef → bytes + checksum
        value = decode_for_invocation(spec, raw, leased)
        try:
            if spec.invocation == "value":
                out = spec.fn(value)
            else:
                out = spec.fn(*value["args"], **value["kwargs"])
        except BaseException as exc:
            if is_shutdown_exception(exc):
                raise
            fail_from_exception(client, leased, exc)
            return
        ref = put_result(client, out, codec=...)
        client.complete(leased.task_id, leased.lease_id, result=ref)
    except StaleLeaseError:
        return  # fenced; do not retry as new execution
    finally:
        hb.stop()
```

**Agent pool command example** (config):

```yaml
workers:
  pools:
    - name: python-default
      runtime: python
      command:
        - python3
        - -m
        - taskwire.worker.runner
        - myapp.tasks
      count: 2
      working_directory: "."
      labels: {workload: general}
      resources: {max_memory_mb: 2048, max_cpu_percent: 80}
```

### Step 5 — Harness hooks + ledger

```python
# AgentHarness.worker_pids() → from status snapshot worker_pids
# harness/ledger.py — append-only per-worker execution events for conservation tests
```

### Step 6 — E2E tests

```python
# python/tests/integration/test_worker_e2e.py
@pytest.mark.integration
def test_registered_success(harness, tmp_path):
    # config with one python pool importing a test tasks module
    # raw runtime client SUBMIT examples.add v1
    # wait_until result succeeded
    ...

@pytest.mark.chaos
def test_worker_killed_mid_task(harness):
    # kill worker PID mid-execution; lease expires; another worker completes
    ...
```

### Implementation order

1. Registry + serialization unit tests  
2. AgentClient HELLO/register/object/pull/complete  
3. Worker loop without heartbeat (memory agent OK if Phase 2 supports it)  
4. Heartbeat + lease-loss  
5. Manager integration + SQLite E2E + chaos  
6. Extend `make smoke-wheel` with one registered task (or prepare for Phase 4)

### Done checklist

- [ ] Worker never connects to submitter; only local agent socket  
- [ ] No PULL before REGISTER_TASKS ACK  
- [ ] Success + task_exception + unknown_task + serialization_error  
- [ ] Object-backed args/results checksum verified  
- [ ] Kill mid-task → requeue → complete; late COMPLETE → stale_lease  
- [ ] Lost COMPLETE ACK → resend same ObjectRef (no double user execution by that worker)  
- [ ] Poison crash → dead letter without fork storm  
- [ ] Ledger conservation holds  
- [ ] No Kafka / callback / direct RESULT  

### Review request template

```text
Please review Phase 3.
Branch: phase-3-...
Implemented: registry, ipc client, worker runner, heartbeat, e2e+chaos
Commands: make unit integration smoke-wheel
Gaps: ...
```
