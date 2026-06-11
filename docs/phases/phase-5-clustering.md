# Phase 5 — Clustering

## Goal

Multi-node self-aware cluster. Sidecars discover each other via gossip (Hashicorp `memberlist`) and mDNS. Tasks route to nodes by label. Work stealing balances load across nodes. Node failure is detected and in-flight tasks are re-queued.

## Testable Outcome

- 3 `taskwire-agent` processes start on localhost (different gossip ports and socket paths), each sees 3 members
- Task with `label="node-2"` routes to node 2, not node 1 or 3
- Kill node 2 while it holds a lease → after TTL, task re-queued on another node → result delivered
- Node rejoins → work stealing distributes queued tasks to it
- mDNS: 2 agents start with no seeds configured → they discover each other automatically (local network test)

---

## Files

```
agent/internal/cluster/cluster.go
agent/internal/cluster/cluster_test.go
agent/internal/scheduler/scheduler.go
agent/internal/scheduler/scheduler_test.go
agent/internal/queue/queue.go          (add StealN method)
agent/internal/ipc/server.go           (add STEAL handler)
agent/cmd/taskwire-agent/main.go       (wire cluster + scheduler)
agent/go.mod                           (add memberlist dependency)
```

---

## Go

### `agent/internal/cluster/cluster.go`

#### `NodeInfo` struct

Broadcast to all peers via gossip metadata. Represents one node's current state.

| Field | Type | Description |
|-------|------|-------------|
| `Name` | `string` | Unique node name (from config, or auto UUID) |
| `Addr` | `string` | Routable `host:port` for the gossip protocol |
| `SocketPath` | `string` | Unix socket path (local only — not broadcast; only used by local manager) |
| `Labels` | `map[string]string` | Node labels from `workers.labels` in config |
| `QueueDepth` | `int` | Current local queue depth; updated periodically and broadcast via gossip |

#### `EventDelegate` interface (memberlist callback)

| Method | Signature | Description |
|--------|-----------|-------------|
| `NotifyJoin` | `(node *memberlist.Node)` | Decode NodeInfo from node.Meta, add to members map |
| `NotifyLeave` | `(node *memberlist.Node)` | Remove from members map |
| `NotifyUpdate` | `(node *memberlist.Node)` | Decode updated NodeInfo (e.g. new QueueDepth), update map entry |

#### `Cluster` interface

| Method | Signature | Description |
|--------|-----------|-------------|
| `Join` | `(seeds []string) error` | Connect to seed nodes via memberlist; also start mDNS browse if enabled |
| `Leave` | `() error` | Graceful leave; memberlist broadcasts departure |
| `Members` | `() []NodeInfo` | Snapshot of all known live nodes |
| `LocalNode` | `() NodeInfo` | This node's own NodeInfo |
| `UpdateQueueDepth` | `(depth int)` | Update local NodeInfo.QueueDepth and trigger memberlist metadata re-broadcast |

#### `GossipCluster` struct (implements `Cluster`)

| Field | Type | Description |
|-------|------|-------------|
| `list` | `*memberlist.Memberlist` | The memberlist instance |
| `cfg` | `*ClusterConfig` | Config reference |
| `localNode` | `NodeInfo` | This node's info |
| `members` | `map[string]*NodeInfo` | All known live nodes by name |
| `mu` | `sync.RWMutex` | RLock for reads (Members), Lock for writes (NotifyJoin/Leave) |

| Method | Responsibility |
|--------|----------------|
| `NewGossipCluster(cfg *ClusterConfig) (*GossipCluster, error)` | Build `memberlist.DefaultLANConfig()`. Set `Name`, `BindAddr`, `AdvertiseAddr` from config. Set `Events` delegate to self. Set `Delegate` for metadata broadcast (NodeInfo JSON). `memberlist.Create(config)`. |
| `Join(seeds []string) error` | `list.Join(seeds)`. If `cfg.MDNS`, start `browseMDNS()` goroutine to find peers on local network. |
| `Members() []NodeInfo` | RLock, copy map values to slice, RUnlock. Return slice. |
| `UpdateQueueDepth(depth int)` | Lock, update `localNode.QueueDepth`, unlock. Call `list.UpdateNode(timeout)` to re-broadcast metadata. |
| `NodeMeta(limit int) []byte` | memberlist Delegate method. Marshal `localNode` to JSON (capped at `limit` bytes). Called by memberlist when it needs to broadcast this node's metadata. |
| `browseMDNS()` | Use `github.com/grandcat/zeroconf` or `net` multicast. Discover peers advertising `_taskwire._tcp`. For each found, call `list.Join([]string{addr})`. |

**Dependency:** `github.com/hashicorp/memberlist` for gossip. `github.com/grandcat/zeroconf` for mDNS.

#### Gossip encryption

When `cluster.encryption_key` is set (base64 32-byte key), pass it to memberlist via `Config.SecretKey` — memberlist then encrypts all gossip traffic (AES-GCM) and silently drops packets from nodes without the key. This doubles as cluster *admission control*: a stray agent on the LAN can't join. mDNS discovery + no key is acceptable for laptops; the docs must state that any multi-host production deployment sets the key. Validate at startup: `seeds` non-empty or `mdns` off + no key → log a prominent warning.

#### STEAL endpoint authentication

memberlist's SecretKey covers *gossip* only — the cluster TCP endpoint (`StartCluster`) is our own protocol and gets nothing for free. An unauthenticated STEAL endpoint hands serialized Python callables to anyone who connects, and accepts task injection from anyone. When `encryption_key` is set, the cluster TCP handshake is: server sends 16-byte random nonce → client replies `HMAC-SHA256(key, nonce)` → server verifies before processing any frame. Constant-time compare (`hmac.Equal`). No key configured → handshake skipped (single-node / trusted-LAN dev mode), warning logged.

---

### `agent/internal/scheduler/scheduler.go`

Routes a task to the best node. Keeps routing logic separate from cluster membership logic (SRP).

#### `Router` interface

| Method | Signature | Description |
|--------|-----------|-------------|
| `Route` | `(task *queue.Task, members []cluster.NodeInfo) (addr string, local bool)` | Return `("", true)` if task should run locally. Return `(addr, false)` if it should be forwarded to `addr`. |

#### `RoutingRule` struct

| Field | Type | Description |
|-------|------|-------------|
| `MatchLabel` | `string` | Task label that triggers this rule |
| `Prefer` | `map[string]string` | Node labels preferred for matched tasks |

#### `LabelRouter` struct (implements `Router`)

| Field | Type | Description |
|-------|------|-------------|
| `rules` | `[]RoutingRule` | Ordered routing rules from config |

| Method | Responsibility |
|--------|----------------|
| `NewLabelRouter(rules []RoutingRule) *LabelRouter` | Store rules |
| `Route(task, members) (string, bool)` | Find first rule where `rule.MatchLabel == task.Label`. Filter `members` to those whose Labels contain all `rule.Prefer` key-value pairs. From matching members, pick one with lowest `QueueDepth`. If no matching members or no rule match: return `("", true)` (run locally). If best match is local node: return `("", true)`. Else return `(member.Addr, false)`. |

---

### Queue additions: work stealing

Add to `Queuer` interface and `InMemoryQueue`:

| Method | Signature | Description |
|--------|-----------|-------------|
| `StealN` | `(n int) []*Task` | Remove and return up to `n` tasks from the back of the queue (steal from tail to avoid FIFO disruption at head) |

#### `WorkStealer` struct

Runs as a background goroutine within the agent. Periodically checks if the local queue is empty; if so, asks a peer for work.

| Field | Type | Description |
|-------|------|-------------|
| `cluster` | `Cluster` | To find peers and their queue depths |
| `queue` | `Queuer` | Local queue to push stolen work into |
| `stopCh` | `chan struct{}` | Shutdown signal |

| Method | Responsibility |
|--------|----------------|
| `NewWorkStealer(cluster Cluster, queue Queuer) *WorkStealer` | Initialise |
| `Start()` | Start `steal()` goroutine |
| `Stop()` | Close `stopCh` |
| `steal()` (goroutine) | Every 500ms: if `queue.Len() == 0`, find the member with highest `QueueDepth`. Connect to their `ipc.Server` TCP endpoint (not Unix socket — cluster communication uses TCP), send STEAL frame requesting up to 5 tasks. On response, push received tasks into local queue. |

---

### IPC Server additions

Add STEAL message handling to `server.go`:

| Method | Responsibility |
|--------|----------------|
| `handleSteal(conn net.Conn, f *protocol.Frame)` | Deserialise `{n}` from payload. Call `queue.StealN(n)`. Encode each stolen task as a TASK frame (without lease — stolen tasks get a fresh lease when a worker pulls them). Send all frames. Send ACK. |

Cluster TCP endpoint — same `Server` but listening on TCP (`cfg.BindAddr`) in addition to the Unix socket. Add to `Server`:

| Method | Responsibility |
|--------|----------------|
| `StartCluster(bindAddr string) error` | `net.Listen("tcp", bindAddr)`. Start accept loop for cluster connections. Run the HMAC handshake (see above) before entering `handleConn`. Same `handleConn` dispatch — STEAL frames arrive here. |

---

### Forwarded-task ownership (failure semantics that actually hold)

The testable outcome "kill node 2 while it holds a lease → task re-queued on another node" does **not** fall out of forwarding alone: once a task is pushed to node 2's queue and node 2 dies, nothing elsewhere knows the task existed. Forwarding must keep ownership at the origin:

- When `Route` returns a remote addr, the origin does **not** delete the task. It moves it to a `forwarded` map (`task_id → {peer, deadline}`) and sends a copy.
- The remote node sends COMPLETE (0x07) to the origin over the cluster connection when the task's lease is released after successful delivery. Origin then drops its shadow copy (and its WAL record).
- On `NotifyLeave`/failure-detection for a peer, the origin re-queues every entry in `forwarded` belonging to that peer. Combined with at-least-once + idempotency, duplicate execution is possible and documented; lost tasks are not.
- Same mechanism covers STEAL: the *stolen-from* node keeps the shadow until COMPLETE from the thief.

This is the single most intricate piece of the phase — build it last, after routing and stealing work without failures.

---

## Tests

### `agent/internal/cluster/cluster_test.go`

| Test | Asserts |
|------|---------|
| `TestThreeNodeCluster` | Start 3 GossipClusters on localhost (ports 7950, 7951, 7952). Node 1 joins with seed 7950. Node 2 joins with seed 7950. Wait 1s. All 3 call `Members()` and see 3 entries. |
| `TestNodeLeave` | 3 nodes. Node 3 calls `Leave()`. Wait 2s. Nodes 1 and 2 see only 2 members. |
| `TestQueueDepthBroadcast` | Node 1 calls `UpdateQueueDepth(10)`. Wait 2s. Node 2's `Members()` shows node 1 with `QueueDepth=10`. |

### `agent/internal/scheduler/scheduler_test.go`

| Test | Asserts |
|------|---------|
| `TestRoute_MatchingLabel` | Task label "cpu". Rule: match "cpu" → prefer `{workload: "compute"}`. Members: node-A `{workload: "compute", depth: 2}`, node-B `{workload: "io", depth: 0}`. Expect route to node-A. |
| `TestRoute_NoMatch_RunsLocally` | Task label "unknown". No matching rule. Expect `local=true`. |
| `TestRoute_LeastLoaded` | Two nodes both match rule. Depths: 5 and 2. Expect route to depth-2 node. |

### Integration: multi-node E2E

| Test | Asserts |
|------|---------|
| `test_label_routing_e2e` | 2 agents (node-general, node-cpu). Config: label "cpu" routes to node-cpu. Submit `@task(label="cpu")` via SDK. Verify worker on node-cpu handled it (check node-cpu worker log). |
| `test_node_failure_requeue` | 2 agents. Submit slow task to node-2. Kill node-2 process. After TTL + steal window, task completes on node-1. |
| `test_work_stealing` | Agent-1 has 20 tasks queued. Start Agent-2 (empty). After 1s, agent-2's workers begin completing tasks (stolen). |
| `test_steal_requires_auth` | Cluster with encryption_key set. Raw TCP client without the key sends STEAL → connection closed, no tasks leaked. |
| `test_forwarded_task_survives_peer_death` | Node-1 forwards task to node-2. `kill -9` node-2 before completion. Node-1 re-queues from its `forwarded` map; task completes locally. |

---

## Implementation Guide

Build order:

1. **`GossipCluster` + membership tests** — 3 nodes on localhost, no scheduler yet. memberlist's defaults assume LAN timings; keep `DefaultLANConfig` and only tune in Phase 7.
2. **`LabelRouter`** — pure function over `[]NodeInfo`, fully unit-testable with no networking.
3. **Cluster TCP endpoint + handshake** — drive with a raw Go client before wiring WorkStealer to it.
4. **WorkStealer** — steal-from-tail; verify FIFO at the head is undisturbed.
5. **Forwarded-task ownership** — last, with the kill-test from day one.

Gotchas:

- **`NodeMeta` size limit**: memberlist caps metadata (512B by default). NodeInfo JSON with many labels can exceed it — use msgpack, keep labels short, and fail loudly at startup if `NodeMeta` would truncate (truncated JSON = nodes silently invisible to routing).
- **Queue-depth staleness**: gossip metadata propagates in seconds, not ms. Routing on stale depth is fine (it self-corrects via stealing); just don't oscillate — `UpdateQueueDepth` should be rate-limited (e.g. broadcast only on change > 10% or every 2s).
- **mDNS in CI**: multicast is usually blocked in containerised CI. The mDNS test must be tagged (`//go:build mdns`) and run locally/nightly, not in the default suite — or it will be deleted in frustration within a month.
- **Don't forward the forwarded**: a task received via forwarding or STEAL is marked (`flags` bit or envelope field) and is never re-forwarded — without this, two misconfigured nodes ping-pong a task forever.

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | `Router` interface | `LabelRouter` today; could add `RoundRobinRouter` or `AffinityRouter` without changing callers |
| Observer | memberlist `EventDelegate` | Cluster membership changes are events; `GossipCluster` reacts to them |
| Interface Segregation | `Cluster` interface | `WorkStealer` only needs `Members()` and `UpdateQueueDepth()`; it doesn't need `Join/Leave` |
| Goroutine Ownership | `WorkStealer.steal()` | Owned and stopped by `WorkStealer`. Clean lifecycle. |
