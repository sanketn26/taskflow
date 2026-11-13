# Design Decisions

A record of the decisions that reset the design in November 2026, and what was removed as a result. Non-normative; the phase documents are the contract.

## Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | Taskwire targets Kubernetes-native distributed parallel processing, not local-first single-host execution | That is the product goal; the earlier plan deferred distribution to a late phase and built on a single-host state model |
| 2 | PostgreSQL is the production state store | The design depends on atomic transactions; `SKIP LOCKED` gives coordination-free claiming; managed and operator-run PostgreSQL provide failover. etcd was rejected: it is sized for small metadata, has no secondary indexes, and the cluster's own etcd is not available on managed Kubernetes |
| 3 | ~~No agent or server on the task path~~ Superseded by 22 | Held while every process connected to PostgreSQL directly |
| 4 | Semantics are defined by a backend-neutral interface, not by PostgreSQL | The guarantees are stated and tested independently of any store. An earlier version of this decision put them in PostgreSQL stored functions; that was reversed because it tied the contract to one database and made it harder to test |
| 5 | ~~Python only~~ Superseded by 23: the controller is Go, libraries are per language | Held while one Python package was the whole system |
| 6 | Workers are pods managed by Kubernetes, not processes supervised by Taskwire | Avoids duplicating the kubelet and lets pools scale as Deployments |
| 7 | Prometheus metrics and the autoscaling signal ship with the first working version | Scaling and operation depend on them |
| 8 | Task state is read by task ID; the per-owner cursor and acknowledgement protocol is dropped | Cursors existed to make a pushed result stream replayable. A client that can query the database does not need them |
| 9 | PostgreSQL is the only production backend; an in-memory backend exists for tests | A second backend keeps the interface honest and makes unit tests database-free. No other production backend is planned |
| 10 | Configuration is environment variables | Fits ConfigMaps and Secrets; removes the dual strict YAML loaders |
| 11 | Every controller process asserts the backend contract at start | Capabilities and settings are checked and the guarantees are probed live. A controller that fails exits and never reports ready, so a misconfigured deployment cannot silently run with weaker semantics. Libraries do not run it; they never touch the backend |
| 12 | Worker liveness is one heartbeat per worker; fencing is one lease ID per task | Keeps writes on the task table to submit, claim, and finish |
| 13 | Workers support process, thread, and async execution | Much fan-out work is I/O-bound, where one task per process wastes a pod |
| 14 | Pods wait for work on a long poll to the controller; inside the controller, polling the backend is the default wake-up and event-driven notification is optional | Idle pods cost almost no database work, and the default needs no extra database connection |
| 15 | No task output passes through the backend, neither between tasks nor back to the submitter. Workers publish results asynchronously to a result channel (S3, Redis, Kafka, or memory); the backend records only state and a small reference | Submitting a task is asynchronous by protocol: the submitter is never owed a value back through the path it submitted on. Also keeps the backend a control plane whose load scales with task count, not data volume; avoids rebuilding a broker or result store inside PostgreSQL; and gives one rule with no inline-size exception. Cost accepted: returning a value needs a result channel in addition to PostgreSQL, and there is no chaining or fan-in primitive |
| 16 | Result tracking is a system capability: the backend records, atomically with success, where each result is, its size, checksum, and expiry; the controller reports, deletes, and sweeps results | Moving result bytes out of the backend must not move the bookkeeping onto the application. Tracking is control-plane data, so it belongs to the backend contract (S12) |
| 17 | Lessons taken from Celery's failure modes are contract rules, not tuning advice: no prefetch, executor recycling, timeouts that are honest per execution mode, no blocking on another task from inside a task, and stranded work is a metric and an alert | Each of these is a known way Celery deployments lose work, deadlock, or stall silently, and each is cheaper to rule out in the contract than to document as a setting |
| 18 | The PostgreSQL backend keeps one row per task for reads and two narrow work-index tables for claiming and reaping; no operation joins task tables | Confines insert-and-delete churn to small tables while every read stays a single-table lookup. A four-table log layout was rejected because every read became a combined read |
| 19 | Tasks have a loose affinity to pods through buckets: a task hashes to a fixed bucket, a bucket table maps buckets to workers, and ownership is assigned by consistent hashing | The indirection means pod churn rewrites table entries, never tasks; consistent hashing limits a membership change to ring neighbours; loose ownership avoids a rebalance protocol and stranded work. Strict per-worker partitions were rejected: pods are disposable, and strict ownership idles workers while another's backlog grows |
| 20 | Task data is partitioned by day on a UUIDv7 task ID, and retention drops partitions. Callers cannot choose task IDs; deduplication uses a separate idempotency key. Retention is one period for all outcomes | Row-by-row deletion is the main source of table churn at volume. Partitioning requires the partition key in the primary key, hence time-ordered IDs, hence a separate key for caller-chosen deduplication. Per-outcome retention was dropped because it cannot be honoured by dropping partitions |
| 21 | ~~Executors speak an executor protocol to a Python supervisor~~ Superseded by 22 and 24: the controller API is the protocol, and there is no supervisor process | The supervisor existed to keep the guarantees away from task code; the controller now does that over the network |
| 22 | Only the controller touches the database. Application pods reach it through the controller API, which is on the task path and runs as a stateless replicated service | One implementation of the guarantees for every language; no database credential in pods that run user code; database connections scale with the controller, not with pods; claims from many idle pods are coalesced. Cost accepted: a Taskwire service on the task path, whose availability multiplies with PostgreSQL's, and a wire protocol to version and secure. Unlike the retired agent it holds no state, forms no cluster, and stores nothing locally |
| 23 | The controller is Go; libraries are written per language, Python first. The controller API is the protocol, the controller treats libraries as untrusted, and a library is supported if it passes `taskwire library-check` | Long-term resource efficiency: one small static binary serves any number of pods, and no extra process runs beside application code. Protocol plus conformance check opens every scenario: any language, embedded or dedicated |
| 24 | There is no distinction between client and worker. One library submits and runs; a pod runs tasks by declaring pools. Where and how tasks run (embedded or dedicated, in-process or child processes) is the application's choice; Taskwire states its recommendation and the trade-off and enforces neither | Applications mix unrelated logic with background processing. Because the controller holds the guarantees, an unwise choice costs a retry, never correctness |
| 25 | With S3, libraries upload and download through short-lived signed links issued by the controller. Redis is the second channel and needs credentials in the pods. Only application pods touch output | Pods hold no storage credential and a library needs no S3 client, which keeps other-language libraries thin. The controller signs without handling the bytes |
| 26 | Observability is provided at three levels — worker pool, work type (task name within a pool), and individual worker — and all three come from the controller. Heartbeats carry a status report; the API role records every outcome; the maintenance role exports the gauges; `taskwire pools`, `work`, and `instances` show the same view | With embedded libraries a pod may expose no metrics of its own, and pool-level numbers hide a single bad pod or a single slow kind of work. The controller sees every outcome once, in any language. Instance IDs are allowed as a label only on gauges that exist while the pod is registered, so cardinality follows live pods |
| 27 | The controller limits submission by rate and by backlog, per installation, pool, and work type. Batches are admitted or refused whole; limits are approximate across replicas; claims and outcomes are never limited | A single submitter must not be able to fill the database or bury other work, and the controller is the one place every submit passes through. Exact global limits were rejected as a shared counter on every submit. Per-submitter limits wait for per-team authorization, since all pods share one token today |
| 28 | Back pressure is explicit and end to end: workers pull only for free slots, the controller reports pressure and a pacing delay on every submit, refuses at the backlog limit, and under database stress sheds submits first, reads second, and never outcomes or heartbeats | Overload should slow the source smoothly rather than grow an unbounded queue or stall at a hard limit, and the system must keep draining while it recovers. Accepted work is never dropped |
| 29 | The controller verifies that a result exists, at its declared size, before recording it; S3 links are bound to size and checksum. With Redis a library is trusted with results, and the docs say so | "Libraries are untrusted" was claimed more widely than the design delivered. A task must never be `succeeded` with a reference to nothing |
| 30 | Statistics are computed by one controller at a time from the narrow work tables, stored, and read by the rest; the refresh interval stretches with its own cost | Per-pool and per-work-type gauges, pressure, and backlog limits all read them. Computing them in every replica on a fixed timer would become a constant scan load as queues grow |
| 31 | Pre-alpha builds a thin slice; partitioning, affinity, rate and concurrency limits, pacing, shedding, Redis, thread and async modes, the conformance tool, and per-worker observability are specified now and built in Phase 6. Schema hooks for them exist from the first migration | Thirty decisions had accumulated before any task had run. The slice reaches a working system soonest, and what it shows should decide the order of the rest |
| 32 | Unmeasured defaults are listed as provisional, each with the test that will set it | Several numbers in the design were invented. Naming them prevents a guess from hardening into a recommendation |
| 33 | Embedding requires an explicit execution mode; a per-work-type concurrency cap (`max_running`) and task retry (`Retry`, a new linked task) are part of the design | Follows "recommend, don't enforce": the library does not pick the riskiest mode silently. The cap and retry were gaps found in review that submit-rate limiting does not cover |

## Removed

- The Go agent, its gRPC control protocol, generated bindings, role interceptors, and configuration loaders.
- The Python gRPC client, protocol validation, YAML configuration loader, agent discovery, and the native-acceleration seam.
- The agent test harness and the tests for all of the above.
- Phase documents for the agent core, gossip clustering, work stealing, distributed storage adapters, the Kafka outbox, Node.js and Go runtimes, and the admin console.

The state before removal is in git history. The last commit containing the agent design is `0df1ce4`.

## Deliberately deferred

- Cron-style and periodic schedules.
- OpenTelemetry tracing (the `trace_context` column is reserved now).
- A web console.
- A terminal-event outbox for Kafka or similar.
- Libraries for Go, Rust, and other languages. The protocol definitions and `taskwire library-check` are what make them possible; none is scheduled.
- Per-team authorization in the controller API. Today one shared token can read or cancel any task.
- An operator with a `WorkerPool` custom resource.

## Carried over unchanged

- At-least-once execution with application-level idempotency for side effects.
- Fenced leases: only the current lease can record an outcome.
- Durable submit: a successful submit means the task is committed.
- Registered `name@version` task identity, with claims filtered by it.
- Workers never connect to submitting applications.
- Tests poll observable state against deadlines, never fixed sleeps, and chaos runs are seeded and reproducible.
