"""Small deterministic fuzz-style seeds for Protobuf decoding and validation.

gRPC owns framing and message parsing, so the property under test is that
Taskwire's semantic validation never crashes on arbitrary bytes and only ever
raises ``ProtocolError`` with a registered stable code.
"""

from __future__ import annotations

import pytest
from google.protobuf.message import DecodeError

from taskwire.protocol import decode_portable_value, encode_portable_value, validate
from taskwire.protocol.errors import ERROR_RETRYABLE, ProtocolError
from taskwire.protocol.pb import control_pb2 as pb

# Message types reachable from untrusted input, paired with a valid instance
# used to seed the corpus.
SEEDS = [
    (
        pb.TaskEnvelope,
        pb.TaskEnvelope(
            owner_id=b"\x01" * 16,
            task_name="task",
            task_version="1",
            invocation="value",
            input=pb.ValueRef(inline=b"x", codec="msgpack"),
        ),
    ),
    (
        pb.WorkerRegistration,
        pb.WorkerRegistration(
            worker_id="w",
            runtime="python",
            runtime_version="3.12",
            sdk_version="0.1.0",
            codecs=["msgpack"],
            tasks=pb.TaskRegistration(worker_id="w", generation=1),
        ),
    ),
    (pb.HeartbeatRequest, pb.HeartbeatRequest(lease_id=b"\x02" * 16)),
    (pb.StatusRequest, pb.StatusRequest()),
]

CORPUS = [
    b"",
    b"\x00",
    b"\xff",
    b"\xff" * 64,
    b"\x0a\xff",
    bytes(range(32)),
]


@pytest.mark.parametrize("message_type,valid", SEEDS)
def test_valid_seeds_validate(message_type, valid):
    validate(message_type.FromString(valid.SerializeToString()))


@pytest.mark.parametrize("data", CORPUS)
@pytest.mark.parametrize("message_type,_valid", SEEDS)
def test_validation_never_crashes_on_arbitrary_bytes(message_type, _valid, data):
    try:
        decoded = message_type.FromString(data)
    except (DecodeError, ValueError):
        return  # malformed bytes are gRPC's layer, not ours

    try:
        validate(decoded)
    except ProtocolError as exc:
        assert exc.code in ERROR_RETRYABLE
        return

    # An accepted message must survive a re-encode round trip.
    again = message_type.FromString(decoded.SerializeToString())
    validate(again)


@pytest.mark.parametrize("data", CORPUS)
def test_portable_value_decoding_never_crashes(data):
    try:
        value = decode_portable_value(data)
    except ProtocolError as exc:
        assert exc.code in ERROR_RETRYABLE
        return
    assert decode_portable_value(encode_portable_value(value)) == value
