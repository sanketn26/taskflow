import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["poetry"]["version"]


@pytest.fixture(scope="session")
def built_agent_binary(tmp_path_factory) -> Path:
    """Build the Go agent once per test session with the release version."""
    bin_dir = tmp_path_factory.mktemp("agent-bin")
    binary = bin_dir / "taskwire-agent"
    subprocess.run(
        [
            "go",
            "build",
            "-ldflags",
            f"-X main.version={_pyproject_version()}",
            "-o",
            str(binary),
            "./agent/cmd/taskwire-agent",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return binary
