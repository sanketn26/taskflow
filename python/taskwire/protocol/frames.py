"""31-byte frame header codec: version, type, task ID, request ID, flags,
payload length, followed by ``payload_len`` bytes of msgpack payload.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Callable, Optional

from taskwire.protocol.errors import (
    UNKNOWN_FLAGS,
    UNKNOWN_MESSAGE_TYPE,
    UNSUPPORTED_VERSION,
    FrameTooLarge,
    ProtocolDecodeError,
    TruncatedFrame,
)

HEADER_SIZE = 31
PROTOCOL_VERSION = 1
MAX_UINT32 = 0xFFFFFFFF

_HEADER_STRUCT = struct.Struct(">BB16sQBI")


class Flag(IntFlag):
    NONE = 0x00
    ERROR = 0x01
    IDEMPOTENT = 0x02
    FORWARDED = 0x04


_ALL_FLAGS = Flag.ERROR | Flag.IDEMPOTENT | Flag.FORWARDED


class MessageType(IntEnum):
    SUBMIT = 0x01
    PULL = 0x02
    TASK = 0x03
    HEARTBEAT = 0x04
    RESULT = 0x05
    CANCEL = 0x06
    COMPLETE = 0x07
    STEAL = 0x08
    ACK = 0x09
    STATUS = 0x0A
    RESUME_RESULTS = 0x0B
    ERROR = 0x0C
    OBJECT_PUT = 0x0D
    OBJECT_GET = 0x0E
    OBJECT_CHUNK = 0x0F
    HELLO = 0x10
    TASK_QUERY = 0x11
    REGISTER_TASKS = 0x12


# Message types the idempotent flag may appear on: retryable-by-identity
# SUBMIT/COMPLETE requests, result ACKs, and object PUT requests. ACK is
# included at the frame layer because "result ACK" cannot be distinguished
# from other ACK kinds without decoding the payload.
_IDEMPOTENT_ALLOWED = frozenset(
    {MessageType.SUBMIT, MessageType.COMPLETE, MessageType.ACK, MessageType.OBJECT_PUT}
)
# The forwarded flag is valid only on authenticated cluster SUBMIT and
# COMPLETE traffic (ForwardedTask / ForwardedCompletion payloads).
_FORWARDED_ALLOWED = frozenset({MessageType.SUBMIT, MessageType.COMPLETE})


@dataclass(frozen=True)
class Frame:
    version: int
    message_type: MessageType
    task_id: bytes
    request_id: int
    flags: Flag
    payload: bytes


@dataclass(frozen=True)
class FrameHeader:
    version: int
    message_type: MessageType
    task_id: bytes
    request_id: int
    flags: Flag
    payload_len: int


def _validate_flag_type(message_type: MessageType, flags: Flag) -> None:
    is_error = bool(flags & Flag.ERROR)
    if is_error != (message_type == MessageType.ERROR):
        raise ProtocolDecodeError(
            UNKNOWN_FLAGS, "error flag is required exactly on ERROR frames"
        )
    if (flags & Flag.IDEMPOTENT) and message_type not in _IDEMPOTENT_ALLOWED:
        raise ProtocolDecodeError(
            UNKNOWN_FLAGS,
            f"idempotent flag invalid on message type {message_type.name}",
        )
    if (flags & Flag.FORWARDED) and message_type not in _FORWARDED_ALLOWED:
        raise ProtocolDecodeError(
            UNKNOWN_FLAGS, f"forwarded flag invalid on message type {message_type.name}"
        )


def encode_frame(frame: Frame, *, max_payload_bytes: int) -> bytes:
    if len(frame.task_id) != 16:
        raise ProtocolDecodeError("invalid_message", "task_id must be exactly 16 bytes")
    if len(frame.payload) > max_payload_bytes:
        raise FrameTooLarge(
            f"payload length {len(frame.payload)} exceeds configured max {max_payload_bytes}"
        )
    if frame.request_id < 0 or frame.request_id > 0xFFFFFFFFFFFFFFFF:
        raise ProtocolDecodeError("invalid_message", "request_id out of range")
    _validate_flag_type(frame.message_type, frame.flags)
    header = _HEADER_STRUCT.pack(
        frame.version,
        int(frame.message_type),
        frame.task_id,
        frame.request_id,
        int(frame.flags),
        len(frame.payload),
    )
    return header + frame.payload


def decode_header(data: bytes, *, max_payload_bytes: int) -> FrameHeader:
    if len(data) != HEADER_SIZE:
        raise ValueError(
            f"decode_header requires exactly {HEADER_SIZE} bytes, got {len(data)}"
        )

    version, message_type_raw, task_id, request_id, flags_raw, payload_len = (
        _HEADER_STRUCT.unpack(data)
    )

    if version != PROTOCOL_VERSION:
        raise ProtocolDecodeError(
            UNSUPPORTED_VERSION, f"unsupported protocol version {version}"
        )

    try:
        message_type = MessageType(message_type_raw)
    except ValueError:
        raise ProtocolDecodeError(
            UNKNOWN_MESSAGE_TYPE, f"unknown message type {message_type_raw:#x}"
        ) from None

    if flags_raw & ~int(_ALL_FLAGS):
        raise ProtocolDecodeError(UNKNOWN_FLAGS, f"unknown flag bits {flags_raw:#x}")
    flags = Flag(flags_raw)

    _validate_flag_type(message_type, flags)

    if payload_len > max_payload_bytes:
        raise FrameTooLarge(
            f"payload length {payload_len} exceeds configured max {max_payload_bytes}"
        )

    return FrameHeader(
        version=version,
        message_type=message_type,
        task_id=task_id,
        request_id=request_id,
        flags=flags,
        payload_len=payload_len,
    )


def read_frame(
    read_exact: Callable[[int], bytes], *, max_payload_bytes: int
) -> Optional[Frame]:
    """Read one frame using an injected ``read_exact(n) -> bytes`` callable.

    ``read_exact`` may return fewer than ``n`` bytes only at EOF. Returns
    ``None`` only when EOF occurs before any header byte is read; any EOF
    after that point raises ``TruncatedFrame``.
    """
    first = read_exact(1)
    if len(first) == 0:
        return None
    if len(first) != 1:
        raise TruncatedFrame("EOF within first header byte")

    rest = read_exact(HEADER_SIZE - 1)
    if len(rest) != HEADER_SIZE - 1:
        raise TruncatedFrame("EOF within frame header")

    header = decode_header(first + rest, max_payload_bytes=max_payload_bytes)

    payload = b""
    if header.payload_len:
        payload = read_exact(header.payload_len)
        if len(payload) != header.payload_len:
            raise TruncatedFrame("EOF within frame payload")

    return Frame(
        version=header.version,
        message_type=header.message_type,
        task_id=header.task_id,
        request_id=header.request_id,
        flags=header.flags,
        payload=payload,
    )
