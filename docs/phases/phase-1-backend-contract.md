# Phase 1 — Backend Contract

## Goal

Define the semantics Taskwire guarantees as a backend-neutral interface, prove them with a conformance suite, and assert them against the live backend every time a process starts. Ship two backends: PostgreSQL for production and in-memory for tests.

This is the most important phase. Every later phase is a caller of this interface, and nothing outside a backend module may depend on a specific database.

## Guaranteed semantics

These are the contract. Each has an identifier so that conformance tests and startup checks can name what they prove.

| ID | Guarantee |
|---|---|
| S1 | **Durable submit.** When `submit` returns, the task survives the loss of any client, worker, or controller process, and of the backend's own restart at the backend's declared durability level |
| S2 | **Idempotent submit.** Submitting the same task ID with the same content any number of times creates one task. The same ID with different content is rejected |
| S3 | **Exclusive claim.** At any moment a task is held by at most one lease |
| S4 | **Eligible claim.** A claim returns only queued tasks in the requested pools, with a requested `name@version`, whose `run_after` has passed. Higher priority is preferred; order within a priority is best effort |
| S5 | **Fencing.** `complete`, `fail`, and `release` take effect only with the task's current lease. With any other lease they change nothing and say so |
| S6 | **Backend clock.** Lease expiry, `run_after`, and backoff are judged by the backend's clock, never the caller's |
| S7 | **Bounded retry.** A retryable failure or a lost lease requeues the task with backoff until `max_attempts`, after which it fails with `max_attempts_exceeded` |
| S8 | **Bounded release.** A release returns the task to the queue without consuming an attempt, up to `max_releases`; further releases consume attempts |
| S9 | **Atomic cancel.** A cancel racing a claim resolves to exactly one of: cancelled before it ran, or cancellation requested of the running attempt |
| S10 | **Terminal is final.** Once a task is `succeeded`, `failed`, or `cancelled`, its state and outcome never change |
| S11 | **Conservation.** Every submitted task is in exactly one state until retention removes it |

Execution is at least once. Nothing here promises exactly-once execution.

## Interface

A Python protocol in `taskwire.backend`. An async twin exposes the same operations as coroutines.

```python
class Backend(Protocol):
    capabilities: Capabilities

    def check(self, level: CheckLevel) -> CheckReport: ...

    def submit(self, tasks: Sequence[NewTask]) -> list[SubmitOutcome]: ...
    def get(self, task_ids: Sequence[TaskID]) -> list[TaskSnapshot]: ...
    def cancel(self, task_id: TaskID) -> CancelOutcome: ...

    def claim(self, worker: WorkerInfo, pools: Sequence[str],
              task_keys: Sequence[str], limit: int) -> list[ClaimedTask]: ...
    def heartbeat(self, worker_id: str, ttl: timedelta) -> Heartbeat: ...
    def complete(self, task_id: TaskID, lease_id: LeaseID, result: Value) -> bool: ...
    def fail(self, task_id: TaskID, lease_id: LeaseID,
             failure: Failure, retryable: bool) -> TaskState | None: ...
    def release(self, task_id: TaskID, lease_id: LeaseID) -> bool: ...

    def wait(self, signal: Signal, timeout: timedelta) -> None: ...

    def reap(self, limit: int) -> int: ...
    def purge(self, policy: RetentionPolicy, limit: int) -> list[ObjectRef]: ...
    def stats(self) -> list[PoolStats]: ...

    def close(self) -> None: ...
```

Notes on the operations:

- **`submit`** takes a batch and is atomic per call.
- **`heartbeat`** is per worker, not per task. It extends the worker's liveness and returns whether the worker is still considered alive and which of its tasks have a cancel request. A worker reported as not alive has lost every lease it held.
- **`complete`, `fail`, `release`** return a negative result, not an exception, when the lease is stale.
- **`wait`** blocks until work or results may be available, or the timeout passes. A backend may implement it as a plain sleep; callers must always re-check after it returns.
- **`reap`** requeues or fails the tasks of workers whose liveness has expired, and tasks past their own `timeout`.
- **`purge`** applies separate retention periods to succeeded, failed, and cancelled tasks.

Errors raised by a backend are limited to `TaskConflictError`, `InvalidArgumentError`, `ValueTooLargeError`, `BackendUnavailableError`, and `BackendCheckError`.

## Capabilities

Each backend declares what it provides. Declarations are checked, not trusted (see the startup assertion).

| Capability | Values | PostgreSQL | In-memory |
|---|---|---|---|
| `durability` | `durable`, `none` | `durable` | `none` |
| `shared` (usable from many processes) | true, false | true | false |
| `backend_clock` | true, false | true | true (injectable for tests) |
| `notify` (`wait` is event-driven) | true, false | optional | true |

A worker, controller, or client refuses to start on a backend with `durability: none` or `shared: false` unless `TASKWIRE_ALLOW_EPHEMERAL=true` is set.

## Startup assertion

Every process calls `backend.check()` before it submits, claims, or reaps anything. A failed check raises `BackendCheckError` naming the guarantee that failed; the process exits non-zero and never reports ready. On Kubernetes that surfaces as a pod that will not become ready, with the reason in its log.

### What is checked

| Level | Checks | Default for |
|---|---|---|
| `settings` | Capabilities meet the requirements above. Schema version is in the SDK's supported range. The connection is writable and has the required privileges. Backend settings that would void a guarantee are rejected | Clients |
| `full` | Everything in `settings`, plus a live probe of the guarantees listed below | Workers, controller, `taskwire check` |
| `off` | Nothing. Logged as a warning at start | Never the default |

Selected by `TASKWIRE_STARTUP_CHECK`.

### The live probe

The probe runs the real operations against the real backend, using tasks in a reserved probe namespace that ordinary claims, statistics, and metrics never see.

| Guarantee | Probe |
|---|---|
| S2 | Submit one task twice; expect one task. Submit the same ID with different content; expect rejection |
| S3 | Claim the probe task from two connections at once; expect exactly one to receive it |
| S5 | Expire the probe lease, re-claim, then complete with the first lease; expect rejection and an unchanged task |
| S6 | Compare the backend clock with the local clock; warn above a small skew, since correctness does not depend on it but log timestamps do |
| S9 | Cancel a queued probe task; expect `cancelled` and that a following claim does not return it |
| S10 | Complete a probe task, then fail it with the same lease; expect no change |

Probe tasks are deleted at the end. Any left behind by a crashed probe are removed by retention.

### What the probe cannot prove

- **Durability (S1).** A process cannot crash the backend to see what survives. For this the check relies on settings. The PostgreSQL backend rejects a server in recovery (a read replica), `fsync = off`, and `synchronous_commit = off`.
- **Correctness under all interleavings.** That is the conformance suite's job, run in CI. The startup probe proves something narrower and still useful: that this deployment, through this connection path, behaves the way the tested implementation assumes. It catches a pooler in the wrong mode, a read replica, missing privileges, and a schema from the wrong version.

`taskwire check` runs the full check on demand and prints the report. The Helm chart runs it after migrations (Phase 5).

## Conformance suite

One parameterised suite, run against every backend. A backend is supported only if it passes.

- S2: idempotent retry, conflict on changed content, batch atomicity.
- S3: many concurrent claimers; each task is claimed exactly once.
- S4: filtering by pool, `name@version`, and `run_after`; priority preference. With many queued tasks that no claimer can run, claim cost stays bounded.
- S5: after liveness expiry and re-claim, the first worker's `complete`, `fail`, and `release` have no effect.
- S6: with the caller's clock skewed far forwards and backwards, expiry behaviour is unchanged.
- S7: backoff schedule, attempt counting, exhaustion.
- S8: releases up to the cap consume no attempt; beyond it they do.
- S9: cancel in each state; cancel racing claim under load.
- S10: every operation against a terminal task leaves it unchanged.
- S11: randomized concurrent load with a seeded schedule; submitted equals queued plus running plus terminal at every check.
- Reap: bounded batches; concurrent reapers do not double-process; task timeout is enforced.
- Purge: per-outcome retention; only terminal tasks are removed.
- The startup check itself: it passes on a correct backend and fails, naming the right guarantee, on deliberately broken ones (a backend with a non-exclusive claim, one that ignores fencing, a PostgreSQL read replica, a PostgreSQL with `synchronous_commit = off`).

A model test runs the same random operation sequence against the in-memory backend and PostgreSQL and requires identical visible results.

## In-memory backend

A direct, lock-protected implementation of the interface in one process, with an injectable clock. It is the reference model for the semantics and the backend for unit tests in every later phase. It is not for production and says so in its capabilities.

## PostgreSQL backend

Supports PostgreSQL 14 and later. Operations are single SQL statements or short transactions issued from Python; there are no stored procedures.

### Data model

A sketch; the migration is authoritative.

```sql
CREATE TABLE taskwire.tasks (
  id               uuid PRIMARY KEY,
  owner_id         uuid NOT NULL,          -- groups a client's tasks; not a security boundary
  batch_id         uuid,
  pool             text NOT NULL,
  task_key         text NOT NULL,          -- 'name@version'
  priority         smallint NOT NULL DEFAULT 0,
  state            text NOT NULL DEFAULT 'queued',
  run_after        timestamptz NOT NULL DEFAULT now(),
  attempt          integer NOT NULL DEFAULT 0,
  max_attempts     integer NOT NULL,
  releases         integer NOT NULL DEFAULT 0,
  timeout_seconds  integer,
  retry_backoff    jsonb NOT NULL,
  content_hash     bytea NOT NULL,
  worker_id        text,
  lease_id         uuid,
  cancel_requested boolean NOT NULL DEFAULT false,
  failure          jsonb,
  trace_context    text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  finished_at      timestamptz
);

CREATE TABLE taskwire.task_values (       -- inputs and results, kept off the hot table
  task_id  uuid NOT NULL,
  kind     text NOT NULL,                 -- 'input' | 'result'
  codec    text NOT NULL,
  inline   bytea,
  ref      jsonb,                         -- object reference (Phase 6)
  PRIMARY KEY (task_id, kind)
);

CREATE TABLE taskwire.workers (
  worker_id   text PRIMARY KEY,
  pools       text[] NOT NULL,
  task_keys   text[] NOT NULL,
  sdk_version text NOT NULL,
  started_at  timestamptz NOT NULL,
  alive_until timestamptz NOT NULL
);
```

Design points:

- **Liveness is per worker.** A heartbeat updates one `workers` row, however many tasks the worker holds. Task rows are written at submit, claim, and finish only. A lease is lost when its worker's `alive_until` passes and the reaper acts, or when the task's own timeout passes.
- **Fencing is per task.** Each claim writes a new `lease_id`; outcome writes are conditional on it.
- **Values live in a separate table**, so the frequently updated task rows stay narrow.
- **Claiming** uses `FOR UPDATE SKIP LOCKED` over a partial index on queued rows keyed by pool and `task_key`, so tasks that no worker can run are not scanned.
- **Inline values** are limited by `TASKWIRE_INLINE_LIMIT_BYTES` (default 256 KiB). Until Phase 6, larger values are rejected.

### Connections

- Polling is the default wake-up mechanism, so the backend works through a transaction-mode connection pooler with one connection per worker.
- `LISTEN/NOTIFY` is optional (`TASKWIRE_PG_NOTIFY=true`). It costs each process one extra direct connection and is intended for small deployments that want lower latency.

### Migrations and versions

- `taskwire migrate` applies numbered, transactional migrations under an advisory lock.
- Within a minor version, migrations are additive, so SDK versions N and N+1 can run against the same schema during a rolling deploy. The conformance suite is run with the previous SDK version against the current schema.
- Upgrade order: database, then controller, workers, and clients.

### Access control

A single application role with read and write access to the `taskwire` schema is the supported setup. Finer-grained roles are a hardening topic for Phase 7, not part of the contract.

## Exit gate

Both backends pass the conformance suite, repeatedly and under load, on the oldest and newest supported PostgreSQL versions. The startup check passes on a correct deployment and fails with the right guarantee named on each deliberately broken one. No module outside `taskwire.backend.postgres` imports the database driver.
