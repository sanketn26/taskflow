"""Live Python/Go conformance over the TaskwireControl gRPC service."""

from __future__ import annotations

import grpc
import pytest

from harness import AgentHarness
from taskwire.protocol import ControlClient
from taskwire.protocol.errors import error_from_rpc_error

pytestmark = pytest.mark.integration


def test_status_rpc_round_trips(built_agent_binary):
    # Python builds the request, Go serves it, Python decodes the response.
    # This is the cross-language conformance boundary, without relying on
    # byte identity.
    with AgentHarness(agent_binary=built_agent_binary, chaos_seed=10) as harness:
        snapshot = harness.status()
        assert snapshot.ready is True
        assert snapshot.storage_healthy is True
        assert snapshot.pid > 0


def test_role_metadata_is_enforced_by_the_agent(built_agent_binary):
    """An admin client may read status but must not submit tasks."""
    with (
        AgentHarness(agent_binary=built_agent_binary, chaos_seed=11) as harness,
        ControlClient.admin(str(harness.socket_path)) as client,
    ):
        assert client.status(timeout=2.0).ready is True

        with pytest.raises(grpc.RpcError) as exc:
            client._stub.Submit(
                _empty_envelope(),
                timeout=2.0,
                metadata=client._metadata,
            )
        assert exc.value.code() is grpc.StatusCode.PERMISSION_DENIED
        detail = error_from_rpc_error(exc.value)
        assert detail is not None
        assert detail.code == "role_forbidden"
        assert detail.retryable is False


def test_missing_role_metadata_is_rejected(built_agent_binary):
    with AgentHarness(agent_binary=built_agent_binary, chaos_seed=12) as harness:
        channel = grpc.insecure_channel(f"unix:{harness.socket_path}")
        with channel:
            from taskwire.protocol.pb import control_pb2 as pb
            from taskwire.protocol.pb import control_pb2_grpc as pb_grpc

            stub = pb_grpc.TaskwireControlStub(channel)
            with pytest.raises(grpc.RpcError) as exc:
                stub.Status(pb.StatusRequest(), timeout=2.0)
            assert exc.value.code() is grpc.StatusCode.FAILED_PRECONDITION
            detail = error_from_rpc_error(exc.value)
            assert detail is not None
            assert detail.code == "not_registered"


def test_worker_stream_registers_and_heartbeats(built_agent_binary):
    """The duplex Work stream replaces HELLO + REGISTER_TASKS + HEARTBEAT."""
    from taskwire.protocol.pb import control_pb2 as pb

    with (
        AgentHarness(agent_binary=built_agent_binary, chaos_seed=13) as harness,
        ControlClient.worker(str(harness.socket_path)) as client,
    ):
        outbound: list[pb.WorkerMessage] = [
            pb.WorkerMessage(
                register=pb.WorkerRegistration(
                    worker_id="w-1",
                    runtime="python",
                    runtime_version="3.12",
                    sdk_version="0.1.0",
                    codecs=["msgpack"],
                    tasks=pb.TaskRegistration(
                        worker_id="w-1",
                        generation=1,
                        tasks=[
                            pb.TaskCapability(
                                task_name="a",
                                task_version="1",
                                invocation="value",
                                codecs=["msgpack"],
                            )
                        ],
                    ),
                )
            ),
            pb.WorkerMessage(heartbeat=pb.HeartbeatRequest(lease_id=b"\x09" * 16)),
        ]

        responses = client.work(outbound)
        registered = next(responses).registered
        assert registered.worker_id == "w-1"
        assert registered.generation == 1
        assert registered.accepted == 1

        heartbeat_ack = next(responses).heartbeat_ack
        assert heartbeat_ack.lease_id == b"\x09" * 16


def _empty_envelope():
    from taskwire.protocol.pb import control_pb2 as pb

    return pb.TaskEnvelope()
