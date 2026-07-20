#!/usr/bin/env python3
"""Generates testdata/protocol/v1/manifest.yaml and testdata/protocol/v1/invalid/*
from the Python protocol package. Python and Go tests both read these files
read-only; neither language generates committed expected bytes for the other.

Deterministic (sha256-derived) IDs so re-running produces byte-identical
output.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python"))

import yaml  # noqa: E402

from taskwire.protocol import messages as m  # noqa: E402
from taskwire.protocol.frames import Flag, Frame, MessageType, encode_frame  # noqa: E402

MAX_PAYLOAD = 16 * 1024 * 1024


def fixed_id(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()[:16]


def fixed_bytes(label: str, n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(f"{label}:{i}".encode()).digest()
        i += 1
    return out[:n]


ZERO_ID = b"\x00" * 16

CASES: list[dict] = []


def add_case(
    name: str,
    message_type: MessageType,
    value,
    *,
    task_id: bytes = ZERO_ID,
    request_id: int = 1,
    flags: Flag = Flag.NONE,
):
    payload = m.encode_payload(message_type, value)
    frame = Frame(
        version=1,
        message_type=message_type,
        task_id=task_id,
        request_id=request_id,
        flags=flags,
        payload=payload,
    )
    frame_bytes = encode_frame(frame, max_payload_bytes=MAX_PAYLOAD)
    CASES.append(
        {
            "name": name,
            "message_type": message_type.name,
            "task_id_hex": task_id.hex(),
            "request_id": request_id,
            "flags": [f.name.lower() for f in Flag if f != Flag.NONE and f & flags],
            "payload_hex": payload.hex(),
            "frame_hex": frame_bytes.hex(),
        }
    )


owner_id = fixed_id("owner")
task_id = fixed_id("task")
worker_id_str = "worker-01"
lease_id = fixed_id("lease")
transfer_id = fixed_id("transfer")

# -- HELLO --------------------------------------------------------------
add_case("hello_runtime", MessageType.HELLO, m.Hello(role="runtime", owner_id=owner_id))
add_case(
    "hello_worker",
    MessageType.HELLO,
    m.Hello(
        role="worker",
        worker_id=worker_id_str,
        runtime="python",
        runtime_version="3.13",
        sdk_version="0.1.0",
        codecs=["msgpack", "bytes", "cloudpickle"],
    ),
)
add_case("hello_admin", MessageType.HELLO, m.Hello(role="admin"))

# -- ACK (all 12 kinds) ---------------------------------------------------
add_case("ack_hello", MessageType.ACK, m.make_ack("hello"))
add_case("ack_submit", MessageType.ACK, m.make_ack("submit", task_id=task_id))
add_case(
    "ack_forward",
    MessageType.ACK,
    m.make_ack("forward", task_id=task_id, transfer_id=transfer_id),
)
add_case("ack_heartbeat", MessageType.ACK, m.make_ack("heartbeat", lease_id=lease_id))
add_case("ack_complete", MessageType.ACK, m.make_ack("complete", lease_id=lease_id))
add_case(
    "ack_cancel", MessageType.ACK, m.make_ack("cancel", task_id=task_id, cancelled=True)
)
add_case(
    "ack_result",
    MessageType.ACK,
    m.make_ack("result", owner_id=owner_id, task_id=task_id, cursor=0),
)
add_case("ack_empty_pull", MessageType.ACK, m.make_ack("empty_pull"))
obj_ref = m.ObjectRef(
    store="local", key="k/1", size=0, sha256=fixed_bytes("sha", 32), codec="msgpack"
)
add_case(
    "ack_object_put",
    MessageType.ACK,
    m.make_ack("object_put", transfer_id=transfer_id, object=obj_ref),
)
add_case(
    "ack_object_get", MessageType.ACK, m.make_ack("object_get", transfer_id=transfer_id)
)
add_case(
    "ack_resume",
    MessageType.ACK,
    m.make_ack(
        "resume", owner_id=owner_id, next_cursor=18446744073709551615, more=False
    ),
)
add_case(
    "ack_steal",
    MessageType.ACK,
    m.make_ack("steal", transfer_id=transfer_id, accepted=4294967295),
)
add_case(
    "ack_register_tasks",
    MessageType.ACK,
    m.make_ack("register_tasks", worker_id=worker_id_str, generation=1, accepted=1),
)

# -- SUBMIT (TaskEnvelope / ForwardedTask) --------------------------------
inline_args = m.ValueRef.from_inline(b"\x91\x01", "msgpack")
env_min = m.TaskEnvelope(
    owner_id=owner_id,
    task_name="add",
    task_version="1",
    invocation="value",
    input=inline_args,
    labels={},
    idempotent=False,
    submitted_at_unix_ms=-9223372036854775808,
)
add_case("submit_task_envelope_inline", MessageType.SUBMIT, env_min, task_id=task_id)

object_args = m.ValueRef.from_object(obj_ref)
env_unicode = m.TaskEnvelope(
    owner_id=owner_id,
    task_name="任务.处理",  # "task.process" in Chinese
    task_version="2.0",
    invocation="value",
    input=object_args,
    labels={"team": "infra", "env": "prod"},
    idempotent=True,
    submitted_at_unix_ms=9223372036854775807,
)
add_case(
    "submit_task_envelope_unicode",
    MessageType.SUBMIT,
    env_unicode,
    task_id=task_id,
    flags=Flag.IDEMPOTENT,
)

forwarded_task = m.ForwardedTask(
    transfer_id=transfer_id, origin_node="node-a", task=env_min
)
add_case(
    "submit_forwarded_task",
    MessageType.SUBMIT,
    forwarded_task,
    task_id=task_id,
    flags=Flag.FORWARDED,
)

# -- PULL / TASK / HEARTBEAT ----------------------------------------------
add_case(
    "pull_request",
    MessageType.PULL,
    m.PullRequest(worker_id=worker_id_str, capability_generation=1),
)
add_case(
    "pull_request_max_generation",
    MessageType.PULL,
    m.PullRequest(worker_id=worker_id_str, capability_generation=18446744073709551615),
)

registration = m.TaskRegistration(
    worker_id=worker_id_str,
    generation=1,
    tasks=[
        m.TaskCapability(
            task_name="add", task_version="1", invocation="value", codecs=["msgpack"]
        )
    ],
)
add_case("register_tasks", MessageType.REGISTER_TASKS, registration)

leased = m.LeasedTask(task=env_min, lease_id=lease_id, ttl_ms=4294967295, attempt=0)
add_case("task_leased", MessageType.TASK, leased, task_id=task_id)

add_case(
    "heartbeat_request", MessageType.HEARTBEAT, m.HeartbeatRequest(lease_id=lease_id)
)

# -- RESULT -----------------------------------------------------------------
result_succeeded = m.ResultNotification(
    owner_id=owner_id, cursor=1, state="succeeded", result=obj_ref, failure=None
)
add_case(
    "result_notification_succeeded",
    MessageType.RESULT,
    result_succeeded,
    task_id=task_id,
    request_id=0,
)

failure = m.Failure(
    code="task_exception", message="boom", details=None, retryable=False
)
result_failed = m.ResultNotification(
    owner_id=owner_id, cursor=2, state="failed", result=None, failure=failure
)
add_case(
    "result_notification_failed",
    MessageType.RESULT,
    result_failed,
    task_id=task_id,
    request_id=0,
)

# -- CANCEL -----------------------------------------------------------------
add_case(
    "cancel_request",
    MessageType.CANCEL,
    m.CancelRequest(owner_id=owner_id),
    task_id=task_id,
)

# -- COMPLETE (Completion / ForwardedCompletion) -----------------------------
completion_result = m.Completion(lease_id=lease_id, result=obj_ref, failure=None)
add_case("complete_result", MessageType.COMPLETE, completion_result, task_id=task_id)

completion_failure = m.Completion(lease_id=lease_id, result=None, failure=failure)
add_case("complete_failure", MessageType.COMPLETE, completion_failure, task_id=task_id)

fwd_completion = m.ForwardedCompletion(
    transfer_id=transfer_id,
    remote_node="node-b",
    remote_attempt=1,
    result=obj_ref,
    failure=None,
)
add_case(
    "complete_forwarded",
    MessageType.COMPLETE,
    fwd_completion,
    task_id=task_id,
    flags=Flag.FORWARDED,
)

# -- STEAL --------------------------------------------------------------
add_case(
    "steal_request",
    MessageType.STEAL,
    m.StealRequest(requester_node="node-c", labels={"workload": "gpu"}, limit=10),
)

# -- STATUS ------------------------------------------------------------------
add_case("status_request", MessageType.STATUS, m.StatusRequest())

snapshot = m.StatusSnapshot(
    version="0.1.0",
    pid=18446744073709551615,
    ready=True,
    task_counts={"queued": 3, "leased": 1},
    active_leases=1,
    worker_pids=[101, 102],
    worker_restarts=0,
    storage_healthy=True,
    cluster_members=1,
    kafka_outbox_pending=0,
    last_error_code=None,
)
add_case("status_snapshot", MessageType.STATUS, snapshot)

snapshot_with_error = m.StatusSnapshot(
    version="0.1.0",
    pid=1,
    ready=False,
    task_counts={},
    active_leases=0,
    worker_pids=[],
    worker_restarts=5,
    storage_healthy=False,
    cluster_members=0,
    kafka_outbox_pending=7,
    last_error_code="storage_unavailable",
)
add_case("status_snapshot_with_error", MessageType.STATUS, snapshot_with_error)

# -- RESUME_RESULTS -----------------------------------------------------------
add_case(
    "resume_results_request",
    MessageType.RESUME_RESULTS,
    m.ResumeResultsRequest(owner_id=owner_id, after_cursor=0, limit=100),
)

# -- ERROR --------------------------------------------------------------
error_payload = m.Error(
    code="task_not_found",
    message="no such task",
    retryable=False,
    details={"task_id": task_id.hex()},
)
add_case(
    "error_frame", MessageType.ERROR, error_payload, task_id=task_id, flags=Flag.ERROR
)

# -- OBJECT_PUT / OBJECT_GET / OBJECT_CHUNK -----------------------------------
add_case(
    "object_put_request",
    MessageType.OBJECT_PUT,
    m.ObjectPutRequest(
        transfer_id=transfer_id,
        codec="bytes",
        size=1024,
        sha256=fixed_bytes("sha2", 32),
    ),
)
add_case(
    "object_get_request",
    MessageType.OBJECT_GET,
    m.ObjectGetRequest(transfer_id=transfer_id, object=obj_ref),
)
add_case(
    "object_chunk_eof",
    MessageType.OBJECT_CHUNK,
    m.ObjectChunk(transfer_id=transfer_id, sequence=0, data=b"", eof=True),
)
add_case(
    "object_chunk_data",
    MessageType.OBJECT_CHUNK,
    m.ObjectChunk(transfer_id=transfer_id, sequence=1, data=b"\x01\x02\x03", eof=False),
)

# -- TASK_QUERY (TaskQuery / TaskSnapshot) ------------------------------------
add_case(
    "task_query",
    MessageType.TASK_QUERY,
    m.TaskQuery(owner_id=owner_id, task_ids=[task_id, fixed_id("task2")]),
)

entries = [
    m.TaskSnapshotEntry(
        task_id=task_id, state="succeeded", cursor=1, result=obj_ref, failure=None
    ),
    m.TaskSnapshotEntry(
        task_id=fixed_id("task2"),
        state="failed",
        cursor=None,
        result=None,
        failure=failure,
    ),
    m.TaskSnapshotEntry(
        task_id=fixed_id("task3"),
        state="unknown",
        cursor=None,
        result=None,
        failure=None,
    ),
]
add_case("task_snapshot", MessageType.TASK_QUERY, m.TaskSnapshot(tasks=entries))
add_case("task_snapshot_empty", MessageType.TASK_QUERY, m.TaskSnapshot(tasks=[]))

manifest_path = REPO_ROOT / "testdata" / "protocol" / "v1" / "manifest.yaml"
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(yaml.safe_dump({"cases": CASES}, sort_keys=False))
print(f"wrote {len(CASES)} cases to {manifest_path}")

# --------------------------------------------------------------------
# Invalid corpus
# --------------------------------------------------------------------

invalid_dir = REPO_ROOT / "testdata" / "protocol" / "v1" / "invalid"
invalid_dir.mkdir(parents=True, exist_ok=True)


def write_invalid(
    name: str, data: bytes, expected_code: str, max_payload_bytes: int = MAX_PAYLOAD
):
    (invalid_dir / f"{name}.bin").write_bytes(data)
    (invalid_dir / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {
                "expected_error_code": expected_code,
                "max_payload_bytes": max_payload_bytes,
            }
        )
    )


# Header claiming payload length 0xffffffff with a 16 MiB configured limit:
# frame_too_large must be returned after exactly 31 bytes are read, no
# payload allocation attempted.
oversize_header = (
    bytes([1, MessageType.PULL.value])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0])
    + (0xFFFFFFFF).to_bytes(4, "big")
)
write_invalid("oversize_payload_length", oversize_header, "frame_too_large")

# Truncated header: fewer than 31 bytes total.
write_invalid("truncated_header", oversize_header[:10], "malformed_payload")

# Unknown message type byte.
unknown_type = (
    bytes([1, 0xFE])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0])
    + (0).to_bytes(4, "big")
)
write_invalid("unknown_message_type", unknown_type, "unknown_message_type")

# Unsupported protocol version.
bad_version = (
    bytes([2, MessageType.PULL.value])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0])
    + (0).to_bytes(4, "big")
)
write_invalid("unsupported_version", bad_version, "unsupported_version")

# Unknown flag bit (0x08) on an otherwise-valid PULL frame.
unknown_flags = (
    bytes([1, MessageType.PULL.value])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0x08])
    + (0).to_bytes(4, "big")
)
write_invalid("unknown_flag_bits", unknown_flags, "unknown_flags")

# ERROR frame missing the required error flag.
error_no_flag = (
    bytes([1, MessageType.ERROR.value])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0])
    + (0).to_bytes(4, "big")
)
write_invalid("error_flag_missing", error_no_flag, "unknown_flags")

# forwarded flag on a message type that doesn't allow it.
forwarded_disallowed = (
    bytes([1, MessageType.PULL.value])
    + ZERO_ID
    + (0).to_bytes(8, "big")
    + bytes([0x04])
    + (0).to_bytes(4, "big")
)
write_invalid("forwarded_flag_disallowed", forwarded_disallowed, "unknown_flags")

# Malformed msgpack payload (truncated string header claims more bytes than present).
malformed_payload_bytes = b"\xa5abc"  # fixstr claiming length 5, only 3 bytes follow
malformed_frame = (
    bytes([1, MessageType.STATUS.value])
    + ZERO_ID
    + (1).to_bytes(8, "big")
    + bytes([0])
    + len(malformed_payload_bytes).to_bytes(4, "big")
    + malformed_payload_bytes
)
write_invalid("malformed_msgpack_payload", malformed_frame, "malformed_payload")

# Duplicate key in payload map: {"lease_id": <16 bytes>, "lease_id": <16 bytes>}
dup_key_payload = (
    bytes([0x82])
    + m._pack_str("lease_id")
    + m._pack_bin(lease_id)
    + m._pack_str("lease_id")
    + m._pack_bin(lease_id)
)
dup_key_frame = (
    bytes([1, MessageType.HEARTBEAT.value])
    + ZERO_ID
    + (1).to_bytes(8, "big")
    + bytes([0])
    + len(dup_key_payload).to_bytes(4, "big")
    + dup_key_payload
)
write_invalid("duplicate_key_payload", dup_key_frame, "invalid_message")

# Unknown field in an otherwise-valid HEARTBEAT payload.
unknown_field_payload = (
    bytes([0x82])
    + m._pack_str("lease_id")
    + m._pack_bin(lease_id)
    + m._pack_str("extra")
    + m._pack_nil()
)
unknown_field_frame = (
    bytes([1, MessageType.HEARTBEAT.value])
    + ZERO_ID
    + (1).to_bytes(8, "big")
    + bytes([0])
    + len(unknown_field_payload).to_bytes(4, "big")
    + unknown_field_payload
)
write_invalid("unknown_field_payload", unknown_field_frame, "invalid_message")

# Missing required field in HEARTBEAT payload (empty map).
missing_field_payload = bytes([0x80])
missing_field_frame = (
    bytes([1, MessageType.HEARTBEAT.value])
    + ZERO_ID
    + (1).to_bytes(8, "big")
    + bytes([0])
    + len(missing_field_payload).to_bytes(4, "big")
    + missing_field_payload
)
write_invalid("missing_required_key", missing_field_frame, "invalid_message")

print(f"wrote invalid cases to {invalid_dir}")
