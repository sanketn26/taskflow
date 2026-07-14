# Phase 1 — Protocol and Configuration

## Goal

Freeze the cross-language wire, envelope, reference, and configuration contracts used by Python and Go. This phase contains no task execution, daemons, networking, or persistent storage implementations.

Workers never receive application callback addresses, and Kafka is not a result-transport mode. The schemas in this file are the complete protocol and configuration contract for implementation.

## Deliverables

- Python and Go frame codecs with byte-for-byte parity.
- Strict msgpack envelope validation in both languages.
- Shared definitions for task identity, `ObjectRef`, owner IDs, cursors, failures, and result records.
- Python and Go configuration loaders validated against one example YAML file.
- Fuzz seeds for frame and envelope decoding.

## Wire Frame

Every connection uses this 23-byte header followed by `payload_len` bytes:

| Offset | Size | Field | Encoding |
|---:|---:|---|---|
| 0 | 1 | protocol version | `0x01` |
| 1 | 1 | message type | unsigned byte |
| 2 | 16 | task ID | raw UUID bytes; zero UUID for connection-scoped requests |
| 18 | 1 | flags | bit field |
| 19 | 4 | payload length | unsigned big-endian uint32 |

Flags are `0x01` error, `0x02` idempotent, and `0x04` forwarded. Unknown flag bits are rejected in v1. Readers validate the version, type, flags, and configured maximum length before allocating a payload buffer. EOF before the first header byte is a clean disconnect; EOF after any frame byte is a truncated-frame error.

## Message Types

| Value | Name | Direction | Payload |
|---:|---|---|---|
| `0x01` | `SUBMIT` | Runtime → origin agent; agent → agent | `TaskEnvelope` |
| `0x02` | `PULL` | worker → local agent | `PullRequest` |
| `0x03` | `TASK` | local agent → worker | `LeasedTask` |
| `0x04` | `HEARTBEAT` | worker → local agent | `{lease_id}` |
| `0x05` | `RESULT` | origin agent → Runtime | `ResultNotification` |
| `0x06` | `CANCEL` | Runtime → origin agent | `{owner_id}` |
| `0x07` | `COMPLETE` | worker → local agent; remote agent → origin | `Completion` |
| `0x08` | `STEAL` | agent ↔ agent | Phase 5 request/response |
| `0x09` | `ACK` | response | typed `Ack` payload |
| `0x0A` | `STATUS` | local client → local agent | empty request; status snapshot response |
| `0x0B` | `RESUME_RESULTS` | Runtime → origin agent | `{owner_id, after_cursor, limit}` |
| `0x0C` | `ERROR` | response | `{code, message, retryable}` |
| `0x0D` | `OBJECT_PUT` | Runtime/worker/agent → local agent | `{transfer_id, codec, size, sha256}` |
| `0x0E` | `OBJECT_GET` | Runtime/worker/agent → local agent | `{transfer_id, object: ObjectRef}` |
| `0x0F` | `OBJECT_CHUNK` | either direction during object RPC | `{transfer_id, sequence, data, eof}` |

`RESULT` is agent-to-Runtime only. A Runtime ACK is `{kind: "result", owner_id, task_id, cursor}`. SUBMIT ACK is `{kind: "submit", task_id}` and is sent only after the configured input and task-state durability boundary is satisfied. ACK payloads are never inferred from connection context.

Object transfers are correlated by a random 16-byte `transfer_id`. Chunks are contiguous, zero-based, and individually bounded by the frame limit. PUT begins with metadata, streams chunks, and ends with an `eof` chunk; the final ACK returns the canonical `ObjectRef` only after size/checksum verification and the store durability boundary. GET returns metadata followed by chunks and a final ACK. A sequence gap, overrun, checksum mismatch, timeout, or disconnect aborts and cleans the partial transfer. Implementations apply per-connection transfer-count and byte limits.

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

### Task and result envelopes

```text
TaskEnvelope {
  owner_id: binary(16),
  task_name: string,
  task_version: string,
  args: ValueRef,
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
```

`Completion` requires exactly one of `result` or `failure`. The agent accepts it only for the active fencing lease. A result notification is replayed until its ACK or retention expiry. Registered `task_name` plus `task_version` is the production identity; inline serialized functions are permitted only when `tasks.allow_inline_functions` is true and use the reserved identity `__inline__`.

## Configuration Contract

Unknown fields are errors. Environment substitution is not performed by the library. Relative filesystem paths resolve against the configuration file directory. Durations are integer milliseconds/seconds as named, not free-form strings.

```yaml
socket: "/var/run/taskwire/agent.sock"
socket_group: "taskwire"

ipc:
  submit_ack_timeout_ms: 5000
  reconnect_backoff_ms: 250
  result_batch_size: 100

queue:
  max_attempts: 5
  max_frame_size_mb: 16
  lease_ttl_ms: 30000

storage:
  state:
    type: "sqlite"              # v0.1: memory | sqlite
    dsn: "/var/lib/taskwire/state.db"
  objects:
    default: "local"
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    stores:
      local:
        type: "filesystem"      # v0.1: memory | filesystem
        root: "/var/lib/taskwire/objects"

tasks:
  allow_inline_functions: false

workers:
  count: 4
  shutdown_grace_ms: 30000
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

routing:
  rules: []

integrations:
  kafka:
    enabled: false
    brokers: []
    topic: "taskwire-results"
    delivery_timeout_ms: 30000

metrics:
  listen_addr: ""
```

Validation requirements:

- `lease_ttl_ms >= 1000`; heartbeat interval is one third of it.
- Frame size, timeouts, batch sizes, attempts, and retention values are positive.
- `memory` state/object stores require an explicit development configuration warning.
- SQLite and filesystem paths must be non-empty; configured default object store must exist.
- Unknown backend types, including PostgreSQL/S3 until their separately gated adapters ship, are rejected with `unsupported_backend`.
- Enabling clustering requires a decoded 32-byte key unless `allow_insecure` is true.
- Enabling Kafka requires brokers and a non-empty topic. Kafka never changes worker completion ordering or Runtime result replay.

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
- Cross-language golden vectors in both directions, including `ObjectRef`, failures, and cursors.
- Reject unknown version/type/flags, short headers, truncated payloads, oversize lengths, malformed msgpack, bad ID sizes, invalid union shapes, and unknown configuration fields.
- Verify a claimed 4 GiB payload is rejected before allocation.
- Verify Python and Go load `taskwire.example.yaml` to equivalent normalized values.
- Verify cluster, storage, lease, and Kafka conditional validation.
- Seed Python and Go fuzzers with all valid message forms and malformed boundary cases.

## Implementation Order

1. Define golden schema fixtures and error codes.
2. Implement the pure-Python frame codec and envelope validators.
3. Port them to Go and run parity tests immediately.
4. Implement both config loaders against the same YAML fixture.
5. Add fuzz targets and commit the seed corpus.

## Exit Gate

Phase 1 is complete when Python and Go pass the same golden vectors and configuration fixture, all malformed-input tests fail closed without large allocation, no callback or direct-result-delivery field remains, and later phases can depend on the schemas without inventing new wire fields.
