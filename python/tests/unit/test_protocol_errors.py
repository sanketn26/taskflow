"""The stable error registry and its gRPC status mapping."""

from __future__ import annotations

import grpc
import pytest

from taskwire.protocol.errors import (
    ERROR_RETRYABLE,
    GRPC_CODE,
    INVALID_MESSAGE,
    ROLE_FORBIDDEN,
    STORAGE_UNAVAILABLE,
    ProtocolError,
)


def test_every_registered_code_maps_to_a_grpc_code():
    for code in ERROR_RETRYABLE:
        assert code in GRPC_CODE, f"{code} has no gRPC status mapping"
        assert GRPC_CODE[code] is not grpc.StatusCode.UNKNOWN


def test_grpc_mapping_has_no_extra_codes():
    assert set(GRPC_CODE) == set(ERROR_RETRYABLE)


def test_unregistered_code_rejected():
    with pytest.raises(ValueError):
        ProtocolError("not_a_real_code", "boom")


def test_error_exposes_retryability_and_status_code():
    retryable = ProtocolError(STORAGE_UNAVAILABLE, "backend down")
    assert retryable.retryable is True
    assert retryable.grpc_code is grpc.StatusCode.UNAVAILABLE

    fatal = ProtocolError(ROLE_FORBIDDEN, "wrong role")
    assert fatal.retryable is False
    assert fatal.grpc_code is grpc.StatusCode.PERMISSION_DENIED


def test_error_message_carries_code_and_text():
    error = ProtocolError(INVALID_MESSAGE, "owner_id must be 16 bytes")
    assert error.code == INVALID_MESSAGE
    assert "owner_id must be 16 bytes" in str(error)
