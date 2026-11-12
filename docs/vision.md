# Taskwire Vision

This document is non-normative. It captures *why* Taskwire exists. Requirements, schemas, failure semantics, and exit gates live in the phase documents under `docs/phases/`. If this vision and a phase disagree, the phase wins.

## Origin

Running Python work in parallel across a Kubernetes cluster is harder than it should be.

The common answer is Celery, and Celery is not Kubernetes-native. It needs a broker and a separate result backend. Its workers know nothing about pods, node drains, or autoscaling. Scaling on queue depth, surviving a spot-node eviction, and getting metrics into Prometheus are all things each team bolts on for itself.

The alternatives each miss in a different way:

| Option | Where it falls short |
|---|---|
| Celery, RQ, Dramatiq | Broker plus result backend to run; no awareness of pods, drains, or autoscaling |
| Procrastinate and other PostgreSQL queues | The closest relatives: PostgreSQL-backed and Python. They are job queues, without a futures and fan-out API, and leave Kubernetes scaling, draining, and metrics to the user |
| Ray, Dask | Built for compute speed; the scheduler is a single point of failure and submitted work is not durable |
| Kubernetes Jobs | One pod per unit of work is too coarse and too slow for thousands of small tasks |
| Temporal and other workflow engines | A new programming model and a heavy service for what is often a parallel `map` |

## The thesis

> Taskwire is a Kubernetes-native library for durable, distributed parallel processing in Python, with PostgreSQL as the only dependency for task state.

Three bets define it:

1. **Guaranteed semantics over a plain database.** Taskwire defines what it guarantees (durable submit, exclusive claims, fenced outcomes, bounded retries) as a backend interface, tests every backend against it, and asserts it at start-up. PostgreSQL is the production backend. There is no broker, no result backend, and no Taskwire server on the task path. The backend is a control plane: no task output passes through it. Workers publish results asynchronously to a result channel the deployment chooses (S3, Redis, Kafka, or memory).
2. **Kubernetes does what Kubernetes is good at.** Workers are ordinary pods in ordinary Deployments. Kubernetes restarts them, KEDA or the HPA scales them, and Taskwire cooperates with drains and evictions instead of supervising processes itself.
3. **Observability is built in, not added later.** Prometheus metrics, health probes, and the autoscaling signal ship with the first working version, because operating the system depends on them.

## What this unlocks

### Parallel work that survives the cluster

Submitted tasks are durable. A pod eviction, a spot-node loss, or a rolling deploy costs at most a retry of the tasks that were running. The client can disconnect and collect results later.

### Fan-out as a first-class operation

`map` submits thousands of tasks in one batched write, and `gather` or `as_completed` collects results. This is the shape of most real workloads: document processing, embeddings, ETL shards, report generation.

### Elastic worker pools

Each pool is a Deployment. Queue depth and oldest-task age are exposed so pools scale with load, including to zero. GPU and CPU pools are separate Deployments with their own images, node selectors, and scaling rules.

### One thing to operate

Teams that already run PostgreSQL add no new stateful system. Backups, failover, and access control are the database's, and the task tables can be queried with plain SQL.

### Trustworthy failure

Execution is at least once. A fenced lease means a late or duplicate worker cannot overwrite a recorded outcome. Retries, backoff, and exhausted attempts are explicit states, not log lines.

## Good fits and poor fits

**Good fits**

- Fan-out batch workloads on Kubernetes that use Celery today.
- Mixed CPU and GPU pools that should scale independently.
- Work that must survive evictions and spot-node loss.
- Teams that already run PostgreSQL and want no further infrastructure.

**Poor fits**

- Very high throughput of tiny tasks, where a database write per state change is too expensive.
- Data-locality scheduling and shared in-memory objects; that is Ray's territory.
- Long-running multi-step workflows with timers and signals; that is Temporal's.
- Untrusted multi-tenant code execution.
- Environments without PostgreSQL or without Kubernetes. Taskwire runs anywhere Python and PostgreSQL do, but the packaging, scaling, and operations story targets Kubernetes.

## Non-goals

Taskwire is not trying to be:

- an exactly-once system; side effects need application-level idempotency
- a workflow engine with sagas, signals, and durable timers
- a compute framework with a distributed object store
- a zero-infrastructure embedded queue; production use requires PostgreSQL
- a polyglot platform; only the Python SDK is planned
- a system that isolates teams from each other on a shared backend

Taskwire's edge over the PostgreSQL queues is narrower than its edge over Celery: the fan-out API, the stated and asserted guarantees, and the Kubernetes integration.

## Delivery posture

- **Kubernetes pre-alpha (Phases 0–5):** backend contract, worker, client SDK, controller with metrics, and a Helm chart, proven end to end on a local cluster.
- **After that:** Redis and Kafka result channels, then hardening and release, then examples.
- **Unscheduled:** cron-style schedules, OpenTelemetry tracing, a web console, other-language SDKs, and an operator with a `WorkerPool` resource.

## Related documents

- [architecture.md](architecture.md) — system shape and the reasoning behind it
- [decisions.md](decisions.md) — the decisions that reset the design, and what was removed
- [phases/](phases/) — authoritative implementation contracts
