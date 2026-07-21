# Taskwire Vision

This document is non-normative. It captures *why* Taskwire exists and what it is aiming to unlock. Requirements, schemas, failure semantics, and exit gates live in the phase documents under `docs/phases/`. If this vision and a phase disagree, the phase wins.

## Origin

Taskwire started from a practical frustration: **Python does not have a good story for reliable concurrent work.**

That gap is not primarily about missing green threads or a prettier `async` API. It is about what happens when real work must leave the request path:

- run off-process without ad hoc `ProcessPool` glue
- survive crashes and restarts
- bound parallelism and resource use
- get results back after the caller disconnects
- grow from a laptop to more than one machine without switching products

Existing options force a bad ladder of tools:

```text
threads → ProcessPool → RQ/Celery + Redis → cluster-specific job systems
```

Each step changes APIs, failure modes, and the local development story. Taskwire exists so that ladder collapses into **one model**.

## The thesis

Three stacked bets define the product:

1. **Python is the wedge** — the concurrency and durability pain is sharpest there, so Python is the first SDK and worker runtime.
2. **Local and clustered share one model** — zero-infrastructure defaults (SQLite + filesystem) on a single host; optional multi-agent clustering and shared backends later, without rewriting the application contract.
3. **Any language can participate** — a Go control plane and a language-neutral protocol make submitters and workers interchangeable once they speak the wire contract.

Python is *why it exists*. Local-to-cluster is the *scale story*. Multi-language is the *platform story*.

### One-line north star

> A Go control plane for durable, leased task execution that feels local-first, scales to a cluster, and is open to any language through a single protocol — starting where Python’s concurrency story fails people.

## Why not free-threaded CPython (3.13+)

Free-threaded (no-GIL) builds improve **in-process** multi-core use for some pure-Python and carefully updated native code. They are not a concurrency platform for production stacks.

Many established libraries and C extensions still assume GIL-era behavior: single-threaded bytecode semantics, shared mutable state that was “safe enough,” and binary wheels that may not match free-threaded ABIs for a long time. Betting the product on free-threaded CPython would mean waiting on ecosystem and ABI readiness that large parts of scientific, ML, and native Python may not deliver for years.

Taskwire deliberately does **not** chase that story.

| Free-threaded CPython optimizes | Taskwire standardizes |
|---------------------------------|------------------------|
| Parallelism *inside* one process | Work *outside* the request process |
| Interpreter and extension safety | Leases, durability, isolation, recovery |
| A subset of builds and dependencies | Default CPython and process boundaries |

Process-level isolation already matches how serious Python systems survive concurrency pain: separate workers, crash domains, restarts, and resource limits. Free-threading can still be used *inside* a worker when a stack supports it. Taskwire owns the layer *around* workers — reliability, pools, and distribution — so the product does not depend on every dependency becoming free-threaded-safe.

## System shape (vision level)

```text
any language SDK          Go agent                     workers (any language)
submit / reattach  ───►   queue · leases · state   ◄──  pull · heartbeat · complete
                          objects · result replay
                          local ····· cluster
```

- The **agent** owns transactional task state, fencing leases, immutable objects, capability-aware pools, and optional clustering or integrations.
- **Applications** submit registered task identities and values (or object references); they receive durable submit ACKs and replayable results.
- **Workers** never connect to applications. They register runtime, codecs, and exact `name@version` capabilities; they complete under a lease.
- **Portable tasks** use a shared value profile (msgpack / bytes). Language-specific shortcuts (for example Python cloudpickle or inline functions) remain explicit non-portable capabilities.

Details and normative behavior: [architecture.md](architecture.md), [storage.md](storage.md), and the phase set.

## What this unlocks

### Reliable concurrency without a new programming model

Developers keep writing ordinary functions (or the equivalent in other languages). Taskwire supplies what language runtimes rarely provide together:

- off-request-path execution
- crash survival for submitted work
- bounded pools, labels, and backpressure
- honest failure (attempts, dead letters, timeout ≠ cancelled)

Concurrency becomes an **ops and durability concern**, not “rewrite everything as async” or “hope the process pool finishes.”

### One mental model from laptop to fleet

```text
local agent (SQLite / filesystem)
  → multi-worker on one host
  → multi-agent cluster
  → optional shared PostgreSQL / S3
  → optional Kafka terminal-event outbox
```

Config and topology grow. The application contract — submit, lease, complete, reattach — does not. Local demos stop lying about production.

### Polyglot execution as a normal choice

Once the agent is the product and SDKs are bindings:

- submit in language A, execute in language B
- rewrite hot tasks in another runtime without changing callers (`name@version` + capabilities)
- one ops surface for a mixed organization instead of one job system per language
- best tool per job (for example Python for ML/data, Go for tight workers, Node for existing web workers)

Language choice becomes **per-task and operational**, not **per-platform**.

### Platform ownership without a broker zoo

Platform engineers own the agent, config, retention, metrics, routing, and storage. Application teams own registered tasks, idempotency where side effects matter, and submit/await/reattach in their SDK. Background work stops being reinvented per service.

### System shapes that become natural

| Shape | Why it fits |
|-------|-------------|
| API → durable job → result | Submit + reattach; client disconnect does not lose durable work |
| Batch pipelines | Backpressure, object refs for large I/O, gather with timeouts |
| Capability routing | GPU vs CPU vs tool-specific worker labels |
| Crash-safe CLIs and local tools | Durable queue without Redis on day one |
| Incremental modernization | Replace task implementations language-by-language |
| Downstream integration | Terminal events (for example Kafka) as *notifications about completion*, not the source of truth for jobs |

### Trustworthy failure

At-least-once execution, fenced completion, result cursor replay, and recovery of both Runtime and agent are first-class. Teams can build product behavior on background work instead of treating jobs as best-effort fire-and-forget.

## Good fits and poor fits

**Good fits** include trusted services that need low-ops background work; single-node durable execution that may later grow; registered, deploy-aligned task versions; workloads comfortable with at-least-once semantics and app-level idempotency for side effects; polyglot fleets that want one control plane.

**Poor fits** include untrusted multi-tenant code execution; hard real-time scheduling; exactly-once side effects without application idempotency; cancellation of running or remotely owned work as a hard requirement in early versions; environments that cannot align task deployments with worker capabilities.

Phase 9 will expand adoption guidance and runnable examples; see [phases/phase-9-examples-use-cases.md](phases/phase-9-examples-use-cases.md).

## Non-goals (honest boundary)

Taskwire is not trying to be:

- a replacement for free-threaded CPython or structured in-process concurrency
- an exactly-once distributed transaction system
- a sandbox for untrusted multi-tenant code
- a full long-running workflow engine (Temporal-class sagas) on day one
- a product that requires Redis, Kafka, or a cloud account for the default path

The boundary that matters:

> **Durable, multi-language, local-to-cluster task execution** — not “all concurrency and distributed-systems problems.”

## Delivery posture

- **MVP (Phases 0–4):** single-node agent, Python worker and SDK, SQLite/filesystem defaults, durable submit and result replay.
- **Post-MVP gates:** clustering (Phase 5), distributed storage backends (Phase 6), Kafka outbox (Phase 7), then hardening and examples.
- **Further runtimes:** Node.js and Go SDKs/workers bind the same protocol (Phases 10–11); additional languages follow the same conformance path.

Ship the Python local durability story first. Multi-language and cluster are openings of the same idea, not a second product.

## Related documents

- [architecture.md](architecture.md) — system shape and delivery sequence
- [storage.md](storage.md) — task state vs immutable objects
- [implementation-plan-review.md](implementation-plan-review.md) — resolved scope decisions
- [phases/](phases/) — authoritative implementation contracts
