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
sdk/taskwire/protocol/messages.py
sdk/taskwire/protocol/frames.py        (pure-Python codec — always present)
sdk/taskwire/protocol/__init__.py      (selects Rust codec if importable, else pure Python)
sdk/taskwire/config.py
sdk/taskwire/exceptions.py

native/Cargo.toml                      (pyo3, maturin, abi3-py311)
native/src/lib.rs
native/src/frame.rs                    (Rust codec — same API as frames.py)

agent/pkg/protocol/protocol.go
agent/pkg/protocol/protocol_test.go
agent/internal/config/config.go
agent/internal/config/config_test.go
```

---

## Python

### `sdk/taskwire/exceptions.py`

Single-responsibility exception hierarchy. All exceptions inherit from `TaskwireError` so callers can catch broadly or narrowly.

| Class | Inherits | Purpose |
|-------|----------|---------|
| `TaskwireError` | `Exception` | Base class for all taskwire exceptions |
| `SidecarNotRunning` | `TaskwireError` | Agent socket not reachable |
| `ConfigError` | `TaskwireError` | YAML parse failure or missing required field |
| `ProtocolError` | `TaskwireError` | Frame too short, unknown type, payload length mismatch |
| `TaskExecutionError` | `TaskwireError` | Task raised an exception on the worker; fields: `task_id: bytes`, `reason: str`, `original: BaseException \| None` |
| `DeliveryFailedError` | `TaskwireError` | Task succeeded but result could not be delivered; fields: `task_id: bytes`, `reason: str` |
| `LeaseExpiredError` | `TaskwireError` | Worker's lease TTL elapsed — stop working on this task |

**Pattern:** Exception hierarchy (not a flat list) so callers can `except TaskwireError` or be specific.

---

### `sdk/taskwire/protocol/messages.py`

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
| `CANCEL` | `0x06` | App → Sidecar | Best-effort cancellation (Phase 4 uses it; defined now so the value is reserved) |
| `COMPLETE` | `0x07` | Sidecar → App | Lease released notification |
| `STEAL` | `0x08` | Sidecar → Sidecar | Request tasks from peer (Phase 5) |
| `ACK` | `0x09` | Any → Any | Generic acknowledgement |

---

### `sdk/taskwire/protocol/frames.py`

#### `Frame` (dataclass, frozen=True)

Immutable value object representing one message on the wire.

| Field | Type | Description |
|-------|------|-------------|
| `msg_type` | `MessageType` | Message type enum value |
| `task_id` | `bytes` | 16-byte UUID in raw form |
| `flags` | `int` | Bit field: `0x01` = error result, `0x02` = idempotent task |
| `payload` | `bytes` | Message-specific body (cloudpickle, msgpack, or empty) |

The wire header carries a leading `version` byte (`PROTOCOL_VERSION = 0x01`). It is *not* a `Frame` field — the codec writes it on encode and validates it on decode. Decoding a frame with an unknown version raises `ProtocolError("unsupported protocol version")`. This one byte is what allows v0.2 to evolve the format without silently corrupting v0.1 peers.

#### `FrameCodec` (no state — class of static methods, SRP)

All methods are pure functions. No instantiation needed.

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `HEADER_SIZE` | `ClassVar[int] = 23` | Constant: 1 (ver) + 1 (type) + 16 (task_id) + 1 (flags) + 4 (pay_len) bytes |
| `MAX_FRAME_SIZE` | `ClassVar[int] = 16 * 1024 * 1024` | Reject frames whose `pay_len` exceeds this — a corrupt length prefix must not OOM the process |
| `encode` | `(frame: Frame) -> bytes` | Pack header fields big-endian using `struct.pack(">BB16sBL", PROTOCOL_VERSION, ...)` followed by payload bytes |
| `decode` | `(data: bytes) -> Frame` | Validate `len(data) >= HEADER_SIZE`, check version byte, unpack header, slice payload; raise `ProtocolError` if data is short, version unknown, `pay_len > MAX_FRAME_SIZE`, or payload_len mismatches available data |
| `read_frame` | `(sock: socket.socket) -> Frame` | Read exactly `HEADER_SIZE` bytes (loop until all received), extract `payload_len`, read exactly that many more bytes; raise `ProtocolError` on short read or closed socket; this is the canonical way all components read from a socket |

**Pattern:** Static utility class (no state, no inheritance needed). All logic is in functions — testable without instantiation.

**Implementation notes (pure-Python codec):**

- `read_frame` must use a `recv_exact` loop — `sock.recv(n)` may return fewer than `n` bytes. Use `memoryview(bytearray(n))` and `sock.recv_into(view[got:])` to avoid per-chunk copies:

  ```python
  def _recv_exact(sock: socket.socket, n: int) -> bytes:
      buf = bytearray(n)
      view = memoryview(buf)
      got = 0
      while got < n:
          r = sock.recv_into(view[got:])
          if r == 0:
              raise ProtocolError(f"socket closed mid-frame ({got}/{n} bytes)")
          got += r
      return bytes(buf)
  ```

- `decode` must slice the payload by the *declared* `pay_len`, never "the rest of the buffer" — frames may be back-to-back in a stream.
- Pre-compile the struct: `_HEADER = struct.Struct(">BB16sBL")` at module level; `_HEADER.pack/unpack_from` is measurably faster than the module-level functions.

---

### `native/src/frame.rs` — Rust codec (`taskwire._native`)

Same public API as `frames.py` so the two are drop-in interchangeable. `sdk/taskwire/protocol/__init__.py` selects at import time:

```python
try:
    from taskwire._native import FrameCodec, Frame  # Rust
    NATIVE = True
except ImportError:
    from taskwire.protocol.frames import FrameCodec, Frame  # pure Python
    NATIVE = False
```

PyO3 sketch:

```rust
#[pyclass(frozen)]
pub struct Frame {
    #[pyo3(get)] msg_type: u8,
    #[pyo3(get)] task_id: Py<PyBytes>,   // 16 bytes
    #[pyo3(get)] flags: u8,
    #[pyo3(get)] payload: Py<PyBytes>,
}

#[pyfunction]
fn read_frame(py: Python<'_>, fd: i32) -> PyResult<Frame> {
    // py.allow_threads(|| { ... read_exact on the raw fd ... })
    // GIL is released for the entire blocking read — other Python
    // threads keep running while we wait on the socket.
}
```

Key decisions:

- The Rust side takes the **raw file descriptor** (`sock.fileno()`), not the Python socket object — all blocking I/O happens inside `py.allow_threads`, so it never holds the GIL while waiting.
- Build with `abi3-py311` so one wheel per platform covers all CPython ≥ 3.11 (and free-threaded builds via `cp313t` when PyO3 marks support stable).
- Why this is Phase 1 and not an afterthought: the codec API is the contract every later phase builds on. Defining the native/fallback split now means Phases 3–4 (heartbeat, result server) slot into an existing pattern instead of retrofitting one.

**Build:** `maturin develop` for local iteration; `maturin build --release` in CI. The Python package must import and pass all tests *without* the native module present (`pip install taskwire` from an sdist on an unsupported platform still works, just slower).

---

### `sdk/taskwire/config.py`

All config classes are frozen dataclasses. Immutable after load — no one mutates config at runtime.

#### `ClusterConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `node_name` | `str` | `""` | Auto-generates a UUID if empty at load time |
| `bind_addr` | `str` | `"0.0.0.0:7946"` | Gossip bind address |
| `advertise_addr` | `str` | `""` | Routable address peers use to reach this node |
| `seeds` | `list[str]` | `[]` | Bootstrap peer addresses for cluster join |
| `mdns` | `bool` | `True` | Enable mDNS for zero-config local discovery |
| `encryption_key` | `str` | `""` | Base64-encoded 32-byte key. When set, gossip traffic is encrypted (memberlist `SecretKey`). Validate length at load time. |

#### `QueueConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `persistence` | `str` | `"none"` | `"none"` (in-memory only) or `"wal"` (queue survives agent restart — Phase 2) |
| `wal_dir` | `str` | `"/var/lib/taskwire/wal"` | Directory for the write-ahead log |
| `max_attempts` | `int` | `5` | Lease-expiry re-queues before a task is dead-lettered |
| `max_frame_size_mb` | `int` | `16` | Reject frames larger than this |

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
| `topic` | `str` | `"taskwire-results"` |

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
| `socket_group` | `str` (default `"taskwire"` — socket is chmod 0660, group-owned) |
| `advertise_addr` | `str` |
| `queue` | `QueueConfig` |
| `workers` | `WorkerConfig` |
| `routing` | `RoutingConfig` |
| `result_delivery` | `ResultDeliveryConfig` |

| Method | Signature | Responsibility |
|--------|-----------|----------------|
| `load` | `classmethod(path: str \| None = None) -> Config` | If path given, load that file. Otherwise call `_find_config_file()`. Parse YAML, call `_from_dict()`, call `_validate()`. Raise `ConfigError` on any failure. |
| `_from_dict` | `classmethod(data: dict) -> Config` | Map raw parsed dict to dataclass hierarchy. Use `.get()` with defaults everywhere — never KeyError. |
| `_find_config_file` | `staticmethod() -> str \| None` | Walk `SEARCH_PATHS = ["./taskwire.yaml", "~/.taskwire/taskwire.yaml", "/etc/taskwire/taskwire.yaml"]`. Return first existing path or None. |
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
Cancel    = 0x06
Complete  = 0x07
Steal     = 0x08
Ack       = 0x09

ProtocolVersion = 0x01
HeaderSize      = 23   // 1 ver + 1 type + 16 task_id + 1 flags + 4 pay_len
MaxFrameSize    = 16 << 20
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
| `Encode` | `(f *Frame) []byte` | Allocate `HeaderSize + len(f.Payload)` bytes. Write ProtocolVersion (1B), Type (1B), TaskID (16B), Flags (1B), payload length big-endian uint32 (4B), then payload. Return buffer. |
| `Decode` | `(data []byte) (*Frame, error)` | Check `len(data) >= HeaderSize`. Check version byte == ProtocolVersion (else `ErrBadVersion`). Unpack header. Reject `payloadLen > MaxFrameSize` (`ErrFrameTooLarge`). Validate `len(data) >= HeaderSize + payloadLen`. Slice payload (no copy). Return Frame or error. |
| `ReadFrame` | `(r io.Reader) (*Frame, error)` | `io.ReadFull(r, header[:HeaderSize])`. Validate version + payloadLen *before* allocating the payload buffer. `io.ReadFull(r, payload)`. Call `Decode(header + payload)`. Return `io.EOF` cleanly if reader is closed before first byte; return `io.ErrUnexpectedEOF` if closed mid-frame. |

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
| `applyDefaults` | `(c *Config)` | Set `NodeName` to `uuid.New().String()` if empty. Set `Workers.Count` to 4 if zero. Set `Socket` to `/var/run/taskwire/agent.sock` if empty. |
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
| `test_unknown_version_rejected` | First byte `0x7F` → `ProtocolError` mentioning version |
| `test_oversized_frame_rejected` | Header claiming `pay_len` > MAX_FRAME_SIZE → `ProtocolError`, no allocation of the claimed size |
| `test_native_python_parity` | (skipped if `_native` not built) every frame encoded by the Rust codec decodes identically in pure Python and vice versa — byte-for-byte equality of `encode` output |

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

## Implementation Guide

Recommended build order — each step is testable before the next starts:

1. **Python exceptions + `messages.py`** — trivial, unblocks everything.
2. **Pure-Python `frames.py` + unit tests** — this is the reference implementation. Get the round-trip, short-read, version, and oversize tests green first.
3. **Go `protocol.go`** — port from the Python reference; run the cross-language test (Python encodes → Go decodes) immediately. Catching an endianness or offset mistake here costs minutes; catching it in Phase 3 costs hours.
4. **Config (Python, then Go)** — keep the YAML keys as the single source of truth; the two parsers are tested against the *same* `taskwire.example.yaml` file.
5. **Rust codec last** — by now the contract is frozen and covered by tests; the parity test (`test_native_python_parity`) is your safety net. Set up `maturin develop` in the Makefile (`make native`).

Pitfalls to expect:

- **Endianness**: Python `struct` `>` and Go `binary.BigEndian` must agree; the cross-language test exists precisely for this.
- **`task_id` as `bytes` vs `str`**: keep it raw 16 bytes everywhere; only `.hex()` it for logging. Mixing representations is the classic source of "task never resolves" bugs later.
- **msgpack `bytes`/`str` confusion**: configure `msgpack.unpackb(raw=False)` consistently and pin it in one helper module; Go's msgpack must emit bin-type for byte fields.

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Value Object | `Frame`, `Config` and all sub-configs | Immutable after creation, no side effects, safe to share across threads |
| Factory Method | `Config.load()`, `Config._from_dict()` | Hides file discovery, YAML parsing, and validation behind a single call |
| Static Utility | `FrameCodec` | All methods are pure functions; no state to manage or inject |
| Fail Fast | `Config._validate()` on load | Catch misconfiguration at startup, not deep in execution |
