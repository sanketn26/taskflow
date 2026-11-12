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
| Recycle an executor process above this resident memory, once it is idle | `TASKWIRE_MAX_MEMORY_PER_PROCESS_MB` | unlimited |
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
- Never claims ahead: a claimed task is handed to an executor immediately. There is no local queue of reserved tasks (Phase 1, `claim`).
- Learns which buckets it owns from each heartbeat and takes no part in assigning them. It claims from its own buckets first and from others only after the affinity window (Phase 1).
- Recycles an executor process when it reaches its task or memory limit, between tasks and never during one. Recycling is not a failure and costs no attempt.
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

**A timeout is only a hard guarantee in `process` mode.** In `thread` mode the timed-out function keeps running until it returns or the process is recycled, so it can still cause side effects after its attempt has been failed and retried elsewhere. In `async` mode a coroutine that never yields behaves the same way. Tasks that need a timeout to actually stop work must run in `process` mode; the worker logs a warning at start when a task with a `timeout` is registered in `thread` mode.

**A task must not block on another task's result.** Inside a running task, `TaskFuture.result()`, `wait()`, and `gather()` raise `BlockingInTaskError`: a slot waiting on work that needs a slot is how pools deadlock. A task that needs follow-up work submits it and returns; the follow-up reads what it needs from the result channel. `TASKWIRE_ALLOW_BLOCKING_IN_TASK=true` turns the error into a logged warning.

## Execution contract

1. Decode the input with the codec recorded on the task.
2. Call the function once for this attempt.
3. Encode the return value and publish it to the result channel. A task that returns `None` publishes nothing.
4. Record the outcome under the lease: `complete` with the reference the channel returned.

The order matters. Publish comes first, so a task is never `succeeded` with a reference to something that was not written. A crash between the two leaves an orphan in the channel and a task that is retried.

Failures are data, not crashes:

| Situation | Failure code | Retryable |
|---|---|---|
| Function raises | `task_exception` | Yes, unless the exception type is listed in the task's `no_retry` |
| Input cannot be decoded, or result cannot be encoded | `serialization_error` | No |
| Result channel rejects or cannot take the publish | `result_publish_failed` | Yes |
| Task returns a value but no result channel is configured | `no_result_channel` | No |
| Executor process dies | `worker_process_died` | Yes |
| Task exceeds its timeout | `timeout` | Yes |
| Attempts run out (written by the backend) | `max_attempts_exceeded` | — |

A failure records the exception type, message, and a bounded traceback. It never records argument values.

If a heartbeat lists a task under `cancel_requested`, a flag is set that the task can read through `taskwire.current_task().cancel_requested`. In `async` mode the coroutine is also cancelled. Cancellation of running work is otherwise cooperative.

## Result channel

Task output never enters the backend (Phase 1, "Results never enter the backend"). It goes to a result channel, a second small interface selected by `TASKWIRE_RESULT_URL`:

```python
class ResultChannel(Protocol):
    capabilities: ChannelCapabilities

    def check(self, level: CheckLevel) -> CheckReport: ...
    def publish(self, task: ClaimedTask, codec: str, payload: bytes) -> ResultRef: ...
    def fetch(self, ref: ResultRef) -> tuple[str, bytes]: ...     # requires `fetch`
    def delete(self, refs: Sequence[ResultRef]) -> None: ...       # requires `delete`
    def close(self) -> None: ...
```

| Capability | Meaning | `memory://` | `s3://` |
|---|---|---|---|
| `fetch` | A result can be read back by reference, so `future.result()` works | yes | yes |
| `delete` | Results can be removed when their task is purged | yes | yes |
| `list` | Published keys can be enumerated, so untracked results can be found and removed | yes | yes |
| `retention` | How long a published result stays readable: a duration, or `until_deleted` | `until_deleted` | `until_deleted` |
| `durable` | A published result survives process loss | no | yes |
| `shared` | Usable from many processes | no | yes |

- This phase ships `memory://` (tests, single process) and `s3://` (any S3-compatible store; `taskwire[s3]` extra; credentials from the standard provider chain, so workload identity works). Redis and Kafka follow in Phase 6.
- A `ResultRef` is small and opaque to the backend: `{channel, key, size, sha256, codec}`. Keys are generated by Taskwire from the task ID and lease ID, so a stale attempt cannot overwrite the result that was recorded.
- The reference is the system's tracking record for the result (Phase 1, S12). The worker adds the publish time and, from the channel's declared `retention`, the expiry time, both by the backend clock. Applications do not track results themselves.
- At start, a channel whose `retention` is shorter than the backend's retention for succeeded tasks is reported by the check: a warning at `settings`, and named in `taskwire check` output, because results would expire while their tasks are still listed.
- `publish` is called from the executor, not through the supervisor's backend connection. No Taskwire process proxies result bytes.
- `fetch` verifies size and SHA-256. A mismatch is `ResultIntegrityError`; a result that has expired or been deleted is `ResultUnavailableError`. Neither changes the task's state.
- The channel is asserted at start like the backend: capabilities, then at `full` level a live publish, fetch, and delete under a reserved key. `memory://` requires `TASKWIRE_ALLOW_EPHEMERAL=true`.
- With no channel configured the worker still runs; tasks must return `None`.
- A channel is supported only if it passes the channel conformance suite: round trip is byte-exact, keys never collide across leases, a failed publish leaves nothing readable, integrity failures are detected, and declared capabilities are true.

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

- Result channel: the conformance suite passes for `memory://` and `s3://`; a publish failure retries the task; a crash between publish and `complete` retries the task and leaves no recorded reference; a stale worker's publish never becomes a task's result; no result byte is ever written to the backend (asserted against the PostgreSQL tables).
- Startup: a worker pointed at a backend that fails the assertion exits non-zero, names the guarantee, and never reports ready.
- Success and exception paths in each mode, with positional and keyword arguments.
- `process` mode: a task that holds the GIL in native code for longer than the liveness period does not cost the worker its liveness.
- Timeout in each mode: the attempt fails with `timeout`, is retried, and the slot is recovered.
- `thread` mode timeout: the attempt is failed and retried while the original function is still running; the start-up warning is emitted.
- Recycling: an executor is replaced after the task limit and after exceeding the memory limit, only while idle, with no attempt consumed and no task lost.
- No prefetch: with one slow task per slot and more tasks queued, a second worker started later receives the queued tasks; the busy worker holds none of them.
- Affinity: with several workers and tasks submitted under a few affinity keys, tasks of one key run on one worker while it has free slots; when that worker is stopped, its keys move to its ring successors only and no task waits longer than the affinity window plus one poll.
- A task that calls `result()` on a task it submitted gets `BlockingInTaskError`, and the worker's other slots keep running.
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
