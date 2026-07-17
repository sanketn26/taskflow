# Phase 1 — Protocol and Configuration

## Goal

Freeze the cross-language wire, envelope, reference, and configuration contracts used by Python and Go. This phase contains no task execution, daemons, networking, or persistent storage implementations.

Workers never receive application callback addresses, and Kafka is not a result-transport mode. The schemas in this file are the complete protocol and configuration contract for implementation.

## Phase 0 Baseline

Phase 0 is complete. This phase builds on the existing Poetry package rooted at `python/taskwire`, Go module and agent command under `agent/`, root `Makefile`, and pytest layout under `python/tests`. Keep `pyproject.toml` as the single release-version source, preserve `taskwire.__version__`, `taskwire-agent version`, `TASKWIRE_AGENT_PATH`, and packaged-binary discovery, and continue to support CPython 3.11–3.13 with no required native extension.

The current Go config loader and `taskwire-agent` server are lifecycle stubs. This phase replaces the stub path-only YAML (`socket_path`, `log_path`, `state_dir`, and `object_dir`) with the contract below and updates `AgentHarness._write_config()` at the same time. The stub's newline-delimited `STATUS` probe may remain only as a short-lived readiness adapter; committed protocol compatibility tests use the framed `STATUS` message defined here. Existing Phase 0 lifecycle, version-parity, wheel-install, agent-discovery, and acceleration-fallback tests must remain green.

## Deliverables

- Python and Go frame codecs with byte-for-byte parity.
- Strict msgpack envelope validation in both languages.
- Shared definitions for task identity, `ObjectRef`, owner IDs, cursors, failures, and result records.
- Python and Go configuration loaders validated against one example YAML file.
- Fuzz seeds for frame and envelope decoding.

## Wire Frame

Every connection uses this 31-byte header followed by `payload_len` bytes:

| Offset | Size | Field | Encoding |
|---:|---:|---|---|
| 0 | 1 | protocol version | `0x01` |
| 1 | 1 | message type | unsigned byte |
| 2 | 16 | task ID | raw UUID bytes; zero UUID for connection-scoped requests |
| 18 | 8 | request ID | unsigned big-endian uint64; nonzero for requests and copied by their direct response |
| 26 | 1 | flags | bit field |
| 27 | 4 | payload length | unsigned big-endian uint32 |

Flags are `0x01` error, `0x02` idempotent, and `0x04` forwarded. Unknown flag bits are rejected in v1. A requester chooses a nonzero request ID unique among its in-flight requests on that connection. `ACK`, `ERROR`, `TASK`, object-transfer responses, and query/status responses copy it. Unsolicited `RESULT` notifications use request ID zero and are correlated by task ID plus cursor. Reuse while the earlier request is in flight is `duplicate_request`; reconnect starts a new request-ID namespace. Readers validate the version, type, flags, and configured maximum length before allocating a payload buffer. EOF before the first header byte is a clean disconnect; EOF after any frame byte is a truncated-frame error.

The error flag is required exactly on `ERROR` frames. The idempotent flag is valid only on retryable-by-identity `SUBMIT`, `COMPLETE`, result `ACK`, and object PUT requests. The forwarded flag is valid only on authenticated cluster `SUBMIT` traffic. Invalid flag/type combinations are `unknown_flags`.

## Message Types

| Value | Name | Direction | Payload |
|---:|---|---|---|
| `0x01` | `SUBMIT` | Runtime → origin agent; agent → agent | `TaskEnvelope`; forwarded flag uses `ForwardedTask` |
| `0x02` | `PULL` | worker → local agent | `PullRequest` |
| `0x03` | `TASK` | local agent → worker | `LeasedTask` |
| `0x04` | `HEARTBEAT` | worker → local agent | `{lease_id}` |
| `0x05` | `RESULT` | origin agent → Runtime | `ResultNotification` |
| `0x06` | `CANCEL` | Runtime → origin agent | `{owner_id}` |
| `0x07` | `COMPLETE` | worker → local agent; remote agent → origin | `Completion`; forwarded flag uses `ForwardedCompletion` |
| `0x08` | `STEAL` | agent ↔ agent | `{requester_node, labels, limit}` request; typed steal ACK |
| `0x09` | `ACK` | response | typed `Ack` payload |
| `0x0A` | `STATUS` | local client → local agent | empty request; status snapshot response |
| `0x0B` | `RESUME_RESULTS` | Runtime → origin agent | `{owner_id, after_cursor, limit}` |
| `0x0C` | `ERROR` | response | `Error` |
| `0x0D` | `OBJECT_PUT` | Runtime/worker/agent → local agent | `{transfer_id, codec, size, sha256}` |
| `0x0E` | `OBJECT_GET` | Runtime/worker/agent → local agent | `{transfer_id, object: ObjectRef}` |
| `0x0F` | `OBJECT_CHUNK` | either direction during object RPC | `{transfer_id, sequence, data, eof}` |
| `0x10` | `HELLO` | Runtime/worker/admin → local agent | `Hello` request; `Ack(kind="hello")` response |
| `0x11` | `TASK_QUERY` | Runtime → origin agent | `TaskQuery` request; `TaskSnapshot` response |

Every local connection begins with `HELLO`; any other request before successful registration receives `not_registered` and closes the connection. A Runtime registers one owner ID and is eligible for that owner's `RESULT` notifications. A newer connection for the same owner becomes primary after resume begins; the old connection may finish in-flight responses but receives no new notifications. A worker registers its worker ID and may send only worker-direction messages. An admin connection may send only `STATUS`; the CLI and harness use this role. Local Unix-socket permissions are the v0.1 authentication boundary; owner ID remains a bearer capability for owner-scoped operations.

`RESULT` is agent-to-Runtime only. ACK payloads are typed and never inferred from connection context. SUBMIT ACK is sent only after the configured input and task-state durability boundary. Responses copy the request ID; notification ACKs use a new nonzero request ID and identify the notification in their payload.

Object transfers are correlated by a random 16-byte `transfer_id`. Chunks are contiguous, zero-based, and individually bounded by the frame limit. PUT begins with metadata, streams chunks, and ends with an `eof` chunk; the final ACK returns the canonical `ObjectRef` only after size/checksum verification and the store durability boundary. GET returns metadata followed by chunks and a final ACK. A sequence gap, overrun, checksum mismatch, timeout, or disconnect aborts and cleans the partial transfer. Implementations enforce `ipc.max_active_transfers`, `ipc.max_transfer_bytes`, `ipc.object_chunk_bytes`, and `ipc.object_transfer_timeout_ms`. Only chunks for registered transfers are accepted. A connection may multiplex transfers and control requests because request ID and transfer ID provide independent correlation.

For GET, the agent first sends an `OBJECT_GET` response `{transfer_id, object: ObjectRef}` with the request ID, then zero or more `OBJECT_CHUNK` frames with that request ID, then `Ack(kind="object_get")`. For PUT, every chunk copies the initiating request ID and the final object-put ACK terminates it. `RESUME_RESULTS` emits up to `limit` request-ID-zero `RESULT` notifications followed by its correlated resume ACK; when `more` is true the Runtime repeats from `next_cursor`. `PULL` returns either `TASK` with the claimed task ID in the frame header or `Ack(kind="empty_pull")`.

## Canonical Msgpack Schemas

All maps use UTF-8 string keys. Decoders reject missing required keys, wrong types, duplicate logical keys, trailing bytes, and unknown keys unless a schema explicitly marks them as forward-compatible. IDs are fixed-size binary values: task/owner/lease IDs are 16 bytes; cursors are unsigned 64-bit integers.

### `ObjectRef`

```text
{
  store: string,       # configured object-store name
  key: string,         # opaque store-relative key
  size: uint64,
  sha256: binary(32),
  codec: string        # "cloudpickle", "msgpack", or "bytes"
}
```

References are immutable and checksum-verified. An explicit `ObjectRef` is not copied inline.

### `ValueRef`

Exactly one of:

```text
{inline: binary, codec: string}
{object: ObjectRef}
```

The encoder selects an object reference when serialized bytes exceed `storage.objects.inline_threshold_bytes`.

### Connection and request schemas

```text
Hello = exactly one of:
  {role: "runtime", owner_id: binary(16)}
  {role: "worker", worker_id: string}
  {role: "admin"}

PullRequest {
  worker_id: string,
  labels: map<string,string>
}

TaskQuery {
  owner_id: binary(16),
  task_ids: array<binary(16)>       # length 1..ipc.task_query_batch_size
}

TaskSnapshot {
  tasks: array<{
    task_id: binary(16),
    state: "queued" | "leased" | "succeeded" | "failed" | "cancelled" | "dead_lettered" | "unknown",
    cursor: uint64 | nil,
    result: ObjectRef | nil,
    failure: Failure | nil
  }>
}
```

`TaskQuery` is owner-isolated. Unknown and wrong-owner task IDs both return `state: "unknown"` to avoid disclosing existence. Terminal snapshots use the same result/failure invariants as notifications. `STATUS` returns the `StatusSnapshot` defined below and is forward-compatible: decoders retain known keys and ignore unknown keys only for this schema.

### Task and result envelopes

```text
TaskEnvelope {
  owner_id: binary(16),
  task_name: string,
  task_version: string,
  arguments: ValueRef,       # serialized canonical {args: array, kwargs: map<string,any>}
  labels: map<string,string>,
  idempotent: bool,
  submitted_at_unix_ms: int64
}

LeasedTask {
  task: TaskEnvelope,
  lease_id: binary(16),
  ttl_ms: uint32,
  attempt: uint32
}

Completion {
  lease_id: binary(16),
  result: ObjectRef | nil,
  failure: Failure | nil
}

ForwardedTask {
  transfer_id: binary(16),
  origin_node: string,
  task: TaskEnvelope
}

ForwardedCompletion {
  transfer_id: binary(16),
  remote_node: string,
  remote_attempt: uint32,
  result: ObjectRef | nil,
  failure: Failure | nil
}

Failure {
  code: string,
  message: string,
  details: ValueRef | nil,
  retryable: bool
}

ResultNotification {
  owner_id: binary(16),
  cursor: uint64,
  state: "succeeded" | "failed" | "cancelled",
  result: ObjectRef | nil,
  failure: Failure | nil
}

StatusSnapshot {
  version: string,
  pid: uint64,
  ready: bool,
  task_counts: map<string,uint64>,
  active_leases: uint64,
  worker_pids: array<uint64>,
  worker_restarts: uint64,
  storage_healthy: bool,
  cluster_members: uint64,
  kafka_outbox_pending: uint64,
  last_error_code: string | nil
}
```

`arguments` is one serialized value containing a two-key map; positional arguments are an array and keyword arguments are a string-keyed map. This shape is identical for every codec. `Completion` and `ForwardedCompletion` each require exactly one of `result` or `failure`. The agent accepts a local completion only for the active fencing lease and a forwarded completion only for the active transfer. Dead-letter exhaustion is delivered as `state: "failed"` with failure code `max_attempts_exceeded`; `dead_lettered` is an internal/query state. A result notification is replayed until its ACK or retention expiry. Registered `task_name` plus `task_version` is the production identity; inline serialized functions are permitted only when `tasks.allow_inline_functions` is true and use the reserved identity `__inline__`.

### ACK and error schemas

```text
Ack = exactly one of:
  {kind: "hello"}
  {kind: "submit", task_id: binary(16)}
  {kind: "forward", task_id: binary(16), transfer_id: binary(16)}
  {kind: "heartbeat", lease_id: binary(16)}
  {kind: "complete", lease_id: binary(16)}
  {kind: "cancel", task_id: binary(16), cancelled: bool}
  {kind: "result", owner_id: binary(16), task_id: binary(16), cursor: uint64}
  {kind: "empty_pull"}
  {kind: "object_put", transfer_id: binary(16), object: ObjectRef}
  {kind: "object_get", transfer_id: binary(16)}
  {kind: "resume", owner_id: binary(16), next_cursor: uint64, more: bool}
  {kind: "steal", transfer_id: binary(16), accepted: uint32}

Error {
  code: string,
  message: string,
  retryable: bool,
  details: map<string,string>
}
```

Error messages and details are bounded and safe for logs; they never contain payload values. The stable v1 error registry is: `unsupported_version`, `unknown_message_type`, `unknown_flags`, `frame_too_large`, `malformed_payload`, `invalid_message`, `duplicate_request`, `not_registered`, `role_forbidden`, `owner_mismatch`, `task_conflict`, `task_not_found`, `too_late`, `stale_lease`, `unknown_lease`, `unknown_task`, `unsupported_codec`, `unsupported_backend`, `transfer_limit`, `transfer_timeout`, `checksum_mismatch`, `storage_unavailable`, `storage_consistency`, `shutdown`, and `internal`. Validation, authorization, conflict, fencing, and consistency errors are non-retryable. Capacity, timeout, storage-unavailable, shutdown, and internal errors are retryable. Python and Go expose constants for this registry, and Phase 4 maps each code to a documented SDK exception.

System-generated `Failure.code` values are `task_exception`, `unknown_task`, `serialization_error`, `max_attempts_exceeded`, `storage_consistency`, and `cancelled`. User exception type/module belongs in bounded failure details, not in the stable code. Failure retryability controls whether the task attempt may be retried; protocol `Error.retryable` controls whether the rejected operation may be retried and the two are never inferred from one another.

`STEAL` uses `{requester_node: string, labels: map<string,string>, limit: uint32}` and returns `Ack(kind="steal")`; accepted tasks then use forwarded `SUBMIT` requests with stable task and transfer identity. A remote completion uses `ForwardedCompletion`; the task ID remains in the frame header and the origin validates the active transfer before accepting it. Phase 5 may add authenticated cluster-handshake payloads but may not change the base frame, task, object, ACK, or error schemas.

## Configuration Contract

Unknown fields are errors. Environment substitution is not performed by the library. Relative filesystem paths resolve against the configuration file directory. Durations are integer milliseconds/seconds as named, not free-form strings.

```yaml
socket: "/var/run/taskwire/agent.sock"
socket_group: "taskwire"

ipc:
  submit_ack_timeout_ms: 5000
  reconnect_backoff_ms: 250
  result_batch_size: 100
  task_query_batch_size: 100
  read_timeout_ms: 30000
  write_timeout_ms: 30000
  object_transfer_timeout_ms: 60000
  object_chunk_bytes: 262144
  max_active_transfers: 4
  max_transfer_bytes: 1073741824
  write_queue_size: 256

queue:
  max_attempts: 5
  max_frame_size_mb: 16
  lease_ttl_ms: 30000
  reaper_interval_ms: 1000

storage:
  state:
    type: "sqlite"              # v0.1: memory | sqlite
    dsn: "/var/lib/taskwire/state.db"
    sqlite_busy_timeout_ms: 5000
    sqlite_synchronous: "FULL"
  objects:
    default: "local"
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    sweep_interval_ms: 60000
    sweep_batch_size: 100
    stores:
      local:
        type: "filesystem"      # v0.1: memory | filesystem
        root: "/var/lib/taskwire/objects"

tasks:
  allow_inline_functions: false
  import_modules: ["myapp.tasks"]

workers:
  count: 4
  python_executable: "python3"
  working_directory: "."
  environment: {}
  shutdown_grace_ms: 30000
  restart_backoff_min_ms: 250
  restart_backoff_max_ms: 30000
  restart_limit: 5
  restart_window_seconds: 60
  labels: {workload: "general"}
  resources: {max_memory_mb: 2048, max_cpu_percent: 80}

cluster:
  enabled: false
  allow_insecure: false
  node_name: ""
  bind_addr: "0.0.0.0:7946"
  advertise_addr: ""
  task_port: 7947
  seeds: []
  mdns: true
  encryption_key: ""
  auth_clock_skew_ms: 30000
  auth_timeout_ms: 5000
  replay_cache_size: 4096
  transfer_timeout_ms: 30000
  ownership_timeout_ms: 120000
  steal_batch_size: 10

routing:
  rules: []

integrations:
  kafka:
    enabled: false
    brokers: []
    topic: "taskwire-results"
    delivery_timeout_ms: 30000
    batch_size: 100
    max_in_flight: 10
    retry_backoff_ms: 1000
    shutdown_grace_ms: 10000
    preserve_owner_order: true
    max_event_bytes: 1048576
    published_retention_seconds: 604800

metrics:
  listen_addr: ""
```

Validation requirements:

- `lease_ttl_ms >= 1000`; heartbeat interval is one third of it.
- Frame size, timeouts, batch sizes, attempts, and retention values are positive.
- `object_chunk_bytes` is no greater than the decoded frame limit; transfer count/size and task-query limits are positive.
- `memory` state/object stores require an explicit development configuration warning.
- SQLite and filesystem paths must be non-empty; configured default object store must exist.
- Unknown backend types, including PostgreSQL/S3 until their separately gated adapters ship, are rejected with `unsupported_backend`.
- Enabling clustering requires a decoded 32-byte key unless `allow_insecure` is true.
- Cluster authentication/transfer limits are positive and ownership timeout exceeds transfer timeout.
- Routing rules have `{task_labels: map<string,string>, require_node_labels: map<string,string>, preference: "local" | "remote" | "any"}`. Rules are evaluated in order using exact all-key matches; the first match wins. With no match, required task labels are matched directly against node labels and local eligible execution is preferred. Duplicate/conflicting rule keys and an empty rule are rejected.
- Enabling Kafka requires brokers and a non-empty topic. Kafka never changes worker completion ordering or Runtime result replay.
- Worker import modules are non-empty dotted names; `python_executable` must resolve at agent startup, `working_directory` must exist, environment keys cannot override Taskwire-owned socket/config/worker-ID variables, and restart minimum cannot exceed maximum.
- `sqlite_synchronous` is exactly `FULL` or `NORMAL`; production defaults to `FULL`. Backend-specific fields supplied for a different backend are rejected rather than ignored.

## Platform Contract

v0.1 supports Linux and macOS on architectures for which the release publishes an agent artifact. IPC uses Unix-domain sockets and lifecycle tests require POSIX signals. Windows is not supported in v0.1; Python imports and pure codec unit tests may run there, but agent discovery must report an unsupported-platform installation error rather than implying `.exe` support exists. Adding Windows requires a separately versioned named-pipe, service, and process-lifecycle contract plus its own artifact gate.

## Files

```text
python/taskwire/exceptions.py
python/taskwire/protocol/messages.py
python/taskwire/protocol/frames.py
python/taskwire/config.py
agent/pkg/protocol/protocol.go
agent/internal/config/config.go
taskwire.example.yaml
python/tests/unit/test_protocol.py
python/tests/unit/test_config.py
agent/pkg/protocol/protocol_test.go
agent/internal/config/config_test.go
python/tests/integration/test_protocol_compat.py
```

The optional Rust codec is deferred until profiling justifies it. If implemented, it must be byte-identical to the Python reference and the package must continue to work without it.

## Required Tests

- Round-trip every message and schema in Python and Go.
- Prove concurrent in-flight requests, out-of-order responses, unsolicited results, request-ID reuse rejection, and reconnect namespace reset.
- Cross-language golden vectors in both directions, including `ObjectRef`, failures, and cursors.
- Reject unknown version/type/flags, short headers, truncated payloads, oversize lengths, malformed msgpack, bad ID sizes, invalid union shapes, and unknown configuration fields.
- Verify a claimed 4 GiB payload is rejected before allocation.
- Verify Python and Go load `taskwire.example.yaml` to equivalent normalized values.
- Verify cluster, storage, lease, and Kafka conditional validation.
- Verify HELLO role enforcement, owner isolation, same-owner primary connection replacement, and TASK_QUERY non-disclosure.
- Verify every stable error code has identical retryability and Python/Go constants.
- Seed Python and Go fuzzers with all valid message forms and malformed boundary cases.

## Implementation Order

1. Define golden schema fixtures and error codes in the existing Python and Go protocol packages.
2. Implement the pure-Python frame codec and envelope validators without changing the `_accel` fallback contract.
3. Port them to Go and run parity tests immediately.
4. Replace both stub config surfaces with loaders for `taskwire.example.yaml`, then update `AgentHarness._write_config()` and its readiness/status probe atomically.
5. Add fuzz targets and commit the seed corpus.
6. Run `make unit`, `make integration`, and `make smoke-wheel` to prove the Phase 0 artifact path still works.

## Exit Gate

Phase 1 is complete when Python and Go pass the same golden vectors and configuration fixture, all malformed-input tests fail closed without large allocation, no callback or direct-result-delivery field remains, and later phases can depend on the schemas without inventing new wire fields.
