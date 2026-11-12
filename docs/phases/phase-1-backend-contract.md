# Phase 1 — Backend Contract

## Goal

Define the semantics Taskwire guarantees as a backend-neutral interface, prove them with a conformance suite, and assert them against the live backend every time a process starts. Ship two backends: PostgreSQL for production and in-memory for tests.

This is the most important phase. Every later phase is a caller of this interface, and nothing outside a backend module may depend on a specific database.

## Guaranteed semantics

These are the contract. Each has an identifier so that conformance tests and startup checks can name what they prove.

| ID | Guarantee |
|---|---|
| S1 | **Durable submit.** When `submit` returns, the task survives the loss of any client, worker, or controller process, and of the backend's own restart at the backend's declared durability level |
| S2 | **Idempotent submit.** Submitting the same task ID, or the same idempotency key, with the same content any number of times creates one task and reports that task's ID. The same ID or key with different content is rejected. A key is remembered for as long as its task is retained |
| S3 | **Exclusive claim.** At any moment a task is held by at most one lease |
| S4 | **Eligible claim.** A claim returns only queued tasks in the requested pools, with a requested `name@version`, whose `run_after` has passed. Higher priority is preferred; order within a priority is best effort |
| S5 | **Fencing.** `complete`, `fail`, and `release` take effect only with the task's current lease. With any other lease they change nothing and say so |
| S6 | **Backend clock.** Lease expiry, `run_after`, and backoff are judged by the backend's clock, never the caller's |
| S7 | **Bounded retry.** A retryable failure or a lost lease requeues the task with backoff until `max_attempts`, after which it fails with `max_attempts_exceeded` |
| S8 | **Bounded release.** A release returns the task to the queue without consuming an attempt, up to `max_releases`; further releases consume attempts |
| S9 | **Atomic cancel.** A cancel racing a claim resolves to exactly one of: cancelled before it ran, or cancellation requested of the running attempt |
| S10 | **Terminal is final.** Once a task is `succeeded`, `failed`, or `cancelled`, its state and outcome never change |
| S11 | **Conservation.** Every submitted task is in exactly one state until retention removes it |
| S12 | **Result tracking.** A succeeded task has exactly one result record: either "no result" or the reference published under the lease that completed it. The record is written atomically with the transition to `succeeded`, never changes afterwards, and is removed only with the task. The backend tracks where a result is and until when; it never holds the result |

Execution is at least once. Nothing here promises exactly-once execution.

### Results never enter the backend

Submitting a task is asynchronous, and that is the protocol: `submit` returns when the task is durable and never hands a value back. The backend is the control plane. It stores task state, inputs, and failures. It does not store, carry, or coordinate task outputs — neither between tasks nor back to the submitter.

- A worker publishes a task's return value to the **result channel** (Phase 2: S3, Redis, Kafka, or memory) and then calls `complete` with the reference the channel returned. Publication is asynchronous to the backend: the backend never sees the bytes.
- A reader learns from the backend only that a task succeeded, and where its result was published. It fetches the value from the channel itself.
- No method in this interface reads, waits on, joins, or streams an output, and no backend may add one.
- **Tracking results is a system capability, and it lives here.** The backend does not hold results, but it always knows about them: for every succeeded task it records whether a result exists, which channel holds it, its key, size, checksum, codec, when it was published, and when the channel will let it expire (S12). `get` returns this record with the task snapshot, so any process can answer "is there a result, where, and is it still there?" without touching the channel.
- Whether the bytes can be read back is still the channel's contract. The record makes the answer predictable: a result past its recorded expiry is reported as expired by the backend clock, not discovered by a failed fetch.

See `docs/architecture.md`, "Control plane and data plane".

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
    def complete(self, task_id: TaskID, lease_id: LeaseID,
                 result: ResultRef | None) -> bool: ...
    def fail(self, task_id: TaskID, lease_id: LeaseID,
             failure: Failure, retryable: bool) -> TaskState | None: ...
    def release(self, task_id: TaskID, lease_id: LeaseID) -> bool: ...

    def wait(self, signal: Signal, timeout: timedelta) -> None: ...

    def reap(self, limit: int) -> int: ...
    def purge(self, policy: RetentionPolicy, limit: int) -> list[ResultRef]: ...
    def stats(self) -> list[PoolStats]: ...

    def close(self) -> None: ...
```

Notes on the operations:

- **`submit`** takes a batch and is atomic per call.
- **Task IDs are UUIDv7**, generated by the SDK before the insert, so a retry after an uncertain commit is safe and IDs sort by creation time. A caller never chooses a task ID. A caller that needs its own deduplication supplies an **idempotency key** (`NewTask.idempotency_key`, any string up to 256 bytes); `SubmitOutcome` then carries the ID of the task that key already created, if any. The backend rejects an ID whose embedded time is more than a day from the backend clock with `InvalidArgumentError` naming clock skew, so a pod with a bad clock cannot misfile tasks.
- **`claim`** is not a prefetch. A caller passes as `limit` the number of execution slots it has free right now, and every task returned starts running at once. The interface has no way to reserve work for later, so a busy worker never holds tasks an idle worker could run, and a dead worker loses only tasks that were executing.
- **`claim`** prefers the caller's own buckets (see "Buckets and loose affinity"). The preference never crosses a priority level and is never a guarantee.
- **`heartbeat`** is per worker, not per task. It extends the worker's liveness and returns the buckets the worker currently owns, whether the worker is still considered alive and which of its tasks have a cancel request. A worker reported as not alive has lost every lease it held.
- **`complete`, `fail`, `release`** return a negative result, not an exception, when the lease is stale.
- **`complete`** records that the task succeeded and, at most, a small opaque `ResultRef` (bounded at 1 KiB). It never receives the result value; see "Results never enter the backend" below.
- **`purge`** removes tasks older than the retention period, oldest first, and only when they are terminal. It is resumable and returns the result references of what it is removing, so the caller can delete them from the result channel. A task that is still queued or running, such as one scheduled far ahead, is never removed however old it is.
- **`wait`** blocks until work or state changes may be available, or the timeout passes. A backend may implement it as a plain sleep; callers must always re-check after it returns.
- **`reap`** requeues or fails the tasks of workers whose liveness has expired, and tasks past their own `timeout`.
- **`stats`** reports, per pool and `name@version`, queued tasks that no live worker has registered for, so stranded work is visible rather than silently waiting.
- Retention is a single period for all outcomes. How long a result stays readable is the result channel's retention, which is tracked separately (S12).

Errors raised by a backend are limited to `TaskConflictError`, `InvalidArgumentError`, `InputTooLargeError`, `BackendUnavailableError`, and `BackendCheckError`.

## Buckets and loose affinity

Every task belongs to a **bucket**, and every bucket has a preferred worker. This gives a task a loose affinity to a pod, and keeps workers from competing for the same rows, without ever tying a task to a pod.

**Two levels of indirection, like a virtual-method table.** A task never names a worker. It names a bucket, and a small table says who owns that bucket now:

```text
  task ──hash──►  bucket (fixed for the task's life)  ──bucket table──►  owner (changes with the pods)
```

Reassigning work when pods come and go rewrites entries in the bucket table. It never touches a task.

- **Buckets.** Each pool has 1024 buckets. A task's bucket is a stable hash of its affinity key if it was submitted with one (`NewTask.affinity`), otherwise of its task ID, which spreads tasks evenly. The hash is fixed by this contract (the first 8 bytes of SHA-256), so every SDK version and every backend agrees.
- **Ownership by consistent hashing.** Live workers of a pool sit at points on a hash ring (`TASKWIRE_RING_POINTS` per worker, default 8); a bucket belongs to the first worker point at or after its own position. When a worker joins, it takes buckets only from the workers that follow its points on the ring. When it leaves or is reaped, its buckets pass only to those successors. No other worker's buckets move.
- **The bucket table** is maintained by the backend, in the same transaction that registers a worker, removes one on clean shutdown, or reaps one whose liveness expired. Only entries whose owner changes are written. All workers read the same table, so they agree on ownership; nothing is computed independently in each pod.
- **Loose, not strict.** A worker claims from its own buckets first. If it still has free slots, it claims any eligible task that has been claimable for longer than `TASKWIRE_AFFINITY_WAIT_SECONDS` (default 5), whatever its bucket. A busy, slow, or dead owner therefore delays a task by at most that window; it never strands it.
- **What affinity is not.** It is not ordering: tasks with the same key can run concurrently and out of order. It is not exclusivity: after the window any worker may run the task. It is not a guarantee: S1–S12 hold with the bucket table empty, stale, or wrong, and no caller may depend on affinity for correctness.

A larger `TASKWIRE_RING_POINTS` evens out how many buckets each worker owns but means a membership change touches more neighbours; a smaller one does the opposite.

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
| S12 | Complete a probe task with a reference; read it back unchanged. Complete with a stale lease and a different reference; expect the recorded reference unchanged |

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
- Buckets: a task's bucket is identical on both backends and stable across retries and releases. An owner with free slots claims its own buckets' tasks first. With the owner busy, stopped, or absent, another worker claims the task once the affinity window has passed. A worker joining or leaving changes ownership only of buckets adjacent to its ring points; every other entry in the bucket table is untouched. With the bucket table deliberately corrupted, every guarantee still passes.
- S5: after liveness expiry and re-claim, the first worker's `complete`, `fail`, and `release` have no effect.
- S6: with the caller's clock skewed far forwards and backwards, expiry behaviour is unchanged.
- S7: backoff schedule, attempt counting, exhaustion.
- S8: releases up to the cap consume no attempt; beyond it they do.
- S9: cancel in each state; cancel racing claim under load.
- S10: every operation against a terminal task leaves it unchanged.
- S11: randomized concurrent load with a seeded schedule; submitted equals queued plus running plus terminal at every check.
- S12: two workers race to complete one task with different references; exactly one is recorded and it belongs to the lease that won. A reference over the size bound is rejected.
- Reap: bounded batches; concurrent reapers do not double-process; task timeout is enforced.
- Purge: only terminal tasks past retention are removed; an old task that is still queued or running survives, with its input; result references are returned before their tasks disappear; an interrupted purge resumes without skipping references.
- S2 with keys: the same idempotency key submitted concurrently from many clients creates one task and every caller receives its ID; the same key with different content is rejected; a key becomes reusable only after its task has been purged.
- Task IDs: an ID that is not UUIDv7, or whose time is more than a day from the backend clock, is rejected.
- Churn benchmark (PostgreSQL): a sustained run of at least ten million tasks through submit, claim, finish, and purge. Claim latency and the size of `ready` and `running` at the end stay within a stated factor of their values after warm-up, and retention removes data by dropping partitions, with no row-by-row delete of `tasks`. The measured sustained throughput is recorded in this document as the supported figure.
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
  id               uuid PRIMARY KEY,       -- UUIDv7: sorts by creation time
  owner_id         uuid NOT NULL,          -- groups a client's tasks; not a security boundary
  batch_id         uuid,
  pool             text NOT NULL,
  task_key         text NOT NULL,          -- 'name@version'
  priority         smallint NOT NULL DEFAULT 0,
  bucket           smallint NOT NULL,      -- hash of the affinity key, else of the id
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
  result_ref       jsonb,                  -- tracking record: channel, key, size, sha256, codec; never the result
  result_published_at timestamptz,
  result_expires_at   timestamptz,         -- null: kept until the task is purged
  trace_context    text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  finished_at      timestamptz
) PARTITION BY RANGE (id);               -- one partition per day of creation

CREATE TABLE taskwire.task_inputs (       -- kept off the hot table; there is no results table
  task_id  uuid PRIMARY KEY,
  codec    text NOT NULL,
  payload  bytea NOT NULL
) PARTITION BY RANGE (task_id);           -- same daily ranges as tasks

CREATE TABLE taskwire.idempotency_keys (  -- only tasks submitted with a key have a row
  key          text PRIMARY KEY,
  task_id      uuid NOT NULL,
  content_hash bytea NOT NULL
);
CREATE INDEX ON taskwire.idempotency_keys (task_id);

-- Work indexes. Internal to the backend: no read by a client ever touches them.
CREATE TABLE taskwire.ready (             -- one narrow row per claimable task
  pool       text NOT NULL,
  bucket     smallint NOT NULL,
  task_key   text NOT NULL,
  priority   smallint NOT NULL,
  run_after  timestamptz NOT NULL,
  task_id    uuid PRIMARY KEY
);
CREATE INDEX ON taskwire.ready (pool, bucket, task_key, priority DESC, run_after);

CREATE TABLE taskwire.running (           -- one narrow row per in-flight task
  task_id    uuid PRIMARY KEY,
  worker_id  text NOT NULL,
  lease_id   uuid NOT NULL,
  deadline   timestamptz                  -- the task's own timeout, if any
);
CREATE INDEX ON taskwire.running (worker_id);

CREATE TABLE taskwire.bucket_owners (     -- the bucket table: who is preferred for each bucket
  pool       text NOT NULL,
  bucket     smallint NOT NULL,
  worker_id  text NOT NULL,
  PRIMARY KEY (pool, bucket)
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
- **Inputs live in a separate table**, so the frequently updated task rows stay narrow. **Results are not stored at all**; a succeeded task row holds only its `result_ref`.
- **One table to read, two to work from.** `tasks` holds everything about a task in one row, and every read (`get`, waiting, batch lookups, result tracking) touches only it. `ready` and `running` are narrow work indexes that exist so claiming and reaping never scan `tasks`. A state change updates the `tasks` row and inserts or deletes the index row in one transaction.
- **No operation joins two task tables.** A query that seems to need a join means a column is missing from one of them. A test inspects the query plans of every backend operation.
- **`tasks` has no index on a column that changes** (state, worker, lease). Its updates can then stay within the page and leave its indexes alone; the insert-and-delete churn is confined to the two narrow tables. The Phase 1 benchmark must confirm this under sustained load.
- **Claiming** deletes from `ready` with `FOR UPDATE SKIP LOCKED`, first within the caller's own buckets and then among tasks past the affinity window, so in steady state each worker scans its own slice and workers rarely contend for the same rows.
- **`tasks` and `task_inputs` are partitioned by day**, on the task ID, whose leading bits are its creation time. Retention drops whole partitions instead of deleting rows. The primary key stays the ID alone, which is why callers cannot choose IDs.
- **Partitions are managed by the controller**: created three days ahead and dropped once every task in them is terminal and past retention. Whether a partition still holds live work is answered from `ready` and `running` by ID range, without scanning it. A default partition catches anything that arrives with no partition prepared, and is reported as a metric so it is noticed; `taskwire migrate` creates the first partitions.
- **A partition with one long-scheduled task cannot be dropped.** Its finished rows are then removed in bounded batches once it is a week past retention, so a single far-future task does not pin a day of data.
- **Idempotency keys are a small unpartitioned table**, because a key must be unique across all time ranges. Rows are deleted in batches when their task's partition is dropped.
- **Agreement between the row and the indexes** is part of S11: a queued task has exactly one `ready` row, a running task exactly one `running` row, and a terminal task neither.
- **Inputs** are limited by `TASKWIRE_MAX_INPUT_BYTES` (default 64 KiB); a larger input is rejected with `InputTooLargeError`. An input is the task's instructions, not its data: large data is passed as a reference to wherever the application keeps it.

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
