"""Fixtures for tests that need a real PostgreSQL server."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from harness import postgres

REQUIRE_VAR = "TASKWIRE_REQUIRE_INTEGRATION"

_HOW_TO_RUN = (
    f"{postgres.ENV_VAR} is not set. Start a server with `make db-up` and run "
    "`make integration`, or point the variable at any PostgreSQL 14+ server "
    "these tests may create databases on."
)


def _server_or_skip() -> str:
    url = postgres.server_url(os.environ)
    if url is not None:
        return url
    if os.environ.get(REQUIRE_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        # CI sets this so a misconfigured job fails instead of passing by skipping.
        pytest.fail(_HOW_TO_RUN, pytrace=False)
    pytest.skip(_HOW_TO_RUN)


@pytest.fixture(scope="session")
def pg_server_url() -> str:
    """URL of the server tests may create databases on."""
    return _server_or_skip()


@pytest.fixture(scope="session")
def pg_database_url(pg_server_url: str) -> Iterator[str]:
    """URL of a database created for this session and dropped afterwards."""
    try:
        name, url = postgres.create_database(pg_server_url)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"cannot reach the test PostgreSQL server ({type(exc).__name__}); "
            "is it running? try `make db-up`",
            pytrace=False,
        )
    try:
        yield url
    finally:
        postgres.drop_database(pg_server_url, name)


@pytest.fixture
def pg_conn(pg_database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to the session database with no ``taskwire`` schema."""
    with psycopg.connect(pg_database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS taskwire CASCADE")
        yield conn
