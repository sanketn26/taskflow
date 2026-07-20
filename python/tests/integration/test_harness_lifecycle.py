import os
import signal

import pytest

import harness.agent_harness as agent_harness_module
from harness import AgentHarness

pytestmark = pytest.mark.integration


def test_start_stop_leaves_no_socket_or_orphans(built_agent_binary):
    harness = AgentHarness(agent_binary=built_agent_binary, chaos_seed=1)
    harness.start()
    try:
        assert harness.status().ready
        assert harness.socket_path.exists()
    finally:
        harness.stop()

    assert not harness.socket_path.exists()
    assert harness.worker_pids() == []
    harness.close()


def test_context_manager_stops_and_cleans_base_dir(built_agent_binary):
    with AgentHarness(agent_binary=built_agent_binary, chaos_seed=2) as harness:
        base_dir = harness.base_dir
        assert harness.status().ready

    assert not base_dir.exists()


def test_kill_and_restart_preserves_storage(built_agent_binary):
    # Use the harness's own short-named tmp dir rather than pytest's tmp_path:
    # a unix socket path assembled from the latter can exceed the OS's
    # ~104-108 byte sun_path limit once nested under a long test name.
    harness = AgentHarness(agent_binary=built_agent_binary, chaos_seed=3)
    harness.start()
    marker = harness.state_dir / "marker.txt"
    marker.write_text("keep me")

    harness.kill()
    assert marker.exists(), "kill() must not touch storage"

    harness.start()
    try:
        assert harness.status().ready
        assert marker.read_text() == "keep me"
    finally:
        harness.close()


def test_sigterm_then_sigkill_fallback_terminates(built_agent_binary, monkeypatch):
    monkeypatch.setattr(agent_harness_module, "_SIGKILL_GRACE_SECONDS", 0.3)
    harness = AgentHarness(agent_binary=built_agent_binary, chaos_seed=4)
    harness.start()

    real_send_signal = harness._proc.send_signal

    def ignore_sigterm(sig):
        if sig == signal.SIGTERM:
            return
        real_send_signal(sig)

    monkeypatch.setattr(harness._proc, "send_signal", ignore_sigterm)

    harness.stop()
    assert not harness.socket_path.exists()
    events = [e.action for e in harness.chaos.events]
    assert events.count("terminate") == 2, (
        "expected SIGTERM attempt then SIGKILL fallback"
    )

    harness.close()


def test_missing_socket_never_appears_for_never_started_harness(
    tmp_path, built_agent_binary
):
    harness = AgentHarness(agent_binary=built_agent_binary, base_dir=tmp_path)
    harness.stop()  # no-op: never started
    assert os.listdir(tmp_path) == []
