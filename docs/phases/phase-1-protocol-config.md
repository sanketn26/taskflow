# Phase 1 — Language-Neutral Protocol and Configuration

## Goal

Freeze the language-neutral wire, envelope, reference, worker-capability, and configuration contracts used by the Python SDK, all worker runtimes, and the Go agent. This phase contains no task execution, daemons, networking, or persistent storage implementations. Python is the first implemented worker runtime, but the v1 contract must admit Node.js and Go workers without adding fields or changing task persistence.

Workers never receive application callback addresses, and Kafka is not a result-transport mode. The schemas in this file are the complete protocol and configuration contract for implementation.

## Phase 0 Baseline

Phase 0 is complete. This phase builds on the existing Poetry package rooted at `python/taskwire`, Go module and agent command under `agent/`, root `Makefile`, and pytest layout under `python/tests`. Keep `pyproject.toml` as the single release-version source, preserve `taskwire.__version__`, `taskwire-agent version`, `TASKWIRE_AGENT_PATH`, and packaged-binary discovery, and continue to support CPython 3.11–3.13 with no required native extension.

The current Go config loader and `taskwire-agent` server are lifecycle stubs. This phase replaces the stub path-only YAML (`socket_path`, `log_path`, `state_dir`, and `object_dir`) with the contract below and updates `AgentHarness._write_config()` at the same time. The stub's newline-delimited `STATUS` probe may remain only as a short-lived readiness adapter; committed protocol compatibility tests use the framed `STATUS` message defined here. Existing Phase 0 lifecycle, version-parity, wheel-install, agent-discovery, and acceleration-fallback tests must remain green.

## Deliverables

- A thin Python and Go frame transport with semantic interoperability.
- One generated Protobuf control-plane schema shared by every runtime.
- Shared definitions for task identity, `ObjectRef`, owner IDs, cursors, failures, and result records.
- Worker-runtime and task-capability registration independent of scheduling labels.
- A portable task-value profile shared by Python, Node.js, and Go.
- Python and Go configuration loaders validated against one example YAML file.
- Fuzz seeds for frame and envelope decoding.

## Wire Frame

Every connection uses this 31-byte header followed by `payload_len` bytes. The
payload is a serialized `taskwire.v1.ControlMessage` Protobuf. The frame remains
Taskwire-specific because it carries connection-local request correlation and
task identity; it does not define a second control-plane schema or codec.

| Offset | Size | Field | Encoding |
|---:|---:|---|---|
| 0 | 1 | protocol version | `0x01` |
| 1 | 1 | message type | unsigned byte |
| 2 | 16 | task ID | raw UUID bytes; zero UUID for connection-scoped requests |
| 18 | 8 | request ID | unsigned big-endian uint64; nonzero for requests and copied by their direct response |
| 26 | 1 | flags | bit field |
| 27 | 4 | payload length | unsigned big-endian uint32 |

Flags are `0x01` error, `0x02` idempotent, and `0x04` forwarded. Unknown flag bits are rejected in v1. A requester chooses a nonzero request ID unique among its in-flight requests on that connection. `ACK`, `ERROR`, `TASK`, object-transfer responses, and query/status responses copy it. Unsolicited `RESULT` notifications use request ID zero and are correlated by task ID plus cursor. Reuse while the earlier request is in flight is `duplicate_request`; reconnect starts a new request-ID namespace. Readers validate the version, type, flags, and configured maximum length before allocating a payload buffer. EOF before the first header byte is a clean disconnect; EOF after any frame byte is a truncated-frame error.

The error flag is required exactly on `ERROR` frames. The idempotent flag is valid only on retryable-by-identity `SUBMIT`, `COMPLETE`, result `ACK`, and object PUT requests. The forwarded flag is valid only on authenticated cluster `SUBMIT` and `COMPLETE` traffic. Invalid flag/type combinations are `unknown_flags`.

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
| `0x12` | `REGISTER_TASKS` | worker → local agent | `TaskRegistration`; `Ack(kind="register_tasks")` response |

Every local connection begins with `HELLO`; any other request before successful registration receives `not_registered` and closes the connection. A Runtime registers one owner ID and is eligible for that owner's `RESULT` notifications. A newer connection for the same owner becomes primary after resume begins; the old connection may finish in-flight responses but receives no new notifications. A worker registers its identity, runtime, and worker-wide codecs, then must successfully send `REGISTER_TASKS` before `PULL`. An admin connection may send only `STATUS`; the CLI and harness use this role. Local Unix-socket permissions are the v0.1 authentication boundary; owner ID remains a bearer capability for owner-scoped operations.

Runtime identity and task capability are not routing labels. The scheduler first filters by exact `(task_name, task_version)`, invocation profile, and input codec, then applies labels/resources among compatible workers. An incompatible task is never leased merely so a worker can return `unknown_task`. A repeated registration generation with identical content is idempotent; an older or conflicting generation is `task_conflict`. Disconnect removes that worker's capabilities without changing queued or terminal task state.

Registration generation starts at 1. A higher generation atomically replaces the
connection's complete task set; partial/delta registration is not supported in
v1. An empty task set is valid but can never receive a task. Duplicate task
name/version entries, an empty codec list, codecs absent from the worker HELLO,
and `python_args` or `cloudpickle` on a non-Python worker are `invalid_message`.
The `worker_id` in HELLO, registration, and PULL must match exactly.

`RESULT` is agent-to-Runtime only. ACK payloads are typed and never inferred from connection context. SUBMIT ACK is sent only after the configured input and task-state durability boundary. Responses copy the request ID; notification ACKs use a new nonzero request ID and identify the notification in their payload.

Object transfers are correlated by a random 16-byte `transfer_id`. Chunks are contiguous, zero-based, and individually bounded by the frame limit. PUT begins with metadata, streams chunks, and ends with an `eof` chunk; the final ACK returns the canonical `ObjectRef` only after size/checksum verification and the store durability boundary. GET returns metadata followed by chunks and a final ACK. A sequence gap, overrun, checksum mismatch, timeout, or disconnect aborts and cleans the partial transfer. Implementations enforce `ipc.max_active_transfers`, `ipc.max_transfer_bytes`, `ipc.object_chunk_bytes`, and `ipc.object_transfer_timeout_ms`. Only chunks for registered transfers are accepted. A connection may multiplex transfers and control requests because request ID and transfer ID provide independent correlation.

For GET, the agent first sends an `OBJECT_GET` response `{transfer_id, object: ObjectRef}` with the request ID, then zero or more `OBJECT_CHUNK` frames with that request ID, then `Ack(kind="object_get")`. For PUT, every chunk copies the initiating request ID and the final object-put ACK terminates it. `RESUME_RESULTS` emits up to `limit` request-ID-zero `RESULT` notifications followed by its correlated resume ACK; when `more` is true the Runtime repeats from `next_cursor`. `PULL` returns either `TASK` with the claimed task ID in the frame header or `Ack(kind="empty_pull")`.

## Protobuf Control Schema

`proto/taskwire/v1/control.proto` is the authoritative control-plane contract.
Every runtime uses generated types rather than reimplementing map schemas or
parsers. Each frame payload contains exactly one `ControlMessage.body` branch,
and that branch must agree with the frame message type. IDs are Protobuf `bytes`
fields with semantic validation requiring 16 bytes; checksums require 32 bytes.

Unknown Protobuf fields are accepted and preserved under Protobuf's binary
compatibility model. Tests compare decoded meaning and behavior, not serialized
byte identity: Protobuf is deliberately not a canonical cross-language byte
representation. Malformed wire data, a missing body, a body/frame mismatch,
invalid oneof state, and failed Taskwire semantic constraints remain errors.

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
  {
    role: "worker",
    worker_id: string,
    runtime: "python" | "nodejs" | "go",
    runtime_version: string,
    sdk_version: string,
    codecs: array<string>
  }
  {role: "admin"}

PullRequest {
  worker_id: string,
  capability_generation: uint64
}

TaskRegistration {
  worker_id: string,
  generation: uint64,                 # starts at 1 and increases on replacement
  tasks: array<{
    task_name: string,
    task_version: string,
    invocation: "value" | "python_args",
    codecs: array<string>
  }>
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
  invocation: "value" | "python_args",
  input: ValueRef,
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

`value` is the portable invocation profile: `input` contains one application value and the runtime adapter passes that value to the registered handler. `python_args` is a Python-only compatibility profile whose decoded input is exactly `{args: array, kwargs: map<string,any>}`. The agent treats input bytes as opaque but leases a task only to a worker advertising the matching invocation and codec. New cross-language APIs should use `value`; Python decorators may expose ordinary `*args/**kwargs` through `python_args`.

`Completion` and `ForwardedCompletion` each require exactly one of `result` or `failure`. The agent accepts a local completion only for the active fencing lease and a forwarded completion only for the active transfer. Dead-letter exhaustion is delivered as `state: "failed"` with failure code `max_attempts_exceeded`; `dead_lettered` is an internal/query state. A result notification is replayed until its ACK or retention expiry. Registered `task_name` plus `task_version` is the production identity; runtime is not part of that identity. Inline serialized functions are permitted only when `tasks.allow_inline_functions` is true, use the reserved identity `__inline__`, the `python_args` invocation, and the `cloudpickle` codec.

### Portable task-value profile

Protobuf control messages and task-value MsgPack are separate layers. Control decoding never unpacks `ValueRef.inline`. A portable `msgpack` task value is restricted recursively to nil, boolean, signed or unsigned 64-bit integer, float64, valid UTF-8 string, binary, array, and map with UTF-8 string keys. NaN, infinity, extension types, timestamps, non-string map keys, and integers outside 64-bit ranges are rejected. Portable maps sort keys by UTF-8 bytes and use minimum-size collection/string/binary headers. Portable integers use the smallest legal representation (nonnegative values use unsigned encodings) and floats are always float64, producing deterministic bytes without a schema-specific integer width.

Python tuples, sets, classes, and arbitrary-size integers; JavaScript `undefined`, `number` values outside the safe integer range, `Date`, symbols, and class instances; and Go structs or maps without an explicit portable adapter are not implicit portable values. Node.js exposes full-width integers as `bigint`; conversion to `number` requires a checked safe range. `bytes` is portable but application-defined. `cloudpickle` is Python-only and must be rejected unless the worker advertises runtime `python`. No Node.js or Go equivalent of inline executable-function serialization is part of v1.

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
  {kind: "register_tasks", worker_id: string, generation: uint64, accepted: uint32}

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

workers:
  shutdown_grace_ms: 30000
  restart_backoff_min_ms: 250
  restart_backoff_max_ms: 30000
  restart_limit: 5
  restart_window_seconds: 60
  pools:
    - name: "python-default"
      runtime: "python"
      command: ["python3", "-m", "taskwire.worker.runner"]
      count: 4
      working_directory: "."
      environment: {}
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
- Worker-pool names are non-empty and unique; runtime is exactly `python`, `nodejs`, or `go`; command is a non-empty argv array with no shell interpretation; count may be zero; working directory must exist at agent startup; environment keys cannot override Taskwire-owned socket/config/worker-ID/pool variables; and restart minimum cannot exceed maximum. The command executable is resolved at agent startup, not config-load time. Runtime-specific imports and bootstrap behavior are command arguments or application-owned environment; the agent does not interpret Python module, Node.js package, or Go symbol names.
- `sqlite_synchronous` is exactly `FULL` or `NORMAL`; production defaults to `FULL`. Backend-specific fields supplied for a different backend are rejected rather than ignored.

## Platform Contract

v0.1 supports Linux and macOS on architectures for which the release publishes an agent artifact. IPC uses Unix-domain sockets and lifecycle tests require POSIX signals. Windows is not supported in v0.1; Python imports and pure codec unit tests may run there, but agent discovery must report an unsupported-platform installation error rather than implying `.exe` support exists. Adding Windows requires a separately versioned named-pipe, service, and process-lifecycle contract plus its own artifact gate.

## Exact Implementation Changes

### Shared schema is the executable contract

Maintain `proto/taskwire/v1/control.proto` as the single language-neutral
control schema. Generated bindings are committed so consumers do not require a
Protobuf compiler at installation time. Cross-language tests exchange messages
between the Python harness and Go agent and compare semantic values. Exact
Protobuf byte identity is deliberately not a compatibility requirement.

Add `testdata/config/normalized.yaml`, containing the normalized representation
expected from `taskwire.example.yaml`. Paths in this expected file are represented
relative to the fixture directory and resolved by each test before comparison.
Do not compare debug strings or marshalled language structs.

### Python protocol package

Create or replace these files:

- `python/taskwire/protocol/frames.py`: define `HEADER_SIZE = 31`,
  `PROTOCOL_VERSION = 1`, `MAX_UINT32`, `Flag(IntFlag)`, `MessageType(IntEnum)`,
  and frozen `Frame(version, message_type, task_id, request_id, flags, payload)`.
  Export `encode_frame(frame, *, max_payload_bytes) -> bytes`,
  `decode_header(data, *, max_payload_bytes) -> FrameHeader`, and
  `read_frame(read_exact, *, max_payload_bytes) -> Frame | None`. `read_exact(n)`
  is an injected callable so unit tests require no socket. It returns `None` only
  when EOF occurs before byte zero; partial header/payload EOF raises
  `TruncatedFrame`.
- `python/taskwire/protocol/pb/control_pb2.py`: generated schema bindings.
- `python/taskwire/protocol/messages.py`: re-export generated schema types,
  validate Taskwire semantic constraints, and wrap/unwrap `ControlMessage`.
  It also owns the separate portable task-value MsgPack adapter.
- `python/taskwire/protocol/errors.py`: the stable error-code constants, one
  immutable `ERROR_RETRYABLE: Mapping[str, bool]`, and protocol exceptions
  `ProtocolDecodeError(code, message)` with subclasses for frame truncation and
  size rejection. Exception messages contain offsets/field names but never
  payload values.
- `python/taskwire/protocol/session.py`: a pure state machine, with no sockets,
  implementing pre-HELLO rejection, role/message authorization, in-flight
  request-ID registration/completion, owner matching, result-ACK correlation, and
  connection-local namespace reset. Primary-owner connection selection itself
  belongs to Phase 2; here expose deterministic transition outputs which Phase 2
  can use to perform replacement.
- `python/taskwire/protocol/__init__.py`: re-export only the supported enums,
  frame API, schema types, codec functions, and errors. Importing `taskwire` must
  remain independent of the optional acceleration module.

The control decoder never invokes MsgPack. Portable task-value MsgPack uses the
established language library with string map keys, duplicate-key rejection,
finite float64 values, and caller-enforced frame/object size bounds.

### Go protocol package

The Go implementation is split into generated `pb/control.pb.go`, `frame.go`,
`messages.go`, `portable_msgpack.go`, `errors.go`, and `session.go`:

- `FrameHeader`, `Frame`, `MessageType`, and `Flags` mirror the Python API.
  `ReadFrame(r io.Reader, maxPayload uint32) (*Frame, error)` uses
  `io.ReadFull`, distinguishes clean initial EOF from truncation, validates the
  header before `make([]byte, payloadLen)`, and returns typed errors carrying a
  stable code. `WriteFrame(w io.Writer, frame Frame, maxPayload uint32) error`
  must handle short writes.
- Generate schema structs from the shared `.proto`. Protobuf `oneof` fields
  represent unions; Taskwire validation applies fixed ID/checksum lengths and
  domain invariants before encode and after decode.
- `messages.go` contains only Protobuf dispatch and semantic validation.
  `portable_msgpack.go` is exclusively the task-value codec.
- Export the same stable error constants and retryability lookup as Python. The
  session validator remains a pure state machine and has no dependency on the
  stub server or future scheduler packages.

The Protobuf and pure-Go MsgPack runtimes are direct dependencies. Optional Rust
value-codec acceleration remains deferred until profiling justifies it.

### Configuration loaders

`taskwire.example.yaml` is currently a pre-contract example (nested `sqlite`,
PostgreSQL/S3 placeholders, `result_delivery`, and missing Phase 1 fields). Replace
its contents with the exact Configuration Contract shown in this document. The
checked-in example is the single cross-language fixture; do not maintain a second
“test-only valid config.” Use repository-safe relative paths in the checked-in
file so `make agent-run` does not require `/var` access.

In `python/taskwire/config.py`, define frozen nested dataclasses and
`load_config(path: str | Path) -> Config`. In `agent/internal/config/config.go`,
replace the four-field stub with corresponding nested structs and keep
`Load(path string) (*Config, error)`. Both loaders must:

- apply defaults only for fields present in the example contract with documented
  defaults; required fields are never silently synthesized;
- reject duplicate YAML keys and unknown keys at every depth;
- reject YAML aliases, custom tags, non-string map keys, and multiple documents;
- resolve `socket`, SQLite `dsn`, filesystem-store `root`, and worker
  `working_directory` relative to the config file's resolved parent directory;
- perform structural decoding first and conditional validation second;
- return errors containing the dotted field path and a stable category
  (`invalid_config` or `unsupported_backend`) without environment substitution,
  filesystem creation, executable lookup, or network access.

Development-store warnings are returned as `Config.warnings` / `Config.Warnings`,
not logged by the library. Startup checks for each pool command executable and
working directory are deliberately left to the agent command, because loader
tests must be hermetic.

Update `agent/cmd/taskwire-agent/main.go` to read `cfg.Socket`, the SQLite DSN, and
the selected filesystem store root. Update `harness/agent_harness.py::_write_config`
to emit the complete contract with one SQLite state store, one filesystem object
store, clustering/Kafka disabled, an empty `workers.pools` array, and all paths
under `base_dir`. No Phase 0 key (`socket_path`, `log_path`, `state_dir`,
`object_dir`) or superseded single-runtime worker key (`workers.count`,
`workers.python_executable`) may remain accepted.

### Framed STATUS readiness adapter

Change `AgentHarness.status()` to return a decoded `StatusSnapshot`. It opens a
connection, sends `HELLO(role="admin")`, requires `Ack(kind="hello")`, sends an
empty `STATUS` with a new nonzero request ID, and validates that the response
copies that ID. `_socket_responds()` returns true only when `snapshot.ready` is
true. Update lifecycle assertions accordingly.

Phase 1 may adapt `agent/internal/stubserver` just far enough to decode frames,
run the session validator, and answer HELLO/STATUS. It must not add task execution,
storage, scheduling, or general networking. Delete the newline `STATUS` path once
the harness is migrated; compatibility tests must fail if text status is accepted.

## Test Layout and Required Assertions

| Test file | Required assertions |
|---|---|
| `python/tests/unit/test_protocol_frames.py` | exact 31-byte header, all flag/type rules, clean EOF versus every truncation offset, length checked before payload read/allocation, short-reader behavior |
| `python/tests/unit/test_protocol_messages.py` | generated-schema/oneof round trips, body/frame agreement, unknown-field compatibility, semantic ID validation, and separate portable-value boundaries |
| `python/tests/unit/test_protocol_session.py` | HELLO-first, worker registration before PULL, capability generation fencing, role matrix, duplicate in-flight request, completion frees ID, reconnect reset, owner mismatch, notification ACK correlation |
| `python/tests/unit/test_config.py` | complete example, defaults, relative paths, duplicate/unknown/nested keys, YAML restrictions, every conditional validation branch |
| `agent/pkg/protocol/*_test.go` | the same frame, schema, session, and error assertions against Go APIs |
| `agent/internal/config/config_test.go` | the same configuration table cases as Python and normalized fixture comparison |
| `python/tests/integration/test_protocol_compat.py` | Python-generated HELLO/STATUS requests interoperate with Go-generated responses; newline STATUS fails |
| existing Phase 0 tests | lifecycle, version parity, wheel install, discovery, and acceleration fallback remain green after assertion updates |

Specific regression cases are mandatory:

- A header claiming payload length `0xffffffff` with a 16 MiB configured limit
  returns `frame_too_large` after exactly 31 bytes have been read and without a
  payload allocation/read attempt.
- Every stable error code and retryability bit matches in Python and Go tests.
- `TaskQuery` wrong-owner and nonexistent IDs produce indistinguishable unknown
  snapshots when passed through the pure owner-filter helper. Actual task lookup
  and owner-primary connection replacement remain Phase 2 integration work.
- Concurrent request behavior is tested by interleaving pure session-state
  transitions; Phase 1 does not claim a production concurrent socket server.
- Python and Go normalized config values match the shared expected fixture,
  including resolved paths and warnings.
- Python, Node.js, and Go capability examples for the same portable task decode
  to the same registration semantics; `cloudpickle` with a non-Python runtime and an
  incompatible invocation/codec combination are rejected before PULL.

Python ordinary tests iterate compact in-code frame and malformed Protobuf seeds
so CI exercises fuzz-style cases without a plugin. Go exposes `FuzzReadFrame` and
`FuzzDecodePayload` with representative valid and malformed seeds. Properties
are: never panic, never allocate beyond the configured bound, accepted messages
round-trip semantically, and errors use a registered code.

## Implementation Order

1. Commit the shared `.proto`, normalized config fixture, and semantic tests
   first; they should fail because generated bindings and APIs are absent.
2. Define the shared Protobuf schema and generate Python and Go bindings, then
   implement frames, semantic validation, and the pure session state machine.
3. Prove semantic Python/Go interoperability before touching the server.
4. Replace the example YAML and both config loaders, then migrate the command and
   harness config atomically.
5. Add framed HELLO/STATUS to the stub server and migrate readiness/lifecycle
   assertions; remove text STATUS.
6. Add fuzz targets/corpora, then run `make format`, `make lint`, `make unit`,
   `make integration`, and `make smoke-wheel`.

## Exit Gate

Phase 1 is complete when Python and Go interoperate through the shared Protobuf schema and configuration fixture, malformed input fails closed without large allocation, no callback or direct-result-delivery field remains, and later phases can evolve the schema using Protobuf compatibility rules.
