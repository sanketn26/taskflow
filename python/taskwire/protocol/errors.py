"""Stable protocol error codes shared with the Go agent.

Values here are wire-visible contract, not free-form Python strings: both
languages expose the same code list and retryability lookup.
"""

from __future__ import annotations

from typing import Mapping

UNSUPPORTED_VERSION = "unsupported_version"
UNKNOWN_MESSAGE_TYPE = "unknown_message_type"
UNKNOWN_FLAGS = "unknown_flags"
FRAME_TOO_LARGE = "frame_too_large"
MALFORMED_PAYLOAD = "malformed_payload"
INVALID_MESSAGE = "invalid_message"
DUPLICATE_REQUEST = "duplicate_request"
NOT_REGISTERED = "not_registered"
ROLE_FORBIDDEN = "role_forbidden"
OWNER_MISMATCH = "owner_mismatch"
TASK_CONFLICT = "task_conflict"
TASK_NOT_FOUND = "task_not_found"
TOO_LATE = "too_late"
STALE_LEASE = "stale_lease"
UNKNOWN_LEASE = "unknown_lease"
UNKNOWN_TASK = "unknown_task"
UNSUPPORTED_CODEC = "unsupported_codec"
UNSUPPORTED_BACKEND = "unsupported_backend"
TRANSFER_LIMIT = "transfer_limit"
TRANSFER_TIMEOUT = "transfer_timeout"
CHECKSUM_MISMATCH = "checksum_mismatch"
STORAGE_UNAVAILABLE = "storage_unavailable"
STORAGE_CONSISTENCY = "storage_consistency"
SHUTDOWN = "shutdown"
INTERNAL = "internal"

# System-generated Failure.code values (separate namespace from Error codes
# above; task_exception/unknown_task/storage_consistency are never inferred
# from the protocol-level Error registry).
FAILURE_TASK_EXCEPTION = "task_exception"
FAILURE_UNKNOWN_TASK = "unknown_task"
FAILURE_SERIALIZATION_ERROR = "serialization_error"
FAILURE_MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
FAILURE_STORAGE_CONSISTENCY = "storage_consistency"
FAILURE_CANCELLED = "cancelled"

ERROR_RETRYABLE: Mapping[str, bool] = {
    UNSUPPORTED_VERSION: False,
    UNKNOWN_MESSAGE_TYPE: False,
    UNKNOWN_FLAGS: False,
    FRAME_TOO_LARGE: False,
    MALFORMED_PAYLOAD: False,
    INVALID_MESSAGE: False,
    DUPLICATE_REQUEST: False,
    NOT_REGISTERED: False,
    ROLE_FORBIDDEN: False,
    OWNER_MISMATCH: False,
    TASK_CONFLICT: False,
    TASK_NOT_FOUND: False,
    TOO_LATE: False,
    STALE_LEASE: False,
    UNKNOWN_LEASE: False,
    UNKNOWN_TASK: False,
    UNSUPPORTED_CODEC: False,
    UNSUPPORTED_BACKEND: False,
    TRANSFER_LIMIT: True,
    TRANSFER_TIMEOUT: True,
    CHECKSUM_MISMATCH: False,
    STORAGE_UNAVAILABLE: True,
    STORAGE_CONSISTENCY: False,
    SHUTDOWN: True,
    INTERNAL: True,
}


class ProtocolDecodeError(Exception):
    """Raised when frame/payload bytes fail decoding.

    ``message`` may contain offsets and field names but must never contain
    payload values.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_RETRYABLE:
            raise ValueError(f"unregistered error code: {code!r}")
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class TruncatedFrame(ProtocolDecodeError):
    """EOF occurred after at least one header/payload byte was read."""

    def __init__(self, message: str = "frame truncated before completion") -> None:
        super().__init__(MALFORMED_PAYLOAD, message)


class FrameTooLarge(ProtocolDecodeError):
    """A claimed payload length exceeds the configured maximum."""

    def __init__(
        self, message: str = "payload length exceeds configured maximum"
    ) -> None:
        super().__init__(FRAME_TOO_LARGE, message)
