"""Seeded, reproducible chaos-action timeline.

Phase 0 only proves the timeline mechanics with a stub agent; later phases
add real fault injectors (process kill, network partition, disk pressure,
resource exhaustion) that record through this same timeline.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChaosEvent:
    action: str
    target: str
    timestamp: float  # time.monotonic() at record time


@dataclass
class ChaosTimeline:
    """Records chaos actions in order, seeded for reproducibility.

    The seed comes from the ``TASKWIRE_CHAOS_SEED`` environment variable by
    default so a failing CI run can be reproduced locally with the same
    seed. An explicit seed always overrides the environment.
    """

    seed: int = field(default_factory=lambda: _default_seed())
    _events: list[ChaosEvent] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def record(self, action: str, target: str) -> ChaosEvent:
        event = ChaosEvent(action=action, target=target, timestamp=time.monotonic())
        self._events.append(event)
        return event

    @property
    def events(self) -> list[ChaosEvent]:
        return list(self._events)

    def render(self) -> str:
        """Human-readable timeline for failure diagnostics."""
        lines = [f"chaos timeline (seed={self.seed}):"]
        for e in self._events:
            lines.append(f"  t={e.timestamp:.3f} {e.action} -> {e.target}")
        return "\n".join(lines)


def _default_seed() -> int:
    raw = os.environ.get("TASKWIRE_CHAOS_SEED")
    if raw is not None:
        return int(raw)
    return random.SystemRandom().randint(0, 2**32 - 1)
