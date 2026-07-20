"""Pure connection-session state machine: no sockets.

Tracks per-connection registration, role authorization, in-flight request
IDs, and owner/result-ACK correlation, and returns deterministic outputs a
caller applies. Primary-owner connection replacement across multiple
connections is Phase 2 integration work; this module exposes the
transition outputs Phase 2 needs to implement it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from taskwire.protocol.errors import (
    DUPLICATE_REQUEST,
    NOT_REGISTERED,
    OWNER_MISMATCH,
    ROLE_FORBIDDEN,
    TASK_CONFLICT,
    ProtocolDecodeError,
)
from taskwire.protocol.frames import MessageType

# Message types allowed for each registered role, once past HELLO.
_RUNTIME_MESSAGES = frozenset(
    {
        MessageType.SUBMIT,
        MessageType.CANCEL,
        MessageType.RESUME_RESULTS,
        MessageType.TASK_QUERY,
        MessageType.ACK,  # notification ACKs
    }
)
_WORKER_MESSAGES = frozenset(
    {
        MessageType.REGISTER_TASKS,
        MessageType.PULL,
        MessageType.HEARTBEAT,
        MessageType.COMPLETE,
    }
)
_ADMIN_MESSAGES = frozenset({MessageType.STATUS})

_ROLE_MESSAGES = {
    "runtime": _RUNTIME_MESSAGES,
    "worker": _WORKER_MESSAGES,
    "admin": _ADMIN_MESSAGES,
}


@dataclass
class ConnectionState:
    """Mutable per-connection state owned by the caller (one per socket)."""

    registered: bool = False
    role: Optional[str] = None
    owner_id: Optional[bytes] = None
    worker_id: Optional[str] = None
    runtime: Optional[str] = None
    codecs: frozenset[str] = frozenset()
    capability_generation: int = 0
    capability_fingerprint: Optional[bytes] = None
    in_flight_request_ids: set[int] = field(default_factory=set)

    def reset_request_namespace(self) -> None:
        """Call on reconnect: a new connection starts a fresh ID namespace."""
        self.in_flight_request_ids = set()


class Session:
    """Pure state machine for one connection.

    All methods take the current ``ConnectionState`` explicitly and mutate
    it in place, returning either a value or raising ``ProtocolDecodeError``
    with a stable error code the caller turns into an ``ERROR`` frame.
    """

    def register(
        self,
        state: ConnectionState,
        *,
        role: str,
        owner_id: Optional[bytes],
        worker_id: Optional[str],
        runtime: Optional[str] = None,
        codecs: Optional[list[str]] = None,
    ) -> None:
        state.registered = True
        state.role = role
        state.owner_id = owner_id
        state.worker_id = worker_id
        state.runtime = runtime
        state.codecs = frozenset(codecs or [])
        state.capability_generation = 0
        state.capability_fingerprint = None

    def authorize(self, state: ConnectionState, message_type: MessageType) -> None:
        if message_type == MessageType.HELLO:
            return
        if not state.registered:
            raise ProtocolDecodeError(NOT_REGISTERED, "connection has not sent HELLO")
        allowed = _ROLE_MESSAGES.get(state.role, frozenset())
        if message_type not in allowed:
            raise ProtocolDecodeError(
                ROLE_FORBIDDEN, f"role {state.role!r} may not send {message_type.name}"
            )
        if (
            state.role == "worker"
            and message_type == MessageType.PULL
            and state.capability_generation == 0
        ):
            raise ProtocolDecodeError(
                NOT_REGISTERED, "worker has not registered task capabilities"
            )

    def register_tasks(
        self,
        state: ConnectionState,
        *,
        worker_id: str,
        generation: int,
        fingerprint: bytes,
        invocations_and_codecs: list[tuple[str, list[str]]],
    ) -> None:
        if state.role != "worker":
            raise ProtocolDecodeError(ROLE_FORBIDDEN, "only workers register tasks")
        if worker_id != state.worker_id:
            raise ProtocolDecodeError(OWNER_MISMATCH, "worker_id does not match HELLO")
        for invocation, codecs in invocations_and_codecs:
            if not set(codecs).issubset(state.codecs):
                raise ProtocolDecodeError(TASK_CONFLICT, "task codec absent from HELLO")
            if state.runtime != "python" and (
                invocation == "python_args" or "cloudpickle" in codecs
            ):
                raise ProtocolDecodeError(
                    TASK_CONFLICT, "runtime cannot provide Python-only capability"
                )
        if generation < state.capability_generation:
            raise ProtocolDecodeError(TASK_CONFLICT, "stale capability generation")
        if generation == state.capability_generation:
            if fingerprint == state.capability_fingerprint:
                return
            raise ProtocolDecodeError(
                TASK_CONFLICT, "conflicting capability generation"
            )
        state.capability_generation = generation
        state.capability_fingerprint = fingerprint

    def begin_request(self, state: ConnectionState, request_id: int) -> None:
        """Register a nonzero request ID as in flight; reject reuse."""
        if request_id == 0:
            return
        if request_id in state.in_flight_request_ids:
            raise ProtocolDecodeError(
                DUPLICATE_REQUEST, f"request id {request_id} already in flight"
            )
        state.in_flight_request_ids.add(request_id)

    def complete_request(self, state: ConnectionState, request_id: int) -> None:
        """Free a request ID once its response has been sent."""
        state.in_flight_request_ids.discard(request_id)

    def check_owner(self, state: ConnectionState, owner_id: bytes) -> None:
        if state.owner_id != owner_id:
            raise ProtocolDecodeError(
                OWNER_MISMATCH, "owner_id does not match registered owner"
            )


@dataclass(frozen=True)
class OwnerRegistration:
    """One Runtime connection registered for an owner ID."""

    owner_id: bytes
    connection_key: object


class OwnerRegistry:
    """Tracks which connection is primary for each owner ID.

    A newer connection for the same owner becomes primary once its resume
    begins; the caller is responsible for detecting "resume begins" and
    calling `promote`. The old connection may finish in-flight responses
    but stops receiving new notifications once replaced.
    """

    def __init__(self) -> None:
        self._primary: dict[bytes, object] = {}

    def promote(self, owner_id: bytes, connection_key: object) -> Optional[object]:
        """Make connection_key primary for owner_id; returns the previous
        primary connection key (or None if there wasn't one)."""
        previous = self._primary.get(owner_id)
        self._primary[owner_id] = connection_key
        return previous

    def is_primary(self, owner_id: bytes, connection_key: object) -> bool:
        return self._primary.get(owner_id) == connection_key

    def remove(self, owner_id: bytes, connection_key: object) -> None:
        if self._primary.get(owner_id) == connection_key:
            del self._primary[owner_id]
