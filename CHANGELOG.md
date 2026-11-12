# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

### Added

- `taskwire` command (`python -m taskwire` also works). `version` works; `migrate`,
  `check`, `worker`, and `controller` are reserved and exit non-zero.
- Environment-variable configuration (`TASKWIRE_BACKEND_URL`,
  `TASKWIRE_STARTUP_CHECK`, `TASKWIRE_ALLOW_EPHEMERAL`) with errors that name the
  variable and never echo its value.
- PostgreSQL integration-test fixture, `make db-up` / `make db-down`, and a CI
  job on PostgreSQL 14 and 18.
- `psycopg` 3 as the PostgreSQL driver (`taskwire[binary]` bundles libpq).
