# Testing Harness

One harness, growing with the phases. Every phase doc lists *what* to assert; this doc defines the machinery that makes those assertions cheap to write — and the chaos layer that finds the gaps the happy-path suites cannot.

## Goals

1. **One-line setup for any topology** — a unit test, a single agent, or a 3-node cluster with Kafka should each be one fixture away.
2. **Chaos is a first-class tier, not an afterthought** — every distributed-systems claim in `architecture.md` (at-least-once, lease expiry, WAL recovery, forwarded-task ownership) has a chaos scenario that tries to falsify it.
3. **Invariants over examples** — chaos tests don't assert specific outcomes (timing makes them flaky); they assert invariants that must hold under *any* interleaving.
4. **No new infrastructure** — chaos via process signals and an in-process TCP proxy. No Jepsen cluster, no Kubernetes, no root.

---

## Layout

```
python/tests/
├── harness/
│   ├── __init__.py
│   ├── agent.py            AgentHarness — spawn/manage one taskwire-agent
│   ├── cluster.py          ClusterHarness — N agents, port allocation, seeds
│   ├── proxy.py            ChaosProxy — TCP proxy with drop/delay/duplicate/partition
│   ├── chaos.py            ChaosController — composes process + network faults
│   ├── ledger.py           Execution ledger task + invariant checker
│   └── conftest_plugin.py  pytest fixtures: agent, cluster, chaos, ledger
├── unit/                   no processes (Phase 1+)
├── integration/            real agent via AgentHarness (Phase 2+)
├── chaos/                  ChaosController scenarios, -m chaos (Phase 2+)
└── benchmark/              (Phase 7)

agent/internal/faults/      Go fault-injection points, built only with -tags chaos
agent/internal/testutil/    Go test client (raw frames), tempdir config builder
```

---

## Core Components

### `AgentHarness` (`harness/agent.py`)

Owns one `taskwire-agent` subprocess and everything it needs.

| Member | Responsibility |
|--------|----------------|
| `__init__(persistence="none", workers=2, config_overrides=None)` | Build a YAML config in a `tmp_path`: unique socket path, ephemeral gossip port, `wal_dir` under tmp. Write it; do not start. |
| `start(timeout=5.0)` | Spawn the binary (path from `TASKWIRE_AGENT_BIN` env or `make build` output). Poll for socket file existence — never `sleep(2)` and hope. Capture stdout/stderr to per-test log files (attached to the pytest report on failure). |
| `stop()` | SIGTERM, wait 5s, SIGKILL fallback. Assert no orphaned worker PIDs remain (invariant I5). |
| `kill()` | SIGKILL immediately — the crash primitive for chaos tests. |
| `restart()` | `kill()` + `start()` on the same config/`wal_dir` — the WAL-recovery primitive. |
| `status()` | Run `taskwire-agent status --json` against the socket (Phase 7+); earlier phases use the raw STATUS frame via the test client. |
| `worker_pids()` | Parse from status; lets chaos target workers directly. |

Context manager; the pytest `agent` fixture yields a started instance and guarantees teardown even on test failure.

### `ClusterHarness` (`harness/cluster.py`)

| Member | Responsibility |
|--------|----------------|
| `__init__(n, encryption_key=None, via_proxy=False)` | Allocate n disjoint port sets. Node 0 is the seed. When `via_proxy=True`, each node's `advertise_addr` and cluster TCP endpoint are routed through a per-link `ChaosProxy`, making partitions injectable. |
| `start_all()` / `node(i)` / `stop_node(i)` / `kill_node(i)` | Lifecycle. |
| `wait_converged(timeout)` | Poll each node's member list until all see n members — replaces every `sleep(2)` in Phase 5 tests. |

### `ChaosProxy` (`harness/proxy.py`)

A ~150-line asyncio TCP proxy. Sits between any two endpoints the harness wires through it (cluster TCP links, result callback paths, Kafka bootstrap). Faults are set per-direction at runtime:

| Fault | Effect |
|-------|--------|
| `partition()` / `heal()` | Drop all bytes (connection appears dead, not refused — the nastier case) |
| `delay(ms, jitter)` | Latency injection |
| `drop(p)` | Drop each new connection with probability p |
| `duplicate(p)` | Replay a completed connection's bytes once more — tests at-least-once dedup (`_pending.pop(..., None)`) |
| `truncate(p)` | Close mid-frame — tests `io.ErrUnexpectedEOF` / `ProtocolError` paths |

Why not toxiproxy/iptables: an in-process proxy needs no daemon, no root, works on macOS and in CI containers, and the harness can assert on what it saw (bytes forwarded, connections dropped).

### `ChaosController` (`harness/chaos.py`)

Composes process-level and network-level faults behind one API so scenarios read declaratively:

```python
def test_node_death_during_lease(cluster, chaos, ledger):
    futures = ledger.submit_many(cluster.client(0), n=50, duration_s=2)
    chaos.after(0.5).kill_node(1)            # node 1 holds ~half the leases
    ledger.await_all(futures, timeout=60)
    ledger.check_invariants(allow_duplicates=True)   # I1, I2, I4, I5
```

Process faults: `kill_node`, `kill_worker(pid)`, `pause_worker(pid)` (SIGSTOP — heartbeats stop while the process lives, the exact lease-expiry-under-life case), `resume_worker`, `restart_agent`. Scheduling: `after(s)`, `every(s, jitter)`, `during(fn)`.

### Execution ledger (`harness/ledger.py`)

The heart of invariant checking. `ledger.task` is a `@task` whose body appends `(task_id, pid, monotonic_ns)` to an append-only file in `tmp_path` (one file per worker, merged at check time — no cross-process locking). After a scenario:

| Invariant | Assertion |
|-----------|-----------|
| **I1 — no acknowledged task lost** | every submitted task_id appears in the ledger or the dead-letter list. With `persistence: "none"` and an agent kill, the *documented* loss is asserted instead — the test pins the guarantee's boundary in both directions. |
| **I2 — liveness** | every future resolves or raises within the scenario bound; zero hung futures. |
| **I3 — duplicates only with cause** | a task_id executed >1× is allowed iff chaos induced a lease expiry/requeue for it; spontaneous duplicates fail the run. |
| **I4 — agent survives** | agent process alive at scenario end (unless the scenario killed it), zero ERROR-level panics in captured logs, malformed input never crashes it. |
| **I5 — no leaks** | no orphan worker processes, no leftover socket files, queue depth returns to 0. |
| **I6 — conservation** | submitted == completed + dead-lettered + still-queued, measured via `status()`. |

### Go-side fault injection (`agent/internal/faults`)

Network/process chaos can't reach inside the agent (e.g. "crash between WAL Put and in-memory Push"). A tiny failpoint package, compiled only with `-tags chaos`:

```go
// faults.Maybe("wal.between_put_and_push") — no-op in normal builds.
// Chaos builds read TASKWIRE_FAULTS="wal.between_put_and_push:panic:1.0,ipc.ack:drop:0.05"
```

Failpoints to plant as the code is written (retrofitting them is how they never happen): `wal.between_put_and_push`, `lease.expire_during_release`, `ipc.ack_drop`, `steal.reply_truncate`, `forward.complete_drop`.

---

## Chaos Scenario Catalog (by phase)

Each scenario names the claim it attacks. All run the ledger invariants unless noted.

### Phase 2 — single agent

| Scenario | Attacks the claim |
|----------|-------------------|
| `kill -9` agent with queued tasks, restart (`persistence: wal`) | "WAL survives restart" — I1 |
| same, `persistence: none` | documents the loss boundary explicitly |
| SIGSTOP the test client holding a lease | lease expiry fires on *silent* peers, not just dead ones |
| garbage bytes / truncated frames / `pay_len=4GiB` to the socket | "max_frame_size, ProtocolError" — I4; also run `atheris`/`go-fuzz` on the codecs here |
| 10k submits in a tight loop, then kill mid-burst | WAL batch-commit window loses nothing acknowledged |

### Phase 3 — workers

| Scenario | Attacks the claim |
|----------|-------------------|
| `kill -9` worker mid-task | requeue + respawn; exactly-once-per-attempt ledger accounting — I3 |
| SIGSTOP worker for 2×TTL, then SIGCONT | lease expires, task re-runs elsewhere, *resumed* worker's late RESULT is discarded not double-resolved |
| GIL-hog task (tight C loop) with 1s TTL | Rust heartbeat immunity (`test_heartbeat_survives_gil_hog` is the unit form; this is the cluster form) |
| callback listener down during delivery, comes back | direct-delivery retry/backoff; `DeliveryFailedError` after exhaustion |
| poison task (segfaults the worker via ctypes) submitted 10× | dead-letter after max_attempts; respawn rate-limit prevents fork-storm — I6 |

### Phase 4 — SDK

| Scenario | Attacks the claim |
|----------|-------------------|
| kill the Runtime process mid-flight, workers still delivering | workers handle refused callbacks gracefully; agent unaffected — I4 |
| `duplicate(p=0.2)` on the result path via ChaosProxy | duplicate RESULT frames resolve futures exactly once — I3 |
| `truncate(p=0.1)` on result path | partial frames don't kill the result server thread (the "every future hangs forever" failure mode) |
| cancel storm: submit 1000, cancel all concurrently with worker pulls | every future ends terminal (result, cancelled, or error); none hung — I2 |

### Phase 5 — cluster (the main event)

| Scenario | Attacks the claim |
|----------|-------------------|
| kill node holding forwarded tasks | forwarded-task ownership: origin re-queues from its shadow map — I1 |
| partition node A↔B for 30s, heal (`via_proxy=True`) | no task lost, no permanent split-brain in member lists; duplicates allowed and attributed — I3 |
| asymmetric partition (A sees B, B doesn't see A) | gossip + lease machinery converge; no requeue storm |
| flapping node (kill/restart every 5s, 10×) | membership churn doesn't wedge the scheduler or leak shadow entries — I5 |
| steal storm: 1 loaded node, 5 empty ones | no task ping-pong (the never-re-forward rule), no duplicate steals of one task |
| STEAL from unauthenticated client during chaos | auth holds under load — connection refused, zero task bytes leaked |

### Phase 6 — Kafka delivery

| Scenario | Attacks the claim |
|----------|-------------------|
| stop Kafka container mid-delivery, restart | worker `flush()` blocks/retries; no silent result loss — I1 |
| Runtime restart + `reattach` with backlog | futures resolve from retained messages |
| two Runtimes, one host, chaos on both | results route to the correct process (consumer-group regression, now under churn) |

### Cross-phase resource chaos (Linux CI only, `-m chaos_resource`)

| Scenario | Attacks |
|----------|---------|
| `wal_dir` on a 1MB loopback fs, submit until ENOSPC | agent degrades with clear errors (rejects SUBMITs), doesn't corrupt the WAL, recovers when space frees |
| `ulimit -n 64` on the agent | fd exhaustion under connection churn → backpressure, not crash |
| worker at `max_memory_mb` cap | resource limit enforcement path |

---

## CI Tiers

| Tier | Trigger | Contents | Budget |
|------|---------|----------|--------|
| unit | every push | `tests/unit`, Go `go test ./...`, no processes | < 1 min |
| integration | every push | `tests/integration` via AgentHarness, single node | < 5 min |
| chaos | nightly + pre-release, Linux | `-m chaos`, each scenario 3× (interleavings differ), seeds logged for replay | < 45 min |
| fuzz | weekly | `go-fuzz` on `protocol.Decode`, `atheris` on `FrameCodec.decode` + msgpack envelopes, corpus committed | 4 h |

Chaos runs are **seeded** (`TASKWIRE_CHAOS_SEED`): all timing jitter and fault probabilities derive from one logged seed, so a nightly failure reproduces locally with one env var. A flaky chaos test is a *finding* — triage it as a bug (in the product or in an invariant's precision), never mark it retry-until-green.

---

## Implementation Guide

The harness is built in slices, each landing *with* its phase — building it all upfront means building against APIs that don't exist yet:

1. **With Phase 1**: nothing — unit tests need no harness. Plant the fuzz targets, though; the codec corpus starts here.
2. **With Phase 2**: `AgentHarness`, the Go raw-frame test client, the first chaos tests (kill/restart, garbage input). The `faults` package goes in *now*, with the WAL failpoints, while that code is being written.
3. **With Phase 3**: `ledger.py` + invariant checker, worker-targeting chaos (`worker_pids`, SIGSTOP/SIGKILL primitives).
4. **With Phase 4**: `ChaosProxy` (the result callback path is the first thing worth proxying), duplicate/truncate scenarios.
5. **With Phase 5**: `ClusterHarness` + `via_proxy` partitions — the full catalog above.
6. **With Phase 6/7**: Kafka container management, resource chaos, and wiring the chaos tier into release gating: **a release candidate ships only after a green nightly chaos run on that exact commit.**

Gotchas:

- **Every wait is a poll with a deadline.** `wait_converged`, socket-file polling, ledger draining — no bare `sleep`. Chaos tests are only as trustworthy as their synchronisation.
- **Capture everything**: agent logs, worker logs, proxy byte counts, the chaos action timeline (action, target, monotonic timestamp). A failed chaos run without a timeline is unreproducible noise; the pytest plugin attaches all of it to the report.
- **Keep scenarios small and named for the claim they attack.** One fault class per test. A scenario that injects five faults and fails tells you nothing.
- **The ledger file is append-only per worker, merged at check time** — the moment the harness needs cross-process locks, it has become a distributed system that itself needs testing.
