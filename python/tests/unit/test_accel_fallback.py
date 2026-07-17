from taskwire import _accel


def test_pure_python_fallback_active_without_native_module():
    # No taskwire._native extension ships in Phase 0, so the fallback path
    # must always be the one that runs.
    assert _accel.using_native() is False


def test_fingerprint_is_deterministic():
    assert _accel.fingerprint(b"taskwire") == _accel.fingerprint(b"taskwire")
    assert _accel.fingerprint(b"taskwire") != _accel.fingerprint(b"taskflow")


def test_fingerprint_matches_known_fnv1a_value():
    # Empty input is the FNV-1a offset basis itself.
    assert _accel.fingerprint(b"") == 0xCBF29CE484222325
