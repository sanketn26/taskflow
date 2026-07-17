"""Taskwire Python SDK."""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("taskwire")
except _metadata.PackageNotFoundError:
    # Source checkout without an installed/editable distribution (e.g. tests
    # invoked via PYTHONPATH). Fall back to the single release source so
    # __version__ still matches taskwire-agent's -X main.version build input.
    import tomllib
    from pathlib import Path

    _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with _pyproject.open("rb") as _f:
        __version__ = tomllib.load(_f)["tool"]["poetry"]["version"]

from taskwire.agent_locate import AgentNotFoundError, find_agent_binary

__all__ = ["__version__", "find_agent_binary", "AgentNotFoundError"]
