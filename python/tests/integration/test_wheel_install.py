"""Clean-artifact packaging tests.

Proves the built wheel works when installed into an environment where the
source tree is *unavailable* — not merely with it on PYTHONPATH, which is what
plain pytest collection would give us for free and therefore prove nothing.
"""

import shutil
import subprocess
import tomllib
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.packaging


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    return uv


def test_wheel_install_works_without_source_checkout(tmp_path):
    subprocess.run(
        [_uv(), "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, wheels

    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    venv_python = venv_dir / "bin" / "python"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
    )

    isolated_cwd = tmp_path / "outside-repo"
    isolated_cwd.mkdir()
    result = subprocess.run(
        [str(venv_python), "-c", "import taskwire; print(taskwire.__version__)"],
        cwd=isolated_cwd,
        env={"PATH": "/usr/bin:/bin"},  # deliberately no PYTHONPATH, no repo cwd
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == _pyproject_version()
