import pytest

from taskwire.config import API_TOKEN, CONTROLLER_URL, ConfigError, load_settings

GOOD = {CONTROLLER_URL: "https://taskwire.svc:8443", API_TOKEN: "s3cret-token"}


def test_loads_valid_settings():
    settings = load_settings(GOOD)
    assert settings.controller_url == "https://taskwire.svc:8443"
    assert settings.api_token == "s3cret-token"


@pytest.mark.parametrize("missing", [CONTROLLER_URL, API_TOKEN])
def test_missing_variable_is_named(missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert exc.value.variable == missing


def test_blank_values_count_as_missing():
    with pytest.raises(ConfigError):
        load_settings({**GOOD, API_TOKEN: "   "})


def test_bad_scheme_is_rejected_without_echoing_the_value():
    with pytest.raises(ConfigError) as exc:
        load_settings({**GOOD, CONTROLLER_URL: "ftp://user:hunter2@host"})
    assert "hunter2" not in str(exc.value)
    assert exc.value.variable == CONTROLLER_URL


def test_token_never_appears_in_repr():
    assert "s3cret-token" not in repr(load_settings(GOOD))
