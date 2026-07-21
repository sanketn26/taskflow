"""Live Python/Go Protobuf compatibility over the thin Taskwire frame."""

from __future__ import annotations

import socket
import pytest

from harness import AgentHarness
from taskwire.protocol import MessageType, StatusRequest, decode_payload, encode_payload
from taskwire.protocol.messages import ControlMessage

pytestmark = pytest.mark.integration


def test_python_payload_is_generated_control_message():
    payload = encode_payload(MessageType.STATUS, StatusRequest())
    assert ControlMessage.FromString(payload).WhichOneof("body") == "status_request"
    assert isinstance(decode_payload(MessageType.STATUS, payload), StatusRequest)


def test_framed_hello_status_works(built_agent_binary):
    # Python encodes HELLO/STATUS requests; Go decodes them and produces
    # Protobuf ACK/STATUS responses which Python decodes. This is the actual
    # cross-language conformance boundary, without relying on byte identity.
    with AgentHarness(agent_binary=built_agent_binary, chaos_seed=10) as harness:
        snapshot = harness.status()
        assert snapshot.ready is True
        assert snapshot.storage_healthy is True


def test_newline_status_probe_is_rejected(built_agent_binary):
    with AgentHarness(agent_binary=built_agent_binary, chaos_seed=11) as harness:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(str(harness.socket_path))
            s.sendall(b"STATUS\n")
            s.shutdown(socket.SHUT_WR)
            # The old text probe is 7 bytes, short of the 31-byte framed
            # header; the agent must not reply with a legacy "OK ..." line
            # and must not keep the connection open waiting for more.
            response = s.recv(4096)
            assert response == b""
            assert not response.startswith(b"OK")
