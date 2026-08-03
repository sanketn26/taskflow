# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased] - yyyy-mm-dd

### Added

- `TaskwireControl` gRPC service in `proto/taskwire/v1/control.proto`, replacing
  the `ControlMessage` envelope. Generated Go and Python service bindings are
  committed alongside the message bindings.
- `ControlClient` in the Python SDK, with `runtime`, `worker`, and `admin`
  constructors that dial the agent socket and attach role metadata.
- Role authorization interceptors and a stable-code ⇄ gRPC status mapping in
  `agent/pkg/protocol`, carrying the Taskwire `Error` in status details.

### Changed

- **The control plane is now gRPC over the Unix domain socket.** The custom
  31-byte frame header is gone: gRPC supplies framing, request correlation,
  ordering, flow control, deadlines, and cancellation. A client in any
  gRPC-capable language can now be generated from the `.proto` alone.
- The `HELLO` handshake is replaced by per-RPC metadata (`taskwire-role`,
  `taskwire-owner-id`), so a reconnect replays no connection-local state.
- `RESUME_RESULTS` plus unsolicited `RESULT` frames are replaced by the
  `WatchResults` server stream, where stream position is the cursor.
- Worker sessions use the `Work` duplex stream; stream lifetime now bounds lease
  ownership. The `empty_pull` ACK is gone — an idle agent simply sends nothing.
- Object transfers are streams (`PutObject`/`GetObject`), so `ObjectChunk` no
  longer carries a transfer ID or sequence number.
- The typed `Ack` union is replaced by per-RPC response messages.
- `queue.max_frame_size_mb` now bounds the gRPC max send/receive message size.
- Renamed `ProtocolDecodeError` to `ProtocolError` (Python) and `DecodeError` to
  `ProtocolError` (Go), since failures are no longer decode-specific.

### Removed

- The frame layer: `agent/pkg/protocol/frame.go` and
  `python/taskwire/protocol/frames.py`, plus their tests.
- `agent/internal/stubserver`, superseded by `agent/internal/controlserver`.
- Error codes made meaningless by gRPC: `unsupported_version`,
  `unknown_message_type`, `unknown_flags`, `frame_too_large`, and
  `duplicate_request`.

### Fixed

- The frame layer's `MessageType` enum and the proto `oneof` had drifted apart
  (18 constants versus 22 branches). A single service definition removes the
  possibility of that class of drift.
