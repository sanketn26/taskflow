import psycopg
import pytest

from harness import postgres

pytestmark = pytest.mark.integration


def test_session_database_accepts_queries(pg_conn):
    assert pg_conn.execute("SELECT 1").fetchone() == (1,)


def test_session_database_is_not_the_server_default(pg_conn, pg_server_url):
    current = pg_conn.execute("SELECT current_database()").fetchone()[0]
    assert current.startswith(postgres.DATABASE_PREFIX)


def test_server_version_is_supported(pg_conn):
    version = pg_conn.execute("SHOW server_version_num").fetchone()[0]
    assert int(version) >= 140000, "Taskwire supports PostgreSQL 14 and later"


def test_each_test_starts_without_a_taskwire_schema(pg_conn):
    exists = pg_conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'taskwire'"
    ).fetchone()
    assert exists is None
    # Leave state behind; the next test must not see it.
    pg_conn.execute("CREATE SCHEMA taskwire")


def test_schema_left_by_a_previous_test_is_gone(pg_conn):
    exists = pg_conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'taskwire'"
    ).fetchone()
    assert exists is None


def test_concurrent_sessions_get_separate_databases(pg_server_url):
    first_name, first_url = postgres.create_database(pg_server_url)
    second_name, second_url = postgres.create_database(pg_server_url)
    try:
        assert first_name != second_name
        with (
            psycopg.connect(first_url, autocommit=True) as a,
            psycopg.connect(second_url, autocommit=True) as b,
        ):
            a.execute("CREATE TABLE marker (id int)")
            assert b.execute("SELECT to_regclass('marker')").fetchone() == (None,)
    finally:
        postgres.drop_database(pg_server_url, first_name)
        postgres.drop_database(pg_server_url, second_name)


def test_drop_database_removes_it_even_with_open_connections(pg_server_url):
    name, url = postgres.create_database(pg_server_url)
    lingering = psycopg.connect(url, autocommit=True)
    try:
        postgres.drop_database(pg_server_url, name)
        with psycopg.connect(pg_server_url, autocommit=True) as admin:
            row = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
        assert row is None
    finally:
        lingering.close()
