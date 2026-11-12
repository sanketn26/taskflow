# Design Decisions

A record of the decisions that reset the design in November 2026, and what was removed as a result. Non-normative; the phase documents are the contract.

## Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | Taskwire targets Kubernetes-native distributed parallel processing, not local-first single-host execution | That is the product goal; the earlier plan deferred distribution to a late phase and built on a single-host state model |
| 2 | PostgreSQL is the production state store | The design depends on atomic transactions; `SKIP LOCKED` gives coordination-free claiming; managed and operator-run PostgreSQL provide failover. etcd was rejected: it is sized for small metadata, has no secondary indexes, and the cluster's own etcd is not available on managed Kubernetes |
| 3 | No agent or server on the task path | With PostgreSQL as the store, the agent's broker and storage roles are redundant. Clients and workers reach the store directly, through the backend interface |
| 4 | Semantics are defined by a backend-neutral interface, not by PostgreSQL | The guarantees are stated and tested independently of any store. An earlier version of this decision put them in PostgreSQL stored functions; that was reversed because it tied the contract to one database and made it harder to test |
| 5 | Python only; the Go code is retired | One codebase and no cross-language parity work. Other-language SDKs are not planned |
| 6 | Workers are pods managed by Kubernetes, not processes supervised by Taskwire | Avoids duplicating the kubelet and lets pools scale as Deployments |
| 7 | Prometheus metrics and the autoscaling signal ship with the first working version | Scaling and operation depend on them |
| 8 | Task state is read by task ID; the per-owner cursor and acknowledgement protocol is dropped | Cursors existed to make a pushed result stream replayable. A client that can query the database does not need them |
| 9 | PostgreSQL is the only production backend; an in-memory backend exists for tests | A second backend keeps the interface honest and makes unit tests database-free. No other production backend is planned |
| 10 | Configuration is environment variables | Fits ConfigMaps and Secrets; removes the dual strict YAML loaders |
| 11 | Every process asserts the backend contract at start | Capabilities and settings are checked, and workers and the controller probe the guarantees live. A process that fails exits and never reports ready, so a misconfigured deployment cannot silently run with weaker semantics |
| 12 | Worker liveness is one heartbeat per worker; fencing is one lease ID per task | Keeps writes on the task table to submit, claim, and finish |
| 13 | Workers support process, thread, and async execution | Much fan-out work is I/O-bound, where one task per process wastes a pod |
| 14 | Polling is the default wake-up; event-driven notification is optional | Works through a connection pooler with one connection per worker |
| 15 | No task output passes through the backend, neither between tasks nor back to the submitter. Workers publish results asynchronously to a result channel (S3, Redis, Kafka, or memory); the backend records only state and a small reference | Submitting a task is asynchronous by protocol: the submitter is never owed a value back through the path it submitted on. Also keeps the backend a control plane whose load scales with task count, not data volume; avoids rebuilding a broker or result store inside PostgreSQL; and gives one rule with no inline-size exception. Cost accepted: returning a value needs a result channel in addition to PostgreSQL, and there is no chaining or fan-in primitive |
| 16 | Result tracking is a system capability: the backend records, atomically with success, where each result is, its size, checksum, and expiry; the controller reports, deletes, and sweeps results | Moving result bytes out of the backend must not move the bookkeeping onto the application. Tracking is control-plane data, so it belongs to the backend contract (S12) |
| 17 | Lessons taken from Celery's failure modes are contract rules, not tuning advice: no prefetch, executor recycling, timeouts that are honest per execution mode, no blocking on another task from inside a task, and stranded work is a metric and an alert | Each of these is a known way Celery deployments lose work, deadlock, or stall silently, and each is cheaper to rule out in the contract than to document as a setting |
| 18 | The PostgreSQL backend keeps one row per task for reads and two narrow work-index tables for claiming and reaping; no operation joins task tables | Confines insert-and-delete churn to small tables while every read stays a single-table lookup. A four-table log layout was rejected because every read became a combined read |
| 19 | Tasks have a loose affinity to pods through buckets: a task hashes to a fixed bucket, a bucket table maps buckets to workers, and ownership is assigned by consistent hashing | The indirection means pod churn rewrites table entries, never tasks; consistent hashing limits a membership change to ring neighbours; loose ownership avoids a rebalance protocol and stranded work. Strict per-worker partitions were rejected: pods are disposable, and strict ownership idles workers while another's backlog grows |
| 20 | Task data is partitioned by day on a UUIDv7 task ID, and retention drops partitions. Callers cannot choose task IDs; deduplication uses a separate idempotency key. Retention is one period for all outcomes | Row-by-row deletion is the main source of table churn at volume. Partitioning requires the partition key in the primary key, hence time-ordered IDs, hence a separate key for caller-chosen deduplication. Per-outcome retention was dropped because it cannot be honoured by dropping partitions |

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
- SDKs in other languages.
- An operator with a `WorkerPool` custom resource.
- A network gateway in front of the backend interface, should multi-tenant isolation or connection fan-in require one.
- Fine-grained database roles. Any credential can read or cancel any task by ID, so Taskwire does not isolate teams sharing one backend.

## Carried over unchanged

- At-least-once execution with application-level idempotency for side effects.
- Fenced leases: only the current lease can record an outcome.
- Durable submit: a successful submit means the task is committed.
- Registered `name@version` task identity, with claims filtered by it.
- Workers never connect to submitting applications.
- Tests poll observable state against deadlines, never fixed sleeps, and chaos runs are seeded and reproducible.
