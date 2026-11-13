# Phase 6 — Stage-Two Features and the Kafka Result Channel

**This phase first builds every stage-two feature listed under "Pre-alpha scope" in [README.md](README.md)**, each to the specification, required tests, and exit-gate clauses in the phase document that describes it: partitioning, affinity, rate and concurrency limits, pacing and load shedding, scoped tokens, the Redis channel, `thread` and `async` modes, the library conformance tool, and per-worker observability. They are built one at a time, each behind its own merge, in an order chosen from what the pre-alpha showed to matter most. The remainder of this document specifies the one feature that is described nowhere else: Kafka.

## Goal

Add Kafka as a result channel, for results consumed as a stream. S3, Redis, and memory ship with the controller API and the Python library (Phases 2 and 3).

The rule from Phase 1 is unchanged: no task output enters the backend or passes through the controller. A library publishes to the topic itself, with Kafka credentials supplied to the pods by the deployment; the controller issues the record key and headers through `ResultUpload` and records the reference.

## Contract

The channel follows the Phase 2 result rules for the capabilities it declares, and the controller checks it at start.

| Capability | `kafka://` |
|---|---|
| Read back by reference (`future.result()` works) | **no** |
| Removed when the task is purged | no; the topic's retention applies |
| Durable | yes |

### Kafka

- One record per result on a topic chosen per pool or per task (`TASKWIRE_RESULT_TOPIC`). The record key is the task ID; headers carry task name, version, lease ID, codec, and SHA-256.
- Publish is acknowledged by the brokers (`acks=all`) before the worker calls `complete`.
- Delivery to consumers is **at least once**, and a stale attempt's record can appear on the topic even though its `complete` was rejected. Every record carries its lease ID, and the reference recorded in the backend names the winning lease. A consumer that needs exactly one result per task deduplicates on task ID, or checks the lease against the backend.
- There is no keyed read. `future.result()` and `gather()` raise `ResultNotFetchableError`; `wait()`, `done()`, and `as_completed()` work as usual because they need only the backend.
- Installed by the `taskwire[kafka]` extra.

## What Taskwire does not add

- No consumer framework, no fan-in, and no "run B when A's result arrives". An application consumes the topic with its own consumer and submits follow-up tasks itself.
- No mirroring of results into the backend to make a stream channel fetchable.
- No per-task channel routing beyond the topic setting above.

## Metrics

Added to the worker: result bytes published, publish duration and failures by channel. Added to the client: fetch duration, and `ResultUnavailableError` and `ResultIntegrityError` counts. Labels stay bounded to pool, task, and channel type.

## Required tests

Run against a real Kafka test server.

- Channel conformance suite for each channel, limited to its declared capabilities.
- Start-up: the controller refuses to start with an unreachable or misconfigured channel.
- A library with no Kafka credentials fails its attempts with `result_publish_failed`, not silently.
- Kafka: a record is acknowledged before `complete`; a broker outage fails the publish and the task is retried; a stale attempt's record carries a lease ID that differs from the recorded one; `result()` raises `ResultNotFetchableError`.
- Channel outage with the backend healthy: tasks retry with `result_publish_failed`; no task is `succeeded` without a published result.
- No result byte is written to the backend with either channel (asserted against the PostgreSQL tables).

## Exit gate

On Kubernetes, the same workload runs with results on S3, on Redis, and on Kafka by changing only the controller's `TASKWIRE_RESULT_URL` and the pods' channel credentials; and the backend's size is unaffected by result volume.
