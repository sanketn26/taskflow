# Phase 5 — Kubernetes Packaging

## Goal

Install Taskwire on a Kubernetes cluster with one Helm release, run worker pools that scale with queue depth, and prove that the system survives the disruptions Kubernetes causes.

**This is the Kubernetes pre-alpha gate.**

## Container image

- One published image containing the `taskwire` package, used for the controller and the migration Job.
- Runs as a non-root user with a read-only root filesystem and no added capabilities.
- Worker pods use the **user's application image** with `taskwire` installed. The documentation includes a minimal Dockerfile.

## Helm chart

`deploy/helm/taskwire`

| Resource | Purpose |
|---|---|
| Migration `Job` (pre-install and pre-upgrade hook) | Runs `taskwire migrate`, then `taskwire check`; the release fails if the backend does not meet the contract |
| Controller `Deployment` (2 replicas) with `PodDisruptionBudget` | Reaper, retention, queue metrics |
| Result channel settings (`TASKWIRE_RESULT_URL`) on workers, controller, and the example client | The chart does not install S3; the end-to-end test brings an S3-compatible test server |
| Controller `Service` | Metrics scrape target |
| `ServiceMonitor` (controller) and `PodMonitor` (workers) | Prometheus Operator discovery; optional |
| `PrometheusRule` | Default alerts; optional |
| Grafana dashboard `ConfigMap` | One overview dashboard; optional |
| Worker pool `Deployment` per entry in `workerPools` | Optional; teams may manage worker Deployments themselves |
| KEDA `ScaledObject` per pool | Optional |

### Database credentials

- The chart does not install PostgreSQL. It takes references to existing Secrets.
- Two Secrets: a schema-owner credential used only by the migration Job, and an application credential used by the controller and workers. Application pods use a credential that the application team manages.
- No credential appears in a ConfigMap, a pod spec literal, or a log.

### Worker pool values

```yaml
workerPools:
  cpu:
    image: registry.example.com/myapp:1.4.2
    modules: [myapp.tasks]
    concurrency: 4
    resources: {requests: {cpu: "4", memory: 4Gi}}
    terminationGracePeriodSeconds: 60
    autoscaling:
      enabled: true
      minReplicas: 0
      maxReplicas: 50
      targetQueueDepthPerReplica: 20
```

Each pool Deployment sets:

- `TASKWIRE_WORKER_ID` from the pod name through the downward API.
- `TASKWIRE_DRAIN_SECONDS` derived from `terminationGracePeriodSeconds` with a safety margin.
- Liveness and readiness probes on `/livez` and `/readyz`.
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
| Partition maintenance failing | `taskwire_default_partition_rows` above zero |
| Workers being lost | Rate of reaped tasks above threshold |
| Startup assertion failing | A worker or controller pod not ready with a failed check |
| Stuck task | Longest-running attempt above threshold |
| Tasks exhausting attempts | Rate of `failed` outcomes above threshold |
| Controller statistics stale | `taskwire_stats_age_seconds` above threshold |
| Controller unavailable | No ready controller replica |

Thresholds are chart values with documented defaults.

## Upgrade procedure

1. `helm upgrade` runs the migration Job first. Migrations are backward compatible within a minor version (Phase 1).
2. The controller rolls.
3. Worker pools and application pods roll on their own schedule; old and new SDK versions run against the same schema during the roll.

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
