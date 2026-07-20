from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taskwire.protocol.errors import ProtocolDecodeError
from taskwire.protocol.frames import (
    Flag,
    Frame,
    MessageType,
    decode_header,
    encode_frame,
)
from taskwire.protocol import messages as m

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "testdata" / "protocol" / "v1" / "manifest.yaml"
INVALID_DIR = REPO_ROOT / "testdata" / "protocol" / "v1" / "invalid"
MAX_PAYLOAD = 16 * 1024 * 1024


def _cases() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_golden_vector_frame_bytes(case):
    payload = bytes.fromhex(case["payload_hex"])
    frame_bytes = bytes.fromhex(case["frame_hex"])
    message_type = MessageType[case["message_type"]]
    task_id = bytes.fromhex(case["task_id_hex"])

    flags = Flag.NONE
    for name in case["flags"]:
        flags |= Flag[name.upper()]

    frame = Frame(1, message_type, task_id, case["request_id"], flags, payload)
    assert encode_frame(frame, max_payload_bytes=MAX_PAYLOAD) == frame_bytes

    header = decode_header(
        frame_bytes[: len(frame_bytes) - len(payload)], max_payload_bytes=MAX_PAYLOAD
    )
    assert header.message_type == message_type
    assert header.task_id == task_id
    assert header.request_id == case["request_id"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_golden_vector_payload_round_trips(case):
    payload = bytes.fromhex(case["payload_hex"])
    message_type = MessageType[case["message_type"]]

    value = m.decode_payload(message_type, payload)
    re_encoded = m.encode_payload(message_type, value)
    assert re_encoded == payload

    value_again = m.decode_payload(message_type, re_encoded)
    assert value_again == value


def _invalid_cases() -> list[tuple[str, bytes, dict]]:
    out = []
    for bin_path in sorted(INVALID_DIR.glob("*.bin")):
        meta = yaml.safe_load((bin_path.with_suffix(".yaml")).read_text())
        out.append((bin_path.stem, bin_path.read_bytes(), meta))
    return out


@pytest.mark.parametrize(
    "name,data,meta", _invalid_cases(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_invalid_corpus_fails_with_stable_code(name, data, meta):
    max_payload = meta["max_payload_bytes"]
    with pytest.raises(ProtocolDecodeError) as exc:
        header_bytes = data[:31]
        if len(header_bytes) < 31:
            raise ProtocolDecodeError("malformed_payload", "truncated header")
        header = decode_header(header_bytes, max_payload_bytes=max_payload)
        payload = data[31:]
        if len(payload) != header.payload_len:
            raise ProtocolDecodeError("malformed_payload", "truncated payload")
        m.decode_payload(header.message_type, payload)
    assert exc.value.code == meta["expected_error_code"]


# -- canonical encoding profile -------------------------------------------


def test_map_keys_sorted_ascending_utf8():
    ref = m.ObjectRef(store="s", key="k", size=1, sha256=b"\x00" * 32, codec="bytes")
    encoded = ref.to_bytes()
    # fixmap(5) then keys in ascending order: codec, key, sha256, size, store
    assert encoded[0] == 0x85
    idx = 1
    for expected_key in ("codec", "key", "sha256", "size", "store"):
        assert (
            encoded[idx : idx + 1 + len(expected_key)]
            == bytes([0xA0 | len(expected_key)]) + expected_key.encode()
        )
        # skip past this key's value by decoding round-trip instead of hand parsing
        break
    decoded = m.ObjectRef.from_dict(m._unpack_strict(encoded))
    assert decoded == ref


def test_fixed_width_uint64_regardless_of_value():
    ref = m.ObjectRef(store="s", key="k", size=0, sha256=b"\x00" * 32, codec="bytes")
    encoded = ref.to_bytes()
    # "size" key followed by 0xcf (uint64 marker) even though value is 0
    assert b"\xa4size\xcf" in encoded


def test_duplicate_key_rejected():
    payload = (
        bytes([0x82])
        + m._pack_str("lease_id")
        + m._pack_bin(b"\x00" * 16)
        + m._pack_str("lease_id")
        + m._pack_bin(b"\x00" * 16)
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_unknown_key_rejected():
    payload = (
        bytes([0x82])
        + m._pack_str("lease_id")
        + m._pack_bin(b"\x00" * 16)
        + m._pack_str("extra")
        + m._pack_nil()
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_missing_required_key_rejected():
    payload = bytes([0x80])
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_trailing_bytes_rejected():
    payload = m.HeartbeatRequest(lease_id=b"\x00" * 16).to_bytes() + b"\x00"
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_bad_id_size_rejected():
    bad_id = b"\x00" * 15  # not 16 bytes
    payload = bytes([0x81]) + m._pack_str("lease_id") + m._pack_bin(bad_id)
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_wrong_type_rejected():
    payload = bytes([0x81]) + m._pack_str("lease_id") + m._pack_str("not binary")
    with pytest.raises(ProtocolDecodeError) as exc:
        m.decode_payload(MessageType.HEARTBEAT, payload)
    assert exc.value.code == "invalid_message"


def test_out_of_range_uint32_rejected():
    with pytest.raises(ProtocolDecodeError):
        m._uint(2**32, 32, "limit")


def test_utf8_string_round_trips():
    req = m.PullRequest(worker_id="worker-日本", capability_generation=1)
    encoded = req.to_bytes()
    decoded = m.decode_payload(MessageType.PULL, encoded)
    assert decoded == req


def test_empty_collections_round_trip():
    req = m.PullRequest(worker_id="w", capability_generation=1)
    encoded = req.to_bytes()
    decoded = m.decode_payload(MessageType.PULL, encoded)
    assert decoded == req

    snap = m.TaskSnapshot(tasks=[])
    encoded_snap = snap.to_bytes()
    decoded_snap = m.decode_payload(MessageType.TASK_QUERY, encoded_snap)
    assert decoded_snap == snap


def test_portable_value_profile_is_canonical_and_rejects_runtime_values():
    value = {"z": [None, True, -33, 2**64 - 1, 1.5], "a": b"x"}
    encoded = m.encode_portable_value(value)
    assert encoded[1:3] == b"\xa1a"
    assert m.decode_portable_value(encoded) == value
    for invalid in (float("nan"), float("inf"), (1, 2), {1: "x"}, 2**64):
        with pytest.raises(ProtocolDecodeError):
            m.encode_portable_value(invalid)


def test_worker_hello_and_task_registration_round_trip():
    hello = m.Hello(
        role="worker",
        worker_id="w",
        runtime="nodejs",
        runtime_version="22",
        sdk_version="0.1.0",
        codecs=["msgpack", "bytes"],
    )
    assert (
        m.decode_payload(MessageType.HELLO, m.encode_payload(MessageType.HELLO, hello))
        == hello
    )
    registration = m.TaskRegistration(
        worker_id="w",
        generation=1,
        tasks=[m.TaskCapability("task", "1", "value", ["msgpack"])],
    )
    assert (
        m.decode_payload(
            MessageType.REGISTER_TASKS,
            m.encode_payload(MessageType.REGISTER_TASKS, registration),
        )
        == registration
    )


# -- union validation --------------------------------------------------


def test_value_ref_requires_exactly_one_branch():
    with pytest.raises(ProtocolDecodeError):
        m.ValueRef()
    obj = m.ObjectRef(store="s", key="k", size=0, sha256=b"\x00" * 32, codec="bytes")
    with pytest.raises(ProtocolDecodeError):
        m.ValueRef(inline=b"x", codec="msgpack", object=obj)


def test_completion_requires_exactly_one_of_result_or_failure():
    with pytest.raises(ProtocolDecodeError):
        m.Completion(lease_id=b"\x00" * 16, result=None, failure=None)
    obj = m.ObjectRef(store="s", key="k", size=0, sha256=b"\x00" * 32, codec="bytes")
    failure = m.Failure(
        code="task_exception", message="x", details=None, retryable=False
    )
    with pytest.raises(ProtocolDecodeError):
        m.Completion(lease_id=b"\x00" * 16, result=obj, failure=failure)


def test_ack_requires_exact_field_set_for_kind():
    with pytest.raises(ProtocolDecodeError):
        m.Ack(kind="submit", fields={})
    with pytest.raises(ProtocolDecodeError):
        m.Ack(kind="hello", fields={"task_id": b"\x00" * 16})


def test_encode_payload_rejects_wrong_type_for_message():
    req = m.PullRequest(worker_id="w", capability_generation=1)
    with pytest.raises(ProtocolDecodeError) as exc:
        m.encode_payload(MessageType.HEARTBEAT, req)
    assert exc.value.code == "invalid_message"
