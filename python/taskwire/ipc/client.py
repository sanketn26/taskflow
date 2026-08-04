"""gRPC client for the Taskwire control plane.

Connects to a local agent over a Unix domain socket and attaches the identity
metadata required by each RPC. Worker identity and capabilities are carried by
the ``Work`` stream itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Self

import grpc

from taskwire.protocol.errors import ProtocolError, error_from_rpc_error
from taskwire.protocol.pb import control_pb2 as pb
from taskwire.protocol.pb import control_pb2_grpc as pb_grpc

ROLE_RUNTIME = "runtime"
ROLE_WORKER = "worker"
ROLE_ADMIN = "admin"

METADATA_ROLE = "taskwire-role"
METADATA_OWNER_ID = "taskwire-owner-id"


def socket_target(socket_path: str) -> str:
    """Return the gRPC target string for a Unix domain socket path."""
    return f"unix:{socket_path}"


class ControlClient:
    """A role-scoped connection to one local agent.

    Use :meth:`runtime`, :meth:`worker`, or :meth:`admin` to construct one;
    the role determines which RPCs the agent will accept.
    """

    def __init__(
        self,
        socket_path: str,
        *,
        role: str,
        owner_id: bytes | None = None,
        max_message_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if role == ROLE_RUNTIME and (owner_id is None or len(owner_id) != 16):
            raise ValueError("runtime role requires a 16-byte owner_id")

        self.role = role
        self.owner_id = owner_id
        self._metadata: list[tuple[str, str]] = [(METADATA_ROLE, role)]
        if owner_id is not None:
            self._metadata.append((METADATA_OWNER_ID, owner_id.hex()))

        self._channel = grpc.insecure_channel(
            socket_target(socket_path),
            options=[
                ("grpc.max_receive_message_length", max_message_bytes),
                ("grpc.max_send_message_length", max_message_bytes),
            ],
        )
        self._stub = pb_grpc.TaskwireControlStub(self._channel)

    # --- constructors ---------------------------------------------------

    @classmethod
    def runtime(cls, socket_path: str, owner_id: bytes, **kwargs) -> ControlClient:
        return cls(socket_path, role=ROLE_RUNTIME, owner_id=owner_id, **kwargs)

    @classmethod
    def worker(cls, socket_path: str, **kwargs) -> ControlClient:
        return cls(socket_path, role=ROLE_WORKER, **kwargs)

    @classmethod
    def admin(cls, socket_path: str, **kwargs) -> ControlClient:
        return cls(socket_path, role=ROLE_ADMIN, **kwargs)

    # --- lifecycle ------------------------------------------------------

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def wait_for_ready(self, timeout: float) -> None:
        """Block until the channel connects, raising on timeout."""
        grpc.channel_ready_future(self._channel).result(timeout=timeout)

    # --- RPCs -----------------------------------------------------------

    def status(self, timeout: float | None = None) -> pb.StatusSnapshot:
        return self._call(self._stub.Status, pb.StatusRequest(), timeout)

    def submit(
        self, envelope: pb.TaskEnvelope, timeout: float | None = None
    ) -> pb.SubmitResponse:
        return self._call(self._stub.Submit, envelope, timeout)

    def cancel(
        self, owner_id: bytes, task_id: bytes, timeout: float | None = None
    ) -> pb.CancelResponse:
        request = pb.CancelRequest(owner_id=owner_id, task_id=task_id)
        return self._call(self._stub.Cancel, request, timeout)

    def query_tasks(
        self,
        owner_id: bytes,
        task_ids: Iterable[bytes],
        timeout: float | None = None,
    ) -> pb.TaskSnapshot:
        request = pb.TaskQuery(owner_id=owner_id, task_ids=list(task_ids))
        return self._call(self._stub.QueryTasks, request, timeout)

    def watch_results(
        self, owner_id: bytes, after_cursor: int = 0
    ) -> Iterator[pb.ResultNotification]:
        """Stream terminal results for an owner, resuming after a cursor.

        Stream position is the cursor, so a dropped connection resumes by
        reopening the stream with the last cursor the caller handled.
        """
        request = pb.WatchResultsRequest(owner_id=owner_id, after_cursor=after_cursor)
        try:
            yield from self._stub.WatchResults(request, metadata=self._metadata)
        except grpc.RpcError as error:
            raise self._translate(error) from None

    def ack_result(
        self,
        owner_id: bytes,
        task_id: bytes,
        cursor: int,
        timeout: float | None = None,
    ) -> pb.AckResultResponse:
        request = pb.AckResultRequest(owner_id=owner_id, task_id=task_id, cursor=cursor)
        return self._call(self._stub.AckResult, request, timeout)

    def work(self, requests: Iterable[pb.WorkerMessage]) -> Iterator[pb.AgentMessage]:
        """Open the worker duplex session."""
        try:
            yield from self._stub.Work(iter(requests), metadata=self._metadata)
        except grpc.RpcError as error:
            raise self._translate(error) from None

    def put_object(
        self, chunks: Iterable[pb.ObjectChunk], timeout: float | None = None
    ) -> pb.PutObjectResponse:
        try:
            return self._stub.PutObject(
                iter(chunks), timeout=timeout, metadata=self._metadata
            )
        except grpc.RpcError as error:
            raise self._translate(error) from None

    def get_object(self, obj: pb.ObjectRef) -> Iterator[pb.ObjectChunk]:
        request = pb.ObjectGetRequest(object=obj)
        try:
            yield from self._stub.GetObject(request, metadata=self._metadata)
        except grpc.RpcError as error:
            raise self._translate(error) from None

    # --- internals ------------------------------------------------------

    def _call(self, method, request, timeout: float | None):
        try:
            return method(request, timeout=timeout, metadata=self._metadata)
        except grpc.RpcError as error:
            raise self._translate(error) from None

    @staticmethod
    def _translate(error: grpc.RpcError) -> BaseException:
        """Prefer the agent's stable Taskwire code over the transport status."""
        return error_from_rpc_error(error) or error


__all__ = ["ControlClient", "ProtocolError", "socket_target"]
