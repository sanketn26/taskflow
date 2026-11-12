import dataclasses

import pytest

from taskwire.config import (
    ALLOW_EPHEMERAL,
    BACKEND_URL,
    STARTUP_CHECK,
    ConfigError,
    StartupCheck,
    load_settings,
    redact_url,
)

PG = "postgresql://user:s3cret@db.example:5432/taskwire"


def test_defaults_when_only_the_backend_url_is_set():
    settings = load_settings({BACKEND_URL: PG})
    assert settings.backend_url == PG
    assert settings.backend_kind == "postgresql"
    assert settings.startup_check is None
    assert settings.startup_check_or(StartupCheck.SETTINGS) is StartupCheck.SETTINGS
    assert settings.allow_ephemeral is False


def test_memory_backend():
    settings = load_settings({BACKEND_URL: "memory://"})
    assert settings.backend_kind == "memory"


def test_overrides():
    settings = load_settings(
        {BACKEND_URL: PG, STARTUP_CHECK: "Settings", ALLOW_EPHEMERAL: "TRUE"}
    )
    assert settings.startup_check is StartupCheck.SETTINGS
    assert settings.allow_ephemeral is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_boolean_true_spellings(value):
    assert load_settings({BACKEND_URL: PG, ALLOW_EPHEMERAL: value}).allow_ephemeral


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_boolean_false_spellings(value):
    assert not load_settings({BACKEND_URL: PG, ALLOW_EPHEMERAL: value}).allow_ephemeral


@pytest.mark.parametrize("env", [{}, {BACKEND_URL: ""}, {BACKEND_URL: "   "}])
def test_missing_backend_url_names_the_variable(env):
    with pytest.raises(ConfigError, match=BACKEND_URL) as info:
        load_settings(env)
    assert info.value.variable == BACKEND_URL


@pytest.mark.parametrize(
    "url", ["mysql://u:pw@h/db", "sqlite:///x.db", "not a url", "postgres:/h/db"]
)
def test_unsupported_scheme_is_rejected_without_echoing_the_value(url):
    with pytest.raises(ConfigError, match=BACKEND_URL) as info:
        load_settings({BACKEND_URL: url})
    assert url not in str(info.value)


def test_postgres_scheme_is_accepted():
    assert load_settings({BACKEND_URL: "postgres://h/db"}).backend_kind == "postgresql"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:pw@h:abc/db",
        "postgresql://u:pw@h1:5432,h2:xx/db",
        "postgresql://u:pw@[::1:5432/db",
    ],
)
def test_malformed_host_or_port_is_rejected_without_echoing(url):
    with pytest.raises(ConfigError, match=BACKEND_URL) as info:
        load_settings({BACKEND_URL: url})
    assert "pw" not in str(info.value)


def test_multi_host_and_socket_urls_load_and_redact():
    multi = "postgresql://u:pw@h1:5432,h2:5432/db?target_session_attrs=read-write"
    assert load_settings({BACKEND_URL: multi}).redacted_backend_url == (
        "postgresql://u:***@h1:5432,h2:5432/db?target_session_attrs=read-write"
    )
    sock = "postgresql:///db?host=/var/run/postgresql"
    assert load_settings({BACKEND_URL: sock}).redacted_backend_url == sock


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:SECRET#x@h/db",
        "postgresql://u:SECRET/x@h/db",
        "postgresql://u:SECRET?x@h/db",
        "postgresql://u:SECRET@x@h/db",
        "postgresql://u:SECRET%40x@h/db",
        "postgresql://u:SECRET@h:abc/db",
        "postgresql://h/db?PASSWORD=SECRET&sslpassword=SECRET",
        "postgresql://h/db?password=SECRET#frag",
    ],
)
def test_redact_url_never_raises_and_never_leaks(url):
    assert "SECRET" not in redact_url(url)


def test_memory_backend_takes_no_address():
    with pytest.raises(ConfigError, match=BACKEND_URL):
        load_settings({BACKEND_URL: "memory://somewhere"})


def test_malformed_startup_check():
    with pytest.raises(ConfigError, match=STARTUP_CHECK) as info:
        load_settings({BACKEND_URL: PG, STARTUP_CHECK: "sometimes"})
    assert "full" in str(info.value) and "off" in str(info.value)


def test_malformed_boolean():
    with pytest.raises(ConfigError, match=ALLOW_EPHEMERAL):
        load_settings({BACKEND_URL: PG, ALLOW_EPHEMERAL: "maybe"})


def test_settings_repr_does_not_leak_credentials():
    settings = load_settings({BACKEND_URL: PG})
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings)


def test_settings_are_immutable():
    settings = load_settings({BACKEND_URL: PG})
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.allow_ephemeral = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (PG, "postgresql://user:***@db.example:5432/taskwire"),
        ("postgresql://user@db.example/x", "postgresql://user@db.example/x"),
        ("postgresql://u:pw@[::1]:5432/x", "postgresql://u:***@[::1]:5432/x"),
        (
            "postgresql://db.example/x?password=hunter2&sslmode=require",
            "postgresql://db.example/x?password=***&sslmode=require",
        ),
        ("memory://", "memory://"),
    ],
)
def test_redact_url(url, expected):
    redacted = redact_url(url)
    assert redacted == expected
    assert "hunter2" not in redacted and "pw@" not in redacted


def test_redacted_backend_url_property():
    settings = load_settings({BACKEND_URL: PG})
    assert "s3cret" not in settings.redacted_backend_url
