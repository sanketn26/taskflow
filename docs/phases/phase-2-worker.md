# Phase 2 — Worker

## Goal

`taskwire worker` asserts the backend contract, claims tasks, runs registered Python functions, keeps itself alive in the backend, records outcomes, and shuts down cleanly when Kubernetes asks it to.

The worker uses only the Phase 1 `Backend` interface. Its unit tests run on the in-memory backend; its integration tests run on PostgreSQL. Tasks are submitted in tests through `Backend.submit`; the client SDK is Phase 3.

## Task registration

```python
from taskwire import task

@task(name="reports.render", version="1", pool="cpu", max_attempts=5, timeout=300)
def render(doc_id: str, *, fmt: str = "pdf") -> dict: ...

@task(name="embeddings.fetch", version="1", pool="io")
async def fetch(text: str) -> list[float]: ...
```

- `name` and `version` are required and form the task identity. `pool`, `max_attempts`, `timeout`, retry backoff, and `priority` are defaults that a submit may override.
- The worker imports the modules named on its command line and registers every decorated function. A duplicate identity bound to a different function is an error at startup.
- The registered `name@version` set is passed to every claim, so a worker never receives a task it cannot run.
- `async def` tasks are registered only by a worker in `async` mode; synchronous tasks only by workers in `process` or `thread` mode. A worker therefore never claims a task its mode cannot run.

## Command

```bash
taskwire worker myapp.tasks --pools cpu --mode process --processes 4
taskwire worker myapp.tasks --pools io  --mode async   --processes 2 --concurrency 100
```

| Setting | Environment variable | Default |
|---|---|---|
| Pools to claim from | `TASKWIRE_POOLS` | `default` |
| Execution mode | `TASKWIRE_MODE` | `process` |
| Executor processes | `TASKWIRE_PROCESSES` | CPU count |
| Tasks per executor process (`thread` and `async` modes) | `TASKWIRE_CONCURRENCY` | 1 in `process` mode, else 16 |
| Worker liveness period | `TASKWIRE_LIVENESS_SECONDS` | 30 |
| Poll interval when idle | `TASKWIRE_POLL_SECONDS` | 2, with jitter and backoff |
| Worker ID | `TASKWIRE_WORKER_ID` | hostname (the pod name) |
| Recycle an executor process after N tasks | `TASKWIRE_MAX_TASKS_PER_PROCESS` | unlimited |
| Startup check level | `TASKWIRE_STARTUP_CHECK` | `full` |

## Startup

1. Load configuration and import task modules.
2. Run the backend startup assertion (Phase 1). On failure, log the failed guarantee and exit non-zero.
3. Start executor processes, register the worker, and begin claiming.

`/readyz` reports ready only after step 2 has passed.

## Process model

One supervisor process and `processes` executor processes.

**Supervisor**

- Holds the only backend connection and runs no user code.
- Claims only as many tasks as there are free execution slots.
- Sends one heartbeat for the worker every third of the liveness period, with jitter.
- If the heartbeat reports the worker as no longer alive, abandons every running task and discards their outcomes; the leases are gone.
- Enforces each task's timeout.
- If the backend is unreachable, keeps running tasks, retries with backoff, and stops claiming. Outcomes that finish in the meantime are held and written on reconnect; a stale lease is rejected by fencing and the outcome is dropped.

**Executor processes**

- Run user code and hold no backend connection.
- Started with the `spawn` method so that no supervisor state is inherited.
- If one exits abnormally, the supervisor fails every attempt it was running with code `worker_process_died` (retryable) and starts a replacement.

### Execution modes

| Mode | Slots per process | Timeout enforcement |
|---|---|---|
| `process` | 1 | The executor process is killed and replaced |
| `async` | `concurrency` coroutines on one event loop | The coroutine is cancelled; if it does not yield within a grace period, the process is recycled |
| `thread` | `concurrency` threads | A thread cannot be stopped. The attempt is failed and its slot is marked lost; the process is recycled once it has no other running tasks or too many lost slots |

In `thread` and `async` modes a crash or a recycle affects every task in that process, so tasks run in these modes must be safe to retry.

## Execution contract

1. Decode the input with the codec recorded on the task.
2. Call the function once for this attempt.
3. Encode the return value. A value over the inline limit fails with `value_too_large` until Phase 6.
4. Record the outcome under the lease.

Failures are data, not crashes:

| Situation | Failure code | Retryable |
|---|---|---|
| Function raises | `task_exception` | Yes, unless the exception type is listed in the task's `no_retry` |
| Input cannot be decoded, or result cannot be encoded | `serialization_error` | No |
| Executor process dies | `worker_process_died` | Yes |
| Task exceeds its timeout | `timeout` | Yes |
| Attempts run out (written by the backend) | `max_attempts_exceeded` | — |

A failure records the exception type, message, and a bounded traceback. It never records argument values.

If a heartbeat lists a task under `cancel_requested`, a flag is set that the task can read through `taskwire.current_task().cancel_requested`. In `async` mode the coroutine is also cancelled. Cancellation of running work is otherwise cooperative.

## Serialization

- The default codec is `msgpack`, carrying `{args, kwargs}` and the return value. Supported types are those msgpack represents natively.
- `cloudpickle` is an opt-in codec for clusters where all submitters are trusted, enabled by `TASKWIRE_ALLOW_PICKLE=true` on the worker.
- The codec name is stored with each value, so a worker rejects a codec it has not enabled with `serialization_error`.

## Shutdown and drain

On `SIGTERM`:

1. Stop claiming and report not ready.
2. Let running tasks finish, up to `TASKWIRE_DRAIN_SECONDS` (default 25, which must be below the pod's `terminationGracePeriodSeconds`).
3. Stop remaining executors and `release` their tasks. A release does not consume an attempt until the task reaches `max_releases` (default 5); after that it does.
4. Deregister the worker and exit zero.

A second `SIGTERM`, or `SIGINT` twice, skips to step 3.

## Health

An HTTP listener on `TASKWIRE_HTTP_PORT` (default 9464) serves `/livez` (the supervisor loop is turning) and `/readyz` (startup assertion passed, backend reachable, and not draining). The same listener serves `/metrics` from Phase 4.

## Required tests

- Startup: a worker pointed at a backend that fails the assertion exits non-zero, names the guarantee, and never reports ready.
- Success and exception paths in each mode, with positional and keyword arguments.
- `process` mode: a task that holds the GIL in native code for longer than the liveness period does not cost the worker its liveness.
- Timeout in each mode: the attempt fails with `timeout`, is retried, and the slot is recovered.
- A hung task with no timeout set does not block other slots, and is visible in metrics as a long-running attempt.
- An executor process killed mid-task: its attempts fail as `worker_process_died`, a replacement starts, and the tasks are retried.
- The supervisor killed mid-task: liveness lapses, `reap` requeues its tasks, and another worker completes them.
- A supervisor paused with `SIGSTOP` past its liveness period: its tasks run elsewhere and its late outcomes are rejected.
- Backend outage shorter than the liveness period loses nothing; a longer one leads to retries and rejected late writes.
- Rolling-deploy case: a worker registered for `v1` only never claims `v2` tasks.
- Drain: `SIGTERM` during a long task releases it without consuming an attempt; a short task finishes. A task released more than `max_releases` times starts consuming attempts and eventually fails.
- A poison task that always crashes reaches `failed` after `max_attempts` without a restart storm.
- `async` mode sustains its configured concurrency of in-flight tasks in one process.
- Conservation ledger: across a seeded chaos run, every submitted task ends terminal, and a task runs more than once only after a recorded liveness loss, process death, timeout, or release.

## Exit gate

Tasks submitted through the backend interface are executed end to end by `taskwire worker` installed from the wheel, in all three modes, and the startup, crash, pause, outage, timeout, and drain tests pass with the conservation ledger intact.
