# Phase 3 — Client SDK

## Goal

The public Python API for submitting work and collecting results: `Client`, `TaskFuture`, and the fan-out operations `map`, `gather`, and `as_completed`.

## Public API

```python
from datetime import timedelta
from taskwire import Client, task

@task(name="reports.render", version="1", pool="cpu")
def render(doc_id: str) -> dict: ...

with Client() as tw:                                  # reads TASKWIRE_BACKEND_URL
    future = tw.submit(render, "doc-1")
    value = future.result(timeout=60)

    futures = tw.map(render, doc_ids)                 # one batched insert
    for done in tw.as_completed(futures, timeout=600):
        handle(done.result())

    values = tw.gather(futures, timeout=600)          # ordered as submitted

    tw.submit(render, "doc-2", _run_after=timedelta(minutes=5), _priority=10)
```

- Creating a `Client` runs the backend startup assertion at the `settings` level (Phase 1) and raises `BackendCheckError` if it fails.
- `submit(task, *args, **kwargs)` returns a `TaskFuture` once the task is durable. Per-call options are underscore-prefixed keywords so they cannot collide with task arguments: `_pool`, `_priority`, `_run_after`, `_max_attempts`, `_timeout`, `_task_id`, `_affinity`.
- `_affinity="some-key"` asks that tasks with the same key prefer the same worker pod, for warm caches or loaded models. It is a preference only (Phase 1, "Buckets and loose affinity"): it promises neither ordering nor exclusivity, and a task waits at most the affinity window for its preferred pod.
- `map(task, iterable)` inserts in batches of `TASKWIRE_SUBMIT_BATCH_SIZE` (default 1000) and tags all tasks with one `batch_id`. Each batch is one transaction; if a later batch fails, the raised error carries the futures already submitted.
- `gather(futures, timeout=None, return_exceptions=False)` returns values in submission order, fetching each from the result channel.
- `as_completed(futures, timeout=None)` yields futures as they finish.
- An async variant, `AsyncClient`, exposes the same operations as coroutines.

## `TaskFuture`

`result(timeout=None)`, `exception(timeout=None)`, `done()`, `cancel()`, `state`, `task_id`.

- `result()` waits for the task's state in the backend, then fetches the value from the result channel. The backend never returns a value. It raises `TimeoutError` on timeout without changing the task.
- `wait(timeout=None)` and `done()` need only the backend, and work with any channel or none.
- `result_info()` returns the tracking record without fetching: `state` (`none`, `available`, `expired`), channel, size, published and expiry times. It needs only the backend. `result()` on an expired record raises `ResultUnavailableError` without calling the channel.
- `cancel()` returns `"cancelled"`, `"requested"`, or `"too_late"`, matching the backend operation.
- Inside a running task, `result()`, `wait()`, `gather()`, and `as_completed()` raise `BlockingInTaskError` (Phase 2). `submit`, `done()`, `state`, and `result_info()` are allowed, so a task can start follow-up work without waiting on it.
- A future is a handle to a row. It can be rebuilt in another process from the task ID.

## Waiting for results

- Waiting watches task state in the backend; values are fetched from the result channel only for tasks that succeeded, concurrently and in bounded batches.
- On a channel without the `fetch` capability (Kafka), `result()` and `gather()` raise `ResultNotFetchableError`; state, `wait()`, and `as_completed()` still work, and consumers read the results from the stream.

- The client polls pending IDs with backoff, using `Backend.wait` between polls. On a backend with the `notify` capability the wait returns early when results arrive; correctness never depends on it.
- Pending IDs are fetched in bounded batches, so waiting on 100,000 futures does not issue 100,000 queries.

## Reattach

```python
futures = tw.reattach(task_ids)       # or tw.reattach_batch(batch_id)
```

Reattach needs nothing but IDs. An ID that does not exist, or whose row has been purged after retention, raises `TaskNotFoundError`.

## Errors

| Exception | Meaning |
|---|---|
| `TaskFailedError` | The task reached `failed`; carries the failure code, remote exception type, message, and traceback text |
| `TaskCancelledError` | The task reached `cancelled` |
| `TaskConflictError` | A task ID was reused with different content |
| `TaskNotFoundError` | Unknown or purged ID on reattach |
| `BlockingInTaskError` | A task tried to block on another task's outcome |
| `SerializationError` | A value could not be encoded or decoded |
| `InputTooLargeError` | The encoded arguments exceed `TASKWIRE_MAX_INPUT_BYTES` |
| `ResultUnavailableError` | The task succeeded but its result has expired or been deleted from the channel |
| `ResultIntegrityError` | The fetched result does not match its recorded size or checksum |
| `ResultNotFetchableError` | The configured channel is a stream, or the client has no channel configured |
| `BackendUnavailableError` | The backend could not be reached within the operation deadline |
| `BackendCheckError` | The startup assertion failed; names the guarantee or setting |

All derive from `TaskwireError`. Messages never include argument or result values.

## Submit semantics

- The task ID is generated before the insert. If the connection drops before the commit is confirmed, the outcome is unknown; the client retries the same ID, which is safe because submit is idempotent.
- `submit` checks that arguments encode and fit the input limit before touching the database.
- A client is safe to share across threads. With the PostgreSQL backend it uses a small connection pool (`TASKWIRE_POOL_SIZE`, default 4).

## Required tests

- Submit and result for success, failure, and cancellation, sync and async.
- `map` of 10,000 tasks: batched inserts, one `batch_id`, `gather` order preserved.
- `as_completed` yields in completion order and honours its timeout.
- Connection drop during submit, followed by retry, yields exactly one task.
- Client restart: `reattach` by IDs and by batch resolves running and already-finished tasks.
- Every future resolves with notification disabled, and with it enabled.
- The whole suite passes on the in-memory backend and on PostgreSQL.
- Cancel while queued, while running (the worker sees the flag), and after completion.
- Called inside a task, `result()`, `wait()`, `gather()`, and `as_completed()` raise `BlockingInTaskError`; `submit` works.
- Thread-safety stress: concurrent submit, wait, and cancel from many threads.
- Close leaves no threads or connections behind.
- End to end from a clean wheel: client and worker in separate processes against PostgreSQL.

## Exit gate

A clean installed wheel runs `submit`, `map`, `gather`, and `reattach` end to end against a real worker, and every future reaches a bounded outcome in the tested failure cases.
