# Phase 5 — Clustering

## Goal

Add authenticated discovery, capability-aware routing, forwarding, and work stealing without weakening the single-node storage, ownership, lease, or result-replay guarantees.

Clustering is a post-MVP feature gate. Membership suspicion is not proof of death, so at-least-once execution may create duplicates during partitions; lease fencing and origin ownership prevent conflicting terminal state.

## Phase 0 Baseline

Clustering extends the same packaged `taskwire-agent`; it is not a separate binary or Python service. Multi-agent tests compose multiple instances of the existing `AgentHarness`, each with an isolated base directory, socket, logs, state, and objects. `harness/cluster.py` owns that composition and `harness/proxy.py` adds network faults while all readiness uses `wait_until` and all fault actions are recorded in each run's seeded `ChaosTimeline`.

Use the existing `cluster` and `chaos` pytest markers. Cluster-disabled tests remain part of the infrastructure-free integration suite, and the Phase 0 wheel/discovery/version tests plus the complete single-node suite must stay green.

## Testable Outcome

Multiple agents discover one another, authenticate cluster traffic, route tasks by labels, balance eligible queued work, return fenced result references to the origin, recover forwarding state across restarts, and converge after partitions without losing acknowledged tasks.

## Topology and Security

- Memberlist owns the gossip bind port; task forwarding uses the separate configured task port.
- `cluster.enabled: false` starts no gossip or cluster task listener.
- Enabled clustering requires a decoded 32-byte key unless the explicit development-only `allow_insecure` switch is true.
- Gossip uses memberlist encryption. Every task-channel connection also uses mutual challenge/response HMAC with nonces, timestamp skew bounds, and replay protection.
- The cluster listener accepts only cluster message types; `STATUS`, Runtime result resume, and local object-path operations are never exposed remotely.
- Frame and request limits apply before authentication and before allocation.

The task-channel handshake is `AUTH_CHALLENGE {node_name, nonce: binary(32), unix_ms: int64}` followed by `AUTH_RESPONSE {node_name, peer_nonce: binary(32), nonce: binary(32), unix_ms: int64, mac: binary(32)}` and a reciprocal response. The HMAC-SHA256 input is the protocol version, both length-prefixed node names, both nonces in challenge order, and both big-endian timestamps. Each side verifies advertised membership identity, constant-time MAC equality, `cluster.auth_clock_skew_ms`, and a bounded nonce replay cache before accepting Phase 1 frames. Authentication messages use a cluster transport preface and are not valid on local IPC; no unauthenticated payload is decoded beyond its fixed maximum.

## Ownership Model

The submitting agent is the immutable origin/owner for a task in v0.1. Its durable state machine is:

```text
queued → forwarding → forwarded → terminal
   ↘ leased → terminal
```

Forwarding metadata includes task ID, target node, transfer ID, state, attempt, and timestamps. It is stored transactionally in `TaskStateStore`; an in-memory shadow map is only a cache.

### Transfer protocol

1. Origin conditionally moves an eligible queued task to `forwarding` and creates a unique `transfer_id`.
2. Origin sends the task envelope/input references plus transfer ID.
3. Remote agent idempotently imports it as a foreign task and ACKs only after its configured durability boundary and required object transfer.
4. Origin conditionally moves `forwarding` → `forwarded` after the matching ACK.
5. Lost responses are retried with the same transfer ID. Conflicting content is rejected.

A crash during any step is recovered from persisted transfer state. Timeouts return uncertain transfers to reconciliation, not immediately to two runnable queues.

## Object Placement

- Shared S3 references may be relayed only when both nodes advertise the same validated store identity and policy. (The S3 adapter itself is Phase 6 scope; this section only fixes the placement/policy contract clustering depends on once that adapter exists.)
- Filesystem or node-local references are copied/proxied agent-to-agent as immutable streams before the receiving task becomes claimable.
- Transfers verify size and SHA-256, use idempotent object keys, and clean partial files.
- Workers always access objects through their local agent and never receive remote filesystem paths or Runtime addresses.

## Remote Execution and Completion

The remote agent grants and fences the worker lease locally. On completion it records the foreign terminal attempt, then sends the origin a completion containing task ID, transfer ID, lease/attempt diagnostics, result reference/failure, and any required object bytes/reference mapping.

The origin accepts the first valid completion for the active transfer, atomically records its terminal result record/cursor, and notifies its Runtime. Duplicate completions are idempotent. The origin ACK causes the remote agent to retire its foreign record after retention. Runtime replay remains owner/cursor based and unchanged from Phase 4.

If a remote node is suspected or unreachable, the origin reconciles the transfer and may make the task eligible again after a bounded ownership timeout. The original remote attempt can still finish, so duplicates are allowed. A late completion cannot overwrite an origin-accepted terminal state.

## Scheduling and Work Stealing

Routing evaluates required labels against advertised node capabilities. Local eligible work is preferred unless a rule selects a better node. Work stealing uses bounded batches and only transfers queued, unleased tasks.

Forwarded tasks carry the forwarded flag and are never forwarded or stolen again in v0.1. This prevents ping-pong and bounds ownership depth to one hop. Cancellation of forwarding, forwarded, or remotely leased tasks remains `too_late` in v0.1.

## Files

```text
agent/internal/cluster/cluster.go
agent/internal/cluster/auth.go
agent/internal/cluster/transport.go
agent/internal/scheduler/scheduler.go
agent/internal/state/transfer.go
agent/internal/object/transfer.go
harness/cluster.py
harness/proxy.py
```

Phase 5 implements the cluster authentication, replay, transfer, ownership, and steal limits already frozen in the Phase 1 configuration. It does not alter the Phase 1 base frame or message schemas.

## Required Tests

- Cluster disabled opens no ports; secure mode rejects missing/wrong keys and replayed authentication.
- Eventual membership convergence uses polling with deadlines, never fixed sleeps.
- Matching/nonmatching label routing and local fallback.
- Concurrent stealers cannot acquire the same queued task; forwarded tasks never re-forward.
- Crash before/after import ACK and before/after origin transition recovers without conservation loss.
- Origin restart recovers forwarding/forwarded state from SQLite.
- Local filesystem objects copy correctly; corrupt/truncated transfers never become claimable.
- Remote completion/result object reaches only the origin owner and replays after Runtime reconnect.
- Symmetric/asymmetric partition, node death, flapping, and heal scenarios prove acknowledged-task conservation, bounded completion, agent survival, no leaked workers/transfers/objects, and accounting equality. Every duplicate must be attributable to a recorded ownership timeout or lease expiry.
- Shadow/foreign records and temporary objects remain bounded and are cleaned after ACK/retention.

## Implementation Order

1. Secure cluster lifecycle and capability advertisement.
2. Durable transfer state machine and authenticated transport.
3. Object transfer and idempotent foreign import.
4. Scheduler/routing and one-hop stealing.
5. Remote completion relay and origin result replay.
6. Restart, partition, and churn chaos suite built from composed `AgentHarness` instances and the existing seeded timeline.

## Exit Gate

Phase 5 is complete when authenticated multi-node chaos tests demonstrate task conservation, restart recovery, bounded ownership records, verified object movement, no task ping-pong, and origin-agent result replay without any worker-to-Runtime connection.
