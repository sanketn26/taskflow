"""Generated-Protobuf control messages and portable task-value MsgPack.

Control-plane payloads are always ``taskwire.v1.ControlMessage`` protobufs.
MsgPack is deliberately confined to ``encode_portable_value`` and
``decode_portable_value``; bytes stored in ``ValueRef.inline`` are opaque here.
"""

from __future__ import annotations

import math
from typing import Any

import msgpack
from google.protobuf.message import DecodeError, Message

from taskwire.protocol.errors import (
    INVALID_MESSAGE,
    MALFORMED_PAYLOAD,
    ProtocolDecodeError,
)
from taskwire.protocol.frames import MessageType
from taskwire.protocol.pb import control_pb2 as pb

# Public schema API is generated from control.proto.
ObjectRef = pb.ObjectRef
ValueRef = pb.ValueRef
Hello = pb.Hello
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
Ack = pb.Ack
Error = pb.Error
HeartbeatRequest = pb.HeartbeatRequest
CancelRequest = pb.CancelRequest
ResumeResultsRequest = pb.ResumeResultsRequest
ObjectPutRequest = pb.ObjectPutRequest
ObjectGetRequest = pb.ObjectGetRequest
ObjectChunk = pb.ObjectChunk
StealRequest = pb.StealRequest
StatusRequest = pb.StatusRequest
ControlMessage = pb.ControlMessage


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
        raise ProtocolDecodeError(
            INVALID_MESSAGE, "duplicate portable map key"
        ) from None
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
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "duplicate portable map key"
            ) from None
        except msgpack.exceptions.ExtraData:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "trailing portable value bytes"
            ) from None
        except (msgpack.exceptions.UnpackException, ValueError, TypeError) as trailing:
            raise ProtocolDecodeError(MALFORMED_PAYLOAD, str(trailing)) from None
        raise ProtocolDecodeError(
            INVALID_MESSAGE, "trailing portable value bytes"
        ) from None
    except (msgpack.exceptions.UnpackException, ValueError, TypeError) as exc:
        raise ProtocolDecodeError(MALFORMED_PAYLOAD, str(exc)) from None


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
    raise ProtocolDecodeError(
        INVALID_MESSAGE, "value is outside portable MsgPack profile"
    )


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


_BRANCHES: dict[MessageType, tuple[tuple[type[Message], str], ...]] = {
    MessageType.SUBMIT: ((TaskEnvelope, "submit"), (ForwardedTask, "forwarded_submit")),
    MessageType.PULL: ((PullRequest, "pull"),),
    MessageType.TASK: ((LeasedTask, "task"),),
    MessageType.HEARTBEAT: ((HeartbeatRequest, "heartbeat"),),
    MessageType.RESULT: ((ResultNotification, "result"),),
    MessageType.CANCEL: ((CancelRequest, "cancel"),),
    MessageType.COMPLETE: (
        (Completion, "complete"),
        (ForwardedCompletion, "forwarded_complete"),
    ),
    MessageType.STEAL: ((StealRequest, "steal"),),
    MessageType.ACK: ((Ack, "ack"),),
    MessageType.STATUS: (
        (StatusRequest, "status_request"),
        (StatusSnapshot, "status_snapshot"),
    ),
    MessageType.RESUME_RESULTS: ((ResumeResultsRequest, "resume_results"),),
    MessageType.ERROR: ((Error, "error"),),
    MessageType.OBJECT_PUT: ((ObjectPutRequest, "object_put"),),
    MessageType.OBJECT_GET: ((ObjectGetRequest, "object_get"),),
    MessageType.OBJECT_CHUNK: ((ObjectChunk, "object_chunk"),),
    MessageType.HELLO: ((Hello, "hello"),),
    MessageType.TASK_QUERY: (
        (TaskQuery, "task_query"),
        (TaskSnapshot, "task_snapshot"),
    ),
    MessageType.REGISTER_TASKS: ((TaskRegistration, "register_tasks"),),
}


def _require_id(value: bytes, field: str) -> None:
    if len(value) != 16:
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field} must be 16 bytes")


def _unique_strings(values: Any, field: str) -> None:
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"{field} must contain unique non-empty strings"
        )


_ACK_KIND_FIELDS = {
    "hello": set(),
    "submit": {"task_id"},
    "forward": {"task_id", "transfer_id"},
    "heartbeat": {"lease_id"},
    "complete": {"lease_id"},
    "cancel": {"task_id", "cancelled"},
    "result": {"owner_id", "task_id", "cursor"},
    "empty_pull": set(),
    "object_put": {"transfer_id", "object"},
    "object_get": {"transfer_id"},
    "resume": {"owner_id", "next_cursor", "more"},
    "steal": {"transfer_id", "accepted"},
    "register_tasks": {"worker_id", "generation", "accepted"},
}


def _validate_ack(message: Ack) -> None:
    expected = _ACK_KIND_FIELDS.get(message.kind)
    if expected is None:
        raise ProtocolDecodeError(INVALID_MESSAGE, "unknown ACK kind")
    present = {
        name
        for name, is_present in {
            "task_id": bool(message.task_id),
            "transfer_id": bool(message.transfer_id),
            "lease_id": bool(message.lease_id),
            "owner_id": bool(message.owner_id),
            "cursor": message.HasField("cursor"),
            "cancelled": message.HasField("cancelled"),
            "object": message.HasField("object"),
            "next_cursor": message.HasField("next_cursor"),
            "more": message.HasField("more"),
            "accepted": message.HasField("accepted"),
            "worker_id": bool(message.worker_id),
            "generation": message.HasField("generation"),
        }.items()
        if is_present
    }
    if present != expected:
        raise ProtocolDecodeError(INVALID_MESSAGE, "ACK fields do not match kind")
    for field in expected & {"task_id", "transfer_id", "lease_id", "owner_id"}:
        _require_id(getattr(message, field), field)
    if "object" in expected:
        _validate(message.object)
    if message.kind == "register_tasks" and message.generation == 0:
        raise ProtocolDecodeError(INVALID_MESSAGE, "generation must be positive")


def _validate(message: Message) -> None:
    if isinstance(message, Hello):
        if message.role == "runtime":
            _require_id(message.owner_id, "owner_id")
            if any(
                (
                    message.worker_id,
                    message.runtime,
                    message.runtime_version,
                    message.sdk_version,
                    message.codecs,
                )
            ):
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "runtime HELLO contains worker fields"
                )
        elif message.role == "worker":
            if not all(
                (
                    message.worker_id,
                    message.runtime,
                    message.runtime_version,
                    message.sdk_version,
                )
            ):
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "worker HELLO identity is incomplete"
                )
            if message.runtime not in {"python", "nodejs", "go"} or not message.codecs:
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "invalid worker runtime/codecs"
                )
            if message.owner_id:
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "worker HELLO contains owner_id"
                )
            _unique_strings(message.codecs, "codecs")
        elif message.role == "admin":
            if any(
                (
                    message.owner_id,
                    message.worker_id,
                    message.runtime,
                    message.runtime_version,
                    message.sdk_version,
                    message.codecs,
                )
            ):
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "admin HELLO contains identity fields"
                )
        else:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid HELLO role")
    elif isinstance(message, ObjectRef):
        if (
            not message.store
            or not message.key
            or not message.codec
            or len(message.sha256) != 32
        ):
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid ObjectRef")
    elif isinstance(message, ValueRef):
        branch = message.WhichOneof("location")
        if (
            branch is None
            or (branch == "inline" and not message.codec)
            or (branch == "object" and message.codec)
        ):
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid ValueRef union")
        if branch == "object":
            _validate(message.object)
    elif isinstance(message, TaskRegistration):
        if not message.worker_id or message.generation == 0:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid task registration")
        identities: set[tuple[str, str]] = set()
        for task in message.tasks:
            identity = (task.task_name, task.task_version)
            if (
                not all(identity)
                or identity in identities
                or task.invocation not in {"value", "python_args"}
                or not task.codecs
            ):
                raise ProtocolDecodeError(INVALID_MESSAGE, "invalid task capability")
            _unique_strings(task.codecs, "task codecs")
            identities.add(identity)
    elif isinstance(message, PullRequest):
        if not message.worker_id or message.capability_generation == 0:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid pull request")
    elif isinstance(message, TaskQuery):
        _require_id(message.owner_id, "owner_id")
        if not message.task_ids:
            raise ProtocolDecodeError(INVALID_MESSAGE, "task_ids must not be empty")
        for task_id in message.task_ids:
            _require_id(task_id, "task_id")
    elif isinstance(message, TaskSnapshot):
        for task in message.tasks:
            _validate(task)
    elif isinstance(message, TaskSnapshotEntry):
        _require_id(message.task_id, "task_id")
        branch = message.WhichOneof("outcome")
        if message.state in {"queued", "leased", "unknown"}:
            if message.HasField("cursor") or branch is not None:
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "nonterminal task snapshot has terminal fields"
                )
        elif message.state == "succeeded":
            if not message.HasField("cursor") or branch != "result":
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "succeeded snapshot requires result and cursor"
                )
            _validate(message.result)
        elif message.state in {"failed", "cancelled", "dead_lettered"}:
            if not message.HasField("cursor") or branch != "failure":
                raise ProtocolDecodeError(
                    INVALID_MESSAGE, "failed snapshot requires failure and cursor"
                )
            _validate(message.failure)
        else:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid task snapshot state")
    elif isinstance(message, TaskEnvelope):
        _require_id(message.owner_id, "owner_id")
        if (
            not message.task_name
            or not message.task_version
            or message.invocation not in {"value", "python_args"}
            or not message.HasField("input")
        ):
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid task envelope")
        _validate(message.input)
    elif isinstance(message, LeasedTask):
        _require_id(message.lease_id, "lease_id")
        if not message.HasField("task") or message.ttl_ms == 0 or message.attempt == 0:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid leased task")
        _validate(message.task)
    elif isinstance(message, Completion):
        _require_id(message.lease_id, "lease_id")
        branch = message.WhichOneof("outcome")
        if branch is None:
            raise ProtocolDecodeError(INVALID_MESSAGE, "outcome is required")
        _validate(getattr(message, branch))
    elif isinstance(message, ForwardedTask):
        _require_id(message.transfer_id, "transfer_id")
        if not message.origin_node or not message.HasField("task"):
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid forwarded task")
        _validate(message.task)
    elif isinstance(message, ForwardedCompletion):
        _require_id(message.transfer_id, "transfer_id")
        branch = message.WhichOneof("outcome")
        if not message.remote_node or message.remote_attempt == 0 or branch is None:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid forwarded completion")
        _validate(getattr(message, branch))
    elif isinstance(message, Failure):
        if not message.code or not message.message:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid failure")
        if message.HasField("details"):
            _validate(message.details)
    elif isinstance(message, ResultNotification):
        _require_id(message.owner_id, "owner_id")
        branch = message.WhichOneof("outcome")
        if message.state not in {"succeeded", "failed", "cancelled"} or branch is None:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid result notification")
        if message.state == "succeeded" and branch != "result":
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "succeeded notification requires result"
            )
        if message.state in {"failed", "cancelled"} and branch != "failure":
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "failed notification requires failure"
            )
        _validate(getattr(message, branch))
    elif isinstance(message, HeartbeatRequest):
        _require_id(message.lease_id, "lease_id")
    elif isinstance(message, CancelRequest):
        _require_id(message.owner_id, "owner_id")
    elif isinstance(message, ResumeResultsRequest):
        _require_id(message.owner_id, "owner_id")
        if message.limit == 0:
            raise ProtocolDecodeError(INVALID_MESSAGE, "resume limit must be positive")
    elif isinstance(message, ObjectPutRequest):
        _require_id(message.transfer_id, "transfer_id")
        if not message.codec or len(message.sha256) != 32:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid object put")
    elif isinstance(message, ObjectGetRequest):
        _require_id(message.transfer_id, "transfer_id")
        if not message.HasField("object"):
            raise ProtocolDecodeError(INVALID_MESSAGE, "object is required")
        _validate(message.object)
    elif isinstance(message, ObjectChunk):
        _require_id(message.transfer_id, "transfer_id")
    elif isinstance(message, StealRequest):
        if not message.requester_node or message.limit == 0:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid steal request")
    elif isinstance(message, Ack):
        _validate_ack(message)
    elif isinstance(message, Error):
        if not message.code or not message.message:
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid protocol error")


def encode_payload(message_type: MessageType, value: Message) -> bytes:
    """Validate and serialize a generated message in a ControlMessage envelope."""
    for expected_type, branch in _BRANCHES[message_type]:
        if isinstance(value, expected_type):
            _validate(value)
            envelope = ControlMessage()
            getattr(envelope, branch).CopyFrom(value)
            return envelope.SerializeToString(deterministic=True)
    raise ProtocolDecodeError(
        INVALID_MESSAGE, f"{type(value).__name__} is not valid for {message_type.name}"
    )


def decode_payload(message_type: MessageType, payload: bytes) -> Message:
    """Parse a ControlMessage and verify its body agrees with the frame type."""
    envelope = ControlMessage()
    try:
        envelope.ParseFromString(payload)
    except DecodeError as exc:
        raise ProtocolDecodeError(MALFORMED_PAYLOAD, str(exc)) from None
    branch = envelope.WhichOneof("body")
    for _, expected_branch in _BRANCHES[message_type]:
        if branch == expected_branch:
            value = getattr(envelope, branch)
            _validate(value)
            return value
    raise ProtocolDecodeError(
        INVALID_MESSAGE, "protobuf body does not match frame type"
    )


def make_ack(kind: str, **fields: Any) -> Ack:
    ack = Ack(kind=kind, **fields)
    _validate_ack(ack)
    return ack
