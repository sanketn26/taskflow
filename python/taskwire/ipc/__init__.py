"""gRPC clients for the local Taskwire agent."""

from taskwire.ipc.client import (
    METADATA_OWNER_ID,
    METADATA_ROLE,
    ROLE_ADMIN,
    ROLE_RUNTIME,
    ROLE_WORKER,
    ControlClient,
    socket_target,
)

__all__ = [
    "METADATA_OWNER_ID",
    "METADATA_ROLE",
    "ROLE_ADMIN",
    "ROLE_RUNTIME",
    "ROLE_WORKER",
    "ControlClient",
    "socket_target",
]
