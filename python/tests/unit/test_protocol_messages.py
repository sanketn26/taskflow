from __future__ import annotations

import math

import pytest
from google.protobuf.internal.encoder import _VarintBytes

from taskwire.protocol import messages as m
from taskwire.protocol.errors import ProtocolError


def test_generated_schema_round_trips():
    registration = m.WorkerRegistration(
        worker_id="worker-1",
        runtime="python",
        runtime_version="3.12",
        sdk_version="0.1.0",
        codecs=["msgpack", "bytes"],
        tasks=m.TaskRegistration(worker_id="worker-1", generation=1),
    )
    decoded = m.WorkerRegistration.FromString(registration.SerializeToString())
    assert decoded == registration
    m.validate(decoded)


def test_unknown_protobuf_fields_are_forward_compatible():
    request = m.StatusRequest()
    raw = request.SerializeToString() + _VarintBytes((100 << 3) | 0) + b"*"
    decoded = m.StatusRequest.FromString(raw)
    assert decoded.SerializeToString().endswith(_VarintBytes((100 << 3) | 0) + b"*")


def test_worker_registration_identity_is_validated():
    with pytest.raises(ProtocolError) as exc:
        m.validate(
            m.WorkerRegistration(
                worker_id="w",
                runtime="ruby",
                runtime_version="1",
                sdk_version="1",
                codecs=["msgpack"],
                tasks=m.TaskRegistration(worker_id="w", generation=1),
            )
        )
    assert exc.value.code == "invalid_message"


def test_worker_registration_codecs_must_be_unique():
    with pytest.raises(ProtocolError):
        m.validate(
            m.WorkerRegistration(
                worker_id="w",
                runtime="go",
                runtime_version="1",
                sdk_version="1",
                codecs=["msgpack", "msgpack"],
                tasks=m.TaskRegistration(worker_id="w", generation=1),
            )
        )


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
    m.validate(registration)

    with pytest.raises(ProtocolError):
        m.validate(m.TaskRegistration(worker_id="w", generation=0))


def test_value_ref_uses_protobuf_oneof():
    ref = m.ValueRef(inline=b"value", codec="msgpack")
    assert ref.WhichOneof("location") == "inline"
    m.validate(ref)
    with pytest.raises(ProtocolError):
        m.validate(m.ValueRef(inline=b"value"))


def test_completion_requires_outcome_oneof():
    with pytest.raises(ProtocolError):
        m.validate(m.Completion(lease_id=b"0" * 16))


def test_task_query_owner_id_is_semantically_validated():
    with pytest.raises(ProtocolError) as exc:
        m.validate(m.TaskQuery(owner_id=b"short", task_ids=[b"1" * 16]))
    assert exc.value.code == "invalid_message"


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
    with pytest.raises(ProtocolError):
        m.validate(envelope)

    completion = m.Completion(
        result=m.ObjectRef(store="s", key="k", codec="bytes", sha256=b"0" * 32)
    )
    with pytest.raises(ProtocolError):
        m.validate(completion)


def test_result_notification_state_must_match_outcome():
    with pytest.raises(ProtocolError):
        m.validate(
            m.ResultNotification(
                owner_id=b"1" * 16,
                task_id=b"2" * 16,
                cursor=1,
                state="succeeded",
                failure=m.Failure(code="task_exception", message="boom"),
            )
        )

    m.validate(
        m.ResultNotification(
            owner_id=b"1" * 16,
            task_id=b"2" * 16,
            cursor=1,
            state="succeeded",
            result=m.ObjectRef(store="s", key="k", codec="bytes", sha256=b"0" * 32),
        )
    )


def test_portable_msgpack_is_separate_and_canonical():
    value = {"z": [None, True, -33, 2**64 - 1, 1.5], "a": b"x"}
    encoded = m.encode_portable_value(value)
    assert encoded[1:3] == b"\xa1a"
    assert m.decode_portable_value(encoded) == value


@pytest.mark.parametrize("value", [math.nan, math.inf, {1: "bad"}, 2**64, object()])
def test_portable_msgpack_rejects_nonportable_values(value):
    with pytest.raises(ProtocolError):
        m.encode_portable_value(value)


def test_portable_duplicate_key_error_matches_go():
    duplicate = bytes([0x82, 0xA1, ord("a"), 1, 0xA1, ord("a"), 2])
    with pytest.raises(ProtocolError) as exc:
        m.decode_portable_value(duplicate)
    assert exc.value.code == "invalid_message"


def test_portable_trailing_byte_classification_matches_go():
    """Align with Go TestPortableDuplicateKeyAndTrailingClassification."""
    with pytest.raises(ProtocolError) as full:
        m.decode_portable_value(bytes([0x01, 0x02]))
    assert full.value.code == "invalid_message"

    with pytest.raises(ProtocolError) as truncated:
        m.decode_portable_value(bytes([0x01, 0xD9]))
    assert truncated.value.code == "malformed_payload"
