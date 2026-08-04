# Phase 1 — Language-Neutral Protocol and Configuration

## Goal

Define the smallest language-neutral boundary between clients, workers, and the
Go agent. The boundary consists of one Protobuf service, generated bindings,
stable domain errors, and configuration needed to operate that service. Python
is the first runtime, but no Python state machine is part of the wire contract.

Workers never receive application callback addresses, and Kafka is not a result-transport mode. The schemas in this file are the complete protocol and configuration contract for implementation.

## Phase 0 Baseline

Phase 0 is complete. This phase builds on the existing Poetry package rooted at `python/taskwire`, Go module and agent command under `agent/`, root `Makefile`, and pytest layout under `python/tests`. Keep `pyproject.toml` as the single release-version source, preserve `taskwire.__version__`, `taskwire-agent version`, `TASKWIRE_AGENT_PATH`, and packaged-binary discovery, and continue to support CPython 3.11–3.13 with no required native extension.

The current Go config loader and `taskwire-agent` server are lifecycle stubs. This phase replaces the stub path-only YAML (`socket_path`, `log_path`, `state_dir`, and `object_dir`) with the contract below and updates `AgentHarness._write_config()` at the same time. Committed protocol compatibility tests use the `Status` RPC defined here. Existing Phase 0 lifecycle, version-parity, wheel-install, agent-discovery, and acceleration-fallback tests must remain green.

## Deliverables

- A gRPC control plane over a Unix domain socket, with generated Python and Go
  clients and semantic interoperability.
- One generated Protobuf service and schema shared by every runtime.
- Shared definitions for task identity, `ObjectRef`, owner IDs, cursors, failures, and result records.
- Worker-runtime and task-capability registration independent of scheduling labels.
- A portable task-value profile shared by Python, Node.js, and Go.
- Python and Go configuration loaders validated against one example YAML file.
- Fuzz seeds for message decoding and semantic validation.

## Design principles

1. **The RPC is the unit of interaction.** Unary calls have no hidden
   connection state. Long-lived state exists only inside the stream that owns
   it.
2. **Generated types are boundary types.** Handlers accept generated Protobuf
   messages, validate their semantics, and call domain services. Taskwire does
   not wrap generated messages in a parallel protocol object model.
3. **`Work` is the worker session.** Registration, capability replacement,
   pulls, heartbeats, and completions share one duplex stream. Closing that
   stream releases its capabilities and begins lease recovery.
4. **Identity is established before domain work.** Local callers are admitted
   by Unix-socket permissions and RPC metadata. Peer agents use a separately
   authenticated transport in Phase 5. Every owner-scoped handler compares the
   authenticated owner with the request owner.
5. **Durability precedes acknowledgement.** A successful response means the
   documented state or object durability boundary has been crossed.
6. **Streams provide ordering, not persistence.** Results remain persisted and
   cursor-addressable; reconnecting clients resume from durable state.
7. **Limits and deadlines are explicit.** Message size, stream lifetime,
   object size, concurrency, and shutdown waits are bounded.

## Transport

The control plane is **gRPC over HTTP/2**, served on a Unix domain socket. gRPC
supplies message boundaries, request correlation, ordering, flow control,
deadlines, and cancellation. Taskwire defines only domain messages and RPC
semantics. Any supported runtime generates its boundary code from
`control.proto`.

`proto/taskwire/v1/control.proto` is the authoritative contract. Message sizes
are bounded by `ipc.max_message_size_mb` applied as the gRPC max send/receive
message size in both directions.

Local Unix-socket permissions are the v0.1 authentication boundary; owner ID
remains a bearer capability for owner-scoped operations.

### Identity and roles

Each local RPC carries identity in gRPC metadata:

| Metadata key | Value |
|---|---|
| `taskwire-role` | `runtime`, `worker`, or `admin` |
| `taskwire-owner-id` | 16 owner-ID bytes, hex-encoded; required for `runtime` |

Runtime owner identity travels with every call. Worker identity and capability
state are established inside `Work`, because those values have stream lifetime.
Peer identity belongs to the authenticated transport introduced in Phase 5.
Cluster RPCs are denied by the local role interceptor and must not be granted
merely because a caller supplied metadata.

## Service

| RPC | Kind | Caller | Purpose |
|---|---|---|---|
| `Submit` | unary | runtime | Enqueue one task; response after the durability boundary |
| `WatchResults` | server stream | runtime | Terminal results for one owner, resuming after a cursor |
| `AckResult` | unary | runtime | Confirm durable handling so the agent may release a result |
| `Cancel` | unary | runtime | Request cancellation of one owner's task |
| `QueryTasks` | unary | runtime | Owner-isolated task snapshots |
| `Work` | duplex stream | worker | Registration, capability updates, pulls, heartbeats, completions ⇄ leased tasks |
| `PutObject` | client stream | runtime/worker | Stream object content; returns the canonical `ObjectRef` |
| `GetObject` | server stream | runtime/worker | Stream stored object content |
| `Status` | unary | any | Agent status snapshot; also the readiness probe |
| `ForwardTask` | unary | agent | Transfer a task to a peer agent (Phase 5) |
| `ForwardCompletion` | unary | agent | Return a remote completion to the origin |
| `Steal` | unary | agent | Request work from a peer agent (Phase 5) |

Stream ownership is explicit:

- **`Work`** is the worker's session. Its first message must be a
  `WorkerRegistration` carrying identity, codecs, and the initial capability
  set; anything else is `not_registered`. Later `update_tasks` messages replace
  the complete capability set atomically. A pull must carry the registered
  worker ID and current generation. Stream lifetime bounds capability and lease
  ownership.
- **`WatchResults`** streams persisted results after a cursor. A reconnect opens
  a new stream from the last durably handled cursor. A newer stream for the same
  owner becomes primary; the old stream is cancelled.
- **`PutObject`/`GetObject`** give each object transfer its own ordered stream.
  The first chunk of a
  `PutObject` stream sets metadata (`codec`, `size`, `sha256`); the response
  returns the canonical `ObjectRef` only after size/checksum verification and
  the store durability boundary. A checksum mismatch, timeout, or disconnect
  aborts and cleans the partial transfer. Implementations enforce
  `ipc.max_active_transfers`, `ipc.max_transfer_bytes`, and
  `ipc.object_chunk_bytes`.

The scheduler first filters by exact `(task_name, task_version)`, invocation
profile, and input codec, then applies labels/resources among compatible
workers. An incompatible task is never leased merely so a worker can return
`unknown_task`. A repeated registration generation with identical content is
idempotent; an older or conflicting generation is `task_conflict`. Disconnect
removes that worker's capabilities without changing queued or terminal task
state.

Registration generation starts at 1. A higher generation atomically replaces the
stream's complete task set; partial/delta registration is not supported in v1.
An empty task set is valid but can never receive a task. Duplicate task
name/version entries, an empty codec list, codecs absent from the worker
registration, and `python_args` or `cloudpickle` on a non-Python worker are
`invalid_message`. The `worker_id` in the registration and in `PullRequest` must
match exactly.

Runtime identity and task capability are not routing labels.

Results are agent-to-Runtime only, delivered on the `WatchResults` stream.
Responses are typed per RPC and never inferred from connection context. The
`Submit` response is sent only after the configured input and task-state
durability boundary. A Runtime confirms durable handling with `AckResult`, which
lets the agent release a notification before retention expiry.

A `PullRequest` is answered with a `LeasedTask` when one is available. When no
compatible task exists, the agent sends nothing and keeps the stream open.

## Protobuf Control Schema

`proto/taskwire/v1/control.proto` is the authoritative control-plane contract.
Every runtime uses generated types rather than reimplementing map schemas or
parsers. gRPC selects the message type per RPC, so there is no envelope union
and no body/type agreement check: the service definition *is* the dispatch
table. IDs are Protobuf `bytes` fields with semantic validation requiring 16
bytes; checksums require 32 bytes.

Unknown Protobuf fields are accepted and preserved under Protobuf's binary
compatibility model. Tests compare decoded meaning and behavior, not serialized
byte identity: Protobuf is deliberately not a canonical cross-language byte
representation. Malformed wire data is rejected by gRPC before Taskwire sees it;
invalid oneof state and failed Taskwire semantic constraints remain errors.

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
WorkerRegistration {            # first message of a Work stream
  worker_id: string,
  runtime: "python" | "nodejs" | "go",
  runtime_version: string,
  sdk_version: string,
  codecs: array<string>,
  tasks: TaskRegistration
}

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

### Response and error schemas

Each RPC declares its own response message.

```text
SubmitResponse             {task_id: binary(16)}
CancelResponse             {task_id: binary(16), cancelled: bool}
CompletionResponse         {lease_id: binary(16)}
HeartbeatResponse          {lease_id: binary(16)}
WorkerRegistrationResponse {worker_id: string, generation: uint64, accepted: uint32}
PutObjectResponse          {object: ObjectRef}
ForwardTaskResponse        {task_id: binary(16), transfer_id: binary(16)}
ForwardCompletionResponse  {transfer_id: binary(16)}
StealResponse              {transfer_id: binary(16), accepted: uint32}
AckResultResponse          {}

Error {
  code: string,
  message: string,
  retryable: bool,
  details: map<string,string>
}
```

Errors are returned as gRPC statuses. The `Error` message above travels in the
status details, so the stable Taskwire code and its retryability survive
alongside the transport status code — a Taskwire client never has to infer
meaning from the gRPC code alone, and a generic gRPC client still sees a
sensible status. Error messages and details are bounded and safe for logs; they
never contain payload values.

The stable v1 error registry is: `malformed_payload`, `invalid_message`,
`not_registered`, `role_forbidden`, `owner_mismatch`, `task_conflict`,
`task_not_found`, `too_late`, `stale_lease`, `unknown_lease`, `unknown_task`,
`unsupported_codec`, `unsupported_backend`, `transfer_limit`,
`transfer_timeout`, `checksum_mismatch`, `storage_unavailable`,
`storage_consistency`, `shutdown`, and `internal`. Validation, authorization,
conflict, fencing, and consistency errors are non-retryable. Capacity, timeout,
storage-unavailable, shutdown, and internal errors are retryable. Python and Go
expose constants for this registry, and Phase 4 maps each code to a documented
SDK exception.

Malformed Protobuf, unsupported RPCs, message-size violations, cancellation,
and transport availability are represented by native gRPC status. Taskwire
status details are reserved for domain and authorization failures.

| Taskwire code | gRPC status |
|---|---|
| `malformed_payload`, `invalid_message`, `unsupported_codec`, `unsupported_backend` | `INVALID_ARGUMENT` |
| `not_registered`, `too_late` | `FAILED_PRECONDITION` |
| `role_forbidden`, `owner_mismatch` | `PERMISSION_DENIED` |
| `task_conflict`, `stale_lease` | `ABORTED` |
| `task_not_found`, `unknown_lease`, `unknown_task` | `NOT_FOUND` |
| `transfer_limit` | `RESOURCE_EXHAUSTED` |
| `transfer_timeout` | `DEADLINE_EXCEEDED` |
| `checksum_mismatch`, `storage_consistency` | `DATA_LOSS` |
| `storage_unavailable`, `shutdown` | `UNAVAILABLE` |
| `internal` | `INTERNAL` |

System-generated `Failure.code` values are `task_exception`, `unknown_task`, `serialization_error`, `max_attempts_exceeded`, `storage_consistency`, and `cancelled`. User exception type/module belongs in bounded failure details, not in the stable code. Failure retryability controls whether the task attempt may be retried; protocol `Error.retryable` controls whether the rejected operation may be retried and the two are never inferred from one another.

`Steal` takes `{requester_node, labels, limit}` and returns `StealResponse`; accepted tasks then use `ForwardTask` with stable task and transfer identity. A remote completion uses `ForwardCompletion`, and the origin validates the active transfer before accepting it. Phase 5 may add authenticated cluster-handshake metadata but may not change the task, object, response, or error schemas.

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
  max_message_size_mb: 16

queue:
  max_attempts: 5
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
- Message size, timeouts, batch sizes, attempts, and retention values are positive.
- `object_chunk_bytes` is no greater than `ipc.max_message_size_mb`; transfer count/size and task-query limits are positive.
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

- `python/taskwire/protocol/pb/control_pb2.py` and `control_pb2_grpc.py`:
  generated schema and service bindings.
- `python/taskwire/protocol/semantics.py`: semantic validation and the portable
  task-value MsgPack adapter. It defines no parallel message classes.
- `python/taskwire/ipc/client.py`: `ControlClient` with `runtime`,
  `worker`, and `admin` constructors. It dials `unix:<socket>`, attaches role
  metadata to every call, bounds message size, and re-raises agent failures as
  `ProtocolError` when the status carries a Taskwire detail.
- `python/taskwire/protocol/errors.py`: the stable error-code constants, one
  immutable `ERROR_RETRYABLE: Mapping[str, bool]`, the `GRPC_CODE` mapping, the
  `ProtocolError(code, message)` exception, and `error_from_rpc_error` to
  recover a Taskwire code from a gRPC status. Exception messages contain field
  names but never payload values.
- `python/taskwire/protocol/__init__.py`: expose the generated `pb` module,
  client, codec functions, semantic validation, and errors. Importing
  `taskwire` must remain independent of the optional acceleration module.

The control path never invokes MsgPack. Portable task-value MsgPack uses the
established language library with string map keys, duplicate-key rejection,
finite float64 values, and caller-enforced object size bounds.

### Go protocol package

The Go implementation is split into generated `pb/control.pb.go` and
`pb/control_grpc.pb.go`, plus `validation.go`, `portable_msgpack.go`,
`errors.go`, `roles.go`, and `status.go`:

- `roles.go` holds the metadata keys, the per-method role table, and the unary
  and stream interceptors that authorize every RPC and attach a
  `CallerIdentity` to the context.
- `status.go` maps each stable code onto a gRPC status code and packs the
  `Error` message into the status details; `ErrorFromStatus` recovers it.
- Generate schema structs and service stubs from the shared `.proto`. Protobuf
  `oneof` fields represent unions; `Validate` applies fixed ID/checksum lengths
  and domain invariants.
- `validation.go` contains only semantic validation. `portable_msgpack.go` is
  exclusively the task-value codec.
- Export the same stable error constants and retryability lookup as Python. The
  only worker session state is the state owned by a live `Work` handler.

The gRPC, Protobuf, and pure-Go MsgPack runtimes are direct dependencies.
Optional Rust value-codec acceleration remains deferred until profiling
justifies it.

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

### Status readiness adapter

`AgentHarness.status()` opens a `ControlClient.admin` against the agent socket
and calls the `Status` RPC, returning the `StatusSnapshot`. `_socket_responds()`
returns true only when `snapshot.ready` is true.

`agent/internal/controlserver` serves the gRPC service: it registers the
interceptors, answers `Status`, and validates the `Work` handshake and
capability registration. It must not add task execution, storage, scheduling, or
general networking. Readiness is defined exclusively by the `Status` RPC.

## Test Layout and Required Assertions

| Test file | Required assertions |
|---|---|
| `python/tests/unit/test_protocol_semantics.py` | generated-schema/oneof round trips, unknown-field compatibility, semantic ID validation, and separate portable-value boundaries |
| `python/tests/unit/test_protocol_errors.py` | every registered code maps to a gRPC status, the mapping has no extra codes, retryability and status code are exposed on the exception |
| `python/tests/unit/test_protocol_fuzz.py` | validation never crashes on arbitrary bytes and only raises registered codes |
| `python/tests/unit/test_config.py` | complete example, defaults, relative paths, duplicate/unknown/nested keys, YAML restrictions, every conditional validation branch |
| `agent/pkg/protocol/*_test.go` | schema semantics, role matrix, status mapping, and error assertions against Go APIs |
| `agent/internal/controlserver/worker_state_test.go` | Work registration, capability replacement, identity matching, and generation fencing |
| `agent/internal/controlserver/controlserver_test.go` | Status over a real socket, role enforcement with Taskwire status details, Work stream registration and heartbeat, socket removal on close |
| `agent/internal/config/config_test.go` | the same configuration table cases as Python and normalized fixture comparison |
| `python/tests/integration/test_protocol_compat.py` | Python gRPC client interoperates with the Go service; role metadata enforced; missing role rejected; Work stream registers and heartbeats |
| existing Phase 0 tests | lifecycle, version parity, wheel install, discovery, and acceleration fallback remain green after assertion updates |

Specific regression cases are mandatory:

- A message exceeding the configured limit is rejected by gRPC as
  `RESOURCE_EXHAUSTED` without reaching a Taskwire handler.
- Every stable error code, retryability bit, and gRPC status mapping matches in
  Python and Go tests.
- A request with missing role metadata is `not_registered`, and a role calling a
  forbidden RPC is `role_forbidden`; both carry a Taskwire `Error` detail.
- `TaskQuery` wrong-owner and nonexistent IDs produce indistinguishable unknown
  snapshots when passed through the pure owner-filter helper. Actual task lookup
  and owner-primary connection replacement remain Phase 2 integration work.
- Concurrent request behavior is tested by interleaving pure session-state
  transitions; Phase 1 does not claim a production concurrent scheduler.
- Python and Go normalized config values match the shared expected fixture,
  including resolved paths and warnings.
- Python, Node.js, and Go capability examples for the same portable task decode
  to the same registration semantics; `cloudpickle` with a non-Python runtime and an
  incompatible invocation/codec combination are rejected before a lease is granted.

Python ordinary tests iterate compact in-code malformed Protobuf seeds so CI
exercises fuzz-style cases without a plugin. Go exposes
`FuzzValidateTaskEnvelope` and `FuzzValidateWorkerRegistration` with
representative valid and malformed seeds. Properties are: never panic, accepted
messages round-trip semantically, and errors use a registered code.

## Implementation Order

1. Commit the shared `.proto` (messages *and* service), normalized config
   fixture, and semantic tests first; they should fail because generated
   bindings and APIs are absent.
2. Generate Python and Go bindings, then implement semantic validation, the
   status mapping, the role interceptors, and the pure session state machine.
3. Prove semantic Python/Go interoperability before touching the server.
4. Replace the example YAML and both config loaders, then migrate the command and
   harness config atomically.
5. Serve the gRPC service from `agent/internal/controlserver` and migrate
   readiness/lifecycle assertions.
6. Add fuzz targets/corpora, then run `make format`, `make lint`, `make unit`,
   `make integration`, and `make smoke-wheel`.

## Exit Gate

Phase 1 is complete when Python and Go interoperate through the shared gRPC service and configuration fixture, invalid input fails closed with a registered error code carried in the gRPC status details, no callback or direct-result-delivery field remains, and later phases can evolve the schema using Protobuf compatibility rules.

---

## Implementation Guide

> **Status:** Baseline is present. Use this to orient Phase 2 work and to fill
> any holes if a test is red. Do not change frozen schemas without a deliberate
> protocol version bump.

### File map (expected)

```text
proto/taskwire/v1/control.proto
python/taskwire/protocol/{client,messages,errors,session}.py
python/taskwire/protocol/pb/{control_pb2,control_pb2_grpc}.py
python/taskwire/config.py
agent/pkg/protocol/{messages,errors,roles,status,session,portable_msgpack}.go
agent/pkg/protocol/pb/{control.pb.go,control_grpc.pb.go}
agent/internal/config/{config,yamlstrict}.go
agent/internal/controlserver/controlserver.go   # Status + Work only until Phase 2
taskwire.example.yaml
testdata/config/normalized.yaml
```

### Residual verification

```bash
make unit
cd agent && go test ./pkg/protocol/... ./internal/config/... -count=1
python -m pytest python/tests/unit/test_protocol_*.py python/tests/unit/test_config.py -v
python -m pytest python/tests/integration/test_protocol_compat.py -v
```

- [ ] Oversized messages are rejected by gRPC before reaching a handler
- [ ] Missing role metadata is `not_registered`; a forbidden RPC is `role_forbidden`
- [ ] Work: registration first, identity match, generation fencing, capability replacement
- [ ] Portable msgpack rejects NaN/inf/non-string keys/extension types
- [ ] Python and Go configs match `testdata/config/normalized.yaml`
- [ ] No accepted `callback_addr` / `result_delivery` / text STATUS
- [ ] Error code registry, retryability, and gRPC status mapping identical in Python and Go

### Client sketch (Python — already expected)

```python
class ControlClient:
    def __init__(self, socket_path, *, role, owner_id=None, max_message_bytes=...):
        self._metadata = [(METADATA_ROLE, role)]
        if owner_id is not None:
            self._metadata.append((METADATA_OWNER_ID, owner_id.hex()))
        self._channel = grpc.insecure_channel(
            f"unix:{socket_path}",
            options=[
                ("grpc.max_receive_message_length", max_message_bytes),
                ("grpc.max_send_message_length", max_message_bytes),
            ],
        )
        self._stub = pb_grpc.TaskwireControlStub(self._channel)

    def status(self, timeout=None):
        return self._stub.Status(
            pb.StatusRequest(), timeout=timeout, metadata=self._metadata
        )
```

### Regenerating Protobuf (only if schema changes)

```bash
# See proto/README.md — commit generated Go + Python bindings; never require
# protoc at install time.
```

### Done checklist / review request

```text
Please review Phase 1.
Commands: make unit integration; go test ./pkg/protocol/... ./internal/config/...
Gaps: <none | list>
```

**Pass criteria:** Exit gate + residual verification green. Phase 2 may then
replace `controlserver`'s handlers with real scheduling without redefining the service.
