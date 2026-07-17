"""Owns one taskwire-agent process for tests: config, socket, logs, storage,
lifecycle, raw status calls, and worker PID discovery.

Phase 0 exercises this against a stub agent to prove lifecycle and
diagnostics; later phases exercise it against the real protocol without
changing this contract.
"""

from __future__ import annotations

import shutil
import signal
import socket
import subprocess
import tempfile
from pathlib import Path

import yaml

from harness.chaos import ChaosTimeline
from harness.timing import wait_until
from taskwire.agent_locate import find_agent_binary

_SIGKILL_GRACE_SECONDS = 5.0


class AgentHarnessError(RuntimeError):
    """Raised for harness lifecycle failures, with attached diagnostics."""


class AgentHarness:
    """Manages one taskwire-agent process under an isolated directory tree."""

    def __init__(
        self,
        agent_binary: str | Path | None = None,
        *,
        base_dir: str | Path | None = None,
        chaos_seed: int | None = None,
        start_timeout: float = 10.0,
    ) -> None:
        self.agent_binary = Path(agent_binary) if agent_binary else find_agent_binary()
        self._owns_base_dir = base_dir is None
        self.base_dir = (
            Path(base_dir)
            if base_dir
            else Path(tempfile.mkdtemp(prefix="taskwire-harness-"))
        )
        self.start_timeout = start_timeout

        self.socket_path = self.base_dir / "agent.sock"
        self.log_path = self.base_dir / "agent.log"
        self.config_path = self.base_dir / "taskwire.yaml"
        self.state_dir = self.base_dir / "state"
        self.object_dir = self.base_dir / "objects"

        self.chaos = (
            ChaosTimeline(seed=chaos_seed)
            if chaos_seed is not None
            else ChaosTimeline()
        )

        self._proc: subprocess.Popen | None = None
        self._log_file = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise AgentHarnessError("agent already started")

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.object_dir.mkdir(parents=True, exist_ok=True)
        self._write_config()

        self._log_file = self.log_path.open("ab")
        self._proc = subprocess.Popen(
            [str(self.agent_binary), "--config", str(self.config_path)],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self.chaos.record("start", str(self.agent_binary))

        try:
            wait_until(
                self._socket_responds,
                timeout=self.start_timeout,
                description=f"agent socket at {self.socket_path}",
            )
        except TimeoutError as exc:
            raise AgentHarnessError(
                f"agent did not become ready: {exc}\n\n{self._diagnostics()}"
            ) from exc

    def stop(self) -> None:
        if self._proc is None:
            return
        self._terminate_process()
        self._proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

        if self.socket_path.exists():
            raise AgentHarnessError(
                f"socket {self.socket_path} still present after shutdown\n\n{self._diagnostics()}"
            )

        orphans = self.worker_pids()
        if orphans:
            raise AgentHarnessError(f"orphan worker PIDs after shutdown: {orphans}")

    def restart(self) -> None:
        """Stop and start again without deleting storage (state/object dirs)."""
        self.stop()
        self.start()

    def kill(self, *, sig: signal.Signals = signal.SIGKILL) -> None:
        """Forcefully kill the agent process, simulating a crash."""
        if self._proc is None:
            return
        self.chaos.record("kill", f"pid={self._proc.pid} sig={sig.name}")
        self._proc.send_signal(sig)
        self._proc.wait(timeout=_SIGKILL_GRACE_SECONDS)
        self._proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def close(self) -> None:
        """Stop the agent (if running) and remove the owned base directory."""
        try:
            self.stop()
        finally:
            if self._owns_base_dir and self.base_dir.exists():
                shutil.rmtree(self.base_dir, ignore_errors=True)

    def __enter__(self) -> AgentHarness:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- raw protocol access --------------------------------------------

    def status(self, timeout: float = 2.0) -> str:
        """Send a raw STATUS call and return the agent's response line."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(self.socket_path))
            s.sendall(b"STATUS\n")
            return s.recv(4096).decode().strip()

    def worker_pids(self) -> list[int]:
        """Discover PIDs of worker processes owned by this agent.

        Phase 0's stub agent spawns no workers; this always returns an
        empty list until Phase 3 introduces the worker pool.
        """
        return []

    # -- diagnostics -----------------------------------------------------

    def _diagnostics(self) -> str:
        log_tail = ""
        if self.log_path.exists():
            log_tail = self.log_path.read_text(errors="replace")[-4000:]
        return (
            f"agent binary: {self.agent_binary}\n"
            f"config: {self.config_path}\n"
            f"socket: {self.socket_path}\n"
            f"log tail:\n{log_tail}\n"
            f"{self.chaos.render()}"
        )

    # -- internals ---------------------------------------------------

    def _write_config(self) -> None:
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "socket_path": str(self.socket_path),
                    "log_path": str(self.log_path),
                    "state_dir": str(self.state_dir),
                    "object_dir": str(self.object_dir),
                }
            )
        )

    def _socket_responds(self) -> bool:
        if not self.socket_path.exists():
            return False
        try:
            return self.status().startswith("OK")
        except OSError:
            return False

    def _terminate_process(self) -> None:
        assert self._proc is not None
        self.chaos.record("terminate", f"pid={self._proc.pid} sig=SIGTERM")
        self._proc.send_signal(signal.SIGTERM)
        try:
            self._proc.wait(timeout=_SIGKILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass

        self.chaos.record("terminate", f"pid={self._proc.pid} sig=SIGKILL (fallback)")
        self._proc.kill()
        try:
            self._proc.wait(timeout=_SIGKILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise AgentHarnessError(
                f"agent pid={self._proc.pid} did not die even after SIGKILL"
            ) from exc

        # A killed process never runs its own cleanup; the harness owns the
        # socket and must remove it itself so no owned socket is left behind.
        self.socket_path.unlink(missing_ok=True)
