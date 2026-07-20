"""Proves the wheel and editable-install stories in the exit gate:

Python must import, locate the bundled agent, and report a matching
version from a built wheel *with the source checkout unavailable* — not
merely with it on PYTHONPATH, which is what plain pytest collection would
give us for free and therefore prove nothing.
"""

import shutil
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.integration


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["poetry"]["version"]


_SMOKE_SCRIPT = """
import subprocess
import taskwire

agent = taskwire.find_agent_binary()
assert agent.is_file(), f"bundled agent not found at {agent}"

out = subprocess.run([str(agent), "version"], capture_output=True, text=True, check=True).stdout.strip()
assert out == taskwire.__version__, f"agent={out!r} python={taskwire.__version__!r}"
print("OK")
"""


def _run_isolated(python_exe: Path, cwd: Path) -> None:
    """Run the smoke script with no repo checkout reachable on sys.path."""
    result = subprocess.run(
        [str(python_exe), "-c", _SMOKE_SCRIPT],
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin"},  # deliberately no PYTHONPATH, no repo cwd
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout


def test_wheel_install_works_without_source_checkout(tmp_path, built_agent_binary):
    staged_bin = REPO_ROOT / "python" / "taskwire" / "bin" / "taskwire-agent"
    staged_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(built_agent_binary, staged_bin)
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        staged_bin.unlink(missing_ok=True)

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
    _run_isolated(venv_python, isolated_cwd)


def test_editable_install_reports_matching_version(tmp_path, built_agent_binary):
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    venv_python = venv_dir / "bin" / "python"

    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "-e", str(REPO_ROOT)],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [str(venv_python), "-c", "import taskwire; print(taskwire.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == _pyproject_version()
