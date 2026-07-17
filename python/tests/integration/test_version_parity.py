import subprocess

import pytest

import taskwire

pytestmark = pytest.mark.integration


def test_agent_version_matches_python_version(built_agent_binary):
    out = subprocess.run(
        [str(built_agent_binary), "version"], check=True, capture_output=True, text=True
    ).stdout.strip()

    assert out == taskwire.__version__
