# Phase 1 — Protocol + Config

## Goal

Define the shared wire protocol and configuration schema implemented in both Python and Go.
No execution, no networking, no processes. Pure data encoding and config parsing.

## Testable Outcome

- Frame encode → decode round-trip passes in Python (unit tests)
- Frame encode → decode round-trip passes in Go (unit tests)
- A frame encoded in Python can be decoded in Go (cross-language test via subprocess)
- `Config.load()` correctly parses a YAML file in Python
- `config.Load()` correctly parses a YAML file in Go
- Invalid/missing config raises `ConfigError` (Python) or returns an error (Go)

---

## Files

```
sdk/taskflow/protocol/messages.py
sdk/taskflow/protocol/frames.py
sdk/taskflow/protocol/__init__.py
sdk/taskflow/config.py
sdk/taskflow/exceptions.py

agent/pkg/protocol/protocol.go
agent/pkg/protocol/protocol_test.go
agent/internal/config/config.go
agent/internal/config/config_test.go
```

---

## Python

### `sdk/taskflow/exceptions.py`

Single-responsibility exception hierarchy. All exceptions inherit from `TaskflowError` so callers can catch broadly or narrowly.

| Class | Inherits | Purpose |
|-------|----------|---------|
| `TaskflowError` | `Exception` | Base class for all taskflow exceptions |
| `SidecarNotRunning` | `TaskflowError` | Agent socket not reachable |
| `ConfigError` | `TaskflowError` | YAML parse failure or missing required field |
| `ProtocolError` | `TaskflowError` | Frame too short, unknown type, payload length mismatch |
| `TaskExecutionError` | `TaskflowError` | Task raised an exception on the worker; fields: `task_id: bytes`, `reason: str`, `original: BaseException \| None` |
| `DeliveryFailedError` | `TaskflowError` | Task succeeded but result could not be delivered; fields: `task_id: bytes`, `reason: str` |
| `LeaseExpiredError` | `TaskflowError` | Worker's lease TTL elapsed — stop working on this task |

**Pattern:** Exception hierarchy (not a flat list) so callers can `except TaskflowError` or be specific.

---

### `sdk/taskflow/protocol/messages.py`

| Symbol | Type | Purpose |
|--------|------|---------|
| `MessageType` | `IntEnum` | Canonical message type constants, shared between SDK and worker so both sides agree on values |

`MessageType` values:

| Name | Value | Direction | Meaning |
|------|-------|-----------|---------|
| `SUBMIT` | `0x01` | App → Sidecar | Submit a task for execution |
| `PULL` | `0x02` | Worker → Sidecar | Request next available task |
| `TASK` | `0x03` | Sidecar → Worker | Deliver task with lease |
| `HEARTBEAT` | `0x04` | Worker → Sidecar | Keep lease alive |
| `RESULT` | `0x05` | Worker → App | Deliver result (direct mode) |
| `STEAL` | `0x08` | Sidecar → Sidecar | Request tasks from peer (Phase 5) |
| `ACK` | `0x09` | Any → Any | Generic acknowledgement |

---

### `sdk/taskflow/protocol/frames.py`

#### `Frame` (dataclass, frozen=True)

Immutable value object representing one message on the wire.

| Field | Type | Description |
|-------|------|-------------|
| `msg_type` | `MessageType` | Message type enum value |
| `task_id` | `bytes` | 16-byte UUID in raw form |
| `flags` | `int` | Bit field: `0x01` = error result, `0x02` = idempotent task |
| `payload` | `bytes` | Message-specific body (cloudpickle, msgpack, or empty) |

#### `FrameCodec` (no state — class of static methods, SRP)

All methods are pure functions. No instantiation needed.

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `HEADER_SIZE` | `ClassVar[int] = 22` | Constant: 1 + 16 + 1 + 4 bytes |
| `encode` | `(frame: Frame) -> bytes` | Pack header fields big-endian using `struct.pack(">B16sBL", ...)` followed by payload bytes |
| `decode` | `(data: bytes) -> Frame` | Validate `len(data) >= HEADER_SIZE`, unpack header, slice payload; raise `ProtocolError` if data is short or payload_len mismatches available data |
| `read_frame` | `(sock: socket.socket) -> Frame` | Read exactly `HEADER_SIZE` bytes (loop until all received), extract `payload_len`, read exactly that many more bytes; raise `ProtocolError` on short read or closed socket; this is the canonical way all components read from a socket |

**Pattern:** Static utility class (no state, no inheritance needed). All logic is in functions — testable without instantiation.

---

### `sdk/taskflow/config.py`

All config classes are frozen dataclasses. Immutable after load — no one mutates config at runtime.

#### `ClusterConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `node_name` | `str` | `""` | Auto-generates a UUID if empty at load time |
| `bind_addr` | `str` | `"0.0.0.0:7946"` | Gossip bind address |
| `advertise_addr` | `str` | `""` | Routable address peers use to reach this node |
| `seeds` | `list[str]` | `[]` | Bootstrap peer addresses for cluster join |
| `mdns` | `bool` | `True` | Enable mDNS for zero-config local discovery |

#### `ResourceConfig`

| Field | Type | Default |
|-------|------|---------|
| `max_memory_mb` | `int` | `2048` |
| `max_cpu_percent` | `int` | `80` |

#### `WorkerConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `count` | `int` | `4` | Number of Python worker processes to spawn |
| `labels` | `dict[str, str]` | `{}` | Node labels used for routing |
| `resources` | `ResourceConfig` | defaults | Resource caps |

#### `DirectDeliveryConfig`

| Field | Type | Default |
|-------|------|---------|
| `max_retries` | `int` | `3` |
| `retry_backoff_ms` | `int` | `500` |
| `retry_strategy` | `str` | `"exponential"` |

#### `QueueDeliveryConfig`

| Field | Type | Default |
|-------|------|---------|
| `type` | `str` | `"kafka"` |
| `brokers` | `list[str]` | `[]` |
| `topic` | `str` | `"taskflow-results"` |

#### `ResultDeliveryConfig`

| Field | Type | Description |
|-------|------|-------------|
| `mode` | `str` | `"direct"` or `"queue"` |
| `direct` | `DirectDeliveryConfig` | Used when mode is "direct" |
| `queue` | `QueueDeliveryConfig` | Used when mode is "queue" |

#### `RoutingRule`

| Field | Type | Description |
|-------|------|-------------|
| `match_label` | `str` | Task label value to match |
| `prefer` | `dict[str, str]` | Node labels to prefer for matched tasks |

#### `RoutingConfig`

| Field | Type |
|-------|------|
| `rules` | `list[RoutingRule]` |

#### `Config`

Top-level config object. This is what both the SDK and (indirectly via YAML) the Go agent consume.

| Field | Type |
|-------|------|
| `cluster` | `ClusterConfig` |
| `socket` | `str` |
| `advertise_addr` | `str` |
| `workers` | `WorkerConfig` |
| `routing` | `RoutingConfig` |
| `result_delivery` | `ResultDeliveryConfig` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `load` | `classmethod(path: str \| None = None) -> Config` | If path given, load that file. Otherwise call `_find_config_file()`. Parse YAML, call `_from_dict()`, call `_validate()`. Raise `ConfigError` on any failure. |
| `_from_dict` | `classmethod(data: dict) -> Config` | Map raw parsed dict to dataclass hierarchy. Use `.get()` with defaults everywhere — never KeyError. |
| `_find_config_file` | `staticmethod() -> str \| None` | Walk `SEARCH_PATHS = ["./taskflow.yaml", "~/.taskflow/taskflow.yaml", "/etc/taskflow/taskflow.yaml"]`. Return first existing path or None. |
| `_validate` | `(self) -> None` | Raise `ConfigError` if `result_delivery.mode` is not "direct" or "queue". Raise if `workers.count < 1`. Auto-fill `cluster.node_name` with `uuid.uuid4().hex` if empty. |

**Pattern:** Factory Method (`Config.load` hides file discovery, parsing, and validation). Value Object (frozen dataclasses, data-only, no behaviour). Fail-fast (`_validate` on load, not at use time).

---

## Go

### `agent/pkg/protocol/protocol.go`

Lives in `pkg/` (not `internal/`) because tests and future tooling may import it directly.

#### Constants

```
MessageType  byte

Submit    = 0x01
Pull      = 0x02
Task      = 0x03
Heartbeat = 0x04
Result    = 0x05
Steal     = 0x08
Ack       = 0x09

HeaderSize = 22
```

#### `Frame` struct

| Field | Type | Description |
|-------|------|-------------|
| `Type` | `MessageType` | Message type |
| `TaskID` | `[16]byte` | UUID raw bytes |
| `Flags` | `byte` | Bit field (0x01 = error, 0x02 = idempotent) |
| `Payload` | `[]byte` | Message body |

#### Functions

| Function | Signature | Responsibility |
|----------|-----------|----------------|
| `Encode` | `(f *Frame) []byte` | Allocate `HeaderSize + len(f.Payload)` bytes. Write Type (1B), TaskID (16B), Flags (1B), payload length big-endian uint32 (4B), then payload. Return buffer. |
| `Decode` | `(data []byte) (*Frame, error)` | Check `len(data) >= HeaderSize`. Unpack header. Validate `len(data) >= HeaderSize + payloadLen`. Slice payload (no copy). Return Frame or error. |
| `ReadFrame` | `(r io.Reader) (*Frame, error)` | `io.ReadFull(r, header[:HeaderSize])`. Extract payloadLen. `io.ReadFull(r, payload)`. Call `Decode(header + payload)`. Return `io.EOF` cleanly if reader is closed before first byte; return `io.ErrUnexpectedEOF` if closed mid-frame. |

**Pattern:** Pure functions, no types with methods — Go idiomatic for a protocol codec.

---

### `agent/internal/config/config.go`

#### Structs

Mirror the Python dataclass hierarchy exactly — same field names in snake_case YAML, same defaults. This ensures one YAML file works for both.

| Struct | Fields |
|--------|--------|
| `Config` | `Cluster ClusterConfig`, `Socket string`, `AdvertiseAddr string`, `Workers WorkerConfig`, `Routing RoutingConfig`, `ResultDelivery ResultDeliveryConfig` |
| `ClusterConfig` | `NodeName, BindAddr, AdvertiseAddr string`, `Seeds []string`, `MDNS bool` |
| `ResourceConfig` | `MaxMemoryMB, MaxCPUPercent int` |
| `WorkerConfig` | `Count int`, `Labels map[string]string`, `Resources ResourceConfig` |
| `DirectDeliveryConfig` | `MaxRetries int`, `RetryBackoffMS int`, `RetryStrategy string` |
| `QueueDeliveryConfig` | `Type, Topic string`, `Brokers []string` |
| `ResultDeliveryConfig` | `Mode string`, `Direct DirectDeliveryConfig`, `Queue QueueDeliveryConfig` |
| `RoutingRule` | `MatchLabel string`, `Prefer map[string]string` |
| `RoutingConfig` | `Rules []RoutingRule` |

#### Functions

| Function | Signature | Responsibility |
|----------|-----------|----------------|
| `Load` | `(path string) (*Config, error)` | Read file at path, unmarshal YAML into Config struct, call `applyDefaults`, call `validate`, return. |
| `FindConfigFile` | `() (string, error)` | Walk standard paths (same as Python SEARCH_PATHS). Return first found or `("", ErrNotFound)`. |
| `applyDefaults` | `(c *Config)` | Set `NodeName` to `uuid.New().String()` if empty. Set `Workers.Count` to 4 if zero. Set `Socket` to `/var/run/taskflow/agent.sock` if empty. |
| `validate` | `(c *Config) error` | Return error if `ResultDelivery.Mode` is not "direct" or "queue". Return error if `Workers.Count < 1`. |

---

## Tests

### Python (`sdk/tests/unit/test_protocol.py`)

| Test | Asserts |
|------|---------|
| `test_encode_decode_roundtrip` | `FrameCodec.decode(FrameCodec.encode(frame)) == frame` for all MessageType values |
| `test_decode_short_data` | `FrameCodec.decode(b"short")` raises `ProtocolError` |
| `test_payload_length_mismatch` | Frame header claims 100B payload but only 10B present → `ProtocolError` |
| `test_flags_preserved` | Frame with `flags=0x01` encodes and decodes with `flags=0x01` |
| `test_task_id_preserved` | 16-byte UUID survives round-trip byte-for-byte |

### Python (`sdk/tests/unit/test_config.py`)

| Test | Asserts |
|------|---------|
| `test_load_valid_file` | All fields parsed correctly from example YAML |
| `test_load_missing_file_uses_defaults` | `Config.load("/nonexistent.yaml")` raises `ConfigError` |
| `test_load_no_path_uses_defaults` | `Config.load()` with no config file in SEARCH_PATHS returns Config with defaults |
| `test_invalid_delivery_mode` | `mode: "ftp"` → `ConfigError` |
| `test_node_name_autofilled` | Empty `node_name` → filled with UUID string after load |

### Go (`agent/pkg/protocol/protocol_test.go`)

| Test | Asserts |
|------|---------|
| `TestEncodeDecodeRoundtrip` | All message types survive encode → decode |
| `TestReadFrameFromReader` | Pipe writer sends encoded frame, ReadFrame reads it correctly |
| `TestReadFrameEOF` | Closed reader before first byte returns `io.EOF` |
| `TestReadFrameUnexpectedEOF` | Reader closes mid-frame returns `io.ErrUnexpectedEOF` |

### Go (`agent/internal/config/config_test.go`)

| Test | Asserts |
|------|---------|
| `TestLoadValidConfig` | Example YAML parses all fields |
| `TestApplyDefaults` | Empty config gets correct defaults |
| `TestValidateInvalidMode` | Returns error for unknown delivery mode |

### Cross-language (`sdk/tests/integration/test_protocol_compat.py`)

| Test | Asserts |
|------|---------|
| `test_python_encode_go_decode` | Python encodes a SUBMIT frame, writes to file, Go binary reads + decodes it, prints JSON — Python asserts field values match |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Value Object | `Frame`, `Config` and all sub-configs | Immutable after creation, no side effects, safe to share across threads |
| Factory Method | `Config.load()`, `Config._from_dict()` | Hides file discovery, YAML parsing, and validation behind a single call |
| Static Utility | `FrameCodec` | All methods are pure functions; no state to manage or inject |
| Fail Fast | `Config._validate()` on load | Catch misconfiguration at startup, not deep in execution |
