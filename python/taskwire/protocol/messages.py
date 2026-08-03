"""Generated-Protobuf control messages and portable task-value MsgPack.

gRPC selects the message type per RPC, so this module carries only semantic
validation — the constraints Protobuf itself cannot express (16-byte IDs,
32-byte checksums, enumerated string fields, required oneof branches).
MsgPack is deliberately confined to ``encode_portable_value`` and
``decode_portable_value``; bytes stored in ``ValueRef.inline`` are opaque here.
"""

from __future__ import annotations

import math
from typing import Any

import msgpack
from google.protobuf.message import Message

from taskwire.protocol.errors import (
    INVALID_MESSAGE,
    MALFORMED_PAYLOAD,
    ProtocolError,
)
from taskwire.protocol.pb import control_pb2 as pb

# Public schema API is generated from control.proto.
ObjectRef = pb.ObjectRef
ValueRef = pb.ValueRef
PullRequest = pb.PullRequest
TaskCapability = pb.TaskCapability
TaskRegistration = pb.TaskRegistration
TaskQuery = pb.TaskQuery
TaskSnapshotEntry = pb.TaskSnapshotEntry
TaskSnapshot = pb.TaskSnapshot
TaskEnvelope = pb.TaskEnvelope
LeasedTask = pb.LeasedTask
Completion = pb.Completion
ForwardedTask = pb.ForwardedTask
ForwardedCompletion = pb.ForwardedCompletion
Failure = pb.Failure
ResultNotification = pb.ResultNotification
StatusSnapshot = pb.StatusSnapshot
Error = pb.Error
HeartbeatRequest = pb.HeartbeatRequest
CancelRequest = pb.CancelRequest
ObjectGetRequest = pb.ObjectGetRequest
ObjectChunk = pb.ObjectChunk
StealRequest = pb.StealRequest
StatusRequest = pb.StatusRequest
WorkerMessage = pb.WorkerMessage
AgentMessage = pb.AgentMessage
WorkerRegistration = pb.WorkerRegistration
WatchResultsRequest = pb.WatchResultsRequest
AckResultRequest = pb.AckResultRequest
SubmitResponse = pb.SubmitResponse


class _DuplicateKey(Exception):
    pass


def _pairs_hook(pairs: list[tuple[Any, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _unpack_portable(payload: bytes) -> Any:
    try:
        return msgpack.unpackb(
            payload, raw=False, strict_map_key=True, object_pairs_hook=_pairs_hook
        )
    except _DuplicateKey:
        raise ProtocolError(INVALID_MESSAGE, "duplicate portable map key") from None
    except msgpack.exceptions.ExtraData as exc:
        # Mirror Go DecodePortableValue: a complete extra value is invalid_message;
        # truncated garbage after the first value is malformed_payload.
        try:
            msgpack.unpackb(
                exc.extra,
                raw=False,
                strict_map_key=True,
                object_pairs_hook=_pairs_hook,
            )
        except _DuplicateKey:
            raise ProtocolError(INVALID_MESSAGE, "duplicate portable map key") from None
        except msgpack.exceptions.ExtraData:
            raise ProtocolError(
                INVALID_MESSAGE, "trailing portable value bytes"
            ) from None
        except (msgpack.exceptions.UnpackException, ValueError, TypeError) as trailing:
            raise ProtocolError(MALFORMED_PAYLOAD, str(trailing)) from None
        raise ProtocolError(INVALID_MESSAGE, "trailing portable value bytes") from None
    except (msgpack.exceptions.UnpackException, ValueError, TypeError) as exc:
        raise ProtocolError(MALFORMED_PAYLOAD, str(exc)) from None


def _portable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, bytes)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if -(1 << 63) <= value <= (1 << 64) - 1:
            return value
    elif isinstance(value, float) and math.isfinite(value):
        return value
    elif isinstance(value, list):
        return [_portable(item) for item in value]
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _portable(item) for key, item in value.items()}
    raise ProtocolError(INVALID_MESSAGE, "value is outside portable MsgPack profile")


def encode_portable_value(value: Any) -> bytes:
    """Encode one application value; never used for control messages."""
    return _pack_portable(_portable(value))


def _collection_header(length: int, *, mapping: bool) -> bytes:
    if length < 16:
        return bytes([(0x80 if mapping else 0x90) | length])
    marker = 0xDE if mapping else 0xDC
    if length <= 0xFFFF:
        return bytes([marker]) + length.to_bytes(2, "big")
    marker = 0xDF if mapping else 0xDD
    return bytes([marker]) + length.to_bytes(4, "big")


def _pack_portable(value: Any) -> bytes:
    if isinstance(value, list):
        return _collection_header(len(value), mapping=False) + b"".join(
            _pack_portable(item) for item in value
        )
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-8"))
        return _collection_header(len(items), mapping=True) + b"".join(
            msgpack.packb(key, use_bin_type=True) + _pack_portable(item)
            for key, item in items
        )
    if isinstance(value, float):
        return b"\xcb" + __import__("struct").pack(">d", value)
    return msgpack.packb(value, use_bin_type=True)


def decode_portable_value(payload: bytes) -> Any:
    """Decode one application value; never used for control messages."""
    return _portable(_unpack_portable(payload))


def _require_id(value: bytes, field: str) -> None:
    if len(value) != 16:
        raise ProtocolError(INVALID_MESSAGE, f"{field} must be 16 bytes")


def _unique_strings(values: Any, field: str) -> None:
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ProtocolError(
            INVALID_MESSAGE, f"{field} must contain unique non-empty strings"
        )


def validate(message: Message) -> None:
    """Enforce Taskwire's semantic constraints on a decoded message.

    gRPC has already guaranteed the message parses and matches the RPC's
    declared type, so this is purely about meaning. Raises ``ProtocolError``.
    """
    if isinstance(message, WorkerRegistration):
        if not all(
            (
                message.worker_id,
                message.runtime_version,
                message.sdk_version,
            )
        ):
            raise ProtocolError(
                INVALID_MESSAGE, "worker registration identity is incomplete"
            )
        if message.runtime not in {"python", "nodejs", "go"} or not message.codecs:
            raise ProtocolError(INVALID_MESSAGE, "invalid worker runtime/codecs")
        _unique_strings(message.codecs, "codecs")
        if not message.HasField("tasks"):
            raise ProtocolError(
                INVALID_MESSAGE, "worker registration requires a task set"
            )
        validate(message.tasks)
    elif isinstance(message, ObjectRef):
        if (
            not message.store
            or not message.key
            or not message.codec
            or len(message.sha256) != 32
        ):
            raise ProtocolError(INVALID_MESSAGE, "invalid ObjectRef")
    elif isinstance(message, ValueRef):
        branch = message.WhichOneof("location")
        if (
            branch is None
            or (branch == "inline" and not message.codec)
            or (branch == "object" and message.codec)
        ):
            raise ProtocolError(INVALID_MESSAGE, "invalid ValueRef union")
        if branch == "object":
            validate(message.object)
    elif isinstance(message, TaskRegistration):
        if not message.worker_id or message.generation == 0:
            raise ProtocolError(INVALID_MESSAGE, "invalid task registration")
        identities: set[tuple[str, str]] = set()
        for task in message.tasks:
            identity = (task.task_name, task.task_version)
            if (
                not all(identity)
                or identity in identities
                or task.invocation not in {"value", "python_args"}
                or not task.codecs
            ):
                raise ProtocolError(INVALID_MESSAGE, "invalid task capability")
            _unique_strings(task.codecs, "task codecs")
            identities.add(identity)
    elif isinstance(message, PullRequest):
        if not message.worker_id or message.capability_generation == 0:
            raise ProtocolError(INVALID_MESSAGE, "invalid pull request")
    elif isinstance(message, TaskQuery):
        _require_id(message.owner_id, "owner_id")
        if not message.task_ids:
            raise ProtocolError(INVALID_MESSAGE, "task_ids must not be empty")
        for task_id in message.task_ids:
            _require_id(task_id, "task_id")
    elif isinstance(message, TaskSnapshot):
        for task in message.tasks:
            validate(task)
    elif isinstance(message, TaskSnapshotEntry):
        _require_id(message.task_id, "task_id")
        branch = message.WhichOneof("outcome")
        if message.state in {"queued", "leased", "unknown"}:
            if message.HasField("cursor") or branch is not None:
                raise ProtocolError(
                    INVALID_MESSAGE, "nonterminal task snapshot has terminal fields"
                )
        elif message.state == "succeeded":
            if not message.HasField("cursor") or branch != "result":
                raise ProtocolError(
                    INVALID_MESSAGE, "succeeded snapshot requires result and cursor"
                )
            validate(message.result)
        elif message.state in {"failed", "cancelled", "dead_lettered"}:
            if not message.HasField("cursor") or branch != "failure":
                raise ProtocolError(
                    INVALID_MESSAGE, "failed snapshot requires failure and cursor"
                )
            validate(message.failure)
        else:
            raise ProtocolError(INVALID_MESSAGE, "invalid task snapshot state")
    elif isinstance(message, TaskEnvelope):
        _require_id(message.owner_id, "owner_id")
        if (
            not message.task_name
            or not message.task_version
            or message.invocation not in {"value", "python_args"}
            or not message.HasField("input")
        ):
            raise ProtocolError(INVALID_MESSAGE, "invalid task envelope")
        validate(message.input)
    elif isinstance(message, LeasedTask):
        _require_id(message.task_id, "task_id")
        _require_id(message.lease_id, "lease_id")
        if not message.HasField("task") or message.ttl_ms == 0 or message.attempt == 0:
            raise ProtocolError(INVALID_MESSAGE, "invalid leased task")
        validate(message.task)
    elif isinstance(message, Completion):
        _require_id(message.lease_id, "lease_id")
        branch = message.WhichOneof("outcome")
        if branch is None:
            raise ProtocolError(INVALID_MESSAGE, "outcome is required")
        validate(getattr(message, branch))
    elif isinstance(message, ForwardedTask):
        _require_id(message.transfer_id, "transfer_id")
        if not message.origin_node or not message.HasField("task"):
            raise ProtocolError(INVALID_MESSAGE, "invalid forwarded task")
        validate(message.task)
    elif isinstance(message, ForwardedCompletion):
        _require_id(message.transfer_id, "transfer_id")
        branch = message.WhichOneof("outcome")
        if not message.remote_node or message.remote_attempt == 0 or branch is None:
            raise ProtocolError(INVALID_MESSAGE, "invalid forwarded completion")
        validate(getattr(message, branch))
    elif isinstance(message, Failure):
        if not message.code or not message.message:
            raise ProtocolError(INVALID_MESSAGE, "invalid failure")
        if message.HasField("details"):
            validate(message.details)
    elif isinstance(message, ResultNotification):
        _require_id(message.owner_id, "owner_id")
        _require_id(message.task_id, "task_id")
        branch = message.WhichOneof("outcome")
        if message.state not in {"succeeded", "failed", "cancelled"} or branch is None:
            raise ProtocolError(INVALID_MESSAGE, "invalid result notification")
        if message.state == "succeeded" and branch != "result":
            raise ProtocolError(
                INVALID_MESSAGE, "succeeded notification requires result"
            )
        if message.state in {"failed", "cancelled"} and branch != "failure":
            raise ProtocolError(INVALID_MESSAGE, "failed notification requires failure")
        validate(getattr(message, branch))
    elif isinstance(message, HeartbeatRequest):
        _require_id(message.lease_id, "lease_id")
    elif isinstance(message, CancelRequest):
        _require_id(message.owner_id, "owner_id")
        _require_id(message.task_id, "task_id")
    elif isinstance(message, WatchResultsRequest):
        _require_id(message.owner_id, "owner_id")
    elif isinstance(message, AckResultRequest):
        _require_id(message.owner_id, "owner_id")
        _require_id(message.task_id, "task_id")
    elif isinstance(message, ObjectGetRequest):
        if not message.HasField("object"):
            raise ProtocolError(INVALID_MESSAGE, "object is required")
        validate(message.object)
    elif isinstance(message, ObjectChunk):
        if message.sha256 and len(message.sha256) != 32:
            raise ProtocolError(INVALID_MESSAGE, "sha256 must be 32 bytes")
    elif isinstance(message, StealRequest):
        if not message.requester_node or message.limit == 0:
            raise ProtocolError(INVALID_MESSAGE, "invalid steal request")
    elif isinstance(message, Error):
        if not message.code or not message.message:
            raise ProtocolError(INVALID_MESSAGE, "invalid protocol error")
