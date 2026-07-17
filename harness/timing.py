"""Deterministic waiting and resource-allocation helpers.

All readiness checks in the harness poll observable state against a
deadline; nothing sleeps for a fixed duration and hopes for the best.
"""

from __future__ import annotations

import contextlib
import socket
import time
from collections.abc import Callable


class DeadlineExceededError(TimeoutError):
    """Raised when a polled condition never became true before the deadline."""


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
    description: str = "condition",
) -> None:
    """Poll ``predicate`` until it returns True or ``timeout`` elapses.

    Raises DeadlineExceededError on timeout so callers get a precise,
    actionable failure instead of a hang or a flaky fixed sleep.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # predicate may probe a not-yet-ready resource
            last_error = exc
        time.sleep(interval)

    detail = f": {last_error}" if last_error else ""
    raise DeadlineExceededError(
        f"{description} did not become true within {timeout}s{detail}"
    )


def free_port() -> int:
    """Return a TCP port that was free at allocation time.

    There is an inherent TOCTOU gap between allocation and use; callers that
    need a stronger guarantee should bind immediately after calling this.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
