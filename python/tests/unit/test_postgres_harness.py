from harness import postgres


def test_server_url_unset_or_blank_is_none():
    assert postgres.server_url({}) is None
    assert postgres.server_url({postgres.ENV_VAR: "  "}) is None


def test_server_url_is_returned_when_set():
    url = "postgresql://u:p@h:5432/postgres"
    assert postgres.server_url({postgres.ENV_VAR: f" {url} "}) == url


def test_database_names_are_unique_and_prefixed():
    names = {postgres.unique_database_name() for _ in range(1000)}
    assert len(names) == 1000
    assert all(n.startswith(postgres.DATABASE_PREFIX) for n in names)


def test_url_for_database_swaps_only_the_database():
    out = postgres.url_for_database(
        "postgresql://u:p@h:5432/postgres?sslmode=require", "taskwire_test_x"
    )
    assert out == "postgresql://u:p@h:5432/taskwire_test_x?sslmode=require"
