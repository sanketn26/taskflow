"""Library settings, read from environment variables.

The library talks only to the controller. It holds no database credential, so
the only secret here is the API token, which is never echoed in an error or in
``repr``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

CONTROLLER_URL = "TASKWIRE_CONTROLLER_URL"
API_TOKEN = "TASKWIRE_API_TOKEN"

_SCHEMES = ("http://", "https://", "grpc://", "grpcs://")


class ConfigError(ValueError):
    """A setting is missing or invalid. The message never contains its value."""

    def __init__(self, variable: str, problem: str) -> None:
        super().__init__(f"{variable}: {problem}")
        self.variable = variable
        self.problem = problem


@dataclass(frozen=True, slots=True)
class Settings:
    controller_url: str
    # repr=False: a bearer token must not reach logs.
    api_token: str = field(repr=False)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read settings from ``env`` (default: the process environment)."""
    if env is None:
        import os

        env = os.environ

    url = env.get(CONTROLLER_URL, "").strip()
    if not url:
        raise ConfigError(CONTROLLER_URL, "is required")
    if not url.lower().startswith(_SCHEMES):
        raise ConfigError(
            CONTROLLER_URL, "must start with one of: " + ", ".join(_SCHEMES)
        )
    token = env.get(API_TOKEN, "").strip()
    if not token:
        raise ConfigError(API_TOKEN, "is required")
    return Settings(controller_url=url, api_token=token)
