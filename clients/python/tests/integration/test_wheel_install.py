"""Clean-artifact packaging tests.

Proves the built wheel works when installed into an environment where the
source tree is *unavailable* — not merely with it on PYTHONPATH, which is what
plain pytest collection would give us for free and therefore prove nothing.
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.packaging


def _pyproject_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    return uv


def test_wheel_install_works_without_source_checkout(tmp_path):
    subprocess.run(
        [_uv(), "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, wheels

    venv_dir = tmp_path / "venv"
    venv_python = venv_dir / "bin" / "python"
    uv = _uv()
    subprocess.run(
        [uv, "venv", "--quiet", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [uv, "pip", "install", "--quiet", "--python", str(venv_python), str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
    )

    isolated_cwd = tmp_path / "outside-repo"
    isolated_cwd.mkdir()
    # Deliberately no PYTHONPATH and a cwd outside the repo.
    env = {"PATH": "/usr/bin:/bin"}

    for command in (
        [str(venv_python), "-c", "import taskwire; print(taskwire.__version__)"],
        [str(venv_python), "-m", "taskwire", "version"],
    ):
        result = subprocess.run(
            command, cwd=isolated_cwd, env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"{command}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert result.stdout.strip() == _pyproject_version(), command
