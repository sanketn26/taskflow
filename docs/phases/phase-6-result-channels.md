# Phase 6 — Redis and Kafka Result Channels

## Goal

Add two result channels to the `memory://` and `s3://` channels that ship with the worker in Phase 2: Redis, for short-lived results read back quickly, and Kafka, for results consumed as a stream.

The rule from Phase 1 is unchanged: no task output enters the backend. This phase adds places for output to go; it adds nothing to the `Backend` interface.

## Contract

Both channels implement the Phase 2 `ResultChannel` interface, pass the channel conformance suite for the capabilities they declare, and are asserted at start.

| Capability | `redis://` | `kafka://` |
|---|---|---|
| `fetch` (read back by reference; `future.result()` works) | yes | **no** |
| `delete` (removed when the task is purged) | yes | no; the topic's retention applies |
| `durable` | as configured on the server; declared `no` unless persistence is confirmed by the startup check | yes |
| `shared` | yes | yes |

### Redis

- One key per result, written with a TTL (`TASKWIRE_RESULT_TTL_SECONDS`, default 24 h). The key and the checksummed reference follow the Phase 2 rules.
- A result that has expired raises `ResultUnavailableError` on fetch. The task remains `succeeded`; expiry is the channel's declared behaviour.
- A maximum result size (`TASKWIRE_RESULT_MAX_BYTES`, default 1 MiB for this channel) is enforced at publish; over it the attempt fails with `result_too_large`, which is not retryable.
- Installed by the `taskwire[redis]` extra.

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

Run against real Redis and Kafka test servers.

- Channel conformance suite for each channel, limited to its declared capabilities.
- Startup assertion: an unreachable or misconfigured channel stops the worker before it claims; a Redis without persistence is reported as not durable.
- Redis: an expired result raises `ResultUnavailableError` and leaves the task `succeeded`; purge deletes the keys of purged tasks; an oversized result fails with `result_too_large`.
- Kafka: a record is acknowledged before `complete`; a broker outage fails the publish and the task is retried; a stale attempt's record carries a lease ID that differs from the recorded one; `result()` raises `ResultNotFetchableError`.
- Channel outage with the backend healthy: tasks retry with `result_publish_failed`; no task is `succeeded` without a published result.
- No result byte is written to the backend with either channel (asserted against the PostgreSQL tables).

## Exit gate

On Kubernetes, the same workload runs with results on S3, on Redis, and on Kafka by changing only `TASKWIRE_RESULT_URL`; each channel passes its conformance suite and startup assertion; and the backend's size is unaffected by result volume.
