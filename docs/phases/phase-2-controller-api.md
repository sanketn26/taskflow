# Phase 2 — Controller API

## Goal

`taskwire controller` serves the API through which every application pod submits tasks, claims them, reports outcomes, and reads task state. The controller is the only process that touches the backend. **This API is the Taskwire protocol**: a library in any language is a supported library if it adheres to it, and the controller enforces every guarantee no matter what a library does.

This phase ships the API, its enforcement rules, and a conformance check for libraries. The first library is Phase 3.

## Shape

```text
  application pods                         controller cluster (Go)              PostgreSQL
  ┌───────────────────────┐   Taskwire    ┌──────────────────────┐   SQL
  │ your code             │   API (gRPC)  │ api role             │──────────►  task state
  │ + Taskwire library    │──────────────►│ stateless, N replicas│
  │   submit · run · read │               └──────────────────────┘
  └──────────┬────────────┘
             │ result bytes, directly
             ▼
        result channel (S3, Redis)          the controller never sees task output
```

- A pod does not distinguish "client" from "worker". Any pod with the library may submit, and any pod that declares pools may also run tasks.
- No library holds a database credential or issues SQL.
- The controller never receives, stores, or forwards task output (Phase 1, "Results never enter the backend").

## Transport and versioning

- gRPC over HTTP/2. The definitions live in `proto/taskwire/v1/` and are the authoritative statement of the protocol.
- Within `taskwire.v1` changes are additive. A library states the protocol revision it was built for when it opens a session; the controller supports the current and the previous revision, so controllers and libraries are upgraded separately. Upgrade the controller first.
- Deadlines are set by the caller on every request. The controller never holds a request longer than its deadline.

## Operations

| Operation | Caller | Purpose |
|---|---|---|
| `Hello` | every library, once per process | Opens a session. Sends library name, version, language, an instance ID (the pod name), and, if the process will run tasks, its pools, `name@version` set, and number of slots. Returns the session, protocol revision, liveness period, limits, and how results are stored |
| `Submit` | any | A batch of new tasks; atomic per call |
| `Get` | any | Snapshots by task ID, including the result tracking record |
| `Wait` | any | Long poll: returns when any of the given tasks is terminal, or at the deadline |
| `Cancel` | any | Cancel one task |
| `Retry` | any | Create a new task from a terminal one, linked to the original |
| `Claim` | instances with pools | Long poll: returns up to the requested number of tasks, each with an opaque attempt token, the input, and the timeout |
| `Heartbeat` | instances with pools | Extends the instance's liveness and carries its status report: slots, tasks in flight, draining, mode, memory, process restarts, and outcome counts since start. Returns whether it is still alive and which of its attempts have a cancel request |
| `Complete`, `Fail`, `Release` | instances with pools | Report an attempt's outcome, by attempt token |
| `ResultUpload` | instances with pools | Before completing with a result: declares size, checksum, and codec; returns where to put the bytes and the reference to report |
| `ResultDownload` | any | Returns where to read a task's result from, and its size and checksum |
| `Goodbye` | instances with pools | Clean shutdown: unregisters the instance at once instead of waiting for liveness to lapse |
| `ListPools`, `ListWorkTypes` | any | Current state per pool, and per task name within a pool (Phase 4) |
| `Pressure` | any | Current pressure for a pool or work type, and the delay a submitter should observe |
| `ListInstances`, `GetInstance` | any | The live instances of a pool, and one instance's report, registered tasks, buckets, and running tasks (Phase 4, "Per worker") |

A process that only submits never calls `Claim` or `Heartbeat` and is not registered as a worker instance.

## What the controller enforces

A library is untrusted with respect to the guarantees. These hold whatever it sends:

- **Fencing.** An attempt token is opaque and bound to one lease. An outcome with any other token changes nothing and says so (S5).
- **No prefetch.** The controller tracks each instance's outstanding attempts and never hands out more than its declared slots, whatever a `Claim` asks for.
- **Eligibility.** An instance receives only tasks in its declared pools and `name@version` set (S4). Bucket ownership and the affinity window are applied by the controller; a library knows nothing about buckets.
- **Liveness.** An instance whose heartbeat lapses loses every attempt it held; its late outcomes are rejected.
- **Result tracking.** `Complete` with a result is accepted only with the reference the controller issued through `ResultUpload` for that attempt (S12). A library cannot record a reference to something else.
- **Limits.** Input size, batch size, and idempotency-key length are checked before anything is written.
- **Status reports are informational.** The controller records what an instance says about itself for observability and never uses it to decide a guarantee; slots in flight, for instance, are enforced from the controller's own count.
- **Task IDs.** Must be UUIDv7 within a day of the backend clock (Phase 1).

## Submission limits

**Only `max_queued` is pre-alpha.** `submit_rate` and `max_running` are stage two.

The controller limits how fast work may be submitted and how much may be waiting, so one eager submitter cannot fill the database or bury everyone else's work. Limits apply to `Submit` only; claiming and reporting outcomes are never limited, because they drain the system.

| Limit | Meaning | Scope |
|---|---|---|
| `submit_rate` | Tasks accepted per second, as a token bucket with a burst | Whole installation, a pool, or a work type (a task name within a pool) |
| `max_queued` | Tasks that may be waiting (queued or scheduled) before further submits are refused | A pool or a work type |
| `max_running` | Tasks of a work type that may run at once, across all pods | A work type |

```json
{
  "default": {"submit_rate": 2000, "burst": 4000},
  "pools":   {"cpu": {"submit_rate": 500, "max_queued": 200000}},
  "work":    {"cpu/reports.render": {"submit_rate": 50, "max_queued": 10000}}
}
```

- Configured on the controller as `TASKWIRE_LIMITS` (a JSON document, from a ConfigMap). With nothing configured there are no limits, and the controller logs that at start.
- The same document carries the back-pressure settings for each scope: `pace_above` (default 0.7) and `pressure_age_seconds` (unset by default); see "Back pressure".
- `max_running` is the one limit applied at claim, not at submit, and the one that is exact: for a work type that has it, claims are serialised and counted in the backend, so the cap is never exceeded. It is meant for work that calls a rate-limited external service, and it costs claim throughput for that work type only.
- The most specific limit that applies is checked, and every enclosing one as well: a submit must pass its work type's, its pool's, and the installation's.
- **A batch is admitted whole or refused whole.** A refused submit has created nothing, so the guarantees are untouched. A batch larger than the applicable burst can never be admitted and is refused as an invalid argument, not as a rate limit.
- A refusal is `RESOURCE_EXHAUSTED` carrying the reason (`rate` or `backlog`), the scope that refused, and a retry delay. For `rate` the delay is when enough tokens will exist; for `backlog` it is a fixed interval.
- A retry of a task that already exists is answered from the backend without consuming tokens, so retrying after an uncertain response is never penalised.
- **The limits are approximate, by design.** API replicas are stateless. Each enforces its share of a rate (the configured rate divided by the number of live API replicas). Libraries spread their calls across replicas call by call, so a submitter is not held to one replica's share, and `max_queued` is judged against the statistics cache, which is a few seconds old. A limit can therefore be overshot briefly by a burst spread across replicas. Exact global limiting would put a shared counter on every submit, which is not worth its cost for a protective limit.
- **This is protection, not fairness or identity.** Every pod presents the same token, so limits cannot be set per team or per submitter; that arrives with per-team authorization. A limit on a work type protects other work from it; it does not divide capacity among submitters of the same work.


## Back pressure

**Pre-alpha delivers steps 1 and 4** (workers pull; submits are refused at the backlog limit and the library waits and retries). Reported pressure, pacing, `Pressure`, and load shedding are stage two.

When work arrives faster than it can be done, the system pushes back on whoever is submitting, step by step, instead of growing a queue without bound or falling over. Pressure travels from the workers to the submitter through the controller:

```text
  workers busy ──► queue grows ──► pressure rises ──► submitters paced ──► submits refused
  (pull only          (visible as         (reported on        (library slows       (at max_queued,
   for free slots)     depth and age)      every Submit)       its batches)         with a delay)
```

1. **Workers pull, they are never pushed.** A pod receives work only for slots it has free, so a slow or saturated pool simply claims less. Nothing accumulates in a pod.
2. **Pressure is a number.** For each pool and work type the controller computes pressure between 0 and 1: the fraction of `max_queued` in use, or, where no backlog limit is set, the oldest queued age against `pressure_age_seconds` if one is configured (otherwise pressure is reported as unknown and nothing is paced).
3. **Every `Submit` response reports it**, accepted or not, together with a suggested delay before the next batch. Above `pace_above` (default 0.7) the delay grows from zero toward a ceiling as pressure approaches 1. A library honours the delay, so a `map` of a million tasks slows smoothly as the queue fills instead of hitting the limit and stalling.
4. **At 1, submits are refused** with the `backlog` reason and a retry delay (see "Submission limits"). A refused batch has created nothing.
5. **The submitter decides what a wait means.** The library waits and retries up to the call's deadline, then raises. `Pressure(pool, task)` lets an application ask before it produces work at all, for example to stop reading from an upstream source.

**Protecting the backend.** The controller watches its own database: connection-pool wait and call latency. When they pass a threshold it sheds load in a fixed order, so the system keeps draining while it recovers:

| Order shed | Operations | Why |
|---|---|---|
| Never | `Complete`, `Fail`, `Release`, `Heartbeat`, `Goodbye` | They finish work and free capacity. Refusing them would make things worse and cause retries |
| Last | `Claim` | Starting work needs the database, but an idle pool helps nobody |
| Second | `Get`, `Wait`, list operations | Reads can be retried and long polls can be answered empty |
| First | `Submit` | New work is the only load that can wait at its source |

A shed request gets `RESOURCE_EXHAUSTED` with reason `overloaded` and a delay. Shedding is per replica, from that replica's own measurements.

**What back pressure does not do.** It does not drop accepted work: a task that was acknowledged stays durable however high the pressure. It does not reorder or deprioritise queued tasks. It does not slow workers down. And it is not a fairness mechanism between submitters of the same work.

## Results

How results are stored is configured once, on the controller (`TASKWIRE_RESULT_URL`), and told to every library in `Hello`, so all pods agree.

| Channel | `ResultUpload` returns | `ResultDownload` returns | Credentials in application pods |
|---|---|---|---|
| `s3://` | A short-lived signed upload link | A short-lived signed download link | None. Only the controller holds storage credentials, and it signs links without touching the bytes |
| `redis://` | The key and time-to-live to write | The key to read | Redis credentials, supplied to the pods by the deployment |
| `memory://` | A key in the process's own memory | The same | None; single process, for tests |

- Keys are generated by the controller from the task ID and lease, so attempts never share a key.
- **The controller verifies before it records.** A signed S3 upload link is bound to the declared size and SHA-256, so the store itself refuses different bytes. On `Complete` the controller confirms the object or key exists with the declared size before it writes the reference; if it does not, the completion is refused and the attempt fails as `result_publish_failed`. A task is therefore never `succeeded` with a reference to nothing. This costs one metadata request per result and never reads the bytes.
- **How far a library is trusted with results depends on the channel.** With S3 it is not trusted: it holds no credential and can write only the one object it was given a link for. With Redis every pod holds the credential and could write or delete any key. The recorded checksum means a changed result is detected on download (`ResultIntegrityError`) but not prevented. Installations that need results protected from other pods use S3.
- A library uploads, then completes. The controller records the reference atomically with success, together with the publish time and the expiry it derives from the channel's retention.
- A library verifies size and SHA-256 on download.
- The controller checks the channel at start: reachable, writable, and for S3 that a signed upload and download round trip works under a reserved key. A channel whose retention is shorter than task retention is reported as a warning.

## Controller process

```bash
taskwire controller --role api            # serves this API
taskwire controller --role maintenance    # reaper, retention, statistics (Phase 4)
taskwire controller                       # both, for small installations
taskwire migrate
taskwire check
```

- API replicas are stateless. Any replica can serve any request from any session; a session is a token signed with `TASKWIRE_SESSION_KEY`, a secret shared by all replicas, not replica memory. Replicas learn how many of them are alive through the backend (`controller_heartbeat`), which is what a rate limit is divided by.
- Long polls (`Claim`, `Wait`) are held by the replica that received them and woken through the backend's `wait`. A replica coalesces waiting claims into batched backend claims, so database work does not grow with the number of idle pods.
- Database connections scale with controller replicas, not with application pods.
- On `SIGTERM` a replica stops accepting new long polls, answers the ones it holds as empty, and exits within its grace period.

### Availability

The API is on the task path, so the controller must be run replicated.

| Situation | Behaviour |
|---|---|
| One replica lost | Its in-flight requests fail and are retried by the libraries against another replica. No task is lost: nothing is acknowledged before it is durable |
| All replicas unreachable | Running tasks keep running. Libraries hold finished outcomes and report them on reconnect, where fencing decides whether they still count. Submits fail with a retryable error once their deadline passes |
| Unreachable for longer than the liveness period | Instances lose their leases when the maintenance role next runs; the tasks are retried |
| Backend unreachable | The API answers `UNAVAILABLE`; readiness turns false; nothing is acknowledged |

### Connections and load

- gRPC connections are long-lived, and a plain Kubernetes Service would pin each pod to one replica. The chart uses a headless Service with client-side round robin, and the controller sets a maximum connection age so load spreads when replicas are added.
- The controller bounds concurrent requests and waiting long polls. Over the limit it answers `RESOURCE_EXHAUSTED` with a retry delay; libraries back off with jitter.

### Authentication

- Every request carries a bearer token (`TASKWIRE_API_TOKEN`, from a Secret). TLS is enabled by mounting a certificate.
- This is one shared credential per installation, and its reach should be understood plainly: any pod holding it can read or cancel any task, and can register for any task name and so **receive the inputs of tasks meant for other pods**. An installation is one trust domain. Do not share one between teams that must not see each other's task inputs; run separate installations.
- Scoped tokens are the planned remedy (after pre-alpha): each token lists the pools it may submit to and the pools it may serve, checked on `Hello`, `Submit`, and `Claim`.
- The chart ships a `NetworkPolicy` restricting the API to labelled namespaces.

## Library conformance

**Stage two as a published tool.** In pre-alpha the same scenarios run as the Python library's own tests against a real controller.

**Adherence is tested, not assumed.**

```bash
taskwire library-check -- <command that starts the library under test>
```

The check starts a controller on the in-memory backend with a controllable clock and drives the library through scripted scenarios. A library's test build registers a small set of well-known tasks (`taskwire.check.echo`, `.fail`, `.sleep`, `.large`).

Checked: session and revision negotiation; submit retried after a dropped response creates one task; claims never exceed declared slots; heartbeats arrive within the liveness period under load; exactly one outcome per attempt; a cancel request is observed; drain releases unfinished attempts; results are uploaded before completion with a correct checksum; a corrupted download is detected; behaviour through a controller restart.

A library that passes is a supported library. The check is what lets libraries for other languages be written, by anyone, without changes to Taskwire.

## Required tests

- Every operation, success and each documented error, against the in-memory backend and PostgreSQL.
- Misbehaving library: stale and forged attempt tokens, outcomes for attempts it does not hold, claims beyond its slots, completion with a reference the controller did not issue, missing heartbeats, oversized input. None causes a task to be completed wrongly, lost, or run concurrently by two holders.
- Long polls: a waiting claim receives a task submitted afterwards without a polling delay when notification is enabled, and within the poll interval when it is not; a waiting `Wait` returns on completion.
- Claim coalescing: many idle instances cause a bounded number of backend claims per interval.
- Replica loss during submit, claim, and complete: the library retries against another replica; exactly one task and exactly one recorded outcome result.
- Full outage shorter and longer than the liveness period.
- Rolling upgrade: a library on the previous protocol revision works against the current controller.
- Results: signed upload and download round trip against an S3-compatible test server; an expired link is refused; no storage credential is ever sent to a library; no result byte reaches the controller or the backend.
- Authentication: a request without a valid token is refused before any backend call.
- Submission limits: a rate above the limit is admitted at the limit within a stated tolerance across several replicas; a batch is never partly admitted; a refused submit leaves no row; a pool at `max_queued` refuses new work and accepts it again once drained; a retry of an existing task is answered while its scope is limited; claims and outcomes are served at full speed while submits are being refused; an over-burst batch is refused as invalid.
- `max_running`: with many pods claiming one capped work type under load, the number running never exceeds the cap; other work types are unaffected.
- Result verification: a library that completes without uploading, or uploads different bytes than it declared, is refused and the task is retried; the signed link rejects a body of the wrong size or checksum.
- Back pressure: with workers stopped and a submitter running, reported pressure rises from 0 to 1, the suggested delay rises with it, and submits are refused at 1 without a single acknowledged task being lost; with workers restarted, pressure falls and submits resume without operator action. With the backend slowed artificially, submits are shed first and reads second, while outcomes and heartbeats are served throughout and no instance loses its liveness. A library that ignores the suggested delay is still stopped at the limit.
- `taskwire library-check` passes against a reference test library and fails, naming the scenario, against deliberately broken ones.

## Exit gate

A minimal test library speaking only the published protocol submits, claims, runs, and completes tasks end to end through a replicated controller on PostgreSQL; every misbehaving-library case is contained; replica loss and controller restart lose nothing; and `taskwire library-check` passes and fails where it should.
