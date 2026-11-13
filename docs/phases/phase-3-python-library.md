# Phase 3 — Python Library

## Goal

The first Taskwire library: one Python package that an application embeds to submit tasks, run tasks, and read results. It speaks only the controller API (Phase 2). It holds no database credential and contains no database driver.

There is no separate "client" and "worker". A process that only submits and a process that runs tasks use the same object; the difference is whether it serves any pools.

## Public API

```python
from datetime import timedelta
from taskwire import Taskwire, task

@task(name="reports.render", version="1", pool="cpu", max_attempts=5, timeout=300)
def render(doc_id: str, *, fmt: str = "pdf") -> dict: ...

@task(name="embeddings.fetch", version="1", pool="io")
async def fetch(text: str) -> list[float]: ...

tw = Taskwire()                                   # reads TASKWIRE_CONTROLLER_URL

# Submit, from any process
future = tw.submit(render, "doc-1", fmt="pdf")
futures = tw.map(render, ["a", "b", "c"])
tw.submit(render, "doc-2", _run_after=timedelta(minutes=5), _priority=10)
value = future.result(timeout=60)

# Run tasks, in this process
tw.serve(pools=["cpu"], mode="process")           # returns at once; tasks run in the background
...
tw.drain()                                        # stop claiming, finish or release, disconnect
```

A dedicated worker pod runs the same thing from the command line:

```bash
python -m taskwire run myapp.tasks --pools cpu --mode process --processes 4
python -m taskwire run myapp.tasks --pools io  --mode async --concurrency 100
```

### Submitting

- `submit(task, *args, **kwargs)` returns a `TaskFuture` once the controller has made the task durable. Per-call options are underscore-prefixed so they cannot collide with task arguments: `_pool`, `_priority`, `_run_after`, `_max_attempts`, `_timeout`, `_idempotency_key`, `_affinity`.
- The task ID is a UUIDv7 generated before the request. If the response is lost, the library retries the same ID, which is safe because submit is idempotent. `_idempotency_key` is for deduplication the application wants across its own retries and restarts.
- `_affinity="some-key"` asks that tasks with the same key prefer the same pod. It is a preference only: no ordering, no exclusivity, and a bounded wait (Phase 1, "Buckets and loose affinity").
- `map(task, iterable)` submits in batches of `TASKWIRE_SUBMIT_BATCH_SIZE` (default 1000) under one `batch_id`. If a later batch fails, the error carries the futures already submitted.
- Arguments are encoded and checked against the input limit before the request is sent.
- When the controller refuses a submit for rate or backlog, the library waits the delay it was given and retries, up to the call's deadline (`_submit_timeout`, default 30 s), then raises `SubmitLimitedError` naming the scope and reason. `map` therefore slows to the permitted rate instead of failing; a caller that would rather fail at once passes `_submit_timeout=0`.
- The library honours the pacing delay the controller returns with each submit, so `map` slows smoothly as a queue fills. `tw.pressure(pool, task=None)` returns the current pressure (0 to 1, or `None` if the controller has no basis for one) for applications that want to stop producing work upstream.
- An `AsyncTaskwire` exposes the same operations as coroutines.

### `TaskFuture`

`result(timeout=None)`, `exception(timeout=None)`, `wait(timeout=None)`, `done()`, `cancel()`, `result_info()`, `state`, `task_id`.

- `wait()` and `done()` ask the controller. Waiting uses the API's long poll, so it does not poll on a timer.
- `result()` waits, asks the controller where the result is, downloads it from the result channel, and verifies its size and checksum. The controller never returns a value.
- `result_info()` returns the tracking record without downloading: `state` (`none`, `available`, `expired`), size, published and expiry times. `result()` on an expired record raises `ResultUnavailableError` without touching the channel.
- `cancel()` returns `"cancelled"`, `"requested"`, or `"too_late"`.
- A future can be rebuilt in another process from the task ID: `tw.reattach(task_ids)` or `tw.reattach_batch(batch_id)`.
- `gather(futures, timeout=None, return_exceptions=False)` returns values in submission order; `as_completed(futures, timeout=None)` yields futures as they finish. Downloads run concurrently in bounded batches.

### Running tasks

- `serve(pools, mode)` registers the decorated tasks it has imported, opens a worker session, and starts claiming. **`mode` has no default when embedding**: how tasks share a process with the application is a choice the application must make knowingly, and the error raised when it is missing states the recommendation and the trade-off. A duplicate `name@version` bound to a different function is an error.
- The library asks for only as many tasks as it has free slots, and the controller enforces that as well.
- A background thread sends the heartbeat every third of the liveness period, with jitter. If the controller reports the instance as no longer alive, the library abandons its running attempts and discards their outcomes.
- Each heartbeat includes a status report (slots, in flight, draining, mode, memory, child-process restarts, outcome counts), so the pod is observable from the controller even when the application exposes no metrics endpoint for it.
- If the controller is unreachable, running tasks continue, claiming stops, and finished outcomes are held and reported on reconnect; the controller decides whether they still count.
- Inside a running task, `result()`, `wait()`, `gather()`, and `as_completed()` raise `BlockingInTaskError`: a slot waiting on work that needs a slot is how pools deadlock. `submit`, `done()`, `state`, and `result_info()` are allowed. A task that checks other tasks' state repeatedly is doing the same thing by hand, so more than ten such checks in one attempt logs a warning and counts in `taskwire_library_polling_in_task_total`; it is not an error, because the library cannot tell a loop from legitimate checks. `TASKWIRE_ALLOW_BLOCKING_IN_TASK=true` turns the error into a logged warning.
- `taskwire.current_task()` gives the running task its ID, attempt number, `cancel_requested`, and `abandoned`. `abandoned` turns true when the attempt has timed out or lost its lease but the code is still running, which can happen in `thread` and `async` modes. **From that moment the task may be running a second time elsewhere**; long-running code should check the flag and stop.
- `future.retry()` creates a new task from a failed or cancelled one and returns its future. The original is unchanged.

## Deployment choices

How to run tasks is the application's decision. Taskwire supports every combination below, states what it recommends, and enforces none of it. **No choice here weakens a guarantee**: the controller enforces them, so the worst a choice can cause is a retry.

| Choice | Recommended | Also supported | What the alternative costs |
|---|---|---|---|
| Where tasks run | Dedicated worker Deployments that serve pools; application pods serve none | `serve()` inside application pods | Tasks compete with requests for CPU and memory, and the pods scale with traffic rather than queue depth |
| How tasks run | `process` mode for tasks that need a hard timeout or are CPU-heavy | `thread` or `async` mode in the application's own process | A hung task cannot be stopped, and code that blocks the interpreter can delay the heartbeat until the pod is taken for dead and its tasks run again elsewhere |
| Shutdown | In `python -m taskwire run`, the library handles `SIGTERM` | The application calls `drain()` from its own shutdown path | The application owns the ordering and the time budget |
| Health and metrics | In `python -m taskwire run`, the library serves its own port | The application mounts `tw.health()` and `tw.metrics()` in its own server | Probes share fate with the application |

The same image works either way. Moving from embedded to dedicated is a change to the Deployment, not to the code. At start the library logs the mode it is running in, and warns once for each task registered with a `timeout` in a mode that cannot enforce it.

### Execution modes

**`process` is the pre-alpha mode.** `thread` and `async` are stage two; until then `serve()` and `python -m taskwire run` accept only `process`.

| Mode | Where tasks run | Slots | Timeout |
|---|---|---|---|
| `process` | Child processes started with `spawn`; the parent only claims, heartbeats, and reports | 1 per child | Enforced: the child is killed and replaced |
| `async` | Coroutines on an event loop in this process | `concurrency` | The coroutine is cancelled; one that never yields keeps running |
| `thread` | Threads in this process | `concurrency` | Not enforceable: the attempt is failed and retried elsewhere while the function keeps running and can still cause side effects |

- `async def` tasks are served only in `async` mode, synchronous tasks only in `process` or `thread` mode, so a process never claims a task it cannot run.
- In `process` mode a child is recycled after `TASKWIRE_MAX_TASKS_PER_PROCESS` tasks or above `TASKWIRE_MAX_MEMORY_PER_PROCESS_MB`, only between tasks, at no cost in attempts. A child that dies fails its attempt as `worker_process_died` and is replaced.
- In `process` mode the heartbeat cannot be starved by task code, because task code runs in another process. That is the main reason it is the recommendation.

### Settings

| Setting | Environment variable | Default |
|---|---|---|
| Controller address | `TASKWIRE_CONTROLLER_URL` | required |
| API token | `TASKWIRE_API_TOKEN` | required unless the controller runs without authentication |
| Pools to serve | `TASKWIRE_POOLS` | none (submit only) |
| Execution mode | `TASKWIRE_MODE` | `process` under `python -m taskwire run`; required under `serve()` |
| Child processes | `TASKWIRE_PROCESSES` | CPU count |
| Tasks per process (`thread`, `async`) | `TASKWIRE_CONCURRENCY` | 16 |
| Instance ID | `TASKWIRE_INSTANCE_ID` | hostname (the pod name) |

Liveness period, input limit, and result-channel settings come from the controller at `Hello`, so every pod agrees with it.

## Execution contract

1. Decode the input with the codec recorded on the task.
2. Call the function once for this attempt.
3. Encode the return value. If it is not `None`, ask the controller where to put it and upload it.
4. Report the outcome. The upload comes first, so a task is never `succeeded` with a reference to something that was not written; a crash in between leaves an orphan in the channel and a task that is retried.

| Situation | Failure code | Retryable |
|---|---|---|
| Function raises | `task_exception` | Yes, unless the exception type is in the task's `no_retry` |
| Input cannot be decoded, or result cannot be encoded | `serialization_error` | No |
| Result upload fails | `result_publish_failed` | Yes |
| Task returns a value but no result channel is configured | `no_result_channel` | No |
| Result exceeds the channel's size limit | `result_too_large` | No |
| Child process dies | `worker_process_died` | Yes |
| Task exceeds its timeout | `timeout` | Yes |
| Attempts run out (decided by the controller) | `max_attempts_exceeded` | — |

A failure records the exception type, message, and a bounded traceback. It never records argument values.

## Results

- **S3.** The library uploads to and downloads from short-lived signed links issued by the controller, with plain HTTP. It contains no S3 client and the pod holds no storage credential.
- **Redis (stage two).** The library writes and reads the key the controller names, using `TASKWIRE_RESULT_REDIS_URL` from the pod's environment (`taskwire[redis]` extra). Results carry a time-to-live (`TASKWIRE_RESULT_TTL_SECONDS` on the controller, default 24 h) and a size limit (default 1 MiB). An expired result raises `ResultUnavailableError`; the task remains `succeeded`.
- **Memory.** Single process, for tests.
- With no channel configured, tasks must return `None`.

## Serialization

- The default codec is `msgpack`, carrying `{args, kwargs}` and the return value. It is the portable codec: a task submitted from one language can be run by a library in another.
- `cloudpickle` is opt-in, Python-only, and for installations where every submitter is trusted (`TASKWIRE_ALLOW_PICKLE=true` on the pods that run tasks).
- The codec name travels with each value; a process rejects a codec it has not enabled with `serialization_error`.

## Shutdown and drain

`drain(timeout)` stops claiming, lets running tasks finish within the timeout, releases what is left (which costs no attempt, up to `max_releases`), and says `Goodbye` so the controller reassigns the instance's buckets at once. Under `python -m taskwire run`, `SIGTERM` calls it with the pod's termination grace period less a margin.

## Errors

| Exception | Meaning |
|---|---|
| `TaskFailedError` | The task reached `failed`; carries the failure code, remote exception type, message, and traceback text |
| `TaskCancelledError` | The task reached `cancelled` |
| `TaskConflictError` | An idempotency key was reused with different content |
| `TaskNotFoundError` | Unknown or purged ID |
| `BlockingInTaskError` | A task tried to block on another task's outcome |
| `SerializationError` | A value could not be encoded or decoded |
| `InputTooLargeError` | The encoded arguments exceed the input limit |
| `SubmitLimitedError` | The controller's submission limit for the pool or work type was still in force at the call's deadline |
| `ResultUnavailableError` | The task succeeded but its result has expired or been deleted |
| `ResultIntegrityError` | The downloaded result does not match its recorded size or checksum |
| `ControllerUnavailableError` | The controller could not be reached within the operation's deadline |
| `ProtocolError` | The controller does not support this library's protocol revision |

All derive from `TaskwireError`. Messages never include argument or result values.

## Packaging

- `pip install taskwire` is pure Python and depends on a gRPC runtime and `msgpack`. It does not depend on a database driver.
- The package does not contain the controller. The controller is the Go `taskwire` binary, distributed as a container image and release archives (Phase 5).
- For local development and tests, `taskwire controller` with the in-memory backend is one process with no dependencies; the library's own test suite starts it that way.

## Required tests

Run against a real controller binary: on the in-memory backend for speed, and on PostgreSQL end to end.

- `taskwire library-check` passes (Phase 2).
- Submit and result for success, failure, and cancellation, sync and async.
- `map` of 10,000 tasks: batched, one `batch_id`, `gather` order preserved. `as_completed` yields in completion order and honours its timeout.
- A lost response during submit, followed by the retry, yields exactly one task. Two submits with one idempotency key return the same task; different arguments raise `TaskConflictError`.
- Submission limits: `map` against a rate-limited work type completes at about the permitted rate with no error; with `_submit_timeout=0` it raises `SubmitLimitedError`; a submit refused for backlog succeeds once the queue drains.
- Back pressure: `map` into a pool with stopped workers slows as pressure rises and blocks at the limit, resuming when workers start; `tw.pressure()` tracks the controller's value.
- Restart: `reattach` by IDs and by batch resolves running and finished tasks.
- Each execution mode: success and exception paths with positional and keyword arguments.
- Timeouts: enforced in `process` mode with the slot recovered; in `thread` mode the attempt is failed and retried while the function is still running, and the start-up warning is emitted.
- `process` mode: a task holding the interpreter in native code for longer than the liveness period does not cost the instance its liveness. `thread` mode: the same task does, its attempts run elsewhere, and its late outcomes are rejected.
- A child process killed mid-task; the whole process killed mid-task; the process paused with `SIGSTOP` past the liveness period.
- Recycling by task count and by memory, only while idle, with no attempt consumed.
- No prefetch: with every slot busy and more tasks queued, a second instance started later receives the queued tasks.
- Affinity: tasks under one key run on one instance while it has free slots; when it stops, they move without waiting longer than the affinity window plus one poll.
- A task that calls `result()` on a task it submitted gets `BlockingInTaskError`, and other slots keep running.
- `serve()` without a mode raises an error that names the choices. In `thread` mode a timed-out function sees `current_task().abandoned` become true while its retry runs elsewhere.
- `future.retry()` on a failed task returns a new future; on a running task it raises.
- Embedded use: `serve()` inside a process that is also serving HTTP; `drain()` called from the application's shutdown path releases unfinished tasks without consuming attempts. A task released more than `max_releases` times starts consuming attempts.
- Rolling deploy: an instance registered for `v1` only never receives `v2` tasks.
- Results on S3 through signed links and on Redis: byte-exact round trip; a corrupted download raises `ResultIntegrityError`; an expired Redis result raises `ResultUnavailableError`; an upload failure retries the task. The pod's environment contains no storage credential in the S3 case.
- Controller outage shorter than the liveness period loses nothing; a longer one leads to retries and rejected late outcomes.
- Thread-safety stress: concurrent submit, wait, and cancel. `drain()` leaves no threads, child processes, or connections behind.
- End to end from a clean wheel, with the source tree unreachable.

## Exit gate

From a wheel installed in a clean environment, one process submits and another runs tasks through a replicated controller on PostgreSQL, in all three modes and both embedded and dedicated; the library passes `taskwire library-check`; and the crash, pause, outage, and drain tests pass repeatedly.
