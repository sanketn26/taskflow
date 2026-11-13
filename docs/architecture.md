# Taskwire Architecture Overview

This document is a non-normative orientation guide. Requirements, schemas, failure semantics, tests, and exit gates are in the phase documents. If this overview differs from a phase, the phase wins.

## System shape

```text
  ┌──────────────────────────────── Kubernetes ────────────────────────────────┐
  │                                                                            │
  │  your pods, each with the Taskwire library                                 │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                      │
  │  │ application  │  │ worker pool  │  │ worker pool  │   any language that  │
  │  │ submits      │  │ "cpu"        │  │ "io"         │   speaks the API     │
  │  │ (may also    │  │ runs tasks   │  │ runs tasks   │                      │
  │  │  run tasks)  │  │              │  │              │                      │
  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                      │
  │         │   Taskwire API (gRPC)             │           result bytes       │
  │         └─────────────────┼─────────────────┘        ┌──────────────────┐  │
  │                           ▼                 pods ───►│ result channel   │  │
  │              ┌─────────────────────────┐             │ S3 · Redis       │  │
  │              │ controller (Go)         │             └──────────────────┘  │
  │              │ api role · maintenance  │                                   │
  │              └────────────┬────────────┘                                   │
  └───────────────────────────┼────────────────────────────────────────────────┘
                              ▼
                         PostgreSQL   (only the controller connects)
```

There are two kinds of thing, joined by one protocol:

| Part | What it is | Responsibility |
|---|---|---|
| Controller, API role | The Go `taskwire` binary, a stateless replicated Deployment | Serves the Taskwire API; the only path to the backend; enforces every guarantee |
| Controller, maintenance role | The same binary, a small Deployment | Reaps lost work, enforces retention, manages partitions, exports queue metrics |
| Migrations | `taskwire migrate` as a Helm hook Job | Installs and upgrades the backend schema |
| Library | A package embedded in your pods; Python first | Submits tasks, runs tasks, uploads and downloads results |

Three rules define the boundaries:

1. **Only the controller touches the database.** No application pod holds a database credential.
2. **Only application pods touch task output.** Results go straight between pods and the result channel; the controller and the database never see them.
3. **The API is the protocol.** The controller treats every library as untrusted and enforces the guarantees itself, so a library in any language is safe to use once it passes the conformance check.

A pod does not have to be a "client" or a "worker". Any pod with the library can submit, and any pod that declares pools also runs tasks. The API role is on the task path, so it runs replicated; the maintenance role is not, and tasks keep flowing while it is down.

## The backend contract

Taskwire guarantees a fixed set of semantics. Inside the controller, storage is reached only through one Go interface that provides them, defined in [Phase 1](phases/phase-1-backend-contract.md). Because only the controller implements it, the guarantees have one implementation however many languages have a library.

| Guarantee | Meaning |
|---|---|
| Durable submit | A submitted task survives the loss of any Taskwire process |
| Idempotent submit | Retrying a submit with the same task ID creates one task |
| Exclusive claim | A task is held by at most one lease at a time |
| Fencing | Only the current lease can record an outcome |
| Backend clock | Expiry and scheduling are judged by the backend, never by a pod's clock |
| Bounded retry and release | Retries and drains are both capped |
| Atomic cancel | A cancel racing a claim has exactly one outcome |
| Terminal is final | A finished task never changes |

Three mechanisms hold the guarantees in place:

1. **A conformance suite** runs against every backend in CI. A backend is supported only if it passes.
2. **A startup assertion** runs in every controller process before it serves a request. It checks the backend's declared capabilities and settings and probes the guarantees live against the deployed backend. A controller that fails exits and never reports ready.
3. **An in-memory reference backend** serves as the model that other backends are compared against, as the backend for controller unit tests, and as a zero-dependency controller for local development.
4. **A library conformance check** (`taskwire library-check`) drives a library through scripted scenarios against a real controller. A library is supported only if it passes.

PostgreSQL is the only production backend. The interface exists so that the semantics are stated independently of it, tested independently of it, and portable to another store that can provide atomic conditional writes, durability, and its own clock. No second production backend is planned.

## Task lifecycle

```text
            submit                claim                 complete
  (new) ──────────► queued ──────────────► running ──────────────► succeeded
                      ▲  │                   │  │
                      │  │ cancel            │  │ fail, attempts exhausted
                      │  ▼                   │  └──────────────────► failed
                      │ cancelled            │
                      │                      │ fail (retryable) · worker lost · timeout · release
                      └──────────────────────┘   requeued with backoff
```

- **Submit** is durable when the call returns. Task IDs are generated by the library, so a retried submit is idempotent.
- **Claim** filters by pool and by the exact `name@version` identities the pod has registered. During a rolling deploy, old pods never receive tasks they cannot run. The controller never hands a pod more tasks than it has free slots.
- **Liveness and fencing.** A pod that runs tasks heartbeats once for itself, not once per task. If its liveness lapses, the controller requeues everything it held. Each claim also carries a token bound to a new lease, and an outcome is accepted only with the current one, so a pod that was paused or partitioned cannot record a stale result.
- **Timeout.** A task may set a maximum run time. The library stops the attempt when it can, and the controller requeues it regardless once the time has passed.
- **Retry.** A retryable failure, a lost worker, or a timeout requeues the task with exponential backoff until `max_attempts`, then the task fails.
- **Release.** A draining worker returns unfinished tasks without consuming an attempt, up to a cap, so a pool that scales down often cannot restart a long task forever.
- **Delay and priority.** `run_after` defers a task; `priority` orders tasks within a pool.
- **Cancel.** Queued tasks cancel atomically. For running tasks the request is delivered on the next heartbeat and is cooperative.

Execution is at least once. Outcome recording is exactly once per task.

## Running tasks

One library both submits and runs. A pod runs tasks by declaring the pools it serves; how it does so is the application's choice, and Taskwire recommends without enforcing ([Phase 3](phases/phase-3-python-library.md)).

| Choice | Recommended | Also supported |
|---|---|---|
| Where | Dedicated worker Deployments | Inside application pods, next to unrelated application logic |
| How | `process` mode: tasks in child processes, hard timeouts, heartbeat immune to task code | `thread` and `async` modes in the application's own process |

No choice weakens a guarantee, because the controller holds them. An embedded, in-process worker gives up hard timeouts and can be taken for dead if application code blocks the interpreter; the cost is a retry, never a lost or doubly recorded task.

- On shutdown a pod stops claiming, lets running tasks finish within its grace period, releases what is left without consuming an attempt, and tells the controller it is leaving.
- Each task hashes to one of a fixed set of buckets, and a bucket table in the backend maps each bucket to a preferred pod by consistent hashing. The controller offers a pod its own buckets' tasks first and others after a short window, so tasks sharing an affinity key tend to run on the same pod, claims rarely contend, and a pod joining or leaving moves work only between ring neighbours. The preference is loose: it never strands a task and no guarantee depends on it. Libraries know nothing about buckets.
- Pods wait for work on a long poll. The controller coalesces waiting pods into batched backend claims, so idle pods cost almost no database work.

## Control plane and data plane

**The controller and its backend are the control plane only.** It holds task state: what was submitted, who holds it, how it ended. **No task output ever passes through it** — not between tasks, and not back to the submitter.

```text
  pod that ran it ──upload──►  result channel  ──download──►  pod that wants it
        │                      (S3, Redis)                          ▲
        └──complete(ref)──►  controller ──► backend: succeeded, ref ─┘
```

- **Submitting a task is asynchronous, and that is the protocol.** `submit` returns once the task is durable; it is never a remote call that hands a value back. A submitter that wants the output gets it later, from the result channel.
- **Publishing is asynchronous.** The pod that ran the task uploads the return value to the result channel, then reports completion; the controller records `succeeded` and a small reference in the backend. Neither the controller nor the backend ever sees the bytes, and nothing blocks on a handshake through it.
- **Reading is the reader's business.** `future.result()` learns from the controller that the task succeeded and where its result is, then downloads the value from the channel. On a stream channel such as Kafka there is nothing to fetch by key: consumers read the topic, and `result()` says so.
- **Cross-task coordination is out of scope.** There is no "wait for the output of task A", no fan-in or barrier over outputs, and no chaining. A task that needs another's output reads the channel; "B after A" means A submits B, or a consumer of the channel does.
- **Result tracking is a system capability.** The backend does not hold results but always knows about them: which tasks have one, where it is, its size and checksum, and when it expires. Clients query that record, the controller exports it as metrics, deletes results when tasks are purged, and sweeps results that no task references. Applications never keep their own bookkeeping.
- **Two contracts, kept apart.** The backend guarantees task state and result tracking (S1–S12). The result channel declares its own properties (keyed fetch or stream, durable or not, retention). A task can be durably `succeeded` while its result has expired from a Redis channel; that is the channel's declared behaviour, not a backend failure.
- **Fencing still holds.** A pod with a stale lease may upload, but its `complete` is rejected, so its reference is never recorded. Result keys include the lease, so a stale publish cannot overwrite the winner's.
- Backend load depends on the number of tasks and state changes, never on the size or volume of results.

This is a design constraint, not an optimization. Any feature that would put task output in the backend is out of scope.

Task state is kept for one retention period and then removed by the controller, a day's partition at a time, which also deletes the purged tasks' results from channels that support deletion.

## Kubernetes integration

| Concern | Mechanism |
|---|---|
| Deployment | Helm chart: controller API and maintenance Deployments, migration Job, and a worker-pool template |
| Configuration | Environment variables from ConfigMaps and Secrets; no configuration file |
| Scaling | KEDA `ScaledObject` per pool, driven by the controller's Prometheus metrics |
| Draining | `SIGTERM` handling and bounded release, as above |
| Health | Liveness and readiness endpoints; controller readiness requires a passed startup assertion |
| Identity | Pod name from the downward API as the instance ID |
| Evictions and spot loss | The pod's liveness lapses and the controller requeues its tasks; no operator action |
| Credentials | Database credentials only in the controller; with S3, storage credentials only in the controller too. Application pods hold an API token |

Application and worker pods use the user's own image with the library installed. The controller uses the published Taskwire image, which contains only the Go binary. There are no custom resources and no operator in the first versions.

## Observability

Metrics are exposed in Prometheus format from three places.

| Source | Metrics | Notes |
|---|---|---|
| Controller | Queue depth, scheduled count, running count, oldest queued age, live workers, work reaped, tasks purged | Read from the backend on an interval. Replicas report the same values, so aggregate with `max`, not `sum` |
| Controller API | Per work type: outcomes, duration, queue wait, end-to-end latency, attempts. Also requests and latency by operation, sessions, waiting claims, rejections | Recorded as each outcome is accepted, so they cover every library and need no pod scraping. Aggregate with `sum` |
| Library | Function run time, in-flight tasks, heartbeat failures, controller reachability | Names and labels are part of the protocol, so dashboards work for a library in any language |

Observability comes at three levels, all from the controller: per worker pool, per work type (a task name within a pool), and per worker. Each pod that runs tasks is visible individually: its heartbeat carries a status report, and the controller exports per-instance gauges and answers `taskwire instances`, so a pod is observable even when it exposes no metrics itself.

Labels are bounded to pool, task name, and outcome. Task IDs, owner IDs, and task versions are never labels. Instance IDs appear only on per-instance gauges that exist while the pod is registered.

The Helm chart ships a `ServiceMonitor` and `PodMonitor`, a `PrometheusRule` with default alerts, and a Grafana dashboard. Logs are structured JSON carrying task ID, attempt, and pool. Each task carries a `trace_context` field reserved for W3C trace propagation; tracing itself is unscheduled.

## Connections and scale

Only controller replicas connect to PostgreSQL, each with a bounded pool, so database connections grow with the controller and not with the number of application pods. A thousand pods are a thousand long-lived API connections to a stateless service, which scales horizontally.

Reads touch one row in one table. Claiming and reaping work from two narrow index tables, task data is partitioned by day, and retention drops partitions rather than deleting rows. The supported throughput is whatever the Phase 1 churn benchmark measures; PostgreSQL on a single primary is the ceiling, and there is no sharding story beyond it.

The controller also limits submission, by rate and by backlog, per pool and per work type, so one submitter cannot fill the database or bury other work. Back pressure is explicit: workers pull only for free slots, every submit response carries the pool's pressure and a pacing delay, and libraries slow down smoothly as a queue fills rather than fail. Under database stress the controller sheds submits first and never outcomes or heartbeats, so the system keeps draining.

The price of this shape is that the API role is on the task path: it must be replicated, and its availability multiplies with PostgreSQL's.

## Authoritative phase set

0. [Phase 0 — Repository and Test Spine](phases/phase-0-repository.md)
1. [Phase 1 — Backend Contract](phases/phase-1-backend-contract.md)
2. [Phase 2 — Controller API](phases/phase-2-controller-api.md)
3. [Phase 3 — Python Library](phases/phase-3-python-library.md)
4. [Phase 4 — Controller Maintenance and Observability](phases/phase-4-controller-observability.md)
5. [Phase 5 — Kubernetes Packaging](phases/phase-5-kubernetes.md)
6. [Phase 6 — Kafka Result Channel](phases/phase-6-kafka-channel.md)
7. [Phase 7 — Hardening and Release](phases/phase-7-hardening-release.md)
8. [Phase 8 — Examples and Adoption](phases/phase-8-examples.md)
