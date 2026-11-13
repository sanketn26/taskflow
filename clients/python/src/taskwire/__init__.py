"""Taskwire Python library."""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("taskwire")
except _metadata.PackageNotFoundError:
    # Source checkout without an installed/editable distribution (e.g. tests
    # invoked via PYTHONPATH). Fall back to pyproject.toml, the single release
    # version source.
    import tomllib
    from pathlib import Path

    _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with _pyproject.open("rb") as _f:
        __version__ = tomllib.load(_f)["project"]["version"]

__all__ = ["__version__"]
