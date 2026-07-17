"""Test harness for taskwire-agent process lifecycle and chaos testing.

Lives outside ``python/`` because it is test infrastructure, not part of the
shipped SDK — it drives a *built* agent binary rather than being imported by
it.
"""

from harness.agent_harness import AgentHarness
from harness.chaos import ChaosTimeline
from harness.timing import free_port, wait_until

__all__ = ["AgentHarness", "ChaosTimeline", "free_port", "wait_until"]
