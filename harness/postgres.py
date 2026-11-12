"""Throwaway PostgreSQL databases for integration tests.

Tests point ``TASKWIRE_TEST_DATABASE_URL`` at a server they may create
databases on. Each session gets a uniquely named database, so concurrent
sessions (parallel CI jobs, two developers on one server) cannot collide.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

ENV_VAR = "TASKWIRE_TEST_DATABASE_URL"
DATABASE_PREFIX = "taskwire_test_"


def server_url(env: Mapping[str, str]) -> str | None:
    """The configured test server URL, or None when it is unset or blank."""
    value = env.get(ENV_VAR, "").strip()
    return value or None


def unique_database_name() -> str:
    return f"{DATABASE_PREFIX}{uuid.uuid4().hex[:16]}"


def url_for_database(server: str, name: str) -> str:
    """``server`` with its database replaced by ``name``."""
    if "://" not in server:
        raise ValueError(f"{ENV_VAR} must be a postgresql:// URL")
    parts = urlsplit(server)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def create_database(server: str) -> tuple[str, str]:
    """Create a uniquely named database; return ``(name, url)``."""
    name = unique_database_name()
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, url_for_database(server, name)


def drop_database(server: str, name: str) -> None:
    """Drop ``name``, disconnecting anything still attached to it."""
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(name)
            )
        )
