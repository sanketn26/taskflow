from __future__ import annotations

import math

import pytest
from google.protobuf.internal.encoder import _VarintBytes

from taskwire.protocol import messages as m
from taskwire.protocol.errors import ProtocolDecodeError
from taskwire.protocol.frames import MessageType


def test_control_payload_is_protobuf_envelope():
    payload = m.encode_payload(MessageType.STATUS, m.StatusRequest())
    envelope = m.ControlMessage.FromString(payload)
    assert envelope.WhichOneof("body") == "status_request"


def test_generated_message_round_trip():
    hello = m.Hello(
        role="worker",
        worker_id="worker-1",
        runtime="nodejs",
        runtime_version="22",
        sdk_version="0.1.0",
        codecs=["msgpack", "bytes"],
    )
    payload = m.encode_payload(MessageType.HELLO, hello)
    assert m.decode_payload(MessageType.HELLO, payload) == hello


def test_frame_type_and_protobuf_body_must_agree():
    payload = m.encode_payload(MessageType.HELLO, m.Hello(role="admin"))
    with pytest.raises(ProtocolDecodeError, match="does not match") as exc:
        m.decode_payload(MessageType.STATUS, payload)
    assert exc.value.code == "invalid_message"


def test_malformed_protobuf_is_rejected():
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HELLO, b"\x9a\xff")
    assert exc.value.code == "malformed_payload"


def test_unknown_nested_fields_are_accepted_and_preserved():
    hello = m.Hello(role="admin")
    raw = hello.SerializeToString() + _VarintBytes((100 << 3) | 0) + _VarintBytes(42)
    hello_with_unknown = m.Hello.FromString(raw)
    payload = m.encode_payload(MessageType.HELLO, hello_with_unknown)
    decoded = m.decode_payload(MessageType.HELLO, payload)
    assert decoded.SerializeToString().endswith(_VarintBytes((100 << 3) | 0) + b"*")


def test_runtime_owner_id_is_semantically_validated():
    with pytest.raises(ProtocolDecodeError) as exc:
        m.encode_payload(MessageType.HELLO, m.Hello(role="runtime", owner_id=b"short"))
    assert exc.value.code == "invalid_message"


def test_registration_identity_and_generation_are_validated():
    registration = m.TaskRegistration(
        worker_id="w",
        generation=1,
        tasks=[
            m.TaskCapability(
                task_name="task",
                task_version="1",
                invocation="value",
                codecs=["msgpack"],
            )
        ],
    )
    assert (
        m.decode_payload(
            MessageType.REGISTER_TASKS,
            m.encode_payload(MessageType.REGISTER_TASKS, registration),
        )
        == registration
    )


def test_value_ref_uses_protobuf_oneof():
    ref = m.ValueRef(inline=b"value", codec="msgpack")
    assert ref.WhichOneof("location") == "inline"
    m._validate(ref)
    with pytest.raises(ProtocolDecodeError):
        m._validate(m.ValueRef(inline=b"value"))


def test_completion_requires_outcome_oneof():
    with pytest.raises(ProtocolDecodeError):
        m.encode_payload(MessageType.COMPLETE, m.Completion(lease_id=b"0" * 16))


def test_wrong_generated_type_is_rejected():
    with pytest.raises(ProtocolDecodeError) as exc:
        m.encode_payload(MessageType.HEARTBEAT, m.PullRequest(worker_id="w"))
    assert exc.value.code == "invalid_message"


def test_portable_msgpack_is_separate_and_canonical():
    value = {"z": [None, True, -33, 2**64 - 1, 1.5], "a": b"x"}
    encoded = m.encode_portable_value(value)
    assert encoded[1:3] == b"\xa1a"
    assert m.decode_portable_value(encoded) == value


@pytest.mark.parametrize("value", [math.nan, math.inf, {1: "bad"}, 2**64, object()])
def test_portable_msgpack_rejects_nonportable_values(value):
    with pytest.raises(ProtocolDecodeError):
        m.encode_portable_value(value)


def test_typed_ack_fields_are_required_and_preserved():
    task_id = b"1" * 16
    ack = m.make_ack("submit", task_id=task_id)
    assert ack.task_id == task_id
    with pytest.raises(ProtocolDecodeError):
        m.make_ack("submit")
    with pytest.raises(ProtocolDecodeError):
        m.make_ack("unknown")


def test_nested_object_and_message_ids_are_validated():
    envelope = m.TaskEnvelope(
        owner_id=b"1" * 16,
        task_name="task",
        task_version="1",
        invocation="value",
        input=m.ValueRef(
            object=m.ObjectRef(store="s", key="k", codec="bytes", sha256=b"short")
        ),
    )
    with pytest.raises(ProtocolDecodeError):
        m.encode_payload(MessageType.SUBMIT, envelope)

    completion = m.Completion(
        result=m.ObjectRef(store="s", key="k", codec="bytes", sha256=b"0" * 32)
    )
    with pytest.raises(ProtocolDecodeError):
        m.encode_payload(MessageType.COMPLETE, completion)


def test_hello_role_fields_are_exclusive_and_codecs_unique():
    with pytest.raises(ProtocolDecodeError):
        m.encode_payload(
            MessageType.HELLO,
            m.Hello(role="runtime", owner_id=b"1" * 16, worker_id="unexpected"),
        )
    with pytest.raises(ProtocolDecodeError):
        m.encode_payload(
            MessageType.HELLO,
            m.Hello(
                role="worker",
                worker_id="w",
                runtime="go",
                runtime_version="1",
                sdk_version="1",
                codecs=["msgpack", "msgpack"],
            ),
        )


def test_portable_duplicate_key_error_matches_go():
    duplicate = bytes([0x82, 0xA1, ord("a"), 1, 0xA1, ord("a"), 2])
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_portable_value(duplicate)
    assert exc.value.code == "invalid_message"


def test_portable_trailing_byte_classification_matches_go():
    """Align with Go TestPortableDuplicateKeyAndTrailingClassification."""
    with pytest.raises(ProtocolDecodeError) as full:
        m.decode_portable_value(bytes([0x01, 0x02]))
    assert full.value.code == "invalid_message"

    with pytest.raises(ProtocolDecodeError) as truncated:
        m.decode_portable_value(bytes([0x01, 0xD9]))
    assert truncated.value.code == "malformed_payload"
