# Phase 0 — Repository and Test Spine

## Goal

A Python-only repository in which later phases can add code and have it linted, unit-tested, tested against a real PostgreSQL, and packaged, locally and in CI.

## Already present

- `pyproject.toml` (hatchling, uv) as the single version source; `taskwire.__version__`.
- `make sync format lint unit packaging wheel clean`.
- Unit test for the version; packaging test that installs the built wheel into a clean virtual environment with the source tree unreachable.
- `harness.wait_until` (deadline polling) and `harness.ChaosTimeline` (seeded, reproducible fault record).
- CI jobs for lint, unit (CPython 3.11–3.13), and packaging.

## Deliverables

### PostgreSQL test fixture

- `TASKWIRE_TEST_DATABASE_URL` points at a PostgreSQL server the tests may create databases on.
- A session-scoped pytest fixture creates a uniquely named database per test session and drops it afterwards. A function-scoped fixture gives each test a clean schema.
- If the variable is unset, tests marked `integration` are skipped with a message that says how to start a database. They are never silently passed.
- `make db-up` and `make db-down` start and stop a local PostgreSQL container for development.
- CI gains an `integration` job using a PostgreSQL service container, run against the oldest and newest supported PostgreSQL major versions.

Supported PostgreSQL versions: 14 and later.

### Command-line entry point

- A `taskwire` console script with subcommands `version`, `migrate`, `check`, `worker`, and `controller`.
- In this phase only `version` works. The others exit non-zero with a "not implemented" message so that later phases fill them in without changing the entry point.

### Configuration loading

- One module that reads configuration from environment variables, with typed fields, defaults, and validation errors that name the variable.
- `TASKWIRE_BACKEND_URL` is the only required setting; its scheme selects the backend (`postgresql://`, `memory://`). Values are never logged.
- There is no configuration file.

### Dependencies

- Add `psycopg` (version 3) with its connection pool as the PostgreSQL driver, imported only by the PostgreSQL backend.
- Each later phase adds the dependencies it first uses. Optional features are extras: `taskwire[metrics]`, `taskwire[s3]`.

## Required tests

- The fixture creates and drops its database, and two concurrent sessions do not collide.
- With the variable unset, integration tests report as skipped.
- `taskwire version` prints the package version from an installed wheel.
- Configuration: missing required variable, malformed value, and defaults.

## Exit gate

`make lint unit integration packaging` passes locally and in CI, with at least one integration test executing a query against PostgreSQL.
