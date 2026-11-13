"""Configuration from environment variables.

There is no configuration file: Kubernetes supplies settings through ConfigMaps
and Secrets as environment variables. Errors name the offending variable and
never echo its value, because the backend URL carries credentials.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

BACKEND_URL = "TASKWIRE_BACKEND_URL"
STARTUP_CHECK = "TASKWIRE_STARTUP_CHECK"
ALLOW_EPHEMERAL = "TASKWIRE_ALLOW_EPHEMERAL"

_BACKEND_SCHEMES = {
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "memory": "memory",
}
_SECRET_QUERY = re.compile(r"(?i)\b(password|sslpassword)=[^&#]*")
_HOST = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(:\d*)?")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """A setting is missing or invalid. The message never contains its value."""

    def __init__(self, variable: str, problem: str) -> None:
        super().__init__(f"{variable}: {problem}")
        self.variable = variable
        self.problem = problem


class StartupCheck(enum.Enum):
    """How much of the backend contract a process verifies at start."""

    FULL = "full"
    SETTINGS = "settings"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class Settings:
    # repr=False: the URL may contain a password.
    backend_url: str = field(repr=False)
    # None: not configured, so each role applies its own default (clients
    # check `settings`; workers and the controller check `full`).
    startup_check: StartupCheck | None = None
    allow_ephemeral: bool = False

    @property
    def backend_kind(self) -> str:
        """``postgresql`` or ``memory``."""
        return _BACKEND_SCHEMES[_scheme(self.backend_url)]

    def startup_check_or(self, default: StartupCheck) -> StartupCheck:
        """The configured level, or the calling role's ``default``."""
        return self.startup_check if self.startup_check is not None else default

    @property
    def redacted_backend_url(self) -> str:
        """The backend URL with credentials replaced, safe to log."""
        return redact_url(self.backend_url)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read settings from ``env`` (default: the process environment)."""
    if env is None:
        import os

        env = os.environ

    return Settings(
        backend_url=_backend_url(env),
        startup_check=_startup_check(env),
        allow_ephemeral=_boolean(env, ALLOW_EPHEMERAL, default=False),
    )


def redact_url(url: str) -> str:
    """Replace any password in ``url`` (userinfo or query) with ``***``.

    Deliberately string-based rather than ``urlsplit``: it must never raise, and
    it must fail closed. Anything between ``://`` and the last ``@`` is treated
    as userinfo, so a password holding an unencoded ``/``, ``#`` or ``?`` is
    still hidden (at worst, more is hidden than necessary).
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    at = rest.rfind("@")
    if at != -1:
        user, colon, _ = rest[:at].partition(":")
        if colon:
            rest = f"{user}:***{rest[at:]}"
    return _SECRET_QUERY.sub(r"\1=***", f"{scheme}{sep}{rest}")


def _backend_url(env: Mapping[str, str]) -> str:
    raw = env.get(BACKEND_URL, "").strip()
    if not raw:
        raise ConfigError(BACKEND_URL, "is required")
    scheme = _scheme(raw)
    if scheme not in _BACKEND_SCHEMES:
        raise ConfigError(
            BACKEND_URL,
            "must start with one of: "
            + ", ".join(f"{name}://" for name in sorted(_BACKEND_SCHEMES)),
        )
    if scheme == "memory" and raw != "memory://":
        raise ConfigError(BACKEND_URL, "memory backend takes no address: use memory://")
    if scheme != "memory":
        _check_hosts(raw)
    return raw


def _scheme(url: str) -> str:
    scheme, sep, _ = url.partition("://")
    return scheme.lower() if sep else ""


def _check_hosts(raw: str) -> None:
    """Reject a malformed host list or port; never echoes the URL."""
    rest = raw.partition("://")[2]
    rest = rest[rest.rfind("@", 0, _authority_end(rest)) + 1 :]
    for host in rest[: _authority_end(rest)].split(","):
        if not _HOST.fullmatch(host):
            raise ConfigError(BACKEND_URL, "has a malformed host or port")


def _authority_end(rest: str) -> int:
    ends = [i for i in (rest.find(c) for c in "/?#") if i != -1]
    return min(ends) if ends else len(rest)


def _startup_check(env: Mapping[str, str]) -> StartupCheck | None:
    raw = env.get(STARTUP_CHECK, "").strip().lower()
    if not raw:
        return None
    try:
        return StartupCheck(raw)
    except ValueError:
        allowed = ", ".join(level.value for level in StartupCheck)
        raise ConfigError(STARTUP_CHECK, f"must be one of: {allowed}") from None


def _boolean(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(name, "must be a boolean: true or false")
