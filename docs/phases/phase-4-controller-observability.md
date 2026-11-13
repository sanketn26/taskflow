# Phase 4 — Controller and Observability

## Goal

Add the controller's maintenance role, which recovers expired leases and enforces retention, and make the whole system observable through Prometheus metrics, health endpoints, and structured logs. The API role is Phase 2; both are the same Go binary.

## Controller

```bash
taskwire controller --role maintenance
```

The maintenance role runs the full startup assertion (Phase 1), then these loops over the `Backend` interface:

| Loop | Operation | Interval | Environment variable |
|---|---|---|---|
| Reaper | `reap(limit)` until it returns zero | 5 s | `TASKWIRE_REAP_SECONDS` |
| Retention | `purge(policy, limit)` in bounded batches, then delete the returned result references from the result channel if it supports deletion | 60 s | `TASKWIRE_PURGE_SECONDS` |
| Statistics | `refresh_stats()`; one replica computes and stores the report, the others read it. The interval stretches when a refresh is slow | 5 s minimum | `TASKWIRE_STATS_SECONDS` |
| Result sweep | Remove published results older than a safety margin that no task record references (left by a crash between upload and completion, or by a stale attempt). For S3 the controller lists and deletes with its own credentials; a bucket lifecycle rule is the documented primary mechanism and this sweep is a bounded backstop | 1 h | `TASKWIRE_RESULT_SWEEP_SECONDS` |

- Retention is one period for all outcomes: `TASKWIRE_RETAIN_SECONDS` (default 604,800, seven days). Task data is stored in daily partitions and removed by dropping a partition once everything in it is terminal and past retention, so retention is accurate to a day. How long a *result* stays readable is the result channel's retention.
- The same loop creates partitions three days ahead. If it cannot, new tasks land in a default partition and `taskwire_default_partition_rows` rises above zero.
- The reaper requeues the tasks of instances whose liveness has lapsed and tasks past their timeout, removes the dead instances' registrations, and reassigns their buckets.
- Any number of replicas may run. The backend operations are safe to run concurrently, so replicas do not conflict and no leader election is needed; statistics are the one loop where only one replica does the work at a time, and the backend arranges that. Two replicas is the recommended deployment.
- The maintenance role is off the task path. While it is down, the API role still serves submits and claims; expired leases are not recovered, partitions are not created ahead, and queue metrics go stale.
- It serves `/livez`, `/readyz`, and `/metrics` on `TASKWIRE_HTTP_PORT` (default 9464) and drains on `SIGTERM`.

## Metrics

Observability is offered at three levels, all from the controller, so none depends on scraping application pods:

| Level | Question it answers | Where |
|---|---|---|
| Worker pool | Is this pool keeping up, and does it have the capacity? | "Per pool" |
| Work type (a task name within a pool) | Is this kind of work flowing, how long does it take, and how often does it fail? | "Per work type" |
| Worker (one pod) | Is this pod healthy, and what is it doing? | "Per worker" |

Prometheus text format. The `taskwire[metrics]` extra installs the client library; without it the endpoints return 404 and nothing else changes.

### Per pool

Exported by the maintenance role.

| Metric | Type | Labels |
|---|---|---|
| `taskwire_queue_depth` | gauge | `pool` |
| `taskwire_queue_scheduled` | gauge | `pool` |
| `taskwire_queue_oldest_age_seconds` | gauge | `pool` |
| `taskwire_queue_unserved_oldest_age_seconds` | gauge | `pool` |
| `taskwire_tasks_running` | gauge | `pool` |
| `taskwire_workers` | gauge | `pool` |
| `taskwire_pool_slots` | gauge | `pool` — total slots declared by live instances |
| `taskwire_pool_slots_in_flight` | gauge | `pool` — utilisation is this over `taskwire_pool_slots` |
| `taskwire_pool_draining_workers` | gauge | `pool` |
| `taskwire_tasks_reaped_total` | counter | `pool`, `reason` |
| `taskwire_bucket_moves_total` | counter | `pool` — bucket-table entries reassigned by a worker joining, leaving, or being reaped |

### Per work type

A work type is a task name within a pool. The task version is deliberately not a label.

Gauges, exported by the maintenance role:

| Metric | Type | Labels |
|---|---|---|
| `taskwire_work_queued` | gauge | `pool`, `task` |
| `taskwire_work_scheduled` | gauge | `pool`, `task` |
| `taskwire_work_running` | gauge | `pool`, `task` |
| `taskwire_work_oldest_age_seconds` | gauge | `pool`, `task` |
| `taskwire_work_serving_workers` | gauge | `pool`, `task` — live instances registered for at least one version of it |
| `taskwire_queue_unserved` | gauge | `pool`, `task` — queued tasks whose `name@version` no live instance has registered |

Counters and histograms, recorded by the API role as it accepts each outcome:

| Metric | Type | Labels |
|---|---|---|
| `taskwire_tasks_total` | counter | `pool`, `task`, `outcome` |
| `taskwire_tasks_submitted_total` | counter | `pool`, `task` |
| `taskwire_submit_limited_total` | counter | `pool`, `task`, `reason` (`rate`, `backlog`) — tasks refused, counted per task in the batch |
| `taskwire_submit_limit` | gauge | `pool`, `task`, `kind` (`rate`, `max_queued`) — the configured limit, so dashboards can draw it beside the actual rate and depth |
| `taskwire_pressure` | gauge | `pool`, `task` — 0 to 1; absent where the controller has no basis for one |
| `taskwire_submit_paced_seconds_total` | counter | `pool`, `task` — total delay the controller asked submitters to observe |
| `taskwire_task_duration_seconds` | histogram | `pool`, `task`, `outcome` — claim to outcome, by the backend clock |
| `taskwire_task_queue_wait_seconds` | histogram | `pool`, `task` — eligible to claimed |
| `taskwire_task_end_to_end_seconds` | histogram | `pool`, `task` — submitted to terminal |
| `taskwire_task_attempts` | histogram | `pool`, `task` — attempts used by tasks reaching a terminal state |
| `taskwire_result_bytes` | histogram | `pool`, `task` — size of published results, from the tracking record |

- The controller records these because it sees every outcome exactly once, whatever the library's language and whether or not the pod is scraped. They are not also exported by libraries, so nothing is counted twice.
- Durations are measured between controller-observed events, so they include the result upload and the report's network time. A library's own execution-time histogram (below) excludes those.
- Each API replica counts the outcomes it accepted; aggregate these with `sum`. The gauges are shared state; aggregate them with `max`.
- The number of series is bounded by the number of task names. A task name is code, not data; a library rejects a name built at run time from a pattern that looks like an ID.

### Maintenance

| Metric | Type | Labels |
|---|---|---|
| `taskwire_backend_info` | gauge | `backend`, `durability` |
| `taskwire_tasks_purged_total` | counter | — |
| `taskwire_partitions` | gauge | — |
| `taskwire_default_partition_rows` | gauge | — |
| `taskwire_results_tracked` | gauge | `channel`, `state` (`available`, `expired`) |
| `taskwire_results_tracked_bytes` | gauge | `channel` |
| `taskwire_results_deleted_total` | counter | `channel`, `reason` (`purged`, `orphan`) |
| `taskwire_stats_age_seconds` | gauge | — |
| `taskwire_controller_loop_errors_total` | counter | `loop` |

The gauges in these three tables are a view of shared database state, so every maintenance replica reports the same numbers. Queries and dashboards must aggregate them with `max`, never `sum`. `taskwire_stats_age_seconds` lets an alert detect a controller that has stopped refreshing.

### API role

| Metric | Type | Labels |
|---|---|---|
| `taskwire_api_requests_total` | counter | `operation`, `code` |
| `taskwire_api_request_duration_seconds` | histogram | `operation` |
| `taskwire_api_sessions` | gauge | `language` |
| `taskwire_api_waiting_claims` | gauge | `pool` |
| `taskwire_api_rejected_total` | counter | `reason` (`unauthenticated`, `overloaded`, `stale_token`, `over_slots`) |
| `taskwire_api_shed_total` | counter | `operation` — requests refused to protect the backend |
| `taskwire_claims_total` | counter | `pool`, `affinity` (`owned`, `spilled`) |
| `taskwire_backend_call_duration_seconds` | histogram | `operation` |


### Library

Exposed by each library through its own port or a handler the application mounts (Phase 3). The names and labels below are part of the protocol's contract, so dashboards work for a library in any language. In `process` mode they are recorded by the parent process, so no multi-process metrics mode is involved.

| Metric | Type | Labels |
|---|---|---|
| `taskwire_library_task_run_seconds` | histogram | `pool`, `task`, `outcome` — the function call only |
| `taskwire_task_longest_running_seconds` | gauge | `pool` |
| `taskwire_controller_reachable` | gauge | — |
| `taskwire_tasks_in_flight` | gauge | `pool` |
| `taskwire_task_processes_restarted_total` | counter | `pool` |
| `taskwire_leases_lost_total` | counter | `pool` |
| `taskwire_heartbeat_failures_total` | counter | — |
| `taskwire_api_call_duration_seconds` | histogram | `operation` |
| `taskwire_draining` | gauge | — |

`outcome` is one of `succeeded`, `failed`, `retried`, `timed_out`, `released`, `abandoned`. `reason` is `worker_lost` or `timeout`.

### Per worker

**Stage two.**

Every pod that runs tasks is visible individually, from the controller, without scraping the pod. This matters because an embedded library may expose no metrics port of its own.

**Source.** Each heartbeat carries a small status report from the library (Phase 2): slots, tasks in flight, draining or not, execution mode, resident memory, child-process restarts, and counts since start of tasks finished by outcome and of attempts lost. The controller already knows the rest: pools, registered `name@version` set, language and library version, start time, last heartbeat, and buckets owned. The report is kept on the instance's row in the backend, so any controller replica can answer.

**Metrics**, exported by the maintenance role for live instances only:

| Metric | Type | Labels |
|---|---|---|
| `taskwire_instance_info` | gauge (always 1) | `instance`, `pool`, `language`, `library_version`, `mode` |
| `taskwire_instance_slots` | gauge | `instance`, `pool` |
| `taskwire_instance_in_flight` | gauge | `instance`, `pool` |
| `taskwire_instance_heartbeat_age_seconds` | gauge | `instance` |
| `taskwire_instance_longest_running_seconds` | gauge | `instance` |
| `taskwire_instance_memory_bytes` | gauge | `instance` |
| `taskwire_instance_draining` | gauge | `instance` |
| `taskwire_instance_buckets` | gauge | `instance`, `pool` |
| `taskwire_instance_leases_lost` | gauge | `instance` — attempts this instance lost since it started |
| `taskwire_instance_process_restarts` | gauge | `instance` |

- These are the one place an instance ID is a label. They are gauges read from the instance registry, so a series exists only while its pod is registered and disappears when it leaves; cardinality is bounded by the number of live pods, not by history. Counts since start are gauges for the same reason: a pod's series must be able to vanish.
- `TASKWIRE_INSTANCE_METRICS=false` turns the set off for very large fleets; the commands below still work.
- Rates and histograms per pod (task duration, outcomes) come from the library's own metrics, to which Prometheus adds the pod label at scrape time. Taskwire does not put an instance label on those.

**Commands and API.**

```bash
taskwire pools                       # one line per pool: queued, running, oldest age, workers, slots used
taskwire work [--pool cpu]           # one line per work type: queued, running, oldest age, versions queued and served, workers serving
taskwire instances [--pool cpu]      # one line per live instance
taskwire instance <instance-id>      # its report, registered tasks, buckets, and running tasks with their ages
taskwire task <task-id>              # state, attempts, and which instance ran each attempt
```

They call `ListPools`, `ListWorkTypes`, `ListInstances`, `GetInstance`, and `Get` on the controller API, so the same view is available to any tool. A task's snapshot names the instance holding it, and each recorded failure names the instance that reported it, so "which pod ran this, and what else was it doing" is answerable after the fact for as long as the task is retained.

### Rules

- Labels are limited to the ones listed. Task IDs, owner IDs, task versions, and exception messages are never labels. Instance IDs are a label only on the `taskwire_instance_*` gauges above. Versions change with every deploy and would grow cardinality without bound.
- Histogram buckets are fixed and documented; duration buckets span 5 ms to 1 hour.
- Metric names and labels are a public contract from the first release.

## Autoscaling signal

The signal is the controller's Prometheus metrics, which are the same for every backend: `max by (pool) (taskwire_queue_depth)` for short tasks, or `taskwire_queue_oldest_age_seconds` for long ones. Pool utilisation (`taskwire_pool_slots_in_flight` over `taskwire_pool_slots`) is available as a third signal. A KEDA `ScaledObject` using its Prometheus scaler is the supported configuration, including scale from zero.

Scaling a pool from zero therefore depends on the controller running and being scraped. Querying the backend directly from the autoscaler would avoid that dependency but ties the deployment to one backend; it is documented as an optional, PostgreSQL-specific alternative and its behaviour must be verified against the KEDA version in use before it is recommended.

## Logging

- One JSON object per line on standard error, with `timestamp`, `level`, `event`, and, where relevant, `task_id`, `task`, `version`, `pool`, `attempt`, `worker_id`.
- Task start and finish are logged at info; lease loss, process death, and reaper activity at warning.
- Inputs, results, and connection strings are never logged.

## Tracing

Out of scope. The `trace_context` column exists; this phase only ensures the library sends it when a W3C `traceparent` is present in the current context and the worker exposes it through `taskwire.current_task()`.

## Required tests

- Reaper recovers a task from a killed worker within one interval plus the lease length.
- Two controller replicas under load: no task is reaped twice and no errors are raised.
- Retention drops a partition only when all its tasks are terminal and past the cutoff, keeps up with a sustained backlog, and leaves a long-scheduled task and its input intact. Partitions exist three days ahead; with partition creation blocked, submits still succeed through the default partition and the metric reports it.
- Unserved work: tasks queued for a version no live worker registers are counted in `taskwire_queue_unserved`; the count drops to zero when such a worker starts, and tasks merely waiting for a busy worker are not counted.
- Result tracking: tracked counts and bytes match what was published; purge deletes the results of purged tasks; the sweep removes an orphan left by a crash between publish and `complete` and never removes a referenced result.
- Per worker: `taskwire instances` and the `taskwire_instance_*` gauges agree with what each test instance is actually doing (slots, in flight, draining, memory, mode); an instance's series disappears within one statistics interval of it leaving or being reaped; an embedded instance with no metrics port is fully visible; a task's snapshot and failure record name the instance that ran each attempt.
- Per pool and per work type: with two pools and several task names under load, every gauge matches a direct count; slots and slots in flight match the instances' declarations; `taskwire_tasks_total` summed over API replicas equals the number of outcomes recorded in the backend, with each counted once across a replica failure; a work type with queued tasks and no serving instance shows zero serving workers and a non-zero unserved count; all of it is present with every library pod unscraped.
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
