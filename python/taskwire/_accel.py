"""Optional native acceleration with a pure-Python fallback.

This module is the seam later phases hang real accelerated codecs off of.
Phase 0 only proves the pattern: importing the optional ``taskwire._native``
extension must never be required, and its absence must not change the
public return value of anything built on top of it.
"""

from __future__ import annotations

try:
    from taskwire import _native  # type: ignore[attr-defined]

    _HAVE_NATIVE = True
except ImportError:
    _native = None  # type: ignore[assignment]
    _HAVE_NATIVE = False


def _fnv1a_python(data: bytes) -> int:
    h = 0xCBF29CE484222325
    for byte in data:
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def fingerprint(data: bytes) -> int:
    """Return a 64-bit fingerprint of ``data``.

    Uses the native accelerator when available; otherwise falls back to a
    pure-Python implementation that returns identical results.
    """
    if _HAVE_NATIVE:
        return _native.fnv1a(data)
    return _fnv1a_python(data)


def using_native() -> bool:
    """Whether the native accelerator is active for this process."""
    return _HAVE_NATIVE
