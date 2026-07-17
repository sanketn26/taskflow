import pytest

import taskwire.agent_locate as agent_locate
from taskwire.agent_locate import AgentNotFoundError, find_agent_binary


def test_finds_binary_via_explicit_override(tmp_path, monkeypatch):
    fake_binary = tmp_path / "taskwire-agent"
    fake_binary.write_text("#!/bin/sh\necho fake\n")
    monkeypatch.setenv("TASKWIRE_AGENT_PATH", str(fake_binary))

    assert find_agent_binary() == fake_binary


def test_rejects_override_pointing_nowhere(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKWIRE_AGENT_PATH", str(tmp_path / "does-not-exist"))

    with pytest.raises(AgentNotFoundError, match="TASKWIRE_AGENT_PATH"):
        find_agent_binary()


def test_finds_bundled_binary_when_no_override(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKWIRE_AGENT_PATH", raising=False)
    bundled = tmp_path / "taskwire-agent"
    bundled.write_text("#!/bin/sh\necho fake\n")
    monkeypatch.setattr(agent_locate, "_bundled_path", lambda: bundled)

    assert find_agent_binary() == bundled


def test_actionable_error_when_nothing_bundled(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKWIRE_AGENT_PATH", raising=False)
    monkeypatch.setattr(
        agent_locate, "_bundled_path", lambda: tmp_path / "taskwire-agent"
    )

    with pytest.raises(AgentNotFoundError, match="make build-agent"):
        find_agent_binary()
