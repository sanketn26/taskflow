"""Stable protocol error codes shared with the Go agent.

Values here are wire-visible contract, not free-form Python strings: both
languages expose the same code list and retryability lookup. Transport-level
failures (unsupported version, unknown method, oversize message) are gRPC's
responsibility and are not part of this registry.
"""

from __future__ import annotations

from collections.abc import Mapping

import grpc

MALFORMED_PAYLOAD = "malformed_payload"
INVALID_MESSAGE = "invalid_message"
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
    MALFORMED_PAYLOAD: False,
    INVALID_MESSAGE: False,
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

# Mirrors the Go grpcCode table: each stable Taskwire code maps onto the gRPC
# status code carrying the same meaning to a generic client. The Taskwire code
# stays authoritative and travels in the status details.
GRPC_CODE: Mapping[str, grpc.StatusCode] = {
    MALFORMED_PAYLOAD: grpc.StatusCode.INVALID_ARGUMENT,
    INVALID_MESSAGE: grpc.StatusCode.INVALID_ARGUMENT,
    NOT_REGISTERED: grpc.StatusCode.FAILED_PRECONDITION,
    ROLE_FORBIDDEN: grpc.StatusCode.PERMISSION_DENIED,
    OWNER_MISMATCH: grpc.StatusCode.PERMISSION_DENIED,
    TASK_CONFLICT: grpc.StatusCode.ABORTED,
    TASK_NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    TOO_LATE: grpc.StatusCode.FAILED_PRECONDITION,
    STALE_LEASE: grpc.StatusCode.ABORTED,
    UNKNOWN_LEASE: grpc.StatusCode.NOT_FOUND,
    UNKNOWN_TASK: grpc.StatusCode.NOT_FOUND,
    UNSUPPORTED_CODEC: grpc.StatusCode.INVALID_ARGUMENT,
    UNSUPPORTED_BACKEND: grpc.StatusCode.INVALID_ARGUMENT,
    TRANSFER_LIMIT: grpc.StatusCode.RESOURCE_EXHAUSTED,
    TRANSFER_TIMEOUT: grpc.StatusCode.DEADLINE_EXCEEDED,
    CHECKSUM_MISMATCH: grpc.StatusCode.DATA_LOSS,
    STORAGE_UNAVAILABLE: grpc.StatusCode.UNAVAILABLE,
    STORAGE_CONSISTENCY: grpc.StatusCode.DATA_LOSS,
    SHUTDOWN: grpc.StatusCode.UNAVAILABLE,
    INTERNAL: grpc.StatusCode.INTERNAL,
}


class ProtocolError(Exception):
    """Raised when a control message fails Taskwire's semantic rules.

    ``message`` may contain field names but must never contain payload values.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_RETRYABLE:
            raise ValueError(f"unregistered error code: {code!r}")
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    @property
    def retryable(self) -> bool:
        return ERROR_RETRYABLE[self.code]

    @property
    def grpc_code(self) -> grpc.StatusCode:
        return GRPC_CODE.get(self.code, grpc.StatusCode.UNKNOWN)


def error_from_rpc_error(error: grpc.RpcError) -> ProtocolError | None:
    """Recover the Taskwire error a gRPC status carries in its details.

    Returns ``None`` when the status has no Taskwire detail — for example a
    transport failure raised before the agent saw the request.
    """
    # Imported lazily: rpc_status pulls in the googleapis status proto, which
    # is only needed when an RPC actually fails.
    from google.rpc import status_pb2
    from grpc_status import rpc_status

    from taskwire.protocol.pb import control_pb2 as pb

    try:
        status: status_pb2.Status | None = rpc_status.from_call(error)
    except (ValueError, AttributeError):
        return None
    if status is None:
        return None

    for detail in status.details:
        if detail.Is(pb.Error.DESCRIPTOR):
            payload = pb.Error()
            detail.Unpack(payload)
            if payload.code not in ERROR_RETRYABLE:
                return None
            return ProtocolError(payload.code, payload.message)
    return None
