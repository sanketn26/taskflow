# Implementation Plan — Critical Review and Corrected Gates

## Review Outcome

The proposed architecture is viable for a pre-alpha, but the original plan was not implementation-ready. It mixed an at-least-once *execution* guarantee with an unacknowledged result transport, omitted lease timing from the shared configuration, left cancellation durability unspecified, made cluster authentication optional despite claiming secure defaults, and deferred packaging until after the `pip install` MVP milestone.

This document is the decision log and delivery gate for the phase documents. If a phase document conflicts with this file, update the phase document before implementing it; do not resolve contract questions ad hoc in code.

## Corrected Semantics

### Guarantee boundaries

Taskwire provides at-least-once execution only after the submitting Runtime receives a SUBMIT ACK. With durable SQLite or PostgreSQL task state, that ACK follows durable input-object storage and task creation. With the explicit in-memory state backend, an agent restart may lose acknowledged work; worker failure alone does not.

Results are agent-relayed and reference-based. A worker writes immutable result bytes through its agent, then performs a fenced COMPLETE containing the result reference. The agent atomically records terminal state before notifying the Runtime over its existing local connection. Runtime reconnect/replay uses an owner ID and cursor; workers never connect to application callback listeners. The authoritative contract is `docs/storage.md`.

Queue delivery is broker-acknowledged. COMPLETE follows a successful Kafka delivery report, not merely `produce()` or a local buffer flush. Runtime restart/re-attach is supported only in queue mode and requires the application to persist task IDs.

Neither mode promises exactly-once execution. A late result from an expired lease can race a retry. For v0.1, the first receiver-accepted result wins; `lease_id` is logged and retained for diagnosis. Fenced/transactional result acceptance is explicitly out of scope and must not be implied by docs.

### Security boundary

Clustering is disabled by default. Enabling it requires a 32-byte encryption key unless `cluster.allow_insecure: true` is explicitly set. The override is development-only and emits a prominent warning. The local Unix socket remains an arbitrary-code-execution boundary because payloads use cloudpickle.

### Cancellation boundary

Cancellation is best-effort and succeeds only while a task is queued at its current owner. A successful cancel atomically removes the task from the in-memory queue and durable store before ACK. A leased, forwarded, stolen, completed, or unknown task returns "too late" in v0.1. Distributed cancellation is deferred rather than pretending a local lookup is sufficient.

## Gaps Fixed in the Plan

| Gap | Correction | Required verification |
|---|---|---|
| Worker-to-app callback creates NAT, lifecycle, and loss races | Relay results through the origin agent and persist result metadata before notification | Kill Runtime before notification ACK; reconnect resumes the result cursor |
| Lease TTL hard-coded but central to correctness | Add `queue.lease_ttl_ms`; heartbeat interval derives from it | Both config parsers reject values below 1,000 ms; short-TTL integration test |
| Secure-default claim contradicted config | Add `cluster.enabled` and `cluster.allow_insecure`; require key by default | Config parity tests and unauthenticated cluster startup refusal |
| Cancellation needs durable atomicity | Make queued → cancelled a conditional `TaskStateStore` transition | Storage reopen after successful cancel does not recover the task |
| Submit ACK reader has no failure contract | Runtime tracks `submitting` until ACK, uses bounded `submit_ack_timeout_ms`, and removes/fails pending entries on rejection or connection loss | Disconnect-before-ACK test has no leaked Future |
| Persistent submitter connection retained forever | Connection registry is synchronized and unregisters all owned task IDs on disconnect/terminal completion | Connection churn test shows bounded registry |
| Shutdown order contradicted itself | Stop accepting, let workers drain within a deadline, stop workers, then stop leases/store | Shutdown integration test; no new work accepted during drain |
| Packaging postponed beyond MVP claim | Establish build backend, native fallback, editable install, and agent discovery in Phase 0 | Clean-venv smoke test before Phase 1; wheel smoke test at MVP gate |
| Phase tests use bare sleeps | All process/network waits poll observable state with deadlines | Harness lint/review rule; no fixed sleeps for readiness/convergence |
| Scope is too broad for one release | MVP is Phases 0–4 with single-node agent-relayed results; clustering and Kafka are post-MVP feature gates | Separate release criteria below |

## Revised Delivery Order

### Phase 0 — repository and packaging spine

- Create the Python, Go, and Rust source roots with one import/build smoke test each.
- Choose one Python build frontend that can produce the Rust extension and include the Go binary; prove it with a minimal wheel. Do not assume Poetry + maturin + cibuildwheel compose automatically.
- Add CI for Python 3.11–3.13, Go, formatting, and the pure-Python fallback.
- Make `make build-sdk` run from the repository root and make `agent-run` use the repository's actual example config path.

Exit gate: a clean virtual environment can install the wheel, import `taskwire`, and locate or clearly report the absence of the agent binary.

### Phases 1–2 — contract and durable single-node core

Implement protocol/config first, including strict envelope validation, task/object references, result cursors, lease timing, cluster security switches, and error replies. Then implement `TaskStateStore`, `ObjectStore`, cancellation, status, and connection lifecycle. SQLite/filesystem crash tests and cancellation durability land with storage, not later.

Exit gate: cross-language protocol/config parity, race-enabled Go tests, fuzz smoke corpus, SQLite/filesystem recovery, cancellation recovery, lease fencing, graceful shutdown, and malformed-frame survival.

### Phases 3–4 — direct-mode MVP

Implement worker and SDK against the acknowledged-result contract. The Runtime must bound SUBMIT ACK waits and terminalize every Future on shutdown or connection failure. The native heartbeat is an accelerator/correctness aid for GIL-holding tasks, but the MVP remains functional without it with documented TTL limitations.

Exit gate: clean-wheel, single-node end-to-end test on every supported Python version; worker crash, Runtime crash-before-result-ACK, delivery failure, duplicate result, cancellation race, and shutdown tests. This is the first publishable pre-alpha.

### Phase 5 — clustering feature gate

Ship only after durable shadow ownership is modeled as an explicit state machine (`queued`, `forwarding`, `forwarded`, `leased`, `terminal`) with crash recovery. Membership failure is suspicion, not proof of peer death, so duplicates remain allowed. Cluster mode requires authentication by default.

Exit gate: partition and restart tests demonstrate conservation; no task ping-pong; shadow entries are bounded and recoverable.

### Phase 6 — Kafka feature gate

Define Kafka topic retention, maximum result size, producer delivery timeout, consumer offset/commit behavior, and reattach API before coding. Per-Runtime consumer groups are correct but scale as O(runtimes × all results); document an operational ceiling and benchmark it.

Exit gate: broker outage/recovery, Runtime reattach, duplicate delivery, poison message, and two-Runtime isolation tests.

### Phase 7 — hardening and release

Release work is continuous, not a final integration phase. Observability counters, structured error codes, resource cleanup, and install smoke tests land with the feature that needs them. Phase 7 adds service management, performance evidence, security documentation, artifact signing/SBOM, and release automation.

## Deferred or Rejected for v0.1

- Exactly-once execution or result delivery.
- Cancellation of leased or remotely owned tasks.
- Windows service management until Unix-domain-socket and process-control behavior has a tested Windows design.
- Automatic agent binary download during package import/install.
- Claiming restart durability when `storage.state.type: memory`.
- Claiming the pure-Python heartbeat is safe for arbitrary GIL-holding native code.

## Definition of Done for Every Phase

A phase is complete only when its acceptance tests run from a clean checkout, failure-path tests exist alongside happy paths, configuration and protocol changes are cross-language compatible, user-visible guarantees are documented without stronger wording than the tests prove, and the previous phase's full suite remains green. File lists and code sketches are guidance, not completion evidence.
