#!/usr/bin/env python3
"""Generates deterministic fuzz seed corpora for frames and envelopes from
the golden manifest plus the invalid corpus, with a handful of seeded
byte-flip mutations for extra boundary coverage. Python's ordinary unit
tests iterate python/tests/fuzz_corpus/*; Go's FuzzReadFrame and
FuzzDecodePayload are seeded from testdata/protocol/v1 directly.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python"))

import yaml  # noqa: E402

MANIFEST = REPO_ROOT / "testdata" / "protocol" / "v1" / "manifest.yaml"
INVALID_DIR = REPO_ROOT / "testdata" / "protocol" / "v1" / "invalid"
FRAMES_DIR = REPO_ROOT / "python" / "tests" / "fuzz_corpus" / "frames"
ENVELOPES_DIR = REPO_ROOT / "python" / "tests" / "fuzz_corpus" / "envelopes"

FRAMES_DIR.mkdir(parents=True, exist_ok=True)
ENVELOPES_DIR.mkdir(parents=True, exist_ok=True)
for old in list(FRAMES_DIR.glob("*.bin")) + list(ENVELOPES_DIR.glob("*.bin")):
    old.unlink()

cases = yaml.safe_load(MANIFEST.read_text())["cases"]

for case in cases:
    (FRAMES_DIR / f"golden_{case['name']}.bin").write_bytes(
        bytes.fromhex(case["frame_hex"])
    )
    (ENVELOPES_DIR / f"golden_{case['name']}.bin").write_bytes(
        bytes.fromhex(case["payload_hex"])
    )

for bin_path in sorted(INVALID_DIR.glob("*.bin")):
    data = bin_path.read_bytes()
    (FRAMES_DIR / f"invalid_{bin_path.stem}.bin").write_bytes(data)
    if len(data) > 31:
        (ENVELOPES_DIR / f"invalid_{bin_path.stem}.bin").write_bytes(data[31:])

rng = random.Random(1234567890)


def mutate(data: bytes, n: int) -> bytes:
    if not data:
        return data
    out = bytearray(data)
    for _ in range(n):
        idx = rng.randrange(len(out))
        out[idx] = rng.randrange(256)
    return bytes(out)


base_frames = [bytes.fromhex(c["frame_hex"]) for c in cases]
for i in range(40):
    src = base_frames[i % len(base_frames)]
    mutated = mutate(src, rng.randrange(1, 4))
    (FRAMES_DIR / f"mutated_{i:03d}.bin").write_bytes(mutated)
    # Also seed truncations at random cut points to exercise EOF handling.
    if len(src) > 1:
        cut = rng.randrange(1, len(src))
        (FRAMES_DIR / f"truncated_{i:03d}.bin").write_bytes(src[:cut])

base_payloads = [bytes.fromhex(c["payload_hex"]) for c in cases]
for i in range(40):
    src = base_payloads[i % len(base_payloads)]
    mutated = mutate(src, rng.randrange(1, 4))
    (ENVELOPES_DIR / f"mutated_{i:03d}.bin").write_bytes(mutated)

print(
    f"wrote {len(list(FRAMES_DIR.glob('*.bin')))} frame corpus files, "
    f"{len(list(ENVELOPES_DIR.glob('*.bin')))} envelope corpus files"
)
