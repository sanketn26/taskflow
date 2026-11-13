# Phase 5 — Kubernetes Packaging

## Goal

Install Taskwire on a Kubernetes cluster with one Helm release, run worker pools that scale with queue depth, and prove that the system survives the disruptions Kubernetes causes.

**This is the Kubernetes pre-alpha gate.**

## Container image

- One published image containing the static `taskwire` Go binary and nothing else, used for the controller's API and maintenance Deployments and the migration Job.
- Runs as a non-root user with a read-only root filesystem and no added capabilities.
- Application and worker pods use the **user's own image** with the Taskwire library installed (`pip install taskwire`). They need no Taskwire binary and no database access. The documentation includes a minimal Dockerfile and shows one image deployed both as an application Deployment serving no pools and as a worker Deployment serving some.

## Helm chart

`deploy/helm/taskwire`

| Resource | Purpose |
|---|---|
| Migration `Job` (pre-install and pre-upgrade hook) | Runs `taskwire migrate`, then `taskwire check`; the release fails if the backend does not meet the contract |
| Controller API `Deployment` (3 replicas) with `PodDisruptionBudget` and optional `HorizontalPodAutoscaler` | The Taskwire API; on the task path |
| Controller API headless `Service` | Client-side load balancing for long-lived connections |
| `NetworkPolicy` | Restricts the API to labelled namespaces; restricts PostgreSQL access to the controller |
| API token and session key `Secret` | The shared credential for libraries, and the key API replicas sign sessions with; may reference existing Secrets |
| Limits `ConfigMap` | `TASKWIRE_LIMITS` from chart values (`limits.default`, `limits.pools`, `limits.work`); changing it rolls the API Deployment |
| Controller maintenance `Deployment` (2 replicas) with `PodDisruptionBudget` | Reaper, retention, partitions, queue metrics |
| Result channel settings (`TASKWIRE_RESULT_URL`) and storage credentials on the controller only | The chart does not install S3; the end-to-end test brings an S3-compatible test server. With S3, application pods receive no storage credential |
| Controller `Service` | Metrics scrape target |
| `ServiceMonitor` (controller) and `PodMonitor` (workers) | Prometheus Operator discovery; optional |
| `PrometheusRule` | Default alerts; optional |
| Grafana dashboard `ConfigMap` | Three dashboards, optional: per pool (depth, oldest age, capacity and utilisation), per work type (rate, duration, failure ratio, queue wait), and per worker (one row per live instance) |
| Worker pool `Deployment` per entry in `workerPools` | Optional; teams may manage worker Deployments themselves |
| KEDA `ScaledObject` per pool | Optional |

### Database credentials

- The chart does not install PostgreSQL. It takes references to existing Secrets.
- Two database Secrets: a schema-owner credential used only by the migration Job, and an application credential used only by the controller. Application and worker pods receive no database credential; they receive the controller address and the API token.
- No credential appears in a ConfigMap, a pod spec literal, or a log.

### Worker pool values

```yaml
workerPools:
  cpu:
    image: registry.example.com/myapp:1.4.2
    command: [python, -m, taskwire, run, myapp.tasks]   # or the application's own entry point that calls serve()
    mode: process
    processes: 4
    resources: {requests: {cpu: "4", memory: 4Gi}}
    terminationGracePeriodSeconds: 60
    autoscaling:
      enabled: true
      minReplicas: 0
      maxReplicas: 50
      targetQueueDepthPerReplica: 20
```

Each pool Deployment sets:

- `TASKWIRE_INSTANCE_ID` from the pod name through the downward API, and `TASKWIRE_CONTROLLER_URL` and `TASKWIRE_API_TOKEN` for the release.
- `TASKWIRE_DRAIN_SECONDS` derived from `terminationGracePeriodSeconds` with a safety margin.
- `TASKWIRE_POOLS` and `TASKWIRE_MODE` from the values above.
- Liveness and readiness probes on the library's own port under `python -m taskwire run`. For an application that embeds the library, probes are the application's, and the chart leaves them to the user.
- A rolling-update strategy that adds new pods before removing old ones.

## Autoscaling

- The default `ScaledObject` uses KEDA's Prometheus scaler on `taskwire_queue_depth`, including scale from zero.
- `taskwire_queue_oldest_age_seconds` is the documented alternative for long-running tasks, where depth is a poor signal.
- The KEDA configuration is verified against a pinned KEDA version in the tests below, not assumed.
- Scale-down is safe: a terminated pod drains, then releases what it could not finish, and the release cap bounds how often one task can be restarted this way.

## Default alerts

| Alert | Condition |
|---|---|
| Queue is not draining | Oldest queued age above threshold while depth is non-zero |
| No workers for a pool with queued work | Depth above zero and workers at zero beyond the scale-up window |
| Queued work that no worker can run | `taskwire_queue_unserved` above zero beyond a rollout window: a deploy removed the last worker for a task version, or a client is ahead of the workers |
| Controller API unavailable or overloaded | No ready API replica, or a sustained rate of `overloaded` rejections |
| Submissions being refused | A sustained rate of `taskwire_submit_limited_total` for a pool or work type: a submitter is over its limit, or the limit is too low for the workers behind it |
| Sustained back pressure | `taskwire_pressure` above the pacing threshold for a pool beyond the scale-up window: workers are not keeping up and autoscaling has not closed the gap |
| Backend protection active | Any sustained rate of `taskwire_api_shed_total` |
| Partition maintenance failing | `taskwire_default_partition_rows` above zero |
| Workers being lost | Rate of reaped tasks above threshold |
| A worker is unhealthy | One instance's heartbeat age near the liveness period, or its lost leases or process restarts rising while its pool's are not |
| A worker is saturated or stuck | One instance at full slots with a longest-running attempt above threshold while its pool has queued work |
| Startup assertion failing | A worker or controller pod not ready with a failed check |
| Stuck task | Longest-running attempt above threshold |
| Tasks exhausting attempts | Rate of `failed` outcomes above threshold |
| Controller statistics stale | `taskwire_stats_age_seconds` above threshold |
| Controller unavailable | No ready controller replica |

Thresholds are chart values with documented defaults.

## Upgrade procedure

1. `helm upgrade` runs the migration Job first. Migrations are backward compatible within a minor version (Phase 1).
2. The controller rolls, API role first. It accepts the current and the previous protocol revision (Phase 2).
3. Worker pools and application pods roll on their own schedule; libraries one protocol revision behind keep working during the roll.

## Required tests

Run on a local `kind` cluster in CI, marked `kubernetes`.

- A release against a backend that fails the contract (a read replica) fails at the migration Job with the failed guarantee in its log.
- Fresh install: migration Job succeeds, controller becomes ready, a worker pool starts, and a client pod runs a `map` of 1,000 tasks to completion.
- Scale from zero: with no worker pods, submitted tasks cause KEDA to start workers, and the pool returns to zero when the queue is empty.
- `kubectl delete pod` on a worker mid-task: the task is released and completed elsewhere without consuming an attempt.
- Force-kill of a worker pod (`--grace-period=0`): the worker's liveness lapses, the reaper requeues, and the task completes.
- `kubectl drain` of a node running workers: no task is lost.
- Rolling update of a worker pool under load from `v1` to an image that also registers `v2` tasks: no task is lost and no worker receives a version it does not know.
- Controller replica deleted under load: no visible effect on task completion.
- `helm upgrade` across a schema migration with old workers still running.
- Prometheus Operator installed: the `ServiceMonitor` and `PodMonitor` are discovered and every documented metric is scraped.
- `helm lint` and a template-render test for every optional resource switched on and off.

## Exit gate

A single `helm install` against an existing PostgreSQL yields a working system on `kind`; the disruption tests above pass with the conservation ledger intact; and autoscaling from and to zero is demonstrated.
