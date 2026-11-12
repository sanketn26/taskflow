# Phase 4 — Controller and Observability

## Goal

Add the controller, which recovers expired leases and enforces retention, and make the whole system observable through Prometheus metrics, health endpoints, and structured logs.

## Controller

```bash
taskwire controller
```

The controller runs the full startup assertion (Phase 1), then three loops over the `Backend` interface:

| Loop | Operation | Interval | Environment variable |
|---|---|---|---|
| Reaper | `reap(limit)` until it returns zero | 5 s | `TASKWIRE_REAP_SECONDS` |
| Retention | `purge(policy, limit)` in bounded batches, then delete the returned result references from the result channel if it supports deletion | 60 s | `TASKWIRE_PURGE_SECONDS` |
| Statistics | `stats()` cached for `/metrics` | 5 s | `TASKWIRE_STATS_SECONDS` |
| Result sweep | On channels with `list` and `delete`: remove published results older than a safety margin that no task record references (left by a crash between publish and `complete`, or by a stale attempt) | 10 min | `TASKWIRE_RESULT_SWEEP_SECONDS` |

- Retention is set per outcome: `TASKWIRE_RETAIN_SUCCEEDED_SECONDS` (default 86,400) and `TASKWIRE_RETAIN_FAILED_SECONDS` (default 604,800), so failed tasks stay available for diagnosis longer.
- The reaper requeues the tasks of workers whose liveness has lapsed and tasks past their timeout, and removes the dead workers' registrations.
- Any number of replicas may run. The backend operations are safe to run concurrently, so replicas do not conflict and no leader election is needed. Two replicas is the recommended deployment.
- The controller is off the task path. While it is down, tasks continue to run; expired leases are not recovered and queue metrics go stale.
- It serves `/livez`, `/readyz`, and `/metrics` on `TASKWIRE_HTTP_PORT` (default 9464) and drains on `SIGTERM`.

## Metrics

Prometheus text format. The `taskwire[metrics]` extra installs the client library; without it the endpoints return 404 and nothing else changes.

### Controller

| Metric | Type | Labels |
|---|---|---|
| `taskwire_queue_depth` | gauge | `pool` |
| `taskwire_queue_scheduled` | gauge | `pool` |
| `taskwire_queue_oldest_age_seconds` | gauge | `pool` |
| `taskwire_queue_unserved` | gauge | `pool`, `task` — queued tasks whose `name@version` no live worker has registered. The version is deliberately not a label |
| `taskwire_queue_unserved_oldest_age_seconds` | gauge | `pool` |
| `taskwire_bucket_moves_total` | counter | `pool` — bucket-table entries reassigned by a worker joining, leaving, or being reaped |
| `taskwire_tasks_running` | gauge | `pool` |
| `taskwire_workers` | gauge | `pool` |
| `taskwire_tasks_reaped_total` | counter | `pool`, `reason` |
| `taskwire_backend_info` | gauge | `backend`, `durability` |
| `taskwire_tasks_purged_total` | counter | — |
| `taskwire_results_tracked` | gauge | `channel`, `state` (`available`, `expired`) |
| `taskwire_results_tracked_bytes` | gauge | `channel` |
| `taskwire_results_deleted_total` | counter | `channel`, `reason` (`purged`, `orphan`) |
| `taskwire_stats_age_seconds` | gauge | — |
| `taskwire_controller_loop_errors_total` | counter | `loop` |

The gauges are a view of shared database state, so every replica reports the same numbers. Queries and dashboards must aggregate them with `max by (pool)`, never `sum`. `taskwire_stats_age_seconds` lets an alert detect a controller that has stopped refreshing.

### Worker

In addition to the table below: `taskwire_claims_total` with labels `pool` and `affinity` (`owned`, `spilled`), so the share of tasks that ran on their preferred worker is visible.

Recorded by the supervisor process, so no multi-process metrics mode is involved.

| Metric | Type | Labels |
|---|---|---|
| `taskwire_task_duration_seconds` | histogram | `pool`, `task`, `outcome` |
| `taskwire_task_queue_wait_seconds` | histogram | `pool`, `task` |
| `taskwire_task_end_to_end_seconds` | histogram | `pool`, `task` |
| `taskwire_tasks_total` | counter | `pool`, `task`, `outcome` |
| `taskwire_task_longest_running_seconds` | gauge | `pool` |
| `taskwire_startup_check_passed` | gauge | — |
| `taskwire_tasks_in_flight` | gauge | `pool` |
| `taskwire_task_processes_restarted_total` | counter | `pool` |
| `taskwire_leases_lost_total` | counter | `pool` |
| `taskwire_heartbeat_failures_total` | counter | — |
| `taskwire_backend_call_duration_seconds` | histogram | `operation` |
| `taskwire_draining` | gauge | — |

`outcome` is one of `succeeded`, `failed`, `retried`, `timed_out`, `released`, `abandoned`. `reason` is `worker_lost` or `timeout`.

### Rules

- Labels are limited to the ones listed. Task IDs, owner IDs, worker IDs, task versions, and exception messages are never labels. Versions change with every deploy and would grow cardinality without bound; they are in the logs.
- Histogram buckets are fixed and documented; duration buckets span 5 ms to 1 hour.
- Metric names and labels are a public contract from the first release.

## Autoscaling signal

The signal is the controller's Prometheus metrics, which are the same for every backend: `max by (pool) (taskwire_queue_depth)` for short tasks, or `taskwire_queue_oldest_age_seconds` for long ones. A tested KEDA example is provided for each.

Scaling a pool from zero therefore depends on the controller running and being scraped. Querying the backend directly from the autoscaler would avoid that dependency but ties the deployment to one backend; it is documented as an optional, PostgreSQL-specific alternative and its behaviour must be verified against the KEDA version in use before it is recommended.

## Logging

- One JSON object per line on standard error, with `timestamp`, `level`, `event`, and, where relevant, `task_id`, `task`, `version`, `pool`, `attempt`, `worker_id`.
- Task start and finish are logged at info; lease loss, process death, and reaper activity at warning.
- Inputs, results, and connection strings are never logged.

## Tracing

Out of scope. The `trace_context` column exists; this phase only ensures the client writes it when a W3C `traceparent` is present in the current context and the worker exposes it through `taskwire.current_task()`.

## Required tests

- Reaper recovers a task from a killed worker within one interval plus the lease length.
- Two controller replicas under load: no task is reaped twice and no errors are raised.
- Retention removes only finished tasks older than the cutoff and keeps up with a sustained backlog.
- Unserved work: tasks queued for a version no live worker registers are counted in `taskwire_queue_unserved`; the count drops to zero when such a worker starts, and tasks merely waiting for a busy worker are not counted.
- Result tracking: tracked counts and bytes match what was published; purge deletes the results of purged tasks; the sweep removes an orphan left by a crash between publish and `complete` and never removes a referenced result.
- With the controller stopped, submitted tasks still run to completion.
- Every metric in the tables above is present with exactly the documented labels; a test fails if an undeclared label appears.
- Gauges match a direct `stats()` call; `taskwire_stats_age_seconds` grows when the backend is unreachable.
- The controller pointed at a backend that fails the startup assertion exits non-zero and never reports ready.
- Histograms and counters change as expected for success, failure, retry, release, and lost-lease cases.
- `/readyz` fails when the backend is unreachable and while draining.
- Without the `metrics` extra, the worker and controller start normally.
- A log-scan test asserts that no test payload value appears in any log line.

## Exit gate

A killed worker's tasks are recovered without manual action, retention holds the table size steady under sustained load, and a Prometheus instance scraping one controller and two workers shows every documented metric.
