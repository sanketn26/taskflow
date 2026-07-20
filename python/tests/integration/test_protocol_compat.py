"""Cross-language protocol compatibility: Python and Go both consume the
shared golden vectors (proven independently by each language's unit
suite), and the live agent speaks framed HELLO/STATUS while rejecting the
retired Phase 0 newline STATUS probe.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import yaml

from harness import AgentHarness
from taskwire.protocol import MessageType, decode_payload, encode_payload

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "testdata" / "protocol" / "v1" / "manifest.yaml"


def test_python_consumes_shared_golden_manifest():
    cases = yaml.safe_load(MANIFEST.read_text())["cases"]
    assert len(cases) > 0
    seen_types = set()
    for case in cases:
        message_type = MessageType[case["message_type"]]
        payload = bytes.fromhex(case["payload_hex"])
        value = decode_payload(message_type, payload)
        assert encode_payload(message_type, value) == payload
        seen_types.add(message_type)
    # Every message type in the protocol has at least one golden case.
    assert seen_types == set(MessageType)


def test_framed_hello_status_works(built_agent_binary):
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
