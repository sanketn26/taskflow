"""Deterministic discovery of the taskwire-agent binary.

Resolution order:
1. ``TASKWIRE_AGENT_PATH`` environment variable, if set (explicit override —
   e.g. developer-built binaries during an editable install).
2. The binary bundled alongside this package at build time.

The package never downloads or compiles a binary at import time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BINARY_NAME = "taskwire-agent.exe" if sys.platform == "win32" else "taskwire-agent"


class AgentNotFoundError(RuntimeError):
    """Raised when no usable taskwire-agent binary can be located."""


def _bundled_path() -> Path:
    return Path(__file__).resolve().parent / "bin" / _BINARY_NAME


def find_agent_binary() -> Path:
    """Return the path to a usable taskwire-agent binary.

    Raises AgentNotFoundError with an actionable message if none is found.
    """
    override = os.environ.get("TASKWIRE_AGENT_PATH")
    if override:
        path = Path(override)
        if not path.is_file():
            raise AgentNotFoundError(
                f"TASKWIRE_AGENT_PATH={override!r} does not point to a file. "
                "Unset it to use the bundled binary, or point it at a built "
                "taskwire-agent executable."
            )
        return path

    bundled = _bundled_path()
    if bundled.is_file():
        return bundled

    raise AgentNotFoundError(
        f"No taskwire-agent binary found at {bundled}. Build it with "
        "'make build-agent' (development) or install the platform wheel "
        "that bundles it, or set TASKWIRE_AGENT_PATH explicitly."
    )
