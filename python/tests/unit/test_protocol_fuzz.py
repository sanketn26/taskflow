"""Small deterministic fuzz-style seeds for frame and Protobuf decoding."""

from __future__ import annotations

import io

import pytest

from taskwire.protocol.errors import ProtocolDecodeError
from taskwire.protocol.frames import Flag, Frame, MessageType, encode_frame, read_frame
from taskwire.protocol import messages as m

MAX_PAYLOAD = 16 * 1024 * 1024


def _valid_status_frame() -> bytes:
    payload = m.encode_payload(MessageType.STATUS, m.StatusRequest())
    return encode_frame(
        Frame(
            version=1,
            message_type=MessageType.STATUS,
            task_id=b"\x00" * 16,
            request_id=1,
            flags=Flag.NONE,
            payload=payload,
        ),
        max_payload_bytes=MAX_PAYLOAD,
    )


def _invalid_submit_payload() -> bytes:
    return m.ControlMessage(submit=m.TaskEnvelope()).SerializeToString()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01",
        b"\x01" * 30,
        b"\x01" * 31,
        _valid_status_frame(),
        _valid_status_frame()[:-1],
        bytes(range(256)),
    ],
    ids=[
        "empty",
        "one-byte",
        "short-header",
        "invalid-header",
        "valid-status",
        "truncated-payload",
        "all-bytes",
    ],
)
def test_fuzz_read_frame_never_panics(data):
    buf = io.BytesIO(data)

    def read_exact(n: int) -> bytes:
        return buf.read(n)

    try:
        frame = read_frame(read_exact, max_payload_bytes=MAX_PAYLOAD)
    except ProtocolDecodeError:
        return  # a registered stable error code: acceptable failure

    if frame is None:
        return  # clean EOF before any header byte

    # Accepted frames must contain structurally valid header fields.
    assert frame.version == 1
    assert isinstance(frame.message_type, MessageType)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00",
        b"\xff",
        b"\x0a\xff",
        bytes(range(256)),
        _invalid_submit_payload(),
    ],
    ids=["empty", "zero", "invalid-tag", "truncated", "all-bytes", "invalid-submit"],
)
def test_fuzz_decode_payload_never_panics(data):
    for message_type in MessageType:
        try:
            value = m.decode_payload(message_type, data)
        except ProtocolDecodeError:
            continue  # a registered stable error code: acceptable failure

        # A successful decode must re-encode canonically and round-trip.
        re_encoded = m.encode_payload(message_type, value)
        again = m.decode_payload(message_type, re_encoded)
        assert again == value
