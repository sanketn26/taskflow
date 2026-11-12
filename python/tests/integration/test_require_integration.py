"""The skip/fail behaviour of the database fixtures, run in a subprocess."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET = "python/tests/integration/test_postgres_fixture.py"
_CLEARED = {"TASKWIRE_TEST_DATABASE_URL", "TASKWIRE_REQUIRE_INTEGRATION"}


def _run(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", TARGET, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_unset_variable_skips():
    result = _run({})
    assert result.returncode == 0, result.stdout
    assert "skipped" in result.stdout and "failed" not in result.stdout


def test_require_flag_turns_the_skip_into_a_failure():
    assert _run({"TASKWIRE_REQUIRE_INTEGRATION": "1"}).returncode != 0


def test_require_flag_zero_does_not_count_as_on():
    assert _run({"TASKWIRE_REQUIRE_INTEGRATION": "0"}).returncode == 0
