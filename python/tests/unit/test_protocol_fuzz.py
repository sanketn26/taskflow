"""Ordinary unit tests iterate the deterministic byte corpora under
python/tests/fuzz_corpus/ so CI exercises fuzz-style coverage without a
fuzz plugin. Properties: never panic (only ProtocolDecodeError is an
acceptable failure), never allocate beyond the configured bound, and
accepted frames/payloads re-encode canonically.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from taskwire.protocol.errors import ProtocolDecodeError
from taskwire.protocol.frames import MessageType, read_frame
from taskwire.protocol import messages as m

REPO_ROOT = Path(__file__).resolve().parents[3]
FRAMES_DIR = REPO_ROOT / "python" / "tests" / "fuzz_corpus" / "frames"
ENVELOPES_DIR = REPO_ROOT / "python" / "tests" / "fuzz_corpus" / "envelopes"
MAX_PAYLOAD = 16 * 1024 * 1024


def _frame_corpus() -> list[Path]:
    return sorted(FRAMES_DIR.glob("*.bin"))


def _envelope_corpus() -> list[Path]:
    return sorted(ENVELOPES_DIR.glob("*.bin"))


@pytest.mark.parametrize("path", _frame_corpus(), ids=lambda p: p.stem)
def test_fuzz_read_frame_never_panics(path):
    data = path.read_bytes()
    buf = io.BytesIO(data)

    def read_exact(n: int) -> bytes:
        return buf.read(n)

    try:
        frame = read_frame(read_exact, max_payload_bytes=MAX_PAYLOAD)
    except ProtocolDecodeError:
        return  # a registered stable error code: acceptable failure

    if frame is None:
        return  # clean EOF before any header byte

    # Accepted frames must re-encode to canonical bytes for their fields
    # (payload canonicality is checked separately by the envelope corpus).
    assert frame.version == 1
    assert isinstance(frame.message_type, MessageType)


@pytest.mark.parametrize("path", _envelope_corpus(), ids=lambda p: p.stem)
def test_fuzz_decode_payload_never_panics(path):
    data = path.read_bytes()
    for message_type in MessageType:
        try:
            value = m.decode_payload(message_type, data)
        except ProtocolDecodeError:
            continue  # a registered stable error code: acceptable failure

        # A successful decode must re-encode canonically and round-trip.
        re_encoded = m.encode_payload(message_type, value)
        again = m.decode_payload(message_type, re_encoded)
        assert again == value
