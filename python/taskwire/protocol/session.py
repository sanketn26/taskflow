"""Pure worker-session state machine: no sockets.

Tracks the capability set a worker has registered on one ``Work`` stream and
enforces the replacement rules. Role authorization and request correlation
belong to gRPC, so they are no longer modelled here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from taskwire.protocol.errors import (
    NOT_REGISTERED,
    OWNER_MISMATCH,
    TASK_CONFLICT,
    ProtocolError,
)
from taskwire.protocol.messages import TaskRegistration, WorkerRegistration, validate

# Roles carried in per-RPC metadata; they replace the HELLO handshake.
ROLE_RUNTIME = "runtime"
ROLE_WORKER = "worker"
ROLE_ADMIN = "admin"

METADATA_ROLE = "taskwire-role"
METADATA_OWNER_ID = "taskwire-owner-id"


@dataclass
class ConnectionState:
    """Mutable per-stream state owned by the caller (one per Work stream)."""

    registered: bool = False
    worker_id: str | None = None
    runtime: str | None = None
    codecs: frozenset[str] = frozenset()
    capability_generation: int = 0
    capability_fingerprint: bytes | None = None


class Session:
    """Pure state machine for one worker stream.

    All methods take the current ``ConnectionState`` explicitly and mutate it
    in place, raising ``ProtocolError`` with a stable error code the caller
    turns into a gRPC status.
    """

    def register_worker(
        self, state: ConnectionState, registration: WorkerRegistration
    ) -> None:
        validate(registration)
        state.registered = True
        state.worker_id = registration.worker_id
        state.runtime = registration.runtime
        state.codecs = frozenset(registration.codecs)
        state.capability_generation = 0
        state.capability_fingerprint = None
        self.register_tasks(state, registration.tasks)

    def register_tasks(
        self,
        state: ConnectionState,
        registration: TaskRegistration,
    ) -> None:
        if not state.registered:
            raise ProtocolError(NOT_REGISTERED, "worker has not registered")
        validate(registration)
        if registration.worker_id != state.worker_id:
            raise ProtocolError(OWNER_MISMATCH, "worker_id does not match registration")
        for task in registration.tasks:
            if not set(task.codecs).issubset(state.codecs):
                raise ProtocolError(
                    TASK_CONFLICT, "task codec absent from worker registration"
                )
            if state.runtime != "python" and (
                task.invocation == "python_args" or "cloudpickle" in task.codecs
            ):
                raise ProtocolError(
                    TASK_CONFLICT, "runtime cannot provide Python-only capability"
                )
        fingerprint = hashlib.sha256(
            registration.SerializeToString(deterministic=True)
        ).digest()
        generation = registration.generation
        if generation < state.capability_generation:
            raise ProtocolError(TASK_CONFLICT, "stale capability generation")
        if generation == state.capability_generation:
            if fingerprint == state.capability_fingerprint:
                return
            raise ProtocolError(TASK_CONFLICT, "conflicting capability generation")
        state.capability_generation = generation
        state.capability_fingerprint = fingerprint


class OwnerRegistry:
    """Tracks which WatchResults stream is primary for each owner ID.

    A newer stream for the same owner becomes primary once it opens; the old
    stream stops receiving new notifications.
    """

    def __init__(self) -> None:
        self._primary: dict[bytes, object] = {}

    def promote(self, owner_id: bytes, stream_key: object) -> object | None:
        """Make stream_key primary for owner_id; returns the previous primary
        stream key (or None if there wasn't one)."""
        previous = self._primary.get(owner_id)
        self._primary[owner_id] = stream_key
        return previous

    def is_primary(self, owner_id: bytes, stream_key: object) -> bool:
        return self._primary.get(owner_id) == stream_key

    def remove(self, owner_id: bytes, stream_key: object) -> None:
        if self._primary.get(owner_id) == stream_key:
            del self._primary[owner_id]
