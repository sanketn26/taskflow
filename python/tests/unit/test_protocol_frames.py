from __future__ import annotations

import io

import pytest

from taskwire.protocol.errors import FrameTooLarge, ProtocolDecodeError, TruncatedFrame
from taskwire.protocol.frames import (
    HEADER_SIZE,
    Flag,
    Frame,
    MessageType,
    decode_header,
    encode_frame,
    read_frame,
)

MAX_PAYLOAD = 16 * 1024 * 1024
ZERO_ID = b"\x00" * 16


def _reader(data: bytes):
    buf = io.BytesIO(data)

    def read_exact(n: int) -> bytes:
        return buf.read(n)

    return read_exact


def test_header_is_exactly_31_bytes():
    frame = Frame(1, MessageType.PULL, ZERO_ID, 1, Flag.NONE, b"")
    encoded = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    assert len(encoded) == HEADER_SIZE
    header = decode_header(encoded, max_payload_bytes=MAX_PAYLOAD)
    assert header.payload_len == 0


def test_round_trip_with_payload():
    payload = b"\x80"
    frame = Frame(1, MessageType.STATUS, ZERO_ID, 42, Flag.NONE, payload)
    encoded = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    decoded = read_frame(_reader(encoded), max_payload_bytes=MAX_PAYLOAD)
    assert decoded == frame


@pytest.mark.parametrize(
    "message_type,flags",
    [
        (MessageType.SUBMIT, Flag.IDEMPOTENT),
        (MessageType.SUBMIT, Flag.FORWARDED),
        (MessageType.COMPLETE, Flag.IDEMPOTENT),
        (MessageType.COMPLETE, Flag.FORWARDED),
        (MessageType.ACK, Flag.IDEMPOTENT),
        (MessageType.OBJECT_PUT, Flag.IDEMPOTENT),
        (MessageType.ERROR, Flag.ERROR),
    ],
)
def test_valid_flag_type_combinations(message_type, flags):
    frame = Frame(1, message_type, ZERO_ID, 1, flags, b"")
    encoded = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    decode_header(encoded, max_payload_bytes=MAX_PAYLOAD)


@pytest.mark.parametrize(
    "message_type,flags",
    [
        (MessageType.PULL, Flag.IDEMPOTENT),
        (MessageType.PULL, Flag.FORWARDED),
        (MessageType.HEARTBEAT, Flag.FORWARDED),
        (MessageType.STATUS, Flag.ERROR),
        (MessageType.ERROR, Flag.NONE),
        (MessageType.STEAL, Flag.FORWARDED),
    ],
)
def test_invalid_flag_type_combinations_rejected(message_type, flags):
    frame = Frame(1, message_type, ZERO_ID, 1, flags, b"")
    with pytest.raises(ProtocolDecodeError) as exc:
        encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    assert exc.value.code == "unknown_flags"


def test_unsupported_version_rejected():
    header = (
        bytes([2, MessageType.PULL.value])
        + ZERO_ID
        + (0).to_bytes(8, "big")
        + b"\x00"
        + (0).to_bytes(4, "big")
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        decode_header(header, max_payload_bytes=MAX_PAYLOAD)
    assert exc.value.code == "unsupported_version"


def test_unknown_message_type_rejected():
    header = (
        bytes([1, 0xFE])
        + ZERO_ID
        + (0).to_bytes(8, "big")
        + b"\x00"
        + (0).to_bytes(4, "big")
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        decode_header(header, max_payload_bytes=MAX_PAYLOAD)
    assert exc.value.code == "unknown_message_type"


def test_unknown_flag_bits_rejected():
    header = (
        bytes([1, MessageType.PULL.value])
        + ZERO_ID
        + (0).to_bytes(8, "big")
        + b"\x08"
        + (0).to_bytes(4, "big")
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        decode_header(header, max_payload_bytes=MAX_PAYLOAD)
    assert exc.value.code == "unknown_flags"


def test_frame_too_large_checked_before_payload_allocation():
    # payload_len = 0xffffffff with a 16 MiB configured limit: rejected
    # right after the 31-byte header, no payload read attempted.
    header = (
        bytes([1, MessageType.PULL.value])
        + ZERO_ID
        + (0).to_bytes(8, "big")
        + b"\x00"
        + (0xFFFFFFFF).to_bytes(4, "big")
    )
    assert len(header) == HEADER_SIZE
    with pytest.raises(FrameTooLarge):
        decode_header(header, max_payload_bytes=MAX_PAYLOAD)

    calls = []

    def read_exact(n: int) -> bytes:
        calls.append(n)
        if len(calls) == 1:
            return header[:1]
        if len(calls) == 2:
            return header[1:]
        raise AssertionError("payload must not be read after frame_too_large")

    with pytest.raises(FrameTooLarge):
        read_frame(read_exact, max_payload_bytes=MAX_PAYLOAD)
    assert calls == [1, HEADER_SIZE - 1]


def test_clean_eof_before_any_byte_returns_none():
    assert read_frame(_reader(b""), max_payload_bytes=MAX_PAYLOAD) is None


@pytest.mark.parametrize("cutoff", [1, 5, 15, 30])
def test_truncated_header_raises(cutoff):
    frame = Frame(1, MessageType.PULL, ZERO_ID, 1, Flag.NONE, b"")
    encoded = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    with pytest.raises(TruncatedFrame):
        read_frame(_reader(encoded[:cutoff]), max_payload_bytes=MAX_PAYLOAD)


def test_truncated_payload_raises():
    frame = Frame(1, MessageType.STATUS, ZERO_ID, 1, Flag.NONE, b"\x80")
    encoded = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    with pytest.raises(TruncatedFrame):
        read_frame(_reader(encoded[:-1]), max_payload_bytes=MAX_PAYLOAD)


def test_encode_frame_rejects_oversize_payload():
    frame = Frame(1, MessageType.STATUS, ZERO_ID, 1, Flag.NONE, b"\x00" * 10)
    with pytest.raises(FrameTooLarge):
        encode_frame(frame, max_payload_bytes=5)
